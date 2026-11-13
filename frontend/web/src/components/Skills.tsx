import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { type Skill, addSkill, deleteSkill, draftSkill, getSkill, listSkills, skillDownloadUrl, updateSkill } from "../api";
import { relativeTime } from "../format";
import { NotFound } from "./NotFound";
import { toast } from "./Toaster";

const TEMPLATE = `---
name: meeting-notes
description: Turns rough meeting notes into a summary with decisions and action items. Use when someone pastes notes from a meeting.
---
# Meeting notes

1. Start with a two-sentence summary.
2. List the **decisions**, then the **action items** as "- [ ] who: what (by when)".
3. Keep the attendees' wording for anything that was agreed.
`;

function base64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = reader.result as string;
      resolve(url.slice(url.indexOf(",") + 1));
    };
    reader.onerror = () => reject(new Error(`Couldn't read ${file.name}`));
    reader.readAsDataURL(file);
  });
}

/** Your skills and the shared ones: write one, upload a SKILL.md or a zipped folder, share it */
export function SkillsTab({ editing, onEditing, onChanged }: {
  // What the address opens: "new", a skill's id (to edit or look at), or null for the list
  editing: string | null;
  onEditing: (id: string | null) => void;
  onChanged: () => void;
}) {
  const [items, setItems] = useState<Skill[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const setEditing = (skill: Skill | "new" | null) => onEditing(skill && skill !== "new" ? skill.id : skill);
  const edited = editing && editing !== "new" ? items?.find(s => s.id === editing) : null;
  const upload = useRef<HTMLInputElement>(null);

  const reload = useCallback(() => {
    listSkills().then(setItems, e => setError((e as Error).message));
  }, []);
  useEffect(reload, [reload]);

  const changed = () => {
    reload();
    onChanged();
  };

  const run = (action: () => Promise<unknown>, failed: string) =>
    action().then(changed, e => toast(`${failed}: ${(e as Error).message}`));

  const add = async (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    setError(null);
    try {
      const skill = file.name.toLowerCase().endsWith(".zip") ? await addSkill({ zip: await base64(file) }) : await addSkill({ skill_md: await file.text() });
      toast(`Added /${skill.name}`);
      changed();
    } catch (e) {
      setError(`${file.name}: ${(e as Error).message}`);
    }
  };

  if (editing && editing !== "new" && !edited) {
    if (!items) return error ? <div className="problem">{error}</div> : null;
    return <NotFound text="There's no skill here: it was deleted, or isn't shared any more." back="Back to the skills" onBack={() => setEditing(null)} />;
  }

  if (editing) {
    return (
      <SkillEditor
        key={editing}
        skill={edited ?? null}
        onDone={saved => {
          setEditing(null);
          if (saved) {
            toast(`Saved /${saved.name}`);
            changed();
          }
        }}
      />
    );
  }

  const mine = items?.filter(s => s.mine) ?? [];
  const shared = items?.filter(s => !s.mine) ?? [];

  const card = (skill: Skill) => (
    <li key={skill.id} className="agent-card">
      <button type="button" className="agent-main" onClick={() => setEditing(skill)} title={skill.mine ? "Edit" : "Look at it"}>
        <span className="agent-text">
          <b className="skill-name">/{skill.name}</b>
          <span>{skill.description}</span>
        </span>
      </button>
      <div className="agent-meta">
        {!skill.mine && <span className="muted">by {skill.owner ?? "someone"}</span>}
        {skill.mine && skill.shared && <span className="model-tag">Shared</span>}
        <span className="model-tag" title={skill.files.join("\n")}>
          {skill.files.length} file{skill.files.length === 1 ? "" : "s"}
        </span>
        <span className="muted">{relativeTime(skill.updated_at)}</span>
      </div>
      <div className="agent-actions">
        <button type="button" onClick={() => setEditing(skill)}>
          {skill.mine ? "Edit" : "Look"}
        </button>
        {skill.mine && (
          <button type="button" onClick={() => run(() => updateSkill(skill.id, { shared: !skill.shared }), "Couldn't change it")}>
            {skill.shared ? "Stop sharing" : "Share"}
          </button>
        )}
        <a className="button" href={skillDownloadUrl(skill.id)} download>
          Download
        </a>
        {skill.mine && (
          <button
            type="button"
            className="danger"
            onClick={() => confirm(`Delete /${skill.name}?`) && run(() => deleteSkill(skill.id), "Couldn't delete it")}
          >
            Delete
          </button>
        )}
      </div>
    </li>
  );

  return (
    <>
      <div className="agents-intro">
        <p className="hint">
          A skill teaches the assistant how to do one kind of task: a <code>SKILL.md</code> with a name, a description of when to use
          it, and instructions, plus any references it needs. The assistant loads one when a task matches its description, and{" "}
          <code>/name</code> in a chat uses it on purpose. labb on your PCs gets them too, along with the skills in{" "}
          <code>.labb/skills/</code> and <code>~/.labb/skills/</code>. Skills in the open Agent Skills format work as they are.
        </p>
        <div className="intro-actions">
          <button type="button" className="primary" onClick={() => setEditing("new")}>
            New skill
          </button>
          <button type="button" onClick={() => upload.current?.click()} title="A SKILL.md, or a zipped skill folder">
            Upload
          </button>
          <input
            ref={upload}
            type="file"
            accept=".zip,.md"
            hidden
            onChange={e => {
              add(e.target.files);
              e.target.value = "";
            }}
          />
        </div>
      </div>
      {error && <div className="problem">{error}</div>}
      <h3 className="agents-heading">Yours</h3>
      {items && mine.length === 0 && <p className="hint">No skills yet. Write one, or upload a SKILL.md or a zipped skill folder.</p>}
      {mine.length > 0 && <ul className="agent-grid">{mine.map(card)}</ul>}
      {shared.length > 0 && (
        <>
          <h3 className="agents-heading">Shared with everyone</h3>
          <ul className="agent-grid">{shared.map(card)}</ul>
        </>
      )}
    </>
  );
}

/**
 * Writing a skill with a prompt: say what it should do (or, with one written, what to change), and the
 * model writes the SKILL.md into the editor to look over. Ctrl/Cmd-Enter writes it.
 */
function DescribeSkill({ current, onWritten }: { current: string | null; onWritten: (skillMd: string) => void }) {
  const [ask, setAsk] = useState("");
  const [writing, setWriting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const changing = current !== null;

  const write = async () => {
    if (!ask.trim() || writing) return;
    setWriting(true);
    setError(null);
    try {
      const drafted = await draftSkill(ask, current ?? undefined);
      onWritten(drafted.skill_md);
      setAsk("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWriting(false);
    }
  };

  return (
    <div className="skill-describe">
      <label className="field">
        <span>
          {changing ? "Change it with a prompt" : "Describe it"}{" "}
          <small>
            {changing
              ? "say what to change, and the assistant rewrites the SKILL.md below for you to look over"
              : "say what the skill should do and when to use it, and the assistant writes the SKILL.md below for you to look over"}
          </small>
        </span>
        <textarea
          value={ask}
          onChange={e => setAsk(e.target.value)}
          onKeyDown={e => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              write();
            }
          }}
          rows={3}
          autoFocus={!changing}
          disabled={writing}
          placeholder={
            changing
              ? "e.g. Also ask who the audience is before writing, and keep it under 200 words"
              : "e.g. Turn a changelog or a list of merged PRs into release notes for users: grouped by new, improved and fixed, in plain words, with no internal ticket numbers"
          }
          aria-label={changing ? "What to change in the skill" : "What the skill should do"}
        />
      </label>
      <div className="skill-describe-actions">
        <button type="button" onClick={write} disabled={!ask.trim() || writing} title="Ctrl/Cmd-Enter">
          {writing ? "Writing…" : changing ? "Rewrite it" : "Write it"}
        </button>
        {writing && <span className="muted">This takes a few seconds</span>}
      </div>
      {error && <div className="problem">{error}</div>}
    </div>
  );
}

function SkillEditor({ skill, onDone }: { skill: Skill | null; onDone: (saved: Skill | null) => void }) {
  const [text, setText] = useState(skill ? "" : TEMPLATE);
  // A new skill's SKILL.md is the example until it's written or typed in
  const [drafted, setDrafted] = useState(Boolean(skill));
  const [shared, setShared] = useState(skill?.shared ?? false);
  const [loaded, setLoaded] = useState(!skill);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const readOnly = skill !== null && !skill.mine;

  useEffect(() => {
    if (!skill) return;
    getSkill(skill.id).then(
      full => (setText(full.skill_md), setLoaded(true)),
      e => setError((e as Error).message),
    );
  }, [skill]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSaving(true);
    try {
      onDone(skill ? await updateSkill(skill.id, { skill_md: text, shared }) : await addSkill({ skill_md: text, shared }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const others = skill?.files.filter(f => f !== "SKILL.md") ?? [];

  return (
    <form className="agent-form skill-form" onSubmit={save}>
      <div className="editor-head">
        <button type="button" className="link" onClick={() => onDone(null)}>
          ← All skills
        </button>
        <h3>{skill ? `/${skill.name}` : "New skill"}</h3>
        {readOnly && <p className="hint">Shared by {skill.owner}. Download it to make your own copy, or to use it in ~/.labb/skills/.</p>}
      </div>
      {!readOnly && loaded && (
        <DescribeSkill
          current={drafted ? text : null}
          onWritten={written => {
            setText(written);
            setDrafted(true);
          }}
        />
      )}
      <label className="field">
        <span>
          SKILL.md <small>{!drafted && "an example to start from, or write one above: "}a front matter with <code>name</code> (lowercase, hyphens) and <code>description</code> (what it does and when to use it), then the instructions</small>
        </span>
        <textarea
          className="code"
          value={text}
          onChange={e => {
            setText(e.target.value);
            setDrafted(true);
          }}
          rows={20}
          spellCheck={false}
          readOnly={readOnly || !loaded}
          aria-label="SKILL.md"
        />
      </label>
      {others.length > 0 && (
        <p className="hint">
          Its other files, from the upload: {others.join(", ")}. Upload the folder again to change them.
        </p>
      )}
      {!readOnly && (
        <label className="check share">
          <input type="checkbox" checked={shared} onChange={e => setShared(e.target.checked)} />
          <span>
            <b>Share with everyone on this server</b> <span className="muted">Their chats and labb can use it; only you can change it</span>
          </span>
        </label>
      )}
      {error && <div className="problem">{error}</div>}
      <div className="editor-actions">
        {!readOnly && (
          <button type="submit" className="primary" disabled={saving || !loaded}>
            {saving ? "Saving…" : skill ? "Save" : "Create"}
          </button>
        )}
        <button type="button" onClick={() => onDone(null)}>
          {readOnly ? "Back" : "Cancel"}
        </button>
      </div>
    </form>
  );
}
