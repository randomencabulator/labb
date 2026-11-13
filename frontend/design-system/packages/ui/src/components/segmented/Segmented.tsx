"use client";

import type { ReactNode } from "react";
import { Toggle as BaseToggle } from "@base-ui/react/toggle";
import { ToggleGroup } from "@base-ui/react/toggle-group";

// One of a few, like the theme; one is always picked
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: ReactNode }[];
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <ToggleGroup
      className="lb-segmented"
      aria-label={label}
      value={[value]}
      onValueChange={(picked) => picked.length > 0 && onChange(picked[0] as T)}
    >
      {options.map((o) => (
        <BaseToggle key={o.value} value={o.value} className="lb-segmented-item">
          {o.label}
        </BaseToggle>
      ))}
    </ToggleGroup>
  );
}
