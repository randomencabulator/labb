"use client";

import type { ComponentProps } from "react";
import { Toggle as BaseToggle } from "@base-ui/react/toggle";
import { cx, type Styled } from "../../utils";

// A chip that's on or off, like Web next to the model picker
export function Toggle({ className, ...rest }: Styled<ComponentProps<typeof BaseToggle>>) {
  return <BaseToggle className={cx("lb-toggle", className)} {...rest} />;
}
