"""
外贸询盘自动回复系统 — Web 应用
启动方式: venv/Scripts/python -m uvicorn main:app --reload --port 8000
"""
import sys
import os
import logging
from logging.handlers import TimedRotatingFileHandler
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# ── 文件日志（30 天滚动，每天 midnight 切文件） ───────────────────────────────
# 重启不丢日志，可用于事后追查"某封邮件为何未处理"等问题。
_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_log_dir, exist_ok=True)
_log_file = os.path.join(_log_dir, "app.log")

_file_handler = TimedRotatingFileHandler(
    _log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
)
_fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_file_handler.setFormatter(_fmt)

logger = logging.getLogger("inquiry")
logger.setLevel(logging.INFO)
logger.addHandler(_file_handler)
# 同时保留 stdout 输出（uvicorn 本身不会重复打印 logger 内容）
_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setFormatter(_fmt)
logger.addHandler(_console_handler)

from dotenv import load_dotenv
load_dotenv()

import json
import asyncio
import re as _re
from datetime import datetime, timezone, timedelta as _timedelta
from contextlib import asynccontextmanager

def _now() -> str:
    """返回本地时间 ISO 字符串（统一格式）"""
    return datetime.now().isoformat()

from fastapi import FastAPI, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

# ── SSE 事件总线 ──────────────────────────────────────────────────────────────
# 每个连接的浏览器 Tab 注册一个 asyncio.Queue；
# APScheduler 线程用 call_soon_threadsafe 写入，SSE 协程读取后推送给浏览器。
# 来源：https://medium.com/@inandelibas/real-time-notifications-in-python-using-sse-with-fastapi-1c8c54746eb7
_sse_clients: list[asyncio.Queue] = []
_sse_loop: asyncio.AbstractEventLoop | None = None  # 主事件循环，lifespan 时赋值


def _push_event(data: dict):
    """从任意线程安全地向所有 SSE 客户端推送事件。"""
    if not _sse_loop or not _sse_clients:
        return
    payload = json.dumps(data, ensure_ascii=False)
    for q in list(_sse_clients):
        _sse_loop.call_soon_threadsafe(q.put_nowait, payload)

import database as db
from auth import get_session_user, verify_imap, detect_provider
from email_client import fetch_unread_emails, fetch_recent_emails, send_email, mark_emails_seen, start_idle_watcher
from ai_processor import (classify_email, parse_inquiry, generate_draft,
                          background_check, generate_followup_draft, score_buyer_intent,
                          extract_products_from_url, describe_email_images,
                          check_ai_health, get_ai_health, rewrite_snippet)
from product_matcher import load_products, reload_products, match_products, get_products
from quotation_pdf import generate_quotation_pdf


# ── Auth 辅助 ─────────────────────────────────────────────────────────────────

_PUBLIC_PATHS = {"/login", "/health"}

def _is_public(path: str) -> bool:
    return path in _PUBLIC_PATHS or path.startswith("/static")


# ── 登录频率限制（简单内存实现，防暴力破解） ──────────────────────────────────
import time as _time
from collections import defaultdict as _defaultdict

_login_attempts: dict[str, list[float]] = _defaultdict(list)
_RL_WINDOW  = 300   # 5 分钟滑动窗口
_RL_MAX     = 5     # 最多 5 次失败尝试

def _check_rate_limit(ip: str) -> bool:
    """返回 True 表示已被限速，False 表示正常"""
    now = _time.time()
    attempts = [t for t in _login_attempts[ip] if now - t < _RL_WINDOW]
    _login_attempts[ip] = attempts
    return len(attempts) >= _RL_MAX

def _record_failed_login(ip: str):
    _login_attempts[ip].append(_time.time())


# ── 邮件处理核心逻辑（与 demo.py 相同） ─────────────────────────────────────

def _extract_domain(email_str: str) -> str:
    """从邮件地址中提取有效域名，必须包含 '.' 且不以 '-' 或 '.' 结尾"""
    m = _re.search(r"@([\w.\-]+)", email_str)
    if not m:
        return ""
    domain = m.group(1).rstrip(".-")
    return domain if "." in domain else ""


def _log_subject(s: str) -> str:
    """截断主题至 20 字符用于日志，避免记录过多正文内容"""
    s = (s or "").strip()
    return s[:20] + "…" if len(s) > 20 else s


def _log_sender(s: str) -> str:
    """日志中只显示发件人域名，隐藏本地部分（PII）"""
    domain = _extract_domain(s)
    return f"*@{domain}" if domain else s[:20]


import threading as _threading
_poll_lock = _threading.Lock()  # 防止定时轮询与手动触发并发，导致同一封邮件被处理两次

def process_new_emails():
    """定时任务：遍历所有活跃邮箱账号，拉取并处理新邮件"""
    if not _poll_lock.acquire(blocking=False):
        logger.info("上一次轮询仍在进行，跳过本次")
        return
    try:
        _process_new_emails_inner()
    finally:
        _poll_lock.release()


def _ai_health_probe_bg():
    """后台线程执行 AI 健康探针，不阻塞邮件轮询"""
    try:
        status_change = check_ai_health()
        if status_change:
            old, new = status_change
            logger.info(f"[AI 状态] {old} → {new}")
            health = get_ai_health()
            _push_event({"type": "ai_status_changed", "old": old, "new": new,
                          "error": health.get("error_message")})
    except Exception as e:
        logger.warning(f"[AI 探针] 异常: {e}")


def _process_new_emails_inner():
    logger.info("检查新邮件...")
    # AI 健康探针：在后台线程执行，不阻塞邮件拉取
    _threading.Thread(target=_ai_health_probe_bg, daemon=True, name="ai-probe").start()
    accounts = db.list_email_accounts(active_only=True)
    # 若数据库无账号，回退到 .env 单账号模式
    if not accounts:
        accounts = [None]
    for account in accounts:
        _process_one_account(account)


def _process_one_account(account: dict | None):
    label = account["label"] if account else os.getenv("IMAP_USER", ".env账号")
    creds = account  # None → email_client 自动读 .env
    try:
        emails = fetch_unread_emails(max_count=10, creds=creds)
    except Exception as e:
        logger.error(f"[{label}] 邮件拉取失败: {e}")
        return

    if not emails:
        logger.info(f"[{label}] 无新邮件")
        return

    logger.info(f"[{label}] 获取到 {len(emails)} 封新邮件")
    account_email = account["imap_user"] if account else None
    processed_uids = []  # 成功保存到 DB 的 IMAP UID，稍后标记为已读
    for raw in emails:
        original_uid = raw["uid"]  # 保存原始 IMAP UID（_handle_one_email 会修改 raw["uid"]）
        raw["_account_email"] = account_email
        try:
            _handle_one_email(raw)
            processed_uids.append(original_uid)
        except Exception as e:
            logger.error(f"[{label}] 处理邮件出错 uid={original_uid}: {e}")
    # 只对成功处理的邮件在 IMAP 服务器上标记已读，失败的保持 UNSEEN 以便下次重试
    if processed_uids:
        try:
            mark_emails_seen(processed_uids, creds=creds)
        except Exception as e:
            logger.warning(f"[{label}] 标记已读失败（不影响主流程）: {e}")


def _handle_one_email(raw: dict):
    account_email = raw.get("_account_email")  # 来源邮箱账号
    # 不同邮箱的 IMAP UID 各自从 1 开始，加前缀保证全局唯一
    if account_email:
        raw["uid"] = f"{account_email}:{raw['uid']}"

    # 黑白名单优先检查
    rule, matched_pattern = db.check_sender_rule(raw["sender"])
    if rule == "block":
        logger.info(f"[黑名单] 已屏蔽 uid={raw['uid']} from={_log_sender(raw['sender'])} 规则='{matched_pattern}'")
        db.save_email(
            uid=raw["uid"], subject=raw["subject"], sender=raw["sender"],
            received_at=raw["received_at"], body_text=raw["body_text"],
            language="unknown", category="spam",
            classify_layer=0, classify_score=None, classify_criteria=None,
            account_email=account_email,
        )
        return

    # 图片识别：若邮件含图片，用 Qwen-VL 提取关键信息追加到正文，供后续 AI 处理
    if raw.get("images"):
        img_desc = describe_email_images(raw["images"])
        if img_desc:
            raw["body_text"] = raw["body_text"] + "\n\n--- 图片内容 ---\n" + img_desc
            logger.info(f"[图片] uid={raw['uid']} 识别 {len(raw['images'])} 张，已追加描述")

    # 线程识别（Re: 邮件关联原始询盘）
    thread_email_id = db.find_thread_parent(raw["subject"])

    if rule == "trust":
        classification = {"category": "valid_inquiry", "layer": 0, "score": None, "criteria": {}}
    elif get_ai_health()["status"] == "unavailable":
        # AI 不可用时跳过 LLM 分类，保存为 pending_ai 等待恢复后补跑
        logger.info(f"[AI 离线] 跳过分类 uid={raw['uid']}，标记为 pending_ai")
        db.save_email(
            uid=raw["uid"], subject=raw["subject"], sender=raw["sender"],
            received_at=raw["received_at"], body_text=raw["body_text"],
            language="unknown", category="pending_ai",
            classify_layer=0, classify_score=None, classify_criteria=None,
            thread_email_id=thread_email_id, account_email=account_email,
        )
        return
    else:
        classification = classify_email(raw["subject"], raw["sender"], raw["body_text"])

    category = classification.get("category", "other")
    layer  = classification.get("layer")
    score  = classification.get("score")
    criteria = classification.get("criteria") or {}

    score_str = f"score={score}/{classification.get('max_score')}" if score is not None else f"layer={layer}"
    thread_str = f" thread→#{thread_email_id}" if thread_email_id else ""
    logger.info(f"[{category}] {score_str}{thread_str} uid={raw['uid']} subject={_log_subject(raw['subject'])} from={_log_sender(raw['sender'])}")

    if category != "valid_inquiry":
        db.save_email(
            uid=raw["uid"], subject=raw["subject"], sender=raw["sender"],
            received_at=raw["received_at"], body_text=raw["body_text"],
            language="unknown", category=category,
            classify_layer=layer, classify_score=score, classify_criteria=criteria,
            thread_email_id=thread_email_id, account_email=account_email,
        )
        return

    parsed = parse_inquiry(raw["body_text"])
    language = parsed.get("language", "en")
    products_requested = parsed.get("products_requested") or []
    matched = match_products(products_requested, top_n=2)
    draft = generate_draft(parsed, matched)
    intent = score_buyer_intent(parsed, raw["body_text"])

    draft_subject = draft.get("subject", f"Re: {raw['subject']}")
    draft_body = draft.get("body", "")

    # 原子写入：邮件 + 草稿 + 状态更新在同一事务中，任意失败全部回滚
    email_id, _ = db.save_email_with_draft(
        uid=raw["uid"], subject=raw["subject"], sender=raw["sender"],
        received_at=raw["received_at"], body_text=raw["body_text"],
        language=language,
        classify_layer=layer, classify_score=score, classify_criteria=criteria,
        intent_score=intent, thread_email_id=thread_email_id,
        account_email=account_email,
        draft_subject=draft_subject, draft_body=draft_body,
        quoted_products=matched, parsed_inquiry=parsed,
    )
    logger.info(f"[草稿已生成] email_id={email_id} from={_log_sender(raw['sender'])} subject={_log_subject(raw['subject'])}")
    _notify_new_draft(raw["subject"], raw["sender"], email_id)
    # 实时推送：通知所有打开的浏览器 Tab 有新草稿
    _push_event({"type": "new_draft", "subject": raw["subject"],
                 "sender": raw["sender"], "email_id": email_id})

    # 更新客户记忆
    domain = _extract_domain(raw["sender"])
    if domain:
        db.upsert_customer(
            domain,
            company_name=parsed.get("company"),
            country=parsed.get("country"),
            is_new_inquiry=True,
        )


def _notify_new_draft(subject: str, sender: str, email_id: int):
    """草稿生成后，给 NOTIFY_EMAIL 发一封提醒（异步，失败静默）"""
    notify_addr = os.getenv("NOTIFY_EMAIL", "")
    if not notify_addr:
        return
    try:
        system_url = os.getenv("SYSTEM_URL", "").rstrip("/")
        url_line = f"\n请前往系统审核并发送：{system_url}/\n" if system_url else ""
        body = (
            f"您有一封新询盘草稿等待审核。\n\n"
            f"发件人：{sender}\n"
            f"主题：{subject}\n"
            f"{url_line}"
        )
        send_email(notify_addr, f"【新草稿】{subject[:40]}", body)
        logger.info(f"[通知] 草稿提醒已发送 → {notify_addr}")
    except Exception as e:
        logger.warning(f"[通知] 发送失败（不影响主流程）: {e}")


# ── 应用生命周期 ──────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler()

# 调度器运行状态，供 /health 端点暴露
_scheduler_status: dict = {
    "last_success_time": None,
    "last_error_time": None,
    "last_error_job": None,
    "last_error_msg": None,
}

def _on_job_error(event):
    """任务异常时记录状态，并写入日志文件"""
    _scheduler_status["last_error_time"] = _now()
    _scheduler_status["last_error_job"]  = event.job_id
    _scheduler_status["last_error_msg"]  = str(event.exception)
    logger.error(f"[调度器错误] job={event.job_id} {event.exception}")

def _on_job_executed(event):
    _scheduler_status["last_success_time"] = _now()

def _check_overdue_followups():
    """每日检查到期跟进，记录日志提醒"""
    overdue = db.get_overdue_followups()
    if overdue:
        logger.info(f"[跟进提醒] 有 {len(overdue)} 个跟进任务已到期，请前往 /followups 处理")


def _run_daily_backup():
    """每日凌晨 2:00 自动备份数据库"""
    try:
        from backup import run_backup
        run_backup()
        logger.info("[备份] 每日自动备份完成")
    except Exception as e:
        logger.error(f"[备份] 自动备份失败: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _sse_loop
    _sse_loop = asyncio.get_event_loop()   # 保存主事件循环供 APScheduler 线程使用
    db.init_db()
    db.init_followups_table()
    db.init_customers_table()
    db.init_rules_table()
    db.init_users_table()
    db.init_email_accounts_table()
    db.init_deal_columns()
    load_products()
    interval = int(os.getenv("POLL_INTERVAL", "60"))
    scheduler.add_job(process_new_emails,    "interval", seconds=interval, id="poll_email")
    scheduler.add_job(_check_overdue_followups, "cron", hour=9,  minute=0,  id="followup_check")
    scheduler.add_job(_run_daily_backup,        "cron", hour=2,  minute=0,  id="daily_backup")
    scheduler.add_listener(_on_job_error,    EVENT_JOB_ERROR)
    scheduler.add_listener(_on_job_executed, EVENT_JOB_EXECUTED)
    scheduler.start()
    # IMAP IDLE 实时监听：服务器推送新邮件时立即触发处理，不受轮询间隔限制
    # 仅监听 .env / 首个账号；多账号场景 60s 轮询兜底仍会覆盖所有账号
    _idle_stop = start_idle_watcher(process_new_emails)
    print(f"✓ 定时任务启动，每 {interval} 秒检查邮件（IMAP IDLE 实时监听已启用），09:00 检查跟进，02:00 自动备份")
    bad = _company_has_placeholder()
    if bad:
        print(f"[⚠️  配置] 公司信息含占位符，AI 草稿签名将不正确，请前往 /settings/company 完善：{bad}")
    yield
    _idle_stop.set()
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)

templates = Jinja2Templates(directory="templates")
templates.env.filters["from_json"] = json.loads   # 模板里用 | from_json
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


import secrets as _secrets

_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_CSRF_EXEMPT_PATHS = {"/health", "/events", "/login"}


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    """CSRF 防护：POST/PUT/DELETE 请求必须携带有效 token"""
    # 注入 csrf_token 到模板全局变量（每个请求都刷新）
    templates.env.globals["csrf_token"] = request.session.get("csrf_token", "")

    if request.method in _CSRF_SAFE_METHODS:
        return await call_next(request)
    if request.url.path in _CSRF_EXEMPT_PATHS or _is_public(request.url.path):
        return await call_next(request)

    session_token = request.session.get("csrf_token", "")
    if not session_token:
        return await call_next(request)  # 无 session（未登录）跳过

    # 从表单字段或请求头获取提交的 token
    submitted = None
    content_type = request.headers.get("content-type", "")
    if "form" in content_type:
        form = await request.form()
        submitted = form.get("_csrf")
    if not submitted:
        submitted = request.headers.get("X-CSRF-Token")

    if not submitted or submitted != session_token:
        return HTMLResponse("403 Forbidden — CSRF token 无效，请刷新页面重试", status_code=403)

    return await call_next(request)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """所有非公开路由要求登录"""
    if _is_public(request.url.path):
        return await call_next(request)
    if not get_session_user(request):
        return RedirectResponse("/login", status_code=302)
    return await call_next(request)


# ── SECRET_KEY 自动生成（首次启动时写入 .env，避免使用弱占位符） ────────────
_WEAK_KEYS = {
    "dev-secret-please-change-in-production",
    "please-change-this-to-a-random-string-in-production",
    "",
}

def _ensure_secret_key() -> str:
    current = os.getenv("SECRET_KEY", "")
    if current and current not in _WEAK_KEYS:
        return current
    import secrets as _sec
    new_key = _sec.token_urlsafe(32)
    _env_path = os.path.join(os.path.dirname(__file__), ".env")
    try:
        with open(_env_path, "r", encoding="utf-8") as _f:
            _content = _f.read()
        if "SECRET_KEY=" in _content:
            _content = _re.sub(r"SECRET_KEY=.*", f"SECRET_KEY={new_key}", _content)
        else:
            _content += f"\nSECRET_KEY={new_key}\n"
        with open(_env_path, "w", encoding="utf-8") as _f:
            _f.write(_content)
        os.environ["SECRET_KEY"] = new_key
        print(f"[安全] 已自动生成 SECRET_KEY 并写入 .env，请勿手动修改（修改后 DB 中加密密码将失效）")
    except Exception as _e:
        os.environ["SECRET_KEY"] = new_key
        print(f"[安全] 已生成 SECRET_KEY（写入 .env 失败: {_e}），请手动添加: SECRET_KEY={new_key}")
    return new_key

# SessionMiddleware 必须在 auth_middleware 之后 add（Starlette 后加的包在外层，先处理请求）
secret_key = _ensure_secret_key()
_dev_mode = os.getenv("DEV_MODE", "").strip() in ("1", "true", "yes")
app.add_middleware(
    SessionMiddleware,
    secret_key=secret_key,
    max_age=86400,                       # 1天（原7天）
    https_only=not _dev_mode,            # 生产环境强制 HTTPS
    same_site="strict",                  # 严格同站策略
)


# ── 路由 ──────────────────────────────────────────────────────────────────────

@app.get("/api/counts")
async def api_counts(request: Request):
    """轻量角标接口：返回待审核草稿数 + 逾期跟进数，供导航栏角标使用"""
    user = get_session_user(request)
    account_email = user["email"] if user else None
    health = get_ai_health()
    return JSONResponse({
        "pending_drafts": db.get_pending_draft_count(account_email=account_email),
        "overdue_followups": len(db.get_overdue_followups()),
        "ai_status": health["status"],
        "ai_error": health["error_message"],
        "ai_last_ok": health["last_ok"],
    })


@app.get("/health")
async def health():
    """健康检查接口：返回服务状态 + 调度器运行情况 + 今日处理统计"""
    has_error = _scheduler_status["last_error_msg"] is not None
    daily = db.get_daily_stats(1)
    today = daily[0] if daily else {}
    pending = db.get_pending_draft_count()
    return JSONResponse({
        "status":  "degraded" if has_error else "ok",
        "time":    _now(),
        "ai": get_ai_health(),
        "today": {
            "received":  today.get("total", 0),
            "inquiries": today.get("inquiries", 0),
            "spam":      today.get("spam", 0),
            "drafted":   today.get("drafted", 0),
            "sent":      today.get("sent", 0),
        },
        "pending_drafts": pending,
        "log_file": _log_file,
        "scheduler": {
            "last_success": _scheduler_status["last_success_time"],
            "last_error":   _scheduler_status["last_error_time"],
            "error_job":    _scheduler_status["last_error_job"],
            "error_msg":    _scheduler_status["last_error_msg"],
        },
    })


@app.get("/events")
async def sse_events(request: Request):
    """SSE 端点：浏览器长连接，有新草稿时服务器主动推送，无需刷新页面。"""
    q: asyncio.Queue = asyncio.Queue()
    _sse_clients.append(q)

    async def stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25)
                    msg = json.loads(payload)
                    event_type = msg.get("type", "new_draft")
                    yield ServerSentEvent(data=payload, event=event_type)
                except asyncio.TimeoutError:
                    yield ServerSentEvent(comment="keepalive")  # 心跳，防止代理断连
        finally:
            _sse_clients.remove(q)

    return EventSourceResponse(stream())


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    if get_session_user(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request=request, name="login.html", context={"error": error}
    )


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email:     str = Form(...),
    password:  str = Form(...),
    imap_host: str = Form(""),
    imap_port: int = Form(993),
):
    # 频率限制：同一 IP 5 分钟内失败超过 5 次则拒绝
    client_ip = request.client.host if request.client else "unknown"
    if _check_rate_limit(client_ip):
        return templates.TemplateResponse(
            request=request, name="login.html",
            context={"error": "登录尝试过于频繁，请 5 分钟后再试",
                     "email_val": email, "imap_host_val": imap_host, "imap_port_val": imap_port},
            status_code=429,
        )

    def _do_login():
        # 确定 IMAP 服务器
        host, port = imap_host.strip(), imap_port
        if not host:
            info = detect_provider(email)
            if info:
                host, port = info["imap_host"], info["imap_port"]

        if not host:
            return None, "无法自动识别邮件服务器，请展开「服务器设置」手动填写"

        print(f"[LOGIN] 尝试 IMAP 连接: {email} → {host}:{port}")
        ok, err_msg = verify_imap(email, password, host, port)
        if not ok:
            print(f"[LOGIN] 失败: {err_msg}")
            return None, f"{err_msg}（服务器：{host}:{port}）"
        print(f"[LOGIN] 成功: {email}")

        # 自动识别 SMTP
        info = detect_provider(email) or {}
        smtp_host = info.get("smtp_host") or host
        smtp_port = info.get("smtp_port") or 465
        label = email.split("@")[0]

        # 登录成功：写入 / 更新 email_accounts（作为主收信账号）
        # 提前判断账号是否已存在，用于决定是否执行初始邮件同步
        _is_new_account = db.get_email_account_by_user(email) is None
        db.add_email_account(label, host, port, email, password, smtp_host, smtp_port)

        # 写入 users 表（仅存身份，不存密码）
        if not db.get_user_by_email(email):
            db.create_user(email, "", label)

        user = db.get_user_by_email(email)
        return {"id": user["id"], "email": email, "name": label, "_is_new": _is_new_account}, None

    import asyncio
    loop = asyncio.get_event_loop()
    session_user, error = await loop.run_in_executor(None, _do_login)

    if error:
        _record_failed_login(client_ip)   # 记录失败次数
        return templates.TemplateResponse(
            request=request, name="login.html",
            context={"error": error, "email_val": email,
                     "imap_host_val": imap_host, "imap_port_val": imap_port},
        )
    _login_attempts.pop(client_ip, None)  # 登录成功，清除该 IP 的失败记录
    is_new_account = session_user.pop("_is_new", False)
    request.session["user"] = session_user
    request.session["csrf_token"] = _secrets.token_hex(32)

    # 仅在首次添加该账号时执行初始邮件同步，避免重启重新登录时重复插入历史邮件
    if is_new_account:
        account = db.get_email_account_by_user(email)
        def _init_inbox_sync():
            try:
                recents = fetch_recent_emails(max_count=100, creds=account)
                for raw in recents:
                    # fetch_recent_emails 的 uid 格式为 "{folder}:{imap_uid}"
                    # 提取纯 IMAP UID，与轮询写入格式 "{email}:{imap_uid}" 保持一致，避免重复存储
                    raw_uid = raw["uid"]
                    imap_uid = raw_uid.split(":")[-1] if ":" in raw_uid else raw_uid
                    uid = f"{email}:{imap_uid}"
                    db.save_email(
                        uid=uid, subject=raw["subject"], sender=raw["sender"],
                        received_at=raw["received_at"], body_text=raw["body_text"],
                        language="unknown", category="other",
                        account_email=email,
                    )
                print(f"[LOGIN] 首次登录，已存入最近 {len(recents)} 封邮件")
            except Exception as e:
                print(f"[LOGIN] 初始邮件拉取失败: {e}")

        import threading
        threading.Thread(target=_init_inbox_sync, daemon=True).start()

    return RedirectResponse("/", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.get("/settings/password", response_class=HTMLResponse)
async def password_page(request: Request):
    """密码即邮箱密码，引导用户去邮箱服务商修改"""
    user = get_session_user(request)
    return templates.TemplateResponse(
        request=request, name="password.html", context={"user": user},
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    from product_matcher import get_products
    user = get_session_user(request)
    account_email = user["email"] if user else None
    qp = request.query_params
    page        = max(1, int(qp.get("page", 1)))
    keyword     = qp.get("q", "").strip() or None
    category    = qp.get("cat", "all")
    status_filter = qp.get("status", "").strip() or None
    date_from   = qp.get("from", "").strip() or None
    date_to     = qp.get("to", "").strip() or None
    sort        = qp.get("sort", "received_at")
    order       = qp.get("order", "desc")
    per_page    = 50
    offset      = (page - 1) * per_page
    filters     = dict(keyword=keyword, category=category,
                       status_filter=status_filter,
                       date_from=date_from, date_to=date_to)
    total  = db.count_emails(account_email=account_email, **filters)
    emails = db.list_emails(limit=per_page, offset=offset,
                            account_email=account_email, sort=sort, order=order,
                            **filters)
    stats = db.count_stats(account_email=account_email)
    setup = db.get_setup_status()
    setup["no_products"] = len(get_products()) == 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse(
        request=request, name="index.html",
        context={"emails": emails, "stats": stats, "setup": setup,
                 "page": page, "total_pages": total_pages, "total": total,
                 "q": qp.get("q", ""), "cat": category,
                 "status_filter": status_filter or "",
                 "date_from": date_from or "", "date_to": date_to or "",
                 "sort": sort, "order": order},
    )


@app.post("/demo/load")
async def demo_load():
    """加载示例数据（仅首次使用时）"""
    n = db.load_demo_data()
    return RedirectResponse(f"/?demo={n}", status_code=303)


@app.post("/email/{email_id}/deal")
async def email_mark_deal(email_id: int, status: str = Form("won"),
                           amount: float = Form(0.0)):
    db.mark_deal(email_id, status, amount)
    return RedirectResponse("/", status_code=303)


@app.post("/emails/bulk")
async def emails_bulk(ids: str = Form(...), action: str = Form(...)):
    """批量操作：action 为 'read' 或 'ignored'"""
    try:
        id_list = [int(i) for i in ids.split(",") if i.strip().isdigit()]
    except ValueError:
        id_list = []
    if id_list and action in ("read", "ignored"):
        db.bulk_update_email_status(id_list, action)
    return RedirectResponse("/", status_code=303)


def _reprocess_email(email_id: int):
    """对已存 DB 的邮件重新跑分类+草稿流水线（跳过 IMAP/黑白名单/图片步骤）。"""
    rec = db.get_email(email_id)
    if not rec:
        return

    subject  = rec["subject"] or ""
    sender   = rec["sender"]  or ""
    body     = rec["body_text"] or ""
    account_email = rec.get("account_email")

    logger.info(f"[重新处理] email_id={email_id} from={_log_sender(sender)}")

    classification = classify_email(subject, sender, body)
    category = classification.get("category", "other")
    layer    = classification.get("layer")
    score    = classification.get("score")
    criteria = classification.get("criteria") or {}

    logger.info(f"[重新处理结果] email_id={email_id} category={category} score={score}")

    if category != "valid_inquiry":
        db.update_email_classification(email_id, category, layer, score, criteria)
        return

    parsed  = parse_inquiry(body)
    language = parsed.get("language", "en")
    products_requested = parsed.get("products_requested") or []
    matched = match_products(products_requested, top_n=2)
    draft   = generate_draft(parsed, matched)
    intent  = score_buyer_intent(parsed, body)

    draft_subject = draft.get("subject", f"Re: {subject}")
    draft_body    = draft.get("body", "")

    db.update_email_classification(email_id, category, layer, score, criteria,
                                   intent_score=intent)
    db.save_draft(email_id, draft_subject, draft_body, matched, parsed_inquiry=parsed)
    db.update_email_status(email_id, "drafted")
    # 同步更新客户记忆（与正常流水线保持一致）
    domain = _extract_domain(sender)
    if domain:
        db.upsert_customer(domain,
                           company_name=parsed.get("company"),
                           country=parsed.get("country"),
                           is_new_inquiry=True)
    logger.info(f"[重新处理→草稿] email_id={email_id}")
    _push_event({"type": "new_draft", "subject": subject,
                 "sender": sender, "email_id": email_id})


@app.post("/email/{email_id}/reprocess")
async def email_reprocess(email_id: int, background_tasks: BackgroundTasks):
    """重新对误判邮件跑 AI 分类流水线（后台执行，不阻塞请求）"""
    background_tasks.add_task(_reprocess_email, email_id)
    return RedirectResponse(f"/?reprocessed=1", status_code=303)


@app.get("/draft/{draft_id}", response_class=HTMLResponse)
async def draft_view(request: Request, draft_id: int):
    draft = db.get_draft(draft_id)
    if not draft:
        return HTMLResponse("草稿不存在", status_code=404)
    # 往来时间线：从根节点展开整条线程
    email_id = draft["email_id"]
    root_id = draft.get("thread_email_id") or email_id
    thread = db.get_email_thread(root_id)
    return templates.TemplateResponse(
        request=request, name="draft.html",
        context={"draft": draft, "thread": thread,
                 "company_bad_fields": _company_has_placeholder()},
    )


@app.post("/draft/{draft_id}/save")
async def draft_save(draft_id: int, subject: str = Form(...), body: str = Form(...)):
    db.update_draft(draft_id, subject, body)
    return RedirectResponse(f"/draft/{draft_id}?saved=1", status_code=303)


def _regenerate_draft(draft_id: int):
    """重新调用 AI 生成草稿正文，覆盖当前草稿内容（保留 subject 前缀）。"""
    draft = db.get_draft(draft_id)
    if not draft or draft["status"] in ("sent", "rejected"):
        return
    body_text = draft.get("body_text") or ""
    parsed_str = draft.get("parsed_inquiry")
    if parsed_str:
        try:
            parsed = json.loads(parsed_str)
        except Exception:
            parsed = parse_inquiry(body_text)
    else:
        parsed = parse_inquiry(body_text)

    products_requested = parsed.get("products_requested") or []
    matched = match_products(products_requested, top_n=2)
    new_draft = generate_draft(parsed, matched)
    new_body = new_draft.get("body", "")
    new_subject = new_draft.get("subject") or draft["subject"]
    db.update_draft(draft_id, new_subject, new_body)
    logger.info(f"[草稿重新生成] draft_id={draft_id}")


@app.post("/draft/{draft_id}/regenerate")
async def draft_regenerate(draft_id: int, background_tasks: BackgroundTasks):
    """后台重新生成草稿，完成后跳回草稿页"""
    background_tasks.add_task(_regenerate_draft, draft_id)
    return RedirectResponse(f"/draft/{draft_id}?regenerated=1", status_code=303)


@app.get("/draft/{draft_id}/quotation.pdf")
async def draft_quotation_pdf(draft_id: int):
    from fastapi.responses import Response
    draft = db.get_draft(draft_id)
    if not draft:
        return HTMLResponse("草稿不存在", status_code=404)
    parsed = {}
    if draft.get("parsed_inquiry"):
        try:
            parsed = json.loads(draft["parsed_inquiry"])
        except Exception:
            pass
    products = []
    if draft.get("quoted_products"):
        try:
            products = json.loads(draft["quoted_products"])
        except Exception:
            pass
    pdf_bytes = generate_quotation_pdf(draft, parsed, products)
    filename = f"Quotation-{draft.get('email_id', draft_id)}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/draft/{draft_id}/rewrite")
async def draft_rewrite(draft_id: int, instruction: str = Form(...)):
    """AI 局部改写：根据用户指令修改草稿正文，返回新正文（JSON）"""
    draft = db.get_draft(draft_id)
    if not draft:
        return JSONResponse({"error": "草稿不存在"}, status_code=404)
    if draft["status"] in ("sent", "sending"):
        return JSONResponse({"error": "已发送，无法修改"}, status_code=400)
    new_body = rewrite_snippet(
        draft["body"], instruction,
        language=draft.get("language", "en")
    )
    return JSONResponse({"body": new_body})


@app.post("/draft/{draft_id}/approve")
async def draft_approve(draft_id: int, background_tasks: BackgroundTasks):
    draft = db.get_draft(draft_id)
    if not draft:
        return HTMLResponse("草稿不存在", status_code=404)
    if draft["status"] in ("sent", "sending"):
        return HTMLResponse("该草稿已发送或正在发送中", status_code=400)

    # 解析发件人地址
    sender_raw = draft["sender"]
    m = _re.search(r"<(.+?)>", sender_raw)
    to_addr = m.group(1) if m else sender_raw.strip()

    # 立即标记为 sending，防止重复提交
    db.update_draft_status(draft_id, "sending")
    background_tasks.add_task(_send_and_update, draft_id, to_addr, draft["subject"], draft["body"], draft["email_id"])
    return RedirectResponse(f"/draft/{draft_id}?sending=1", status_code=303)


def _send_and_update(draft_id, to_addr, subject, body, email_id):
    try:
        email_rec = db.get_email(email_id)
        creds = db.get_email_account_by_user(
            email_rec.get("account_email") if email_rec else None
        )
        send_email(to_addr, subject, body, creds=creds)
        now = _now()
        db.update_draft_status(draft_id, "sent", sent_at=now, send_error=None)
        db.update_email_status(email_id, "sent")
        logger.info(f"[已发送] draft_id={draft_id} email_id={email_id} to={to_addr}")

        # 自动建跟进（3 天后）
        due = (datetime.now() + _timedelta(days=3)).date().isoformat()
        db.save_followup(email_id, draft_id, due, note=f"报价已发送至 {to_addr}")

        # 更新客户记忆
        domain = _extract_domain(to_addr)
        if domain:
            db.upsert_customer(domain, is_quoted=True)

        logger.info(f"[跟进] 已创建跟进任务，到期日：{due}")
    except Exception as e:
        err_msg = str(e)
        logger.error(f"[发送失败] draft_id={draft_id} to={to_addr} error={err_msg}")
        # 回滚：草稿恢复为 pending，记录错误原因，邮件状态恢复为 drafted
        db.update_draft_status(draft_id, "send_failed", send_error=err_msg)
        db.update_email_status(email_id, "drafted")


@app.post("/draft/{draft_id}/reject")
async def draft_reject(draft_id: int):
    draft = db.get_draft(draft_id)
    if draft:
        db.update_draft_status(draft_id, "rejected")
        db.update_email_status(draft["email_id"], "ignored")
    return RedirectResponse("/", status_code=303)


# ── 内联快速审核（列表页展开行使用，返回 JSON 不跳转） ────────────────────────

@app.get("/draft/{draft_id}/preview")
async def draft_preview_json(draft_id: int):
    draft = db.get_draft(draft_id)
    if not draft:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({
        "subject": draft["subject"],
        "body":    draft["body"],
        "sender":  draft["sender"],
        "status":  draft["status"],
    })


@app.post("/draft/{draft_id}/quick-approve")
async def draft_quick_approve(draft_id: int, background_tasks: BackgroundTasks):
    draft = db.get_draft(draft_id)
    if not draft:
        return JSONResponse({"error": "草稿不存在"}, status_code=404)
    if draft["status"] in ("sent", "sending"):
        return JSONResponse({"error": "已发送或发送中"}, status_code=400)
    m = _re.search(r"<(.+?)>", draft["sender"])
    to_addr = m.group(1) if m else draft["sender"].strip()
    db.update_draft_status(draft_id, "sending")
    background_tasks.add_task(
        _send_and_update, draft_id, to_addr,
        draft["subject"], draft["body"], draft["email_id"]
    )
    return JSONResponse({"ok": True})


@app.post("/draft/{draft_id}/quick-reject")
async def draft_quick_reject(draft_id: int):
    draft = db.get_draft(draft_id)
    if draft:
        db.update_draft_status(draft_id, "rejected")
        db.update_email_status(draft["email_id"], "ignored")
    return JSONResponse({"ok": True})


@app.post("/email/{email_id}/read")
async def email_mark_read(email_id: int):
    db.mark_email_read(email_id)
    return {"ok": True}


@app.get("/check/{email_id}", response_class=HTMLResponse)
async def check_view(request: Request, email_id: int):
    email = db.get_email(email_id)
    if not email:
        return HTMLResponse("邮件不存在", status_code=404)
    check = db.get_bg_check(email_id)
    draft = db.get_draft_by_email_id(email_id)
    # 往来记录：找到根节点（如果当前是回复，溯源到原始询盘），再展开整棵线程
    root_id = email.get("thread_email_id") or email_id
    thread = db.get_email_thread(root_id)
    return templates.TemplateResponse(
        request=request, name="check.html",
        context={"email": email, "check": check, "draft": draft, "thread": thread},
    )


@app.post("/check/{email_id}/run")
async def check_run(email_id: int, background_tasks: BackgroundTasks):
    background_tasks.add_task(_run_bg_check, email_id)
    return RedirectResponse(f"/check/{email_id}?running=1", status_code=303)


def _run_bg_check(email_id: int):
    email = db.get_email(email_id)
    if not email:
        return
    logger.info(f"[背调] 开始 email_id={email_id}")
    # 先从 AI 解析询盘获取公司名/国家，或从邮件正文推断
    try:
        parsed = parse_inquiry(email["body_text"])
        company = parsed.get("company") or ""
        country = parsed.get("country") or ""
    except Exception:
        company, country = "", ""
    result = background_check(
        sender=email["sender"],
        company_name=company,
        country=country,
        email_body=email["body_text"],
    )
    if result and result.get("risk_level"):
        db.save_bg_check(email_id, result)
        logger.info(f"[背调] email_id={email_id} 风险等级={result.get('risk_level')}")
    else:
        logger.warning(f"[背调] email_id={email_id} 结果无效或解析失败，未存库")


@app.post("/poll")
async def manual_poll(background_tasks: BackgroundTasks):
    """手动触发一次邮件检查"""
    background_tasks.add_task(process_new_emails)
    return RedirectResponse("/?polling=1", status_code=303)


# ── 跟进提醒路由 ────────────────────────────────────────────────────────────

@app.get("/followups", response_class=HTMLResponse)
async def followups_page(request: Request):
    all_followups = db.list_followups()
    stats = db.count_followup_stats()
    today = datetime.now().date().isoformat()  # local date
    return templates.TemplateResponse(
        request=request, name="followups.html",
        context={"followups": all_followups, "stats": stats, "today": today},
    )


@app.post("/followup/{fid}/skip")
async def followup_skip(fid: int):
    db.update_followup(fid, status="skipped")
    return RedirectResponse("/followups", status_code=303)


@app.post("/followup/{fid}/generate")
async def followup_generate(fid: int, background_tasks: BackgroundTasks):
    background_tasks.add_task(_gen_followup_draft, fid)
    return RedirectResponse(f"/followups?generating={fid}", status_code=303)


def _gen_followup_draft(fid: int):
    fu = db.get_followup(fid)
    if not fu:
        return
    draft = db.get_draft(fu["draft_id"]) if fu.get("draft_id") else None
    # 只对已实际发出的草稿生成跟进，避免"我们已发送报价"但实际未发的尴尬
    if not draft or draft.get("status") != "sent":
        logger.warning(f"[跟进] fid={fid} 原始草稿未发送（status={draft.get('status') if draft else 'missing'}），跳过跟进草稿生成")
        return
    sent_body = draft["body"]

    domain = _extract_domain(fu["sender"])
    customer = db.get_customer_by_domain(domain) if domain else {}

    result = generate_followup_draft(
        original_subject=fu["original_subject"],
        original_body=fu["body_text"],
        sent_draft_body=sent_body,
        language=fu.get("language", "en"),
        customer_history=customer or {},
    )
    db.update_followup(fid, status="draft_ready",
                       subject=result.get("subject"),
                       body=result.get("body"))
    print(f"  [跟进] 草稿已生成 fid={fid}")


@app.post("/followup/{fid}/save_draft")
async def followup_save_draft(fid: int, subject: str = Form(...), body: str = Form(...)):
    db.update_followup(fid, status="draft_ready", subject=subject, body=body)
    return RedirectResponse("/followups?saved=1", status_code=303)


@app.post("/followup/{fid}/send")
async def followup_send(fid: int, background_tasks: BackgroundTasks):
    fu = db.get_followup(fid)
    if not fu or not fu.get("followup_body"):
        return RedirectResponse(f"/followups?err=no_draft", status_code=303)
    if fu.get("status") in ("sent", "sending"):
        return RedirectResponse("/followups?err=already_sent", status_code=303)
    m = _re.search(r"<(.+?)>", fu["sender"])
    to_addr = m.group(1) if m else fu["sender"].strip()
    db.update_followup(fid, status="sending")
    background_tasks.add_task(_send_followup, fid, to_addr,
                              fu["followup_subject"], fu["followup_body"])
    return RedirectResponse("/followups?sent=1", status_code=303)


def _send_followup(fid, to_addr, subject, body):
    try:
        fu = db.get_followup(fid)
        email_rec = db.get_email(fu["email_id"]) if fu else None
        creds = db.get_email_account_by_user(
            email_rec.get("account_email") if email_rec else None
        )
        send_email(to_addr, subject, body, creds=creds)
        db.update_followup(fid, status="sent", sent_at=_now())
        # 更新客户 reply_count
        domain = _extract_domain(to_addr)
        if domain:
            db.upsert_customer(domain, is_replied=True)
        logger.info(f"[跟进] 已发送 fid={fid} to={to_addr}")
    except Exception as e:
        logger.error(f"[跟进] 发送失败 fid={fid} to={to_addr} error={e}")


# ── 客户记忆路由 ─────────────────────────────────────────────────────────────

@app.get("/customers", response_class=HTMLResponse)
async def customers_page(request: Request):
    qp = request.query_params
    keyword  = qp.get("q", "").strip() or None
    page     = max(1, int(qp.get("page", 1)))
    per_page = 50
    offset   = (page - 1) * per_page
    total    = db.count_customers(keyword=keyword)
    customers = db.list_customers(limit=per_page, offset=offset, keyword=keyword)
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse(
        request=request, name="customers.html",
        context={"customers": customers, "q": qp.get("q", ""),
                 "page": page, "total_pages": total_pages, "total": total},
    )


@app.get("/customer/{domain:path}", response_class=HTMLResponse)
async def customer_detail(request: Request, domain: str):
    customer = db.get_customer_by_domain(domain)
    emails = db.get_customer_emails(domain)
    return templates.TemplateResponse(
        request=request, name="customer_detail.html",
        context={"customer": customer or {"domain": domain}, "emails": emails},
    )


# ── 帮助文档 ─────────────────────────────────────────────────────────────────

@app.get("/help", response_class=HTMLResponse)
async def help_page(request: Request):
    return templates.TemplateResponse(request=request, name="help.html", context={})


# ── 漏斗分析路由 ──────────────────────────────────────────────────────────────

@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    data = db.get_analytics()
    daily = db.get_daily_stats(7)
    pending = db.get_pending_draft_count()
    return templates.TemplateResponse(
        request=request, name="analytics.html",
        context={"data": data, "daily": daily, "pending_drafts": pending,
                 "log_file": os.path.basename(_log_file)},
    )


# ── 黑白名单设置路由 ──────────────────────────────────────────────────────────

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    rules = db.list_rules()
    accounts = db.list_email_accounts()
    return templates.TemplateResponse(
        request=request, name="settings.html",
        context={"rules": rules, "accounts": accounts},
    )


@app.post("/settings/account/add")
async def account_add(
    label:     str = Form(...),
    imap_host: str = Form(...),
    imap_port: str = Form("993"),
    imap_user: str = Form(...),
    imap_pass: str = Form(...),
    smtp_host: str = Form(""),
    smtp_port: str = Form("465"),
):
    db.add_email_account(label, imap_host, imap_port, imap_user, imap_pass,
                         smtp_host, smtp_port)
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.post("/settings/account/{acc_id}/delete")
async def account_delete(acc_id: int):
    db.delete_email_account(acc_id)
    return RedirectResponse("/settings?deleted=1", status_code=303)


@app.post("/settings/account/{acc_id}/toggle")
async def account_toggle(acc_id: int):
    db.toggle_email_account(acc_id)
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/rule/add")
async def rule_add(pattern: str = Form(...), rule: str = Form(...), note: str = Form("")):
    if pattern and rule in ("block", "trust"):
        db.add_rule(pattern, rule, note)
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.post("/settings/rule/{rule_id}/delete")
async def rule_delete(rule_id: int):
    db.delete_rule(rule_id)
    return RedirectResponse("/settings?deleted=1", status_code=303)


# ── 公司信息管理 ──────────────────────────────────────────────────────────────

# 已知占位符值，命中任意一个说明未完成配置
_PLACEHOLDER_COMPANY = {
    "sales@yourdomain.com", "your@email.com",
    "+86-xxx-xxxx-xxxx", "+86-",
    "yourdomain.com", "https://yourdomain.com",
    "your company", "Your Company",
}

def _company_info() -> dict:
    return {
        "name":    os.getenv("COMPANY_NAME", ""),
        "desc":    os.getenv("COMPANY_DESC", ""),
        "website": os.getenv("COMPANY_WEBSITE", ""),
        "email":   os.getenv("COMPANY_EMAIL", ""),
        "phone":   os.getenv("COMPANY_PHONE", ""),
    }

def _company_has_placeholder() -> list[str]:
    """返回含占位符的字段名列表，空列表表示配置完整"""
    info = _company_info()
    bad = []
    for field, val in info.items():
        if not val or any(p in val for p in _PLACEHOLDER_COMPANY):
            bad.append(field)
    return bad

def _save_company_to_env(data: dict):
    """将公司信息写回 .env 文件并同步 os.environ"""
    _env_path = os.path.join(os.path.dirname(__file__), ".env")
    mapping = {
        "COMPANY_NAME":    data.get("name", ""),
        "COMPANY_DESC":    data.get("desc", ""),
        "COMPANY_WEBSITE": data.get("website", ""),
        "COMPANY_EMAIL":   data.get("email", ""),
        "COMPANY_PHONE":   data.get("phone", ""),
    }
    with open(_env_path, "r", encoding="utf-8") as f:
        content = f.read()
    for key, val in mapping.items():
        val_escaped = val.replace("\\", "\\\\")
        if f"{key}=" in content:
            content = _re.sub(rf"^{key}=.*", f"{key}={val_escaped}", content, flags=_re.MULTILINE)
        else:
            content += f"\n{key}={val_escaped}"
        os.environ[key] = val
    with open(_env_path, "w", encoding="utf-8") as f:
        f.write(content)


@app.get("/settings/company", response_class=HTMLResponse)
async def company_settings_page(request: Request):
    bad_fields = _company_has_placeholder()
    return templates.TemplateResponse(
        request=request, name="company.html",
        context={"info": _company_info(), "bad_fields": bad_fields, "saved": False},
    )


@app.post("/settings/company", response_class=HTMLResponse)
async def company_settings_save(
    request: Request,
    name:    str = Form(""),
    desc:    str = Form(""),
    website: str = Form(""),
    email:   str = Form(""),
    phone:   str = Form(""),
):
    _save_company_to_env({"name": name, "desc": desc,
                          "website": website, "email": email, "phone": phone})
    bad_fields = _company_has_placeholder()
    return templates.TemplateResponse(
        request=request, name="company.html",
        context={"info": _company_info(), "bad_fields": bad_fields, "saved": True},
    )


# ── AI 配置路由 ──────────────────────────────────────────────────────────────

def _save_ai_config_to_env(provider: str, api_key: str | None):
    """将 LLM 提供商和 API Key 写回 .env 并热更新 os.environ"""
    _env_path = os.path.join(os.path.dirname(__file__), ".env")
    with open(_env_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 更新 LLM_PROVIDER
    if "LLM_PROVIDER=" in content:
        content = _re.sub(r"^LLM_PROVIDER=.*", f"LLM_PROVIDER={provider}", content, flags=_re.MULTILINE)
    else:
        content += f"\nLLM_PROVIDER={provider}"
    os.environ["LLM_PROVIDER"] = provider

    # 更新 API Key（仅在用户填了新值时）
    if api_key:
        from ai_processor import PROVIDERS
        key_env = PROVIDERS[provider]["api_key_env"]
        if f"{key_env}=" in content:
            content = _re.sub(rf"^{key_env}=.*", f"{key_env}={api_key}", content, flags=_re.MULTILINE)
        else:
            content += f"\n{key_env}={api_key}"
        os.environ[key_env] = api_key

    with open(_env_path, "w", encoding="utf-8") as f:
        f.write(content)


def _mask_key(key: str) -> str:
    """遮蔽 API Key：前3后3，中间 ****"""
    if not key or len(key) < 8:
        return key or ""
    return key[:3] + "****" + key[-3:]


@app.get("/settings/ai", response_class=HTMLResponse)
async def ai_settings_page(request: Request):
    from ai_processor import PROVIDERS
    health = get_ai_health()
    provider = os.getenv("LLM_PROVIDER", "qianwen")
    key_env = PROVIDERS.get(provider, PROVIDERS["qianwen"])["api_key_env"]
    raw_key = os.getenv(key_env, "")
    return templates.TemplateResponse(
        request=request, name="settings_ai.html",
        context={"ai": health, "current_provider": provider,
                 "masked_key": _mask_key(raw_key)},
    )


@app.post("/settings/ai/save", response_class=HTMLResponse)
async def ai_settings_save(
    request: Request,
    provider: str = Form("qianwen"),
    api_key:  str = Form(""),
):
    from ai_processor import PROVIDERS
    if provider not in PROVIDERS:
        provider = "qianwen"
    api_key = api_key.strip() or None
    _save_ai_config_to_env(provider, api_key)

    # 保存后自动测试
    change = check_ai_health()
    health = get_ai_health()
    test_ok = health["status"] == "healthy"
    test_error = health["error_message"] if not test_ok else None

    if change:
        _push_event({"type": "ai_status_changed", "old": change[0], "new": change[1],
                      "error": health.get("error_message")})

    key_env = PROVIDERS.get(provider, PROVIDERS["qianwen"])["api_key_env"]
    raw_key = os.getenv(key_env, "")
    return templates.TemplateResponse(
        request=request, name="settings_ai.html",
        context={"ai": health, "current_provider": provider,
                 "masked_key": _mask_key(raw_key),
                 "saved": True, "save_test_ok": test_ok, "save_test_error": test_error},
    )


@app.post("/settings/ai/test", response_class=HTMLResponse)
async def ai_settings_test(request: Request):
    from ai_processor import PROVIDERS
    change = check_ai_health()
    health = get_ai_health()
    provider = os.getenv("LLM_PROVIDER", "qianwen")
    key_env = PROVIDERS.get(provider, PROVIDERS["qianwen"])["api_key_env"]
    raw_key = os.getenv(key_env, "")

    if change:
        _push_event({"type": "ai_status_changed", "old": change[0], "new": change[1],
                      "error": health.get("error_message")})

    test_result = {
        "ok": health["status"] == "healthy",
        "model": os.getenv("LLM_MODEL", "qwen-plus"),
        "error": health["error_message"],
    }
    return templates.TemplateResponse(
        request=request, name="settings_ai.html",
        context={"ai": health, "current_provider": provider,
                 "masked_key": _mask_key(raw_key),
                 "test_result": test_result},
    )


# ── 产品库管理 ────────────────────────────────────────────────────────────────

@app.get("/settings/products", response_class=HTMLResponse)
async def products_page(request: Request):
    products = get_products()
    return templates.TemplateResponse(
        request=request, name="products.html",
        context={"products": products},
    )


@app.get("/settings/products/template")
async def products_template():
    """下载通用产品 CSV 模板"""
    from fastapi.responses import Response
    csv_content = (
        "product_code,product_name,display_name,category,grade,unit,moq,price_usd,lead_time_days,description\r\n"
        "P001,示例产品 A,Example Product A,Category 1,Model-X1,pcs,100,15.00,14,Product description and application\r\n"
        "P002,示例产品 B,Example Product B,Category 2,Model-Y2,kg,50,28.50,21,Another product description\r\n"
    )
    return Response(
        content=csv_content.encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=products_template.csv"},
    )


@app.post("/settings/products/upload")
async def products_upload(request: Request):
    """上传 CSV 文件覆盖现有产品库"""
    import csv as _csv, io as _io
    form = await request.form()
    upload = form.get("file")
    if not upload:
        return RedirectResponse("/settings/products?err=no_file", status_code=303)
    content = await upload.read()
    # 校验 CSV 必要列是否存在
    _REQUIRED_COLS = {"product_name", "category"}
    try:
        reader = _csv.DictReader(_io.StringIO(content.decode("utf-8-sig", errors="replace")))
        headers = set(h.strip().lower() for h in (reader.fieldnames or []))
        missing = _REQUIRED_COLS - headers
        if missing:
            return RedirectResponse(f"/settings/products?err=missing_cols&cols={','.join(missing)}", status_code=303)
    except Exception:
        return RedirectResponse("/settings/products?err=invalid_csv", status_code=303)
    path = os.getenv("PRODUCTS_CSV", "products.csv")
    with open(path, "wb") as f:
        f.write(content)
    reload_products()
    count = len(get_products())
    return RedirectResponse(f"/settings/products?uploaded={count}", status_code=303)


@app.post("/settings/products/import-web")
async def products_import_web(request: Request, background_tasks: BackgroundTasks,
                               url: str = Form(...)):
    """后台抓取网站并提取产品，存入 session 供预览"""
    background_tasks.add_task(_import_from_web, url)
    return RedirectResponse(f"/settings/products?importing=1", status_code=303)


def _import_from_web(url: str):
    print(f"  [产品导入] 开始抓取: {url}")
    result = extract_products_from_url(url)
    if result.get("error"):
        print(f"  [产品导入] 失败: {result['error']}")
        return
    products = result.get("products", [])
    if not products:
        print("  [产品导入] AI 未提取到任何产品")
        return
    # 写入 CSV（追加模式：如已有产品则合并，否则直接写入）
    import csv as _csv
    path = os.getenv("PRODUCTS_CSV", "products.csv")
    fieldnames = ["product_code", "product_name", "display_name", "category",
                  "grade", "unit", "moq", "price_usd", "lead_time_days", "description"]
    existing = get_products()
    existing_names = {p.get("product_name", "").lower() for p in existing}
    new_rows = []
    for i, p in enumerate(products):
        if p.get("product_name", "").lower() not in existing_names:
            new_rows.append({
                "product_code":   f"W{i+1:03d}",
                "product_name":   p.get("product_name", ""),
                "display_name":   p.get("product_name", ""),
                "category":       p.get("category", ""),
                "grade":          "",
                "unit":           p.get("unit", "pcs"),
                "moq":            p.get("moq", 0),
                "price_usd":      p.get("price_usd", 0),
                "lead_time_days": p.get("lead_time_days", 0),
                "description":    p.get("description", ""),
            })
    file_has_content = os.path.exists(path) and os.path.getsize(path) > 0
    write_header = not existing and not file_has_content
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        writer = _csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)
    reload_products()
    print(f"  [产品导入] 完成，新增 {len(new_rows)} 条产品，共 {len(get_products())} 条")
