"""
认证模块：IMAP 验证登录 + 主流邮箱服务商自动识别
用户用真实邮箱 + 密码登录，系统通过 IMAP 验证，无需单独注册。

如需添加企业邮箱服务商，在同目录创建 providers.json：
  {"corp.example.com": ["imap.corp.example.com", 993, "smtp.corp.example.com", 465]}
此文件会在启动时自动合并，覆盖同名内置条目。
"""
import imaplib
import json
import os


# ── 主流邮箱服务商 IMAP/SMTP 配置 ────────────────────────────────────────────
# (imap_host, imap_port, smtp_host, smtp_port)
PROVIDERS: dict[str, tuple] = {
    # 腾讯系
    "exmail.qq.com":   ("imap.exmail.qq.com",  993, "smtp.exmail.qq.com",  465),
    "qq.com":          ("imap.qq.com",          993, "smtp.qq.com",         465),
    "foxmail.com":     ("imap.foxmail.com",      993, "smtp.foxmail.com",    465),
    # 网易系
    "163.com":         ("imap.163.com",          993, "smtp.163.com",        465),
    "126.com":         ("imap.126.com",          993, "smtp.126.com",        465),
    "yeah.net":        ("imap.yeah.net",          993, "smtp.yeah.net",       465),
    # 阿里系
    "aliyun.com":      ("imap.aliyun.com",       993, "smtp.aliyun.com",     465),
    "alibaba-inc.com": ("imap.alibaba-inc.com",  993, "smtp.alibaba-inc.com",465),
    # 新浪
    "sina.com":        ("imap.sina.com",         993, "smtp.sina.com",       465),
    "sina.cn":         ("imap.sina.com",         993, "smtp.sina.com",       465),
    # 移动 139
    "139.com":         ("imap.139.com",          993, "smtp.139.com",        465),
    # 国际
    "gmail.com":       ("imap.gmail.com",        993, "smtp.gmail.com",      465),
    "outlook.com":     ("outlook.office365.com", 993, "smtp.office365.com",  587),
    "hotmail.com":     ("outlook.office365.com", 993, "smtp.office365.com",  587),
    "live.com":        ("outlook.office365.com", 993, "smtp.office365.com",  587),
    "yahoo.com":       ("imap.mail.yahoo.com",   993, "smtp.mail.yahoo.com", 465),
    "icloud.com":      ("imap.mail.me.com",      993, "smtp.mail.me.com",    587),
}

# 用户自定义服务商：providers.json 同目录存在时自动合并（可覆盖内置条目）
_custom = os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers.json")
if os.path.isfile(_custom):
    try:
        with open(_custom, encoding="utf-8") as _f:
            for _domain, _cfg in json.load(_f).items():
                PROVIDERS[_domain.lower()] = tuple(_cfg)
    except Exception as _e:
        print(f"[auth] providers.json 加载失败，使用内置配置: {_e}")


def detect_provider(email: str) -> dict | None:
    """
    根据邮件域名自动返回 IMAP/SMTP 配置。
    已知服务商直接返回；未知域名尝试 mail.{domain} 推断。
    返回 None 表示需要用户手动填写。
    """
    if "@" not in email:
        return None
    domain = email.split("@")[-1].lower()
    if domain in PROVIDERS:
        h_imap, p_imap, h_smtp, p_smtp = PROVIDERS[domain]
        return {"imap_host": h_imap, "imap_port": p_imap,
                "smtp_host": h_smtp, "smtp_port": p_smtp,
                "label": domain}
    # 企业自建邮件服务器：尝试 mail.{domain}
    return {"imap_host": f"mail.{domain}", "imap_port": 993,
            "smtp_host": f"mail.{domain}", "smtp_port": 465,
            "label": domain, "guessed": True}


def verify_imap(email: str, password: str, imap_host: str, imap_port: int = 993) -> tuple[bool, str]:
    """
    用 IMAP SSL 验证邮箱凭据。
    返回 (True, "") 表示成功；(False, 错误原因) 表示失败。
    """
    import socket
    import ssl
    try:
        mail = imaplib.IMAP4_SSL(imap_host, imap_port)
        mail.login(email, password)
        mail.logout()
        return True, ""
    except imaplib.IMAP4.error as e:
        msg = str(e).lower()
        if any(k in msg for k in ("authenticationfailed", "invalid credentials",
                                   "authentication failed", "login failed")):
            return False, f"密码（或授权码）错误，请确认后重试。腾讯/163/QQ 邮箱需使用「授权码」而非登录密码"
        return False, f"IMAP 登录拒绝：{e}"
    except (socket.timeout, TimeoutError, ConnectionRefusedError) as e:
        return False, f"无法连接到 {imap_host}:{imap_port}，请检查服务器地址是否正确，或邮箱后台是否开启了 IMAP"
    except ssl.SSLError as e:
        return False, f"SSL 握手失败（{imap_host}:{imap_port}），请确认端口与加密方式匹配"
    except OSError as e:
        return False, f"网络错误：{e}，请检查服务器地址"
    except Exception as e:
        return False, f"连接失败：{e}"


def get_session_user(request) -> dict | None:
    """从 request.session 取出登录用户信息，未登录返回 None"""
    return request.session.get("user")
