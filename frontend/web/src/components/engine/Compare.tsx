// The same prompts through several engines, side by side. Nothing here is kept.
import { useEffect, useMemo, useRef, useState } from "react";
import { type ChatEvent, type Engine, type PromptSet, type Stats, endTrial, engineApi, streamChat } from "../../api";
import { seconds } from "../../format";
import { Markdown } from "../Markdown";
import { toast } from "../Toaster";

type Cell = { text: string; stats?: Stats; error?: string; running: boolean; tools: string[] };
type Results = Record<string, Cell>;
const key = (prompt: number, engine: string) => `${prompt}:${engine}`;
const number = new Intl.NumberFormat();

export function Compare({ engines, model, onBack }: { engines: Engine[]; model: string | null; onBack: () => void }) {
  const usable = engines.filter(h => !h.web_problems.length);
  const [chosen, setChosen] = useState<string[]>(() => usable.slice(0, 2).map(h => h.id));
  const [text, setText] = useState("");
  const [sets, setSets] = useState<PromptSet[]>([]);
  const [setId, setSetId] = useState<string | null>(null);
  const [results, setResults] = useState<Results>({});
  const [ran, setRan] = useState<{ prompts: string[]; ids: string[] } | null>(null);
  const [running, setRunning] = useState(false);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    engineApi.promptSets().then(setSets, () => {});
    return () => controller.current?.abort();
  }, []);

  const prompts = useMemo(() => text.split(/\n\s*\n|\n(?=\S)/).map(p => p.trim()).filter(Boolean), [text]);
  const toggle = (id: string) => setChosen(now => (now.includes(id) ? now.filter(x => x !== id) : [...now, id]));

  const run = async () => {
    const abort = new AbortController();
    controller.current = abort;
    setRunning(true);
    setResults({});
    setRan({ prompts, ids: chosen });
    const update = (at: string, change: Partial<Cell>) =>
      setResults(now => ({ ...now, [at]: { ...(now[at] ?? { text: "", running: true, tools: [] }), ...change } }));
    // One at a time: replies compete for the model otherwise, and the times wouldn't compare
    for (let p = 0; p < prompts.length; p++) {
      for (const id of chosen) {
        if (abort.signal.aborted) break;
        const at = key(p, id);
        let reply = "";
        const tools: string[] = [];
        let thread: string | null = null;
        update(at, { running: true });
        try {
          await streamChat(prompts[p], null, { trial: true, engineId: id, model: model ?? undefined }, (event: ChatEvent) => {
            if (event.type === "thread") thread = event.thread_id;
            else if (event.type === "token") update(at, { text: (reply += event.content) });
            else if (event.type === "tool_call") {
              tools.push(event.name);
              update(at, { tools: [...tools] });
            }
            else if (event.type === "error") update(at, { error: event.message });
            else if (event.type === "done" || event.type === "stopped") update(at, { stats: event.stats });
          }, abort.signal);
        } catch (e) {
          if (!abort.signal.aborted) update(at, { error: (e as Error).message });
        } finally {
          update(at, { running: false });
          if (thread) endTrial(thread);
        }
      }
    }
    setRunning(false);
  };

  const saveSet = async () => {
    const current = sets.find(s => s.id === setId);
    const name = prompt("Name this set of prompts", current?.name ?? "");
    if (!name) return;
    try {
      const saved = await engineApi.savePromptSet(current?.id ?? null, name, prompts);
      setSets(now => [saved, ...now.filter(s => s.id !== saved.id)]);
      setSetId(saved.id);
      toast(`Saved ${saved.name}`);
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const name = (id: string) => engines.find(h => h.id === id)?.name ?? id;

  return (
    <div className="page-inner engine-compare">
      <div className="editor-head">
        <button type="button" className="link" onClick={onBack}>← All engines</button>
        <h3>Compare engines</h3>
      </div>
      <p className="hint">The same prompts go through each engine in a new conversation, one reply at a time, with the model picked in the chat. They use tokens like any reply, and aren't kept.</p>
      <fieldset className="field">
        <legend>Engines</legend>
        <div className="compare-picks">
          {usable.map(h => (
            <label key={h.id} className="check">
              <input type="checkbox" checked={chosen.includes(h.id)} onChange={() => toggle(h.id)} />
              <span>{h.name}</span>
            </label>
          ))}
        </div>
        {usable.length < 2 && <p className="hint">Make a second engine to compare with: fork the default and change something.</p>}
      </fieldset>
      <label className="field">
        <span>Prompts <small>one per line, or a blank line between longer ones</small></span>
        <textarea rows={5} value={text} onChange={e => setText(e.target.value)} placeholder={"Fix the failing test in test_login.py\nExplain what this project does"} />
      </label>
      <div className="editor-actions">
        {running ? (
          <button type="button" onClick={() => controller.current?.abort()}>Stop</button>
        ) : (
          <button type="button" className="primary" disabled={!prompts.length || !chosen.length} onClick={run}>
            Run {prompts.length * chosen.length || ""} {prompts.length * chosen.length === 1 ? "reply" : "replies"}
          </button>
        )}
        <select aria-label="A saved set of prompts" value={setId ?? ""} onChange={e => {
          const picked = sets.find(s => s.id === e.target.value);
          setSetId(picked?.id ?? null);
          if (picked) setText(picked.prompts.join("\n\n"));
        }}>
          <option value="">Saved sets…</option>
          {sets.map(s => <option key={s.id} value={s.id}>{s.name} ({s.prompts.length})</option>)}
        </select>
        <button type="button" disabled={!prompts.length} onClick={saveSet}>Save the prompts</button>
        {setId && (
          <button type="button" className="danger" onClick={async () => {
            await engineApi.deletePromptSet(setId);
            setSets(now => now.filter(s => s.id !== setId));
            setSetId(null);
          }}>Delete the set</button>
        )}
      </div>
      {ran && <Totals ran={ran} results={results} name={name} />}
      {ran?.prompts.map((asked, p) => (
        <section key={p} className="compare-prompt">
          <h4>{asked}</h4>
          <div className="compare-row" style={{ gridTemplateColumns: `repeat(${ran.ids.length}, minmax(0, 1fr))` }}>
            {ran.ids.map(id => {
              const cell = results[key(p, id)];
              return (
                <article key={id} className="compare-cell">
                  <header><b>{name(id)}</b> {cell?.running && <span className="muted">writing…</span>}</header>
                  {cell?.error && <div className="problem">{cell.error}</div>}
                  {cell?.text && <div className="compare-reply"><Markdown text={cell.text} /></div>}
                  {!cell && <span className="muted">Waiting</span>}
                  {cell?.tools.length ? <p className="muted compare-tools">{cell.tools.join(", ")}</p> : null}
                  {cell?.stats && <p className="muted compare-stats">{seconds(cell.stats.duration_ms)} · {number.format(cell.stats.input_tokens)} in / {number.format(cell.stats.output_tokens)} out · {cell.stats.llm_calls} model call{cell.stats.llm_calls === 1 ? "" : "s"} · {cell.stats.tool_calls} tool call{cell.stats.tool_calls === 1 ? "" : "s"}</p>}
                </article>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}

function Totals({ ran, results, name }: { ran: { prompts: string[]; ids: string[] }; results: Results; name: (id: string) => string }) {
  const rows = ran.ids.map(id => {
    const cells = ran.prompts.map((_, p) => results[key(p, id)]).filter(Boolean);
    const stats = cells.flatMap(c => (c.stats ? [c.stats] : []));
    const sum = (pick: (s: Stats) => number) => stats.reduce((total, s) => total + pick(s), 0);
    return { id, replies: stats.length, ms: sum(s => s.duration_ms), input: sum(s => s.input_tokens), output: sum(s => s.output_tokens), calls: sum(s => s.llm_calls), tools: sum(s => s.tool_calls), errors: cells.filter(c => c.error).length };
  });
  return (
    <table className="compare-totals">
      <thead><tr><th>Engine</th><th>Replies</th><th>Time</th><th>Tokens in</th><th>Tokens out</th><th>Model calls</th><th>Tool calls</th><th>Errors</th></tr></thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.id}><td>{name(r.id)}</td><td>{r.replies}</td><td>{seconds(r.ms)}</td><td>{number.format(r.input)}</td><td>{number.format(r.output)}</td><td>{r.calls}</td><td>{r.tools}</td><td>{r.errors}</td></tr>
        ))}
      </tbody>
    </table>
  );
}
