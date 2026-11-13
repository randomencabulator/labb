"use client";

import type { ComponentProps } from "react";
import { Input as BaseInput } from "@base-ui/react/input";
import { cx, type Styled } from "../../utils";

export function Input({ className, ...rest }: Styled<ComponentProps<typeof BaseInput>>) {
  return <BaseInput className={cx("lb-input", className)} {...rest} />;
}
