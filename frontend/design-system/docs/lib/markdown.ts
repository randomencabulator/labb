// Every docs page as Markdown, built from the same tokens, registry and words (content.ts) as the
// pages themselves. Served at the page's path plus .md (app/[...path]/route.ts), and listed in
// llms.txt. When a page changes, its Markdown here changes with it.
import { border, colors, contrast, font, motion, palette, radius, refColors, shadow, space, z } from "@labb/tokens";
import {
  allPages,
  bordersNote,
  colorToken,
  componentsPage,
  contrastBase,
  foundations,
  hazard,
  markdownPath,
  motionNote,
  overview,
  page,
  paletteNotes,
  refVar,
  principles,
  site,
  skillNotes,
  ios,
  resources,
  sizesNote,
  spaceUse,
  usage,
} from "./content";
import { components, type ComponentDoc } from "./registry";
import { installSteps, SKILL_NAME, skillFiles, skillUrl } from "./skill";
import * as ui from "@labb/ui";

// Links in the Markdown: absolute with SITE_URL (e.g. https://example.github.io), else from the root
const basePath = process.env.PAGES_BASE_PATH ?? "";
export const url = (path: string) => `${process.env.SITE_URL ?? ""}${basePath}${path}`;

const table = (head: string[], rows: string[][]) =>
  [`| ${head.join(" | ")} |`, `| ${head.map(() => "---").join(" | ")} |`, ...rows.map((r) => `| ${r.map((c) => c.replace(/\|/g, "\\|")).join(" | ")} |`)].join("\n");

const fence = (code: string, lang: string) => "```" + lang + "\n" + code + "\n```";

const head = (title: string, lede: string) => `# ${title}\n\n${lede}`;

function ratio(name: string, theme: "light" | "dark") {
  const base = contrastBase(name);
  if (!base) return "";
  const r = contrast(colorToken(name)[theme], colorToken(base)[theme]);
  return r == null ? "" : `${r.toFixed(1)}:1 on ${base}`;
}

function overviewMd() {
  return [
    head(overview.title, overview.lede),
    "## Principles",
    principles.map((p) => `- **${p.title}.** ${p.body}`).join("\n"),
    "## Pages",
    allPages()
      .slice(1)
      .map((p) => `- [${p.title}](${url(markdownPath(p.path))}): ${p.lede}`)
      .join("\n"),
    "## Using it",
    usage.intro,
    fence(usage.tsx, "tsx"),
    fence(usage.css, "css"),
  ].join("\n\n");
}

function colorsMd() {
  const p = page("/colors");
  return [
    head(p.title, p.lede),
    ...colors.flatMap((g) => [
      `## ${g.name}`,
      g.about,
      table(
        ["Token", "Variable", "Light", "Dark", "Use", "Web app"],
        g.tokens.map((t) => [
          t.name,
          `\`var(--lb-sys-color-${t.name})\``,
          [`\`${t.light}\``, t.lightRef && `→ \`${refVar(t.lightRef)}\``, ratio(t.name, "light")].filter(Boolean).join(" · "),
          [`\`${t.dark}\``, t.darkRef && `→ \`${refVar(t.darkRef)}\``, ratio(t.name, "dark")].filter(Boolean).join(" · "),
          t.use,
          t.web ? `\`${t.web}\`` : "",
        ]),
      ),
    ]),
    "## Hazard stripes",
    hazard,
    "Class: `lb-hazard`.",
  ].join("\n\n");
}

function paletteMd() {
  const p = page("/palette");
  return [
    head(p.title, p.lede),
    paletteNotes.layers,
    paletteNotes.pinned,
    ...palette.flatMap((r) => [
      `## ${r.title}`,
      r.about,
      table(
        ["Step", "Variable", "Value", "Pinned"],
        r.steps.map((st) => [st.step, `\`var(--lb-ref-${r.name}-${st.step})\``, `\`${st.value}\``, st.pinned ? "yes" : ""]),
      ),
    ]),
    "## White and black",
    table(
      ["Name", "Variable", "Value"],
      refColors.map((c) => [c.name, `\`var(--lb-ref-${c.name})\``, `\`${c.value}\``]),
    ),
    "## Where they come from",
    paletteNotes.source,
  ].join("\n\n");
}

function typographyMd() {
  const p = page("/typography");
  return [
    head(p.title, p.lede),
    "## Families",
    table(
      ["Family", "Variable", "Stack"],
      Object.entries(font.family).map(([name, stack]) => [name, `\`var(--lb-font-${name})\``, `\`${stack}\``]),
    ),
    "## Sizes",
    sizesNote,
    table(
      ["Token", "Size", "Line height", "Use"],
      Object.entries(font.size).map(([name, s]) => [`\`var(--lb-text-${name})\``, `${s.size}px`, `${s.line} (\`var(--lb-leading-${name})\`)`, s.use]),
    ),
    "## Weights",
    table(
      ["Token", "Weight"],
      Object.entries(font.weight).map(([name, w]) => [`\`var(--lb-weight-${name})\``, String(w)]),
    ),
    "## Styles",
    [
      `- **Headings:** weight black, tracking \`${font.tracking.heading}\` (\`var(--lb-tracking-heading)\`).`,
      `- **Mono labels over sections:** 2xs mono, bold, uppercase, tracking \`${font.tracking.label}\` (class \`lb-label\`).`,
      `- **The wordmark:** weight black, tracking \`${font.tracking.wordmark}\`.`,
      "- **Inline code:** mono at 0.9em on code-bg (class `lb-code`).",
      "- **Links:** in the accent with a highlighter under them on light (class `lb-link`).",
    ].join("\n"),
  ].join("\n\n");
}

function spacingMd() {
  const p = page("/spacing");
  return [
    head(p.title, p.lede),
    "## Spacing",
    table(
      ["Token", "Value", "Use"],
      Object.entries(space)
        .filter(([k]) => k !== "0")
        .map(([name, px]) => [`\`var(--lb-space-${name})\``, `${px}px`, spaceUse[name] ?? ""]),
    ),
    "## Radii",
    table(
      ["Token", "Value", "Use"],
      Object.entries(radius).map(([name, r]) => [`\`var(--lb-radius-${name})\``, r.value, r.use]),
    ),
    "## Borders",
    bordersNote,
    table(
      ["Token", "Width"],
      Object.entries(border).map(([name, w]) => [`\`var(--lb-border-${name})\``, w]),
    ),
  ].join("\n\n");
}

function effectsMd() {
  const p = page("/effects");
  return [
    head(p.title, p.lede),
    "## Shadows",
    table(
      ["Token", "Value", "Use"],
      Object.entries(shadow).map(([name, s]) => [`\`var(--lb-shadow-${name})\``, `\`${s.value}\``, s.use]),
    ),
    "## Motion",
    motionNote,
    table(
      ["Token", "Value", "Use"],
      Object.entries(motion).map(([name, m]) => [`\`var(--lb-motion-${name})\``, `\`${m.value}\``, m.use]),
    ),
    "## Layers",
    table(
      ["Token", "z-index"],
      Object.entries(z).map(([name, v]) => [`\`var(--lb-z-${name})\``, String(v)]),
    ),
  ].join("\n\n");
}

function componentsIndexMd() {
  return [
    head(componentsPage.title, componentsPage.lede),
    'Import them from `@labb/ui`, with `@labb/ui/styles.css` and the tokens on the page.',
    components.map((c) => `- [${c.name}](${url(markdownPath(`/components/${c.slug}`))}): ${c.about}`).join("\n"),
  ].join("\n\n");
}

// What a component's examples use from @labb/ui: the components they render and the hooks they call
export function importsOf(doc: ComponentDoc) {
  const code = doc.examples.map((ex) => ex.code).join("\n");
  const names = new Set([...code.matchAll(/<([A-Z]\w*)/g), ...code.matchAll(/\b(use[A-Z]\w*)\(/g)].map((m) => m[1]));
  // Only what @labb/ui has: examples also render the reader's own components, like <App />
  return [...names].filter((n) => n in ui).sort();
}

export function componentMd(doc: ComponentDoc) {
  return [
    head(doc.name, doc.about),
    [
      `- In the web app: \`${doc.web}\``,
      doc.base ? `- Built on Base UI's [${doc.base.name}](${doc.base.url})` : "- Plain markup; no Base UI primitive",
      `- React, from \`@labb/ui\`: \`import { ${importsOf(doc).join(", ")} } from "@labb/ui";\``,
    ].join("\n"),
    ...doc.examples.flatMap((ex) => [`## ${ex.title}`, ...(ex.about ? [ex.about] : []), fence(ex.code, "tsx")]),
  ].join("\n\n");
}

function iosMd() {
  const p = page("/ios");
  return [
    head(p.title, p.lede),
    ios.where,
    ios.differs,
    ...ios.sections.flatMap((sec) => [`## ${sec.title}`, sec.about, fence(sec.code, "swift")]),
  ].join("\n\n");
}

function skillMd() {
  const p = page("/skill");
  return [
    head(p.title, p.lede),
    skillNotes.what,
    "## What it teaches",
    skillNotes.teaches.map((t) => `- ${t}`).join("\n"),
    "## Its files",
    Object.keys(skillFiles()).map((f) => `- [${SKILL_NAME}/${f}](${skillUrl(f)})`).join("\n"),
    skillNotes.generated,
    ...installSteps().flatMap((st) => [`## ${st.title}`, st.about, fence(st.code, st.lang)]),
  ].join("\n\n");
}

// Every page's Markdown, by its .md path (without the base path)
export function markdownPages(): Record<string, string> {
  const pages: Record<string, string> = {
    [markdownPath(overview.path)]: overviewMd(),
    [markdownPath("/colors")]: colorsMd(),
    [markdownPath("/palette")]: paletteMd(),
    [markdownPath("/skill")]: skillMd(),
    [markdownPath("/ios")]: iosMd(),
    [markdownPath("/typography")]: typographyMd(),
    [markdownPath("/spacing")]: spacingMd(),
    [markdownPath("/effects")]: effectsMd(),
    [markdownPath(componentsPage.path)]: componentsIndexMd(),
  };
  for (const c of components) pages[markdownPath(`/components/${c.slug}`)] = componentMd(c);
  // Each page says where its HTML is, at the end
  for (const p of allPages()) pages[markdownPath(p.path)] += `\n\n---\n\nThis page on the web: ${url(p.path === "/" ? "/" : `${p.path}/`)}\n`;
  return pages;
}

// llms.txt (https://llmstxt.org): what the site is, then every page's Markdown
export function llmsTxt() {
  const link = (p: { path: string; title: string; lede: string }) => `- [${p.title}](${url(markdownPath(p.path))}): ${p.lede}`;
  return [
    `# ${site.name}`,
    `> ${site.summary}`,
    `Every page is also Markdown at its path plus \`.md\` (the overview is \`/index.md\`). [llms-full.txt](${url("/llms-full.txt")}) has all of them in one file.`,
    "## Foundations",
    [overview, ...foundations].map(link).join("\n"),
    "## Resources",
    [...resources.map(link), `- [${SKILL_NAME} (SKILL.md)](${skillUrl()}): the agent skill itself, with references/tokens.md and references/components.md next to it`].join("\n"),
    "## React components",
    [componentsPage, ...components.map((c) => ({ path: `/components/${c.slug}`, title: c.name, lede: c.about }))].map(link).join("\n"),
  ].join("\n\n") + "\n";
}

// llms-full.txt: every page's Markdown in one file, in the same order
export function llmsFullTxt() {
  const pages = markdownPages();
  return [`# ${site.name}`, `> ${site.summary}`, ...allPages().map((p) => pages[markdownPath(p.path)].trim())].join("\n\n---\n\n") + "\n";
}
