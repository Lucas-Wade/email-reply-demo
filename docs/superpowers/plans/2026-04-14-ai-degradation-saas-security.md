# AI 服务降级感知 + SaaS 安全修复 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当 LLM API 不可用时，系统自动检测、前端实时展示降级状态、用户可在 UI 中更换 API Key；同时修复公网 SaaS 部署必需的安全防护。

**Architecture:** 在 `ai_processor.py` 中新增模块级健康状态和探针函数，`main.py` 轮询前调用探针、通过 SSE 推送状态变化。新增 `/settings/ai` 页面管理 LLM 配置。安全修复涵盖 CSRF 中间件、Cookie 安全标志、依赖锁定、前端 fetch 错误处理。

**Tech Stack:** Python 3 / FastAPI / Starlette SessionMiddleware / Jinja2 / Bootstrap 5 / SSE (EventSource)

**Spec:** `docs/superpowers/specs/2026-04-14-ai-degradation-saas-security-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `ai_processor.py` | Modify | 新增 `_ai_health` 状态、`check_ai_health()`、`get_ai_health()`；各 LLM 调用点异常上报 |
| `main.py` | Modify | 轮询探针调用、CSRF 中间件、Cookie 安全、`/settings/ai` 路由组、`/api/counts` 增加 ai_status、SSE 新事件类型 |
| `templates/base.html` | Modify | 导航栏 AI 指示器、告警横幅、CSRF hidden field、SSE ai_status_changed 监听、fetch 修复 |
| `templates/settings.html` | Modify | 设置导航栏增加 "AI 配置" tab |
| `templates/settings_ai.html` | Create | AI 状态卡片 + Key 配置表单 + 说明 |
| `templates/index.html` | Modify | `pending_ai` 分类标记、fetch 错误处理 |
| `requirements.txt` | Modify | 版本锁定 |

---

### Task 1: AI 健康状态模块（ai_processor.py）

**Files:**
- Modify: `ai_processor.py:1-35`（顶部，`_get_client()` 之后）

- [ ] **Step 1: 在 `_get_client()` 之后添加健康状态字典和探针函数**

在 `ai_processor.py` 的 `_model()` 函数之后（约第 37 行后），插入：

```python
# ── AI 健康状态 ─────────────────────────────────────────────────────────────
from datetime import datetime as _dt

_ai_health = {
    "status":        "healthy",   # healthy / degraded / unavailable
    "last_ok":       None,        # ISO 时间戳
    "last_fail":     None,
    "fail_count":    0,
    "error_message": None,
}


def get_ai_health() -> dict:
    """返回当前 AI 健康状态的只读副本"""
    return dict(_ai_health)


def check_ai_health() -> tuple[str, str] | None:
    """
    轻量探针：发一个 max_tokens=1 的请求检测 LLM 可用性。
    返回 (old_status, new_status) 如果状态变化了，否则返回 None。
    """
    old = _ai_health["status"]
    try:
        client = _get_client()
        client.chat.completions.create(
            model=_model(),
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
            timeout=10,
        )
        _ai_health["last_ok"] = _dt.now().isoformat()
        _ai_health["fail_count"] = 0
        _ai_health["error_message"] = None
        _ai_health["status"] = "healthy"
    except Exception as e:
        _ai_health["last_fail"] = _dt.now().isoformat()
        _ai_health["fail_count"] += 1
        _ai_health["error_message"] = str(e)[:200]
        if _ai_health["fail_count"] >= 2:
            _ai_health["status"] = "unavailable"
        else:
            _ai_health["status"] = "degraded"

    new = _ai_health["status"]
    return (old, new) if old != new else None
```

- [ ] **Step 2: 验证语法**

Run: `cd "D:/Work space/email-reply-demo" && venv/Scripts/python -c "import ai_processor; print(ai_processor.get_ai_health())"`

Expected: `{'status': 'healthy', 'last_ok': None, 'last_fail': None, 'fail_count': 0, 'error_message': None}`

- [ ] **Step 3: Commit**

```bash
git add ai_processor.py
git commit -m "feat: add AI health probe and status tracking to ai_processor"
```

---

### Task 2: 轮询前探针调用 + SSE 推送（main.py）

**Files:**
- Modify: `main.py:73-75`（import 区）
- Modify: `main.py:132-140`（`process_new_emails` 函数）

- [ ] **Step 1: 在 main.py 的 import 区增加导入**

在 `main.py:73-75` 的 `from ai_processor import ...` 行中追加 `check_ai_health, get_ai_health`：

```python
from ai_processor import (classify_email, parse_inquiry, generate_draft,
                          background_check, generate_followup_draft, score_buyer_intent,
                          extract_products_from_url, describe_email_images,
                          check_ai_health, get_ai_health)
```

- [ ] **Step 2: 在 `process_new_emails()` 函数的 try 块开头调用探针**

在 `_process_new_emails_inner()` 函数（`main.py:143`）中，`logger.info("检查新邮件...")` 之后插入：

```python
    # AI 健康探针：检测 LLM 可用性，状态变化时通过 SSE 通知前端
    status_change = check_ai_health()
    if status_change:
        old, new = status_change
        logger.info(f"[AI 状态] {old} → {new}")
        health = get_ai_health()
        _push_event({"type": "ai_status_changed", "old": old, "new": new,
                      "error": health.get("error_message")})
```

- [ ] **Step 3: 在 `_handle_one_email` 中增加 unavailable 降级逻辑**

在 `main.py:216`（`classification = classify_email(...)` 行之前）插入检查：

```python
    # AI 不可用时跳过 LLM 分类，保存为 pending_ai 等待恢复后补跑
    if get_ai_health()["status"] == "unavailable" and rule != "trust":
        logger.info(f"[AI 离线] 跳过分类 uid={raw['uid']}，标记为 pending_ai")
        db.save_email(
            uid=raw["uid"], subject=raw["subject"], sender=raw["sender"],
            received_at=raw["received_at"], body_text=raw["body_text"],
            language="unknown", category="pending_ai",
            classify_layer=0, classify_score=None, classify_criteria=None,
            thread_email_id=thread_email_id, account_email=account_email,
        )
        return
```

- [ ] **Step 4: SSE 端点支持新事件类型**

在 `main.py:473`，SSE stream 中 `yield ServerSentEvent(data=payload, event="new_draft")` 这行需要改为从 payload 中读取事件类型：

```python
                    msg = json.loads(payload)
                    event_type = msg.get("type", "new_draft")
                    yield ServerSentEvent(data=payload, event=event_type)
```

- [ ] **Step 5: 验证服务能启动**

Run: `cd "D:/Work space/email-reply-demo" && venv/Scripts/python -c "from main import app; print('OK')"`

Expected: `OK`（可能有启动打印，不应有 ImportError/SyntaxError）

- [ ] **Step 6: Commit**

```bash
git add main.py
git commit -m "feat: call AI health probe before each poll cycle, degrade to pending_ai when unavailable"
```

---

### Task 3: `/api/counts` 增加 AI 状态 + `/health` 增强（main.py）

**Files:**
- Modify: `main.py:421-429`（`api_counts` 路由）
- Modify: `main.py:432-457`（`health` 路由）

- [ ] **Step 1: 在 `/api/counts` 返回中增加 `ai_status`**

修改 `main.py:421-429` 的 `api_counts` 函数，在返回的 JSON 中加入 AI 状态：

```python
@app.get("/api/counts")
async def api_counts(request: Request):
    """轻量角标接口：返回待审核草稿数 + 逾期跟进数 + AI 状态"""
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
```

- [ ] **Step 2: 在 `/health` 返回中增加 `ai` 字段**

修改 `main.py:432-457` 的 `health` 函数，增加 AI 健康信息：

```python
    return JSONResponse({
        "status":  "degraded" if has_error else "ok",
        "time":    _now(),
        "ai": get_ai_health(),
        # ... 其余字段不变 ...
    })
```

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: expose AI health status in /api/counts and /health endpoints"
```

---

### Task 4: 导航栏 AI 指示器 + 告警横幅 + SSE 监听（base.html）

**Files:**
- Modify: `templates/base.html:245-280`（导航栏区域）
- Modify: `templates/base.html:305-307`（content 容器前）
- Modify: `templates/base.html:340-362`（JS 区域）

- [ ] **Step 1: 在导航栏"检查邮件"按钮前插入 AI 状态指示器**

在 `templates/base.html:273`（`<!-- 检查邮件 -->` 注释前）插入：

```html
      <!-- AI 状态指示器 -->
      <span id="ai-indicator" class="d-flex align-items-center gap-1 me-1" title="AI 正常" style="cursor:pointer">
        <span id="ai-dot" class="rounded-circle d-inline-block" style="width:8px;height:8px;background:var(--status-sent)"></span>
        <span id="ai-label" class="small d-none d-md-inline text-white-50" style="display:none!important"></span>
      </span>
```

- [ ] **Step 2: 在 `<div class="container-lg pb-5">` 前插入告警横幅**

在 `templates/base.html:305`（`<div class="container-lg pb-5">` 行前）插入：

```html
<!-- AI 离线告警横幅 -->
<div id="ai-banner" class="alert alert-danger mb-0 rounded-0 d-none" role="alert">
  <div class="container-lg d-flex align-items-center justify-content-between flex-wrap gap-2">
    <div>
      <i class="bi bi-exclamation-triangle-fill me-2"></i>
      <strong>AI 服务不可用</strong> — 邮件分类和草稿生成已暂停，邮件仍在正常收取。
      <span id="ai-banner-detail" class="text-muted small ms-2"></span>
    </div>
    <a href="/settings/ai" class="btn btn-sm btn-outline-danger">前往配置 API Key</a>
  </div>
</div>
```

- [ ] **Step 3: 修改 JS 的 `refreshBadges` 函数以更新 AI 指示器**

在 `templates/base.html` 的 `refreshBadges()` 函数中，在现有 `.then(d => {` 回调内、角标更新逻辑之后追加：

```javascript
    // AI 状态指示器
    const dot = document.getElementById('ai-dot');
    const label = document.getElementById('ai-label');
    const indicator = document.getElementById('ai-indicator');
    const banner = document.getElementById('ai-banner');
    const bannerDetail = document.getElementById('ai-banner-detail');
    if (dot && d.ai_status) {
      if (d.ai_status === 'healthy') {
        dot.style.background = 'var(--status-sent)';
        label.style.display = 'none';
        indicator.title = 'AI 正常';
        indicator.onclick = null;
        indicator.style.cursor = 'default';
        if (banner) banner.classList.add('d-none');
      } else if (d.ai_status === 'degraded') {
        dot.style.background = 'var(--status-pending)';
        dot.classList.add('pulse');
        label.style.display = '';
        label.textContent = 'AI 波动';
        indicator.title = 'AI 服务响应异常';
        if (banner) banner.classList.add('d-none');
      } else {
        dot.style.background = 'var(--bs-danger)';
        label.style.display = '';
        label.textContent = 'AI 离线';
        indicator.title = '点击配置 API Key';
        indicator.style.cursor = 'pointer';
        indicator.onclick = () => { location.href = '/settings/ai'; };
        if (banner) {
          banner.classList.remove('d-none');
          let detail = '';
          if (d.ai_last_ok) detail += '上次正常：' + d.ai_last_ok.replace('T', ' ').slice(0, 16);
          if (d.ai_error) detail += (detail ? '  |  ' : '') + '错误：' + d.ai_error.slice(0, 80);
          if (bannerDetail) bannerDetail.textContent = detail;
        }
      }
    }
```

- [ ] **Step 4: 添加脉动动画 CSS**

在 `templates/base.html` 的 `<style>` 块中追加：

```css
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
    .pulse { animation: pulse 1.5s infinite; }
```

- [ ] **Step 5: 在 SSE 区域增加 `ai_status_changed` 监听**

这段逻辑放在 `base.html` 的 JS 区域底部（`refreshBadges` 和 `setInterval` 之后）：

```javascript
// SSE: AI 状态变化实时推送
(function() {
  const es = new EventSource('/events');
  es.addEventListener('ai_status_changed', (e) => {
    try {
      const d = JSON.parse(e.data);
      if (d.new === 'healthy') {
        showToast('AI 服务已恢复', 'success');
      } else if (d.new === 'unavailable') {
        showToast('AI 服务不可用：' + (d.error || '').slice(0, 60), 'danger');
      }
    } catch(_) {}
    refreshBadges();
  });
})();
```

注意：`index.html` 已有自己的 EventSource 连接处理 `new_draft` 事件，`base.html` 的这个只监听 AI 状态变化——两个连接独立运行。但这意味着每个页面打开两个 SSE 连接。更好的做法：把这个监听器加到 `base.html` 已有的 `refreshBadges` 附近，与 `index.html` 独立即可（非 index 页面只有 base 的连接）。

- [ ] **Step 6: 修复 `refreshBadges` 的空 `.catch`**

将 `}).catch(() => {});` 改为：

```javascript
  }).catch(() => {
    // 网络错误时隐藏角标，静默降级
    const bd = document.getElementById('badge-drafts');
    const bf = document.getElementById('badge-followups');
    if (bd) bd.classList.add('d-none');
    if (bf) bf.classList.add('d-none');
  });
```

- [ ] **Step 7: Commit**

```bash
git add templates/base.html
git commit -m "feat: add AI status indicator, alert banner, and SSE listener to base template"
```

---

### Task 5: 邮件列表 `pending_ai` 标记 + fetch 修复（index.html）

**Files:**
- Modify: `templates/index.html:203-210`（分类 badge 区域）
- Modify: `templates/index.html:438`（deal fetch）
- Modify: `templates/index.html:491-496`（mark-read fetch）

- [ ] **Step 1: 在分类 badge 中增加 `pending_ai`**

在 `templates/index.html:204-210` 的分类 badge 逻辑中，在 `{% else %}` 之前（`{% elif e.category == 'spam' %}` 之后）追加：

```html
            {% elif e.category == 'pending_ai' %}
              <span class="badge bg-warning-subtle text-warning border border-warning-subtle">
                <i class="bi bi-exclamation-triangle me-1"></i>待AI
              </span>
```

- [ ] **Step 2: 修复 deal fetch 缺少 .catch**

在 `templates/index.html:438` 的 `await fetch(...)` 调用后（`markDeal` 的 deal-confirm 事件监听器中），整个 async 函数用 try/catch 包裹，或在 fetch 后添加错误处理。将整个 `deal-confirm` 事件处理改为：

```javascript
document.getElementById('deal-confirm')?.addEventListener('click', async () => {
  try {
    const amount = parseFloat(document.getElementById('deal-amount').value) || 0;
    const form = new FormData();
    form.append('status', 'won');
    form.append('amount', amount);
    const resp = await fetch(`/email/${_dealEmailId}/deal`, { method: 'POST', body: form });
    if (!resp.ok) throw new Error(resp.statusText);
    bootstrap.Modal.getInstance(document.getElementById('dealModal'))?.hide();
    _dealBtn.outerHTML = '<span class="badge bg-danger align-self-center" title="已成单"><i class="bi bi-trophy-fill"></i></span>';
    showToast('已标记成单！');
  } catch(e) {
    showToast('操作失败，请重试', 'danger');
  }
});
```

- [ ] **Step 3: 修复 mark-read fetch 缺少 .catch**

在 `templates/index.html:492` 的 fetch 调用后添加 `.catch`：

```javascript
        fetch(`/email/${id}/read`, { method: 'POST' }).then(() => {
          statusCell.innerHTML = '<span class="s-ignored">已读</span>';
          statusCell.dataset.status = 'read';
        }).catch(() => {});
```

这个 catch 是合理的静默——标记已读失败不需要打扰用户。

- [ ] **Step 4: Commit**

```bash
git add templates/index.html
git commit -m "feat: add pending_ai badge in email list, fix unhandled fetch errors"
```

---

### Task 6: 设置导航增加 AI 配置 tab（settings.html + 关联页面）

**Files:**
- Modify: `templates/settings.html:6-19`（设置导航栏）

- [ ] **Step 1: 在设置导航栏添加 "AI 配置" 按钮**

在 `templates/settings.html:13`（公司信息按钮之后、修改密码之前）插入：

```html
  <a href="/settings/ai" class="btn btn-sm btn-outline-secondary">
    <i class="bi bi-cpu me-1"></i>AI 配置
  </a>
```

同时给所有设置子页面的导航也加上这个按钮（保持一致性）。检查 `settings.html` 中现有按钮是否需要条件高亮——当前只有第一个 `/settings` 有 `{% if %}` 判断，其他都是 `btn-outline-secondary`。保持一致即可。

- [ ] **Step 2: Commit**

```bash
git add templates/settings.html
git commit -m "feat: add AI config tab to settings navigation"
```

---

### Task 7: AI 配置页面（settings_ai.html + 路由）

**Files:**
- Create: `templates/settings_ai.html`
- Modify: `main.py`（新增 3 个路由 + `.env` 写入函数）

- [ ] **Step 1: 创建 `templates/settings_ai.html`**

```html
{% extends "base.html" %}
{% block title %}AI 配置 — 询盘回复系统{% endblock %}

{% block content %}
<!-- 设置导航 -->
<div class="d-flex gap-2 mb-4">
  <a href="/settings" class="btn btn-sm btn-outline-secondary">
    <i class="bi bi-mailbox me-1"></i>邮箱账号 & 规则
  </a>
  <a href="/settings/products" class="btn btn-sm btn-outline-secondary">
    <i class="bi bi-box-seam me-1"></i>产品库
  </a>
  <a href="/settings/company" class="btn btn-sm btn-outline-secondary">
    <i class="bi bi-building me-1"></i>公司信息
  </a>
  <a href="/settings/ai" class="btn btn-sm {% if request.url.path == '/settings/ai' %}btn-primary{% else %}btn-outline-secondary{% endif %}">
    <i class="bi bi-cpu me-1"></i>AI 配置
  </a>
  <a href="/settings/password" class="btn btn-sm btn-outline-secondary">
    <i class="bi bi-lock me-1"></i>修改密码
  </a>
</div>

<div class="row g-4">
  <!-- 左列：状态 + 配置 -->
  <div class="col-lg-7">
    <!-- AI 状态卡片 -->
    <div class="card mb-4">
      <div class="card-header bg-white fw-semibold">
        <i class="bi bi-activity me-2 text-secondary"></i>AI 服务状态
      </div>
      <div class="card-body">
        <div class="d-flex align-items-center gap-3 mb-3">
          {% if ai.status == 'healthy' %}
            <span class="rounded-circle d-inline-block" style="width:12px;height:12px;background:var(--status-sent)"></span>
            <span class="fw-semibold text-success">正常运行</span>
          {% elif ai.status == 'degraded' %}
            <span class="rounded-circle d-inline-block pulse" style="width:12px;height:12px;background:var(--status-pending)"></span>
            <span class="fw-semibold text-warning">响应波动</span>
          {% else %}
            <span class="rounded-circle d-inline-block" style="width:12px;height:12px;background:var(--bs-danger)"></span>
            <span class="fw-semibold text-danger">不可用</span>
          {% endif %}
        </div>
        <div class="small text-muted">
          {% if ai.last_ok %}
            <div><i class="bi bi-check-circle me-1 text-success"></i>上次成功：{{ ai.last_ok[:19] | replace('T', ' ') }}</div>
          {% endif %}
          {% if ai.fail_count > 0 %}
            <div><i class="bi bi-x-circle me-1 text-danger"></i>连续失败：{{ ai.fail_count }} 次</div>
          {% endif %}
          {% if ai.error_message %}
            <div class="mt-1"><i class="bi bi-bug me-1 text-warning"></i>错误信息：<code>{{ ai.error_message[:120] }}</code></div>
          {% endif %}
        </div>
        <form method="post" action="/settings/ai/test" class="mt-3">
          <button type="submit" class="btn btn-sm btn-outline-primary">
            <i class="bi bi-arrow-repeat me-1"></i>立即测试连接
          </button>
        </form>
        {% if test_result is defined and test_result is not none %}
          <div class="alert alert-{{ 'success' if test_result.ok else 'danger' }} mt-3 mb-0 small">
            {% if test_result.ok %}
              <i class="bi bi-check-circle-fill me-1"></i>连接成功！模型 {{ test_result.model }} 响应正常。
            {% else %}
              <i class="bi bi-x-circle-fill me-1"></i>连接失败：{{ test_result.error }}
            {% endif %}
          </div>
        {% endif %}
      </div>
    </div>

    <!-- LLM 配置表单 -->
    <div class="card">
      <div class="card-header bg-white fw-semibold">
        <i class="bi bi-key me-2 text-secondary"></i>LLM 提供商配置
      </div>
      <div class="card-body">
        {% if saved %}
          <div class="alert alert-success small py-2">
            <i class="bi bi-check-circle me-1"></i>已保存{% if save_test_ok %}，连接测试通过{% elif save_test_ok is not none %}，但连接测试失败：{{ save_test_error }}{% endif %}
          </div>
        {% endif %}
        <form method="post" action="/settings/ai/save">
          <div class="mb-3">
            <label class="form-label small fw-semibold text-muted">LLM 提供商</label>
            <select name="provider" class="form-select form-select-sm" id="provider-select">
              <option value="qianwen" {% if current_provider == 'qianwen' %}selected{% endif %}>通义千问（Qianwen）</option>
              <option value="zhipu" {% if current_provider == 'zhipu' %}selected{% endif %}>智谱 AI（Zhipu）</option>
            </select>
          </div>
          <div class="mb-3">
            <label class="form-label small fw-semibold text-muted">API Key</label>
            <input type="text" name="api_key" class="form-control form-control-sm font-monospace"
                   placeholder="输入新的 API Key（留空则不修改）"
                   autocomplete="off">
            {% if masked_key %}
              <div class="form-text">当前 Key：<code>{{ masked_key }}</code></div>
            {% else %}
              <div class="form-text text-warning">未配置 API Key</div>
            {% endif %}
          </div>
          <button type="submit" class="btn btn-primary btn-sm">
            <i class="bi bi-save me-1"></i>保存并测试
          </button>
        </form>
      </div>
    </div>
  </div>

  <!-- 右列：说明 -->
  <div class="col-lg-5">
    <div class="card">
      <div class="card-header bg-white fw-semibold">
        <i class="bi bi-info-circle me-2 text-secondary"></i>使用说明
      </div>
      <div class="card-body small text-muted">
        <h6 class="fw-semibold text-dark">通义千问（推荐）</h6>
        <ol class="ps-3 mb-3">
          <li>注册 <a href="https://dashscope.console.aliyun.com/" target="_blank">阿里云百炼平台</a></li>
          <li>进入「API-KEY 管理」创建 Key</li>
          <li>复制 Key 粘贴到左侧输入框</li>
        </ol>

        <h6 class="fw-semibold text-dark">智谱 AI</h6>
        <ol class="ps-3 mb-3">
          <li>注册 <a href="https://open.bigmodel.cn/" target="_blank">智谱开放平台</a></li>
          <li>在「API keys」中创建 Key</li>
          <li>选择提供商为"智谱 AI"并粘贴 Key</li>
        </ol>

        <h6 class="fw-semibold text-dark">常见问题</h6>
        <ul class="ps-3 mb-0">
          <li><strong>额度用尽</strong>：错误信息含 "quota" 或 "balance"，需去平台充值</li>
          <li><strong>Key 无效</strong>：错误信息含 "401" 或 "authentication"，请重新生成 Key</li>
          <li><strong>服务超时</strong>：可能是平台临时故障，稍后会自动恢复</li>
        </ul>
      </div>
    </div>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 2: 在 main.py 中新增 `.env` 写入辅助函数**

在 `main.py` 的 `_save_company_to_env` 函数之后（约第 1133 行）插入：

```python
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
```

- [ ] **Step 3: 在 main.py 中新增 3 个路由**

在 `/settings/company` 路由之后插入：

```python
# ── AI 配置路由 ──────────────────────────────────────────────────────────────

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
    test_ok, test_error = None, None
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
```

- [ ] **Step 4: 验证页面可渲染**

Run: `cd "D:/Work space/email-reply-demo" && venv/Scripts/python -c "from main import app; print('routes OK')"`

Expected: 无 ImportError

- [ ] **Step 5: Commit**

```bash
git add templates/settings_ai.html main.py
git commit -m "feat: add /settings/ai page for LLM provider config and connection testing"
```

---

### Task 8: CSRF 防护（main.py + base.html）

**Files:**
- Modify: `main.py:374-381`（auth_middleware 附近）
- Modify: `main.py:491-558`（login_submit，CSRF token 初始化）
- Modify: `templates/base.html`（meta tag + hidden field）

- [ ] **Step 1: 在 auth_middleware 之后新增 CSRF 中间件**

在 `main.py:374` 的 `auth_middleware` 函数之后插入：

```python
import secrets as _secrets

_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_CSRF_EXEMPT_PATHS = {"/health", "/events", "/login"}

@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    """CSRF 防护：POST/PUT/DELETE 请求必须携带有效 token"""
    if request.method in _CSRF_SAFE_METHODS:
        return await call_next(request)
    if _is_public(request.url.path) or request.url.path in _CSRF_EXEMPT_PATHS:
        return await call_next(request)

    # 从 session 取出 token
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
```

- [ ] **Step 2: 在登录成功后初始化 CSRF token**

在 `main.py` 的 `login_submit` 函数中，`request.session["user"] = session_user` 之后追加：

```python
    request.session["csrf_token"] = _secrets.token_hex(32)
```

- [ ] **Step 3: 通过 Jinja2 全局变量自动注入 CSRF token**

在 `main.py:368`（`templates = Jinja2Templates(...)` 行之后）追加：

```python
@app.middleware("http")
async def inject_csrf_to_template(request: Request, call_next):
    """将 CSRF token 注入 Jinja2 全局变量，模板中可用 {{ csrf_token }}"""
    templates.env.globals["csrf_token"] = request.session.get("csrf_token", "")
    return await call_next(request)
```

- [ ] **Step 4: 在 base.html 所有 form 标签后自动插入 CSRF hidden field**

在 `templates/base.html` 的 `</head>` 前添加 meta：

```html
<meta name="csrf-token" content="{{ csrf_token }}">
```

然后在底部 JS 区域添加自动注入逻辑：

```javascript
// CSRF: 给所有 POST 表单自动注入 hidden _csrf 字段
document.querySelectorAll('form[method="post"], form[action]').forEach(form => {
  if (form.method.toLowerCase() !== 'get' && !form.querySelector('input[name="_csrf"]')) {
    const input = document.createElement('input');
    input.type = 'hidden'; input.name = '_csrf';
    input.value = document.querySelector('meta[name="csrf-token"]')?.content || '';
    form.appendChild(input);
  }
});
```

同时给 JS 中的 fetch POST 请求添加 CSRF header。在 `showToast` 函数之后追加：

```javascript
// CSRF: 包装 fetch 自动附加 CSRF header
const _origFetch = window.fetch;
window.fetch = function(url, opts = {}) {
  if (opts.method && opts.method.toUpperCase() !== 'GET') {
    opts.headers = opts.headers || {};
    if (opts.headers instanceof Headers) {
      if (!opts.headers.has('X-CSRF-Token'))
        opts.headers.set('X-CSRF-Token', document.querySelector('meta[name="csrf-token"]')?.content || '');
    } else {
      opts.headers['X-CSRF-Token'] = opts.headers['X-CSRF-Token'] || document.querySelector('meta[name="csrf-token"]')?.content || '';
    }
  }
  return _origFetch.call(this, url, opts);
};
```

- [ ] **Step 5: 验证 CSRF 不会阻塞正常操作**

启动服务后访问登录页、登录、执行一次 POST 操作（如标记已读），确认不被 403 拦截。

- [ ] **Step 6: Commit**

```bash
git add main.py templates/base.html
git commit -m "feat: add CSRF protection middleware with auto-inject for forms and fetch"
```

---

### Task 9: Session Cookie 安全标志（main.py）

**Files:**
- Modify: `main.py:414-416`（SessionMiddleware 配置）

- [ ] **Step 1: 修改 SessionMiddleware 参数**

将 `main.py:416` 的：

```python
app.add_middleware(SessionMiddleware, secret_key=secret_key, max_age=86400 * 7)
```

改为：

```python
_dev_mode = os.getenv("DEV_MODE", "").strip() in ("1", "true", "yes")
app.add_middleware(
    SessionMiddleware,
    secret_key=secret_key,
    max_age=86400,                       # 7天 → 1天
    https_only=not _dev_mode,            # 生产环境强制 HTTPS
    same_site="strict",                  # 严格同站策略
)
```

- [ ] **Step 2: Commit**

```bash
git add main.py
git commit -m "fix: harden session cookie — reduce max_age, add https_only and same_site=strict"
```

---

### Task 10: 依赖版本锁定（requirements.txt）

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: 替换 requirements.txt 为精确版本**

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

- [ ] **Step 2: 验证 pip 能解析**

Run: `cd "D:/Work space/email-reply-demo" && venv/Scripts/pip install --dry-run -r requirements.txt`

Expected: 所有包都 already satisfied

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "fix: pin all dependency versions for reproducible builds"
```

---

### Task 11: 端到端验证

- [ ] **Step 1: 启动服务**

Run: `cd "D:/Work space/email-reply-demo" && venv/Scripts/python -m uvicorn main:app --port 8000`

- [ ] **Step 2: 验证以下场景**

1. 登录后，导航栏右侧有绿色 AI 状态圆点
2. 访问 `/settings/ai`，看到 AI 状态卡片 + Key 配置表单
3. 点击"立即测试连接" → 显示测试结果
4. 把 API Key 改为无效值 → 保存 → 看到失败提示
5. 等待一个轮询周期 → AI 状态变为红色，顶部出现告警横幅
6. 换回正确的 Key → 保存 → 状态恢复绿色，横幅消失
7. 所有 POST 表单提交正常（CSRF 不阻塞）
8. `/health` 接口包含 `ai` 字段

- [ ] **Step 3: 最终 Commit**

如果有遗漏修复，统一提交。
