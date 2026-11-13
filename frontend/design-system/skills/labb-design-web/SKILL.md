---
name: labb-design-web
description: "Build and style web pages and React components with labb's design system: its CSS variables (--lb-sys-color-*, --lb-space-*, --lb-radius-*, --lb-text-*, --lb-shadow-*…) loaded once globally, and its @labb/ui components on Base UI. Use when building or changing a labb website, web app or UI, when a project depends on @labb/tokens or @labb/ui, or when asked to make something look like labb (black and hazard yellow, hard shadows)."
metadata:
  source: "frontend/design-system/skills/labb-design-web, generated from the tokens and the component registry"
---

# Building on the web with labb's design system

labb's look is a set of CSS variables (the tokens) and React components that use them. Your job when building UI is to **load the variables once, globally, and then use them for every colour and as many other values as possible**, so the page matches labb, follows light and dark mode by itself, and changes when the design system changes.

## 1. Set it up once, at the root

Do this in the app's root (the root layout, `index.html`, `_app`, the global stylesheet), never per page or per component:

```tsx
// Next.js app/layout.tsx, or your app's root
import "@labb/tokens/tokens.css";   // every variable, light and dark
import "@labb/ui/styles.css";       // the components' classes (lb-*), if you use components
import { ToastProvider } from "@labb/ui";

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body className="lb-root">       {/* page background, text colour, font, focus rings */}
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}
```

- Without a bundler, link `tokens.css` (and `styles.css`) in `<head>`, or put `tokensCss()` from `@labb/tokens/css` in a `<style>` there.
- **Theme:** the variables follow the system's light or dark setting. To force one, set `data-theme="light"` or `"dark"` on `<html>`; to put one section in a theme, `data-lb-theme="light"` or `"dark"` on it. **Never** write your own `@media (prefers-color-scheme: dark)` colour overrides: the variables already switch.
- **Never** copy token values into your own `:root` or redefine an `--lb-*` variable. If the values look wrong, the design system is what changes.
- `ToastProvider` only if you show toasts (`useToast()`).

## 2. Every colour is a system colour

Use `var(--lb-sys-color-<name>)` for every colour: backgrounds, text, borders, outlines, shadows, SVG fills and strokes, chart series. Pick by meaning, not by how it looks:

| You're colouring | Use |
| --- | --- |
| The page | `var(--lb-sys-color-bg)` |
| A card, panel, sidebar, dialog or menu | `var(--lb-sys-color-surface)` |
| A hovered or selected row | `var(--lb-sys-color-active)` |
| Lines between things, input borders | `var(--lb-sys-color-border)` |
| Body text, strong outlines | `var(--lb-sys-color-text)` |
| Labels, hints, secondary text | `var(--lb-sys-color-muted)` |
| Links, focus rings, selected outlines (accent-coloured lines and text) | `var(--lb-sys-color-accent)` |
| The primary button, a switch that's on, the yellow bar (a fill) | `var(--lb-sys-color-accent-fill)` |
| Text and icons on accent-fill | `var(--lb-sys-color-on-accent)` |
| Errors, destructive actions | `var(--lb-sys-color-error)` |
| The user's own messages, toasts (inverted) | `var(--lb-sys-color-user-bg)` |
| Text on user-bg | `var(--lb-sys-color-user-text)` |
| Behind a dialog | `var(--lb-sys-color-scrim)` |
| Code blocks, inline code | `var(--lb-sys-color-code-bg)` |
| Chart series, in order: series-1 to series-4, then series-other for the rest | `var(--lb-sys-color-series-1)` |

All 32 are in [references/tokens.md](references/tokens.md), with what each is for.

- **No literal colours.** No `#hex`, `rgb()`, `hsl()`, named colours (`white`, `black`) or Tailwind/other palette classes for colour. `transparent` and `currentColor` are fine.
- **Not the palette.** `--lb-ref-<hue>-<step>` (e.g. `var(--lb-ref-sand-500)`) is the reference layer that system colours are made from. Components and pages don't use it: a system colour says what a colour is for, and changes with the theme; a palette step doesn't.
- **A tint or a see-through version** is `color-mix()` on a system colour: `color-mix(in srgb, var(--lb-sys-color-accent-fill) 10%, transparent)`.
- **Colours that belong together:** text on `bg` or `surface` is `text` or `muted`; on `accent-fill` it's `on-accent`; on `user-bg` it's `user-text`. Don't put `accent-fill` (yellow) text on a light background: it's a fill, and `accent` is the colour for accent text and lines.
- **Brand colours that never change with the theme** (only for the hazard stripes and the logo): `var(--lb-brand-yellow)`, `var(--lb-brand-black)`, `var(--lb-brand-paper)`.
- **If no system colour fits**, use the closest by meaning and say so. Don't invent a hex. If you work on the design system itself, a new colour is a new system colour (pointing at a palette step) in `frontend/design-system/packages/tokens/tokens/sys/`, not a value in your CSS.

## 3. Everything else from the tokens too

| For | Use | Values |
| --- | --- | --- |
| Padding, margins, gaps | `var(--lb-space-<n>)` | 1=2px, 2=4px, 3=6px, 4=8px, 5=10px, 6=12px, 7=14px, 8=16px, 10=20px, 12=24px, 16=32px |
| Corners | `var(--lb-radius-<name>)` | xs=2px, sm=3px, md=4px, lg=6px, xl=8px, sheet=14px, full=999px |
| Font size and line height | `var(--lb-text-<size>)`, `var(--lb-leading-<size>)` | 2xs=11px, xs=12px, sm=13px, md=14px, base=15px, lg=17px, xl=20px, 2xl=24px, 3xl=32px |
| Font weight | `var(--lb-weight-<name>)` | regular=400, medium=500, semibold=600, bold=700, black=900 |
| Font family | `var(--lb-font-sans)`, `var(--lb-font-mono)` | system sans; mono for code, counts and labels |
| Letter spacing | `var(--lb-tracking-<name>)` | heading=-0.03em, wordmark=-0.05em, label=0.08em |
| Shadows | `var(--lb-shadow-<name>)` | press, press-hover, toast, popover, lift, bar, soft, knob |
| Transitions | `var(--lb-motion-<name>)` | press=80ms ease, lift=100ms ease, base=150ms ease, sheet=240ms cubic-bezier(0.2, 1, 0.3, 1) |
| Border widths | `var(--lb-border-<name>)` | thin=1px, strong=1.5px, heavy=2px, focus=2px |
| Stacking | `var(--lb-z-<name>)` | sticky=1, popover=25, modal=30, toast=40, sheet=60 |

Use the nearest step of a scale rather than a value between steps. Literal values are fine only for what no token covers: one component's own dimensions (an icon's 16px, a switch's track) and layout (page widths, breakpoints, grid columns).

## 4. React components before hand-made ones

Use `@labb/ui`'s React components when it has the thing: Button; Toggle and segmented; Switch; Field and inputs; Badge; Card and tile; Menu; Dialog; Toast; Messages and tools; Approval; Logo and type bits. They're [Base UI](https://base-ui.com) primitives underneath (keyboard, focus and ARIA done) with labb's classes. Imports, props and examples: [references/components.md](references/components.md).

- Style a component's states through Base UI's data attributes (`[data-pressed]`, `[data-checked]`, `[data-highlighted]`, `[data-invalid]`, `[data-disabled]`, `[data-starting-style]`/`[data-ending-style]`), not your own state classes.
- For something new that's interactive (a tooltip, a popover, a select, tabs), build it on the Base UI primitive and style it with the variables, like the others.

When you make your own UI, make it look like labb:

- Hairline borders (`var(--lb-border-thin) solid var(--lb-sys-color-border)`); heavier (`--lb-border-strong` in `var(--lb-sys-color-text)`) for popovers and cards that need weight.
- Small corners: `--lb-radius-md` (4px) on controls, `--lb-radius-lg` (6px) on cards and dialogs.
- Hard offset shadows, never blurred: `--lb-shadow-popover` under menus, popovers and dialogs; `--lb-shadow-lift` (yellow) under a card on hover.
- One yellow primary action per view (`<Button variant="primary">`): it lifts on hover and presses down when clicked.
- Headings: `--lb-weight-black` with `--lb-tracking-heading`. Labels over sections: the `lb-label` class (mono, uppercase, spaced).
- Motion with the motion tokens, and none under `prefers-reduced-motion: reduce`.

## 5. Before you're done

- Search the CSS and styles you wrote for `#`, `rgb(`, `hsl(` and `--lb-ref-`: each one is a colour that should be a system colour.
- Look at it in light and dark (`data-theme` on `<html>`), and at a phone width with no sideways scroll.
- Tab through it: every control shows a focus ring and can be used from the keyboard.
- Text keeps its pair (`text`/`muted` on `bg`/`surface`, `on-accent` on `accent-fill`), so contrast holds in both themes.
