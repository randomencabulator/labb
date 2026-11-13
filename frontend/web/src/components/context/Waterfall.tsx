import { useEffect, useMemo, useRef, useState } from "react";
import { type ReplyTrace, type TraceSpan, getTraces } from "../../api";

const POLL_MS = 1000;
const KINDS = [
  { id: "model", label: "Model" },
  { id: "tool", label: "Tools" },
  { id: "step", label: "Steps" },
] as const;

const seconds = (ms: number) => (ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`);
// Short enough for the axis
const tickLabel = (ms: number) => (ms === 0 ? "0" : ms < 1000 ? `${ms}ms` : `${+(ms / 1000).toFixed(1)}s`);
// Room each of the axis's labels needs
const TICK_WIDTH = 48;
const endOf = (span: TraceSpan, elapsed: number) => span.end ?? elapsed;

/** A clean step for the time axis, about `count` of them across `total` ms */
function tickStep(total: number, count: number) {
  const rough = Math.max(total, 1) / count;
  const size = 10 ** Math.floor(Math.log10(rough));
  return [1, 2, 5, 10].map(n => n * size).find(step => step >= rough) ?? rough;
}

/** How deep each span sits: what it ran inside, up to the reply */
function depths(spans: TraceSpan[]) {
  const byId = new Map(spans.map(span => [span.id, span]));
  const depth = new Map<string, number>();
  const of = (span: TraceSpan): number => {
    if (depth.has(span.id)) return depth.get(span.id)!;
    const parent = span.parent ? byId.get(span.parent) : undefined;
    const found = parent ? of(parent) + 1 : 0;
    depth.set(span.id, found);
    return found;
  };
  spans.forEach(of);
  return depth;
}

/** Spans first, then what ran inside each right under it, each level in the order it started */
function inTreeOrder(spans: TraceSpan[]) {
  const ids = new Set(spans.map(span => span.id));
  const children = new Map<string | null, TraceSpan[]>();
  for (const span of spans) {
    const parent = span.parent && ids.has(span.parent) ? span.parent : null;
    children.set(parent, [...(children.get(parent) ?? []), span]);
  }
  const ordered: TraceSpan[] = [];
  const add = (parent: string | null) => {
    for (const span of children.get(parent) ?? []) {
      ordered.push(span);
      add(span.id);
    }
  };
  add(null);
  return ordered;
}

/** Which spans ran at the same time as another without one being inside the other */
function analyze(spans: TraceSpan[], elapsed: number) {
  const byId = new Map(spans.map(span => [span.id, span]));
  const ancestors = (span: TraceSpan) => {
    const found = new Set<string>();
    for (let up = span.parent; up && !found.has(up); up = byId.get(up)?.parent ?? null) found.add(up);
    return found;
  };
  const lines = new Map(spans.map(span => [span.id, ancestors(span)]));
  const parallel = new Set<string>();
  for (let i = 0; i < spans.length; i++) {
    for (let j = i + 1; j < spans.length; j++) {
      const [a, b] = [spans[i], spans[j]];
      if (lines.get(a.id)!.has(b.id) || lines.get(b.id)!.has(a.id)) continue;
      if (a.start < endOf(b, elapsed) && b.start < endOf(a, elapsed)) {
        parallel.add(a.id);
        parallel.add(b.id);
      }
    }
  }
  // The most running at once, and the time nothing was: counting only what isn't holding another
  const holders = new Set(spans.map(span => span.parent));
  const leaves = spans.filter(span => !holders.has(span.id));
  const edges = leaves.flatMap(span => [[span.start, 1], [endOf(span, elapsed), -1]]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  let running = 0, peak = 0, idle = 0, last = 0;
  for (const [at, change] of edges) {
    if (running === 0) idle += Math.max(0, at - last);
    running += change;
    peak = Math.max(peak, running);
    last = at;
  }
  idle += Math.max(0, elapsed - last);
  return { parallel, peak, idle };
}

const total = (spans: TraceSpan[], kind: string, elapsed: number) => {
  // Time on the clock: what's nested in one of its own kind isn't counted twice
  const ranges = spans.filter(span => span.kind === kind).map(span => [span.start, endOf(span, elapsed)]).sort((a, b) => a[0] - b[0]);
  let sum = 0, reached = 0;
  for (const [start, end] of ranges) {
    if (end > reached) sum += end - Math.max(start, reached);
    reached = Math.max(reached, end);
  }
  return sum;
};

/** The reply over time: each model call, tool and step on a shared clock, so what ran when, and what
 * ran together, can be seen */
export function Waterfall({ threadId, streaming }: { threadId: string; streaming: boolean }) {
  const [traces, setTraces] = useState<ReplyTrace[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Which reply: the latest unless one was picked
  const [picked, setPicked] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [pointer, setPointer] = useState<number | null>(null);
  const tracks = useRef<HTMLSpanElement>(null);
  const [trackWidth, setTrackWidth] = useState(240);

  useEffect(() => {
    let current = true;
    const load = () =>
      getTraces(threadId).then(
        found => {
          if (!current) return;
          setTraces(found);
          setError(null);
        },
        e => current && setError(e instanceof Error ? e.message : String(e)),
      );
    load();
    const timer = streaming ? setInterval(load, POLL_MS) : undefined;
    return () => {
      current = false;
      clearInterval(timer);
    };
  }, [threadId, streaming]);
  useEffect(() => {
    setPicked(null);
    setSelected(null);
  }, [threadId]);

  const trace = traces?.find(item => item.id === picked) ?? traces?.at(-1) ?? null;
  const hasTrace = trace !== null;
  useEffect(() => {
    if (!tracks.current) return;
    const watch = new ResizeObserver(([entry]) => setTrackWidth(entry.contentRect.width));
    watch.observe(tracks.current);
    return () => watch.disconnect();
  }, [hasTrace]);
  const spans = trace?.spans ?? [];
  const elapsed = trace?.elapsed ?? 0;
  const facts = useMemo(() => analyze(spans, elapsed), [spans, elapsed]);
  const depth = useMemo(() => depths(spans), [spans]);
  const rows = useMemo(() => inTreeOrder(spans).filter(span => !hidden.has(span.kind)), [spans, hidden]);

  if (error) return <p className="error">{error}</p>;
  if (!traces) return <p className="muted">Loading…</p>;
  if (!trace) return <p className="muted">Nothing recorded yet: replies from now on show here, step by step.</p>;

  const domain = Math.max(elapsed, 1);
  const at = (ms: number) => `${(ms / domain) * 100}%`;
  const step = tickStep(domain, Math.max(1, Math.floor(trackWidth / TICK_WIDTH)));
  const ticks = Array.from({ length: Math.floor(domain / step) + 1 }, (_, i) => i * step);
  const shown = spans.find(span => span.id === selected);
  const calls = (kind: string) => spans.filter(span => span.kind === kind).length;
  const runningAt = pointer === null ? [] : spans.filter(span => span.start <= pointer && pointer <= endOf(span, elapsed));
  const index = traces.indexOf(trace);

  const movePointer = (event: React.PointerEvent) => {
    const box = tracks.current?.getBoundingClientRect();
    if (!box?.width) return;
    const share = (event.clientX - box.left) / box.width;
    setPointer(share < 0 || share > 1 ? null : share * domain);
  };

  return (
    <>
      <div className="waterfall-replies">
        <button type="button" aria-label="Earlier reply" disabled={index <= 0} onClick={() => setPicked(traces[index - 1].id)}>‹</button>
        <select aria-label="Reply" value={trace.id} onChange={e => setPicked(e.target.value)}>
          {traces.map((item, i) => <option key={item.id} value={item.id}>{i + 1}. {item.live ? "(writing) " : ""}{item.asked || "Reply"}</option>)}
        </select>
        <button type="button" aria-label="Later reply" disabled={index >= traces.length - 1} onClick={() => setPicked(traces[index + 1].id)}>›</button>
      </div>

      <p className="waterfall-facts">
        <strong>{seconds(elapsed)}</strong>{trace.live ? " so far" : ""}
        <span className="muted"> · {calls("model")} model call{calls("model") === 1 ? "" : "s"} {seconds(total(spans, "model", elapsed))}
          {" "}· {calls("tool")} tool{calls("tool") === 1 ? "" : "s"} {seconds(total(spans, "tool", elapsed))}
          {" "}· {seconds(facts.idle)} between calls</span>
      </p>
      <p className="waterfall-verdict">
        {facts.peak > 1 ? <>Up to <strong>{facts.peak}</strong> at once: the calls marked <span className="model-tag">parallel</span> overlapped.</> : <>Nothing ran in parallel: one call at a time{spans.some(span => span.parent) ? ", apart from calls made from inside a tool, shown under it" : ""}.</>}
      </p>

      <div className="waterfall-filters">
        {KINDS.map(kind => (
          <label key={kind.id}>
            <input type="checkbox" checked={!hidden.has(kind.id)} onChange={() => {
              const next = new Set(hidden);
              if (next.has(kind.id)) next.delete(kind.id);
              else next.add(kind.id);
              setHidden(next);
            }} /> <i className={`waterfall-key kind-${kind.id}`} /> {kind.label}
          </label>
        ))}
        <span className="muted"><i className="waterfall-key kind-waiting" /> Waiting for the first token</span>
      </div>

      <div className="waterfall" onPointerMove={movePointer} onPointerLeave={() => setPointer(null)}>
        <div className="waterfall-row waterfall-axis" aria-hidden>
          <span className="waterfall-name" />
          <span className="waterfall-time" />
          <span className="waterfall-track" ref={tracks}>
            {ticks.map(tick => <span key={tick} className="waterfall-tick" style={{ left: at(tick) }}>{tickLabel(tick)}</span>)}
          </span>
        </div>
        <div className="waterfall-rows">
          {rows.map(span => {
            const end = endOf(span, elapsed);
            const first = span.first_token ?? null;
            const words = span.status === "ok" ? "" : span.status;
            return (
              <button key={span.id} type="button" className={`waterfall-row${selected === span.id ? " selected" : ""}`} aria-pressed={selected === span.id}
                aria-label={`${span.name}, ${span.kind}, from ${seconds(span.start)} to ${seconds(end)}${words ? `, ${words}` : ""}${facts.parallel.has(span.id) ? ", parallel" : ""}`}
                onClick={() => setSelected(selected === span.id ? null : span.id)}>
                <span className="waterfall-name" style={{ paddingLeft: (depth.get(span.id) ?? 0) * 12 }} title={span.name}>
                  <span className="waterfall-label">{span.name}</span>
                  {facts.parallel.has(span.id) && <span className="model-tag">parallel</span>}
                  {words && <span className={`model-tag state-${span.status}`}>{words}</span>}
                </span>
                <span className="waterfall-time">{seconds(end - span.start)}</span>
                <span className="waterfall-track">
                  {ticks.map(tick => <i key={tick} className="waterfall-grid" style={{ left: at(tick) }} />)}
                  <span className={`waterfall-bar kind-${span.kind} state-${span.status}`} style={{ left: at(span.start), width: `max(2px, ${((end - span.start) / domain) * 100}%)` }}>
                    {span.kind === "model" && first !== null && end > span.start && (
                      <i className="waterfall-waiting" style={{ width: `${((first - span.start) / (end - span.start)) * 100}%` }} />
                    )}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
        {/* Lines over the rows: where the picked call starts and ends, and where the pointer is */}
        <div className="waterfall-overlay" aria-hidden>
          <span className="waterfall-name" />
          <span className="waterfall-time" />
          <span className="waterfall-track">
            {shown && <i className="waterfall-guide" style={{ left: at(shown.start) }} />}
            {shown && <i className="waterfall-guide" style={{ left: at(endOf(shown, elapsed)) }} />}
            {pointer !== null && <i className="waterfall-crosshair" style={{ left: at(pointer) }} />}
          </span>
        </div>
      </div>

      <div className="waterfall-readout" role="status">
        {pointer !== null ? (
          <>
            <strong>{seconds(Math.round(pointer))}</strong>{" "}
            <span className="muted">{runningAt.length ? `running: ${runningAt.map(span => span.name).join(", ")}` : "between calls"}</span>
          </>
        ) : !shown && <span className="muted">Point along the rows to see what was running then; click a row for its details.</span>}
      </div>

      {shown && (
        <dl className="waterfall-details">
          <dt>{shown.name}</dt><dd>{KINDS.find(kind => kind.id === shown.kind)?.label}{shown.model ? ` · ${shown.model}` : ""}</dd>
          <dt>Ran</dt><dd>{seconds(shown.start)} to {seconds(endOf(shown, elapsed))} ({seconds(endOf(shown, elapsed) - shown.start)})</dd>
          {shown.first_token != null && <><dt>Waited</dt><dd>{seconds(shown.first_token - shown.start)} for the first token, then wrote for {seconds(endOf(shown, elapsed) - shown.first_token)}</dd></>}
          {shown.input_tokens != null && <><dt>Tokens</dt><dd>{shown.input_tokens.toLocaleString()} in · {(shown.output_tokens ?? 0).toLocaleString()} out</dd></>}
          {shown.detail && <><dt>Input</dt><dd><code>{shown.detail}</code></dd></>}
          {shown.error && <><dt>Error</dt><dd className="error">{shown.error}</dd></>}
        </dl>
      )}

      <p className="muted context-notes">
        {trace.truncated && "This reply made more calls than are kept, so its last ones are missing. "}
        Steps quicker than 10 ms are left out, and the chat's title is written after the reply, so it isn't here. Replies from before this was added have no timings.
      </p>
    </>
  );
}
