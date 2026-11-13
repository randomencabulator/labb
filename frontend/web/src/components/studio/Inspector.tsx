// The builder's right-hand panel: the selected step's settings, or the workflow's own
import { useRef, useState } from "react";
import type { Agent, Model, Schedule, Skill } from "../../api";
import type { Action, Catalog, Definition, FieldSpec, StepNode, Workflow } from "../../studioApi";
import { ScheduleFields } from "../Tasks";
import { StepIcon } from "../Icons";
import { referencesFor } from "./graph";

type Lists = { agents: Agent[]; models: Model[]; skills: Skill[]; actions: Action[] };

const KEY = /^[a-z][a-z0-9_]{0,39}$/;

function browserZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** A text setting, with a menu of what it can refer to */
function TemplateInput({ value, rows, refs, onChange, label, mono }: {
  value: string;
  rows?: number;
  refs: { ref: string; label: string }[];
  onChange: (value: string) => void;
  label: string;
  mono?: boolean;
}) {
  const box = useRef<HTMLTextAreaElement & HTMLInputElement>(null);
  const insert = (ref: string) => {
    const element = box.current;
    const text = `{{${ref}}}`;
    if (!element) return onChange(value + text);
    const start = element.selectionStart ?? value.length;
    const end = element.selectionEnd ?? value.length;
    onChange(value.slice(0, start) + text + value.slice(end));
    requestAnimationFrame(() => {
      element.focus();
      element.setSelectionRange(start + text.length, start + text.length);
    });
  };
  return (
    <div className="template-input">
      {rows && rows > 1 ? (
        <textarea ref={box} rows={rows} value={value} aria-label={label} className={mono ? "mono" : undefined}
          onChange={e => onChange(e.target.value)} spellCheck={!mono} />
      ) : (
        <input ref={box} value={value} aria-label={label} onChange={e => onChange(e.target.value)} />
      )}
      {refs.length > 0 && (
        <select className="insert-ref" value="" aria-label="Insert a reference" onChange={e => e.target.value && insert(e.target.value)}>
          <option value="">{"{{ }} Insert…"}</option>
          {refs.map(r => (
            <option key={r.ref} value={r.ref}>
              {r.label} — {`{{${r.ref}}}`}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}

function ListEditor<T>({ items, onChange, blank, render, addLabel }: {
  items: T[];
  onChange: (items: T[]) => void;
  blank: () => T;
  render: (item: T, set: (item: T) => void) => React.ReactNode;
  addLabel: string;
}) {
  return (
    <div className="list-editor">
      {items.map((item, i) => (
        <div key={i} className="list-row">
          {render(item, next => onChange(items.map((x, j) => (j === i ? next : x))))}
          <button type="button" className="icon-button" aria-label="Remove" onClick={() => onChange(items.filter((_, j) => j !== i))}>
            ×
          </button>
        </div>
      ))}
      <button type="button" className="link" onClick={() => onChange([...items, blank()])}>
        + {addLabel}
      </button>
    </div>
  );
}

type Input = { name: string; label?: string; type?: string; required?: boolean; default?: unknown };
type Rule = { left: string; op: string; right: string };
type Field = { name: string; description?: string };

function FieldEditor({ field, config, set, node, catalog, lists, refs }: {
  field: FieldSpec;
  config: Record<string, unknown>;
  set: (name: string, value: unknown) => void;
  node: StepNode;
  catalog: Catalog;
  lists: Lists;
  refs: { ref: string; label: string }[];
}) {
  const value = config[field.name];
  const id = `f-${node.id}-${field.name}`;
  const text = (value as string) ?? "";
  switch (field.kind) {
    case "template":
      return <TemplateInput label={field.label} value={text} rows={field.rows ?? 1} refs={refs} onChange={v => set(field.name, v)} />;
    case "code":
      return (
        <textarea id={id} className="mono" rows={field.rows ?? 10} value={text} spellCheck={false} onChange={e => set(field.name, e.target.value)}
          onKeyDown={e => {
            if (e.key === "Tab") {
              e.preventDefault();
              const el = e.currentTarget;
              const start = el.selectionStart;
              set(field.name, text.slice(0, start) + "    " + text.slice(el.selectionEnd));
              requestAnimationFrame(() => el.setSelectionRange(start + 4, start + 4));
            }
          }} />
      );
    case "number":
      return <input id={id} type="number" min={field.min} max={field.max} value={(value as number) ?? ""}
        onChange={e => set(field.name, e.target.value === "" ? null : Number(e.target.value))} />;
    case "boolean":
      return (
        <label className="check">
          <input type="checkbox" checked={Boolean(value)} onChange={e => set(field.name, e.target.checked)} /> {field.label}
        </label>
      );
    case "select":
      return (
        <select id={id} value={text} onChange={e => set(field.name, e.target.value)}>
          {(field.options ?? []).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
      );
    case "model":
      return (
        <select id={id} value={text} onChange={e => set(field.name, e.target.value || null)}>
          <option value="">Default</option>
          {lists.models.map(m => <option key={m.spec} value={m.spec}>{m.name}</option>)}
          {text && !lists.models.some(m => m.spec === text) && <option value={text}>{text}</option>}
        </select>
      );
    case "agent":
      return (
        <select id={id} value={text} onChange={e => set(field.name, e.target.value || null)}>
          <option value="">labb (the default assistant)</option>
          {lists.agents.map(a => <option key={a.id} value={a.id}>{a.emoji} {a.name}</option>)}
        </select>
      );
    case "skill":
      return (
        <select id={id} value={text} onChange={e => set(field.name, e.target.value)}>
          <option value="">Pick a skill</option>
          {lists.skills.map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
        </select>
      );
    case "tool":
      return (
        <select id={id} value={text} onChange={e => set(field.name, e.target.value)}>
          <option value="">Pick a tool</option>
          {catalog.tools.map(t => <option key={t.name} value={t.name} title={t.description}>{t.name}</option>)}
        </select>
      );
    case "action":
      return (
        <select id={id} value={text} onChange={e => set(field.name, e.target.value)}>
          <option value="">Pick an action</option>
          {lists.actions.map(a => <option key={a.id} value={a.id}>{a.emoji} {a.name}</option>)}
        </select>
      );
    case "args": {
      const args = (value as Record<string, string>) ?? {};
      const names = node.type === "tool"
        ? catalog.tools.find(t => t.name === config.tool)?.args ?? []
        : lists.actions.find(a => a.id === config.action_id)?.config.inputs?.map(i => i.name) ?? [];
      if (!names.length) return <p className="muted small">{node.type === "tool" ? "Pick a tool first." : "Pick an action first."}</p>;
      return (
        <div className="args">
          {names.map(name => (
            <label key={name} className="arg">
              <span>{name}</span>
              <TemplateInput label={name} value={args[name] ?? ""} refs={refs} onChange={v => set(field.name, { ...args, [name]: v })} />
            </label>
          ))}
        </div>
      );
    }
    case "inputs":
      return (
        <ListEditor<Input> items={(value as Input[]) ?? []} addLabel="Add an input" onChange={v => set(field.name, v)}
          blank={() => ({ name: "", label: "", type: "text", required: false })}
          render={(item, setItem) => (
            <div className="input-row">
              <input placeholder="name" value={item.name} aria-label="Input name"
                className={item.name && !KEY.test(item.name) ? "invalid" : undefined}
                onChange={e => setItem({ ...item, name: e.target.value })} />
              <input placeholder="Label" value={item.label ?? ""} aria-label="Label" onChange={e => setItem({ ...item, label: e.target.value })} />
              <select value={item.type ?? "text"} aria-label="Type" onChange={e => setItem({ ...item, type: e.target.value })}>
                {catalog.input_types.map(t => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
              </select>
              <label className="check" title="Required">
                <input type="checkbox" checked={Boolean(item.required)} onChange={e => setItem({ ...item, required: e.target.checked })} /> needed
              </label>
              <input placeholder="Default" value={String(item.default ?? "")} aria-label="Default" onChange={e => setItem({ ...item, default: e.target.value })} />
            </div>
          )} />
      );
    case "schedule": {
      const schedule = value as Schedule | null;
      return (
        <div className="schedule-field">
          <label className="check">
            <input type="checkbox" checked={Boolean(schedule)} onChange={e => {
              set(field.name, e.target.checked ? { kind: "daily", time: "08:00" } : null);
              if (e.target.checked && !config.tz) set("tz", browserZone());
            }} /> Run on a schedule
          </label>
          {schedule && <ScheduleFields schedule={schedule} tz={(config.tz as string) || browserZone()} onChange={s => set(field.name, s)} />}
        </div>
      );
    }
    case "events": {
      const events = (value as string[]) ?? [];
      return (
        <div>
          {catalog.events.map(ev => (
            <label key={ev.name} className="check">
              <input type="checkbox" checked={events.includes(ev.name)}
                onChange={e => set(field.name, e.target.checked ? [...events, ev.name] : events.filter(x => x !== ev.name))} /> {ev.label}
            </label>
          ))}
        </div>
      );
    }
    case "fields":
      return (
        <ListEditor<Field> items={(value as Field[]) ?? []} addLabel="Add a field" onChange={v => set(field.name, v)}
          blank={() => ({ name: "", description: "" })}
          render={(item, setItem) => (
            <div className="input-row">
              <input placeholder="name" value={item.name} aria-label="Field name" onChange={e => setItem({ ...item, name: e.target.value })} />
              <input placeholder="What it is" value={item.description ?? ""} aria-label="Description" onChange={e => setItem({ ...item, description: e.target.value })} />
            </div>
          )} />
      );
    case "rules":
      return (
        <ListEditor<Rule> items={(value as Rule[]) ?? []} addLabel="Add a rule" onChange={v => set(field.name, v)}
          blank={() => ({ left: "", op: "contains", right: "" })}
          render={(item, setItem) => (
            <div className="rule-row">
              <TemplateInput label="Value" value={item.left} refs={refs} onChange={v => setItem({ ...item, left: v })} />
              <select value={item.op} aria-label="Rule" onChange={e => setItem({ ...item, op: e.target.value })}>
                {catalog.operators.map(o => <option key={o.name} value={o.name}>{o.label}</option>)}
              </select>
              {!["empty", "not_empty"].includes(item.op) && (
                <input value={item.right} aria-label="Compared to" placeholder="value" onChange={e => setItem({ ...item, right: e.target.value })} />
              )}
            </div>
          )} />
      );
    case "choices":
      return (
        <ListEditor<string> items={(value as string[]) ?? []} addLabel="Add a choice" onChange={v => set(field.name, v)} blank={() => ""}
          render={(item, setItem) => <input value={item} aria-label="Choice" onChange={e => setItem(e.target.value)} />} />
      );
    case "loop_step": {
      const type = (config.step_type as string) || "prompt";
      const inner = (value as Record<string, unknown>) ?? {};
      const innerNode = { ...node, type, config: inner };
      return (
        <div className="loop-step">
          {catalog.nodes[type].fields.map(f => (
            <div key={f.name} className="field">
              {f.kind !== "boolean" && <span className="field-label">{f.label}</span>}
              <FieldEditor field={f} config={inner} node={innerNode} catalog={catalog} lists={lists} refs={refs}
                set={(name, v) => set(field.name, { ...inner, [name]: v })} />
            </div>
          ))}
        </div>
      );
    }
    default:
      return <input id={id} value={text} onChange={e => set(field.name, e.target.value)} />;
  }
}

export function StepInspector({ node, definition, catalog, lists, problems, readOnly, onChange, onDelete }: {
  node: StepNode;
  definition: Definition;
  catalog: Catalog;
  lists: Lists;
  problems: string[];
  readOnly: boolean;
  onChange: (node: StepNode) => void;
  onDelete: () => void;
}) {
  const spec = catalog.nodes[node.type];
  const refs = referencesFor(definition, node.id);
  const [keyDraft, setKeyDraft] = useState(node.key);
  const set = (name: string, value: unknown) => onChange({ ...node, config: { ...node.config, [name]: value } });
  const shown = (field: FieldSpec) =>
    Object.entries(field.show ?? {}).every(([k, v]) => (node.config[k] ?? spec.fields.find(f => f.name === k)?.default) === v);
  const keyTaken = keyDraft !== node.key && definition.nodes.some(n => n.key === keyDraft);
  const commitKey = () => {
    if (KEY.test(keyDraft) && !keyTaken) onChange({ ...node, key: keyDraft });
    else setKeyDraft(node.key);
  };
  return (
    <fieldset className="inspector" disabled={readOnly}>
      <div className="inspector-head">
        <span className="step-icon big" aria-hidden><StepIcon type={node.type} size={22} /></span>
        <div>
          <input className="title-input" value={node.label} aria-label="Step name"
            onChange={e => onChange({ ...node, label: e.target.value })} />
          <p className="muted small">{spec.description}</p>
        </div>
      </div>
      {problems.length > 0 && (
        <ul className="problems">
          {problems.map(p => <li key={p}>{p}</li>)}
        </ul>
      )}
      {node.type !== "trigger" && (
        <label className="field">
          <span className="field-label">Key</span>
          <span className="key-input">
            <code>{"{{"}</code>
            <input value={keyDraft} aria-label="Key" className={!KEY.test(keyDraft) || keyTaken ? "invalid" : undefined}
              onChange={e => setKeyDraft(e.target.value)} onBlur={commitKey} onKeyDown={e => e.key === "Enter" && commitKey()} />
            <code>{"}}"}</code>
          </span>
          <span className="help">How later steps use its output. {keyTaken ? "Another step has this key." : ""}</span>
        </label>
      )}
      {spec.fields.filter(shown).map(field => (
        <div key={field.name} className="field">
          {field.kind !== "boolean" && (
            <span className="field-label">
              {field.label}
              {field.required && <span className="required"> *</span>}
            </span>
          )}
          <FieldEditor field={field} config={node.config} set={set} node={node} catalog={catalog} lists={lists} refs={refs} />
          {field.help && <span className="help">{field.help}</span>}
        </div>
      ))}
      {node.type === "python" && !catalog.sandbox && (
        <p className="problem-text small">This server has no sandbox (SANDBOX_URL), so Python steps can't run.</p>
      )}
      {node.type !== "trigger" && (
        <details className="advanced">
          <summary>If it fails</summary>
          <label className="field">
            <span className="field-label">Try again</span>
            <select value={node.retries ?? 0} onChange={e => onChange({ ...node, retries: Number(e.target.value) })}>
              {[0, 1, 2, 3].map(n => <option key={n} value={n}>{n === 0 ? "No" : `${n} more time${n === 1 ? "" : "s"}`}</option>)}
            </select>
          </label>
          <label className="check">
            <input type="checkbox" checked={node.on_error === "continue"}
              onChange={e => onChange({ ...node, on_error: e.target.checked ? "continue" : "stop" })} /> Go on without it (its output is empty)
          </label>
        </details>
      )}
      {node.type !== "trigger" && (
        <button type="button" className="danger-link" onClick={onDelete}>
          Delete this step
        </button>
      )}
    </fieldset>
  );
}

export function WorkflowInspector({ workflow, readOnly, onChange, onWebhook, webhookPath, onExport, onDelete, onDuplicate }: {
  workflow: Workflow;
  readOnly: boolean;
  onChange: (fields: Partial<Workflow>) => void;
  onWebhook: (on: boolean) => void;
  webhookPath: string | null;
  onExport: () => void;
  onDelete: () => void;
  onDuplicate: () => void;
}) {
  const trigger = workflow.draft.nodes.find(n => n.type === "trigger");
  return (
    <div className="inspector">
      <div className="inspector-head">
        <input className="emoji-input" value={workflow.emoji} aria-label="Emoji" disabled={readOnly}
          onChange={e => onChange({ emoji: e.target.value })} />
        <input className="title-input" value={workflow.name} aria-label="Workflow name" disabled={readOnly}
          onChange={e => onChange({ name: e.target.value })} />
      </div>
      <label className="field">
        <span className="field-label">Description</span>
        <textarea rows={3} value={workflow.description} disabled={readOnly} onChange={e => onChange({ description: e.target.value })} />
      </label>
      {!readOnly && (
        <>
          <label className="check">
            <input type="checkbox" checked={workflow.shared} onChange={e => onChange({ shared: e.target.checked })} /> Shared: everyone on
            the server can run and duplicate it
          </label>
          <label className="check">
            <input type="checkbox" checked={workflow.enabled} onChange={e => onChange({ enabled: e.target.checked })} /> On: its schedule,
            webhook and events start runs
          </label>
        </>
      )}
      <div className="field">
        <span className="field-label">Starts</span>
        <ul className="plain small">
          <li>By hand, with Run</li>
          {workflow.schedule_text && <li>{workflow.schedule_text} ({workflow.tz}){workflow.next_run ? `, next ${new Date(workflow.next_run).toLocaleString()}` : ""}</li>}
          {Boolean(trigger?.config.webhook) && <li>From its webhook</li>}
          {((trigger?.config.events as string[]) ?? []).map(e => <li key={e}>On {e}</li>)}
        </ul>
        <span className="help">Automatic starts run the published version. Change them in the Start step.</span>
      </div>
      {Boolean(trigger?.config.webhook) && !readOnly && (
        <div className="field">
          <span className="field-label">Webhook</span>
          {webhookPath ? (
            <div className="secret">
              <span className="small">POST JSON to this address; it's shown only now:</span>
              <code className="copyable">{window.location.origin + webhookPath}</code>
            </div>
          ) : (
            <p className="small muted">{workflow.webhook ? "It has an address. Making a new one replaces it." : "No address yet."}</p>
          )}
          <div className="row">
            <button type="button" onClick={() => onWebhook(true)}>{workflow.webhook ? "New address" : "Make an address"}</button>
            {workflow.webhook && <button type="button" onClick={() => onWebhook(false)}>Turn off</button>}
          </div>
        </div>
      )}
      <div className="field">
        <span className="field-label">Versions</span>
        {workflow.versions.length ? (
          <ul className="plain small">
            {workflow.versions.slice(0, 5).map(v => (
              <li key={v.version}>v{v.version} · {new Date(v.published_at).toLocaleString()}{v.version === workflow.published_version ? " (live)" : ""}</li>
            ))}
          </ul>
        ) : (
          <p className="small muted">Not published yet: only test runs of the draft.</p>
        )}
      </div>
      <div className="row wrap">
        <button type="button" onClick={onExport}>Export</button>
        <button type="button" onClick={onDuplicate}>Duplicate</button>
        {!readOnly && <button type="button" className="danger" onClick={onDelete}>Delete</button>}
      </div>
      {!workflow.mine && <p className="small muted">Shared by {workflow.owner}. Duplicate it to change it.</p>}
    </div>
  );
}
