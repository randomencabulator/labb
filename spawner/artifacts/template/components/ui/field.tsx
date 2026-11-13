"use client";

import type { ReactNode } from "react";
import { Field as BaseField } from "@base-ui/react/field";
import { cn } from "@/lib/cn";

export type FieldProps = {
  label: ReactNode;
  description?: ReactNode;
  // Shown under the control, and marks it invalid
  error?: ReactNode;
  className?: string;
  children: ReactNode;
};

// A labelled form control: <Field label="Email"><Input type="email" /></Field>
export function Field({ label, description, error, className, children }: FieldProps) {
  return (
    <BaseField.Root invalid={Boolean(error)} className={cn("flex flex-col gap-1.5", className)}>
      <BaseField.Label className="text-sm font-medium">{label}</BaseField.Label>
      {children}
      {description && <BaseField.Description className="text-xs text-muted-foreground">{description}</BaseField.Description>}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </BaseField.Root>
  );
}
