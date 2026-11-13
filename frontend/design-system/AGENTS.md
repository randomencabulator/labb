# AGENTS.md

How labb's design system works, how to change it, and how it's meant to reach the apps that use it. Read this before changing anything under `frontend/design-system/`. `docs/AGENTS.md` is written by `next dev` and is about Next.js only.

## What this is

One source of truth for how labb looks: tokens (colours, type, spacing, radii, shadows, motion), React components and a Swift package built on those tokens, and a documentation site that shows them. It started as a copy of the web chat's design (`frontend/web/src/styles.css`, the black-and-hazard-yellow brand) so the iOS app (`mobile/ios`) and anything else can look the same.

It sits in `frontend/` with the web app (`frontend/web/`), and its packages are part of the npm workspace there (`frontend/package.json`), separate from the labb Python package. `frontend/web/` depends on `@labb/tokens` and `@labb/ui` through the workspace but doesn't use them yet; the iOS app (`mobile/ios`) uses the Swift package. See **Distribution**.

```
frontend/design-system/
  packages/tokens/   @labb/tokens: the tokens as JSON, and the build that turns them into CSS and TypeScript
  packages/ui/       @labb/ui: React components (Base UI underneath) and their CSS
  swift/             LabbDesign: the Swift package for iOS (tokens generated, styles and views by hand)
  docs/              the documentation site (Next.js, exported as static HTML)
  skills/            the Agent Skill for building web UI with it (generated; see **The agent skill**)
```

Needs Node 22+ (the token build runs TypeScript with `--experimental-strip-types`).

Everything runs from `frontend/`, where the workspace is:

```bash
npm install              # the whole workspace: web, the design system's packages and the docs
npm run tokens           # check the token JSON and write packages/tokens/generated/
npm run tokens:watch     # the same on every save, next to docs:dev
npm run skill            # write skills/labb-design-web from the tokens and the registry
npm run docs:dev         # tokens, the skill, then the docs on http://localhost:3200
npm run docs:build       # tokens, the skill, then the docs as static HTML in docs/out
npm run docs:preview     # serves docs/out on :3200
npm run design:typecheck # fails if generated/ or the skill is stale, then type-checks everything the docs import
npm run typecheck        # design:typecheck, then the web app's
```

A dependency for one package goes in with `npm install <pkg> -w @labb/ui` (or `-w @labb/tokens`, `-w @labb/design-docs`). The docs site's package is `@labb/design-docs`.

There's no test runner or linter. Check a change with `npm run typecheck` (it also type-checks `frontend/web/`, which compiles these packages) and `npm run docs:build`, then look at the pages it touches (light, dark and phone width) and at their Markdown in `docs/out/`.

## Tokens (`packages/tokens`)

### The source is JSON

The tokens are [Design Tokens Format Module 2025.10](https://www.designtokens.org/tr/2025.10/format/) JSON (the W3C community group's format), so any tool that reads it (Tokens Studio for Figma, Style Dictionary) can read ours, and every platform's output comes from the same files.

```
packages/tokens/
  tokens/
    labb.resolver.json   how the files fit together (the format's Resolver Module)
    ref.json             reference colours: the palette
    base.json            everything that isn't a theme's colour
    sys/light.json       system colours in light (and what each is for)
    sys/dark.json        system colours in dark (values only)
  scripts/build.ts       checks the JSON and writes generated/ and the Swift package's generated files
  scripts/swift.ts       the Swift writer: Tokens.swift and the asset catalog
  generated/             tokens.css and tokens.ts: never edit these, edit the JSON
  src/index.ts           re-exports generated/tokens.ts, plus rgb() and contrast()
  src/css.ts             tokensCss(): the generated CSS as a string
  src/contrast.ts        WCAG contrast, used by the build and the docs
```

What the format asks for, and what we rely on:

- A token has `$value` and a `$type` (on itself or inherited from a group); `$description` and `$extensions` are optional. Groups are any other object. Names can't contain `{`, `}` or `.`.
- Colours are objects: `{"colorSpace": "srgb", "components": [r, g, b], "alpha"?: 0..1, "hex"?: "#rrggbb"}`. The build checks `hex` against `components` and, if they disagree, tells you the components for the hex you meant.
- Dimensions are `{"value": n, "unit": "px" | "rem"}`; durations `{"value": n, "unit": "ms" | "s"}`.
- References are `"{group.token}"` and must point at a token of the same type.
- Things the format doesn't have go in `$extensions."dev.labb"`: a group's `title`, a system colour's `web` (its variable in `frontend/web/src/styles.css`) and `contrast` (`{"on": "<colour name>", "min": 4.5}`), `pinned` on palette steps, `inset` on shadows, `unit: "em"` on tracking (letter spacing is a `number` in em, since dimensions can't be em).

### Two layers of colour

**Reference colours** (`ref.json`, `ref.<hue>.<step>`) are the palette: a ramp from 50 (lightest) to 950 (darkest) for each hue, plus `ref.white` and `ref.black`. They mean nothing on their own.

| Ramp | What it's for |
| --- | --- |
| `sand` | warm neutrals: paper, surfaces and lines in light, muted text |
| `ink` | cool neutrals: text, dark mode's layers (has an extra `925`) |
| `yellow` | hazard yellow, the brand (`yellow.300` is the brand yellow) |
| `amber`, `orange`, `red`, `green`, `cyan`, `blue`, `purple` | errors, charts, syntax colours |

Each ramp's hue and chroma come from the colours labb already used: those are pinned exactly on their steps (`"pinned": true`) and the steps between were generated in OKLCH so lightness steps look even. When a ramp needs a new step, generate it the same way (OKLCH, the ramp's hue, lightness between its neighbours) rather than picking a hex by eye.

**System colours** (`sys/light.json` and `sys/dark.json`, `sys.color.<group>.<name>`) say what a colour is for (`bg`, `surface`, `text`, `muted`, `accent`, `accent-fill`, `error`, `scrim`, `knob`, `code-keyword`, `series-1`…) and point at a palette step per theme, e.g. `bg` is `{ref.sand.50}` in light and `{ref.ink.950}` in dark. Components use system colours only, never the palette, so a theme or a brand change is a change of pointers.

Rules the build enforces:

- `sys/light.json` holds each system colour's `$description` (what it's for), `web` and `contrast`; `sys/dark.json` only values. Both must have the same tokens in the same order.
- A system colour's name (the last part of its path) is unique across groups, because it becomes `--lb-sys-color-<name>`.
- Every `contrast.min` holds in both themes, against the `contrast.on` colour. Text colours ask for 4.5; chart colours name what they sit on but have no minimum.
- Every reference resolves, with no loops.

Rules the build can't enforce:

- A new system colour points at a palette step. If the palette doesn't have the colour, add a step or a ramp to `ref.json` first.
- A colour with transparency (`link-mark`) can't be a reference; keep it a literal and say in its `$description` which step it comes from.

### What the build writes

`scripts/build.ts` reads `labb.resolver.json` (sets `ref` and `base`, then the `theme` modifier's `light` (default) and `dark` contexts), validates everything above, and writes:

- `generated/tokens.css`: `:root` has `--lb-ref-*` (the palette), the shared variables, and the light system colours as `var(--lb-ref-…)`; dark colours apply under `prefers-color-scheme: dark` unless `<html data-theme="light">`, and always under `data-theme="dark"`; `[data-lb-theme="light"|"dark"]` puts one part of a page in a theme.
- `generated/tokens.ts`: the same as typed data (`palette`, `refColors`, `colors` with each colour's light and dark value and palette step, `brand`, `space`, `radius`, `font`, `shadow`, `motion`, `border`, `z`, and `cssText`). The docs read everything from here.

CSS names: `--lb-ref-<hue>-<step>`, `--lb-sys-color-<name>`, `--lb-brand-<name>`, `--lb-space-<n>`, `--lb-radius-<name>`, `--lb-font-sans|mono`, `--lb-text-<size>`, `--lb-leading-<size>`, `--lb-weight-<name>`, `--lb-tracking-<name>`, `--lb-shadow-<name>`, `--lb-motion-<name>`, `--lb-border-<name>`, `--lb-z-<name>`. Renaming or removing one breaks whoever uses it (see **Versioning**).

- The Swift package's `Sources/LabbDesign/Generated/Tokens.swift` and `Sources/LabbDesign/Resources/LabbColors.xcassets` (`scripts/swift.ts`; see **The Swift package**). The build owns only those two folders, removes files in them it no longer makes, and leaves the rest of the package alone.

`npm run typecheck` fails if any of it is stale, a stray file in the generated folders included. A new output is a new writer next to `swift.ts`, fed by `build()` from the same resolved tokens; don't add a second source.

## Components (`packages/ui`)

One folder per component family in `src/components/`, one file per component, and the family's CSS next to them:

```
src/components/menu/Menu.tsx, MenuItem.tsx, MenuSeparator.tsx, menu.css
src/index.ts      exports every component
src/styles.css    imports base.css, then every component's CSS
src/base.css      what any page needs: .lb-root (page colours and type), focus rings, links
src/utils.ts      cx() and the Styled<> helper type
```

- **Base UI underneath.** Interactive components wrap [Base UI](https://base-ui.com) primitives (`@base-ui/react`): Button, Toggle, Segmented (ToggleGroup), Switch, Field/Input/Textarea, Menu, Dialog, Toast. Base UI brings keyboard handling, focus management and ARIA; we bring the look. Style their states through Base UI's data attributes (`[data-pressed]`, `[data-checked]`, `[data-highlighted]`, `[data-invalid]`, `[data-disabled]`, `[data-starting-style]`/`[data-ending-style]` for enter and exit), not `aria-*` or our own classes. Start a new interactive component from a Base UI primitive when there is one.
- **Plain elements** for the rest (Badge, Dot, Card, Tile, Message, ToolLine, Approval, Code, Kbd, Label, Logo, Wordmark).
- **`"use client"`** only in files that use Base UI or hooks, so the plain ones render on the server.
- **Classes start with `lb-`.** Colours always come from system colours (`var(--lb-sys-color-…)`, or `var(--lb-brand-…)` for what's the same in both themes, like the hazard stripes), never a literal or the palette's `--lb-ref-*`: if a colour is missing, it's a new system colour. Spacing, radii, type, shadows and motion come from their tokens where the scale has the value; sizes that belong to one component (a switch's 36×20, a toggle's 34px height) can be literal. Each CSS file ends with its `prefers-reduced-motion` rules if it animates.
- **The brand in CSS:** hairline borders, 4px corners on controls and 6px on cards, hard offset shadows (`--lb-shadow-popover` under menus and dialogs), yellow fills that lift on hover and press on click, heavy headings, mono labels.
- Toasts need a `ToastProvider` around the app, then `useToast()`.

## The Swift package (`swift`)

`LabbDesign` (`swift/Package.swift`: swift-tools 6.0, iOS 17+ and macOS 14+, Swift 6 language mode), for SwiftUI apps. The iOS app adds it as a local package (`../../frontend/design-system/swift`). Build it with Xcode, not `swift build`, since asset catalogs need Xcode: `xcodebuild -scheme LabbDesign -destination 'generic/platform=iOS Simulator' build` in `swift/`.

```
swift/Sources/LabbDesign/
  Generated/Tokens.swift              generated: colours, spacing, radii, borders, layers, type, shadows, motion
  Resources/LabbColors.xcassets       generated: sys/<name> (light and dark) and ref/<hue>-<step> colour sets
  Components/Foundations.swift        .labbRoot(), .labbText(), .labbHeading(), .labbLabel(), .labbShadow()
  Components/LabbButtonStyle.swift    .buttonStyle(.labb(.primary | .secondary | .danger | .ghost))
  Components/LabbControls.swift       .toggleStyle(.labb), .labbCard(lifted:), LabbBadge, .labbField()
```

- **Names follow the tokens**, camel-cased: `--lb-sys-color-accent-fill` is `Color.labb.accentFill` (or `.labb.accentFill` where a `ShapeStyle` is expected), `--lb-ref-sand-500` is `Color.labbPalette.sand500`, brand colours are `Color.labbBrand.*`, `--lb-space-6` is `LabbSpace.s6`, `--lb-radius-md` is `LabbRadius.md`, text sizes are `LabbTextStyle` cases (`2xs` becomes `xxs`, `3xl` becomes `xxxl`), shadows `LabbShadow.*`, motion `LabbMotion.*` (SwiftUI `Animation`s). Renaming a token renames the Swift too.
- **Colours come from the asset catalog**, so light and dark follow the system appearance with nothing in code. They're written as exact 8-bit sRGB, so they're the CSS's colours to the bit. Alpha is written as `"1.000"`: beside 8-bit components, Xcode reads a bare `"1"` as 1 out of 255, which makes every colour all but invisible.
- **Type scales with Dynamic Type:** each token size follows the Apple text style nearest to it (`LabbTextStyle.relativeTo`), through `@ScaledMetric` in `.labbText`. Line height is approximate (SwiftUI's own is about 1.2 times the size; `.lineSpacing` adds the rest).
- **The hand-written styles and views** use only the generated tokens and system colours, like `@labb/ui`'s CSS: no literal colours, no palette. On purpose different from the web: controls are at least 44pt tall, there's no hover, and motion stops under `accessibilityReduceMotion`.
- **Swift 6 isolation:** SwiftUI calls some style protocols (`TextFieldStyle`) off the main actor, so a style can't call a `View` extension (they're main-actor isolated), and a main-actor conformance isn't accepted either. Use a view modifier instead, as `.labbField()` does; `ButtonStyle` and `ToggleStyle` work, since they hand off to a `View`.
- **Check a change** by building the package (above) and the iOS app (`mobile/README.md`), and look at it in the Simulator in light and dark (`xcrun simctl ui <device> appearance dark`).

## The documentation site (`docs`)

Next.js 16 (App Router) with `output: "export"`: every page is static HTML in `docs/out`, so any static host can serve it. `PAGES_BASE_PATH=/x` builds it to live under `/x` (e.g. GitHub Pages' `/<repo>`), and `SITE_URL=https://…` makes the links in the Markdown and `llms.txt` absolute.

```
app/                 pages: overview, colors, palette, typography, spacing, effects, ios, skill, components/<slug>
app/skills/          the agent skill's files
app/[...path]/       every page as Markdown
app/llms.txt/, app/llms-full.txt/
lib/content.ts       the pages' words (titles, intros, notes) and the page list
lib/registry.ts      each component's examples: their code, shown and copied
lib/demos.tsx        the live examples, in the registry's order
lib/markdown.ts      every page as Markdown, llms.txt and llms-full.txt
lib/skill.ts         the agent skill (scripts/skill.ts writes it to ../skills)
lib/ComponentPage.tsx, PageHead.tsx, Nav.tsx, CodeBlock.tsx, highlight.ts (Shiki, coloured by the code tokens)
```

**Every page is also Markdown**, at its path plus `.md` (`/colors.md`, `/components/menu.md`, `/index.md` for the overview), listed in `/llms.txt` ([llmstxt.org](https://llmstxt.org)) and all in one file at `/llms-full.txt`. Each HTML page links to its Markdown and names it in `<link rel="alternate" type="text/markdown">`. The Markdown is generated from the same data as the HTML, which is what keeps them the same:

- **A change to a page changes its Markdown in the same change.** Words go in `lib/content.ts`, never only in a `.tsx` (`Prose` renders their backticks as code). Anything else a page shows (a table, a section) goes in that page's function in `lib/markdown.ts` too.
- **A new page** goes in `content.ts`'s lists (the nav, `llms.txt` and `llms-full.txt` read them) and gets a function in `markdownPages()`.
- **A new component**: its folder in `packages/ui`, its entry in `lib/registry.ts` (the Markdown shows its examples and lists their imports), its demos in `lib/demos.tsx`, and `app/components/<slug>/page.tsx` (copy another). Component pages are folders of their own, not a `[slug]` route, because a dynamic segment would catch `/components/<slug>.md` before the Markdown route; the build fails if one is missing.

After `npm run docs:build`, every page in `docs/out` (except 404) has a `.md` beside it; check the ones you changed. In `next dev`, a new page's `.md` can 404 until the Markdown route works out its list of pages again; any request to an `.md` (e.g. `/index.md`) does it, and the static build is never affected.

## The agent skill (`skills/labb-design-web`)

An [Agent Skill](https://agentskills.io/specification) that teaches coding agents (Claude Code, labb, others that read Agent Skills) to build web UI with this system: load `tokens.css` and `styles.css` once at the root with `lb-root` on `<body>`; `var(--lb-sys-color-*)` for every colour, picked by meaning, never hex values or the palette; the other tokens for as much else as possible; `@labb/ui` components first; and a checklist (light, dark, phone, keyboard, a search for literal colours).

```
skills/labb-design-web/
  SKILL.md                    the rules, with tables of the main colours and every token scale
  references/tokens.md        every variable and what it's for
  references/components.md    every component's imports and examples (the docs' component Markdown)
```

- **Generated, never edited by hand.** `docs/lib/skill.ts` writes all three from the tokens and the component registry (`npm run skill`, run by `docs:dev` and `docs:build`; `docs/scripts/skill.ts` with `tsx`), so they always list the current variables and components. `npm run typecheck` fails if the folder is stale. Its rules (the prose) are in `skill.ts`; change them there.
- **Keep it true.** When a change affects how something should be built (a new system colour people will reach for, a new component, a changed rule here), check that `SKILL.md` still says the right thing. `skill.ts` fails the build if a colour its table names is gone.
- **Format:** `name` matches the folder (lowercase and hyphens, up to 64), `description` up to 1024 characters and says when to use it, the body well under 500 lines (it's about 100), details in `references/` one level down. `skill.ts` checks the name and the description's length.
- **Exposed** on the docs site at `/skills/labb-design-web/SKILL.md` (and `references/…`), with install steps on the Skill page (`/skill`) and links in `llms.txt`.

## Distribution

What exists now, and how each part is meant to reach its users. Both packages are `"private": true` so nothing can be published by accident; that comes off as part of the first release.

### Today

- The packages export TypeScript source (`"exports"` point at `src/*.ts`). Inside this repository that's all they need: the workspace gives them to `frontend/web/` (Vite compiles them like its own code) and the docs (Next, with `transpilePackages`), by version, with no build and no publishing. Their source has to pass both: the docs' TypeScript 5.9 and `frontend/web/`'s TypeScript 7 with its strict settings (`noUnusedLocals`, `noUnusedParameters`).
- The token build already makes the final CSS and TypeScript (`generated/`), checked in so nothing has to run the build to use them.
- The docs build to `docs/out`. They aren't hosted yet: GitHub Pages can't serve from this private repo on a free plan, so it's a host that builds from a private repo (Cloudflare Pages, Vercel, Netlify: root `design-system`, command `npm run build`, output `docs/out`), a workflow that pushes `docs/out` to a public repo, or a paid GitHub plan.

### The plan, per consumer

**Web (React apps)**: `frontend/web/` in this repository uses the workspace (above). Apps in other repositories need the two packages published to a registry:

- `@labb/tokens`: `tokens.css` (plain CSS, for anything), the typed tokens as compiled JS with type declarations, and the JSON itself (`@labb/tokens/tokens/*`) for design tools. Needs a small step that compiles `src/` and `generated/tokens.ts` to `dist/` and points `exports` at it.
- `@labb/ui`: the components compiled to ESM with type declarations, file by file so each `"use client"` stays at the top of its own module (bundling them into one file would make everything a client component or lose the directive), plus `styles.css` with its imports resolved into one file. `react` and `react-dom` are peer dependencies; `@base-ui/react` and `@labb/tokens` are dependencies.
- An app (`frontend/web/` too) adds `@labb/tokens/tokens.css` (or `tokensCss()`) and `@labb/ui/styles.css` once, puts `class="lb-root"` on `<body>`, and imports components. Moving `frontend/web/` over means replacing its own variables with these (each system colour's `web` field names the old variable) and its classes with the components, page by page.

**iOS (`mobile/ios`)**: the Swift package (see **The Swift package**), which the app adds as a local package by relative path, so it builds wherever the repository is checked out, with nothing to publish. For SwiftUI's sheets, alerts and `Menu`, use the system's own, coloured with the system colours. If an app outside this repository needs the package, a git tag on a repository that holds it makes it a remote Swift package (Swift Package Manager needs `Package.swift` at the top of that repository, so it would be published as its own repository or a mirror).

**Design tools**: the JSON is the format Tokens Studio for Figma reads. If tokens are ever edited in Figma, they come back as a change to this JSON, checked by the same build.

**The docs**: the static export on a host (see above), with `llms.txt` so agents building with the system can read it.

**The agent skill**: today, copied from `skills/labb-design-web` into a project's `.claude/skills/` or `.labb/skills/` (or a user's `~/.claude/skills/`, `~/.labb/skills/`), downloaded from the docs site, or uploaded as a zip of the folder on labb's web app's Skills page (a `SKILL.md` on its own leaves out `references/`). Once `@labb/ui` is published it ships inside it (`skills/` in the package's files), so a project that installs the components has the skill for the same version at `node_modules/@labb/ui/skills/labb-design-web`.

### Versioning

Each package has its own semver version, released together when they change together:

- **Major**: a token or component renamed or removed, a CSS variable or class renamed, a component's props changed incompatibly.
- **Minor**: a new token, palette step, component or prop; a system colour pointed at a different step (it looks different, nothing breaks).
- **Patch**: fixes that don't change how anything looks or is used.

Before the first release, decide where the packages are published (npm under an `@labb` scope, which needs the scope, or GitHub Packages, which keeps them private but needs a token to install) and whether releases are tagged by hand or with a tool like Changesets. Neither is decided yet.

## Conventions

- Comments and docs are short plain prose, like the rest of the repo.
- Colours come from tokens, everywhere but the docs' own swatch backgrounds (the checkerboard under see-through colours). Layout sizes (page widths, breakpoints) are the docs' own and stay literal.
- Check light, dark and a phone width (the docs have no horizontal scroll at 390px) for anything visual.
- Keep `README.md` (for people) and this file (for agents) up to date when how something works changes.
