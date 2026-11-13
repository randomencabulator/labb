import { useEffect, useState } from "react";
import { type ContextInspection, type ContextOptions, getContextRaw } from "../../api";
import { CopyButton } from "../CopyButton";

/** The next model call's whole input as JSON. It's long, so it's fetched here and when a reply has ended. */
export function RawTab({ threadId, seen, options, streaming }: { threadId: string; seen: ContextInspection; options: ContextOptions; streaming: boolean }) {
  const [raw, setRaw] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { model, answerMode, web, engineId } = options;

  useEffect(() => {
    if (streaming && raw !== null) return;
    let current = true;
    getContextRaw(threadId, options).then(
      found => {
        if (!current) return;
        setRaw(JSON.stringify(found, null, 2));
        setError(null);
      },
      e => current && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      current = false;
    };
  }, [threadId, streaming, model, answerMode, web, engineId, seen.total_messages, seen.summarized]);

  const download = () => {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([raw ?? ""], { type: "application/json" }));
    link.download = "context.json";
    link.click();
    URL.revokeObjectURL(link.href);
  };

  return (
    <>
      <p className="muted context-count">
        labb's input for the next call, before the provider's own formatting. Image data is left out.
        {streaming && " It updates when the reply ends."}
      </p>
      {error && <p className="error">{error}</p>}
      {!raw && !error && <p className="muted">Loading…</p>}
      {raw && (
        <>
          <div className="context-raw-actions">
            <CopyButton getText={() => raw} />
            <button type="button" onClick={download}>Download</button>
          </div>
          <pre className="context-raw">{raw}</pre>
        </>
      )}
    </>
  );
}
