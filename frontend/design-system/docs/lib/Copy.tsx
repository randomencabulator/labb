"use client";

import { useState } from "react";

function useCopy(ms: number) {
  const [copied, setCopied] = useState(false);
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), ms);
    } catch {
      // Clipboard can be blocked; the text is still there to select
    }
  };
  return [copied, copy] as const;
}

export function CopyButton({ text }: { text: string }) {
  const [copied, copy] = useCopy(1500);
  return (
    <button type="button" onClick={() => copy(text)}>
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

// A value with a button to copy it, like a token's variable name
export function CopyValue({ value }: { value: string }) {
  const [copied, copy] = useCopy(1200);
  return (
    <button type="button" className="docs-copy-value" title="Copy" onClick={() => copy(value)}>
      {copied ? "Copied" : value}
    </button>
  );
}
