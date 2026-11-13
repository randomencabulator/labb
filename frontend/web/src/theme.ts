import { useEffect, useState } from "react";
import darkCode from "highlight.js/styles/github-dark.css?inline";
import lightCode from "highlight.js/styles/github.css?inline";

// The page's background in each theme (styles.css's --bg), for the status bar
const LIGHT_BG = "#fffdf3";
const DARK_BG = "#0b0b0a";

// "system" follows the OS; the others are picked in the header
export type Theme = "system" | "light" | "dark";
export const THEMES: Theme[] = ["system", "light", "dark"];

function load(): Theme {
  try {
    const saved = localStorage.getItem("theme");
    return saved === "light" || saved === "dark" ? saved : "system";
  } catch {
    return "system";
  }
}

// The code highlighting theme, matching the page's
function codeStyles(theme: Theme) {
  if (theme === "light") return lightCode;
  if (theme === "dark") return darkCode;
  return `@media (prefers-color-scheme: light) {${lightCode}}\n@media (prefers-color-scheme: dark) {${darkCode}}`;
}

function apply(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;
  let style = document.getElementById("code-theme");
  if (!style) {
    style = document.createElement("style");
    style.id = "code-theme";
    document.head.append(style);
  }
  style.textContent = codeStyles(theme);
  // The phone's status bar (index.html's theme-color): the system's light or dark, or the one picked
  for (const meta of document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')) {
    const dark = meta.media.includes("dark");
    meta.content = theme === "system" ? (dark ? DARK_BG : LIGHT_BG) : theme === "dark" ? DARK_BG : LIGHT_BG;
  }
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(load);

  useEffect(() => {
    apply(theme);
    try {
      if (theme === "system") localStorage.removeItem("theme");
      else localStorage.setItem("theme", theme);
    } catch {
      // Storage can be unavailable, e.g. in a private window
    }
  }, [theme]);

  return [theme, setTheme] as const;
}
