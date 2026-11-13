"use client";

import type { ReactElement, ReactNode } from "react";
import { Menu as BaseMenu } from "@base-ui/react/menu";

// A menu that opens from its trigger, e.g. <Menu trigger={<Button>Account</Button>}>…</Menu>
export function Menu({
  trigger,
  children,
  side = "bottom",
  align = "start",
}: {
  trigger: ReactElement;
  children: ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  align?: "start" | "center" | "end";
}) {
  return (
    <BaseMenu.Root>
      <BaseMenu.Trigger render={trigger} />
      <BaseMenu.Portal>
        <BaseMenu.Positioner className="lb-menu-positioner" side={side} align={align} sideOffset={6}>
          <BaseMenu.Popup className="lb-menu">{children}</BaseMenu.Popup>
        </BaseMenu.Positioner>
      </BaseMenu.Portal>
    </BaseMenu.Root>
  );
}
