import type { HTMLAttributes } from "react";
import { cx } from "../../utils";

// A small yellow dot for what's new, like an unread chat
export function Dot({ className, ...rest }: HTMLAttributes<HTMLSpanElement>) {
  return <span className={cx("lb-dot", className)} aria-hidden="true" {...rest} />;
}
