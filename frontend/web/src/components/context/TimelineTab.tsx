import { useState } from "react";
import type { ContextInspection } from "../../api";
import { Waterfall } from "./Waterfall";

type Call = ContextInspection["timeline"][number];

const HEIGHT = 200;
const TOP = 16;
const BOTTOM = 22;
const LEFT = 44;
const RIGHT = 8;
const PLOT = HEIGHT - TOP - BOTTOM;
// A call's column, and each of its two bars at most
const MIN_SLOT = 14;
const MAX_BAR = 24;
const GAP = 2;

const tokens = (n: number | null) => (n === null ? "not reported" : n.toLocaleString());
const compact = (n: number) => (n >= 1_000_000 ? `${+(n / 1_000_000).toFixed(1)}M` : n >= 10_000 ? `${Math.round(n / 1000)}K` : n.toLocaleString());

/** How far the input fell from the call before, if it did. Earlier summaries and clearing leave no
 * record, so this says what the numbers show and not why. */
function fellBy(calls: Call[], i: number) {
  const now = calls[i].input_tokens;
  const before = calls.slice(0, i).reverse().find(call => call.input_tokens !== null)?.input_tokens;
  return now !== null && before != null && now < before ? before - now : 0;
}

/** A bar with a rounded top, square at the baseline */
function bar(x: number, width: number, height: number, baseline: number) {
  const r = Math.min(4, width / 2, height);
  return `M${x},${baseline}V${baseline - height + r}Q${x},${baseline - height} ${x + r},${baseline - height}H${x + width - r}Q${x + width},${baseline - height} ${x + width},${baseline - height + r}V${baseline}Z`;
}

/** A reply over time, or the chat's tokens call by call */
export function TimelineTab({ threadId, seen, streaming, onOpenMessage }: { threadId: string; seen: ContextInspection; streaming: boolean; onOpenMessage: (index: number) => void }) {
  const [view, setView] = useState<"waterfall" | "tokens">("waterfall");
  return (
    <>
      <div className="context-views" role="radiogroup" aria-label="Timeline view">
        {(["waterfall", "tokens"] as const).map(item => (
          <button key={item} type="button" role="radio" aria-checked={view === item} className={view === item ? "active" : ""} onClick={() => setView(item)}>
            {item === "waterfall" ? "Waterfall" : "Tokens"}
          </button>
        ))}
      </div>
      {view === "waterfall" ? <Waterfall threadId={threadId} streaming={streaming} /> : <TokensChart seen={seen} onOpenMessage={onOpenMessage} />}
    </>
  );
}

/** The real tokens of each saved reply's model call, against the window */
function TokensChart({ seen, onOpenMessage }: { seen: ContextInspection; onOpenMessage: (index: number) => void }) {
  const [active, setActive] = useState<number | null>(null);
  const calls = seen.timeline;
  if (calls.length === 0) return <p className="muted">No replies yet.</p>;

  const most = Math.max(seen.window, ...calls.map(call => call.input_tokens ?? 0));
  const y = (n: number) => (n / most) * PLOT;
  const baseline = TOP + PLOT;
  const slot = Math.max(MIN_SLOT, Math.min(2 * MAX_BAR + 3 * GAP, 480 / calls.length));
  const width = LEFT + RIGHT + slot * calls.length;
  const barWidth = Math.min(MAX_BAR, (slot - 3 * GAP) / 2);
  // The summary covers the first messages, so these calls come first
  const covered = calls.filter(call => call.summarized).length;
  // Its label reads into whichever side has room
  const labelAfter = covered <= calls.length / 2;
  const shown = active === null ? null : calls[active];
  const lines = [
    { value: seen.window, label: "Window" },
    { value: seen.compact_at, label: "Summarizes" },
  ];
  const reported = calls.filter(call => call.input_tokens !== null).length;

  return (
    <>
      <div className="context-legend" aria-hidden>
        <span><i style={{ background: "var(--series-1)" }} /> Input</span>
        <span><i style={{ background: "var(--series-3)" }} /> Output</span>
      </div>
      <div className="context-chart">
        <svg viewBox={`0 0 ${width} ${HEIGHT}`} width={width} height={HEIGHT} role="img"
          aria-label={`Input and output tokens of ${calls.length} model calls; the table below has the numbers`} onPointerLeave={() => setActive(null)}>
          <line x1={LEFT} x2={width - RIGHT} y1={baseline} y2={baseline} className="context-axis" />
          {lines.map(line => (
            <g key={line.label}>
              <line x1={LEFT} x2={width - RIGHT} y1={baseline - y(line.value)} y2={baseline - y(line.value)} className="context-axis" />
              <text x={LEFT - 4} y={baseline - y(line.value) + 3} textAnchor="end" className="context-tick">{compact(line.value)}</text>
              <text x={LEFT + 4} y={baseline - y(line.value) - 3} className="context-tick">{line.label}</text>
            </g>
          ))}
          <text x={LEFT - 4} y={baseline + 3} textAnchor="end" className="context-tick">0</text>
          {covered > 0 && (
            <g>
              <line x1={LEFT + slot * covered} x2={LEFT + slot * covered} y1={TOP} y2={baseline} className="context-boundary" />
              <text x={LEFT + slot * covered + (labelAfter ? 4 : -4)} y={TOP + PLOT / 2} textAnchor={labelAfter ? "start" : "end"} className="context-tick">
                {labelAfter ? "← in the summary" : "in the summary →"}
              </text>
            </g>
          )}
          {calls.map((call, i) => {
            const x = LEFT + slot * i;
            const left = x + (slot - 2 * barWidth - GAP) / 2;
            return (
              <g key={call.index} className={active === i ? "context-call active" : "context-call"}>
                <rect x={x} y={TOP} width={slot} height={PLOT + BOTTOM} className="context-hit" tabIndex={0} role="button"
                  aria-label={`Call ${i + 1}: input ${tokens(call.input_tokens)}, output ${tokens(call.output_tokens)}. Open message ${call.index + 1}.`}
                  onPointerEnter={() => setActive(i)} onFocus={() => setActive(i)} onBlur={() => setActive(null)}
                  onClick={() => onOpenMessage(call.index)}
                  onKeyDown={event => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onOpenMessage(call.index);
                    }
                  }} />
                {call.input_tokens !== null && <path d={bar(left, barWidth, Math.max(1, y(call.input_tokens)), baseline)} fill="var(--series-1)" pointerEvents="none" />}
                {call.output_tokens !== null && <path d={bar(left + barWidth + GAP, barWidth, Math.max(1, y(call.output_tokens)), baseline)} fill="var(--series-3)" pointerEvents="none" />}
                {call.input_tokens === null && <text x={x + slot / 2} y={baseline - 4} textAnchor="middle" className="context-tick" pointerEvents="none">–</text>}
              </g>
            );
          })}
        </svg>
      </div>

      <div className="context-readout" role="status">
        {shown ? (
          <>
            <strong>{tokens(shown.input_tokens)}</strong> input · <strong>{tokens(shown.output_tokens)}</strong> output
            {shown.cached_tokens ? <> · <strong>{shown.cached_tokens.toLocaleString()}</strong> from cache</> : null}
            <span className="muted"> · #{shown.index + 1}{shown.model ? ` · ${shown.model}` : ""}{shown.tool_calls.length ? ` → ${shown.tool_calls.join(", ")}` : ""}</span>
            {fellBy(calls, active!) > 0 && <div className="muted">Input fell by {fellBy(calls, active!).toLocaleString()} from the call before.</div>}
          </>
        ) : (
          <span className="muted">Point at a call for its numbers; click it to open its message.</span>
        )}
      </div>

      <p className="muted context-notes">
        The real tokens each reply's model call reported{reported < calls.length ? `, for the ${reported} of ${calls.length} calls that reported them` : ""}. A summary's own call and an engine's
        side steps aren't saved as replies, so they aren't here. The lines are for {seen.model}.
      </p>

      <details className="context-section">
        <summary><span className="context-title">Table</span></summary>
        <table className="context-parts context-calls">
          <thead>
            <tr><th>Message</th><th>Input</th><th>Output</th><th>Cached</th><th>Tools</th></tr>
          </thead>
          <tbody>
            {calls.map((call, i) => (
              <tr key={call.index}>
                <td>
                  <button type="button" className="context-link" onClick={() => onOpenMessage(call.index)}>#{call.index + 1}</button>
                  {call.summarized && <span className="model-tag">in the summary</span>}
                </td>
                <td title={fellBy(calls, i) ? `Fell by ${fellBy(calls, i).toLocaleString()} from the call before` : undefined}>
                  {fellBy(calls, i) > 0 && "↓ "}{tokens(call.input_tokens)}
                </td>
                <td>{tokens(call.output_tokens)}</td>
                <td>{call.cached_tokens === null ? "–" : call.cached_tokens.toLocaleString()}</td>
                <td>{call.tool_calls.join(", ") || "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </>
  );
}
