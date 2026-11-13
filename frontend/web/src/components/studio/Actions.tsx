// Custom actions (HTTP requests by admins, saved prompts by anyone) and secrets (admins)
import { type FormEvent, useEffect, useState } from "react";
import type { Model } from "../../api";
import { type Action, type ActionInput, type Catalog, studio } from "../../studioApi";
import { toast } from "../Toaster";

type Draft = {
  id?: string;
  kind: "http" | "prompt";
  name: string;
  emoji: string;
  description: string;
  shared: boolean;
  method: string;
  url: string;
  headers: [string, string][];
  body: string;
  allow_private: boolean;
  timeout: number;
  prompt: string;
  system: string;
  model: string;
  format: "text" | "json";
  inputs: ActionInput[];
};

function toDraft(action: Action | null, kind: "http" | "prompt"): Draft {
  const c = (action?.config ?? {}) as Record<string, unknown>;
  return {
    id: action?.id,
    kind: action?.kind ?? kind,
    name: action?.name ?? "",
    emoji: action?.emoji ?? (kind === "http" ? "🌐" : "✨"),
    description: action?.description ?? "",
    shared: action?.shared ?? false,
    method: (c.method as string) ?? "POST",
    url: (c.url as string) ?? "https://",
    headers: Object.entries((c.headers as Record<string, string>) ?? {}),
    body: (c.body as string) ?? "",
    allow_private: Boolean(c.allow_private),
    timeout: (c.timeout as number) ?? 20,
    prompt: (c.prompt as string) ?? "",
    system: (c.system as string) ?? "",
    model: (c.model as string) ?? "",
    format: (c.format as "text" | "json") ?? "text",
    inputs: action?.config.inputs ?? [],
  };
}

function fromDraft(d: Draft): Partial<Action> {
  const inputs = d.inputs.filter(i => i.name.trim());
  const config = d.kind === "http"
    ? { method: d.method, url: d.url, headers: Object.fromEntries(d.headers.filter(([k]) => k.trim())), body: d.body,
        allow_private: d.allow_private, timeout: d.timeout, inputs }
    : { prompt: d.prompt, system: d.system, model: d.model || null, format: d.format, inputs };
  return { kind: d.kind, name: d.name, emoji: d.emoji, description: d.description, shared: d.shared, config };
}

function ActionEditor({ action, kind, models, onDone }: {
  action: Action | null;
  kind: "http" | "prompt";
  models: Model[];
  onDone: (changed: boolean) => void;
}) {
  const [d, setD] = useState<Draft>(() => toDraft(action, kind));
  const [testArgs, setTestArgs] = useState<Record<string, string>>({});
  const [tested, setTested] = useState<{ ok: boolean; output?: unknown; error?: string } | null>(null);
  const set = (fields: Partial<Draft>) => setD({ ...d, ...fields });

  const save = async (event: FormEvent) => {
    event.preventDefault();
    try {
      if (d.id) await studio.updateAction(d.id, fromDraft(d));
      else setD({ ...d, id: (await studio.createAction(fromDraft(d))).id });
      toast("Saved");
      onDone(true);
    } catch (e) {
      toast((e as Error).message);
    }
  };
  const test = async () => {
    if (!d.id) return toast("Save it first");
    try {
      await studio.updateAction(d.id, fromDraft(d));
      setTested(await studio.testAction(d.id, testArgs));
    } catch (e) {
      toast((e as Error).message);
    }
  };

  return (
    <form className="action-editor" onSubmit={save}>
      <div className="row">
        <input className="emoji-input" value={d.emoji} aria-label="Emoji" onChange={e => set({ emoji: e.target.value })} />
        <input className="title-input" value={d.name} placeholder={d.kind === "http" ? "Create Todoist task" : "Summarize in 3 bullets"}
          aria-label="Name" required onChange={e => set({ name: e.target.value })} />
      </div>
      <label className="field">
        <span className="field-label">What it does</span>
        <input value={d.description} onChange={e => set({ description: e.target.value })} />
      </label>
      <fieldset className="field">
        <legend>Inputs</legend>
        {d.inputs.map((input, i) => (
          <div key={i} className="input-row">
            <input placeholder="name" value={input.name} aria-label="Input name"
              onChange={e => set({ inputs: d.inputs.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })} />
            <input placeholder="What to give it" value={input.description} aria-label="Description"
              onChange={e => set({ inputs: d.inputs.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)) })} />
            <label className="check">
              <input type="checkbox" checked={input.required}
                onChange={e => set({ inputs: d.inputs.map((x, j) => (j === i ? { ...x, required: e.target.checked } : x)) })} /> needed
            </label>
            <button type="button" className="icon-button" aria-label="Remove" onClick={() => set({ inputs: d.inputs.filter((_, j) => j !== i) })}>×</button>
          </div>
        ))}
        <button type="button" className="link" onClick={() => set({ inputs: [...d.inputs, { name: "", label: "", description: "", required: false }] })}>
          + Add an input
        </button>
        <span className="help">Use them as {"{{name}}"} below{d.kind === "http" ? "; secrets as {{secrets.NAME}}" : ""}.</span>
      </fieldset>
      {d.kind === "http" ? (
        <>
          <div className="row">
            <select value={d.method} aria-label="Method" onChange={e => set({ method: e.target.value })}>
              {["GET", "POST", "PUT", "PATCH", "DELETE"].map(m => <option key={m}>{m}</option>)}
            </select>
            <input className="grow" value={d.url} aria-label="URL" onChange={e => set({ url: e.target.value })} />
          </div>
          <fieldset className="field">
            <legend>Headers</legend>
            {d.headers.map(([k, v], i) => (
              <div key={i} className="input-row">
                <input placeholder="Authorization" value={k} aria-label="Header"
                  onChange={e => set({ headers: d.headers.map((x, j) => (j === i ? [e.target.value, x[1]] : x)) })} />
                <input placeholder="Bearer {{secrets.TOKEN}}" value={v} aria-label="Value"
                  onChange={e => set({ headers: d.headers.map((x, j) => (j === i ? [x[0], e.target.value] : x)) })} />
                <button type="button" className="icon-button" aria-label="Remove" onClick={() => set({ headers: d.headers.filter((_, j) => j !== i) })}>×</button>
              </div>
            ))}
            <button type="button" className="link" onClick={() => set({ headers: [...d.headers, ["", ""]] })}>+ Add a header</button>
          </fieldset>
          {!["GET", "DELETE"].includes(d.method) && (
            <label className="field">
              <span className="field-label">Body</span>
              <textarea className="mono" rows={5} value={d.body} placeholder={'{"content": "{{title}}"}'} onChange={e => set({ body: e.target.value })} />
            </label>
          )}
          <label className="check">
            <input type="checkbox" checked={d.allow_private} onChange={e => set({ allow_private: e.target.checked })} /> It may reach this
            network's own addresses (e.g. Home Assistant); otherwise only the internet
          </label>
        </>
      ) : (
        <>
          <label className="field">
            <span className="field-label">Prompt</span>
            <textarea rows={5} value={d.prompt} required onChange={e => set({ prompt: e.target.value })} />
          </label>
          <label className="field">
            <span className="field-label">Instructions</span>
            <textarea rows={2} value={d.system} onChange={e => set({ system: e.target.value })} />
          </label>
          <div className="row">
            <select value={d.model} aria-label="Model" onChange={e => set({ model: e.target.value })}>
              <option value="">Default model</option>
              {models.map(m => <option key={m.spec} value={m.spec}>{m.name}</option>)}
            </select>
            <select value={d.format} aria-label="Answer as" onChange={e => set({ format: e.target.value as "text" | "json" })}>
              <option value="text">Text</option>
              <option value="json">JSON</option>
            </select>
            <label className="check">
              <input type="checkbox" checked={d.shared} onChange={e => set({ shared: e.target.checked })} /> Shared
            </label>
          </div>
        </>
      )}
      {d.id && (
        <fieldset className="field">
          <legend>Try it</legend>
          {d.inputs.filter(i => i.name).map(i => (
            <label key={i.name} className="arg">
              <span>{i.name}</span>
              <input value={testArgs[i.name] ?? ""} onChange={e => setTestArgs({ ...testArgs, [i.name]: e.target.value })} />
            </label>
          ))}
          <button type="button" onClick={test}>Run it once</button>
          {tested && <pre className={tested.ok ? "" : "problem"}>{tested.ok ? JSON.stringify(tested.output, null, 2) : tested.error}</pre>}
        </fieldset>
      )}
      <div className="row">
        <button type="submit" className="primary">Save</button>
        <button type="button" onClick={() => onDone(false)}>Close</button>
      </div>
    </form>
  );
}

function Secrets() {
  const [secrets, setSecrets] = useState<{ name: string; updated_at: string }[]>([]);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  useEffect(() => {
    studio.secrets().then(setSecrets, e => toast(e.message));
  }, []);
  const add = async (event: FormEvent) => {
    event.preventDefault();
    try {
      setSecrets(await studio.putSecret(name.trim(), value));
      setName("");
      setValue("");
    } catch (e) {
      toast((e as Error).message);
    }
  };
  return (
    <section className="secrets">
      <h3>Secrets</h3>
      <p className="muted small">
        For HTTP actions, as {"{{secrets.NAME}}"}. Encrypted on the server, never shown again, never given to models or Python, and
        masked in runs.
      </p>
      <ul className="plain">
        {secrets.map(s => (
          <li key={s.name} className="row">
            <code>{s.name}</code>
            <span className="muted small">changed {new Date(s.updated_at).toLocaleDateString()}</span>
            <button type="button" className="link" onClick={async () => {
              if (!window.confirm(`Delete ${s.name}? Actions that use it will fail.`)) return;
              await studio.removeSecret(s.name).catch(e => toast(e.message));
              setSecrets(secrets.filter(x => x.name !== s.name));
            }}>Delete</button>
          </li>
        ))}
      </ul>
      <form className="row" onSubmit={add}>
        <input placeholder="TODOIST_TOKEN" value={name} aria-label="Name" onChange={e => setName(e.target.value.toUpperCase())} required />
        <input type="password" placeholder="Value" value={value} aria-label="Value" autoComplete="off" onChange={e => setValue(e.target.value)} required />
        <button type="submit">Save secret</button>
      </form>
      <p className="muted small">Saving a name that's there replaces its value.</p>
    </section>
  );
}

export function ActionsTab({ catalog, actions, models, onChanged }: {
  catalog: Catalog;
  actions: Action[];
  models: Model[];
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState<{ action: Action | null; kind: "http" | "prompt" } | null>(null);
  return (
    <div className="actions-tab">
      <p className="muted intro">
        Actions are steps of your own for workflows: HTTP requests to other services (admins make them, for everyone) and saved
        prompts.
      </p>
      {editing ? (
        <ActionEditor action={editing.action} kind={editing.kind} models={models}
          onDone={changed => (setEditing(null), changed && onChanged())} />
      ) : (
        <>
          <div className="row">
            {catalog.is_admin && <button type="button" className="primary" onClick={() => setEditing({ action: null, kind: "http" })}>New HTTP action</button>}
            <button type="button" onClick={() => setEditing({ action: null, kind: "prompt" })}>New prompt action</button>
          </div>
          <div className="workflow-grid">
            {actions.map(a => {
              const editable = a.kind === "http" ? catalog.is_admin : a.mine;
              return (
                <article key={a.id} className={`workflow-card${editable ? "" : " static"}`} onClick={() => editable && setEditing({ action: a, kind: a.kind })}>
                  <div className="card-head">
                    <span className="emoji">{a.emoji}</span>
                    <b>{a.name}</b>
                  </div>
                  <p className="small muted">{a.description}</p>
                  <div className="chips">
                    <span className="chip">{a.kind === "http" ? "HTTP" : "Prompt"}</span>
                    {(a.config.inputs ?? []).map(i => <span key={i.name} className="chip">{i.name}</span>)}
                    {!a.mine && a.owner && <span className="chip">By {a.owner}</span>}
                  </div>
                  {editable && (
                    <button type="button" className="link danger-link" onClick={async e => {
                      e.stopPropagation();
                      if (!window.confirm(`Delete ${a.name}? Workflows that use it will stop working.`)) return;
                      await studio.removeAction(a.id).catch(err => toast(err.message));
                      onChanged();
                    }}>Delete</button>
                  )}
                </article>
              );
            })}
            {actions.length === 0 && <p className="muted">No actions yet.</p>}
          </div>
          {catalog.is_admin && <Secrets />}
        </>
      )}
    </div>
  );
}
