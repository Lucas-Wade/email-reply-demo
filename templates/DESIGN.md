# Design System Document: The Executive Lens

## 1. Overview & Creative North Star
### Creative North Star: "The Architect’s Ledger"
The design system is built upon the concept of **The Architect’s Ledger**. In a B2B SaaS environment focused on "Inquiry Replies," the interface must balance the authority of a legacy financial institution with the fluidity of modern high-performance software. We move beyond the "standard dashboard" by treating data not as items in a list, but as an editorial narrative.

The system breaks the "template" look through **tonal depth** and **intentional negative space**. By removing 1px borders—the hallmark of "cheap" SaaS—we rely on sophisticated layering and typographic weight to guide the eye. The result is a high-contrast, professional environment that feels curated, not cluttered.

---

## 2. Colors & Surface Philosophy
Our palette is anchored in **Deep Navy (#1a3a5c)** for authority and **Professional Blue (#0d6efd)** for action. However, the sophistication lies in how these colors are layered.

### The "No-Line" Rule
Explicitly prohibited: 1px solid borders for sectioning. Boundaries must be defined solely through background color shifts. 
- Use `surface_container_low` for the main canvas.
- Use `surface_container_lowest` (Pure White) for active interactive cards.
- Use `surface_container_high` for sidebar navigation or utility panels.
*This creates a "seamless" interface where the UI feels like a single, cohesive piece of industrial design.*

### Surface Hierarchy & Nesting
Treat the UI as a series of physical layers. 
- **Tier 1 (Base):** `surface` (#f8f9fd) - The vast landscape.
- **Tier 2 (Panels):** `surface_container` (#eceef1) - Defined functional areas.
- **Tier 3 (Interaction):** `surface_container_lowest` (#ffffff) - Actionable cards that "float" toward the user.

### Signature Textures & Glassmorphism
To avoid a flat, "out-of-the-box" feel:
- **Floating Modals:** Use `surface_container_lowest` with a 20px `backdrop-blur`. This allows the deep navy of the primary brand colors to bleed through softly, grounding the element in the workspace.
- **CTA Gradients:** Main action buttons should use a subtle linear gradient from `primary_container` (#1a3a5c) to `primary` (#002444) at a 135-degree angle. This adds a "weighted" feel to the click.

---

## 3. Typography
We use a dual-font strategy to balance character with readability.

*   **Display & Headlines (Manrope):** A geometric sans-serif that feels engineered and modern. 
    - Use `display-md` for high-level inquiry metrics to convey scale.
    - Use `headline-sm` for section titles to establish a firm "Editorial" anchor.
*   **Body & Labels (Inter):** The workhorse. Inter provides exceptional legibility at small sizes for complex inquiry data.
    - **Title-sm:** Used for table headers; always in `on_surface_variant` (#43474e) with all-caps styling to differentiate from data.
    - **Body-md:** The standard for inquiry content.

---

## 4. Elevation & Depth
We eschew traditional drop shadows for **Tonal Layering**.

### The Layering Principle
Depth is achieved by "stacking." A card does not need a shadow if it is `surface_container_lowest` sitting on a `surface_container_low` background. The subtle shift in hex value provides a cleaner, more premium distinction.

### Ambient Shadows
Where a "floating" effect is mandatory (e.g., a reply fly-out menu), use:
- **Shadow:** `0px 12px 32px rgba(25, 28, 31, 0.06)`. 
- The shadow must be tinted with the `on_surface` color to mimic natural ambient light.

### The Ghost Border Fallback
For accessibility in high-density tables, use a **Ghost Border**: `outline_variant` (#c3c6cf) at **15% opacity**. It should be felt, not seen.

---

## 5. Components

### Modern Cards (The Inquiry Card)
- **Style:** No borders. Background: `surface_container_lowest`. 
- **Layout:** Use `xl` (0.75rem) rounded corners. 
- **Content:** Forbid divider lines. Use 24px of vertical white space to separate the "Inquiry Subject" from the "Customer Meta-data."

### The Sleek Table
- **Header:** `surface_container_high` with `label-md` text.
- **Rows:** Hover state triggers a shift to `surface_container_lowest`. 
- **Indicators:** Status badges (e.g., "Replied," "Pending") use `secondary_fixed` (#dae2ff) backgrounds with `on_secondary_fixed_variant` (#00419e) text. No pill-shapes; use `md` (0.375rem) corners for a more architectural look.

### Primary Buttons
- **Shape:** `md` roundedness. 
- **Color:** `primary` (#002444).
- **Interaction:** On hover, shift to `secondary` (#0055c9) with a 200ms ease-in-out transition.

### Input Fields
- **Background:** `surface_container_low`.
- **Active State:** A 2px bottom-border only in `secondary` (#0055c9). This maintains the "No-Line" rule for the container while providing clear feedback.

---

## 6. Do’s and Don’ts

### Do:
- **Do** use `display-lg` typography for single, impactful data points (e.g., "Average Response Time").
- **Do** use `tertiary_container` (#4f3300) for "Urgent/High-Priority" inquiry badges to create a high-contrast call to action that breaks the blue monochromatic feel.
- **Do** utilize asymmetrical layouts in the dashboard hero to lead the user's eye from the "Total Inquiries" to the "Reply Action."

### Don't:
- **Don't** use 1px grey lines to separate list items. Use white space (`spacing-4` or `spacing-6`).
- **Don't** use pure black (#000000) for text. Always use `on_surface` (#191c1f) to maintain the premium tonal range.
- **Don't** use "Alert Red" for everything. Reserve `error` (#ba1a1a) for destructive actions (e.g., "Delete Inquiry") to maintain a calm, professional atmosphere.