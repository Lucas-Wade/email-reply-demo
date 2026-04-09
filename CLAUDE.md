# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# 启动 Web 应用
cd "D:/Work space/email-reply-demo"
venv/Scripts/python -m uvicorn main:app --port 8000

# 重启前确认只有一个 Python 进程
wmic process where "name='python.exe'" get processid,commandline
taskkill /F /PID <pid>

# 一次性批量处理（测试 AI 流水线，不启动 Web）
venv/Scripts/python demo.py

# 手动触发数据库备份
venv/Scripts/python backup.py

# 测试分类是否正常
venv/Scripts/python -c "
import sys; sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv; load_dotenv()
from ai_processor import classify_email
print(classify_email('Test inquiry', 'buyer@example.com', 'Please send quotation'))
"

# 查询数据库（调试用）
venv/Scripts/python -c "
import sys; sys.stdout.reconfigure(encoding='utf-8')
import sqlite3; conn = sqlite3.connect('email_reply.db'); conn.row_factory = sqlite3.Row
for r in conn.execute('SELECT id,uid,subject,category,status FROM emails ORDER BY id DESC LIMIT 10').fetchall(): print(dict(r))
"

# 清空所有数据（恢复出厂状态）
venv/Scripts/python -c "
import sqlite3; conn = sqlite3.connect('email_reply.db')
for t in ['bg_checks','customers','followups','drafts','emails','sender_rules','email_accounts','users','sqlite_sequence']:
    conn.execute(f'DELETE FROM {t}')
conn.commit()
"
```

## Architecture

### 两个入口，共享同一条流水线

```
IMAP INBOX (UNSEEN)
  └─ email_client.fetch_unread_emails()        # UID SEARCH + BODY.PEEK[]，不自动标已读
       └─ main._handle_one_email()
            ├─ db.check_sender_rule()               # 黑白名单优先（返回 tuple(rule, pattern)）
            ├─ ai_processor.describe_email_images() # Qwen-VL 图片识别（可选）
            ├─ inquiry_criteria.is_definite_spam()  # Layer 1：规则预过滤
            ├─ inquiry_criteria.has_inquiry_signal() # Layer 1：跳过 LLM 的快速通道
            ├─ ai_processor.classify_email()         # Layer 2：LLM 8项评分
            ├─ ai_processor.parse_inquiry()          # 结构化解析（产品/数量/语言）
            ├─ product_matcher.match_products()      # 关键词+同义词匹配
            ├─ ai_processor.generate_draft()         # 语言匹配的回复草稿
            └─ database.save_email_with_draft()      # 原子事务：邮件+草稿同时写入或全部回滚
                 └─ main._push_event()               # SSE 推送给浏览器
```

- **`demo.py`**：同步，处理最多 5 封，打印结果，用于测试流水线
- **`main.py`**：FastAPI + APScheduler，每 `POLL_INTERVAL` 秒（默认 60）轮询一次；`/poll` 手动触发

### 关键文件职责

| 文件 | 职责 |
|---|---|
| `main.py` | FastAPI 路由、APScheduler 调度、SSE 事件总线 |
| `email_client.py` | IMAP 收信（`fetch_unread_emails`）、初始同步（`fetch_recent_emails`）、SMTP 发信、`mark_emails_seen` |
| `ai_processor.py` | 所有 LLM 调用：分类、解析、生成草稿、背调、跟进草稿 |
| `inquiry_criteria.py` | 三层分类标准（规则 + 评分项 + 阈值），**调分类宽严改这里** |
| `product_matcher.py` | 纯关键词匹配，SYNONYMS + CATEGORY_HINTS 两张查找表 |
| `database.py` | SQLite 操作，每函数独立 connect/close，无连接池 |
| `auth.py` | IMAP 登录验证 + 主流邮箱服务商自动识别 |
| `backup.py` | SQLite 热备份（`VACUUM INTO` 优先，降级为 `shutil.copy2`），保留 30 份 |

### APScheduler 定时任务（3 个）

| Job ID | 触发方式 | 函数 |
|---|---|---|
| `poll_email` | 每 `POLL_INTERVAL` 秒 | `process_new_emails()` |
| `followup_check` | 每日 09:00 | `_check_overdue_followups()` |
| `daily_backup` | 每日 02:00 | `_run_daily_backup()` → `backup.run_backup()` |

### IMAP 收信的关键设计

`fetch_unread_emails` 使用：
- `mail.uid("search", None, "UNSEEN")` — 返回稳定的 IMAP UID
- `mail.uid("fetch", uid, "(BODY.PEEK[])")` — 获取邮件内容但**不标记 `\Seen`**
- 处理成功后调用 `mark_emails_seen()` 统一标已读；失败的保持 UNSEEN 下次重试

**HTML 邮件处理**：`_extract_text()` 优先提取 `text/plain` part；若邮件仅含 `text/html`，自动调用 `_strip_html()` 剥离标签后存为纯文本。

**首次登录初始同步**（`fetch_recent_emails`）：
- 只拉 INBOX，不遍历其他文件夹
- 默认拉取 90 天内邮件（`INIT_SYNC_DAYS` 环境变量可调）
- **仅在该账号首次添加时执行**，重启重新登录不会重复触发，避免历史邮件重复插入

### UID 格式规范

| 模式 | 格式 | 示例 |
|---|---|---|
| 多账号轮询（有 `email_accounts` 记录） | `{imap_user}:{imap_uid}` | `sales@co.com:42` |
| 首次登录同步 | `{email}:{imap_uid}` | `sales@co.com:42` |
| `.env` 单账号轮询（`email_accounts` 为空） | 纯 IMAP UID | `42` |

**注意**：`.env` 纯模式和账号模式的 uid 格式不同，一旦用户登录（`email_accounts` 有记录）系统会切换为账号模式，旧的纯数字 uid 记录不会自动迁移，会留存为孤立记录。

### 多账号调度

`process_new_emails()` 从 `email_accounts` 表取所有 `active=1` 的账号逐个处理。表为空时回退到 `.env` 单账号模式。启动两个进程会导致双调度竞争，重启前务必确认只有一个 Python 进程。

### SSE 实时推送机制

`main.py` 顶部维护 `_sse_clients: list[asyncio.Queue]`。APScheduler 后台线程通过 `_sse_loop.call_soon_threadsafe()` 跨线程写入队列，前端收到 `new_draft` 事件后弹 toast 并自动刷新页面。

### 三层邮件分类（`inquiry_criteria.py`）

```
Layer 1（零 API）: is_definite_spam() → spam
                   has_inquiry_signal() 无信号 → other
Layer 2（LLM）:    8 项评分（C1-C8），各项 0/1，权重见 SCORING_CRITERIA
Layer 3（阈值）:   总分 >= INQUIRY_THRESHOLD(3) → valid_inquiry
                   总分 >= OTHER_THRESHOLD(1)  → other
                   总分 < 1                    → spam
```

调整宽严只需改 `INQUIRY_THRESHOLD` 和 `OTHER_THRESHOLD`。C1（产品相关性，权重2）和 C2（询价意图，权重2）是主要得分项。

### 草稿状态机

```
new → drafted → sending → sent
                        ↘ send_failed   （SMTP 失败，可重试）
         ↘ rejected     （人工拒绝，邮件变为 ignored）
```

`draft_approve` 在后台任务启动**前**写入 `sending` 状态，防止双击重复发送。

### 原子写入（`save_email_with_draft`）

邮件 + 草稿在同一个 SQLite 连接的显式事务中完成。`INSERT OR IGNORE` 因 UID 重复被忽略时（`lastrowid == 0`）跳过草稿插入，返回已有草稿 ID。

### 批量操作

前端列表支持复选框多选，批量提交至 `POST /emails/bulk`（`ids` 逗号分隔，`action` 为 `read` 或 `ignored`）。后端通过 `db.bulk_update_email_status()` 执行，白名单校验防止非法 action。

### 邮件列表排序

`list_emails()` 接受 `sort`（列名）和 `order`（`asc`/`desc`）参数，合法列名由 `_SORT_COLS` 字典白名单控制，防 SQL 注入。URL 参数通过路由透传到数据库层。

### 数据库 Schema 要点

- `emails.uid` 有 `UNIQUE` 约束，`INSERT OR IGNORE` 天然去重
- `emails.status`：`new` → `drafted` → `sent`（或 `ignored` / `read`）
- `drafts.quoted_products` / `bg_checks.red_flags` 等字段存 JSON 字符串，模板中用 Jinja2 filter `| from_json` 解析
- 旧库升级通过 `ALTER TABLE ADD COLUMN`（忽略 already-exists 异常）完成，无迁移脚本
- 常用查询列均已建索引：`emails(category, status, received_at, sender)`、`drafts(status, email_id)`

### 日志与 PII 处理

- 日志写入 `logs/app.log`，`TimedRotatingFileHandler`，每日切换，保留 30 天
- 发件人日志用 `_log_sender(s)` → 仅显示 `*@domain`
- 主题日志用 `_log_subject(s)` → 截断至 20 字符

### 可扩展配置文件

- **`synonyms.json`**：定义 `synonyms`（同义词）和 `category_hints`（类别区分词），`product_matcher` 启动时自动加载
- **`providers.json`**：格式 `{"domain": [imap_host, imap_port, smtp_host, smtp_port]}`，合并覆盖内置邮件服务商配置

## Environment Variables

必填：
- `QIANWEN_API_KEY` 或 `ZHIPU_API_KEY`
- `IMAP_HOST`, `IMAP_USER`, `IMAP_PASS`
- `SECRET_KEY`（Session 加密密钥，首次启动自动生成并写入 `.env`；修改后 DB 中加密密码失效）

选填：
- `LLM_PROVIDER`（默认 `qianwen`），`LLM_MODEL`（默认 `qwen-plus`）
- `POLL_INTERVAL`（秒，默认 `60`）
- `INIT_SYNC_DAYS`（首次登录拉取历史邮件天数，默认 `90`）
- `SMTP_HOST/PORT/USER/PASS`（不填则复用 IMAP 凭据）
- `SERPER_API_KEY`（买家背调用 Google 搜索）
- `NOTIFY_EMAIL`（新草稿生成后发邮件通知）
- `SYSTEM_URL`（通知邮件中的系统访问链接）
- `COMPANY_NAME/DESC/WEBSITE/EMAIL/PHONE`（写入草稿签名，也可在 `/settings/company` 页面填写）
- `DB_PATH`（默认 `email_reply.db`），`PRODUCTS_CSV`（默认 `products.csv`）
