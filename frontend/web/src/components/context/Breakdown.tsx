import { useState } from "react";
import type { ContextInspection } from "../../api";

type Part = ContextInspection["parts"][number]["kind"];

const COLORS: Record<Part, string> = {
  system: "var(--series-1)",
  tools: "var(--series-2)",
  messages: "var(--series-3)",
  summary: "var(--series-4)",
  recalled: "var(--accent)",
};

export const percent = (tokens: number, window: number) => `${Math.round((tokens / window) * 100)}%`;

/** How much of the window the next model call takes, part by part */
export function ContextUsage({ seen }: { seen: ContextInspection }) {
  const { window } = seen;
  const notes = [`Summarizes at ${seen.compact_at.toLocaleString()} tokens (${percent(seen.compact_at, window)})${seen.will_compact ? ": the next message will be summarized first" : ""}`];
  if (seen.summarized) notes.push(`the summary stands in for the first ${seen.summarized} of ${seen.total_messages} messages`);
  if (seen.cleared.count) notes.push(`${seen.cleared.count} old tool result${seen.cleared.count === 1 ? "" : "s"} cleared (${seen.cleared.chars.toLocaleString()} characters)`);
  if (seen.scale !== 1) notes.push(`estimates scaled ×${seen.scale} to the last reply's real input tokens`);

  return (
    <>
      <p className="context-total">
        <strong>{seen.used.toLocaleString()}</strong> of {window.toLocaleString()} tokens ({percent(seen.used, window)}) ·{" "}
        <span className="muted">{seen.model}</span>
      </p>
      <div className="context-bar" role="img" aria-label={`${percent(seen.used, window)} of the context window used`}>
        {seen.parts.map(part => (
          <span key={part.kind} style={{ width: `${(part.tokens / window) * 100}%`, background: COLORS[part.kind] }} title={part.label} />
        ))}
        <i className="context-mark" style={{ left: `${(seen.compact_at / window) * 100}%` }} title="Summarizes here" />
      </div>
      <table className="context-parts">
        <tbody>
          {seen.parts.map(part => (
            <tr key={part.kind}>
              <td>
                <span className="swatch" style={{ background: COLORS[part.kind] }} /> {part.label}
              </td>
              <td>{part.tokens.toLocaleString()}</td>
              <td className="muted">{percent(part.tokens, window)}</td>
            </tr>
          ))}
          <tr className="muted">
            <td>
              <span className="swatch" /> Free
            </td>
            <td>{Math.max(0, window - seen.used).toLocaleString()}</td>
            <td>{percent(Math.max(0, window - seen.used), window)}</td>
          </tr>
        </tbody>
      </table>
      <p className="muted context-notes">{notes.join("; ")}.</p>
    </>
  );
}

/** What the model would get: how much of the window it takes, and the exact text */
export function Breakdown({ seen }: { seen: ContextInspection }) {
  return (
    <>
      <ContextUsage seen={seen} />
      <Section title="System prompt" tokens={seen.parts.find(p => p.kind === "system")?.tokens ?? 0}>
        {seen.system.map((section, i) => (
          <Section key={i} title={section.label} tokens={section.tokens}>
            <pre>{section.text}</pre>
          </Section>
        ))}
      </Section>
      {seen.tools.length > 0 && (
        <Section title={`Tools (${seen.tools.length})`} tokens={seen.parts.find(p => p.kind === "tools")?.tokens ?? 0}>
          <table className="context-parts">
            <tbody>
              {[...seen.tools].sort((a, b) => b.tokens - a.tokens).map(tool => (
                <tr key={tool.name}>
                  <td>
                    <code>{tool.name}</code>
                  </td>
                  <td>{tool.tokens.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}
    </>
  );
}

export function Section({ title, tokens, open, children }: { title: React.ReactNode; tokens: number; open?: boolean; children: React.ReactNode }) {
  const [shown, setShown] = useState(open ?? false);
  return (
    <details className="context-section" open={shown} onToggle={e => setShown(e.currentTarget.open)}>
      <summary>
        <span className="context-title">{title}</span>
        <span className="muted">{tokens.toLocaleString()}</span>
      </summary>
      {shown && children}
    </details>
  );
}
