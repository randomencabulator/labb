"use client";

import type { ComponentProps } from "react";
import { Tabs as BaseTabs } from "@base-ui/react/tabs";
import { cn } from "@/lib/cn";

type Styled<P> = Omit<P, "className"> & { className?: string };

// Tabs > TabsList > Tab (value) … then a TabsPanel (value) for each:
// <Tabs defaultValue="chart"><TabsList><Tab value="chart">Chart</Tab></TabsList><TabsPanel value="chart">…</TabsPanel></Tabs>
export function Tabs({ className, ...props }: Styled<ComponentProps<typeof BaseTabs.Root>>) {
  return <BaseTabs.Root className={cn("flex flex-col gap-3", className)} {...props} />;
}

export function TabsList({ className, children, ...props }: Styled<ComponentProps<typeof BaseTabs.List>>) {
  return (
    <BaseTabs.List className={cn("relative z-0 inline-flex w-fit items-center gap-1 rounded-lg bg-muted p-1", className)} {...props}>
      {children}
      <BaseTabs.Indicator className="absolute top-1/2 left-0 -z-1 h-(--active-tab-height) w-(--active-tab-width) translate-x-(--active-tab-left) -translate-y-1/2 rounded-md bg-card shadow-sm transition-all duration-200" />
    </BaseTabs.List>
  );
}

export function Tab({ className, ...props }: Styled<ComponentProps<typeof BaseTabs.Tab>>) {
  return (
    <BaseTabs.Tab
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-md px-3 text-sm font-medium whitespace-nowrap text-muted-foreground outline-none select-none",
        "hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring data-active:text-foreground [&_svg]:size-4",
        className,
      )}
      {...props}
    />
  );
}

export function TabsPanel({ className, ...props }: Styled<ComponentProps<typeof BaseTabs.Panel>>) {
  return <BaseTabs.Panel className={cn("outline-none", className)} {...props} />;
}
