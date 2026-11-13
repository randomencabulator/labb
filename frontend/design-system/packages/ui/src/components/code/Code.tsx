import type { ReactNode } from "react";

// Inline code
export function Code({ children }: { children: ReactNode }) {
  return <code className="lb-code">{children}</code>;
}
