"use client";

import type { TextareaHTMLAttributes } from "react";
import { Field as BaseField } from "@base-ui/react/field";
import { cx } from "../../utils";

// A field's control rendered as a textarea, so it's labelled and validated like an Input
export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <BaseField.Control render={<textarea className={cx("lb-textarea", className)} {...rest} />} />;
}
