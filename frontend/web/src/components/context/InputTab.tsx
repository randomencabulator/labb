import { useEffect, useMemo, useRef, useState } from "react";
import { type ContextInspection, type SavedMessage, getSavedMessage } from "../../api";
import { CopyButton } from "../CopyButton";
import { Section } from "./Breakdown";

type Row = {
  key: string;
  role: string;
  order: number;
  tokens: number;
  title: React.ReactNode;
  // What search looks through, and what Copy gives
  text: string;
  body: React.ReactNode;
  index?: number;
};

const argsOf = (args: unknown) => JSON.stringify(args, null, 2);

/** A message as it's saved, read when asked for: the server keeps it, and it can be long */
function SavedText({ threadId, number }: { threadId: string; number: number }) {
  const [saved, setSaved] = useState<SavedMessage | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    setSaved(null);
    setError(null);
    getSavedMessage(threadId, number).then(
      found => current && setSaved(found),
      e => current && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      current = false;
    };
  }, [threadId, number]);
  if (error) return <p className="error">{error}</p>;
  if (!saved) return <p className="muted">Loading…</p>;
  return (
    <>
      <p className="muted context-saved-head">
        #{saved.index + 1} {saved.role}{saved.name ? ` ${saved.name}` : ""}{saved.images ? ` · ${saved.images} image${saved.images === 1 ? "" : "s"}` : ""}
      </p>
      {saved.text && <pre>{saved.text}</pre>}
      {saved.tool_calls.map((call, i) => <pre key={i} className="muted">{call.name}({argsOf(call.args)})</pre>)}
    </>
  );
}

function ShowSaved({ threadId, number }: { threadId: string; number: number }) {
  const [shown, setShown] = useState(false);
  return shown ? <SavedText threadId={threadId} number={number} /> : <button type="button" onClick={() => setShown(true)}>Show the saved text</button>;
}

/** The messages a summary stands in for: the model doesn't get them, and they can be read one by one */
function SummarizedRange({ threadId, count, focus }: { threadId: string; count: number; focus: number | null }) {
  const [number, setNumber] = useState(focus ?? count - 1);
  useEffect(() => {
    if (focus !== null) setNumber(focus);
  }, [focus]);
  return (
    <>
      <div className="context-stepper">
        <button type="button" disabled={number <= 0} onClick={() => setNumber(number - 1)}>Earlier</button>
        <span className="muted">#{number + 1} of {count}</span>
        <button type="button" disabled={number >= count - 1} onClick={() => setNumber(number + 1)}>Later</button>
      </div>
      <SavedText threadId={threadId} number={number} />
    </>
  );
}

/** Everything the next model call is sent, in order */
export function InputTab({ threadId, seen, focus }: { threadId: string; seen: ContextInspection; focus: number | null }) {
  const [query, setQuery] = useState("");
  const [role, setRole] = useState("all");
  const [byTokens, setByTokens] = useState(false);
  const opened = useRef<HTMLDivElement>(null);
  const focusSummarized = focus !== null && focus < seen.summarized;

  // Something was picked in another tab: it's shown whatever was being looked for
  useEffect(() => {
    if (focus === null) return;
    setQuery("");
    setRole("all");
    setByTokens(false);
  }, [focus]);
  useEffect(() => {
    if (focus !== null) opened.current?.scrollIntoView({ block: "center" });
  }, [focus]);

  const rows = useMemo<Row[]>(() => {
    const summaryTokens = seen.parts.find(p => p.kind === "summary")?.tokens ?? 0;
    const system: Row[] = seen.system.map((section, i) => ({
      key: `system ${i}`, role: "system", order: i, tokens: section.tokens, title: <>System: {section.label}</>, text: section.text, body: <pre>{section.text}</pre>,
    }));
    const summary: Row[] = seen.summary ? [{
      key: "summary", role: "summary", order: system.length, tokens: summaryTokens,
      title: seen.summarized ? `Summary of messages 1–${seen.summarized}` : "Summary", text: seen.summary, body: <pre>{seen.summary}</pre>,
    }] : [];
    const messages: Row[] = seen.messages.map(m => {
      const calls = m.tool_calls.map(call => `${call.name}(${argsOf(call.args)})`);
      return {
        key: `message ${m.index}`,
        role: m.role,
        order: system.length + 1 + m.index,
        tokens: m.tokens,
        index: m.index,
        text: [m.text, ...calls].filter(Boolean).join("\n\n"),
        title: (
          <>
            #{m.index + 1} {m.role}
            {m.name && <code> {m.name}</code>}
            {m.cleared && <span className="model-tag" title={`${m.original_chars?.toLocaleString()} characters, replaced with a note`}>cleared</span>}
            {m.recalled && <span className="model-tag">+ memories</span>}
            {m.reminder && <span className="model-tag" title="Added for this call only: the model sees it, the chat doesn't">+ reminder</span>}
            {m.images > 0 && <span className="model-tag">{m.images} image{m.images === 1 ? "" : "s"}</span>}
            {m.tool_calls.map((call, i) => <code key={i} className="muted"> → {call.name}</code>)}
          </>
        ),
        body: (
          <>
            {m.text && <pre>{m.text}</pre>}
            {calls.map((call, i) => <pre key={i} className="muted">{call}</pre>)}
            {m.cleared && <ShowSaved threadId={threadId} number={m.index} />}
          </>
        ),
      };
    });
    return [...system, ...summary, ...messages];
  }, [seen, threadId]);

  const roles = [...new Set(rows.map(row => row.role))];
  const words = query.trim().toLowerCase();
  const shown = rows
    .filter(row => (role === "all" || row.role === role) && (!words || row.text.toLowerCase().includes(words)))
    .sort((a, b) => (byTokens ? b.tokens - a.tokens : a.order - b.order));

  return (
    <>
      <div className="context-filters">
        <input type="search" placeholder="Search the input" aria-label="Search the input" value={query} onChange={e => setQuery(e.target.value)} />
        <select aria-label="Show" value={role} onChange={e => setRole(e.target.value)}>
          <option value="all">Everything</option>
          {roles.map(item => <option key={item} value={item}>{item}</option>)}
        </select>
        <select aria-label="Order" value={byTokens ? "tokens" : "order"} onChange={e => setByTokens(e.target.value === "tokens")}>
          <option value="order">As sent</option>
          <option value="tokens">Largest first</option>
        </select>
      </div>
      <p className="muted context-count">{shown.length} of {rows.length} items · tokens are estimates</p>

      {seen.summarized > 0 && !words && role === "all" && (
        <div ref={focusSummarized ? opened : undefined}>
          <Section key={focusSummarized ? `range ${focus}` : "range"} open={focusSummarized} tokens={0}
            title={<>Messages 1–{seen.summarized} <span className="model-tag">not sent: in the summary</span></>}>
            <SummarizedRange threadId={threadId} count={seen.summarized} focus={focusSummarized ? focus : null} />
          </Section>
        </div>
      )}
      {shown.map(row => {
        const focused = row.index !== undefined && row.index === focus;
        return (
          <div key={row.key} ref={focused ? opened : undefined} className="context-row">
            <Section key={focused ? "focused" : "row"} title={row.title} tokens={row.tokens} open={focused}>
              {row.body}
              {row.text && <CopyButton getText={() => row.text} />}
            </Section>
          </div>
        );
      })}
      {shown.length === 0 && <p className="muted">Nothing matches.</p>}
    </>
  );
}
