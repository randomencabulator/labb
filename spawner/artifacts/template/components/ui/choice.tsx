"use client";

import type { ComponentProps, ReactNode } from "react";
import { Checkbox as BaseCheckbox } from "@base-ui/react/checkbox";
import { Slider as BaseSlider } from "@base-ui/react/slider";
import { Switch as BaseSwitch } from "@base-ui/react/switch";
import { CheckIcon } from "@phosphor-icons/react";
import { cn } from "@/lib/cn";

type WithLabel<P> = Omit<P, "className"> & { label?: ReactNode; className?: string };

// A checkbox, with its label: <Checkbox label="Remember me" checked={on} onCheckedChange={setOn} />
export function Checkbox({ label, className, ...props }: WithLabel<ComponentProps<typeof BaseCheckbox.Root>>) {
  return (
    <label className={cn("inline-flex items-center gap-2 text-sm", className)}>
      <BaseCheckbox.Root
        className="flex size-4 shrink-0 items-center justify-center rounded border bg-card outline-none focus-visible:ring-3 focus-visible:ring-ring data-checked:border-primary data-checked:bg-primary data-checked:text-primary-foreground data-disabled:opacity-50"
        {...props}
      >
        <BaseCheckbox.Indicator className="flex data-unchecked:hidden">
          <CheckIcon weight="bold" className="size-3" />
        </BaseCheckbox.Indicator>
      </BaseCheckbox.Root>
      {label}
    </label>
  );
}

// An on/off switch, with its label: <Switch label="Dark mode" checked={on} onCheckedChange={setOn} />
export function Switch({ label, className, ...props }: WithLabel<ComponentProps<typeof BaseSwitch.Root>>) {
  return (
    <label className={cn("inline-flex items-center gap-2 text-sm", className)}>
      <BaseSwitch.Root
        className="flex h-5 w-9 shrink-0 rounded-full bg-border p-0.5 outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring data-checked:bg-primary data-disabled:opacity-50"
        {...props}
      >
        <BaseSwitch.Thumb className="size-4 rounded-full bg-card shadow-sm transition-transform data-checked:translate-x-4" />
      </BaseSwitch.Root>
      {label}
    </label>
  );
}

// A slider for one number: <Slider value={volume} onValueChange={(v) => setVolume(v as number)} min={0} max={100} />
export function Slider({ className, ...props }: Omit<ComponentProps<typeof BaseSlider.Root>, "className"> & { className?: string }) {
  return (
    <BaseSlider.Root className={cn("w-full", className)} {...props}>
      <BaseSlider.Control className="flex w-full touch-none items-center py-2 select-none">
        <BaseSlider.Track className="h-1.5 w-full rounded-full bg-muted select-none">
          <BaseSlider.Indicator className="rounded-full bg-primary select-none" />
          <BaseSlider.Thumb className="size-4 rounded-full border-2 border-primary bg-card shadow-sm outline-none select-none focus-visible:ring-3 focus-visible:ring-ring" />
        </BaseSlider.Track>
      </BaseSlider.Control>
    </BaseSlider.Root>
  );
}
