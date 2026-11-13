// A run as it goes: each step's status, input and output; approvals; stopping and running again
import { type FormEvent, useEffect, useRef, useState } from "react";
import { type Definition, type Run, type StepState, studio } from "../../studioApi";
import { toast } from "../Toaster";
import { StepIcon } from "../Icons";
import { moment } from "../../format";

const STATUS: Record<string, string> = {
  queued: "Queued", running: "Running", waiting: "Waiting", done: "Done", failed: "Failed", stopped: "Stopped", skipped: "Skipped",
};

function show(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function seconds(start: string | null, end: string | null) {
  if (!start) return "";
  const ms = (end ? new Date(end) : new Date()).getTime() - new Date(start).getTime();
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

type Input = { name: string; label?: string; type?: string; required?: boolean; default?: unknown };

/** The Start step's inputs, asked before a run */
export function RunForm({ definition, onStart, onCancel, busy, label }: {
  definition: Definition;
  onStart: (inputs: Record<string, unknown>) => void;
  onCancel: () => void;
  busy: boolean;
  label: string;
}) {
  const inputs = (definition.nodes.find(n => n.type === "trigger")?.config.inputs as Input[]) ?? [];
  const [values, setValues] = useState<Record<string, unknown>>(() =>
    Object.fromEntries(inputs.map(i => [i.name, i.default ?? (i.type === "boolean" ? false : "")])),
  );
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onStart(values);
  };
  return (
    <form className="run-form" onSubmit={submit}>
      {inputs.length === 0 && <p className="muted small">It has no inputs.</p>}
      {inputs.map(input => (
        <label key={input.name} className={input.type === "boolean" ? "check" : "field"}>
          {input.type === "boolean" ? (
            <>
              <input type="checkbox" checked={Boolean(values[input.name])} onChange={e => setValues({ ...values, [input.name]: e.target.checked })} />{" "}
              {input.label || input.name}
            </>
          ) : (
            <>
              <span className="field-label">
                {input.label || input.name}
                {input.required && <span className="required"> *</span>}
              </span>
              {input.type === "long_text" ? (
                <textarea rows={4} value={String(values[input.name] ?? "")} required={input.required}
                  onChange={e => setValues({ ...values, [input.name]: e.target.value })} />
              ) : (
                <input type={input.type === "number" ? "number" : "text"} value={String(values[input.name] ?? "")} required={input.required}
                  onChange={e => setValues({ ...values, [input.name]: e.target.value })} />
              )}
            </>
          )}
        </label>
      ))}
      <div className="row">
        <button type="submit" className="primary" disabled={busy}>{label}</button>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function StepDetails({ step, run, onRerun, onDecide }: {
  step: StepState;
  run: Run;
  onRerun: () => void;
  onDecide: (approved: boolean, comment: string) => void;
}) {
  const node = run.definition?.nodes.find(n => n.id === step.node_id);
  const [comment, setComment] = useState("");
  const waitingApproval = step.status === "waiting" && node?.type === "approval";
  return (
    <div className="step-details">
      {step.error && <pre className="problem">{step.error}</pre>}
      {waitingApproval && (
        <div className="approval">
          <p className="approval-message">{(step.input as { message?: string })?.message}</p>
          <input placeholder="A note (optional)" value={comment} onChange={e => setComment(e.target.value)} />
          <div className="row">
            <button type="button" className="primary" onClick={() => onDecide(true, comment)}>Approve</button>
            <button type="button" onClick={() => onDecide(false, comment)}>Reject</button>
          </div>
        </div>
      )}
      {step.input !== null && step.input !== undefined && !waitingApproval && (
        <details>
          <summary>Input</summary>
          <pre>{show(step.input)}</pre>
        </details>
      )}
      {step.output !== null && step.output !== undefined && (
        <details open>
          <summary>Output</summary>
          <pre>{show(step.output)}</pre>
        </details>
      )}
      {step.trace.length > 0 && (
        <details>
          <summary>Tool calls and printed output</summary>
          <pre>{show(step.trace)}</pre>
        </details>
      )}
      {["done", "failed", "stopped"].includes(run.status) && node?.type !== "trigger" && (
        <button type="button" className="link" onClick={onRerun}>Run again from this step</button>
      )}
    </div>
  );
}

type Filter = "all" | "live" | "failed" | "done";
const FILTERS: [Filter, string][] = [["all", "All"], ["live", "Running or waiting"], ["failed", "Failed"], ["done", "Done"]];
const isLive = (status: string) => status === "queued" || status === "running" || status === "waiting";

/** Runs, newest first, as rows to open: in Studio's Runs, and a workflow's own in the builder */
export function RunList({ runs, onOpen, showWorkflow = true, empty = "No runs yet." }: {
  runs: Run[] | null;
  onOpen: (run: Run) => void;
  showWorkflow?: boolean;
  empty?: string;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  if (!runs) return <p className="muted small run-list-empty">Loading runs…</p>;
  const counts: Record<Filter, number> = {
    all: runs.length,
    live: runs.filter(r => isLive(r.status)).length,
    failed: runs.filter(r => r.status === "failed").length,
    done: runs.filter(r => r.status === "done").length,
  };
  const shown = runs.filter(r =>
    filter === "all" ? true : filter === "live" ? isLive(r.status) : r.status === filter);
  return (
    <div className="run-list">
      {runs.length > 0 && (
        <div className="run-filters" role="tablist" aria-label="Show">
          {FILTERS.filter(([f]) => f === "all" || counts[f] > 0).map(([f, label]) => (
            <button key={f} type="button" role="tab" aria-selected={filter === f} onClick={() => setFilter(f)}>
              {label} <span className="count">{counts[f]}</span>
            </button>
          ))}
        </div>
      )}
      <ul className="run-rows">
        {shown.map(r => (
          <li key={r.id}>
            <button type="button" className={`run-row ${r.status}`} onClick={() => onOpen(r)}>
              <span className={`run-badge ${r.status}`}>{STATUS[r.status]}</span>
              <span className="run-row-main">
                <span className="run-row-title">
                  {showWorkflow ? <>{r.emoji} {r.workflow ?? "(deleted)"}</> : moment(r.queued_at)}
                </span>
                <span className="run-row-meta">
                  {showWorkflow && <>{moment(r.queued_at)} · </>}
                  {r.trigger} · {r.version ? `v${r.version}` : "draft"}
                  {r.started_at && ` · ${seconds(r.started_at, r.finished_at)}`}
                  {r.input_tokens + r.output_tokens > 0 && ` · ${(r.input_tokens + r.output_tokens).toLocaleString()} tokens`}
                </span>
                {r.error && <span className="run-row-error">{r.error}</span>}
              </span>
              <span className="run-row-go" aria-hidden>›</span>
            </button>
          </li>
        ))}
      </ul>
      {shown.length === 0 && <p className="muted small run-list-empty">{runs.length ? "None like that." : empty}</p>}
    </div>
  );
}

export function RunPanel({ runId, onClose, onBack, onSteps, onOpenRun, onOpenChat, onFix, onFocusStep }: {
  runId: string;
  // Ask labb for a fix, for a failed run
  onFix?: (runId: string) => void;
  onClose: () => void;
  // Back to the list of runs
  onBack?: () => void;
  onSteps?: (steps: StepState[]) => void;
  onOpenRun: (runId: string) => void;
  onOpenChat?: (threadId: string) => void;
  // Show a step on the canvas
  onFocusStep?: (nodeId: string) => void;
}) {
  const [run, setRun] = useState<Run | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const opened = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    opened.current = false;
    setRun(null);
    setOpen(null);
    const load = async () => {
      try {
        const next = await studio.getRun(runId);
        if (cancelled) return;
        setRun(next);
        onSteps?.(next.steps ?? []);
        // The step that failed opens by itself, once
        const failed = (next.steps ?? []).find(s => s.status === "failed");
        if (failed && !opened.current) {
          opened.current = true;
          setOpen(failed.node_id);
        }
        // Often while it runs, now and then while it waits for an answer or its time
        if (next.status === "queued" || next.status === "running") timer = setTimeout(load, 800);
        else if (next.status === "waiting") timer = setTimeout(load, 4000);
      } catch (e) {
        if (!cancelled) toast((e as Error).message);
      }
    };
    load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [runId]);

  if (!run) return <div className="run-panel"><p className="muted">Loading the run…</p></div>;
  const order = run.definition?.nodes ?? [];
  const states = new Map((run.steps ?? []).map(s => [s.node_id, s]));
  const steps = order.map(n => ({ node: n, state: states.get(n.id) })).filter(x => x.state);
  // Steps it hasn't got to yet, while it runs
  const ahead = isLive(run.status) ? order.filter(n => !states.has(n.id)) : [];
  const live = isLive(run.status);
  const chat = (run.steps ?? []).map(s => (s.output as { thread_id?: string })?.thread_id).find(Boolean);
  const inputs = Object.entries(run.inputs ?? {}).filter(([, v]) => v !== "" && v !== null && v !== undefined);
  const current = steps.find(x => x.state!.status === "running" || x.state!.status === "waiting");

  const decide = async (nodeId: string, approved: boolean, comment: string) => {
    try {
      setRun(await studio.approve(run.id, nodeId, approved, comment));
    } catch (e) {
      toast((e as Error).message);
    }
  };
  const rerun = async (nodeId: string) => {
    try {
      onOpenRun((await studio.rerun(run.id, nodeId)).id);
    } catch (e) {
      toast((e as Error).message);
    }
  };

  return (
    <div className={`run-panel status-${run.status}`}>
      <div className="run-head">
        {onBack && <button type="button" className="icon-button" aria-label="All runs" title="All runs" onClick={onBack}>←</button>}
        <span className={`run-badge big ${run.status}`}>{STATUS[run.status]}</span>
        <span className="run-when">{moment(run.queued_at)}</span>
        <span className="spacer" />
        {live && <button type="button" onClick={() => studio.stop(run.id).catch(e => toast(e.message))}>Stop</button>}
        <button type="button" className="icon-button" aria-label="Close the run" title="Close" onClick={onClose}>×</button>
      </div>
      <div className="run-meta">
        <span>{run.version ? `v${run.version}` : "Draft"}</span>
        <span>{run.trigger}</span>
        {run.started_at && <span>{seconds(run.started_at, run.finished_at)}</span>}
        {run.input_tokens + run.output_tokens > 0 && <span>{(run.input_tokens + run.output_tokens).toLocaleString()} tokens</span>}
        <span>{steps.length} of {order.length} steps</span>
      </div>
      {live && (
        <div className="run-now">
          <span className="run-now-bar" aria-hidden />
          {current ? (
            <>{current.state!.status === "waiting" ? "Waiting at" : "Now at"} <b>{current.node.label || current.node.key}</b></>
          ) : run.status === "queued" ? "Queued: it starts in a moment" : "Working…"}
        </div>
      )}
      {run.error && <pre className="problem">{run.error}</pre>}
      {run.status === "failed" && onFix && (
        <div className="fix-row">
          <span className="small">labb can read what failed and suggest a change.</span>
          <button type="button" className="primary" onClick={() => onFix(run.id)}>Fix it</button>
        </div>
      )}
      {run.status === "done" && run.result !== null && run.result !== undefined && (
        <div className="run-result">
          <span className="field-label">Result</span>
          <pre>{show(run.result)}</pre>
        </div>
      )}
      {chat && onOpenChat && (
        <button type="button" className="link" onClick={() => onOpenChat(chat)}>Open the chat it wrote</button>
      )}
      {inputs.length > 0 && (
        <details className="run-inputs">
          <summary>Inputs ({inputs.length})</summary>
          <dl>
            {inputs.map(([k, v]) => (
              <div key={k}><dt>{k}</dt><dd>{show(v)}</dd></div>
            ))}
          </dl>
        </details>
      )}
      <ol className="run-steps">
        {steps.map(({ node, state }) => {
          const expanded = open === node.id || (state!.status === "waiting" && node.type === "approval");
          return (
            <li key={node.id} className={`run-step ${state!.status}${expanded ? " open" : ""}`}>
              <button type="button" className="run-step-head" aria-expanded={expanded}
                onClick={() => (setOpen(open === node.id ? null : node.id), onFocusStep?.(node.id))}>
                <span className={`dot ${state!.status}`} title={STATUS[state!.status]} />
                <span className="step-icon" aria-hidden><StepIcon type={node.type} /></span>
                <span className="run-step-label">{node.label || node.key}</span>
                {state!.handle && <span className="chip">{state!.handle}</span>}
                {state!.attempt > 1 && <span className="chip">try {state!.attempt}</span>}
                <span className="spacer" />
                <span className="run-step-time">{state!.status === "skipped" ? "skipped" : seconds(state!.started_at, state!.finished_at)}</span>
              </button>
              {state!.error && !expanded && <div className="run-step-error">{state!.error.split("\n")[0]}</div>}
              {expanded && (
                <StepDetails step={state!} run={run} onRerun={() => rerun(node.id)}
                  onDecide={(approved, comment) => decide(node.id, approved, comment)} />
              )}
            </li>
          );
        })}
        {ahead.map(node => (
          <li key={node.id} className="run-step ahead">
            <div className="run-step-head">
              <span className="dot" />
              <span className="step-icon" aria-hidden><StepIcon type={node.type} /></span>
              <span className="run-step-label">{node.label || node.key}</span>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
