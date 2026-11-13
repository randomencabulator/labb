import type { ReactNode } from "react";

// A key to press
export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="lb-kbd">{children}</kbd>;
}
