// The design system as an Agent Skill (https://agentskills.io) for building on the web: SKILL.md (the
// rules) and references/ (every variable, every component), built from the same tokens and registry
// as the docs. scripts/skill.ts writes it to frontend/design-system/skills/; app/skills/ serves it on the site.
import { border, brand, colors, font, motion, palette, radius, shadow, space, z } from "@labb/tokens";
import { refVar } from "./content";
import { componentMd, url } from "./markdown";
import { components } from "./registry";

export const SKILL_NAME = "labb-design-web";

const table = (head: string[], rows: string[][]) =>
  [`| ${head.join(" | ")} |`, `| ${head.map(() => "---").join(" | ")} |`, ...rows.map((r) => `| ${r.map((c) => c.replace(/\|/g, "\\|")).join(" | ")} |`)].join("\n");

const sys = (name: string) => `var(--lb-sys-color-${name})`;
const allColors = colors.flatMap((g) => g.tokens);
const has = (name: string) => allColors.some((t) => t.name === name);

// The colours an agent reaches for most, by what it's making; checked against the tokens below
const PICK: [string, string][] = [
  ["The page", "bg"],
  ["A card, panel, sidebar, dialog or menu", "surface"],
  ["A hovered or selected row", "active"],
  ["Lines between things, input borders", "border"],
  ["Body text, strong outlines", "text"],
  ["Labels, hints, secondary text", "muted"],
  ["Links, focus rings, selected outlines (accent-coloured lines and text)", "accent"],
  ["The primary button, a switch that's on, the yellow bar (a fill)", "accent-fill"],
  ["Text and icons on accent-fill", "on-accent"],
  ["Errors, destructive actions", "error"],
  ["The user's own messages, toasts (inverted)", "user-bg"],
  ["Text on user-bg", "user-text"],
  ["Behind a dialog", "scrim"],
  ["Code blocks, inline code", "code-bg"],
  ["Chart series, in order: series-1 to series-4, then series-other for the rest", "series-1"],
];
for (const [, name] of PICK) if (!has(name)) throw new Error(`skill.ts: ${name} isn't a system colour any more`);

// The format's limits (https://agentskills.io/specification)
if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(SKILL_NAME) || SKILL_NAME.length > 64) throw new Error("skill.ts: the name breaks the format's rules");

function skillMd() {
  const description =
    "Build and style web pages and React components with labb's design system: its CSS variables (--lb-sys-color-*, --lb-space-*, --lb-radius-*, --lb-text-*, --lb-shadow-*…) loaded once globally, and its @labb/ui components on Base UI. Use when building or changing a labb website, web app or UI, when a project depends on @labb/tokens or @labb/ui, or when asked to make something look like labb (black and hazard yellow, hard shadows).";
  if (description.length > 1024) throw new Error(`skill.ts: the description is ${description.length} characters; the format allows 1024`);
  return `---
name: ${SKILL_NAME}
description: ${JSON.stringify(description)}
metadata:
  source: ${JSON.stringify(`frontend/design-system/skills/${SKILL_NAME}, generated from the tokens and the component registry`)}
---

# Building on the web with labb's design system

labb's look is a set of CSS variables (the tokens) and React components that use them. Your job when building UI is to **load the variables once, globally, and then use them for every colour and as many other values as possible**, so the page matches labb, follows light and dark mode by itself, and changes when the design system changes.

## 1. Set it up once, at the root

Do this in the app's root (the root layout, \`index.html\`, \`_app\`, the global stylesheet), never per page or per component:

\`\`\`tsx
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
\`\`\`

- Without a bundler, link \`tokens.css\` (and \`styles.css\`) in \`<head>\`, or put \`tokensCss()\` from \`@labb/tokens/css\` in a \`<style>\` there.
- **Theme:** the variables follow the system's light or dark setting. To force one, set \`data-theme="light"\` or \`"dark"\` on \`<html>\`; to put one section in a theme, \`data-lb-theme="light"\` or \`"dark"\` on it. **Never** write your own \`@media (prefers-color-scheme: dark)\` colour overrides: the variables already switch.
- **Never** copy token values into your own \`:root\` or redefine an \`--lb-*\` variable. If the values look wrong, the design system is what changes.
- \`ToastProvider\` only if you show toasts (\`useToast()\`).

## 2. Every colour is a system colour

Use \`var(--lb-sys-color-<name>)\` for every colour: backgrounds, text, borders, outlines, shadows, SVG fills and strokes, chart series. Pick by meaning, not by how it looks:

${table(
  ["You're colouring", "Use"],
  PICK.map(([what, name]) => [what, `\`${sys(name)}\``]),
)}

All ${allColors.length} are in [references/tokens.md](references/tokens.md), with what each is for.

- **No literal colours.** No \`#hex\`, \`rgb()\`, \`hsl()\`, named colours (\`white\`, \`black\`) or Tailwind/other palette classes for colour. \`transparent\` and \`currentColor\` are fine.
- **Not the palette.** \`--lb-ref-<hue>-<step>\` (e.g. \`${refVar(palette[0].name + ".500")}\`) is the reference layer that system colours are made from. Components and pages don't use it: a system colour says what a colour is for, and changes with the theme; a palette step doesn't.
- **A tint or a see-through version** is \`color-mix()\` on a system colour: \`color-mix(in srgb, ${sys("accent-fill")} 10%, transparent)\`.
- **Colours that belong together:** text on \`bg\` or \`surface\` is \`text\` or \`muted\`; on \`accent-fill\` it's \`on-accent\`; on \`user-bg\` it's \`user-text\`. Don't put \`accent-fill\` (yellow) text on a light background: it's a fill, and \`accent\` is the colour for accent text and lines.
- **Brand colours that never change with the theme** (only for the hazard stripes and the logo): \`var(--lb-brand-yellow)\`, \`var(--lb-brand-black)\`, \`var(--lb-brand-paper)\`.
- **If no system colour fits**, use the closest by meaning and say so. Don't invent a hex. If you work on the design system itself, a new colour is a new system colour (pointing at a palette step) in \`frontend/design-system/packages/tokens/tokens/sys/\`, not a value in your CSS.

## 3. Everything else from the tokens too

${table(
  ["For", "Use", "Values"],
  [
    ["Padding, margins, gaps", "`var(--lb-space-<n>)`", Object.entries(space).filter(([k]) => k !== "0").map(([k, v]) => `${k}=${v}px`).join(", ")],
    ["Corners", "`var(--lb-radius-<name>)`", Object.entries(radius).map(([k, v]) => `${k}=${v.value}`).join(", ")],
    ["Font size and line height", "`var(--lb-text-<size>)`, `var(--lb-leading-<size>)`", Object.entries(font.size).map(([k, v]) => `${k}=${v.size}px`).join(", ")],
    ["Font weight", "`var(--lb-weight-<name>)`", Object.entries(font.weight).map(([k, v]) => `${k}=${v}`).join(", ")],
    ["Font family", "`var(--lb-font-sans)`, `var(--lb-font-mono)`", "system sans; mono for code, counts and labels"],
    ["Letter spacing", "`var(--lb-tracking-<name>)`", Object.entries(font.tracking).map(([k, v]) => `${k}=${v}`).join(", ")],
    ["Shadows", "`var(--lb-shadow-<name>)`", Object.keys(shadow).join(", ")],
    ["Transitions", "`var(--lb-motion-<name>)`", Object.entries(motion).map(([k, v]) => `${k}=${v.value}`).join(", ")],
    ["Border widths", "`var(--lb-border-<name>)`", Object.entries(border).map(([k, v]) => `${k}=${v}`).join(", ")],
    ["Stacking", "`var(--lb-z-<name>)`", Object.entries(z).map(([k, v]) => `${k}=${v}`).join(", ")],
  ],
)}

Use the nearest step of a scale rather than a value between steps. Literal values are fine only for what no token covers: one component's own dimensions (an icon's 16px, a switch's track) and layout (page widths, breakpoints, grid columns).

## 4. React components before hand-made ones

Use \`@labb/ui\`'s React components when it has the thing: ${components.map((c) => c.name).join("; ")}. They're [Base UI](https://base-ui.com) primitives underneath (keyboard, focus and ARIA done) with labb's classes. Imports, props and examples: [references/components.md](references/components.md).

- Style a component's states through Base UI's data attributes (\`[data-pressed]\`, \`[data-checked]\`, \`[data-highlighted]\`, \`[data-invalid]\`, \`[data-disabled]\`, \`[data-starting-style]\`/\`[data-ending-style]\`), not your own state classes.
- For something new that's interactive (a tooltip, a popover, a select, tabs), build it on the Base UI primitive and style it with the variables, like the others.

When you make your own UI, make it look like labb:

- Hairline borders (\`var(--lb-border-thin) solid ${sys("border")}\`); heavier (\`--lb-border-strong\` in \`${sys("text")}\`) for popovers and cards that need weight.
- Small corners: \`--lb-radius-md\` (4px) on controls, \`--lb-radius-lg\` (6px) on cards and dialogs.
- Hard offset shadows, never blurred: \`--lb-shadow-popover\` under menus, popovers and dialogs; \`--lb-shadow-lift\` (yellow) under a card on hover.
- One yellow primary action per view (\`<Button variant="primary">\`): it lifts on hover and presses down when clicked.
- Headings: \`--lb-weight-black\` with \`--lb-tracking-heading\`. Labels over sections: the \`lb-label\` class (mono, uppercase, spaced).
- Motion with the motion tokens, and none under \`prefers-reduced-motion: reduce\`.

## 5. Before you're done

- Search the CSS and styles you wrote for \`#\`, \`rgb(\`, \`hsl(\` and \`--lb-ref-\`: each one is a colour that should be a system colour.
- Look at it in light and dark (\`data-theme\` on \`<html>\`), and at a phone width with no sideways scroll.
- Tab through it: every control shows a focus ring and can be used from the keyboard.
- Text keeps its pair (\`text\`/\`muted\` on \`bg\`/\`surface\`, \`on-accent\` on \`accent-fill\`), so contrast holds in both themes.
`;
}

function tokensMd() {
  const groups = colors.map((g) =>
    [
      `### ${g.name}`,
      g.about,
      table(
        ["Variable", "For", "Light", "Dark", "Read on"],
        g.tokens.map((t) => [
          `\`${sys(t.name)}\``,
          t.use,
          `${t.light}${t.lightRef ? ` (${t.lightRef})` : ""}`,
          `${t.dark}${t.darkRef ? ` (${t.darkRef})` : ""}`,
          t.contrast ? `${t.contrast.on}${t.contrast.min ? `, at least ${t.contrast.min}:1` : ""}` : "",
        ]),
      ),
    ].join("\n\n"),
  );
  return `# labb's tokens

Every CSS variable, from \`@labb/tokens/tokens.css\` (load it once, at the root). Use system colours (\`--lb-sys-color-*\`) for every colour; the values in brackets are the palette steps they point at, for reference only.

## System colours

${groups.join("\n\n")}

## Brand colours (the same in both themes)

${table(
  ["Variable", "Value"],
  Object.entries(brand).map(([k, v]) => [`\`var(--lb-brand-${k})\``, v]),
)}

## Spacing

${table(
  ["Variable", "Value"],
  Object.entries(space).map(([k, v]) => [`\`var(--lb-space-${k})\``, `${v}px`]),
)}

## Corners

${table(
  ["Variable", "Value", "For"],
  Object.entries(radius).map(([k, v]) => [`\`var(--lb-radius-${k})\``, v.value, v.use]),
)}

## Type

${table(
  ["Variable", "Size", "Line height", "For"],
  Object.entries(font.size).map(([k, v]) => [`\`var(--lb-text-${k})\``, `${v.size}px`, `\`var(--lb-leading-${k})\` (${v.line})`, v.use]),
)}

${table(
  ["Variable", "Value"],
  [
    ...Object.entries(font.family).map(([k, v]) => [`\`var(--lb-font-${k})\``, v]),
    ...Object.entries(font.weight).map(([k, v]) => [`\`var(--lb-weight-${k})\``, String(v)]),
    ...Object.entries(font.tracking).map(([k, v]) => [`\`var(--lb-tracking-${k})\``, v]),
  ],
)}

## Shadows

${table(
  ["Variable", "Value", "For"],
  Object.entries(shadow).map(([k, v]) => [`\`var(--lb-shadow-${k})\``, `\`${v.value}\``, v.use]),
)}

## Motion

${table(
  ["Variable", "Value", "For"],
  Object.entries(motion).map(([k, v]) => [`\`var(--lb-motion-${k})\``, `\`${v.value}\``, v.use]),
)}

## Borders and layers

${table(
  ["Variable", "Value"],
  [...Object.entries(border).map(([k, v]) => [`\`var(--lb-border-${k})\``, v]), ...Object.entries(z).map(([k, v]) => [`\`var(--lb-z-${k})\``, String(v)])],
)}

## The palette (not for components)

\`--lb-ref-<hue>-<step>\` is the reference layer: ${palette.map((r) => `${r.name} (${r.steps.map((s) => s.step).join(", ")})`).join("; ")}; plus \`--lb-ref-white\` and \`--lb-ref-black\`. System colours point at these steps. Don't use them in pages or components.
`;
}

function componentsMd() {
  return `# labb's React components

From \`@labb/ui\`, with \`@labb/ui/styles.css\` and \`@labb/tokens/tokens.css\` loaded once at the root, and \`<body className="lb-root">\`. Toasts also need a \`ToastProvider\` around the app.

${components.map((c) => componentMd(c).replace(/^# /, "## ").replace(/\n## /g, "\n### ")).join("\n\n---\n\n")}
`;
}

// Every file of the skill, by its path in the skill's folder
export function skillFiles(): Record<string, string> {
  return {
    "SKILL.md": skillMd(),
    "references/tokens.md": tokensMd(),
    "references/components.md": componentsMd(),
  };
}

// Where the skill's files are on the site, and how to install it, for the Skill page and its Markdown
export function skillUrl(file = "SKILL.md") {
  return url(`/skills/${SKILL_NAME}/${file}`);
}

export function installSteps(): { title: string; about: string; code: string; lang: "bash" }[] {
  const site = process.env.SITE_URL ? "" : "https://<docs-site>";
  const fetchAll = (into: string) =>
    [
      `mkdir -p ${into}/references`,
      ...Object.keys(skillFiles()).map((f) => `curl -fsSL ${site}${skillUrl(f)} -o ${into}/${f}`),
    ].join("\n");
  return [
    {
      title: "From this repository",
      about: "Copy the folder into the project you're building, where your agent looks for skills.",
      code: `# Claude Code, for one project (or ~/.claude/skills/ for all of them)
cp -R frontend/design-system/skills/${SKILL_NAME} <project>/.claude/skills/

# labb in the terminal, for one project (or ~/.labb/skills/ for all of them)
cp -R frontend/design-system/skills/${SKILL_NAME} <project>/.labb/skills/`,
      lang: "bash",
    },
    {
      title: "From this site",
      about: "The same files, from these docs.",
      code: fetchAll(`.claude/skills/${SKILL_NAME}`),
      lang: "bash",
    },
    {
      title: "In labb's web app",
      about: "On the Skills page, Upload the folder as a zip (a SKILL.md on its own leaves out references/), so every chat, and the agents you give it to, can use it.",
      code: `cd frontend/design-system/skills
zip -r ${SKILL_NAME}.zip ${SKILL_NAME}
# then Skills → Upload → ${SKILL_NAME}.zip`,
      lang: "bash",
    },
  ];
}
