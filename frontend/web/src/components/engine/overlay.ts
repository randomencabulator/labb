// An engine is an overlay on the defaults: only what differs. These keep it that way as it's edited.
import type { Overlay } from "../../api";

type Tree = Record<string, unknown>;

const isEmpty = (value: unknown) =>
  value === undefined || value === null || (Array.isArray(value) && value.length === 0) ||
  (typeof value === "object" && value !== null && !Array.isArray(value) && Object.keys(value).length === 0);

/** The overlay with this setting changed; undefined or empty takes it out, and what's left empty goes too */
export function setIn(overlay: Overlay, path: string[], value: unknown): Overlay {
  const [key, ...rest] = path;
  const next: Tree = { ...(overlay as Tree) };
  const inner = rest.length ? setIn((next[key] ?? {}) as Overlay, rest, value) : value;
  if (isEmpty(inner)) delete next[key];
  else next[key] = inner;
  return next as Overlay;
}

export function getIn<T = unknown>(overlay: Overlay, path: string[]): T | undefined {
  let found: unknown = overlay;
  for (const key of path) found = found && typeof found === "object" ? (found as Tree)[key] : undefined;
  return found as T | undefined;
}

/** Set a setting that has a default: the default itself is the same as not setting it */
export function setSetting(overlay: Overlay, path: string[], value: unknown, fallback: unknown): Overlay {
  return setIn(overlay, path, JSON.stringify(value) === JSON.stringify(fallback) ? undefined : value);
}

const flat = (overlay: Overlay): Record<string, unknown> => {
  const found: Record<string, unknown> = {};
  for (const [section, values] of Object.entries(overlay)) {
    for (const [key, value] of Object.entries((values ?? {}) as Tree)) found[`${section}.${key}`] = value;
  }
  return found;
};

export type Change = { where: string; before: unknown; after: unknown };

/** What differs between two overlays, setting by setting */
export function diff(before: Overlay, after: Overlay): Change[] {
  const a = flat(before);
  const b = flat(after);
  return [...new Set([...Object.keys(a), ...Object.keys(b)])]
    .filter(where => JSON.stringify(a[where]) !== JSON.stringify(b[where]))
    .map(where => ({ where, before: a[where], after: b[where] }));
}

export const shown = (value: unknown) => (value === undefined ? "(the default)" : typeof value === "string" ? value : JSON.stringify(value, null, 1));
