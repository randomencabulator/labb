import { useState } from "react";

/** What the sidebar shows, as this browser keeps it: the pages in their order, which are pinned to
 * the top and which are hidden (they still open from their address, and come back from Customize),
 * and whether the Terminal list is shown */
export type SidebarLayout = {
  // Page ids in the order they're shown; pages missing from it (new ones) go at the end
  order: string[];
  pinned: string[];
  hidden: string[];
  terminal: boolean;
};

const KEY = "sidebarLayout";
export const DEFAULT_LAYOUT: SidebarLayout = { order: [], pinned: [], hidden: [], terminal: true };

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function load(): SidebarLayout {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    return {
      order: strings(saved.order),
      pinned: strings(saved.pinned),
      hidden: strings(saved.hidden),
      terminal: saved.terminal !== false,
    };
  } catch {
    // Storage can be unavailable, or hold something else
    return DEFAULT_LAYOUT;
  }
}

/** The pages as they're shown: pinned first (in the order they were pinned), then the rest in order */
export function arrange<T extends { id: string }>(pages: T[], layout: SidebarLayout): T[] {
  const position = (page: T) => {
    const at = layout.order.indexOf(page.id);
    return at === -1 ? layout.order.length + pages.indexOf(page) : at;
  };
  const ordered = [...pages].sort((a, b) => position(a) - position(b));
  const pinned = layout.pinned.flatMap(id => ordered.filter(page => page.id === id));
  return [...pinned, ...ordered.filter(page => !layout.pinned.includes(page.id))];
}

export function useSidebarLayout() {
  const [layout, setLayout] = useState(load);
  const save = (next: SidebarLayout) => {
    setLayout(next);
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      // Storage can be unavailable, e.g. in a private window
    }
  };
  return [layout, save] as const;
}
