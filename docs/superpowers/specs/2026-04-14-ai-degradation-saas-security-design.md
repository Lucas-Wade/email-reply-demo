# AI 服务降级感知 + 前端 API Key 管理 + 公网 SaaS 安全修复

**日期**: 2026-04-14
**状态**: 已批准

---

## 1. 背景

当前系统的 AI 功能（通义千问 / 智谱）在 API Key 到期、额度耗尽、服务宕机时，邮件被静默分类为 `other`，用户毫无感知。此外，系统缺少面向公网 SaaS 部署的基本安全防护（CSRF、Cookie 安全、依赖锁定）。

本次迭代解决两个问题：
1. AI 不可用时的降级感知 + 前端 API Key 管理
2. 公网 SaaS 安全硬伤修复

---

## 2. AI 健康检测机制

### 2.1 全局状态

`ai_processor.py` 新增模块级字典：

```python
_ai_health = {
    "status":        "healthy",   # healthy / degraded / unavailable
    "last_ok":       None,        # ISO 时间戳
    "last_fail":     None,
    "fail_count":    0,           # 连续失败次数
    "error_message": None,        # 最近错误摘要
}
```

### 2.2 探针函数 `check_ai_health()`

- 调用时机：`process_new_emails()` 每轮开头
- 请求：`messages=[{"role":"user","content":"hi"}]`，`max_tokens=1`，`timeout=10`
- 成功 → `status="healthy"`，`fail_count=0`
- 失败 → `fail_count += 1`
  - `fail_count == 1` → `status="degraded"`
  - `fail_count >= 2` → `status="unavailable"`
- 状态变化时返回旧状态 → 新状态，供调用方触发 SSE 推送

### 2.3 降级行为

`_handle_one_email` 中：
- Layer 1 规则照常执行（`is_definite_spam`、`has_inquiry_signal`）
- 当 `_ai_health["status"] == "unavailable"` 时，跳过 Layer 2 LLM 调用
- 邮件保存为 `category="pending_ai"`，`status="new"`
- AI 恢复后，`pending_ai` 邮件可通过现有"重新处理"按钮补跑

### 2.4 对外接口

- `get_ai_health() -> dict`：供 `main.py` 路由读取
- `check_ai_health() -> tuple[str, str] | None`：返回 `(old, new)` 或 `None`（未变化）
- `/health` 端点增加 `ai` 字段

---

## 3. 前端展示

### 3.1 导航栏 AI 状态指示器

位置：`base.html` 导航栏，"检查邮件"按钮左侧。

| 状态 | 表现 |
|------|------|
| `healthy` | 绿色小圆点，hover 提示"AI 正常" |
| `degraded` | 黄色脉动圆点 + 文字"AI 波动" |
| `unavailable` | 红色圆点 + 文字"AI 离线"，点击跳转 `/settings/ai` |

数据来源：复用 `/api/counts` 接口，增加返回 `ai_status` 字段。

### 3.2 顶部告警横幅

仅 `unavailable` 时显示，位于导航栏下方 `<div class="container-lg">` 之前：

```
⚠ AI 服务不可用 — 邮件分类和草稿生成已暂停，邮件仍在正常收取。
  上次正常：2026-04-14 15:30  |  错误：Insufficient quota
  [前往配置 API Key →]
```

### 3.3 邮件列表标记

`index.html` 中，`pending_ai` 状态的邮件显示黄色三角图标 + "待AI处理"标签。

### 3.4 SSE 实时推送

复用 `/events`，新增事件类型 `ai_status_changed`：
```json
{"type": "ai_status_changed", "old": "healthy", "new": "unavailable", "error": "..."}
```

前端收到后：更新导航栏指示器 + 弹 toast + 刷新/显示横幅。

恢复时：toast "AI 服务已恢复" + 隐藏横幅。

---

## 4. 设置页 API Key 管理

### 4.1 导航

`settings.html` 设置导航栏新增按钮：

```
[邮箱账号 & 规则]  [产品库]  [公司信息]  [AI 配置]  [修改密码]
```

### 4.2 新页面 `/settings/ai`（`settings_ai.html`）

**① AI 状态卡片**
- 当前状态（绿/黄/红指示器 + 文字描述）
- 上次成功时间、连续失败次数、错误信息
- "立即测试"按钮 → POST `/settings/ai/test`

**② LLM 提供商配置表单**

| 字段 | 类型 | 说明 |
|------|------|------|
| 提供商 | 下拉选择 | `qianwen` / `zhipu` |
| API Key | 文本输入 | 显示遮蔽值（前3后3 + `****`），可编辑替换 |

- POST `/settings/ai/save`
- 保存逻辑：写入 `.env` 文件 + 热更新 `os.environ`
- 保存后自动执行探针测试，结果通过 toast 反馈
- API Key 回显时只展示遮蔽值，完整值永远不回传到浏览器

**③ 使用说明卡片**
- 通义千问 API Key 获取指引
- 智谱 API Key 获取指引
- 额度用尽的常见表现和解决方法

### 4.3 路由

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/settings/ai` | 渲染配置页 |
| POST | `/settings/ai/save` | 保存提供商 + Key 到 `.env` |
| POST | `/settings/ai/test` | 执行探针测试，返回结果 |

### 4.4 `.env` 写入

复用 `_ensure_secret_key()` 中的 `.env` 读写模式：
- 读取整个 `.env` 文件
- 用正则替换对应行（`LLM_PROVIDER=...`、`QIANWEN_API_KEY=...` 或 `ZHIPU_API_KEY=...`）
- 行不存在则追加
- 同时更新 `os.environ` 使热生效，无需重启

---

## 5. 公网 SaaS 安全修复

### 5.1 CSRF 防护

- 生成：登录时在 session 写入 `csrf_token = secrets.token_hex(32)`
- 注入：`base.html` 的 `<head>` 输出 `<meta name="csrf-token" content="...">`
- 所有 `<form>` 注入隐藏字段 `<input type="hidden" name="_csrf" value="...">`
- 通过 Jinja2 全局变量实现，模板中无需逐个手写
- 校验：新增中间件，POST/PUT/DELETE 请求校验 `_csrf` 表单字段或 `X-CSRF-Token` 请求头
- 不匹配返回 403
- 豁免：`/health`、`/events`（SSE）

### 5.2 Session Cookie 安全标志

```python
app.add_middleware(SessionMiddleware,
    secret_key=secret_key,
    max_age=86400,            # 7天 → 1天
    https_only=True,          # HTTPS only
    same_site="strict",       # 严格同站
)
```

- 环境变量 `DEV_MODE=1` 时关闭 `https_only`（开发环境 localhost）

### 5.3 依赖版本锁定

`requirements.txt` 锁定为当前精确版本：

```
openai==2.30.0
python-dotenv==1.2.2
fastapi==0.135.3
uvicorn[standard]==0.44.0
jinja2==3.1.6
apscheduler==3.11.2
imap-tools==1.11.1
cryptography==46.0.7
```

### 5.4 JS fetch 错误处理

- `base.html` 的 `refreshBadges()` — `.catch` 改为静默降级（隐藏角标，不报错）
- `index.html` 所有缺少 `.catch` 的 fetch 调用 — 补上 `.catch` + `showToast('操作失败', 'danger')`

---

## 6. 改动范围

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `ai_processor.py` | 修改 | 新增 `_ai_health`、`check_ai_health()`、`get_ai_health()`，各 LLM 调用点降级包装 |
| `main.py` | 修改 | 轮询前调探针、CSRF 中间件、Cookie 标志、`/settings/ai` 路由组、`/api/counts` 增加 ai_status、SSE 新事件 |
| `templates/base.html` | 修改 | 导航栏 AI 指示器、告警横幅、CSRF meta + hidden field、SSE 监听、fetch 修复 |
| `templates/settings.html` | 修改 | 导航栏加 "AI 配置" tab |
| `templates/settings_ai.html` | 新增 | AI 状态 + Key 配置 + 说明 |
| `templates/index.html` | 修改 | `pending_ai` 标记、fetch 错误处理 |
| `requirements.txt` | 修改 | 版本锁定 |

**不改数据库 schema，不新增数据库表。**

---

## 7. 不做的事

- 不加离线队列 / 补跑调度器（现有 UNSEEN 重试 + 手动"重新处理"已覆盖）
- 不加模型选择字段（固定用环境变量默认值）
- 不加多 LLM 提供商 failover（超出当前范围）
- 不加 API 调用量统计 / 费用监控
