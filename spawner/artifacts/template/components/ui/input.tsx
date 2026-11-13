"use client";

import type { ComponentProps } from "react";
import { Input as BaseInput } from "@base-ui/react/input";
import { cn } from "@/lib/cn";

const field =
  "w-full min-w-0 rounded-md border bg-card px-3 text-sm outline-none transition-[box-shadow] placeholder:text-muted-foreground focus-visible:ring-3 focus-visible:ring-ring data-disabled:opacity-50 aria-invalid:border-destructive";

export type InputProps = Omit<ComponentProps<typeof BaseInput>, "className"> & { className?: string };

// A text field; put it in a Field for its label, description and error
export function Input({ className, ...props }: InputProps) {
  return <BaseInput className={cn(field, "h-9", className)} {...props} />;
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea className={cn(field, "min-h-20 py-2", className)} {...props} />;
}
