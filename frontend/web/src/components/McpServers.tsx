// Settings → MCP servers (admins): the servers whose tools the web chat offers. The ones added, how
// they're doing, and adding more: well-known ones and a search of the MCP Registry, each set up with
// a short form; or by hand, as JSON
import { type FormEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { type McpCatalog, type McpEntry, type McpOption, type McpSettings, getMcpCatalog, getMcpSettings, saveMcpSettings } from "../api";
import { toast } from "./Toaster";

const EXAMPLE = `{
  "time": {"command": "uvx", "args": ["mcp-server-time"]},
  "docs": {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer …"}}
}`;

type Servers = Record<string, Record<string, unknown>>;

const PLACEHOLDER = /\{\{([A-Za-z0-9_]+)\}\}/g;

/**
 * An option's config with the answers in: {{KEY}} becomes its value. An env var, header or
 * argument whose answer was left empty (an optional one) is left out
 */
export function fill(config: unknown, values: Record<string, string>): unknown {
  const empty = (text: string) => [...text.matchAll(PLACEHOLDER)].some(m => !values[m[1]]?.trim());
  const put = (text: string) => text.replace(PLACEHOLDER, (_, key) => values[key]?.trim() ?? "");
  if (typeof config === "string") return put(config);
  if (Array.isArray(config)) return config.filter(item => typeof item !== "string" || !empty(item)).map(item => fill(item, values));
  if (config && typeof config === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(config)) {
      if (typeof value === "string" && empty(value) && key !== "url") continue;
      const filled = fill(value, values);
      if (filled && typeof filled === "object" && !Array.isArray(filled) && !Object.keys(filled).length) continue;
      out[key] = filled;
    }
    return out;
  }
  return config;
}

/** What a server is, in a line: its command, or where it is */
function describe(config: Record<string, unknown>): string {
  if (typeof config.url === "string") return config.url;
  const args = Array.isArray(config.args) ? config.args.join(" ") : "";
  return `${config.command ?? "?"} ${args}`.trim();
}

/** A name not taken: "github", "github-2", … */
function freeName(base: string, servers: Servers) {
  if (!servers[base]) return base;
  for (let n = 2; ; n++) if (!servers[`${base}-${n}`]) return `${base}-${n}`;
}

const NEEDS: Record<string, string> = {
  npx: "Node.js (npx)",
  uvx: "uv (uvx)",
  docker: "Docker",
};

function OptionBadge({ option }: { option: McpOption }) {
  const text = option.kind === "remote" ? "Hosted" : option.runtime;
  return (
    <span className={`mcp-badge ${option.available ? "" : "missing"}`} title={option.available ? option.detail : `Needs ${NEEDS[option.runtime ?? ""]} on the server`}>
      {text}
    </span>
  );
}

/** Setting up one server from the catalog: which way to run it, its name, what it needs */
function AddForm({ entry, servers, onAdd, onCancel, busy }: {
  entry: McpEntry;
  servers: Servers;
  onAdd: (name: string, config: Record<string, unknown>) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const [index, setIndex] = useState(0);
  const option = entry.options[index];
  const [name, setName] = useState(() => freeName(entry.suggested_name, servers));
  const [values, setValues] = useState<Record<string, string>>({});
  useEffect(() => {
    setValues(Object.fromEntries(option.fields.map(f => [f.key, f.default ?? ""])));
  }, [index]);
  const config = fill(option.config, values) as Record<string, unknown>;
  const taken = Boolean(servers[name.trim()]);
  const shown = useMemo(() => {
    // Secrets don't show in the preview
    const masked = { ...values };
    for (const f of option.fields) if (f.secret && masked[f.key]) masked[f.key] = "••••••";
    return JSON.stringify({ [name.trim() || "?"]: fill(option.config, masked) }, null, 2);
  }, [values, name, option]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim() || taken) return;
    onAdd(name.trim(), config);
  };

  return (
    <form className="mcp-add" onSubmit={submit}>
      {entry.options.length > 1 && (
        <div className="mcp-ways" role="radiogroup" aria-label="How it runs">
          {entry.options.map((o, i) => (
            <button key={i} type="button" role="radio" aria-checked={i === index} onClick={() => setIndex(i)}>
              <b>{o.label}</b>
              <span>{o.detail}</span>
              {!o.available && <em>needs {NEEDS[o.runtime ?? ""]}</em>}
            </button>
          ))}
        </div>
      )}
      {!option.available && (
        <div className="problem">
          This runs with <code>{option.runtime}</code>, which this server doesn't have, so it won't connect. Install{" "}
          {NEEDS[option.runtime ?? ""]} where labb's server runs{entry.options.some(o => o.available) ? ", or pick another way above" : ""}.
        </div>
      )}
      <label className="mcp-field">
        <span>Name</span>
        <input value={name} onChange={e => setName(e.target.value.replace(/\s+/g, "-"))} required aria-invalid={taken} />
        <small className={taken ? "problem-text" : ""}>{taken ? "There's one with this name already." : "Its tools are called mcp__name__tool."}</small>
      </label>
      {option.fields.map(field => (
        <label key={field.key} className="mcp-field">
          <span>
            {field.label}
            {field.required ? <span className="required"> *</span> : <span className="hint"> (optional)</span>}
          </span>
          {field.choices.length ? (
            <select value={values[field.key] ?? ""} required={field.required} onChange={e => setValues({ ...values, [field.key]: e.target.value })}>
              {!field.required && <option value="">—</option>}
              {field.choices.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          ) : (
            <input type={field.secret ? "password" : "text"} autoComplete="off" spellCheck={false} value={values[field.key] ?? ""}
              required={field.required} onChange={e => setValues({ ...values, [field.key]: e.target.value })} />
          )}
          {field.help && <small>{field.help}</small>}
        </label>
      ))}
      <details className="mcp-preview">
        <summary>What gets saved</summary>
        <pre>{shown}</pre>
      </details>
      <div className="mcp-add-actions">
        <button type="submit" className={option.available ? "primary" : ""} disabled={busy || !name.trim() || taken}>{busy ? "Connecting…" : option.available ? "Add and connect" : "Add anyway"}</button>
        <button type="button" onClick={onCancel}>Cancel</button>
        {entry.url && <a className="hint" href={entry.url} target="_blank" rel="noreferrer">Its page ↗</a>}
      </div>
    </form>
  );
}

/** A server that's set up: how it's doing, and changing or removing it */
function ServerRow({ name, config, state, busy, onSave, onRemove }: {
  name: string;
  config: Record<string, unknown>;
  state: number | string | undefined;
  busy: boolean;
  onSave: (config: Record<string, unknown>) => void;
  onRemove: () => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const save = () => {
    try {
      const parsed = JSON.parse(editing ?? "");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("it should be an object");
      setError(null);
      onSave(parsed);
      setEditing(null);
    } catch (e) {
      setError(`That isn't valid: ${(e as Error).message}`);
    }
  };
  const ok = typeof state === "number";
  return (
    <li className={`mcp-server ${ok ? "ok" : state === undefined ? "" : "down"}`}>
      <div className="mcp-server-head">
        <span className="mcp-dot" aria-hidden />
        <b>{name}</b>
        <span className="mcp-server-state">
          {ok ? `${state} tool${state === 1 ? "" : "s"}` : state === undefined ? "not connected" : "couldn't connect"}
        </span>
        <span className="spacer" />
        <button type="button" onClick={() => (setEditing(editing === null ? JSON.stringify(config, null, 2) : null), setError(null))}>
          {editing === null ? "Edit" : "Close"}
        </button>
        <button type="button" className="danger" disabled={busy} onClick={() => window.confirm(`Remove ${name}? Its tools leave the chat.`) && onRemove()}>
          Remove
        </button>
      </div>
      <code className="mcp-server-what">{describe(config)}</code>
      {!ok && typeof state === "string" && <div className="problem">{state}</div>}
      {editing !== null && (
        <div className="mcp-edit">
          <textarea value={editing} onChange={e => setEditing(e.target.value)} rows={8} spellCheck={false} aria-label={`${name} (JSON)`} />
          {error && <div className="problem">{error}</div>}
          <div className="mcp-add-actions">
            <button type="button" className="primary" disabled={busy} onClick={save}>{busy ? "Connecting…" : "Save and connect"}</button>
          </div>
        </div>
      )}
    </li>
  );
}

export function McpServersTab() {
  const [settings, setSettings] = useState<McpSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [query, setQuery] = useState("");
  const [catalog, setCatalog] = useState<McpCatalog | null>(null);
  const [searching, setSearching] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const [json, setJson] = useState<string | null>(null);
  const asked = useRef(0);

  useEffect(() => {
    getMcpSettings().then(setSettings, e => setError((e as Error).message));
  }, []);

  // The catalog, a moment after typing stops
  useEffect(() => {
    const n = ++asked.current;
    setSearching(true);
    const timer = setTimeout(async () => {
      try {
        const found = await getMcpCatalog(query.trim());
        if (n === asked.current) setCatalog(found);
      } catch (e) {
        if (n === asked.current) setCatalog({ servers: [], next: null, error: (e as Error).message, runtimes: {} });
      } finally {
        if (n === asked.current) setSearching(false);
      }
    }, query.trim() ? 350 : 0);
    return () => clearTimeout(timer);
  }, [query]);

  const more = async () => {
    if (!catalog?.next) return;
    setSearching(true);
    try {
      const found = await getMcpCatalog(query.trim(), catalog.next);
      setCatalog({ ...found, servers: [...catalog.servers, ...found.servers] });
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setSearching(false);
    }
  };

  const servers = (settings?.servers ?? {}) as Servers;
  const status = settings?.status.servers ?? {};

  /** Save all of them and connect; the status of `name`, if one was named */
  const save = async (next: Servers, name?: string) => {
    setSaving(true);
    setError(null);
    try {
      const saved = await saveMcpSettings(next);
      setSettings(saved);
      if (name && name in saved.servers) {
        const state = saved.status.servers[name];
        if (typeof state === "number") toast(`${name}: connected, ${state} tool${state === 1 ? "" : "s"}`, "info");
        else toast(`${name} was added but couldn't connect: ${state}`);
      }
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setSaving(false);
    }
  };

  const saveJson = async () => {
    let parsed: Servers;
    try {
      parsed = json?.trim() ? JSON.parse(json) : {};
    } catch (e) {
      setError(`That isn't valid JSON: ${(e as Error).message}`);
      return;
    }
    if (await save(parsed)) setJson(null);
  };

  if (!settings && !error) return <p className="hint">Loading…</p>;
  const names = Object.keys(servers);
  const missing = Object.entries(catalog?.runtimes ?? {}).filter(([, has]) => !has).map(([name]) => name);

  return (
    <div className="mcp-servers">
      <p className="hint">
        Their tools are offered in the web chat, to the default assistant (not to agents). Tools a server marks read-only run
        right away; the others ask in the chat first, and don't run in scheduled tasks. Their settings are kept encrypted.
      </p>
      {error && <div className="problem">{error}</div>}

      <section>
        <h3 className="mcp-heading">On this server <span className="count">{names.length}</span></h3>
        {names.length ? (
          <ul className="mcp-list">
            {names.map(name => (
              <ServerRow key={name} name={name} config={servers[name]} state={status[name]} busy={saving}
                onSave={config => save({ ...servers, [name]: config }, name)}
                onRemove={() => {
                  const { [name]: _, ...rest } = servers;
                  save(rest);
                }} />
            ))}
          </ul>
        ) : (
          <p className="hint">None yet. Add one below: GitHub, docs lookups, a browser, files…</p>
        )}
      </section>

      <section>
        <h3 className="mcp-heading">Add a server</h3>
        <div className="mcp-search">
          <input type="search" value={query} onChange={e => setQuery(e.target.value)} aria-label="Search MCP servers"
            placeholder="Search the MCP Registry: github, postgres, slack, notion…" />
          {searching && <span className="mcp-searching" aria-label="Searching" />}
        </div>
        <p className="hint mcp-source">
          {query.trim() ? <>Well-known ones first, then the <a href="https://registry.modelcontextprotocol.io" target="_blank" rel="noreferrer">MCP Registry</a>&apos;s.</> : "Well-known ones. Search for more."}
          {missing.length > 0 && <> This server doesn&apos;t have {missing.map(m => <code key={m}>{m}</code>).reduce<ReactNode[]>((all, c, i) => (i ? [...all, ", ", c] : [c]), [])}, so packages that need {missing.length === 1 ? "it" : "them"} won&apos;t run here.</>}
        </p>
        {catalog?.error && <div className="problem">{catalog.error}</div>}
        <ul className="mcp-results">
          {catalog?.servers.map(entry => {
            const open = adding === entry.id;
            // Already set up: one of the servers runs its package, or is at its address
            const added = names.some(n => entry.options.some(o => describe(servers[n]).includes(o.kind === "remote" ? String(o.config.url) : o.detail)));
            return (
              <li key={entry.id} className={`mcp-result${open ? " open" : ""}`}>
                <div className="mcp-result-head">
                  <div className="mcp-result-text">
                    <b>{entry.title}</b>
                    {entry.featured && <span className="mcp-badge known">Well-known</span>}
                    <span className="mcp-result-id">{entry.id}{entry.version ? ` · v${entry.version}` : ""}</span>
                    {entry.description && <p>{entry.description}</p>}
                  </div>
                  <div className="mcp-result-side">
                    <span className="mcp-badges">{entry.options.map((o, i) => <OptionBadge key={i} option={o} />)}</span>
                    {!open && (
                      <button type="button" className="mcp-add-button" onClick={() => setAdding(entry.id)}>
                        {added ? "Add again" : "Add"}
                      </button>
                    )}
                  </div>
                </div>
                {open && (
                  <AddForm entry={entry} servers={servers} busy={saving} onCancel={() => setAdding(null)}
                    onAdd={async (name, config) => {
                      if (await save({ ...servers, [name]: config }, name)) setAdding(null);
                    }} />
                )}
              </li>
            );
          })}
        </ul>
        {catalog && !catalog.servers.length && !searching && !catalog.error && <p className="hint">Nothing called that. Try another word, or add it by hand below.</p>}
        {catalog?.next && <button type="button" onClick={more} disabled={searching}>More results</button>}
        <p className="hint mcp-trust">Packages are downloaded and run on the server, and hosted ones get what the chat sends them: add the ones you trust.</p>
      </section>

      <details className="mcp-json" open={json !== null} onToggle={e => setJson((e.target as HTMLDetailsElement).open ? (json ?? (names.length ? JSON.stringify(servers, null, 2) : "")) : null)}>
        <summary>By hand, as JSON</summary>
        <p className="hint">
          All of them, in the same format as labb&apos;s <code>mcpServers</code>: a <code>command</code> to run on the server, or a <code>url</code>.
        </p>
        {json !== null && (
          <>
            <textarea value={json} onChange={e => setJson(e.target.value)} placeholder={EXAMPLE} rows={12} spellCheck={false} aria-label="MCP servers (JSON)" />
            <div className="mcp-add-actions">
              <button type="button" className="primary" disabled={saving} onClick={saveJson}>{saving ? "Connecting…" : "Save and connect"}</button>
            </div>
          </>
        )}
      </details>
    </div>
  );
}
