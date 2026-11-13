# labb design system

labb's colours, tokens and components, for the web (React) and iOS (SwiftUI), taken from the web app (`frontend/web/src/styles.css`), with a Next.js site that shows them.

From `frontend/`, where the npm workspace (this and `web/`) is:

```bash
npm install
npm run docs:dev           # the docs on http://localhost:3200
npm run docs:build         # static HTML in docs/out (PAGES_BASE_PATH=/x to host it under /x)
npm run docs:preview       # serves docs/out on :3200
npm run design:typecheck
```

`frontend/web/` gets `@labb/tokens` and `@labb/ui` from the workspace by version, so changes here reach it straight away, with nothing to publish.

## Layout

- `packages/tokens` (`@labb/tokens`): the tokens as JSON, generated into CSS and TypeScript (see **Tokens** below). `tokensCss()` (or `@labb/tokens/tokens.css`) gives CSS variables named `--lb-<group>-<name>`; the page follows the system theme unless `data-theme="light"`/`"dark"` is set on `<html>`, as in the app, and `data-lb-theme` puts one part of a page in a theme.
- `packages/ui` (`@labb/ui`): one folder per component in `src/components/` (e.g. `menu/` has `Menu.tsx`, `MenuItem.tsx`, `MenuSeparator.tsx` and `menu.css`), exported from `src/index.ts`; `src/styles.css` imports every component's CSS after `base.css`. Classes start with `lb-` and use the tokens. The interactive ones (Button, Toggle, Segmented, Switch, Field, Input, Textarea, Menu, Dialog, Toast) are [Base UI](https://base-ui.com) primitives (`@base-ui/react`) with our classes, styled through Base UI's state attributes (`data-pressed`, `data-checked`, `data-highlighted`, `data-invalid`, `data-starting-style`…); the rest are plain elements. Toasts need a `ToastProvider` around the app.
- `docs`: the Next.js site (App Router), exported as static HTML. `lib/content.ts` holds the pages' words, `lib/registry.ts` each component's examples and their code, and `lib/demos.tsx` draws them, in the same order.

## Tokens

The source is [Design Tokens Format 2025.10](https://www.designtokens.org/tr/2025.10/format/) JSON in `packages/tokens/tokens/`, with colour in two layers:

- **Reference colours** (`ref.json`): the palette. A ramp from 50 to 950 per hue (sand, ink, yellow, amber, orange, red, green, cyan, blue, purple; ink also has a 925), plus white and black. They mean nothing on their own. The colours labb already used are pinned on their steps; the steps between are made in OKLCH. CSS: `--lb-ref-<hue>-<step>`.
- **System colours** (`sys/light.json`, `sys/dark.json`, `sys.color.<group>.<name>`): what a colour is for (`bg`, `text`, `accent`, `error`…), each pointing at a palette step per theme, e.g. `bg` is `{ref.sand.50}` in light and `{ref.ink.950}` in dark. Components use these, never the palette. CSS: `--lb-sys-color-<name>`.
- `base.json`: brand aliases, spacing, radii, type, shadows, motion, borders and layers. `labb.resolver.json` puts it together, with light and dark as the theme modifier's contexts.

```bash
npm run tokens                    # check the JSON and write generated/ (docs:dev and docs:build run it first)
npm run tokens:watch              # the same, on every change
```

The build checks the JSON against the format, follows every reference, makes sure light and dark have the same tokens, and fails if a colour drops below the contrast its token asks for (`$extensions."dev.labb".contrast`). It writes `generated/tokens.css` and `generated/tokens.ts`, and the iOS package's `Tokens.swift` and colour asset catalog; edit the JSON, not those. `npm run typecheck` fails if they're out of date.

## iOS

`swift/` is `LabbDesign`, a Swift package for SwiftUI: the same tokens (colours in an asset catalog, light and dark; the rest in `Tokens.swift`, both generated) and a few styles and views on them (`.labbRoot()`, `.labbText()`, `.buttonStyle(.labb(.primary))`, `.toggleStyle(.labb)`, `.labbCard()`, `LabbBadge`, `.labbField()`). The iOS app in `mobile/ios` adds it as a local package; the docs site's iOS page shows how to use it.

## Agent skill

`skills/labb-design-web` is an [Agent Skill](https://agentskills.io) for building web UI with the system: it tells coding agents to load the variables once at the root, use `--lb-sys-color-*` for every colour (never hex values or the palette) and the other tokens for as much else as possible, and reach for `@labb/ui` first. It's generated from the tokens and the component registry (`npm run skill`), so it's always current. To use it, copy the folder into a project's `.claude/skills/` or `.labb/skills/`; the docs site's Skill page has the other ways.

## Markdown and llms.txt

Every page is also Markdown at its path plus `.md` (`/colors.md`, `/components/menu.md`, and `/index.md` for the overview). `/llms.txt` lists them for agents ([llmstxt.org](https://llmstxt.org)), and `/llms-full.txt` has them all in one file. Each HTML page links to its Markdown and names it in a `<link rel="alternate" type="text/markdown">`.

They're built from the same data as the pages by `docs/lib/markdown.ts`, so when a page changes, change its Markdown with it: words go in `lib/content.ts`, and anything else a page shows goes in its function in `markdown.ts`. Links in them start from the site's root; set `SITE_URL` (e.g. `https://example.github.io`) when building to make them absolute.

To add a component: start from a Base UI primitive if there's one for it. Give it a folder in `packages/ui/src/components/` with its `.tsx` (`"use client"` if it uses Base UI or hooks) and its `.css`, export it from `src/index.ts`, import its CSS in `src/styles.css`, then add an entry in `docs/lib/registry.ts` and its demos in `docs/lib/demos.tsx`, and `docs/app/components/<slug>/page.tsx` (copy another; the build fails without it).

## Not yet

How it all fits together, the rules the builds depend on, and how it's meant to reach the web and iOS apps is in [AGENTS.md](AGENTS.md).


The packages are private and export TypeScript source, which `frontend/web/` and the docs compile through the workspace. Publishing them, for apps in other repositories, needs a build of `@labb/ui` (JS and type declarations) and a registry to publish to; the tokens already build to CSS, TypeScript and Swift. The web app still has its own copy of these values; each colour token notes its name there (`web`).
