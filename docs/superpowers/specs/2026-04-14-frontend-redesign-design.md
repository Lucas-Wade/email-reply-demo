# Frontend Redesign: The Architect's Ledger
**Date:** 2026-04-14  
**Scope:** Core pages — `base.html`, `login.html`, `index.html`, `draft.html`  
**Approach:** Method A — all design tokens centralized in `base.html` `<style>` block  
**Constraint:** All existing functionality must remain intact (routes, forms, JS behaviors, SSE, bulk ops)

---

## 1. Design System Source

Based on `templates/DESIGN.md` — "The Executive Lens / The Architect's Ledger".

---

## 2. CSS Variables (Design Tokens)

Replace existing `:root` block in `base.html`:

```css
:root {
  --surface:         #f8f9fd;   /* page background */
  --surface-panel:   #eceef1;   /* search bars, table headers, input bg */
  --surface-card:    #ffffff;   /* interactive cards */
  --on-surface:      #191c1f;   /* primary text */
  --on-surface-var:  #43474e;   /* secondary text, table headers */
  --brand:           #1a3a5c;   /* navbar, brand anchor */
  --brand-light:     #2a5080;   /* navbar hover */
  --primary:         #002444;   /* button base */
  --secondary:       #0055c9;   /* button hover, focus accent */
  --badge-bg:        #dae2ff;   /* status badge background */
  --badge-text:      #00419e;   /* status badge text */
  --error:           #ba1a1a;   /* destructive actions only */
  --urgent:          #4f3300;   /* high-priority/urgent badges */
  --shadow-ambient:  0px 12px 32px rgba(25, 28, 31, 0.06);
}
```

---

## 3. Typography

Add Google Fonts import at top of `<head>`:
```html
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
```

- `font-family` on `body`: `'Inter', sans-serif`
- Headlines / stat numbers / navbar brand: `font-family: 'Manrope', sans-serif`
- Table headers: Inter, `text-transform: uppercase`, `letter-spacing: 0.05em`, color `--on-surface-var`

---

## 4. The "No-Line" Rule

- **Remove** all `border: 1px solid` used for sectioning (card borders, dividers, table row borders)
- **Keep** functional borders: form inputs (structural), accessibility outlines
- **Table row ghost border:** `border-bottom: 1px solid rgba(195, 198, 207, 0.15)` — felt, not seen
- **Depth via tonal layering:** `--surface` → `--surface-panel` → `--surface-card`

---

## 5. Component Specifications

### Cards
```css
.card {
  background: var(--surface-card);
  border: none;
  border-radius: 0.75rem;
  box-shadow: var(--shadow-ambient);
}
.card-header {
  background: transparent;
  border-bottom: none;        /* removed — use padding/whitespace instead */
  padding: 1rem 1.25rem 0.5rem;
}
```

### Primary Buttons
```css
.btn-primary {
  background: linear-gradient(135deg, #1a3a5c, #002444);
  border: none;
  border-radius: 0.375rem;
  font-family: 'Inter', sans-serif;
  font-weight: 600;
  transition: background 200ms ease-in-out;
}
.btn-primary:hover {
  background: #0055c9;
}
```

### Danger Buttons (destructive only)
- Color: `--error` (#ba1a1a)
- Reserved for: reject draft, delete, ignore

### Input Fields
```css
.form-control, .form-select {
  background: var(--surface-panel);
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0.375rem 0.375rem 0 0;
}
.form-control:focus, .form-select:focus {
  background: var(--surface-panel);
  border-bottom-color: var(--secondary);
  box-shadow: none;
}
```

### Status Badges
All status/category badges use unified style:
```css
.badge {
  background: var(--badge-bg);
  color: var(--badge-text);
  border-radius: 0.375rem;   /* architectural, not pill */
  font-weight: 600;
}
```
Exceptions:
- Urgent/high-priority: `background: #4f3300; color: #fff`
- Destructive: `background: #ba1a1a; color: #fff`

### Table
```css
thead tr { background: var(--surface-panel); }
thead th {
  font-family: 'Inter', sans-serif;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--on-surface-var);
  font-weight: 600;
}
tbody tr {
  border-bottom: 1px solid rgba(195, 198, 207, 0.15);
}
tbody tr:hover { background: var(--surface-card); }
```

---

## 6. Page-by-Page Changes

### `base.html`
- Add Google Fonts `<link>`
- Replace `:root` variables
- Replace `.card`, `.card-header` styles
- Replace `.btn-primary`, `.btn-danger`, `.btn-outline-*` overrides
- Replace `.form-control`, `.form-select` styles
- Replace `.badge-*` styles
- Replace `.table` styles
- Update `body` font to Inter
- Update `.navbar-brand` to Manrope
- Update `.stat-num` to Manrope
- **Do not change** navbar background (stays `--brand` #1a3a5c)
- **Do not change** any JS, routes, SSE, modal IDs, form actions

### `login.html`
- `.login-card`: remove `overflow:hidden`, add `var(--shadow-ambient)`, `border-radius: 0.75rem`
- `.login-header`: keep `--brand` bg, switch title to Manrope Bold
- Inputs: apply new bottom-border focus style
- Submit button: gradient primary style
- Background: change to `--surface` (#f8f9fd)

### `index.html`
- **Stat cards:** Remove colored icon-background squares. New layout: large Manrope number (1.9rem bold) + small Inter label below. Keep the 5-card flex row.
- **Search bar card:** `background: var(--surface-panel)`, no border, subtle ambient shadow
- **Table header:** ALL CAPS, Inter, `--on-surface-var` color, `--surface-panel` background
- **Table rows:** ghost border only, hover to `--surface-card`
- **Category/status badges:** unified `--badge-bg / --badge-text` with `0.375rem` radius
- **Pagination:** keep existing links, update active state to `--primary`
- **Preview pane** (expanded row): `--surface-panel` background, left accent `--secondary`

### `draft.html`
- **Left column (original inquiry):** `--surface-card` card, no border
- **Verification card** (currently yellow-bordered): replace with `--surface-panel` background + `4px solid #e6a817` left accent only (no full border)
- **Draft textarea:** `--surface-panel` background, bottom-border focus
- **Approve button:** gradient green (`linear-gradient(135deg, #1a6b3a, #0d5c31)`) — green kept for semantic send action
- **Reject button:** `--error` (#ba1a1a)
- **"Sending to" preview bar:** `--surface-panel` background, left `--secondary` accent, no blue border box
- **All inner card-headers:** remove bottom border, use padding only

---

## 7. What Does NOT Change

| Element | Reason |
|---|---|
| Navbar background (#1a3a5c) | Brand anchor per spec |
| All `<form action="">` attributes | Functional |
| All `id=` attributes on JS targets | Functional |
| SSE EventSource logic | Functional |
| Bulk operation bar structure | Functional |
| Modal IDs (confirmModal, dealModal) | Functional |
| Badge-specific semantic meaning | Only visual style changes |
| `.spin` animation | Functional |
| `btn-copy` behavior | Functional |

---

## 8. Out of Scope (This Phase)

- `analytics.html`, `customers.html`, `settings.html`, `followups.html`, `check.html`, `help.html`, `products.html`, `company.html`, `password.html`, `customer_detail.html`
- No new JS introduced
- No changes to `main.py` or backend
