import type { ReactNode } from "react";

// When labb needs a yes before it acts
export function Approval({ title, children, actions }: { title: ReactNode; children?: ReactNode; actions: ReactNode }) {
  return (
    <div className="lb-approval" role="group" aria-label="Needs your approval">
      <div className="lb-approval-title">{title}</div>
      {children}
      <div className="lb-approval-actions">{actions}</div>
    </div>
  );
}
