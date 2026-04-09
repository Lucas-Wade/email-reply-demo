import sqlite3
import json
import os
import re
import base64
import hashlib
from datetime import datetime as _dt

DB_PATH = os.getenv("DB_PATH", "email_reply.db")


# ── 邮箱密码加密工具 ──────────────────────────────────────────────────────────
# 使用 Fernet 对称加密，密钥由 SECRET_KEY 派生（SHA-256 → base64url）。
# 旧版明文密码读取时自动兼容：Fernet token 固定以 b"gAAAAA" 开头，否则视为明文。

def _fernet():
    from cryptography.fernet import Fernet
    secret = os.getenv("SECRET_KEY", "dev-secret-please-change-in-production")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)

def _enc(password: str) -> str:
    """加密明文密码，返回可存 DB 的字符串"""
    if not password:
        return password
    return _fernet().encrypt(password.encode()).decode()

def _dec(token: str) -> str:
    """解密密码；自动兼容旧版明文（Fernet token 固定以 gAAAAA 开头）"""
    if not token:
        return token
    if token.startswith("gAAAAA"):
        try:
            return _fernet().decrypt(token.encode()).decode()
        except Exception:
            pass  # 解密失败则原样返回（密钥被换过）
    return token  # 旧版明文，直接返回


def get_conn():
    # timeout=10：Python 层面等待锁最多 10 秒再抛 OperationalError
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL 模式允许读写并发（读不阻塞写，写不阻塞读），解决多线程锁争用
    conn.execute("PRAGMA journal_mode=WAL")
    # SQLite 层面：锁被占用时最多等 5000ms，避免立即失败
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    conn = get_conn()
    # 主表结构
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS emails (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            uid              TEXT UNIQUE,
            subject          TEXT,
            sender           TEXT,
            received_at      TEXT,
            body_text        TEXT,
            language         TEXT,
            category         TEXT,
            status           TEXT DEFAULT 'new',
            classify_layer   INTEGER,
            classify_score   REAL,
            classify_criteria TEXT,
            created_at       TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS drafts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id        INTEGER REFERENCES emails(id),
            subject         TEXT,
            body            TEXT,
            quoted_products TEXT,
            status          TEXT DEFAULT 'pending',
            created_at      TEXT DEFAULT (datetime('now')),
            sent_at         TEXT
        );

        CREATE TABLE IF NOT EXISTS bg_checks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id     INTEGER REFERENCES emails(id) UNIQUE,
            risk_level   TEXT,
            buyer_type   TEXT,
            domain_type  TEXT,
            red_flags    TEXT,
            positives    TEXT,
            recommendation TEXT,
            summary      TEXT,
            created_at   TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    # 常用查询列建索引（IF NOT EXISTS 确保幂等）
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_emails_category    ON emails(category)",
        "CREATE INDEX IF NOT EXISTS idx_emails_status      ON emails(status)",
        "CREATE INDEX IF NOT EXISTS idx_emails_received_at ON emails(received_at)",
        "CREATE INDEX IF NOT EXISTS idx_emails_sender      ON emails(sender)",
        "CREATE INDEX IF NOT EXISTS idx_drafts_status      ON drafts(status)",
        "CREATE INDEX IF NOT EXISTS idx_drafts_email_id    ON drafts(email_id)",
    ]:
        conn.execute(idx_sql)
    conn.commit()
    # 为旧库动态补列（列已存在时 SQLite 会报错，忽略即可）
    for col, coltype in [
        ("classify_layer", "INTEGER"),
        ("classify_score", "REAL"),
        ("classify_criteria", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE emails ADD COLUMN {col} {coltype}")
            conn.commit()
        except Exception:
            pass
    # drafts.sent_at may not exist in older DBs
    try:
        conn.execute("ALTER TABLE drafts ADD COLUMN sent_at TEXT")
        conn.commit()
    except Exception:
        pass
    # drafts.parsed_inquiry stores AI-extracted fields for the verification card
    try:
        conn.execute("ALTER TABLE drafts ADD COLUMN parsed_inquiry TEXT")
        conn.commit()
    except Exception:
        pass
    # intent_score and thread_email_id for emails
    for col, coltype in [
        ("intent_score",    "INTEGER"),
        ("thread_email_id", "INTEGER"),
    ]:
        try:
            conn.execute(f"ALTER TABLE emails ADD COLUMN {col} {coltype}")
            conn.commit()
        except Exception:
            pass
    conn.close()


def init_email_accounts_table():
    """多邮箱账号表"""
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_accounts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            label      TEXT NOT NULL,
            imap_host  TEXT NOT NULL,
            imap_port  INTEGER DEFAULT 993,
            imap_user  TEXT NOT NULL UNIQUE,
            imap_pass  TEXT NOT NULL,
            smtp_host  TEXT,
            smtp_port  INTEGER DEFAULT 465,
            smtp_user  TEXT,
            smtp_pass  TEXT,
            active     INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    # emails 表记录来源账号
    try:
        conn.execute("ALTER TABLE emails ADD COLUMN account_email TEXT")
        conn.commit()
    except Exception:
        pass
    conn.close()


def _decrypt_account(row: dict) -> dict:
    """将账号 dict 中的加密密码字段解密（就地修改并返回）"""
    row["imap_pass"] = _dec(row.get("imap_pass", ""))
    row["smtp_pass"] = _dec(row.get("smtp_pass", ""))
    return row


def list_email_accounts(active_only=False) -> list:
    conn = get_conn()
    try:
        sql = "SELECT * FROM email_accounts"
        if active_only:
            sql += " WHERE active=1"
        sql += " ORDER BY created_at"
        return [_decrypt_account(dict(r)) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def add_email_account(label, imap_host, imap_port, imap_user, imap_pass,
                      smtp_host="", smtp_port=465, smtp_user="", smtp_pass=""):
    conn = get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO email_accounts
               (label, imap_host, imap_port, imap_user, imap_pass,
                smtp_host, smtp_port, smtp_user, smtp_pass)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (label, imap_host, int(imap_port), imap_user,
             _enc(imap_pass),                               # 加密存储
             smtp_host or imap_host, int(smtp_port),
             smtp_user or imap_user,
             _enc(smtp_pass or imap_pass)),                 # 加密存储
        )
        conn.commit()
    finally:
        conn.close()


def delete_email_account(account_id: int):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM email_accounts WHERE id=?", (account_id,))
        conn.commit()
    finally:
        conn.close()


def toggle_email_account(account_id: int):
    conn = get_conn()
    try:
        conn.execute("UPDATE email_accounts SET active=NOT active WHERE id=?", (account_id,))
        conn.commit()
    finally:
        conn.close()


def get_email_account_by_user(imap_user: str) -> dict | None:
    """根据 imap_user 查出完整凭据（密码已解密），用于 IMAP/SMTP 连接"""
    if not imap_user:
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM email_accounts WHERE imap_user=?", (imap_user,)
        ).fetchone()
        return _decrypt_account(dict(row)) if row else None
    finally:
        conn.close()


def init_rules_table():
    """黑白名单规则表（domain 或完整邮件地址）"""
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sender_rules (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern    TEXT UNIQUE,
            rule       TEXT NOT NULL,   -- 'block' | 'trust'
            note       TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def check_sender_rule(sender: str):
    """返回 ('block'|'trust', matched_pattern) 或 (None, None)"""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT pattern, rule FROM sender_rules").fetchall()
        sender_lower = sender.lower()
        for row in rows:
            if row["pattern"].lower() in sender_lower:
                return row["rule"], row["pattern"]
        return None, None
    finally:
        conn.close()


def list_rules():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM sender_rules ORDER BY created_at DESC"
        ).fetchall()]
    finally:
        conn.close()


def add_rule(pattern: str, rule: str, note: str = ""):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sender_rules (pattern, rule, note) VALUES (?,?,?)",
            (pattern.strip(), rule, note),
        )
        conn.commit()
    finally:
        conn.close()


def delete_rule(rule_id: int):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM sender_rules WHERE id=?", (rule_id,))
        conn.commit()
    finally:
        conn.close()


def init_users_table():
    """用户账号表"""
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name          TEXT,
            created_at    TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def get_user_by_email(email: str) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(email)=LOWER(?)", (email,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_user(email: str, password_hash: str, name: str = "") -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO users (email, password_hash, name) VALUES (?,?,?)",
            (email, password_hash, name),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_user_password(email: str, password_hash: str):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE users SET password_hash=? WHERE LOWER(email)=LOWER(?)",
            (password_hash, email),
        )
        conn.commit()
    finally:
        conn.close()


def count_users() -> int:
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()


def find_thread_parent(subject: str) -> int | None:
    """若 subject 以 Re: 开头，查找原始邮件 id（最近一封匹配的）"""
    clean = re.sub(r"^(Re:\s*)+", "", subject, flags=re.IGNORECASE).strip()
    if not clean or clean == subject:
        return None
    # 转义 LIKE 通配符，防止主题中含 % 或 _ 匹配到无关邮件
    escaped = clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT id FROM emails
               WHERE LOWER(subject) LIKE LOWER(?) ESCAPE '\\'
               ORDER BY received_at DESC LIMIT 1""",
            (f"%{escaped}%",),
        ).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def save_email(uid, subject, sender, received_at, body_text, language, category,
               classify_layer=None, classify_score=None, classify_criteria=None,
               intent_score=None, thread_email_id=None, account_email=None):
    conn = get_conn()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO emails
               (uid, subject, sender, received_at, body_text, language, category,
                classify_layer, classify_score, classify_criteria,
                intent_score, thread_email_id, account_email)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, subject, sender, received_at, body_text, language, category,
             classify_layer, classify_score,
             json.dumps(classify_criteria, ensure_ascii=False) if classify_criteria else None,
             intent_score, thread_email_id, account_email),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM emails WHERE uid=?", (uid,)).fetchone()
        return row["id"]
    finally:
        conn.close()


def save_draft(email_id, subject, body, quoted_products, parsed_inquiry=None):
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO drafts (email_id, subject, body, quoted_products, parsed_inquiry)
               VALUES (?, ?, ?, ?, ?)""",
            (email_id, subject, body,
             json.dumps(quoted_products, ensure_ascii=False),
             json.dumps(parsed_inquiry, ensure_ascii=False) if parsed_inquiry else None),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def save_email_with_draft(uid, subject, sender, received_at, body_text, language,
                          classify_layer, classify_score, classify_criteria,
                          intent_score, thread_email_id, account_email,
                          draft_subject, draft_body, quoted_products, parsed_inquiry):
    """
    原子写入邮件 + 草稿 + 状态更新，三步在同一个连接的事务中完成。
    若任意步骤失败，全部回滚，避免出现"邮件已保存但草稿不存在"的孤儿记录。
    返回 (email_id, draft_id)。
    """
    conn = get_conn()
    try:
        conn.execute("BEGIN")
        cur = conn.execute(
            """INSERT OR IGNORE INTO emails
               (uid, subject, sender, received_at, body_text, language, category,
                classify_layer, classify_score, classify_criteria,
                intent_score, thread_email_id, account_email, status)
               VALUES (?, ?, ?, ?, ?, ?, 'valid_inquiry', ?, ?, ?, ?, ?, ?, 'new')""",
            (uid, subject, sender, received_at, body_text, language,
             classify_layer, classify_score,
             json.dumps(classify_criteria, ensure_ascii=False) if classify_criteria else None,
             intent_score, thread_email_id, account_email),
        )
        row = conn.execute("SELECT id FROM emails WHERE uid=?", (uid,)).fetchone()
        email_id = row["id"]
        # 若 INSERT 被 IGNORE（UID 已存在），lastrowid 为 0；此时跳过草稿插入，防止重复草稿
        if cur.lastrowid == 0:
            conn.commit()
            existing_draft = conn.execute(
                "SELECT id FROM drafts WHERE email_id=? ORDER BY id DESC LIMIT 1", (email_id,)
            ).fetchone()
            return email_id, (existing_draft["id"] if existing_draft else None)
        draft_cur = conn.execute(
            """INSERT INTO drafts (email_id, subject, body, quoted_products, parsed_inquiry)
               VALUES (?, ?, ?, ?, ?)""",
            (email_id, draft_subject, draft_body,
             json.dumps(quoted_products, ensure_ascii=False),
             json.dumps(parsed_inquiry, ensure_ascii=False) if parsed_inquiry else None),
        )
        draft_id = draft_cur.lastrowid
        conn.execute("UPDATE emails SET status='drafted' WHERE id=?", (email_id,))
        conn.commit()
        return email_id, draft_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_email_read(email_id):
    """仅当状态为 new 时标为 read，避免覆盖 drafted/sent/ignored"""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE emails SET status='read' WHERE id=? AND status='new'",
            (email_id,),
        )
        conn.commit()
    finally:
        conn.close()


def update_email_status(email_id, status):
    conn = get_conn()
    try:
        conn.execute("UPDATE emails SET status=? WHERE id=?", (status, email_id))
        conn.commit()
    finally:
        conn.close()


def update_draft(draft_id, subject, body):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE drafts SET subject=?, body=? WHERE id=?",
            (subject, body, draft_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_draft_status(draft_id, status, sent_at=None, send_error=None):
    conn = get_conn()
    try:
        # 动态补列（旧库兼容）
        for col, coltype in [("send_error", "TEXT")]:
            try:
                conn.execute(f"ALTER TABLE drafts ADD COLUMN {col} {coltype}")
                conn.commit()
            except Exception:
                pass
        conn.execute(
            "UPDATE drafts SET status=?, sent_at=?, send_error=? WHERE id=?",
            (status, sent_at, send_error, draft_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_draft(draft_id):
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT d.*, e.sender, e.subject AS original_subject,
                      e.body_text, e.language, e.received_at,
                      e.intent_score, e.thread_email_id, e.account_email
               FROM drafts d JOIN emails e ON d.email_id = e.id
               WHERE d.id=?""",
            (draft_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_draft_by_email_id(email_id: int):
    """根据 email_id 查最新草稿（与 get_draft 相同结构，按 id DESC 取第一条）"""
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT d.*, e.sender, e.subject AS original_subject,
                      e.body_text, e.language, e.received_at,
                      e.intent_score, e.thread_email_id, e.account_email
               FROM drafts d JOIN emails e ON d.email_id = e.id
               WHERE d.email_id=?
               ORDER BY d.id DESC LIMIT 1""",
            (email_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _build_email_where(account_email=None, keyword=None, category=None,
                       status_filter=None, date_from=None, date_to=None):
    """构建 WHERE 子句和参数列表，供 count_emails / list_emails 复用"""
    clauses, params = [], []
    if account_email:
        clauses.append("e.account_email = ?")
        params.append(account_email)
    if keyword:
        clauses.append("(e.subject LIKE ? OR e.sender LIKE ? OR e.body_text LIKE ?)")
        like = f"%{keyword}%"
        params += [like, like, like]
    if category and category != "all":
        clauses.append("e.category = ?")
        params.append(category)
    if status_filter:
        # drafted 状态在 emails 表里可能是 'new'，草稿状态在 drafts 表；
        # 对用户暴露的"待审核"映射为 drafts.status=pending
        if status_filter == "drafted":
            clauses.append("EXISTS (SELECT 1 FROM drafts d2 WHERE d2.email_id=e.id AND d2.status='pending')")
        else:
            clauses.append("e.status = ?")
            params.append(status_filter)
    if date_from:
        clauses.append("e.received_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("e.received_at <= ?")
        params.append(date_to + "T23:59:59")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def count_emails(account_email=None, keyword=None, category=None,
                 status_filter=None, date_from=None, date_to=None) -> int:
    where, params = _build_email_where(account_email, keyword, category, status_filter, date_from, date_to)
    conn = get_conn()
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM emails e {where}", params
        ).fetchone()[0]
    finally:
        conn.close()


_SORT_COLS = {
    "received_at": "e.received_at",
    "sender":      "e.sender",
    "category":    "e.category",
    "status":      "e.status",
}

def list_emails(limit=50, offset=0, account_email=None,
                keyword=None, category=None, status_filter=None,
                date_from=None, date_to=None,
                sort="received_at", order="desc"):
    where, params = _build_email_where(account_email, keyword, category, status_filter, date_from, date_to)
    sort_col = _SORT_COLS.get(sort, "e.received_at")
    sort_dir = "ASC" if order == "asc" else "DESC"
    conn = get_conn()
    try:
        sql = f"""SELECT e.id, e.subject, e.sender, e.category, e.status,
                         e.language, e.received_at, e.body_text,
                         e.classify_layer, e.classify_score, e.classify_criteria,
                         e.intent_score, e.thread_email_id,
                         d.id AS draft_id, d.status AS draft_status,
                         (SELECT COUNT(*) FROM emails r WHERE r.thread_email_id = e.id) AS reply_count
                  FROM emails e
                  LEFT JOIN drafts d ON d.id = (SELECT id FROM drafts WHERE email_id = e.id ORDER BY id DESC LIMIT 1)
                  {where}
                  ORDER BY {sort_col} {sort_dir} LIMIT ? OFFSET ?"""
        rows = conn.execute(sql, params + [limit, offset]).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def bulk_update_email_status(ids: list, status: str):
    """批量更新邮件状态，ids 为整数列表，status 只允许 'read' 或 'ignored'。"""
    if not ids or status not in ("read", "ignored"):
        return
    conn = get_conn()
    try:
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE emails SET status=? WHERE id IN ({placeholders})",
            [status] + list(ids),
        )
        conn.commit()
    finally:
        conn.close()


def save_bg_check(email_id, result: dict):
    conn = get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO bg_checks
               (email_id, risk_level, buyer_type, domain_type,
                red_flags, positives, recommendation, summary)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                email_id,
                result.get("risk_level"),
                result.get("buyer_type"),
                result.get("domain_type"),
                json.dumps(result.get("red_flags", []), ensure_ascii=False),
                json.dumps(result.get("positive_signals", []), ensure_ascii=False),
                result.get("recommendation"),
                result.get("summary"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_bg_check(email_id):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM bg_checks WHERE email_id=?", (email_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_email(email_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM emails WHERE id=?", (email_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_email_thread(email_id: int) -> list[dict]:
    """
    返回以 email_id 为根的完整往来记录，每条包含 email 字段 + draft（若有）。
    顺序：按 received_at ASC（时间线顺序）。
    涵盖：原始邮件、所有 thread_email_id=email_id 的回复、以及再往后的 Re:Re: 链。
    """
    conn = get_conn()
    try:
        # 收集线程中所有 email id：BFS 向下展开，visited 集合防止循环引用死循环
        visited: set[int] = {email_id}
        all_ids: list[int] = [email_id]
        frontier = [email_id]
        while frontier:
            placeholders = ",".join("?" * len(frontier))
            rows = conn.execute(
                f"SELECT id FROM emails WHERE thread_email_id IN ({placeholders})",
                frontier,
            ).fetchall()
            next_level = [r["id"] for r in rows if r["id"] not in visited]
            for nid in next_level:
                visited.add(nid)
            all_ids.extend(next_level)
            frontier = next_level

        if not all_ids:
            return []

        placeholders = ",".join("?" * len(all_ids))
        emails = conn.execute(
            f"""SELECT e.*, d.id AS draft_id, d.status AS draft_status,
                       d.subject AS draft_subject, d.sent_at AS draft_sent_at
                FROM emails e
                LEFT JOIN drafts d ON d.email_id = e.id
                WHERE e.id IN ({placeholders})
                ORDER BY e.received_at ASC""",
            all_ids,
        ).fetchall()
        return [dict(r) for r in emails]
    finally:
        conn.close()


def update_email_classification(email_id: int, category: str, classify_layer,
                                 classify_score, classify_criteria, intent_score=None):
    """重新处理后更新邮件分类字段"""
    conn = get_conn()
    try:
        conn.execute("""
            UPDATE emails
            SET category=?, classify_layer=?, classify_score=?, classify_criteria=?,
                intent_score=?, status='new'
            WHERE id=?
        """, (category, classify_layer, classify_score,
              json.dumps(classify_criteria) if isinstance(classify_criteria, dict) else classify_criteria,
              intent_score, email_id))
        conn.commit()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 跟进提醒 (followups)
# ══════════════════════════════════════════════════════════════════

def init_followups_table():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS followups (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id    INTEGER REFERENCES emails(id),
            draft_id    INTEGER REFERENCES drafts(id),
            due_date    TEXT,
            status      TEXT DEFAULT 'pending',
            note        TEXT,
            followup_subject TEXT,
            followup_body    TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            sent_at     TEXT
        );
    """)
    conn.commit()
    conn.close()


def save_followup(email_id, draft_id, due_date, note=""):
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO followups (email_id, draft_id, due_date, note)
               VALUES (?, ?, ?, ?)""",
            (email_id, draft_id, due_date, note),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_followups(status=None, limit=200):
    conn = get_conn()
    try:
        if status:
            rows = conn.execute(
                """SELECT f.*, e.sender, e.subject AS original_subject,
                          e.language, d.subject AS draft_subject
                   FROM followups f
                   JOIN emails e ON f.email_id = e.id
                   LEFT JOIN drafts d ON f.draft_id = d.id
                   WHERE f.status=?
                   ORDER BY f.due_date ASC LIMIT ?""",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT f.*, e.sender, e.subject AS original_subject,
                          e.language, d.subject AS draft_subject
                   FROM followups f
                   JOIN emails e ON f.email_id = e.id
                   LEFT JOIN drafts d ON f.draft_id = d.id
                   ORDER BY f.due_date ASC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_followup(followup_id):
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT f.*, e.sender, e.subject AS original_subject,
                      e.body_text, e.language
               FROM followups f JOIN emails e ON f.email_id = e.id
               WHERE f.id=?""",
            (followup_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_followup(followup_id, status, subject=None, body=None, sent_at=None):
    conn = get_conn()
    try:
        fields = ["status=?"]
        vals = [status]
        if subject is not None:
            fields.append("followup_subject=?")
            vals.append(subject)
        if body is not None:
            fields.append("followup_body=?")
            vals.append(body)
        if sent_at is not None:
            fields.append("sent_at=?")
            vals.append(sent_at)
        vals.append(followup_id)
        conn.execute(f"UPDATE followups SET {', '.join(fields)} WHERE id=?", vals)
        conn.commit()
    finally:
        conn.close()


def get_overdue_followups():
    """返回今天及之前到期、状态为 pending 的跟进"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT f.*, e.sender, e.subject AS original_subject,
                      e.body_text, e.language
               FROM followups f JOIN emails e ON f.email_id = e.id
               WHERE f.status='pending' AND f.due_date <= date('now')""",
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_followup_stats():
    conn = get_conn()
    try:
        pending  = conn.execute("SELECT COUNT(*) FROM followups WHERE status='pending'").fetchone()[0]
        overdue  = conn.execute(
            "SELECT COUNT(*) FROM followups WHERE status='pending' AND due_date <= date('now')"
        ).fetchone()[0]
        done     = conn.execute("SELECT COUNT(*) FROM followups WHERE status='sent'").fetchone()[0]
        return {"pending": pending, "overdue": overdue, "done": done}
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 客户记忆 (customers)
# ══════════════════════════════════════════════════════════════════

def init_customers_table():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            domain          TEXT UNIQUE,
            company_name    TEXT,
            country         TEXT,
            buyer_type      TEXT,
            first_seen      TEXT,
            last_seen       TEXT,
            inquiry_count   INTEGER DEFAULT 0,
            quote_count     INTEGER DEFAULT 0,
            reply_count     INTEGER DEFAULT 0,
            notes           TEXT
        );
    """)
    conn.commit()
    conn.close()


def upsert_customer(domain, company_name=None, country=None,
                    buyer_type=None, is_new_inquiry=False, is_quoted=False, is_replied=False):
    if not domain:
        return
    conn = get_conn()
    try:
        now = _dt.now().isoformat()
        existing = conn.execute(
            "SELECT * FROM customers WHERE domain=?", (domain,)
        ).fetchone()
        if existing:
            fields, vals = [], []
            fields.append("last_seen=?"); vals.append(now)
            if company_name and not existing["company_name"]:
                fields.append("company_name=?"); vals.append(company_name)
            if country and not existing["country"]:
                fields.append("country=?"); vals.append(country)
            if buyer_type and not existing["buyer_type"]:
                fields.append("buyer_type=?"); vals.append(buyer_type)
            if is_new_inquiry:
                fields.append("inquiry_count=inquiry_count+1")
            if is_quoted:
                fields.append("quote_count=quote_count+1")
            if is_replied:
                fields.append("reply_count=reply_count+1")
            conn.execute(
                f"UPDATE customers SET {', '.join(fields)} WHERE domain=?",
                vals + [domain],
            )
        else:
            conn.execute(
                """INSERT INTO customers
                   (domain, company_name, country, buyer_type,
                    first_seen, last_seen, inquiry_count, quote_count, reply_count)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (domain, company_name, country, buyer_type,
                 now, now,
                 1 if is_new_inquiry else 0,
                 1 if is_quoted else 0,
                 1 if is_replied else 0),
            )
        conn.commit()
    finally:
        conn.close()


def get_customer_by_domain(domain):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM customers WHERE domain=?", (domain,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def count_customers(keyword=None) -> int:
    conn = get_conn()
    try:
        if keyword:
            like = f"%{keyword}%"
            return conn.execute(
                "SELECT COUNT(*) FROM customers WHERE domain LIKE ? OR company_name LIKE ?",
                (like, like),
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    finally:
        conn.close()


def list_customers(limit=50, offset=0, keyword=None):
    conn = get_conn()
    try:
        if keyword:
            like = f"%{keyword}%"
            rows = conn.execute(
                """SELECT * FROM customers
                   WHERE domain LIKE ? OR company_name LIKE ?
                   ORDER BY last_seen DESC LIMIT ? OFFSET ?""",
                (like, like, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM customers ORDER BY last_seen DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_customer_emails(domain, limit=20):
    """获取某个域名的所有历史邮件"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT e.*, d.id AS draft_id, d.status AS draft_status,
                      d.subject AS draft_subject
               FROM emails e
               LEFT JOIN drafts d ON d.email_id = e.id
               WHERE e.sender LIKE ?
               ORDER BY e.received_at DESC LIMIT ?""",
            (f"%@{domain}", limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 漏斗分析 (analytics)
# ══════════════════════════════════════════════════════════════════

def get_analytics():
    conn = get_conn()
    try:
        # 分类分布
        cats = conn.execute(
            "SELECT category, COUNT(*) AS cnt FROM emails GROUP BY category"
        ).fetchall()
        category_dist = {r["category"]: r["cnt"] for r in cats}

        # 状态漏斗（有效询盘）
        funnel = {
            "inquiries": conn.execute(
                "SELECT COUNT(*) FROM emails WHERE category='valid_inquiry'"
            ).fetchone()[0],
            "drafted": conn.execute(
                "SELECT COUNT(*) FROM drafts"
            ).fetchone()[0],
            "sent": conn.execute(
                "SELECT COUNT(*) FROM drafts WHERE status='sent'"
            ).fetchone()[0],
            "followed_up": conn.execute(
                "SELECT COUNT(*) FROM followups WHERE status='sent'"
            ).fetchone()[0] if _table_exists(conn, "followups") else 0,
        }

        # 近 8 周询盘量
        weekly = conn.execute(
            """SELECT strftime('%Y-W%W', received_at) AS week,
                      COUNT(*) AS cnt
               FROM emails
               WHERE category='valid_inquiry'
                 AND received_at >= date('now', '-56 days')
               GROUP BY week ORDER BY week""",
        ).fetchall()
        weekly_data = [{"week": r["week"], "cnt": r["cnt"]} for r in weekly]

        # 国家分布（来自背调）
        countries = conn.execute(
            """SELECT c.country, COUNT(*) AS cnt
               FROM customers c
               WHERE c.country IS NOT NULL AND c.country != ''
               GROUP BY c.country ORDER BY cnt DESC LIMIT 10"""
        ).fetchall() if _table_exists(conn, "customers") else []
        country_dist = [{"country": r["country"], "cnt": r["cnt"]} for r in countries]

        # 平均响应时间（收到询盘 → 草稿发送，单位：小时）
        avg_resp = conn.execute(
            """SELECT AVG(
                 (julianday(d.sent_at) - julianday(e.received_at)) * 24
               ) AS avg_hours
               FROM drafts d JOIN emails e ON d.email_id = e.id
               WHERE d.status='sent' AND d.sent_at IS NOT NULL"""
        ).fetchone()["avg_hours"]

        return {
            "category_dist": category_dist,
            "funnel": funnel,
            "weekly": weekly_data,
            "country_dist": country_dist,
            "avg_response_hours": round(avg_resp, 1) if avg_resp else None,
        }
    finally:
        conn.close()


def _table_exists(conn, table):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def count_stats(account_email=None):
    conn = get_conn()
    try:
        where = "WHERE account_email = ?" if account_email else ""
        args = (account_email,) if account_email else ()
        total = conn.execute(f"SELECT COUNT(*) FROM emails {where}", args).fetchone()[0]
        inquiries = conn.execute(
            f"SELECT COUNT(*) FROM emails {where + (' AND' if where else 'WHERE')} category='valid_inquiry'",
            args,
        ).fetchone()[0]
        # drafts 通过 email_id 关联过滤
        if account_email:
            pending = conn.execute(
                "SELECT COUNT(*) FROM drafts d JOIN emails e ON d.email_id=e.id WHERE d.status='pending' AND e.account_email=?",
                (account_email,),
            ).fetchone()[0]
            sent = conn.execute(
                "SELECT COUNT(*) FROM drafts d JOIN emails e ON d.email_id=e.id WHERE d.status='sent' AND e.account_email=?",
                (account_email,),
            ).fetchone()[0]
            won = conn.execute(
                "SELECT COUNT(*) FROM emails WHERE deal_status='won' AND account_email=?",
                (account_email,),
            ).fetchone()[0] if _col_exists(conn, "emails", "deal_status") else 0
        else:
            pending = conn.execute("SELECT COUNT(*) FROM drafts WHERE status='pending'").fetchone()[0]
            sent = conn.execute("SELECT COUNT(*) FROM drafts WHERE status='sent'").fetchone()[0]
            won = conn.execute(
                "SELECT COUNT(*) FROM emails WHERE deal_status='won'"
            ).fetchone()[0] if _col_exists(conn, "emails", "deal_status") else 0
        return {"total": total, "inquiries": inquiries, "pending": pending,
                "sent": sent, "won": won}
    finally:
        conn.close()


def get_daily_stats(days: int = 7) -> list[dict]:
    """返回最近 N 天每天的邮件处理统计，用于健康看板。"""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT
                date(created_at) AS day,
                COUNT(*) AS total,
                SUM(CASE WHEN category='valid_inquiry' THEN 1 ELSE 0 END) AS inquiries,
                SUM(CASE WHEN category='spam' THEN 1 ELSE 0 END) AS spam,
                SUM(CASE WHEN category='other' THEN 1 ELSE 0 END) AS other_cnt,
                SUM(CASE WHEN status IN ('drafted','sent') THEN 1 ELSE 0 END) AS drafted,
                SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) AS sent
            FROM emails
            WHERE created_at >= date('now', ?)
            GROUP BY date(created_at)
            ORDER BY day DESC
        """, (f"-{days} days",)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_pending_draft_count(account_email=None) -> int:
    """当前待审核草稿数；传入 account_email 则只统计该账号收到的邮件对应的草稿"""
    conn = get_conn()
    try:
        if account_email:
            return conn.execute(
                """SELECT COUNT(*) FROM drafts d
                   JOIN emails e ON e.id = d.email_id
                   WHERE d.status='pending' AND e.account_email=?""",
                (account_email,)
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM drafts WHERE status='pending'").fetchone()[0]
    finally:
        conn.close()


# ── 成单追踪 ─────────────────────────────────────────────────────────────────

def init_deal_columns():
    """为旧库补充成单追踪列"""
    conn = get_conn()
    for col, coltype in [("deal_status", "TEXT"), ("deal_amount", "REAL")]:
        try:
            conn.execute(f"ALTER TABLE emails ADD COLUMN {col} {coltype}")
            conn.commit()
        except Exception:
            pass
    conn.close()


def mark_deal(email_id: int, status: str, amount: float = 0.0):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE emails SET deal_status=?, deal_amount=? WHERE id=?",
            (status, amount, email_id),
        )
        conn.commit()
    finally:
        conn.close()


def _col_exists(conn, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == col for r in rows)


# ── 配置状态检测 ─────────────────────────────────────────────────────────────

def get_setup_status() -> dict:
    """返回各项配置是否完成，用于引导卡片"""
    from dotenv import load_dotenv
    load_dotenv()
    issues = []
    if not os.getenv("IMAP_HOST") or not os.getenv("IMAP_USER"):
        issues.append(("邮箱未配置", "前往设置配置企业邮箱账号，系统才能自动收信", "/settings"))
    if not os.getenv("QIANWEN_API_KEY") and not os.getenv("ZHIPU_API_KEY"):
        issues.append(("AI 接口未配置", "在 .env 文件中填入 QIANWEN_API_KEY 或 ZHIPU_API_KEY", "/help#env"))
    # 产品数量由调用方通过 product_matcher.get_products() 在内存中检测
    return {"issues": issues, "ok": len(issues) == 0}


# ── Demo 示例数据 ─────────────────────────────────────────────────────────────

DEMO_EMAILS = [
    {
        "uid": "demo-001", "subject": "Inquiry for carbide products",
        "sender": "Ahmad Al-Rashid <ahmad@gulf-trading.com>",
        "received_at": "2026-04-06T09:23:00",
        "body_text": "Dear Sir/Madam,\n\nWe are a trading company based in Dubai and are interested in purchasing carbide drilling buttons for oil field applications.\n\nPlease quote us:\n- Spherical buttons, diameter 13mm, quantity 50,000 pcs\n- Target price: USD 0.8/pcs\n- Payment: T/T\n\nLooking forward to your reply.\n\nBest regards,\nAhmad Al-Rashid\nGulf Trading LLC",
        "language": "en", "category": "valid_inquiry",
        "classify_layer": 2, "classify_score": 8.0,
    },
    {
        "uid": "demo-002", "subject": "Request for quotation - road milling tools",
        "sender": "Maria Garcia <m.garcia@constructora-iberia.es>",
        "received_at": "2026-04-06T14:05:00",
        "body_text": "Hola,\n\nSomos una empresa constructora en España. Necesitamos herramientas de fresado para carretera.\n\nCantidad: 2000 unidades\nUso: fresado de asfalto\nPor favor envíe cotización con precio CIF Barcelona.\n\nGracias,\nMaria Garcia",
        "language": "es", "category": "valid_inquiry",
        "classify_layer": 2, "classify_score": 7.0,
    },
    {
        "uid": "demo-003", "subject": "Re: Our quotation - Follow up",
        "sender": "James Wilson <j.wilson@miningco.au>",
        "received_at": "2026-04-07T03:41:00",
        "body_text": "Hi,\n\nThank you for your quick quotation. We've reviewed it with our team.\n\nCould you offer a better price if we increase the order to 100,000 pcs? Also, can you provide a sample shipment of 500 pcs first?\n\nRegards,\nJames Wilson\nMining Co. Australia",
        "language": "en", "category": "valid_inquiry",
        "classify_layer": 2, "classify_score": 9.0,
    },
    {
        "uid": "demo-004", "subject": "FREE OFFER - Business Promotion",
        "sender": "no-reply@marketing-blast.net",
        "received_at": "2026-04-07T08:00:00",
        "body_text": "Congratulations! You have been selected for our exclusive business promotion...",
        "language": "en", "category": "spam",
        "classify_layer": 1, "classify_score": None,
    },
    {
        "uid": "demo-005", "subject": "Tungsten carbide strips inquiry",
        "sender": "Liu Wei <liuwei@sino-imports.cn>",
        "received_at": "2026-04-08T07:15:00",
        "body_text": "您好，\n\n我公司是国内一家模具厂，需要采购硬质合金条，规格如下：\n- 牌号：YG8\n- 尺寸：4×20×100mm\n- 数量：500kg/月，长期需求\n- 请报含税价格\n\n期待您的回复。\n\n刘伟\n中诺进口贸易有限公司",
        "language": "zh", "category": "valid_inquiry",
        "classify_layer": 2, "classify_score": 9.0,
    },
]

def load_demo_data() -> int:
    """插入示例邮件，已有则跳过（uid 唯一）。返回实际插入数量。"""
    conn = get_conn()
    inserted = 0
    try:
        for e in DEMO_EMAILS:
            cur = conn.execute(
                """INSERT OR IGNORE INTO emails
                   (uid, subject, sender, received_at, body_text, language, category,
                    classify_layer, classify_score, status)
                   VALUES (?,?,?,?,?,?,?,?,?,'new')""",
                (e["uid"], e["subject"], e["sender"], e["received_at"],
                 e["body_text"], e["language"], e["category"],
                 e["classify_layer"], e["classify_score"]),
            )
            if cur.rowcount:
                inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted
