import { type FormEvent, useCallback, useEffect, useState } from "react";
import { type App, type Notes, type NotesChange, type NotesSource, type Pc, changeNotes, getNotes, listApps, listPcs } from "../api";
import { Markdown } from "./Markdown";

// A part of the notes: what's under one heading (or before the first), as lines [start, end)
type Section = { heading: string | null; text: string; start: number; end: number };

const HEADING = /^#{1,6}\s+\S/;

/** The notes split at their headings */
export function sectionsOf(text: string): Section[] {
  const lines = text.split("\n");
  const found: Section[] = [];
  let start = 0;
  const close = (end: number) => {
    const part = lines.slice(start, end).join("\n").trim();
    if (part) found.push({ heading: HEADING.test(lines[start]) ? lines[start].replace(/^#+\s*/, "").trim() : null, text: part, start, end });
  };
  lines.forEach((line, i) => {
    if (i > 0 && HEADING.test(line)) {
      close(i);
      start = i;
    }
  });
  close(lines.length);
  return found;
}

/** The notes with one section's lines replaced (by nothing, to delete it) */
function withSection(text: string, section: Section, replacement: string) {
  const lines = text.split("\n");
  const next = [...lines.slice(0, section.start), ...(replacement.trim() ? [replacement.trim(), ""] : []), ...lines.slice(section.end)];
  return next.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

// The page's address: /notes for yours, /notes/app/<app id> for an app's, /notes/<pc id>/<folder> for a PC folder's
function sourceOf(route: string[]): NotesSource {
  if (route[0] === "app" && route.length === 2) return { appId: route[1] };
  return route.length >= 2 ? { pcId: route[0], folder: route.slice(1).join("/") } : null;
}

// The select's value for a place: "" yours, app/<id>, <pc id>/<folder>
const keyOf = (source: NotesSource) => (!source ? "" : "appId" in source ? `app/${source.appId}` : `${source.pcId}/${source.folder}`);

function SectionView({ section, busy, onSave, onDelete }: { section: Section; busy: boolean; onSave: (text: string) => Promise<boolean>; onDelete: () => void }) {
  const [editing, setEditing] = useState<string | null>(null);
  const body = section.heading ? section.text.split("\n").slice(1).join("\n") : section.text;
  return (
    <section className="note">
      {editing !== null ? (
        <form
          className="field"
          onSubmit={async event => {
            event.preventDefault();
            if (await onSave(editing)) setEditing(null);
          }}
        >
          <textarea value={editing} onChange={e => setEditing(e.target.value)} aria-label="This note" autoFocus />
          <div className="note-actions">
            <button type="submit" className="primary" disabled={busy}>
              Save
            </button>
            <button type="button" onClick={() => setEditing(null)}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <>
          <div className="note-head">
            <h3>{section.heading ?? "Without a topic"}</h3>
            <span className="row-actions">
              <button type="button" onClick={() => setEditing(section.text)} disabled={busy}>
                Edit
              </button>
              <button type="button" className="danger" onClick={onDelete} disabled={busy}>
                Delete
              </button>
            </span>
          </div>
          {body.trim() && <Markdown text={body} />}
        </>
      )}
    </section>
  );
}

/** The notes the agent keeps as it works: yours (the web chat's, tasks' and workflows'), an app's (its chat's), or a project's on a PC */
export function NotesTab({ route, onRoute }: { route: string[]; onRoute: (rest: string[]) => void }) {
  const source = sourceOf(route);
  const key = keyOf(source);
  const [pcs, setPcs] = useState<Pc[]>([]);
  const [apps, setApps] = useState<App[]>([]);
  const [notes, setNotes] = useState<Notes | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [topic, setTopic] = useState("");
  const [text, setText] = useState("");
  const [all, setAll] = useState<string | null>(null);

  useEffect(() => {
    listPcs().then(setPcs, () => setPcs([]));
    // Without apps on this server, there are none to list
    listApps().then(setApps, () => setApps([]));
  }, []);

  const reload = useCallback(() => {
    setNotes(null);
    setError(null);
    setAll(null);
    getNotes(sourceOf(key ? key.split("/") : [])).then(setNotes, e => setError((e as Error).message));
  }, [key]);
  useEffect(reload, [reload]);

  /** Change the notes; false (and the reason shown) if it didn't work */
  const change = async (what: NotesChange) => {
    setBusy(true);
    setError(null);
    try {
      setNotes(await changeNotes(source, what));
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const replace = (next: string) => change({ action: "replace", text: next, base: notes?.digest });

  const add = async (event: FormEvent) => {
    event.preventDefault();
    if (await change({ action: "add", text, topic })) {
      setText("");
      setTopic("");
    }
  };

  const folders = pcs.flatMap(pc => pc.folders.map(folder => ({ pc, folder, value: `${pc.id}/${folder}` })));
  const sections = notes ? sectionsOf(notes.text) : [];
  return (
    <>
      <p className="hint">
        What the agent writes down as it works, so it can pick a job up later: <b>your notes</b> are the web chat's, tasks' and
        workflows' (only you see them), an <b>app's</b> are what its chat keeps about it (it doesn't see yours), and a{" "}
        <b>PC folder's</b> are what labb keeps in that project (<code>.labb/notes.md</code>), while the PC is connected. You can
        add and change them too.
      </p>
      <div className="inline-form">
        <select
          className="notes-source"
          aria-label="Whose notes"
          value={key}
          onChange={e => {
            const value = e.target.value;
            const chosen = folders.find(f => f.value === value);
            onRoute(value.startsWith("app/") ? ["app", value.slice(4)] : chosen ? [chosen.pc.id, chosen.folder] : []);
          }}
        >
          <option value="">Your notes</option>
          {apps.length > 0 && (
            <optgroup label="Apps">
              {apps.map(app => (
                <option key={app.id} value={`app/${app.id}`}>
                  {app.name}
                </option>
              ))}
            </optgroup>
          )}
          {folders.map(({ pc, folder, value }) => (
            <option key={value} value={value} disabled={!pc.online && value !== key}>
              {pc.name}: {folder}
              {pc.online ? "" : " (not connected)"}
            </option>
          ))}
        </select>
        <button type="button" onClick={reload} disabled={busy}>
          Reload
        </button>
        {notes && all === null && (
          <button type="button" onClick={() => setAll(notes.text)} disabled={busy}>
            Edit all
          </button>
        )}
      </div>
      {error && (
        <div className="problem">
          {error}{" "}
          {notes && (
            <button type="button" className="link" onClick={reload}>
              Load them again
            </button>
          )}
        </div>
      )}
      {notes && all !== null && (
        <form
          className="field"
          onSubmit={async event => {
            event.preventDefault();
            if (await replace(all)) setAll(null);
          }}
        >
          <textarea className="notes-all" value={all} onChange={e => setAll(e.target.value)} aria-label="All the notes" autoFocus />
          <div className="note-actions">
            <button type="submit" className="primary" disabled={busy}>
              Save
            </button>
            <button type="button" onClick={() => setAll(null)}>
              Cancel
            </button>
            <span className="hint">Markdown; each “## topic” heading starts a note.</span>
          </div>
        </form>
      )}
      {notes && all === null && (
        <>
          <form className="note note-new field" onSubmit={add}>
            <input placeholder="Topic, e.g. Deploying" value={topic} onChange={e => setTopic(e.target.value)} maxLength={120} aria-label="Topic" />
            <textarea placeholder="What to remember (Markdown)" value={text} onChange={e => setText(e.target.value)} required aria-label="Note" />
            <div className="note-actions">
              <button type="submit" className="primary" disabled={busy || !text.trim()}>
                Add a note
              </button>
            </div>
          </form>
          {sections.length ? (
            [...sections].reverse().map(section => (
              <SectionView
                key={`${notes.digest}-${section.start}`}
                section={section}
                busy={busy}
                onSave={edited => replace(withSection(notes.text, section, edited))}
                onDelete={() => {
                  if (confirm(`Delete “${section.heading ?? "the note without a topic"}”?`)) void replace(withSection(notes.text, section, ""));
                }}
              />
            ))
          ) : (
            <p className="hint">No notes yet.</p>
          )}
        </>
      )}
      {!notes && !error && <p className="hint">Loading…</p>}
    </>
  );
}
