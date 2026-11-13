"use client";

import type { ComponentProps } from "react";
import { Button as BaseButton } from "@base-ui/react/button";
import { cn } from "@/lib/cn";

const variants = {
  primary: "bg-primary text-primary-foreground hover:bg-primary/90",
  outline: "border bg-card hover:bg-muted",
  ghost: "hover:bg-muted",
  destructive: "bg-destructive text-white hover:bg-destructive/90",
  link: "text-primary underline-offset-4 hover:underline",
};

const sizes = {
  sm: "h-8 gap-1.5 px-3 text-xs",
  md: "h-9 gap-2 px-4 text-sm",
  lg: "h-11 gap-2 px-6 text-base",
  icon: "size-9",
};

export type ButtonProps = Omit<ComponentProps<typeof BaseButton>, "className"> & {
  variant?: keyof typeof variants;
  size?: keyof typeof sizes;
  className?: string;
};

// A button. Give an icon-only one (size="icon") an aria-label
export function Button({ variant = "primary", size = "md", className, ...props }: ButtonProps) {
  return (
    <BaseButton
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-md font-medium whitespace-nowrap transition-colors outline-none select-none",
        "focus-visible:ring-3 focus-visible:ring-ring data-disabled:pointer-events-none data-disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    />
  );
}
