import type { HTMLAttributes } from "react";
import { cx } from "../../utils";

export function Badge({
  tone = "default",
  mono,
  className,
  ...rest
}: HTMLAttributes<HTMLSpanElement> & { tone?: "default" | "accent" | "outline" | "error"; mono?: boolean }) {
  return <span className={cx("lb-badge", tone !== "default" && tone, mono && "mono", className)} {...rest} />;
}
