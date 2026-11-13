"use client";

import type { ReactNode } from "react";
import { Select as BaseSelect } from "@base-ui/react/select";
import { CaretUpDownIcon, CheckIcon } from "@phosphor-icons/react";
import { cn } from "@/lib/cn";

export type SelectItem<V extends string> = { value: V; label: ReactNode };

export type SelectProps<V extends string> = {
  items: readonly SelectItem<V>[];
  value?: V | null;
  defaultValue?: V;
  onValueChange?: (value: V) => void;
  placeholder?: ReactNode;
  className?: string;
  "aria-label"?: string;
};

// A dropdown of choices, typed by their values:
// <Select items={[{ value: "week", label: "This week" }]} value={range} onValueChange={setRange} />
export function Select<V extends string>({ items, value, defaultValue, onValueChange, placeholder, className, ...rest }: SelectProps<V>) {
  return (
    <BaseSelect.Root
      items={items as SelectItem<V>[]}
      value={value}
      defaultValue={defaultValue}
      onValueChange={(next) => {
        if (next !== null) onValueChange?.(next as V);
      }}
    >
      <BaseSelect.Trigger
        aria-label={rest["aria-label"]}
        className={cn(
          "inline-flex h-9 min-w-40 items-center justify-between gap-2 rounded-md border bg-card px-3 text-sm outline-none select-none",
          "hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring data-popup-open:bg-muted",
          className,
        )}
      >
        <BaseSelect.Value placeholder={placeholder} className="truncate data-placeholder:text-muted-foreground" />
        <BaseSelect.Icon className="text-muted-foreground">
          <CaretUpDownIcon className="size-4" />
        </BaseSelect.Icon>
      </BaseSelect.Trigger>
      <BaseSelect.Portal>
        <BaseSelect.Positioner sideOffset={4} className="z-50 outline-none select-none">
          <BaseSelect.Popup
            className={cn(
              "min-w-(--anchor-width) origin-(--transform-origin) rounded-lg border bg-card p-1 shadow-lg outline-none",
              "transition-[transform,scale,opacity] data-ending-style:scale-95 data-ending-style:opacity-0 data-starting-style:scale-95 data-starting-style:opacity-0",
            )}
          >
            <BaseSelect.List className="max-h-(--available-height) overflow-y-auto">
              {items.map((item) => (
                <BaseSelect.Item
                  key={item.value}
                  value={item.value}
                  className="grid cursor-default grid-cols-[1rem_1fr] items-center gap-2 rounded-md py-1.5 pr-3 pl-2 text-sm outline-none data-highlighted:bg-muted"
                >
                  <BaseSelect.ItemIndicator className="col-start-1">
                    <CheckIcon className="size-3.5" />
                  </BaseSelect.ItemIndicator>
                  <BaseSelect.ItemText className="col-start-2">{item.label}</BaseSelect.ItemText>
                </BaseSelect.Item>
              ))}
            </BaseSelect.List>
          </BaseSelect.Popup>
        </BaseSelect.Positioner>
      </BaseSelect.Portal>
    </BaseSelect.Root>
  );
}
