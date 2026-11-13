import type { ReactNode } from "react";

// A mono label over a section
export function Label({ children, as: Tag = "span" }: { children: ReactNode; as?: "span" | "h3" | "div" }) {
  return <Tag className="lb-label">{children}</Tag>;
}
