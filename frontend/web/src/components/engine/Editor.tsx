// An engine's editor: what it changes on the left, what the model would get and a chat to try it on the right.
import { Fragment, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { type ContextInspection, type CustomSection, type CustomTool, type Engine, type EngineCatalog, type EngineChange, type EngineCheck, type EngineVersion, type HookEntry, type Loop, type Model, type Overlay, engineApi } from "../../api";
import { TryChat } from "../Agents";
import { Breakdown } from "../context/Breakdown";
import { type Action, studio } from "../../studioApi";
import { toast } from "../Toaster";
import { LoopEditor } from "./LoopEditor";
import { diff, getIn, setIn, setSetting, shown } from "./overlay";

type SectionId = "prompt" | "context" | "tools" | "behavior" | "model" | "loop" | "hooks" | "json";
const SECTIONS: { id: SectionId; label: string; about: string; parts: string[] }[] = [
  { id: "prompt", label: "Prompt", about: "What the agent is told", parts: ["prompts", "tool_prompts", "sections"] },
  { id: "loop", label: "Loop", about: "The steps of a reply", parts: ["loop"] },
  { id: "context", label: "Context", about: "What stays in the window", parts: ["context"] },
  { id: "tools", label: "Tools", about: "What it can use", parts: ["tools"] },
  { id: "behavior", label: "Input & repair", about: "What's added, what's fixed, when it goes on", parts: ["input", "repair", "continue"] },
  { id: "model", label: "Model", about: "Which models it uses", parts: ["model"] },
  { id: "hooks", label: "Hooks & servers", about: "Terminal: commands around its work", parts: ["hooks", "mcp"] },
  { id: "json", label: "JSON", about: "All of it, to copy or import", parts: [] },
];
const SURFACES = [
  { id: "web", label: "Web" },
  { id: "terminal", label: "Terminal" },
  { id: "plan", label: "Plan mode" },
  { id: "task", label: "Task" },
];
const PANELS = ["Preview", "Try it", "Changes", "Versions"] as const;
type Panel = (typeof PANELS)[number];
const EXPORT_NOTE = "Paste into ~/.labb/engines/<name>.json to use it in the terminal.";

type Update = (next: Overlay) => void;
type Editing = { overlay: Overlay; update: Update; catalog: EngineCatalog; readOnly: boolean };

export function Editor({ engine, catalog, models, onBack, onSaved, onOpen }: {
  engine: Engine; catalog: EngineCatalog; models: Model[]; onBack: () => void; onSaved: () => void; onOpen: (id: string) => void;
}) {
  const [saved, setSaved] = useState(engine);
  const [overlay, setOverlay] = useState<Overlay>(engine.overlay);
  const [name, setName] = useState(engine.name);
  const [description, setDescription] = useState(engine.description);
  const [shared, setShared] = useState(engine.shared);
  const [section, setSection] = useState<SectionId>("prompt");
  const [panel, setPanel] = useState<Panel>("Preview");
  // The loop's canvas wants the room: the panel makes way unless it's asked for
  const [sideOnLoop, setSideOnLoop] = useState(false);
  const [surface, setSurface] = useState("web");
  const [checked, setChecked] = useState<EngineCheck | null>(null);
  const [seen, setSeen] = useState<ContextInspection | null>(null);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const readOnly = !saved.mine;

  const dirty = JSON.stringify(overlay) !== JSON.stringify(saved.overlay) || name !== saved.name || description !== saved.description || shared !== saved.shared;

  // What's wrong and what the model would get, shortly after each edit
  useEffect(() => {
    const timer = setTimeout(() => {
      engineApi.check(overlay).then(setChecked, () => {});
      engineApi.preview(overlay, surface).then(r => setSeen(r.seen), () => {});
    }, 300);
    return () => clearTimeout(timer);
  }, [overlay, surface]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const problems = checked?.problems ?? [];
  const leave = () => {
    if (!dirty || confirm("Leave without saving your changes?")) onBack();
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const next = await engineApi.update(saved.id, { name, description, shared, overlay, note: note.trim() || undefined });
      setSaved(next);
      setOverlay(next.overlay);
      setNote("");
      onSaved();
      toast(next.version === saved.version ? `Saved ${next.name}` : `Saved ${next.name} as version ${next.version}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const fork = async () => {
    try {
      const copy = await engineApi.duplicate(saved.id);
      onSaved();
      onOpen(copy.id);
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const editing: Editing = { overlay, update: setOverlay, catalog, readOnly };
  const changedIn = (parts: string[]) => parts.some(part => Object.keys((overlay as Record<string, object>)[part] ?? {}).length > 0);

  return (
    <div className="engine-editor">
      <header className="engine-top">
        <button type="button" className="link" onClick={leave}>← All engines</button>
        {readOnly ? (
          <div className="engine-title"><h3>{saved.name}</h3><span className="model-tag">{saved.built_in ? "Built-in" : `By ${saved.owner ?? "someone"}`}</span></div>
        ) : (
          <div className="engine-title">
            <input className="engine-name" aria-label="Name" value={name} maxLength={60} onChange={e => setName(e.target.value)} />
            <input className="engine-description" aria-label="Description" value={description} maxLength={200} placeholder="What's different about it" onChange={e => setDescription(e.target.value)} />
          </div>
        )}
        <span className="engine-version muted">{saved.built_in ? "" : `v${saved.version}${dirty ? " · unsaved" : ""}`}</span>
        <div className="engine-actions">
          {readOnly ? (
            <button type="button" className="primary" onClick={fork}>Fork to change</button>
          ) : (
            <>
              <label className="check" title="Everyone on this server can see it, use it and copy it; only you can change it">
                <input type="checkbox" checked={shared} onChange={e => setShared(e.target.checked)} /><span>Shared</span>
              </label>
              <input className="engine-note" aria-label="What changed" placeholder="What changed (optional)" value={note} maxLength={200} onChange={e => setNote(e.target.value)} />
              <button type="button" className="primary" disabled={saving || !dirty || problems.length > 0 || !name.trim()} onClick={save}>{saving ? "Saving…" : "Save"}</button>
            </>
          )}
        </div>
      </header>
      {readOnly && saved.built_in && <p className="engine-banner">{saved.id === "default" ? "This is what labb does as it comes, and it can't be changed. Look around, or fork it to start your own." : "This one comes with labb and can't be changed. Use it in the message box, or fork it to make it your own."}</p>}
      {error && <div className="problem">{error}</div>}
      {problems.length > 0 && (
        <div className="problem engine-problems" role="alert">
          <b>Can't be saved yet</b>
          <ul>{problems.slice(0, 6).map(problem => <li key={problem}>{problem}</li>)}</ul>
        </div>
      )}
      {checked && checked.web_problems.length > 0 && (
        <p className="engine-warning">Works in the terminal only, so the web chat can't use it, and Try it and the Web preview are limited. {checked.web_problems.join(". ")}.</p>
      )}
      <div className={`engine-body${section === "loop" && !sideOnLoop ? " no-side" : ""}`}>
        <nav className="engine-nav" aria-label="What an engine changes">
          {SECTIONS.map(item => (
            <button key={item.id} type="button" className={section === item.id ? "active" : ""} aria-current={section === item.id ? "true" : undefined} onClick={() => setSection(item.id)}>
              <b>{item.label}{changedIn(item.parts) && <i className="changed-dot" title="Changed from the default" />}</b>
              <span>{item.about}</span>
            </button>
          ))}
        </nav>
        <main className={`engine-main${section === "loop" ? " wide" : ""}`}>
          <fieldset className="bare" disabled={readOnly && section !== "loop" && section !== "json"}>
            {section === "prompt" && <PromptSection {...editing} />}
            {section === "context" && <ContextSection {...editing} seen={seen} />}
            {section === "tools" && <ToolsSection {...editing} />}
            {section === "behavior" && <BehaviorSection {...editing} />}
            {section === "model" && <ModelSection {...editing} models={models} />}
            {section === "hooks" && <HooksSection {...editing} />}
            {section === "json" && <JsonSection {...editing} name={name} />}
          </fieldset>
          {section === "loop" && (
            <LoopEditor loop={overlay.loop ?? catalog.default_loop} customized={Boolean(overlay.loop)} catalog={catalog} readOnly={readOnly} problems={problems}
              sideOpen={sideOnLoop} onToggleSide={() => setSideOnLoop(!sideOnLoop)}
              onChange={(loop: Loop) => setOverlay(setIn(overlay, ["loop"], loop))} onReset={() => setOverlay(setIn(overlay, ["loop"], undefined))} />
          )}
        </main>
        <aside className="engine-side">
          <div className="engine-tabs" role="tablist">
            {PANELS.map(item => <button key={item} type="button" role="tab" aria-selected={panel === item} className={panel === item ? "active" : ""} onClick={() => setPanel(item)}>{item}</button>)}
          </div>
          {panel === "Preview" && (
            <div className="engine-panel">
              <div className="surface-switch" role="radiogroup" aria-label="Which kind of run to preview">
                {SURFACES.map(item => <button key={item.id} type="button" role="radio" aria-checked={surface === item.id} className={surface === item.id ? "active" : ""} onClick={() => setSurface(item.id)}>{item.label}</button>)}
              </div>
              {seen ? <div className="context-inspector"><Breakdown seen={seen} /></div> : <p className="muted">{problems.length ? "Fix what's wrong to see it." : "Loading…"}</p>}
            </div>
          )}
          {/* Mounted while it's open, so a conversation survives switching section */}
          <div className="engine-panel try" hidden={panel !== "Try it"}>
            {checked?.web_problems.length ? <p className="muted">The web chat can't try an engine that runs commands. Use <code>labb --engine {saved.slug}</code> in the terminal after saving.</p>
              : problems.length ? <p className="muted">Fix what's wrong to try it.</p>
              : <TryChat options={{ engineDraft: overlay }} who={{ emoji: "⚙️", name: name || "Your engine", description: "Uses your changes as you make them; not saved", starters: [] }} note="Each message uses your latest changes; not saved" placeholder="Message the agent" />}
          </div>
          {panel === "Changes" && <ChangesPanel overlay={overlay} saved={saved.overlay} checked={checked} readOnly={readOnly} onApply={setOverlay} />}
          {panel === "Versions" && <VersionsPanel engine={saved} current={overlay} onRestored={next => { setSaved(next); setOverlay(next.overlay); onSaved(); }} />}
        </aside>
      </div>
    </div>
  );
}

function Reset({ onClick }: { onClick: () => void }) {
  return <button type="button" className="link reset" onClick={onClick}>Back to default</button>;
}

function Row({ title, about, changed, onReset, children }: { title: ReactNode; about?: ReactNode; changed?: boolean; onReset?: () => void; children?: ReactNode }) {
  return (
    <div className={`engine-row${changed ? " changed" : ""}`}>
      <div className="engine-row-head">
        <b>{title}{changed && <i className="changed-dot" title="Changed from the default" />}</b>
        {changed && onReset && <Reset onClick={onReset} />}
      </div>
      {about && <p className="hint">{about}</p>}
      {children}
    </div>
  );
}

function PromptText({ overlay, update, catalog, id, section = "prompts", fallback, label }: Editing & { id: string; section?: string; fallback?: string; label?: string }) {
  const standard = fallback ?? catalog.prompts[id];
  const value = getIn<string>(overlay, [section, id]) ?? standard;
  return (
    <label className="field prompt-text">
      {label && <span>{label}</span>}
      <textarea rows={Math.min(14, Math.max(3, Math.ceil(value.length / 70)))} value={value} onChange={e => update(setSetting(overlay, [section, id], e.target.value, standard))} />
      {value !== standard && <Reset onClick={() => update(setIn(overlay, [section, id], undefined))} />}
    </label>
  );
}

const PROMPT_LABELS: Record<string, string> = { base: "Everywhere", terminal: "Added in the terminal", mode_fast: "Fast", mode_thinking: "Thinking" };

function PromptSection(editing: Editing) {
  const { overlay, update, catalog, readOnly } = editing;
  const [open, setOpen] = useState<string | null>("how_to_work");
  const [toolsOpen, setToolsOpen] = useState(false);
  const off = overlay.sections?.off ?? [];
  const added = overlay.sections?.add ?? [];
  const toolsets = catalog.toolsets.filter(t => t.prompt);
  const standardOrder = useMemo(() => {
    const [base, ...rest] = catalog.sections.map(s => s.id);
    return [base, ...toolsets.map(t => `tools:${t.name}`), ...rest];
  }, [catalog, toolsets]);
  const allIds = [...standardOrder, ...added.map(s => s.id)];
  const given = overlay.sections?.order ?? [];
  const order = [...given.filter(id => allIds.includes(id)), ...allIds.filter(id => !given.includes(id))];

  const move = (id: string, by: number) => {
    const next = [...order];
    const at = next.indexOf(id);
    const to = at + by;
    if (to < 0 || to >= next.length) return;
    next.splice(at, 1);
    next.splice(to, 0, id);
    update(setSetting(overlay, ["sections", "order"], next, allIds));
  };
  const toggle = (id: string, on: boolean) => update(setIn(overlay, ["sections", "off"], on ? off.filter(x => x !== id) : [...off, id]));
  const setAdded = (next: CustomSection[], base = overlay) => update(setIn(base, ["sections", "add"], next));

  const about = (id: string) => {
    if (id.startsWith("tools:")) {
      const toolset = catalog.toolsets.find(t => t.name === id.slice(6));
      return { label: toolset?.label ?? id, about: "", tag: "Tools", edits: toolset ? [{ id: toolset.name, section: "tool_prompts", fallback: toolset.prompt }] : [] };
    }
    const known = catalog.sections.find(s => s.id === id);
    return { label: known?.label ?? id, about: known?.about ?? "", tag: "", edits: (known?.prompts ?? []).map(key => ({ id: key, section: "prompts", fallback: catalog.prompts[key] })) };
  };

  const addSection = () => {
    const taken = new Set(allIds);
    let i = 1;
    while (taken.has(`custom_${i}`)) i++;
    const id = `custom_${i}`;
    setAdded([...added, { id, label: "New section", text: "Write what the agent should know." }]);
    setOpen(id);
  };

  return (
    <>
      <h4>The system prompt, section by section</h4>
      <p className="hint">In the order the model reads them. Sections only appear in the runs they're for: switch the preview between Web, Terminal, Plan mode and Task to see each. <code>{"{{today}}"}</code> and <code>{"{{workdir}}"}</code> are filled in.</p>
      <ol className="section-list">
        {order.map((id, index) => {
          // The toolsets' sections are many and alike: one line until they're asked for
          const isToolset = id.startsWith("tools:");
          const firstToolset = isToolset && !order.slice(0, index).some(other => other.startsWith("tools:"));
          const toolsetsChanged = toolsets.filter(t => overlay.tool_prompts?.[t.name] !== undefined || off.includes(`tools:${t.name}`)).length;
          const folder = firstToolset && (
            <li key="toolsets" className="section-item section-folder">
              <button type="button" className="section-name" aria-expanded={toolsOpen} onClick={() => setToolsOpen(!toolsOpen)}>
                <b>{toolsOpen ? "▾" : "▸"} What each toolset adds ({toolsets.length}){toolsetsChanged > 0 && <i className="changed-dot" title={`${toolsetsChanged} changed`} />}</b>
                <span className="muted">How to use its tools, only when they're offered</span>
              </button>
            </li>
          );
          if (isToolset && !toolsOpen) return folder || null;
          const custom = added.find(s => s.id === id);
          const info = custom ? { label: custom.label || id, about: custom.when?.length ? `Only: ${custom.when.join(", ")}` : "Always", tag: "Yours", edits: [] } : about(id);
          const isOn = !off.includes(id);
          const edited = info.edits.some(e => getIn(overlay, [e.section, e.id]) !== undefined);
          const canEdit = Boolean(custom) || info.edits.length > 0;
          return (
            <Fragment key={id}>
            {folder}
            <li className={`section-item${isOn ? "" : " off"}${open === id ? " open" : ""}${isToolset ? " in-folder" : ""}`}>
              <div className="section-line">
                {!readOnly && (
                  <span className="section-move">
                    <button type="button" aria-label={`Move ${info.label} up`} disabled={index === 0} onClick={() => move(id, -1)}>↑</button>
                    <button type="button" aria-label={`Move ${info.label} down`} disabled={index === order.length - 1} onClick={() => move(id, 1)}>↓</button>
                  </span>
                )}
                <input type="checkbox" aria-label={`${info.label} on`} checked={isOn} onChange={e => toggle(id, e.target.checked)} />
                <button type="button" className="section-name" disabled={false} aria-expanded={canEdit ? open === id : undefined} onClick={() => canEdit && setOpen(open === id ? null : id)}>
                  <b>{info.label}{edited && <i className="changed-dot" title="Changed from the default" />}</b>
                  <span className="muted">{info.about}</span>
                </button>
                {info.tag && info.tag !== "Tools" && <span className="model-tag">{info.tag}</span>}
                {!canEdit && <span className="muted section-found">written by people, or found as it runs</span>}
              </div>
              {open === id && custom && (
                <div className="section-edit">
                  <label className="field"><span>Name</span><input value={custom.label ?? ""} onChange={e => setAdded(added.map(s => (s.id === id ? { ...s, label: e.target.value } : s)))} /></label>
                  <label className="field"><span>Text</span><textarea rows={5} value={custom.text} onChange={e => setAdded(added.map(s => (s.id === id ? { ...s, text: e.target.value } : s)))} /></label>
                  <fieldset className="field"><legend>Only in (none: always)</legend>
                    <div className="compare-picks">{catalog.when.map(when => (
                      <label key={when} className="check"><input type="checkbox" checked={custom.when?.includes(when) ?? false} onChange={e => setAdded(added.map(s => {
                        if (s.id !== id) return s;
                        const next = e.target.checked ? [...(s.when ?? []), when] : (s.when ?? []).filter(w => w !== when);
                        const { when: _, ...rest } = s;
                        return next.length ? { ...rest, when: next } : rest;
                      }))} /><span>{when}</span></label>
                    ))}</div>
                  </fieldset>
                  <button type="button" className="danger" onClick={() => {
                    const without = setIn(setIn(overlay, ["sections", "off"], off.filter(x => x !== id)), ["sections", "order"], given.filter(x => x !== id).length ? given.filter(x => x !== id) : undefined);
                    setAdded(added.filter(s => s.id !== id), without);
                  }}>Remove this section</button>
                </div>
              )}
              {open === id && !custom && (
                <div className="section-edit">
                  {info.edits.map(edit => <PromptText key={edit.id} {...editing} id={edit.id} section={edit.section} fallback={edit.fallback} label={info.edits.length > 1 ? PROMPT_LABELS[edit.id] : undefined} />)}
                </div>
              )}
            </li>
            </Fragment>
          );
        })}
      </ol>
      {!readOnly && <button type="button" onClick={addSection}>+ Add a section</button>}
      <h4>Other prompts</h4>
      {catalog.other_prompts.map(item => (
        <Row key={item.key} title={item.label} about={item.about} changed={getIn(overlay, ["prompts", item.key]) !== undefined}>
          <PromptText {...editing} id={item.key} />
        </Row>
      ))}
    </>
  );
}

function ContextSection({ overlay, update, catalog, seen }: Editing & { seen: ContextInspection | null }) {
  const value = (key: string) => (getIn<number>(overlay, ["context", key]) ?? catalog.context[key]) as number;
  const set = (key: string, next: number) => update(setSetting(overlay, ["context", key], next, catalog.context[key]));
  const files = (getIn<string[]>(overlay, ["context", "instruction_files"]) ?? catalog.context.instruction_files) as string[];
  return (
    <>
      <h4>What stays in the window</h4>
      <p className="hint">The saved conversation always stays complete. This is about what the model is sent.</p>
      <div className="window-bar" aria-hidden>
        <span className="window-keep" style={{ width: `${value("keep_recent") * 100}%` }}>keep</span>
        <span className="window-mark" style={{ left: `${value("compact_at") * 100}%` }} />
        {seen && <span className="window-used" style={{ width: `${Math.min(100, seen.share * 100)}%` }} />}
      </div>
      <p className="hint window-legend">
        Summarizes at {Math.round(value("compact_at") * 100)}% of the window, then keeps up to {Math.round(value("keep_recent") * 100)}% of recent turns.
        {seen && ` ${seen.window.toLocaleString()} tokens on ${seen.model}; the prompt and tools take ${Math.round(seen.share * 100)}% before any message.`}
      </p>
      {catalog.context_fields.map(field => {
        const range = catalog.ranges[field.key];
        const changed = getIn(overlay, ["context", field.key]) !== undefined;
        const percent = field.unit === "share";
        return (
          <Row key={field.key} title={field.label} about={field.about} changed={changed} onReset={() => update(setIn(overlay, ["context", field.key], undefined))}>
            <div className="number-field">
              {percent && <input type="range" aria-label={field.label} min={range.min} max={range.max} step={0.01} value={value(field.key)} onChange={e => set(field.key, Number(e.target.value))} />}
              <input type="number" aria-label={`${field.label}, number`} min={percent ? range.min * 100 : range.min} max={percent ? range.max * 100 : range.max} step={percent ? 1 : range.whole ? 1 : 0.1}
                value={percent ? Math.round(value(field.key) * 100) : value(field.key)} onChange={e => set(field.key, percent ? Number(e.target.value) / 100 : Number(e.target.value))} />
              <span className="muted">{percent ? "%" : field.unit ?? ""}</span>
            </div>
          </Row>
        );
      })}
      <Row title="Project instruction files" about="Looked for in each folder from the project up to your home; the first found in a folder wins. Plain file names only." changed={getIn(overlay, ["context", "instruction_files"]) !== undefined} onReset={() => update(setIn(overlay, ["context", "instruction_files"], undefined))}>
        <input value={files.join(", ")} aria-label="Project instruction files" onChange={e => update(setSetting(overlay, ["context", "instruction_files"], e.target.value.split(",").map(s => s.trim()).filter(Boolean), catalog.context.instruction_files))} />
      </Row>
    </>
  );
}

function ToolsSection(editing: Editing) {
  const { overlay, update, catalog, readOnly } = editing;
  const [open, setOpen] = useState<string | null>(null);
  const deny = overlay.tools?.deny ?? [];
  const allow = overlay.tools?.allow;
  const descriptions = overlay.tools?.descriptions ?? {};
  const custom = overlay.tools?.custom ?? [];
  const setDeny = (next: string[]) => update(setIn(overlay, ["tools", "deny"], next));
  const flip = (name: string, on: boolean) => setDeny(on ? deny.filter(d => d !== name) : [...deny, name]);
  return (
    <>
      <h4>Tools</h4>
      <p className="hint">An engine can only turn tools off. What an agent wasn't given, what a person hasn't connected, and what needs approval stay as they are.</p>
      {catalog.toolsets.map(toolset => {
        const setOn = !deny.includes(toolset.name);
        return (
          <div key={toolset.name} className={`toolset${setOn ? "" : " off"}`}>
            <label className="check toolset-name">
              <input type="checkbox" checked={setOn} onChange={e => flip(toolset.name, e.target.checked)} />
              <span><b>{toolset.label}</b> {toolset.terminal_only && <span className="model-tag">Terminal</span>}</span>
            </label>
            <ul>
              {toolset.tools.map(tool => {
                const on = setOn && !deny.includes(tool.name);
                const edited = descriptions[tool.name] !== undefined;
                return (
                  <li key={tool.name}>
                    <div className="tool-line">
                      <input type="checkbox" aria-label={`${tool.name} on`} disabled={!setOn} checked={on} onChange={e => flip(tool.name, e.target.checked)} />
                      <button type="button" className="link" onClick={() => setOpen(open === tool.name ? null : tool.name)} aria-expanded={open === tool.name}><code>{tool.name}</code></button>
                      {edited && <i className="changed-dot" title="Description changed" />}
                    </div>
                    {open === tool.name && (
                      <label className="field tool-description">
                        <span>What the model reads about it</span>
                        <textarea rows={5} value={descriptions[tool.name] ?? tool.description} onChange={e => update(setSetting(overlay, ["tools", "descriptions", tool.name], e.target.value, tool.description))} />
                        {edited && <Reset onClick={() => update(setIn(overlay, ["tools", "descriptions", tool.name], undefined))} />}
                      </label>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
      <Row title="Only these" about="Names of tools or toolsets, separated by commas, with wildcards like mcp__github__*. Empty: all that aren't off above." changed={allow !== undefined} onReset={() => update(setIn(overlay, ["tools", "allow"], undefined))}>
        <input aria-label="Only these tools" value={(allow ?? []).join(", ")} placeholder="files, edits, mcp__github__*" onChange={e => update(setIn(overlay, ["tools", "allow"], e.target.value.split(",").map(s => s.trim()).filter(Boolean)))} />
      </Row>
      <h4>Its own tools</h4>
      <p className="hint">Offered to the model as <code>custom__name</code>, so they can't stand in for labb's. Agents don't get them.</p>
      {custom.map((tool, i) => (
        <CustomToolCard key={i} tool={tool} editing={editing} onChange={next => update(setIn(overlay, ["tools", "custom"], custom.map((t, j) => (j === i ? next : t))))} onRemove={() => update(setIn(overlay, ["tools", "custom"], custom.filter((_, j) => j !== i)))} />
      ))}
      {!readOnly && (
        <button type="button" onClick={() => update(setIn(overlay, ["tools", "custom"], [...custom, { name: `tool_${custom.length + 1}`, description: "Say what it's for and when to use it.", kind: "prompt", prompt: "Answer this: {{question}}", params: [{ name: "question", description: "What to answer", type: "string" }] }]))}>+ Add a tool</button>
      )}
    </>
  );
}

function CustomToolCard({ tool, editing, onChange, onRemove }: { tool: CustomTool; editing: Editing; onChange: (tool: CustomTool) => void; onRemove: () => void }) {
  const { catalog } = editing;
  const [actions, setActions] = useState<Action[]>([]);
  useEffect(() => {
    if (tool.kind === "action") studio.actions().then(setActions, () => setActions([]));
  }, [tool.kind]);
  const params = tool.params ?? [];
  const setParam = (i: number, change: object) => onChange({ ...tool, params: params.map((p, j) => (j === i ? { ...p, ...change } : p)) });
  const body = { command: "command", prompt: "prompt", python: "code", action: "action" }[tool.kind] as "command" | "prompt" | "code" | "action";
  return (
    <div className="custom-tool">
      <div className="field-row">
        <label className="field grow"><span>Name</span><input value={tool.name} onChange={e => onChange({ ...tool, name: e.target.value })} /></label>
        <label className="field"><span>It</span>
          <select value={tool.kind} onChange={e => onChange({ ...tool, kind: e.target.value })}>
            {Object.entries(catalog.tool_kinds).map(([kind, about]) => <option key={kind} value={kind}>{about}</option>)}
          </select>
        </label>
      </div>
      <label className="field"><span>What the model reads about it</span><textarea rows={2} value={tool.description} onChange={e => onChange({ ...tool, description: e.target.value })} /></label>
      {tool.kind === "action" ? (
        <label className="field"><span>Action</span>
          <select value={tool.action ?? ""} onChange={e => onChange({ ...tool, action: e.target.value })}>
            <option value="">Choose one of your Studio actions</option>
            {actions.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </label>
      ) : (
        <label className="field"><span>{tool.kind === "command" ? "Command" : tool.kind === "python" ? "Code" : "What to ask"}</span>
          <textarea className={tool.kind === "prompt" ? "" : "code"} spellCheck={tool.kind === "prompt"} rows={tool.kind === "python" ? 8 : 3} value={tool[body] ?? ""} onChange={e => onChange({ ...tool, [body]: e.target.value })} />
          <small className="muted">
            {tool.kind === "command" && "{{name}} is an argument, quoted for the shell. It runs like run_command: it asks when it should, and not at all in plan mode."}
            {tool.kind === "prompt" && "{{name}} is an argument."}
            {tool.kind === "python" && "Reads inputs[\"args\"], sets result. In a process of its own, or the server's sandbox."}
          </small>
        </label>
      )}
      <div className="custom-params">
        <span className="muted">Arguments</span>
        {params.map((param, i) => (
          <div key={i} className="field-row param">
            <input aria-label="Argument's name" placeholder="name" value={param.name} onChange={e => setParam(i, { name: e.target.value })} />
            <input aria-label="What it's for" className="grow" placeholder="What it's for" value={param.description ?? ""} onChange={e => setParam(i, { description: e.target.value })} />
            <select aria-label="Type" value={param.type ?? "string"} onChange={e => setParam(i, { type: e.target.value })}>{catalog.param_types.map(type => <option key={type}>{type}</option>)}</select>
            <label className="check"><input type="checkbox" checked={param.required !== false} onChange={e => setParam(i, { required: e.target.checked })} /><span>needed</span></label>
            <button type="button" aria-label="Remove this argument" onClick={() => onChange({ ...tool, params: params.filter((_, j) => j !== i) })}>×</button>
          </div>
        ))}
        <button type="button" className="link" onClick={() => onChange({ ...tool, params: [...params, { name: `arg_${params.length + 1}`, type: "string" }] })}>+ Add an argument</button>
      </div>
      <button type="button" className="danger" onClick={onRemove}>Remove this tool</button>
    </div>
  );
}

function BehaviorSection({ overlay, update, catalog }: Editing) {
  const titles = { input: "What's added to the model's input", repair: "How tool calls get repaired" };
  return (
    <>
      {(["input", "repair"] as const).map(section => (
        <div key={section}>
          <h4>{titles[section]}</h4>
          {catalog.switches.filter(s => s.section === section).map(item => {
            const value = getIn<boolean>(overlay, [section, item.key]) ?? catalog[section][item.key];
            return (
              <label key={item.key} className="check engine-switch">
                <input type="checkbox" checked={value} onChange={e => update(setSetting(overlay, [section, item.key], e.target.checked, catalog[section][item.key]))} />
                <span><b>{item.label}{getIn(overlay, [section, item.key]) !== undefined && <i className="changed-dot" />}</b> <span className="muted">{item.about}</span></span>
              </label>
            );
          })}
        </div>
      ))}
      <h4>When a reply ends too early</h4>
      <p className="hint">The model is told why and goes on, at most this many times per reply. 0 turns it off.</p>
      {catalog.continue_fields.map(field => {
        const value = getIn<number>(overlay, ["continue", field.key]) ?? catalog.continue[field.key];
        return (
          <Row key={field.key} title={field.label} about={field.about} changed={getIn(overlay, ["continue", field.key]) !== undefined}
            onReset={() => update(setIn(overlay, ["continue", field.key], undefined))}>
            <div className="number-field">
              <input type="number" aria-label={field.label} min={0} max={catalog.max_continue} step={1} value={value}
                onChange={e => update(setSetting(overlay, ["continue", field.key], Number(e.target.value), catalog.continue[field.key]))} />
              <span className="muted">times</span>
            </div>
          </Row>
        );
      })}
    </>
  );
}

function ModelSection({ overlay, update, models }: Editing & { models: Model[] }) {
  const select = (key: "model" | "summary_model", label: string, about: string) => {
    const value = overlay.model?.[key] ?? "";
    return (
      <label className="field">
        <span>{label}</span>
        <select value={value} onChange={e => update(setIn(overlay, ["model", key], e.target.value || undefined))}>
          <option value="">Don't choose: {key === "model" ? "what's picked in the chat" : "the model's own"}</option>
          {models.map(m => <option key={m.spec} value={m.spec}>{m.spec}</option>)}
          {value && !models.some(m => m.spec === value) && <option value={value}>{value}</option>}
        </select>
        <small className="muted">{about}</small>
      </label>
    );
  };
  return (
    <>
      <h4>Models</h4>
      <p className="hint">A model picked for an agent or in the terminal with <code>-m</code> or <code>/model</code> comes first. Who may use which model doesn't change.</p>
      {select("model", "Model", "Used for replies, unless a model is chosen another way.")}
      {select("summary_model", "Summaries", "Writes the summaries and chat titles.")}
      <label className="field">
        <span>How much it thinks first</span>
        <select value={overlay.model?.answer_mode ?? ""} onChange={e => update(setIn(overlay, ["model", "answer_mode"], e.target.value || undefined))}>
          <option value="">Don't choose: what's picked in the chat</option>
          <option value="fast">Fast</option><option value="auto">Auto</option><option value="thinking">Thinking</option>
        </select>
        <small className="muted">Applies when the chat is on Auto.</small>
      </label>
    </>
  );
}

const HOOK_ABOUT: Record<string, string> = {
  RunStart: "When a reply starts", PreModel: "Before each model call", PostModel: "After each model call", PreToolUse: "Before each tool call: exit 2 refuses it",
  PostToolUse: "After each tool call", PreCompact: "Before a summary is written", RunEnd: "When a reply ends",
};

function HooksSection({ overlay, update, catalog }: Editing) {
  const hooks = overlay.hooks ?? {};
  const [mcp, setMcp] = useState(() => (overlay.mcp ? JSON.stringify(overlay.mcp, null, 2) : ""));
  const [mcpError, setMcpError] = useState<string | null>(null);
  const setEntries = (event: string, entries: HookEntry[]) => update(setIn(overlay, ["hooks", event], entries));
  return (
    <>
      <h4>Hooks <span className="model-tag">Terminal only</span></h4>
      <p className="hint">Commands run around the agent's work, in the working directory, with the event as JSON on stdin. Exit code 2 sends the command's stderr to the model. An engine with hooks can't be used in the web chat, so nothing here ever runs on the server.</p>
      {catalog.hook_events.map(event => (
        <Row key={event} title={event} about={HOOK_ABOUT[event]}>
          {(hooks[event] ?? []).map((entry, i) => {
            const change = (next: Partial<HookEntry>) => setEntries(event, (hooks[event] ?? []).map((e, j) => (j === i ? { ...e, ...next } : e)));
            return (
              <div key={i} className="field-row param">
                <input className="grow code" aria-label="Command" value={entry.command} placeholder="./check.sh" onChange={e => change({ command: e.target.value })} />
                {event.includes("ToolUse") && <input aria-label="Only for tools matching" placeholder="tool (regex)" value={entry.matcher ?? ""} onChange={e => change({ matcher: e.target.value || undefined })} />}
                <button type="button" aria-label="Remove this hook" onClick={() => setEntries(event, (hooks[event] ?? []).filter((_, j) => j !== i))}>×</button>
              </div>
            );
          })}
          <button type="button" className="link" onClick={() => setEntries(event, [...(hooks[event] ?? []), { command: "./check.sh" }])}>+ Add a command</button>
        </Row>
      ))}
      <h4>MCP servers <span className="model-tag">Terminal only</span></h4>
      <p className="hint">More servers while this engine is in use, as in <code>mcpServers</code> in the settings. They start when the engine does.</p>
      <textarea className="code" rows={6} spellCheck={false} aria-label="MCP servers as JSON" placeholder={'{\n  "github": {"command": "npx", "args": ["-y", "server-github"]}\n}'} value={mcp} onChange={e => {
        setMcp(e.target.value);
        try {
          update(setIn(overlay, ["mcp"], e.target.value.trim() ? JSON.parse(e.target.value) : undefined));
          setMcpError(null);
        } catch (err) {
          setMcpError((err as Error).message);
        }
      }} />
      {mcpError && <div className="problem">{mcpError}</div>}
    </>
  );
}

function JsonSection({ overlay, update, readOnly, name }: Editing & { name: string }) {
  const [text, setText] = useState(() => JSON.stringify(overlay, null, 2));
  const [error, setError] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!error) setText(JSON.stringify(overlay, null, 2));
    // Only follow edits while what's typed here is good
  }, [overlay]);
  const apply = (value: string) => {
    setText(value);
    try {
      const parsed = value.trim() ? JSON.parse(value) : {};
      if (typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("An engine is a JSON object");
      const { description: _, ...rest } = parsed;
      update(rest);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(overlay, null, 2) + "\n"], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${name.toLowerCase().replace(/[^a-z0-9_-]+/g, "-") || "engine"}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <>
      <h4>All of it, as JSON</h4>
      <p className="hint">Only what differs from the default. {EXPORT_NOTE}</p>
      <div className="editor-actions">
        <button type="button" onClick={download}>Download</button>
        <button type="button" onClick={() => navigator.clipboard.writeText(JSON.stringify(overlay, null, 2)).then(() => toast("Copied"), () => toast("Couldn't copy"))}>Copy</button>
        {!readOnly && <button type="button" onClick={() => file.current?.click()}>Import a file</button>}
        <input ref={file} type="file" accept="application/json,.json" hidden onChange={async e => {
          const picked = e.target.files?.[0];
          if (picked) apply(await picked.text());
          e.target.value = "";
        }} />
      </div>
      <textarea className="code json-text" rows={24} spellCheck={false} readOnly={readOnly} aria-label="The engine as JSON" value={text} onChange={e => apply(e.target.value)} />
      {error && <div className="problem">{error}</div>}
    </>
  );
}

function ChangeList({ changes }: { changes: EngineChange[] }) {
  return (
    <ul className="change-list">
      {changes.map(change => (
        <li key={change.where}>
          <code>{change.where}</code>
          <pre className="before">{shown(change.before)}</pre>
          <pre className="after">{shown(change.after)}</pre>
        </li>
      ))}
    </ul>
  );
}

function ChangesPanel({ overlay, saved, checked, readOnly, onApply }: { overlay: Overlay; saved: Overlay; checked: EngineCheck | null; readOnly: boolean; onApply: (overlay: Overlay) => void }) {
  const [ask, setAsk] = useState("");
  const [asking, setAsking] = useState(false);
  const [suggestion, setSuggestion] = useState<{ overlay: Overlay; changes: EngineChange[]; problems: string[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const unsaved = diff(saved, overlay);

  const suggest = async () => {
    setAsking(true);
    setError(null);
    setSuggestion(null);
    try {
      setSuggestion(await engineApi.suggest(overlay, ask));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAsking(false);
    }
  };

  return (
    <div className="engine-panel">
      {!readOnly && (
        <form className="ask-change" onSubmit={e => { e.preventDefault(); if (ask.trim()) suggest(); }}>
          <label className="field">
            <span>Describe a change</span>
            <textarea rows={2} value={ask} onChange={e => setAsk(e.target.value)} placeholder="Summarize earlier, and tell it to always run the tests" />
          </label>
          <button type="submit" disabled={asking || !ask.trim()}>{asking ? "Asking the model…" : "Suggest"}</button>
        </form>
      )}
      {error && <div className="problem">{error}</div>}
      {suggestion && (
        <div className="suggestion">
          <b>Suggested</b>
          {suggestion.problems.length > 0 && <div className="problem">{suggestion.problems.join("; ")}</div>}
          {suggestion.changes.length === 0 && !suggestion.problems.length && <p className="muted">It changes nothing.</p>}
          <ChangeList changes={suggestion.changes} />
          <div className="editor-actions">
            <button type="button" className="primary" disabled={suggestion.problems.length > 0 || !suggestion.changes.length} onClick={() => { onApply(suggestion.overlay); setSuggestion(null); setAsk(""); }}>Use this</button>
            <button type="button" onClick={() => setSuggestion(null)}>Discard</button>
          </div>
        </div>
      )}
      {unsaved.length > 0 && (
        <>
          <h4>Not saved yet</h4>
          <ChangeList changes={unsaved} />
        </>
      )}
      <h4>From the default</h4>
      {checked?.changes.length ? <ChangeList changes={checked.changes} /> : <p className="muted">Nothing: this does what labb does as it comes.</p>}
    </div>
  );
}

function VersionsPanel({ engine, current, onRestored }: { engine: Engine; current: Overlay; onRestored: (next: Engine) => void }) {
  const [versions, setVersions] = useState<EngineVersion[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => {
    if (engine.built_in) return;
    engineApi.versions(engine.id).then(setVersions, () => setVersions([]));
  }, [engine.id, engine.version, engine.built_in]);
  if (engine.built_in) return <div className="engine-panel"><p className="muted">The built-in changes with labb itself. Fork it to keep versions of your own.</p></div>;
  return (
    <div className="engine-panel">
      {!versions && <p className="muted">Loading…</p>}
      <ol className="version-list">
        {versions?.map(version => (
          <li key={version.version}>
            <div className="version-line">
              <button type="button" className="link" aria-expanded={open === version.version} onClick={() => setOpen(open === version.version ? null : version.version)}><b>v{version.version}</b> {version.note}</button>
              {version.version === engine.version ? <span className="model-tag">Current</span> : engine.mine && (
                <button type="button" onClick={async () => {
                  if (JSON.stringify(current) !== JSON.stringify(engine.overlay) && !confirm("Restoring drops your unsaved changes. Go on?")) return;
                  try {
                    onRestored(await engineApi.restore(engine.id, version.version));
                    toast(`Restored version ${version.version}`);
                  } catch (e) {
                    toast((e as Error).message);
                  }
                }}>Restore</button>
              )}
            </div>
            <span className="muted">{new Date(version.created_at).toLocaleString()} · labb {version.labb_version}</span>
            {open === version.version && <ChangeList changes={diff(version.overlay, engine.overlay)} />}
          </li>
        ))}
      </ol>
    </div>
  );
}
