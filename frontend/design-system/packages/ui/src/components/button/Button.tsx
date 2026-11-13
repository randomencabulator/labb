"use client";

import type { ComponentProps } from "react";
import { Button as BaseButton } from "@base-ui/react/button";
import { cx, type Styled } from "../../utils";

export type ButtonProps = Styled<ComponentProps<typeof BaseButton>> & {
  variant?: "default" | "primary" | "danger" | "ghost" | "link";
  size?: "sm" | "md" | "lg";
  // A square button holding just an icon (give it an aria-label)
  icon?: boolean;
};

export function Button({ variant = "default", size = "md", icon, className, ...rest }: ButtonProps) {
  return (
    <BaseButton
      className={cx("lb-button", variant !== "default" && variant, size !== "md" && size, icon && "icon", className)}
      {...rest}
    />
  );
}
