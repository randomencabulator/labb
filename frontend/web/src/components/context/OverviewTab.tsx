import { useState } from "react";
import { type ContextInspection, type ContextOptions, summarizeNow } from "../../api";
import { ContextUsage, percent } from "./Breakdown";
import type { ContextTab } from "./ContextPanel";

// One thing taking this much of the window is worth pointing out
const HEAVY_SHARE = 0.25;
const LARGEST = 5;

type Item = { key: string; label: string; tokens: number; open: () => void };

export function OverviewTab({ threadId, seen, options, streaming, summarizing, onSummarizing, onSeen, onOpenMessage, onTab }: {
  threadId: string;
  seen: ContextInspection;
  options: ContextOptions;
  streaming: boolean;
  summarizing: boolean;
  onSummarizing: (summarizing: boolean) => void;
  onSeen: (seen: ContextInspection) => void;
  onOpenMessage: (index: number) => void;
  onTab: (tab: ContextTab) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  const items: Item[] = [
    ...seen.system.map(s => ({ key: `system ${s.label}`, label: `System: ${s.label}`, tokens: s.tokens, open: () => onTab("input") })),
    ...seen.tools.map(t => ({ key: `tool ${t.name}`, label: `Tool: ${t.name}`, tokens: t.tokens, open: () => onTab("tools") })),
    ...seen.messages.map(m => ({ key: `message ${m.index}`, label: `#${m.index + 1} ${m.role}${m.name ? ` ${m.name}` : ""}`, tokens: m.tokens, open: () => onOpenMessage(m.index) })),
  ].sort((a, b) => b.tokens - a.tokens);
  const heavy = items.filter(item => item.tokens >= seen.window * HEAVY_SHARE);

  const summarize = async () => {
    setConfirming(false);
    setFailed(null);
    onSummarizing(true);
    try {
      onSeen(await summarizeNow(threadId, options));
    } catch (e) {
      setFailed(e instanceof Error ? e.message : String(e));
    } finally {
      onSummarizing(false);
    }
  };

  return (
    <>
      <ContextUsage seen={seen} />
      {(seen.will_compact || seen.next_to_clear !== null || heavy.length > 0) && (
        <ul className="context-warnings">
          {seen.will_compact && <li>The next message will be summarized first.</li>}
          {seen.next_to_clear !== null && (
            <li>
              <button type="button" className="context-link" onClick={() => onOpenMessage(seen.next_to_clear!)}>#{seen.next_to_clear + 1}</button>{" "}
              will be cleared when another large tool result arrives.
            </li>
          )}
          {heavy.map(item => (
            <li key={item.key}>
              <button type="button" className="context-link" onClick={item.open}>{item.label}</button> takes {percent(item.tokens, seen.window)} of the window.
            </li>
          ))}
        </ul>
      )}

      <h3 className="context-heading">Largest</h3>
      <table className="context-parts context-largest">
        <tbody>
          {items.slice(0, LARGEST).map(item => (
            <tr key={item.key}>
              <td><button type="button" className="context-link" onClick={item.open}>{item.label}</button></td>
              <td>{item.tokens.toLocaleString()}</td>
              <td className="muted">{percent(item.tokens, seen.window)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 className="context-heading">Make room</h3>
      {confirming ? (
        <div className="context-confirm" role="group" aria-label="Summarize now?">
          <p>
            This asks the model to summarize all {seen.messages.length} messages sent now. The saved messages stay complete,
            but you won't be able to edit or regenerate anything the summary covers.
          </p>
          <div className="context-confirm-actions">
            <button type="button" className="primary" onClick={summarize}>Summarize</button>
            <button type="button" onClick={() => setConfirming(false)}>Cancel</button>
          </div>
        </div>
      ) : (
        <p className="context-make-room">
          <button type="button" disabled={streaming || summarizing || seen.messages.length === 0} onClick={() => setConfirming(true)}>
            {summarizing ? "Summarizing…" : "Summarize now"}
          </button>
          {streaming && <span className="muted">Wait for the reply to finish.</span>}
          {!streaming && seen.messages.length === 0 && <span className="muted">Everything is summarized already.</span>}
        </p>
      )}
      {failed && <p className="error">{failed}</p>}
    </>
  );
}
