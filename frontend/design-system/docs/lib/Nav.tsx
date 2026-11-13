"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Logo, Segmented, Wordmark } from "@labb/ui";
import { componentsPage, foundations, resources } from "./content";
import { components } from "./registry";

type Theme = "system" | "light" | "dark";

// The same pages, in the same order, as llms.txt (content.ts)
const sections = [
  {
    name: "Foundations",
    links: [{ href: "/", label: "Overview" }, ...foundations.map((p) => ({ href: p.path, label: p.title }))],
  },
  {
    name: "Resources",
    links: resources.map((p) => ({ href: p.path, label: p.title })),
  },
  {
    name: "React components",
    links: [{ href: componentsPage.path, label: "All components" }, ...components.map((c) => ({ href: `/components/${c.slug}`, label: c.name }))],
  },
];

function useTheme() {
  const [theme, setTheme] = useState<Theme>("system");
  useEffect(() => {
    const saved = document.documentElement.dataset.theme;
    if (saved === "light" || saved === "dark") setTheme(saved);
  }, []);
  const pick = (next: Theme) => {
    setTheme(next);
    const root = document.documentElement;
    if (next === "system") delete root.dataset.theme;
    else root.dataset.theme = next;
    try {
      if (next === "system") localStorage.removeItem("lb-theme");
      else localStorage.setItem("lb-theme", next);
    } catch {
      // Storage can be unavailable, e.g. in a private window
    }
  };
  return [theme, pick] as const;
}

export function Nav() {
  const path = usePathname();
  const [theme, setTheme] = useTheme();
  const [open, setOpen] = useState(false);

  useEffect(() => setOpen(false), [path]);

  return (
    <aside className="docs-nav" data-open={open}>
      <div className="docs-brand-row">
        <Link href="/" className="docs-brand">
          <Logo size={22} />
          <Wordmark />
          <span className="docs-brand-tag">design</span>
        </Link>
        <button type="button" className="docs-menu-button lb-button ghost sm" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Close" : "Menu"}
        </button>
      </div>
      <nav className="docs-links">
        {sections.map((s) => (
          <div key={s.name}>
            <div className="lb-label docs-links-head">{s.name}</div>
            {s.links.map((l) => (
              <Link key={l.href} href={l.href} aria-current={path === l.href ? "page" : undefined}>
                {l.label}
              </Link>
            ))}
          </div>
        ))}
      </nav>
      <div className="docs-theme">
        <Segmented
          label="Theme"
          value={theme}
          onChange={setTheme}
          options={[
            { value: "system", label: "System" },
            { value: "light", label: "Light" },
            { value: "dark", label: "Dark" },
          ]}
        />
      </div>
    </aside>
  );
}
