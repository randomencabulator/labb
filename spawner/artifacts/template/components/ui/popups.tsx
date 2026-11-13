"use client";

import type { ComponentProps, ReactNode } from "react";
import { Menu as BaseMenu } from "@base-ui/react/menu";
import { Popover as BasePopover } from "@base-ui/react/popover";
import { Tooltip as BaseTooltip } from "@base-ui/react/tooltip";
import { cn } from "@/lib/cn";

const popup =
  "origin-(--transform-origin) rounded-lg border bg-card shadow-lg outline-none transition-[scale,opacity] data-ending-style:scale-95 data-ending-style:opacity-0 data-starting-style:scale-95 data-starting-style:opacity-0";

type Trigger = ComponentProps<typeof BaseTooltip.Trigger>["render"];

// A hint on hover or focus: <Tooltip content="Download"><Button size="icon" aria-label="Download">…</Button></Tooltip>
export function Tooltip({ content, children }: { content: ReactNode; children: Trigger }) {
  return (
    <BaseTooltip.Root>
      <BaseTooltip.Trigger render={children} />
      <BaseTooltip.Portal>
        <BaseTooltip.Positioner sideOffset={6} className="z-50">
          <BaseTooltip.Popup className={cn(popup, "px-2 py-1 text-xs")}>{content}</BaseTooltip.Popup>
        </BaseTooltip.Positioner>
      </BaseTooltip.Portal>
    </BaseTooltip.Root>
  );
}

// A panel that opens from what's given as `trigger`: <Popover trigger={<Button variant="outline">Filters</Button>}>…</Popover>
export function Popover({ trigger, className, children }: { trigger: Trigger; className?: string; children: ReactNode }) {
  return (
    <BasePopover.Root>
      <BasePopover.Trigger render={trigger} />
      <BasePopover.Portal>
        <BasePopover.Positioner sideOffset={6} className="z-50">
          <BasePopover.Popup className={cn(popup, "w-72 p-4 text-sm", className)}>{children}</BasePopover.Popup>
        </BasePopover.Positioner>
      </BasePopover.Portal>
    </BasePopover.Root>
  );
}

export type MenuAction = { label: ReactNode; icon?: ReactNode; onClick?: () => void; danger?: boolean; disabled?: boolean } | "separator";

// A menu of actions: <Menu trigger={<Button variant="ghost" size="icon" aria-label="More">…</Button>} items={[{ label: "Edit", onClick }]} />
export function Menu({ trigger, items }: { trigger: Trigger; items: readonly MenuAction[] }) {
  return (
    <BaseMenu.Root>
      <BaseMenu.Trigger render={trigger} />
      <BaseMenu.Portal>
        <BaseMenu.Positioner sideOffset={6} align="start" className="z-50 outline-none">
          <BaseMenu.Popup className={cn(popup, "min-w-40 p-1")}>
            {items.map((item, i) =>
              item === "separator" ? (
                <BaseMenu.Separator key={i} className="mx-1 my-1 h-px bg-border" />
              ) : (
                <BaseMenu.Item
                  key={i}
                  onClick={item.onClick}
                  disabled={item.disabled}
                  className={cn(
                    "flex cursor-default items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none select-none data-disabled:opacity-50 data-highlighted:bg-muted [&_svg]:size-4",
                    item.danger && "text-destructive",
                  )}
                >
                  {item.icon}
                  {item.label}
                </BaseMenu.Item>
              ),
            )}
          </BaseMenu.Popup>
        </BaseMenu.Positioner>
      </BaseMenu.Portal>
    </BaseMenu.Root>
  );
}
