import type { ReactNode } from "react";
import { cx } from "../../utils";

export function Message({ from, children }: { from: "user" | "assistant"; children: ReactNode }) {
  return <div className={cx("lb-message", from)}>{children}</div>;
}
