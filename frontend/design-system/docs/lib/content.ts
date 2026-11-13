// The docs' words, shared by the pages and their Markdown versions (markdown.ts), so the two say
// the same thing. `code` in backticks becomes <Code> on a page (Prose) and stays as is in Markdown.
import { colors } from "@labb/tokens";
import { components } from "./registry";

export const site = {
  name: "labb design system",
  summary:
    "labb's colours, tokens and React components: black and hazard yellow on warm paper, small square corners, hard offset shadows. Tokens are CSS variables named `--lb-<group>-<name>`; components are React components on Base UI with `lb-` classes.",
};

export type Page = { path: string; title: string; lede: string };

export const overview: Page = {
  path: "/",
  title: "labb design",
  lede: "The colours, tokens and React components behind labb's web chat, taken from `frontend/web/src/styles.css` so other front ends can look like it.",
};

export const foundations: Page[] = [
  {
    path: "/colors",
    title: "Colours",
    lede: "System colours: what each colour is for, as `--lb-sys-color-*`. Each points at a palette step in light and in dark; the page follows the system unless a theme is picked, and a part of a page can be put in one with `data-lb-theme`. Contrast is checked against the colour each one is read on.",
  },
  {
    path: "/palette",
    title: "Palette",
    lede: "Reference colours: a ramp from 50 to 950 for each hue, with no meaning attached. System colours point at these steps; components use the system colours, never the palette directly.",
  },
  {
    path: "/typography",
    title: "Typography",
    lede: "The system's own sans for everything read, and its mono for code, counts and labels. Headings are black weight with tight tracking; labels over sections are spaced mono capitals.",
  },
  {
    path: "/spacing",
    title: "Spacing and radii",
    lede: "Everything sits on a 2px grid; most gaps are 4, 6, 8, 10 or 12px. Corners are small: 4px on controls, 6px on cards.",
  },
  {
    path: "/effects",
    title: "Shadows and motion",
    lede: "Shadows are solid and offset down and right, never blurred, except a soft one for a picked segment. Motion is quick and small, and goes away for people who ask for less.",
  },
];

// Pages about using the system rather than what's in it
export const resources: Page[] = [
  {
    path: "/ios",
    title: "iOS (SwiftUI)",
    lede: "The same tokens for SwiftUI: LabbDesign, a Swift package with the colours in an asset catalog (light and dark), the rest as Swift, and a few styles and views on them. Generated from the same JSON as the CSS.",
  },
  {
    path: "/skill",
    title: "Agent skill",
    lede: "An Agent Skill that teaches coding agents to build web UI with this system: load the variables once, globally, use system colours for every colour and tokens for as much else as possible, and use the components first.",
  },
];

export const componentsPage: Page = {
  path: "/components",
  title: "React components",
  lede: "What the web chat is made of, as React components from `@labb/ui`: Base UI underneath where they're interactive, styled with `lb-` classes on the tokens.",
};

export const page = (path: string) => [overview, ...foundations, ...resources, componentsPage].find((p) => p.path === path)!;

// Every page, in the order the nav and llms.txt list them
export function allPages(): Page[] {
  return [
    overview,
    ...foundations,
    componentsPage,
    ...components.map((c) => ({ path: `/components/${c.slug}`, title: c.name, lede: c.about })),
    ...resources,
  ];
}

// Where a page's Markdown version is: /index.md for the overview, else the page's path plus .md
export const markdownPath = (path: string) => (path === "/" ? "/index.md" : `${path}.md`);

// The overview
export const principles = [
  { title: "Black and hazard yellow", body: "Warm paper in light, near-black in dark. Yellow is a fill, never text on light." },
  { title: "Hard shadows", body: "Popovers, dialogs and toasts sit on solid offset shadows; yellow buttons lift and press." },
  { title: "Small and square", body: "4px corners on controls, 6px on cards. Hairline borders, heavier where it matters." },
  { title: "Heavy headings, mono labels", body: "Headings are black weight and tight; labels over sections are spaced mono caps." },
];

export const usage = {
  intro:
    "The packages live in `frontend/design-system/packages` and aren't published yet. Tokens are CSS variables named `--lb-<group>-<name>`; the React components (`@labb/ui`) are styled with classes starting with `lb-`.",
  tsx: `import { tokensCss } from "@labb/tokens/css"; // the variables, light and dark
import "@labb/ui/styles.css"; // the components' classes
import { Button } from "@labb/ui";

<style>{tokensCss()}</style>
<Button variant="primary">New chat</Button>`,
  css: `/* Your own components, on the same tokens */
.panel {
  padding: var(--lb-space-6) var(--lb-space-7);
  background: var(--lb-sys-color-surface);
  border: 1.5px solid var(--lb-sys-color-text);
  border-radius: var(--lb-radius-lg);
  box-shadow: var(--lb-shadow-popover);
}`,
};

// Colours: the colour each one is read against, for its contrast (set in the tokens' JSON)
// A palette step's CSS variable: "sand.50" is var(--lb-ref-sand-50)
export const refVar = (step: string) => `var(--lb-ref-${step.replace(/\./g, "-")})`;

export const contrastBase = (name: string): string | undefined => colorToken(name).contrast?.on;

export const colorToken = (name: string) => colors.flatMap((g) => g.tokens).find((t) => t.name === name)!;

// The palette
export const paletteNotes = {
  layers:
    "Tokens come in two layers. Reference colours (`--lb-ref-<hue>-<step>`) are the palette. System colours (`--lb-sys-color-<name>`) say what a colour is for and point at a step in each theme, so a theme or a brand change is a change of pointers, not of every component.",
  pinned:
    "Each ramp's hue and chroma come from the colours labb already used: those are pinned on their steps (marked ●) and the steps between are made in OKLCH, so lightness steps look even. Ink has a 925 between 900 and 950 for dark mode's layers.",
  source: "The source is Design Tokens JSON in `packages/tokens/tokens/`: `ref.json` (this palette), `sys/light.json` and `sys/dark.json` (system colours), `base.json` (the rest).",
};

// The iOS page: how to use the Swift package, with examples that build (mobile/ios uses them)
export const ios = {
  where:
    "The package is `frontend/design-system/swift` (`LabbDesign`). The token build writes its `Generated/Tokens.swift` and `Resources/LabbColors.xcassets` from the token JSON (`npm run tokens` in `frontend/`), so the colours are the CSS's, to the bit; the styles and views next to them are written by hand. Never edit the generated ones: `npm run typecheck` fails if they're stale.",
  differs:
    "On purpose different from the web: controls are at least 44pt tall, type scales with Dynamic Type from the token sizes (each size follows the Apple text style nearest to it), there's no hover, and motion stops under Reduce Motion.",
  sections: [
    {
      title: "Add the package",
      about: "In Xcode: File → Add Package Dependencies → Add Local, and pick `frontend/design-system/swift`; the iOS app in `mobile/ios` already has it. In another package's manifest:",
      code: `.package(path: "<path to the repository>/frontend/design-system/swift"),
// and in the target
.product(name: "LabbDesign", package: "LabbDesign"),`,
    },
    {
      title: "Set up the root",
      about: "Once, on the root view: labb's background edge to edge, the text colour, the accent and body type.",
      code: `import LabbDesign
import SwiftUI

struct ContentView: View {
    var body: some View {
        NavigationStack {
            ScrollView { /* … */ }
                .labbRoot()
        }
    }
}`,
    },
    {
      title: "Colours",
      about: "System colours by meaning, the same names as the CSS (`--lb-sys-color-accent-fill` is `.labb.accentFill`). They come from the asset catalog, so dark mode follows the system by itself. Never a literal colour, and never the palette (`Color.labbPalette`) in views.",
      code: `Text("Hint").foregroundStyle(.labb.muted)
RoundedRectangle(cornerRadius: LabbRadius.lg).fill(Color.labb.surface)
Rectangle().fill(.labb.accentFill)
Color.labbBrand.yellow   // the same in both appearances: the hazard stripes, the logo`,
    },
    {
      title: "Type",
      about: "The token sizes, scaling with Dynamic Type.",
      code: `Text("labb").labbHeading(.xxxl)          // black weight, tight tracking
Text("Body").labbText(.base)
Text("Recent").labbLabel()               // mono, uppercase, spaced, muted
Text("Trip").labbText(.md, weight: LabbWeight.semibold)`,
    },
    {
      title: "Spacing, corners, shadows and motion",
      about: "Numbers in points, the same scales as the web.",
      code: `VStack(spacing: LabbSpace.s6) { … }
    .padding(LabbSpace.s8)
    .background(Color.labb.surface, in: RoundedRectangle(cornerRadius: LabbRadius.lg))
    .labbShadow(.popover)                // solid and offset, never blurred

@Environment(\.accessibilityReduceMotion) var reduceMotion
withAnimation(reduceMotion ? nil : LabbMotion.base) { open.toggle() }`,
    },
    {
      title: "Components",
      about: "Styles for SwiftUI's own controls, so they keep their accessibility, and a few views.",
      code: `Button("New chat") { … }.buttonStyle(.labb(.primary))   // yellow, presses onto its shadow
Button("Cancel") { … }.buttonStyle(.labb())
Button("Delete") { … }.buttonStyle(.labb(.danger))
Toggle("Memories", isOn: $on).toggleStyle(.labb)
TextField("Your name", text: $name).labbField()
VStack { … }.labbCard(lifted: true)
LabbBadge("3", tone: .accent)`,
    },
  ],
};

// The agent skill page
export const skillNotes = {
  what: "Coding agents (Claude Code, labb, and others that read Agent Skills) load a skill's short description up front and its instructions only when a task needs them. This one is for building or styling web pages and React components with the design system.",
  teaches: [
    "Load `@labb/tokens/tokens.css` (and `@labb/ui/styles.css`) once at the app's root, put `lb-root` on `<body>`, and switch themes with `data-theme`, never with colour overrides of your own.",
    "Use `var(--lb-sys-color-*)` for every colour, picked by meaning; no hex values, no palette (`--lb-ref-*`) in pages or components, `color-mix()` for tints.",
    "Use the spacing, radius, type, shadow and motion variables for as much else as possible.",
    "Use `@labb/ui`'s React components first, built on Base UI, and make anything new look like labb.",
    "Check light, dark, a phone width and the keyboard before calling it done.",
  ],
  generated: "It's generated from the same tokens and component registry as these docs (`docs/lib/skill.ts`), so its tables of variables and components are always the current ones. In the repo it's `frontend/design-system/skills/labb-design-web/`; `npm run skill` rewrites it and `npm run typecheck` fails if it's stale.",
};

export const hazard = "Yellow and black at 45°, 9px each: along the top of what needs your approval, and nowhere else.";

// Typography
export const sizesNote = "Body text is 15px. Controls and the sidebar are 13px, hints 12px, and mono labels 11px.";

// Spacing
export const spaceUse: Record<string, string> = {
  "1": "Gaps between rows in menus and lists",
  "2": "Badge and chip insides, the smallest gaps",
  "3": "Button padding, gaps between an icon and its label",
  "4": "Gaps in rows of controls",
  "5": "Input padding, tight panels",
  "6": "Button sides, gaps between cards",
  "7": "Card and message sides",
  "8": "Dialog bodies, page gutters on phones",
  "10": "The chat log's top",
  "12": "Between sections",
  "16": "Large breaks",
};

export const bordersNote =
  "Hairlines in border between things; 1.5px in text for popovers, cards and chips that need weight; 2px for the message box and approvals.";

// Shadows and motion
export const motionNote = "Hover and press the button: it lifts on press-hover and presses down to nothing.";
