"use client";

import type { ComponentProps } from "react";
import { Switch as BaseSwitch } from "@base-ui/react/switch";
import { cx, type Styled } from "../../utils";

export function Switch({ className, ...rest }: Styled<ComponentProps<typeof BaseSwitch.Root>>) {
  return (
    <BaseSwitch.Root className={cx("lb-switch", className)} {...rest}>
      <BaseSwitch.Thumb className="lb-switch-thumb" />
    </BaseSwitch.Root>
  );
}
