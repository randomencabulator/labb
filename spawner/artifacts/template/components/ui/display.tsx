"use client";

import type { ComponentProps, ReactNode } from "react";
import { Accordion as BaseAccordion } from "@base-ui/react/accordion";
import { Avatar as BaseAvatar } from "@base-ui/react/avatar";
import { Progress as BaseProgress } from "@base-ui/react/progress";
import { Separator as BaseSeparator } from "@base-ui/react/separator";
import { ArrowDownRightIcon, ArrowUpRightIcon, CaretDownIcon } from "@phosphor-icons/react";
import { cn } from "@/lib/cn";

// Sections that open and close: <Accordion items={[{ title: "Shipping", content: "…" }]} />
export function Accordion({ items, multiple, className }: { items: readonly { title: ReactNode; content: ReactNode }[]; multiple?: boolean; className?: string }) {
  return (
    <BaseAccordion.Root multiple={multiple} className={cn("flex flex-col rounded-lg border", className)}>
      {items.map((item, i) => (
        <BaseAccordion.Item key={i} className="border-b last:border-b-0">
          <BaseAccordion.Header>
            <BaseAccordion.Trigger className="group flex w-full items-center justify-between gap-4 px-4 py-3 text-left text-sm font-medium outline-none hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring">
              {item.title}
              <CaretDownIcon className="size-4 shrink-0 text-muted-foreground transition-transform group-data-panel-open:rotate-180" />
            </BaseAccordion.Trigger>
          </BaseAccordion.Header>
          <BaseAccordion.Panel className="h-(--accordion-panel-height) overflow-hidden text-sm text-muted-foreground transition-[height] data-ending-style:h-0 data-starting-style:h-0">
            <div className="px-4 pb-3">{item.content}</div>
          </BaseAccordion.Panel>
        </BaseAccordion.Item>
      ))}
    </BaseAccordion.Root>
  );
}

// A person's picture, or their initials without one
export function Avatar({ name, src, className }: { name: string; src?: string; className?: string }) {
  const initials = name.split(/\s+/).map((part) => part[0] ?? "").join("").slice(0, 2).toUpperCase();
  return (
    <BaseAvatar.Root className={cn("inline-flex size-9 shrink-0 items-center justify-center overflow-hidden rounded-full bg-muted text-xs font-medium", className)}>
      {src && <BaseAvatar.Image src={src} alt={name} className="size-full object-cover" />}
      <BaseAvatar.Fallback>{initials}</BaseAvatar.Fallback>
    </BaseAvatar.Root>
  );
}

// How far along something is, 0 to `max` (100)
export function Progress({ value, max = 100, label, className }: { value: number; max?: number; label?: ReactNode; className?: string }) {
  return (
    <BaseProgress.Root value={value} max={max} className={cn("flex flex-col gap-1.5", className)}>
      {label && (
        <div className="flex justify-between text-sm">
          <BaseProgress.Label>{label}</BaseProgress.Label>
          <BaseProgress.Value className="text-muted-foreground" />
        </div>
      )}
      <BaseProgress.Track className="h-2 overflow-hidden rounded-full bg-muted">
        <BaseProgress.Indicator className="bg-primary transition-all" />
      </BaseProgress.Track>
    </BaseProgress.Root>
  );
}

export function Separator({ className, ...props }: Omit<ComponentProps<typeof BaseSeparator>, "className"> & { className?: string }) {
  return <BaseSeparator className={cn("shrink-0 bg-border data-[orientation=horizontal]:h-px data-[orientation=vertical]:w-px", className)} {...props} />;
}

// A number that matters, with how it changed: <Stat label="Revenue" value="€12.4k" change={8.2} />
export function Stat({ label, value, change, hint, className }: { label: ReactNode; value: ReactNode; change?: number; hint?: ReactNode; className?: string }) {
  const up = change !== undefined && change >= 0;
  return (
    <div className={cn("flex flex-col gap-1 rounded-xl border bg-card p-5", className)}>
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-2xl font-semibold tracking-tight tabular-nums">{value}</span>
      {(change !== undefined || hint) && (
        <span className="flex items-center gap-1 text-xs text-muted-foreground">
          {change !== undefined && (
            <span className={cn("inline-flex items-center gap-0.5 font-medium", up ? "text-success" : "text-destructive")}>
              {up ? <ArrowUpRightIcon className="size-3" /> : <ArrowDownRightIcon className="size-3" />}
              {Math.abs(change)}%
            </span>
          )}
          {hint}
        </span>
      )}
    </div>
  );
}
