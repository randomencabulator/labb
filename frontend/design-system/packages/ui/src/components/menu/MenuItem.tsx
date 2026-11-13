"use client";

import type { ComponentProps } from "react";
import { Menu as BaseMenu } from "@base-ui/react/menu";
import { cx, type Styled } from "../../utils";

export function MenuItem({ danger, className, ...rest }: Styled<ComponentProps<typeof BaseMenu.Item>> & { danger?: boolean }) {
  return <BaseMenu.Item className={cx("lb-menu-item", danger && "danger", className)} {...rest} />;
}
