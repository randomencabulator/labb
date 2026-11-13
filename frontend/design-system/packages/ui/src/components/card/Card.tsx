"use client";

import type { ReactNode } from "react";
import { Button as BaseButton } from "@base-ui/react/button";
import { cx } from "../../utils";

export function Card({
  eyebrow,
  title,
  children,
  onClick,
  className,
}: {
  eyebrow?: ReactNode;
  title?: ReactNode;
  children?: ReactNode;
  // A card with onClick is a button that lifts on hover
  onClick?: () => void;
  className?: string;
}) {
  const inner = (
    <>
      {eyebrow != null && <span className="lb-card-eyebrow">{eyebrow}</span>}
      {title != null && <b className="lb-card-title">{title}</b>}
      {children != null && <span className="lb-card-body">{children}</span>}
    </>
  );
  return onClick ? (
    <BaseButton className={cx("lb-card", "interactive", className)} onClick={onClick}>
      {inner}
    </BaseButton>
  ) : (
    <div className={cx("lb-card", className)}>{inner}</div>
  );
}
