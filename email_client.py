import imaplib
import smtplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import re
import html as _html_mod
import threading
import time
import logging

logger = logging.getLogger("inquiry")


def _decode_str(value):
    """解码邮件头部字段（处理编码混合情况）"""
    if not value:
        return ""
    parts = decode_header(value)
    result = []
    for raw, charset in parts:
        if isinstance(raw, bytes):
            result.append(raw.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(raw)
    return "".join(result)


_MAX_IMAGES      = 3               # 每封邮件最多处理前 3 张图
_MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 单张上限 4 MB，超过则跳过


def _extract_images(msg) -> list[dict]:
    """从 MIME 邮件中提取图片（inline 内嵌 + 普通附件均提取）。
    返回 list of {"content_type": str, "raw": bytes}，最多 _MAX_IMAGES 张。
    参考：https://docs.python.org/3/library/email.message.html"""
    images = []
    for part in msg.walk():
        if len(images) >= _MAX_IMAGES:
            break
        ctype = part.get_content_type()
        if not ctype.startswith("image/"):
            continue
        raw = part.get_payload(decode=True)   # get_payload(decode=True) 自动解 base64/QP
        if not raw or len(raw) > _MAX_IMAGE_BYTES:
            continue
        images.append({"content_type": ctype, "raw": raw})
    return images


import base64 as _b64


def _extract_html_with_cid(msg) -> str | None:
    """
    提取 HTML 正文，并将 cid:xxx 内联图片替换为 base64 data URI，
    确保在浏览器 iframe 中可直接渲染而不丢失图片。
    若邮件无 HTML part 则返回 None（调用方回退到纯文本）。
    """
    html_parts: list[str] = []
    cid_map:    dict[str, str] = {}   # content-id → data: URI

    for part in msg.walk():
        ctype = part.get_content_type()
        cid   = part.get("Content-ID", "").strip("<>")

        if ctype == "text/html":
            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True)
            if payload:
                html_parts.append(payload.decode(charset, errors="replace"))

        elif ctype.startswith("image/") and cid:
            raw_bytes = part.get_payload(decode=True)
            if raw_bytes and len(raw_bytes) <= _MAX_IMAGE_BYTES:
                b64 = _b64.b64encode(raw_bytes).decode("ascii")
                cid_map[cid] = f"data:{ctype};base64,{b64}"

    if not html_parts:
        return None

    html = "\n".join(html_parts)
    # 将所有 cid:xxx 替换为对应的 data URI；无匹配时保留原样（浏览器会显示破图）
    html = re.sub(
        r'cid:([^"\'>\s]+)',
        lambda m: cid_map.get(m.group(1), m.group(0)),
        html,
    )
    return html


def _strip_html(raw_html: str) -> str:
    """去除 HTML 标签，将实体转义还原为纯文本。"""
    # 把常见块级标签转换为换行，避免所有文字挤在一行
    raw_html = re.sub(r'<(br|p|div|tr|li|h[1-6])\b[^>]*>', '\n', raw_html, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', raw_html)
    text = _html_mod.unescape(text)
    # 压缩多余空白行，保留段落感
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_text(msg):
    """从邮件中提取纯文本内容。优先 text/plain；无 plain part 时回退到 text/html 并剥离标签。"""
    plain_parts = []
    html_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                continue
            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            decoded = payload.decode(charset, errors="replace")
            if ctype == "text/plain":
                plain_parts.append(decoded)
            elif ctype == "text/html":
                html_parts.append(decoded)
    else:
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        if payload:
            decoded = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                html_parts.append(decoded)
            else:
                plain_parts.append(decoded)

    if plain_parts:
        return "\n".join(plain_parts).strip()
    if html_parts:
        return _strip_html("\n".join(html_parts))
    return ""


def fetch_unread_emails(max_count=10, creds: dict = None):
    """
    连接 IMAP，拉取未读邮件。
    creds 可传入 dict(imap_host, imap_port, imap_user, imap_pass)，
    不传则回退到 .env 环境变量。
    返回 list of dict: uid, subject, sender, received_at, body_text

    使用 UID SEARCH + BODY.PEEK[] 抓取，不自动标记已读，
    确保 AI 处理失败时邮件不丢失，由调用方在处理成功后调用
    mark_emails_seen() 显式标记。
    """
    c = creds or {}
    host     = c.get("imap_host") or os.getenv("IMAP_HOST")
    port     = int(c.get("imap_port") or os.getenv("IMAP_PORT", "993"))
    user     = c.get("imap_user") or os.getenv("IMAP_USER")
    password = c.get("imap_pass") or os.getenv("IMAP_PASS")

    if not all([host, user, password]):
        raise ValueError("缺少 IMAP 配置，请检查 .env 文件或邮箱账号设置")

    mail = imaplib.IMAP4_SSL(host, port)
    mail.login(user, password)
    mail.select("INBOX")

    try:
        # 用 UID SEARCH 返回稳定的 IMAP UID（不是序列号），避免邮件被删除后序号漂移
        status, data = mail.uid("search", None, "UNSEEN")
        if status != "OK" or not data[0]:
            return []

        uids = data[0].split()
        # 只取最新的 max_count 封
        uids = uids[-max_count:]

        results = []
        for uid in uids:
            # BODY.PEEK[] 不设置 \Seen 标志，处理失败时邮件仍可被下次轮询重新检测
            status, msg_data = mail.uid("fetch", uid, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or msg_data[0] is None:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject = _decode_str(msg.get("Subject", "(no subject)"))
            sender = _decode_str(msg.get("From", ""))
            date_str = msg.get("Date", "")
            try:
                received_at = parsedate_to_datetime(date_str).isoformat()
            except Exception:
                received_at = date_str

            body_text = _extract_text(msg)
            body_html = _extract_html_with_cid(msg)   # HTML 预览（cid: 已转 data URI）

            results.append({
                "uid": uid.decode(),
                "subject": subject,
                "sender": sender,
                "received_at": received_at,
                "body_text": body_text,
                "body_html": body_html,
                "images": _extract_images(msg),   # 内嵌图片，供 AI 视觉识别
            })

        return results
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def mark_emails_seen(uids: list, creds: dict = None) -> None:
    """在 IMAP 服务器上将指定 UID 列表的邮件标记为已读。
    仅在 _handle_one_email 成功保存到 DB 后由调用方调用。"""
    if not uids:
        return
    c = creds or {}
    host     = c.get("imap_host") or os.getenv("IMAP_HOST")
    port     = int(c.get("imap_port") or os.getenv("IMAP_PORT", "993"))
    user     = c.get("imap_user") or os.getenv("IMAP_USER")
    password = c.get("imap_pass") or os.getenv("IMAP_PASS")

    if not all([host, user, password]):
        return

    mail = imaplib.IMAP4_SSL(host, port)
    mail.login(user, password)
    mail.select("INBOX")
    try:
        # IMAP 命令行长度有限制，分批处理（每批最多 100 个 UID）
        batch_size = 100
        for i in range(0, len(uids), batch_size):
            batch = uids[i:i + batch_size]
            uid_str = ",".join(str(u) for u in batch)
            mail.uid("store", uid_str, "+FLAGS", "\\Seen")
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def fetch_recent_emails(max_count=100, creds: dict = None):
    """
    从 INBOX 拉取最近 max_count 封邮件（不限已读/未读）。
    用于用户首次登录时初始化显示。

    优化说明：
    - 只拉 INBOX，不遍历 Sent/Trash/All Mail 等其他文件夹（避免 N 倍无效拉取）
    - 限制拉取 90 天内的邮件，跳过极早期历史（可通过 INIT_SYNC_DAYS 环境变量调整）
    """
    c = creds or {}
    host     = c.get("imap_host") or os.getenv("IMAP_HOST")
    port     = int(c.get("imap_port") or os.getenv("IMAP_PORT", "993"))
    user     = c.get("imap_user") or os.getenv("IMAP_USER")
    password = c.get("imap_pass") or os.getenv("IMAP_PASS")

    if not all([host, user, password]):
        raise ValueError("缺少 IMAP 配置")

    from imap_tools import MailBox, AND
    from datetime import datetime, timedelta

    days = int(os.getenv("INIT_SYNC_DAYS", "90"))
    date_since = (datetime.now() - timedelta(days=days)).date()

    all_results = []
    with MailBox(host, port).login(user, password) as mailbox:
        mailbox.folder.set("INBOX")
        msgs = list(mailbox.fetch(AND(date_gte=date_since), limit=max_count,
                                  mark_seen=False, reverse=True))
        for msg in msgs:
            received_at = msg.date.isoformat() if msg.date else ""
            # 处理 HTML 正文：用 imap-tools 的 attachments 替换 cid: 内联图片
            body_html: str | None = msg.html or None
            if body_html:
                cid_map: dict[str, str] = {}
                for att in (msg.attachments or []):
                    cid = (att.content_id or "").strip("<>")
                    if cid and att.content_type.startswith("image/") and att.payload:
                        if len(att.payload) <= _MAX_IMAGE_BYTES:
                            b64 = _b64.b64encode(att.payload).decode("ascii")
                            cid_map[cid] = f"data:{att.content_type};base64,{b64}"
                if cid_map:
                    body_html = re.sub(
                        r'cid:([^"\'>\s]+)',
                        lambda m: cid_map.get(m.group(1), m.group(0)),
                        body_html,
                    )
            all_results.append({
                "uid": f"INBOX:{msg.uid}",
                "subject": msg.subject or "(no subject)",
                "sender": msg.from_ or "",
                "received_at": received_at,
                "body_text": msg.text or (body_html and _strip_html(body_html)) or "",
                "body_html": body_html,
            })

    all_results.sort(key=lambda x: x["received_at"] or "", reverse=True)
    return all_results[:max_count]


def start_idle_watcher(on_new_mail, creds: dict = None) -> threading.Event:
    """
    启动 IMAP IDLE 监听线程（RFC 2177）。
    服务器推送新邮件通知时立即调用 on_new_mail()，无需等待轮询间隔。
    主流 IMAP 服务商（Gmail、Outlook、QQ 企业邮箱等）均支持 IDLE。

    返回 threading.Event，调用 .set() 可停止监听（应用退出时调用）。

    参考：https://datatracker.ietf.org/doc/html/rfc2177
    imap-tools idle API：https://github.com/ikvk/imap_tools#idle
    """
    stop_event = threading.Event()

    def _watch():
        from imap_tools import MailBox
        c = creds or {}
        host     = c.get("imap_host") or os.getenv("IMAP_HOST")
        port     = int(c.get("imap_port") or os.getenv("IMAP_PORT", "993"))
        user     = c.get("imap_user") or os.getenv("IMAP_USER")
        password = c.get("imap_pass") or os.getenv("IMAP_PASS")
        if not all([host, user, password]):
            return

        while not stop_event.is_set():
            try:
                logger.info("[IDLE] 正在连接 IMAP 服务器...")
                with MailBox(host, port).login(user, password) as mailbox:
                    logger.info("[IDLE] 连接成功，开始监听新邮件")
                    while not stop_event.is_set():
                        # idle.wait() = start IDLE → 等待服务器推送 → stop IDLE
                        # timeout=55s：RFC 2177 建议 ≤29 分钟重新发 IDLE，55s 是保守安全值
                        # 服务器有任何事件（新邮件/删除/标志变更）时立即返回非空列表
                        responses = mailbox.idle.wait(timeout=55)
                        if responses and not stop_event.is_set():
                            logger.info(f"[IDLE] 收到服务器推送，触发邮件检查")
                            on_new_mail()
            except Exception as e:
                if not stop_event.is_set():
                    logger.warning(f"[IDLE] 连接中断: {e}, 5秒后重连")
                    time.sleep(5)   # 连接断开后等 5s 重连（原 30s 太久导致邮件延迟）

    t = threading.Thread(target=_watch, daemon=True, name="imap-idle")
    t.start()
    return stop_event


def send_email(to_address: str, subject: str, body: str, creds: dict = None) -> None:
    """通过 SMTP 发送邮件。creds 同 fetch_unread_emails，不传则用 .env"""
    c = creds or {}
    host     = c.get("smtp_host") or os.getenv("SMTP_HOST") or os.getenv("IMAP_HOST")
    port     = int(c.get("smtp_port") or os.getenv("SMTP_PORT", "465"))
    user     = c.get("smtp_user") or c.get("imap_user") or os.getenv("SMTP_USER") or os.getenv("IMAP_USER")
    password = c.get("smtp_pass") or c.get("imap_pass") or os.getenv("SMTP_PASS") or os.getenv("IMAP_PASS")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_address
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # 587 端口（Outlook/Office365/iCloud）用 STARTTLS，465 端口用 SSL 直连
    if port == 587:
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, [to_address], msg.as_string())
    else:
        with smtplib.SMTP_SSL(host, port) as server:
            server.login(user, password)
            server.sendmail(user, [to_address], msg.as_string())
