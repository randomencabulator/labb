import type { ReactNode } from "react";

// A tool call: its name, then what it was called with
export function ToolLine({ name, children }: { name: string; children?: ReactNode }) {
  return (
    <span className="lb-tool-line">
      <b>{name}</b>
      {children}
    </span>
  );
}
