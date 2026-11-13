import { useEffect, useState } from "react";
import { copyText } from "../copy";
import { CopyIcon } from "./Icons";

export function CopyButton({ getText, label = "Copy" }: { getText: () => string; label?: string }) {
  const [result, setResult] = useState<string | null>(null);

  useEffect(() => {
    if (!result) return;
    const timer = setTimeout(() => setResult(null), 1500);
    return () => clearTimeout(timer);
  }, [result]);

  return (
    <button
      type="button"
      className="copy"
      onClick={async () => setResult((await copyText(getText())) ? "Copied" : "Copy failed")}
    >
      <CopyIcon /> {result ?? label}
    </button>
  );
}
