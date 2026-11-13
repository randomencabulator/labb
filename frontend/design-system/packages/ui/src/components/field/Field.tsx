"use client";

import type { ReactNode } from "react";
import { Field as BaseField } from "@base-ui/react/field";
import { cx } from "../../utils";

// A label over its control, with a hint and an error under it. An error marks the field invalid
export function Field({
  label,
  hint,
  error,
  children,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <BaseField.Root className={cx("lb-field", className)} invalid={error != null ? true : undefined}>
      <BaseField.Label className="lb-field-label">{label}</BaseField.Label>
      {children}
      {hint != null && error == null && <BaseField.Description className="lb-field-hint">{hint}</BaseField.Description>}
      {error != null && (
        <BaseField.Error className="lb-field-error" match>
          {error}
        </BaseField.Error>
      )}
    </BaseField.Root>
  );
}
