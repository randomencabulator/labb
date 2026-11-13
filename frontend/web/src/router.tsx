// The address bar: every page, chat and editor has its own path, so links, reloads, Back and Forward work.
// The server answers page loads of these paths with the app (server/app.py's `PAGE_ROOTS`).
import { type AnchorHTMLAttributes, useSyncExternalStore } from "react";

const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  window.addEventListener("popstate", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("popstate", listener);
  };
}

/** The current path, e.g. "/agents/new"; re-renders when it changes */
export function usePath() {
  return useSyncExternalStore(subscribe, () => window.location.pathname);
}

/** Go to a path; `replace` changes this history entry instead of adding one */
export function navigate(path: string, { replace = false }: { replace?: boolean } = {}) {
  if (path === window.location.pathname) return;
  if (replace) window.history.replaceState(null, "", path);
  else window.history.pushState(null, "", path);
  for (const listener of listeners) listener();
}

/** The path's parts, decoded: "/studio/workflows/a%20b" → ["studio", "workflows", "a b"] */
export function segments(path: string) {
  return path
    .split("/")
    .filter(Boolean)
    .map(part => {
      try {
        return decodeURIComponent(part);
      } catch {
        return part;
      }
    });
}

/** A path from its parts, each encoded */
export function pathOf(...parts: (string | null | undefined)[]) {
  return "/" + parts.filter((part): part is string => Boolean(part)).map(encodeURIComponent).join("/");
}

export const chatPath = (threadId: string | null) => (threadId ? pathOf("chat", threadId) : "/");

/**
 * A link inside the app: a plain click runs `onClick` and goes there in this page;
 * Ctrl/Cmd/Shift/middle click is left to the browser (a new tab or window)
 */
export function Link({ to, onClick, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & { to: string }) {
  return (
    <a
      {...props}
      href={to}
      onClick={event => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        onClick?.(event);
        navigate(to);
      }}
    />
  );
}
