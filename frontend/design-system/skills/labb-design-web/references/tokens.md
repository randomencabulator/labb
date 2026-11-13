# labb's tokens

Every CSS variable, from `@labb/tokens/tokens.css` (load it once, at the root). Use system colours (`--lb-sys-color-*`) for every colour; the values in brackets are the palette steps they point at, for reference only.

## System colours

### Surfaces

The page, and what sits on it.

| Variable | For | Light | Dark | Read on |
| --- | --- | --- | --- | --- |
| `var(--lb-sys-color-bg)` | The page behind everything | #fffdf3 (sand.50) | #0b0b0a (ink.950) |  |
| `var(--lb-sys-color-surface)` | Cards, the sidebar, dialogs, menus | #ffffff (white) | #161614 (ink.900) |  |
| `var(--lb-sys-color-active)` | Hovered and selected rows | #f4f0dc (sand.200) | #24241f (ink.800) |  |
| `var(--lb-sys-color-code-bg)` | Code blocks and tool lines | #f7f4e6 (sand.100) | #10100e (ink.925) |  |
| `var(--lb-sys-color-border)` | Lines between things, quiet outlines | #e2ddc6 (sand.300) | #2e2e2a (ink.700) |  |

### Text

What's read on the surfaces.

| Variable | For | Light | Dark | Read on |
| --- | --- | --- | --- | --- |
| `var(--lb-sys-color-text)` | Body text, strong outlines | #0b0b0a (ink.950) | #f7f6ef (ink.100) | bg, at least 4.5:1 |
| `var(--lb-sys-color-muted)` | Labels, hints, secondary text | #6d6a5c (sand.500) | #9c9b92 (ink.400) | bg, at least 4.5:1 |

### Brand

Black on light and yellow on dark for lines; yellow to fill with in both.

| Variable | For | Light | Dark | Read on |
| --- | --- | --- | --- | --- |
| `var(--lb-sys-color-accent)` | Accent text, focus rings, selected outlines | #0b0b0a (ink.950) | #ffd400 (yellow.300) | bg, at least 4.5:1 |
| `var(--lb-sys-color-accent-fill)` | Primary buttons, switches, the yellow bar | #ffd400 (yellow.300) | #ffd400 (yellow.300) |  |
| `var(--lb-sys-color-on-accent)` | Text and icons on accent-fill | #0b0b0a (ink.950) | #0b0b0a (ink.950) | accent-fill, at least 4.5:1 |
| `var(--lb-sys-color-link-mark)` | The highlighter under links: yellow.300 at 60% in light, none in dark | #ffd40099 | transparent |  |
| `var(--lb-sys-color-logo-inner)` | The inside of the flask logo | #ffd400 (yellow.300) | #0b0b0a (ink.950) |  |

### Messages

Your own messages are inverted.

| Variable | For | Light | Dark | Read on |
| --- | --- | --- | --- | --- |
| `var(--lb-sys-color-user-bg)` | Your messages, toasts | #0b0b0a (ink.950) | #f7f6ef (ink.100) |  |
| `var(--lb-sys-color-user-text)` | Text on user-bg | #fffdf3 (sand.50) | #0b0b0a (ink.950) | user-bg, at least 4.5:1 |

### Status

Errors, and the hard shadows.

| Variable | For | Light | Dark | Read on |
| --- | --- | --- | --- | --- |
| `var(--lb-sys-color-error)` | Errors, failed runs, destructive hovers | #c92a2a (red.600) | #ff6b6b (red.400) | surface, at least 4.5:1 |
| `var(--lb-sys-color-hard)` | Hard offset shadows under popovers | #0b0b0a (ink.950) | #000000 (black) |  |
| `var(--lb-sys-color-press)` | The shadow under yellow buttons | #0b0b0a (ink.950) | #8a7300 (yellow.600) |  |

### Overlays

What sits over the page, the same in both themes.

| Variable | For | Light | Dark | Read on |
| --- | --- | --- | --- | --- |
| `var(--lb-sys-color-scrim)` | Behind dialogs: black at 40% | rgb(0 0 0 / 0.4) | rgb(0 0 0 / 0.4) |  |
| `var(--lb-sys-color-knob)` | The knob of a switch, on or off | #ffffff (white) | #ffffff (white) |  |

### Code

Syntax colours, each at least 4.5:1 on code-bg. Constants are yellow on dark.

| Variable | For | Light | Dark | Read on |
| --- | --- | --- | --- | --- |
| `var(--lb-sys-color-code-comment)` | Comments | #6d6a5c (sand.500) | #9c9b92 (ink.400) | code-bg, at least 4.5:1 |
| `var(--lb-sys-color-code-keyword)` | import, const, return, => | #b03a10 (orange.700) | #ff8f5c (orange.400) | code-bg, at least 4.5:1 |
| `var(--lb-sys-color-code-string)` | Strings | #187150 (green.700) | #7ad6a8 (green.300) | code-bg, at least 4.5:1 |
| `var(--lb-sys-color-code-constant)` | Numbers, true, CSS values | #8a5a00 (amber.800) | #ffd400 (yellow.300) | code-bg, at least 4.5:1 |
| `var(--lb-sys-color-code-tag)` | JSX and HTML tags, types, CSS selectors | #1f5fb8 (blue.700) | #79b0ff (blue.400) | code-bg, at least 4.5:1 |
| `var(--lb-sys-color-code-attr)` | Props, attributes, CSS properties | #7c3fb0 (purple.700) | #c9a6ff (purple.300) | code-bg, at least 4.5:1 |
| `var(--lb-sys-color-code-function)` | Functions and commands | #0f6e80 (cyan.700) | #6fd0e0 (cyan.400) | code-bg, at least 4.5:1 |
| `var(--lb-sys-color-code-punctuation)` | Brackets, braces, operators | #5c594d (sand.600) | #d1d0c8 (ink.300) | code-bg, at least 4.5:1 |

### Chart series

In this order; checked for colour vision deficiency on surface.

| Variable | For | Light | Dark | Read on |
| --- | --- | --- | --- | --- |
| `var(--lb-sys-color-series-1)` | First series | #2a78d6 (blue.600) | #3987e5 (blue.500) | surface |
| `var(--lb-sys-color-series-2)` | Second series | #eb6834 (orange.500) | #d95926 (orange.600) | surface |
| `var(--lb-sys-color-series-3)` | Third series, and success | #1baf7a (green.500) | #199e70 (green.600) | surface |
| `var(--lb-sys-color-series-4)` | Fourth series | #eda100 (amber.500) | #c98500 (amber.600) | surface |
| `var(--lb-sys-color-series-other)` | Everything else | #9c9b92 (ink.400) | #6b6b65 (ink.500) | surface |

## Brand colours (the same in both themes)

| Variable | Value |
| --- | --- |
| `var(--lb-brand-yellow)` | #ffd400 |
| `var(--lb-brand-black)` | #0b0b0a |
| `var(--lb-brand-paper)` | #fffdf3 |

## Spacing

| Variable | Value |
| --- | --- |
| `var(--lb-space-0)` | 0px |
| `var(--lb-space-1)` | 2px |
| `var(--lb-space-2)` | 4px |
| `var(--lb-space-3)` | 6px |
| `var(--lb-space-4)` | 8px |
| `var(--lb-space-5)` | 10px |
| `var(--lb-space-6)` | 12px |
| `var(--lb-space-7)` | 14px |
| `var(--lb-space-8)` | 16px |
| `var(--lb-space-10)` | 20px |
| `var(--lb-space-12)` | 24px |
| `var(--lb-space-16)` | 32px |

## Corners

| Variable | Value | For |
| --- | --- | --- |
| `var(--lb-radius-xs)` | 2px | Inline code, the corner of your messages |
| `var(--lb-radius-sm)` | 3px | Badges, tool lines, secrets |
| `var(--lb-radius-md)` | 4px | Buttons, inputs, chips, toasts |
| `var(--lb-radius-lg)` | 6px | Cards, messages, dialogs, the message box |
| `var(--lb-radius-xl)` | 8px | Large panels |
| `var(--lb-radius-sheet)` | 14px | The top of bottom sheets on phones |
| `var(--lb-radius-full)` | 999px | Dots and avatars |

## Type

| Variable | Size | Line height | For |
| --- | --- | --- | --- |
| `var(--lb-text-2xs)` | 11px | `var(--lb-leading-2xs)` (1.4) | Mono labels, table heads, counts |
| `var(--lb-text-xs)` | 12px | `var(--lb-leading-xs)` (1.45) | Hints, small print, badges |
| `var(--lb-text-sm)` | 13px | `var(--lb-leading-sm)` (1.5) | Controls, sidebar, tables |
| `var(--lb-text-md)` | 14px | `var(--lb-leading-md)` (1.55) | Secondary body, toasts |
| `var(--lb-text-base)` | 15px | `var(--lb-leading-base)` (1.55) | Body text |
| `var(--lb-text-lg)` | 17px | `var(--lb-leading-lg)` (1.4) | Small headings |
| `var(--lb-text-xl)` | 20px | `var(--lb-leading-xl)` (1.3) | Page titles |
| `var(--lb-text-2xl)` | 24px | `var(--lb-leading-2xl)` (1.2) | Section headings |
| `var(--lb-text-3xl)` | 32px | `var(--lb-leading-3xl)` (1.1) | The empty chat's greeting |

| Variable | Value |
| --- | --- |
| `var(--lb-font-sans)` | system-ui, -apple-system, "Segoe UI", sans-serif |
| `var(--lb-font-mono)` | ui-monospace, SFMono-Regular, Menlo, Consolas, monospace |
| `var(--lb-weight-regular)` | 400 |
| `var(--lb-weight-medium)` | 500 |
| `var(--lb-weight-semibold)` | 600 |
| `var(--lb-weight-bold)` | 700 |
| `var(--lb-weight-black)` | 900 |
| `var(--lb-tracking-heading)` | -0.03em |
| `var(--lb-tracking-wordmark)` | -0.05em |
| `var(--lb-tracking-label)` | 0.08em |

## Shadows

| Variable | Value | For |
| --- | --- | --- |
| `var(--lb-shadow-press)` | `2px 2px 0px 0px var(--lb-sys-color-press)` | Under yellow buttons at rest |
| `var(--lb-shadow-press-hover)` | `3px 3px 0px 0px var(--lb-sys-color-press)` | Under yellow buttons on hover |
| `var(--lb-shadow-toast)` | `4px 4px 0px 0px var(--lb-sys-color-hard)` | Toasts |
| `var(--lb-shadow-popover)` | `5px 5px 0px 0px var(--lb-sys-color-hard)` | Menus, popovers, dialogs, tooltips |
| `var(--lb-shadow-lift)` | `4px 4px 0px 0px var(--lb-sys-color-accent-fill)` | Cards on hover, the focused message box |
| `var(--lb-shadow-bar)` | `inset 3px 0px 0px 0px var(--lb-sys-color-accent-fill)` | The yellow bar on what's open |
| `var(--lb-shadow-soft)` | `0px 1px 2px 0px rgb(0 0 0 / 0.1)` | A picked segment |
| `var(--lb-shadow-knob)` | `0px 1px 2px 0px rgb(0 0 0 / 0.25)` | Under a switch's knob |

## Motion

| Variable | Value | For |
| --- | --- | --- |
| `var(--lb-motion-press)` | `80ms ease` | Buttons lifting and pressing |
| `var(--lb-motion-lift)` | `100ms ease` | Cards lifting |
| `var(--lb-motion-base)` | `150ms ease` | Switches, colour changes |
| `var(--lb-motion-sheet)` | `240ms cubic-bezier(0.2, 1, 0.3, 1)` | Sheets sliding up |

## Borders and layers

| Variable | Value |
| --- | --- |
| `var(--lb-border-thin)` | 1px |
| `var(--lb-border-strong)` | 1.5px |
| `var(--lb-border-heavy)` | 2px |
| `var(--lb-border-focus)` | 2px |
| `var(--lb-z-sticky)` | 1 |
| `var(--lb-z-popover)` | 25 |
| `var(--lb-z-modal)` | 30 |
| `var(--lb-z-toast)` | 40 |
| `var(--lb-z-sheet)` | 60 |

## The palette (not for components)

`--lb-ref-<hue>-<step>` is the reference layer: sand (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950); ink (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 925, 950); yellow (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950); amber (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950); orange (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950); red (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950); green (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950); cyan (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950); blue (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950); purple (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950); plus `--lb-ref-white` and `--lb-ref-black`. System colours point at these steps. Don't use them in pages or components.
