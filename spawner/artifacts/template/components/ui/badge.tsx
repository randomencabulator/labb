import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

const variants = {
  default: "bg-muted text-foreground",
  primary: "bg-primary text-primary-foreground",
  outline: "border text-foreground",
  success: "bg-success/15 text-success",
  warning: "bg-warning/20 text-foreground",
  destructive: "bg-destructive/15 text-destructive",
};

export type BadgeProps = ComponentProps<"span"> & { variant?: keyof typeof variants };

// A small label: a status, a count, a tag
export function Badge({ variant = "default", className, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium whitespace-nowrap [&_svg]:size-3",
        variants[variant],
        className,
      )}
      {...props}
    />
  );
}
