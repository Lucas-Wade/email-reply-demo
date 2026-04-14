# Frontend Redesign: The Architect's Ledger — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the "Architect's Ledger" design system to `base.html`, `login.html`, `index.html`, and `draft.html` without touching any backend logic, routes, or JS behavior.

**Architecture:** Method A — all design tokens and component CSS are centralized in `base.html`'s `<style>` block. Page-specific templates only receive targeted HTML structural edits (stat cards, badge classes, verification card). No new files are created; no `main.py` changes.

**Tech Stack:** Bootstrap 5.3.3, Bootstrap Icons 1.11.3, Google Fonts (Manrope + Inter), Jinja2, vanilla JS (untouched).

**Spec:** `docs/superpowers/specs/2026-04-14-frontend-redesign-design.md`

**Start server to verify:** `cd "D:/Work space/email-reply-demo" && venv/Scripts/python -m uvicorn main:app --port 8000`

---

## File Map

| File | Action | What changes |
|---|---|---|
| `templates/base.html` | Modify | Add fonts, replace `:root`, rewrite all CSS in `<style>` block |
| `templates/login.html` | Modify | Body bg, card shadow, header font, input focus, button |
| `templates/index.html` | Modify | Stat card HTML structure (remove icon squares), badge classes |
| `templates/draft.html` | Modify | Verification card borders, action button styles |

---

## Task 1: base.html — Google Fonts import

**Files:**
- Modify: `templates/base.html:7-8`

- [ ] **Step 1: Add Google Fonts `<link>` after Bootstrap Icons link**

In `templates/base.html`, find:
```html
  <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
```
Replace with:
```html
  <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
  <link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
```

- [ ] **Step 2: Start server and confirm fonts load**

Run: `cd "D:/Work space/email-reply-demo" && venv/Scripts/python -m uvicorn main:app --port 8000`

Open http://localhost:8000 in browser → open DevTools → Network tab → filter "fonts.gstatic.com". Two font files (Manrope, Inter) should appear as 200 responses.

- [ ] **Step 3: Stop server (Ctrl+C) and commit**

```bash
cd "D:/Work space/email-reply-demo"
git add templates/base.html
git commit -m "style: add Manrope + Inter Google Fonts to base.html"
```

---

## Task 2: base.html — Replace design tokens and body

**Files:**
- Modify: `templates/base.html` (`:root` block, `body` rule)

- [ ] **Step 1: Replace `:root` block and `body` rule**

Find:
```css
    :root {
      --brand: #1a3a5c;
      --brand-light: #2a5080;
    }
    body { background: #f0f2f5; font-size: .93rem; }
```
Replace with:
```css
    /* ═══════════════════════════════════════
       Design System: The Architect's Ledger
       Source: templates/DESIGN.md
    ═══════════════════════════════════════ */
    :root {
      /* Surface hierarchy */
      --surface:        #f8f9fd;
      --surface-panel:  #eceef1;
      --surface-card:   #ffffff;
      /* Text */
      --on-surface:     #191c1f;
      --on-surface-var: #43474e;
      /* Brand (navbar anchor — do not change) */
      --brand:          #1a3a5c;
      --brand-light:    #2a5080;
      /* Actions */
      --primary:        #002444;
      --secondary:      #0055c9;
      /* Badges */
      --badge-bg:       #dae2ff;
      --badge-text:     #00419e;
      /* Semantic */
      --error:          #ba1a1a;
      --urgent:         #4f3300;
      /* Elevation */
      --shadow-ambient: 0px 12px 32px rgba(25, 28, 31, 0.06);
    }
    body {
      background: var(--surface);
      font-family: 'Inter', sans-serif;
      font-size: .93rem;
      color: var(--on-surface);
    }
```

- [ ] **Step 2: Verify token application**

Start server → open http://localhost:8000 → page background should shift from `#f0f2f5` (slightly warm gray) to `#f8f9fd` (cool blue-tinted white). Text should render in Inter. No layout breakage.

- [ ] **Step 3: Stop server and commit**

```bash
git add templates/base.html
git commit -m "style: replace CSS design tokens and body font (Architect's Ledger)"
```

---

## Task 3: base.html — Navbar, cards, stat-card

**Files:**
- Modify: `templates/base.html` (`<style>` block — Navbar, Cards, Stat cards sections)

- [ ] **Step 1: Replace Navbar and card styles**

Find:
```css
    /* Navbar */
    .navbar { background: var(--brand) !important; box-shadow: 0 2px 8px rgba(0,0,0,.25); }
    .navbar-brand { color: #fff !important; font-weight: 700; letter-spacing: .02em; }
    /* 导航角标 */
    .nav-badge {
      position: absolute; top: -4px; right: -6px;
      font-size: .6rem; padding: 2px 5px; border-radius: 8px;
      line-height: 1; pointer-events: none;
    }

    /* Cards */
    .card { border: none; box-shadow: 0 1px 4px rgba(0,0,0,.08); border-radius: 10px; }
    .card-header { border-bottom: 1px solid rgba(0,0,0,.06); padding: .75rem 1.1rem; }

    /* Stat cards */
    .stat-card { border-radius: 12px; transition: transform .15s; }
    .stat-card:hover { transform: translateY(-2px); }
    .stat-num { font-size: 1.9rem; font-weight: 700; line-height: 1; }
```
Replace with:
```css
    /* ── Navbar ── */
    .navbar { background: var(--brand) !important; box-shadow: 0 2px 12px rgba(0,0,0,.18); }
    .navbar-brand {
      color: #fff !important;
      font-family: 'Manrope', sans-serif;
      font-weight: 700;
      letter-spacing: .02em;
    }
    .nav-badge {
      position: absolute; top: -4px; right: -6px;
      font-size: .6rem; padding: 2px 5px; border-radius: 8px;
      line-height: 1; pointer-events: none;
    }

    /* ── Cards — no border, tonal layering ── */
    .card {
      border: none;
      background: var(--surface-card);
      box-shadow: var(--shadow-ambient);
      border-radius: 0.75rem;
    }
    .card-header {
      background: transparent;
      border-bottom: none;
      padding: 1rem 1.25rem 0.5rem;
    }

    /* ── Stat cards ── */
    .stat-card { border-radius: 0.75rem; transition: transform .15s; }
    .stat-card:hover { transform: translateY(-2px); }
    .stat-num {
      font-family: 'Manrope', sans-serif;
      font-size: 2rem;
      font-weight: 800;
      line-height: 1;
      color: var(--on-surface);
    }
```

- [ ] **Step 2: Verify card appearance**

Start server → open http://localhost:8000 → cards should have a softer, deeper ambient shadow (not the thin 1px-feeling shadow). Card headers (e.g., "邮件列表") should have no visible bottom border — whitespace separates header from body. Navbar brand text should render in Manrope.

- [ ] **Step 3: Stop server and commit**

```bash
git add templates/base.html
git commit -m "style: update navbar, card, stat-card styles — ambient shadow, no-border rule"
```

---

## Task 4: base.html — Table, badges, status text

**Files:**
- Modify: `templates/base.html` (`<style>` block — Badges, Status text, Table sections)

- [ ] **Step 1: Replace badge, status, and table styles**

Find:
```css
    /* Badges */
    .badge-inquiry { background: #0d6efd; }
    .badge-spam    { background: #6c757d; }
    .badge-other   { background: #adb5bd; color: #333 !important; }

    /* Status text */
    .s-pending { color: #e67e00; font-weight: 600; }
    .s-sent    { color: #198754; font-weight: 600; }
    .s-rejected{ color: #adb5bd; }
    .s-ignored { color: #adb5bd; }
    .s-new     { color: #0d6efd; }

    /* Table */
    .table { font-size: .88rem; }
    .table > :not(caption) > * > * { padding: .6rem .9rem; }
    .table tbody tr:hover { background: #f5f8ff; cursor: pointer; }
    .row-expanded { background: #f8f9fb !important; }
```
Replace with:
```css
    /* ── Badges — unified tonal style ── */
    .badge { border-radius: 0.375rem; font-weight: 600; }
    .badge-inquiry,
    .badge-spam,
    .badge-other {
      background: var(--badge-bg) !important;
      color: var(--badge-text) !important;
    }

    /* ── Status text ── */
    .s-pending { color: #b35c00; font-weight: 600; }
    .s-sent    { color: #1a6b3a; font-weight: 600; }
    .s-rejected{ color: var(--on-surface-var); }
    .s-ignored { color: var(--on-surface-var); }
    .s-new     { color: var(--secondary); }

    /* ── Table — ghost borders, tonal header ── */
    .table { font-size: .88rem; color: var(--on-surface); }
    .table > :not(caption) > * > * { padding: .65rem 1rem; }
    .table thead tr { background: var(--surface-panel); }
    .table thead th {
      font-family: 'Inter', sans-serif;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--on-surface-var);
      font-weight: 600;
      border: none;
    }
    .table tbody tr { border-bottom: 1px solid rgba(195, 198, 207, 0.15); }
    .table tbody tr:hover { background: var(--surface-card); cursor: pointer; }
    .row-expanded { background: #f0f4ff !important; }
```

- [ ] **Step 2: Verify table and badges**

Start server → open http://localhost:8000 → the email table should show:
- Table header row with `--surface-panel` light gray background and ALL CAPS column names
- Badges (询盘/垃圾/其他) should now all be `#dae2ff` background with `#00419e` text, rectangular corners
- Row hover should be white (`--surface-card`) not blue-tinted gray

- [ ] **Step 3: Stop server and commit**

```bash
git add templates/base.html
git commit -m "style: unified badge style, table headers all-caps, ghost row borders"
```

---

## Task 5: base.html — Form controls, buttons, draft styles, misc

**Files:**
- Modify: `templates/base.html` (`<style>` block — Preview pane, Draft page, Match score, Toast, Copy btn sections + add form/button overrides)

- [ ] **Step 1: Replace preview pane, draft page, score pill, toast, copy btn**

Find:
```css
    /* Preview pane */
    .email-preview {
      background: #f8f9fb;
      border-left: 3px solid #dee2e6;
      padding: .7rem 1rem;
      font-size: .83rem;
      color: #555;
      white-space: pre-wrap;
      max-height: 300px;
      overflow-y: auto;
    }

    /* Draft page */
    .draft-body-area {
      font-size: .87rem;
      line-height: 1.65;
      font-family: inherit;
      resize: vertical;
    }
    .original-mail {
      white-space: pre-wrap;
      font-size: .83rem;
      color: #444;
      max-height: 360px;
      overflow-y: auto;
      background: #f8f9fb;
      border-radius: 6px;
      padding: .9rem 1rem;
      border: 1px solid #e9ecef;
    }

    /* Match score pill */
    .score-high { background: #d1f0e0; color: #0f5132; }
    .score-mid  { background: #fff3cd; color: #664d03; }
    .score-low  { background: #f8d7da; color: #842029; }

    /* Toast */
    .toast-container { position: fixed; bottom: 1.5rem; right: 1.5rem; z-index: 9999; }

    /* Copy btn */
    .btn-copy { transition: all .2s; }
    .btn-copy.copied { background: #198754; color: #fff; border-color: #198754; }
```
Replace with:
```css
    /* ── Preview pane ── */
    .email-preview {
      background: var(--surface-panel);
      border-left: 3px solid var(--secondary);
      border-radius: 0 0.375rem 0.375rem 0;
      padding: .8rem 1rem;
      font-size: .83rem;
      color: var(--on-surface-var);
      white-space: pre-wrap;
      max-height: 300px;
      overflow-y: auto;
    }

    /* ── Draft page ── */
    .draft-body-area {
      font-size: .87rem;
      line-height: 1.65;
      font-family: 'Inter', sans-serif;
      resize: vertical;
      background: var(--surface-panel);
      border: none;
      border-bottom: 2px solid transparent;
      border-radius: 0.375rem 0.375rem 0 0;
      transition: border-color .15s;
    }
    .draft-body-area:focus {
      outline: none;
      border-bottom-color: var(--secondary);
      box-shadow: none;
    }
    .original-mail {
      white-space: pre-wrap;
      font-size: .83rem;
      color: var(--on-surface-var);
      max-height: 360px;
      overflow-y: auto;
      background: var(--surface-panel);
      border-radius: 0.5rem;
      padding: .9rem 1rem;
      border: none;
    }

    /* ── Match score pill ── */
    .score-high { background: #d1f0e0; color: #0f5132; }
    .score-mid  { background: #fff3cd; color: #664d03; }
    .score-low  { background: #fde8e8; color: #ba1a1a; }

    /* ── Toast ── */
    .toast-container { position: fixed; bottom: 1.5rem; right: 1.5rem; z-index: 9999; }

    /* ── Copy btn ── */
    .btn-copy { transition: all .2s; }
    .btn-copy.copied { background: #1a6b3a; color: #fff; border-color: #1a6b3a; }

    /* ══════════════════════════════════════════
       Button overrides (Bootstrap reset)
    ══════════════════════════════════════════ */
    .btn-primary {
      background: linear-gradient(135deg, #1a3a5c, #002444);
      border-color: #002444;
      border-radius: 0.375rem;
      font-weight: 600;
      transition: background 200ms ease-in-out, border-color 200ms ease-in-out;
    }
    .btn-primary:hover,
    .btn-primary:focus,
    .btn-primary:active {
      background: var(--secondary) !important;
      border-color: var(--secondary) !important;
    }
    .btn-danger,
    .btn-outline-danger:hover {
      background: var(--error);
      border-color: var(--error);
      border-radius: 0.375rem;
      font-weight: 600;
    }
    .btn-outline-danger {
      color: var(--error);
      border-color: var(--error);
      border-radius: 0.375rem;
    }
    .btn-success { border-radius: 0.375rem; font-weight: 600; }
    .btn-outline-secondary { border-radius: 0.375rem; }
    .btn-outline-warning   { border-radius: 0.375rem; }
    .btn-outline-info      { border-radius: 0.375rem; }
    .btn-outline-light     { border-radius: 0.375rem; }

    /* ══════════════════════════════════════════
       Form control overrides (bottom-border focus)
    ══════════════════════════════════════════ */
    .form-control,
    .form-select {
      background: var(--surface-panel);
      border: none;
      border-bottom: 2px solid transparent;
      border-radius: 0.375rem 0.375rem 0 0;
      color: var(--on-surface);
      transition: border-color .15s;
    }
    .form-control:focus,
    .form-select:focus {
      background: var(--surface-panel);
      border-bottom-color: var(--secondary);
      box-shadow: none;
      color: var(--on-surface);
    }
    .form-control::placeholder { color: var(--on-surface-var); opacity: .7; }
    /* input-group icon wrapper */
    .input-group-text {
      background: var(--surface-panel);
      border: none;
      border-bottom: 2px solid transparent;
      border-radius: 0.375rem 0 0 0;
    }
```

- [ ] **Step 2: Verify form controls and buttons**

Start server → open http://localhost:8000:
- "搜索" button should show dark navy gradient (not solid Bootstrap blue)
- Search input fields should have no visible border, `--surface-panel` background; clicking into one should show only a blue bottom underline, no box-shadow glow
- "检查邮件" button in navbar (btn-light) should be unchanged

Open http://localhost:8000/draft/1 (if any draft exists):
- Textarea should have `--surface-panel` background; clicking should show bottom-border only
- "批准并发送" (btn-success) and "拒绝" (btn-outline-danger) should have `0.375rem` border-radius

- [ ] **Step 3: Stop server and commit**

```bash
git add templates/base.html
git commit -m "style: form bottom-border focus, button gradient, draft textarea, preview pane"
```

---

## Task 6: login.html — Full page update

**Files:**
- Modify: `templates/login.html`

- [ ] **Step 1: Update login page `<style>` block**

Find the entire `<style>` block in `login.html`:
```html
  <style>
    :root { --brand: #1a3a5c; }
    body {
      background: #f0f2f5;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .login-card {
      width: 100%;
      max-width: 420px;
      border: none;
      box-shadow: 0 4px 24px rgba(0,0,0,.10);
      border-radius: 14px;
      overflow: hidden;
    }
    .login-header {
      background: var(--brand);
      padding: 2rem 2rem 1.6rem;
      text-align: center;
      color: #fff;
    }
    .login-header .brand-icon { font-size: 2.2rem; opacity: .9; }
    .login-header h5 { font-weight: 700; letter-spacing: .02em; margin-top: .5rem; margin-bottom: .2rem; }
    .login-header p { opacity: .6; font-size: .82rem; margin: 0; }
    .login-body { padding: 2rem; }
    .form-control:focus { border-color: var(--brand); box-shadow: 0 0 0 .2rem rgba(26,58,92,.15); }
    .btn-login { background: var(--brand); border-color: var(--brand); font-weight: 600; }
    .btn-login:hover { background: #2a5080; border-color: #2a5080; }
    .login-footer { font-size: .78rem; text-align: center; color: #adb5bd; padding-bottom: 1.5rem; }
    .provider-badge { font-size: .75rem; color: #0d6efd; }
  </style>
```
Replace with:
```html
  <link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --surface:        #f8f9fd;
      --surface-panel:  #eceef1;
      --surface-card:   #ffffff;
      --on-surface:     #191c1f;
      --on-surface-var: #43474e;
      --brand:          #1a3a5c;
      --secondary:      #0055c9;
      --shadow-ambient: 0px 12px 32px rgba(25, 28, 31, 0.06);
    }
    body {
      background: var(--surface);
      font-family: 'Inter', sans-serif;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--on-surface);
    }
    .login-card {
      width: 100%;
      max-width: 420px;
      border: none;
      background: var(--surface-card);
      box-shadow: var(--shadow-ambient);
      border-radius: 0.75rem;
      overflow: hidden;
    }
    .login-header {
      background: var(--brand);
      padding: 2rem 2rem 1.6rem;
      text-align: center;
      color: #fff;
    }
    .login-header .brand-icon { font-size: 2.2rem; opacity: .9; }
    .login-header h5 {
      font-family: 'Manrope', sans-serif;
      font-weight: 700;
      letter-spacing: .02em;
      margin-top: .5rem;
      margin-bottom: .2rem;
    }
    .login-header p { opacity: .6; font-size: .82rem; margin: 0; }
    .login-body { padding: 2rem; background: var(--surface-card); }

    /* Form controls — bottom-border focus only */
    .form-control,
    .form-select {
      background: var(--surface-panel);
      border: none;
      border-bottom: 2px solid transparent;
      border-radius: 0.375rem 0.375rem 0 0;
      color: var(--on-surface);
      transition: border-color .15s;
    }
    .form-control:focus,
    .form-select:focus {
      background: var(--surface-panel);
      border-bottom-color: var(--secondary);
      box-shadow: none;
      color: var(--on-surface);
    }
    .input-group-text {
      background: var(--surface-panel);
      border: none;
      border-bottom: 2px solid transparent;
      border-radius: 0.375rem 0 0 0;
    }
    .form-control::placeholder { color: var(--on-surface-var); opacity: .7; }

    /* Login button — gradient primary */
    .btn-login {
      background: linear-gradient(135deg, #1a3a5c, #002444);
      border: none;
      border-radius: 0.375rem;
      font-family: 'Inter', sans-serif;
      font-weight: 600;
      color: #fff;
      transition: background 200ms ease-in-out;
    }
    .btn-login:hover,
    .btn-login:focus { background: var(--secondary); color: #fff; }

    /* Toggle password button (keep neutral) */
    .btn-outline-secondary {
      border-radius: 0 0.375rem 0 0;
      border: none;
      border-bottom: 2px solid transparent;
      background: var(--surface-panel);
      color: var(--on-surface-var);
    }
    .btn-outline-secondary:hover { background: var(--surface-panel); color: var(--on-surface); }

    .login-footer { font-size: .78rem; text-align: center; color: #adb5bd; padding-bottom: 1.5rem; background: var(--surface-card); }
    .provider-badge { font-size: .75rem; color: var(--secondary); }

    /* Collapse section */
    .collapse .border { background: var(--surface-panel); border: none !important; border-radius: 0.375rem; }
  </style>
```

- [ ] **Step 2: Verify login page**

Open http://localhost:8000/logout then http://localhost:8000/login:
- Page background should be `#f8f9fd` (cool white)
- Login card has ambient shadow, no visible hard border
- "询盘回复系统" title renders in Manrope Bold
- Email/password inputs: `--surface-panel` background, no border; clicking shows only blue underline
- "登录" button shows dark navy gradient; hovering turns it `#0055c9`

- [ ] **Step 3: Stop server and commit**

```bash
git add templates/login.html
git commit -m "style: login.html — ambient shadow card, Manrope headline, bottom-border inputs, gradient button"
```

---

## Task 7: index.html — Stat card HTML restructure

**Files:**
- Modify: `templates/index.html:50-106`

Removes the colored icon-background squares. The large Manrope number becomes the visual anchor.

- [ ] **Step 1: Replace the entire stat cards block**

Find (lines 50–106):
```html
<!-- 统计卡片：flex 布局，5 张等宽自适应 -->
<div class="d-flex flex-wrap gap-3 mb-4">
  <div class="card stat-card p-3 flex-fill" style="min-width:130px">
    <div class="d-flex align-items-center gap-3">
      <div class="rounded-3 p-2" style="background:#e8f0fe">
        <i class="bi bi-envelope fs-4 text-primary"></i>
      </div>
      <div>
        <div class="stat-num text-primary">{{ stats.total }}</div>
        <div class="text-muted small">总邮件数</div>
      </div>
    </div>
  </div>
  <div class="card stat-card p-3 flex-fill" style="min-width:130px">
    <div class="d-flex align-items-center gap-3">
      <div class="rounded-3 p-2" style="background:#d1f0e0">
        <i class="bi bi-briefcase fs-4 text-success"></i>
      </div>
      <div>
        <div class="stat-num text-success">{{ stats.inquiries }}</div>
        <div class="text-muted small">有效询盘</div>
      </div>
    </div>
  </div>
  <div class="card stat-card p-3 flex-fill" style="min-width:130px">
    <div class="d-flex align-items-center gap-3">
      <div class="rounded-3 p-2" style="background:#fff3cd">
        <i class="bi bi-hourglass-split fs-4 text-warning"></i>
      </div>
      <div>
        <div class="stat-num text-warning">{{ stats.pending }}</div>
        <div class="text-muted small">待审核草稿</div>
      </div>
    </div>
  </div>
  <div class="card stat-card p-3 flex-fill" style="min-width:130px">
    <div class="d-flex align-items-center gap-3">
      <div class="rounded-3 p-2" style="background:#d1f0e0">
        <i class="bi bi-send-check fs-4 text-success"></i>
      </div>
      <div>
        <div class="stat-num text-success">{{ stats.sent }}</div>
        <div class="text-muted small">已发送回复</div>
      </div>
    </div>
  </div>
  <div class="card stat-card p-3 flex-fill" style="min-width:130px">
    <div class="d-flex align-items-center gap-3">
      <div class="rounded-3 p-2" style="background:#fde8f0">
        <i class="bi bi-trophy fs-4 text-danger"></i>
      </div>
      <div>
        <div class="stat-num text-danger">{{ stats.won }}</div>
        <div class="text-muted small">已成单</div>
      </div>
    </div>
  </div>
</div>
```
Replace with:
```html
<!-- 统计卡片：flex 布局，5 张等宽自适应 -->
<div class="d-flex flex-wrap gap-3 mb-4">
  <div class="card stat-card p-4 flex-fill" style="min-width:130px">
    <div class="stat-num">{{ stats.total }}</div>
    <div class="small mt-2" style="color:var(--on-surface-var)">
      <i class="bi bi-envelope me-1 opacity-50"></i>总邮件数
    </div>
  </div>
  <div class="card stat-card p-4 flex-fill" style="min-width:130px">
    <div class="stat-num">{{ stats.inquiries }}</div>
    <div class="small mt-2" style="color:var(--on-surface-var)">
      <i class="bi bi-briefcase me-1 opacity-50"></i>有效询盘
    </div>
  </div>
  <div class="card stat-card p-4 flex-fill" style="min-width:130px">
    <div class="stat-num" style="color:#b35c00">{{ stats.pending }}</div>
    <div class="small mt-2" style="color:var(--on-surface-var)">
      <i class="bi bi-hourglass-split me-1 opacity-50"></i>待审核草稿
    </div>
  </div>
  <div class="card stat-card p-4 flex-fill" style="min-width:130px">
    <div class="stat-num" style="color:#1a6b3a">{{ stats.sent }}</div>
    <div class="small mt-2" style="color:var(--on-surface-var)">
      <i class="bi bi-send-check me-1 opacity-50"></i>已发送回复
    </div>
  </div>
  <div class="card stat-card p-4 flex-fill" style="min-width:130px">
    <div class="stat-num" style="color:var(--error)">{{ stats.won }}</div>
    <div class="small mt-2" style="color:var(--on-surface-var)">
      <i class="bi bi-trophy me-1 opacity-50"></i>已成单
    </div>
  </div>
</div>
```

- [ ] **Step 2: Verify stat cards**

Start server → open http://localhost:8000:
- Five cards with large bold numbers (Manrope 2rem), no colored icon boxes
- Labels are small gray text with a faint icon prefix
- "待审核" number is amber-ish (#b35c00), "已发送" is green, "已成单" is error red

- [ ] **Step 3: Stop server and commit**

```bash
git add templates/index.html
git commit -m "style: index.html stat cards — remove icon squares, Manrope large numbers"
```

---

## Task 8: draft.html — Verification card + action area

**Files:**
- Modify: `templates/draft.html:72-77` (verification card borders)
- Modify: `templates/draft.html:82` (inner border-end divider)
- Modify: `templates/draft.html:170` (draft card header)
- Modify: `templates/draft.html:234-239` (send-to preview bar)

- [ ] **Step 1: Update verification card — replace yellow full-border with amber left-strip**

Find:
```html
    <div class="card border-warning" style="border-width:2px!important">
      <div class="card-header bg-warning bg-opacity-10 fw-semibold d-flex align-items-center gap-2">
        <i class="bi bi-shield-check text-warning"></i>
        核验：AI理解是否正确？
        <span class="ms-auto text-muted fw-normal small">发送前请逐项确认</span>
      </div>
```
Replace with:
```html
    <div class="card" style="border-left: 4px solid #e6a817 !important; border-radius: 0 0.75rem 0.75rem 0.75rem; background: var(--surface-panel);">
      <div class="card-header fw-semibold d-flex align-items-center gap-2" style="background:transparent">
        <i class="bi bi-shield-check" style="color:#e6a817"></i>
        核验：AI理解是否正确？
        <span class="ms-auto text-muted fw-normal small">发送前请逐项确认</span>
      </div>
```

- [ ] **Step 2: Remove inner vertical divider line**

Find:
```html
          <!-- 左：AI从询盘里读到的 -->
          <div class="col-6 border-end p-3">
```
Replace with:
```html
          <!-- 左：AI从询盘里读到的 -->
          <div class="col-6 p-3" style="border-right: 1px solid rgba(195,198,207,0.2)">
```

- [ ] **Step 3: Remove draft card header's bg-white**

Find:
```html
      <div class="card-header bg-white d-flex justify-content-between align-items-center">
        <span class="fw-semibold"><i class="bi bi-pencil me-2 text-secondary"></i>回复草稿</span>
```
Replace with:
```html
      <div class="card-header d-flex justify-content-between align-items-center">
        <span class="fw-semibold"><i class="bi bi-pencil me-2" style="color:var(--on-surface-var)"></i>回复草稿</span>
```

- [ ] **Step 4: Update the "send-to" preview bar**

Find:
```html
        <div class="d-flex align-items-center gap-2 mb-3 px-1 py-2 rounded"
             style="background:#f0f7ff;border:1px solid #cfe2ff">
          <i class="bi bi-send text-primary"></i>
```
Replace with:
```html
        <div class="d-flex align-items-center gap-2 mb-3 px-3 py-2 rounded"
             style="background:var(--surface-panel);border-left:3px solid var(--secondary)">
          <i class="bi bi-send" style="color:var(--secondary)"></i>
```

- [ ] **Step 5: Verify draft page**

Start server → open any draft URL (e.g., http://localhost:8000/draft/1):
- Verification card: `--surface-panel` background, amber left strip (4px), no full yellow border
- Inner column divider: barely visible ghost line
- Draft card header: no bg-white flash, seamlessly transparent
- "发送至" bar: `--surface-panel` background, blue left accent
- "批准并发送" button: Bootstrap green (kept for semantic)
- "拒绝" button: `--error` red (#ba1a1a)

- [ ] **Step 6: Stop server and commit**

```bash
git add templates/draft.html
git commit -m "style: draft.html — amber left-strip verification card, surface-panel send bar, ghost divider"
```

---

## Task 9: Final verification pass

- [ ] **Step 1: Start server and check all three pages end-to-end**

```bash
cd "D:/Work space/email-reply-demo"
venv/Scripts/python -m uvicorn main:app --port 8000
```

Checklist:
- [ ] **Login** (`/login`): ambient shadow card, Manrope title, bottom-border inputs, gradient button
- [ ] **Index** (`/`): 5 stat cards with large Manrope numbers; table with ALL CAPS header and ghost row borders; all category/status badges are `#dae2ff / #00419e` rectangular style
- [ ] **Draft** (`/draft/<id>`): amber-strip verification card, no yellow border; surface-panel textarea; send bar with blue left accent
- [ ] **Functional checks**:
  - [ ] Search form submits and filters correctly
  - [ ] Row click expands email preview (toggle icon rotates)
  - [ ] Batch checkbox select/deselect works
  - [ ] "检查邮件" button submits poll form
  - [ ] SSE badge counter updates (watch `badge-drafts` in DevTools → Network → EventStream)
  - [ ] Draft textarea unsaved indicator still shows yellow border on edit
  - [ ] "批准并发送" triggers confirm modal before submit

- [ ] **Step 2: Fix any regressions found above, then commit**

```bash
git add templates/
git commit -m "style: final verification — fix any regressions after Architect's Ledger redesign"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Google Fonts: Manrope + Inter | Task 1 |
| CSS design tokens `:root` | Task 2 |
| body font Inter, bg `--surface` | Task 2 |
| navbar brand Manrope | Task 3 |
| cards: ambient shadow, no border, 0.75rem radius | Task 3 |
| card-header: no bottom border | Task 3 |
| badges: `#dae2ff / #00419e`, 0.375rem radius | Task 4 |
| table: ALL CAPS header, ghost row border, surface-panel bg | Task 4 |
| row hover → surface-card | Task 4 |
| form controls: surface-panel bg, bottom-border focus only | Task 5 |
| btn-primary: gradient 135deg, hover to secondary | Task 5 |
| btn-danger: error color only | Task 5 |
| login.html: ambient shadow, Manrope, gradient button | Task 6 |
| index.html stat cards: large Manrope numbers, no icon squares | Task 7 |
| draft.html verification card: amber left-strip, surface-panel | Task 8 |
| "send-to" bar: surface-panel + secondary left accent | Task 8 |
| draft card header: no bg-white | Task 8 |
| All functionality preserved | Task 9 |

**Placeholder scan:** None found — all steps contain exact code.

**Type consistency:** No shared function signatures across tasks; each task is a self-contained Edit. No cross-task naming conflicts.
