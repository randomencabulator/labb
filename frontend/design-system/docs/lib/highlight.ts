// Syntax highlighting at build time with Shiki. The theme's colours are the code tokens' CSS
// variables, so code follows the page's theme and the site ships no highlighter.
import { createHighlighter, type Highlighter, type ThemeRegistration } from "shiki";

export type Lang = "tsx" | "css" | "bash" | "swift";

const v = (name: string) => `var(--lb-sys-color-code-${name})`;

const theme: ThemeRegistration = {
  name: "labb",
  type: "light",
  colors: { "editor.foreground": "var(--lb-sys-color-text)", "editor.background": "transparent" },
  // Later rules win where scopes overlap equally; more specific scopes win anyway
  tokenColors: [
    { settings: { foreground: "var(--lb-sys-color-text)" } },
    { scope: ["punctuation", "keyword.operator", "meta.brace", "meta.tag.tsx punctuation"], settings: { foreground: v("punctuation") } },
    { scope: ["comment", "punctuation.definition.comment"], settings: { foreground: v("comment"), fontStyle: "italic" } },
    {
      scope: [
        "keyword",
        "storage",
        "keyword.operator.new",
        "keyword.operator.expression",
        "storage.type.function.arrow",
        "keyword.control.import",
        "keyword.control.from",
      ],
      settings: { foreground: v("keyword") },
    },
    { scope: ["string", "punctuation.definition.string", "string.template"], settings: { foreground: v("string") } },
    {
      scope: ["constant.numeric", "constant.language", "constant.character", "support.constant", "keyword.other.unit", "constant.other.color"],
      settings: { foreground: v("constant") },
    },
    {
      scope: ["entity.name.tag", "support.class.component", "entity.name.type", "support.type.primitive", "entity.other.attribute-name.class.css", "entity.other.attribute-name.pseudo-class.css"],
      settings: { foreground: v("tag") },
    },
    { scope: ["entity.other.attribute-name", "support.type.property-name", "support.type.custom-property", "variable.css", "variable.argument.css"], settings: { foreground: v("attr") } },
    { scope: ["entity.name.function", "support.function", "meta.function-call entity.name.function"], settings: { foreground: v("function") } },
    // A JSX expression's braces read as punctuation, its contents as code
    { scope: ["punctuation.section.embedded"], settings: { foreground: v("punctuation") } },
  ],
};

let highlighter: Promise<Highlighter> | undefined;

function load() {
  highlighter ??= createHighlighter({ themes: [theme], langs: ["tsx", "css", "bash", "swift"] });
  return highlighter;
}

// The code as HTML: a <pre class="shiki"> with a span per token
export async function highlight(code: string, lang: Lang) {
  const h = await load();
  return h.codeToHtml(code, { lang, theme: "labb" });
}
