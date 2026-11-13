import { type FormEvent, useEffect, useReducer, useRef, useState } from "react";
import { useLimits } from "../useLimits";
import {
  type Agent,
  type AgentDocuments,
  type AgentFields,
  type DocumentInfo,
  type Skill,
  type Model,
  type SendOptions,
  type ToolChoice,
  type Engine,
  engineApi,
  createAgent,
  deleteAgent,
  duplicateAgent,
  endTrial,
  listAgentTools,
  listDocuments,
  listSkills,
  streamChat,
  updateAgent,
} from "../api";
import { type Action, studio } from "../studioApi";
import { turnsReducer } from "../useChat";
import { MessageList } from "./MessageList";
import { SendIcon, StopIcon } from "./Icons";
import { NotFound } from "./NotFound";
import { toast } from "./Toaster";

const STARTERS = 4;

type Props = {
  agents: Agent[];
  // Whether `agents` came yet
  loaded: boolean;
  // What the address opens: "new", an agent's id, or null for the cards
  editing: string | null;
  onEditing: (id: string | null) => void;
  models: Model[];
  onChanged: () => void;
  // Start a chat with the agent
  onChat: (agent: Agent) => void;
  // Make a task for it
  onSchedule: (agent: Agent) => void;
};

/** Your agents and the shared ones, as cards; and the editor, with a chat to try it out */
export function AgentsTab({ agents, loaded, editing, onEditing, models, onChanged, onChat, onSchedule }: Props) {
  const setEditing = (agent: Agent | "new" | null) => onEditing(agent && agent !== "new" ? agent.id : agent);
  const edited = editing && editing !== "new" ? agents.find(a => a.id === editing && a.mine) : null;

  if (editing && editing !== "new" && !edited) {
    if (!loaded) return null;
    return <NotFound text="There's no agent of yours here: it was deleted, or it's someone else's." back="Back to the agents" onBack={() => setEditing(null)} />;
  }

  if (editing) {
    return (
      <AgentEditor
        key={editing}
        agent={edited ?? null}
        agents={agents}
        models={models}
        onDone={saved => {
          setEditing(null);
          onChanged();
          if (saved) toast(`Saved ${saved.name}`);
        }}
        onChat={agent => {
          onChanged();
          onChat(agent);
        }}
      />
    );
  }

  const mine = agents.filter(a => a.mine);
  const shared = agents.filter(a => !a.mine);

  const run = (action: () => Promise<unknown>, failed: string) =>
    action().then(onChanged, e => toast(`${failed}: ${(e as Error).message}`));

  const card = (agent: Agent) => (
    <li key={agent.id} className="agent-card">
      <button type="button" className="agent-main" onClick={() => onChat(agent)} title={`Chat with ${agent.name}`}>
        <span className="agent-emoji" aria-hidden>
          {agent.emoji}
        </span>
        <span className="agent-text">
          <b>{agent.name}</b>
          <span>{agent.description || (agent.instructions ? agent.instructions.slice(0, 120) : "No description")}</span>
        </span>
      </button>
      <div className="agent-meta">
        {agent.mine ? agent.shared && <span className="model-tag">Shared</span> : <span className="muted">by {agent.owner ?? "someone"}</span>}
        {agent.model && <span className="model-tag">{agent.model.split(":").slice(1).join(":")}</span>}
      </div>
      <div className="agent-actions">
        <button type="button" className="primary" onClick={() => onChat(agent)}>
          Chat
        </button>
        {agent.mine && (
          <button type="button" onClick={() => setEditing(agent)}>
            Edit
          </button>
        )}
        <button type="button" onClick={() => onSchedule(agent)} title="Have it do something on a schedule">
          Schedule
        </button>
        <button
          type="button"
          onClick={() => run(() => duplicateAgent(agent.id), "Couldn't duplicate it")}
          title="Make your own copy to change"
        >
          Duplicate
        </button>
        {agent.mine && (
          <button
            type="button"
            className="danger"
            onClick={() => {
              if (confirm(`Delete ${agent.name}? Its chats stay, and go on with labb.`)) run(() => deleteAgent(agent.id), "Couldn't delete it");
            }}
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
          An agent is an assistant set up for one job: its own instructions, model, tools and documents. Start a chat with one here, or
          pick it next to the model in a new chat. Share one and everyone on this server can use it; their chats and memories stay
          their own.
        </p>
        <button type="button" className="primary" onClick={() => setEditing("new")}>
          New agent
        </button>
      </div>
      <h3 className="agents-heading">Yours</h3>
      {mine.length ? (
        <ul className="agent-grid">{mine.map(card)}</ul>
      ) : (
        <p className="hint">No agents yet. Make one for something you ask about often: a cook, a tutor, a helpdesk for your documents.</p>
      )}
      {shared.length > 0 && (
        <>
          <h3 className="agents-heading">Shared with everyone</h3>
          <ul className="agent-grid">{shared.map(card)}</ul>
        </>
      )}
    </>
  );
}

type Draft = Required<Omit<AgentFields, "toolsets">> & { toolsets: string[] | null };

function draftOf(agent: Agent | null): Draft {
  return {
    name: agent?.name ?? "",
    emoji: agent?.emoji ?? "🤖",
    description: agent?.description ?? "",
    instructions: agent?.instructions ?? "",
    model: agent?.model ?? null,
    toolsets: agent?.toolsets ?? null,
    documents: agent?.documents ?? "mine",
    skills: agent?.skills ?? "mine",
    memories: agent?.memories ?? true,
    starters: [...(agent?.starters ?? []), ...Array(STARTERS).fill("")].slice(0, STARTERS),
    shared: agent?.shared ?? false,
    delegates: agent?.delegates ?? [],
    actions: agent?.actions ?? [],
    engine_id: agent?.engine_id ?? null,
  };
}

function AgentEditor({ agent, agents, models, onDone, onChat }: {
  agent: Agent | null;
  agents: Agent[];
  models: Model[];
  onDone: (saved: Agent | null) => void;
  onChat: (agent: Agent) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(agent));
  const limits = useLimits();
  const [tools, setTools] = useState<ToolChoice[]>([]);
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [actions, setActions] = useState<Action[]>([]);
  const [engines, setEngines] = useState<Engine[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    listAgentTools().then(setTools, () => setTools([]));
    listDocuments().then(setDocuments, () => setDocuments([]));
    studio.actions().then(setActions, () => setActions([]));
    engineApi.list().then(list => setEngines(list.engines), () => setEngines([]));
    // The ones you can give it: yours and the shared ones, one per name
    listSkills().then(all => {
      const seen = new Set<string>();
      setSkills(all.filter(s => !seen.has(s.name) && seen.add(s.name)));
    }, () => setSkills([]));
  }, []);

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft(d => ({ ...d, [key]: value }));
  const fields = (): AgentFields => ({ ...draft, starters: draft.starters.filter(s => s.trim()) });

  // All the tools ticked is saved as "all", so tools added later come too
  const toolOn = (name: string) => draft.toolsets === null || draft.toolsets.includes(name);
  const toggleTool = (name: string) => {
    const current = draft.toolsets ?? tools.map(t => t.name);
    const next = current.includes(name) ? current.filter(t => t !== name) : [...current, name];
    set("toolsets", tools.every(t => next.includes(t.name)) ? null : next);
  };

  const mode = Array.isArray(draft.documents) ? "some" : draft.documents;
  const picked = Array.isArray(draft.documents) ? draft.documents : [];
  const setMode = (value: string) => set("documents", (value === "some" ? picked : value) as AgentDocuments);
  const skillMode = Array.isArray(draft.skills) ? "some" : draft.skills;
  const pickedSkills = Array.isArray(draft.skills) ? draft.skills : [];
  const setSkillMode = (value: string) => set("skills", value === "some" ? pickedSkills : (value as "mine" | "none"));
  const toggleSkill = (name: string) =>
    set("skills", pickedSkills.includes(name) ? pickedSkills.filter(s => s !== name) : [...pickedSkills, name]);
  const toggle = (key: "delegates" | "actions", id: string) =>
    set(key, draft[key].includes(id) ? draft[key].filter(x => x !== id) : [...draft[key], id]);
  const toggleDocument = (id: string) => set("documents", picked.includes(id) ? picked.filter(d => d !== id) : [...picked, id]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSaving(true);
    try {
      const saved = agent ? await updateAgent(agent.id, fields()) : await createAgent(fields());
      onDone(saved);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const defaultModel = models[0]?.name;

  return (
    <div className="agent-editor">
      <form className="agent-form" onSubmit={save}>
        <div className="editor-head">
          <button type="button" className="link" onClick={() => onDone(null)}>
            ← All agents
          </button>
          <h3>{agent ? `Edit ${agent.name}` : "New agent"}</h3>
        </div>

        <div className="field-row">
          <label className="field emoji-field">
            <span>Icon</span>
            <input value={draft.emoji} onChange={e => set("emoji", e.target.value)} maxLength={16} aria-label="Icon (an emoji)" />
          </label>
          <label className="field grow">
            <span>Name</span>
            <input value={draft.name} onChange={e => set("name", e.target.value)} maxLength={60} placeholder="e.g. Chef" required autoFocus={!agent} />
          </label>
        </div>
        <label className="field">
          <span>Description</span>
          <input
            value={draft.description}
            onChange={e => set("description", e.target.value)}
            maxLength={200}
            placeholder="One line on what it's for, shown on its card"
          />
        </label>
        <label className="field">
          <span>
            Instructions <small>{draft.instructions.length.toLocaleString()} / {limits.agent_instructions_chars.toLocaleString()}</small>
          </span>
          <textarea
            value={draft.instructions}
            onChange={e => set("instructions", e.target.value)}
            maxLength={limits.agent_instructions_chars}
            rows={8}
            placeholder={"What it does and how it answers, e.g.\nYou're a home cook's helper. Suggest recipes from what's in the fridge, metric units, at most 30 minutes."}
          />
        </label>
        <label className="field">
          <span>Model</span>
          <select value={draft.model ?? ""} onChange={e => set("model", e.target.value || null)}>
            <option value="">The server's default{defaultModel ? ` (${defaultModel})` : ""}</option>
            {models.map(m => (
              <option key={m.spec} value={m.spec}>
                {m.spec}
              </option>
            ))}
            {draft.model && !models.some(m => m.spec === draft.model) && <option value={draft.model}>{draft.model}</option>}
          </select>
        </label>

        <label className="field">
          <span>Engine</span>
          <select value={draft.engine_id ?? ""} onChange={e => set("engine_id", e.target.value || null)}>
            <option value="">Whoever chats, their own</option>
            {engines.filter(h => !h.web_problems.length).map(h => <option key={h.id} value={h.id}>{h.name}</option>)}
          </select>
          <small>The version of the agent's core its chats, tasks and workflows use.</small>
        </label>

        <fieldset className="field">
          <legend>Tools</legend>
          {tools.map(tool => (
            <label key={tool.name} className="check">
              <input type="checkbox" checked={toolOn(tool.name)} onChange={() => toggleTool(tool.name)} />
              <span>
                <b>{tool.label}</b> <span className="muted">{tool.description}</span>
              </span>
            </label>
          ))}
          <label className="check">
            <input type="checkbox" checked={draft.memories} onChange={e => set("memories", e.target.checked)} />
            <span>
              <b>Memories</b> <span className="muted">Uses the memories of whoever chats, and can save new ones</span>
            </span>
          </label>
        </fieldset>

        <fieldset className="field">
          <legend>Documents it searches</legend>
          <label className="check">
            <input type="radio" name="documents" checked={mode === "mine"} onChange={() => setMode("mine")} />
            <span>Whoever chats, their own documents</span>
          </label>
          <label className="check">
            <input type="radio" name="documents" checked={mode === "some"} onChange={() => setMode("some")} />
            <span>Only some of yours</span>
          </label>
          {mode === "some" && (
            <div className="document-picks">
              {documents.length === 0 && <p className="hint">You have no documents yet. Add them on the Documents page.</p>}
              {documents.map(doc => (
                <label key={doc.id} className="check">
                  <input type="checkbox" checked={picked.includes(doc.id)} onChange={() => toggleDocument(doc.id)} />
                  <span>{doc.name}</span>
                </label>
              ))}
              {draft.shared && picked.length > 0 && (
                <p className="warning">It's shared: everyone who chats with it can read passages of these documents.</p>
              )}
            </div>
          )}
          <label className="check">
            <input type="radio" name="documents" checked={mode === "none"} onChange={() => setMode("none")} />
            <span>None</span>
          </label>
        </fieldset>

        <fieldset className="field">
          <legend>Skills it uses</legend>
          <label className="check">
            <input type="radio" name="skills" checked={skillMode === "mine"} onChange={() => setSkillMode("mine")} />
            <span>
              Whoever chats, their own skills <span className="muted">and it can save new ones when asked</span>
            </span>
          </label>
          <label className="check">
            <input type="radio" name="skills" checked={skillMode === "some"} onChange={() => setSkillMode("some")} />
            <span>Only some</span>
          </label>
          {skillMode === "some" && (
            <div className="document-picks">
              {skills.length === 0 && <p className="hint">No skills yet. Add them on the Skills page.</p>}
              {skills.map(skill => (
                <label key={skill.name} className="check">
                  <input type="checkbox" checked={pickedSkills.includes(skill.name)} onChange={() => toggleSkill(skill.name)} />
                  <span>
                    <b className="skill-name">/{skill.name}</b> <span className="muted">{skill.description}</span>
                  </span>
                </label>
              ))}
            </div>
          )}
          <label className="check">
            <input type="radio" name="skills" checked={skillMode === "none"} onChange={() => setSkillMode("none")} />
            <span>None</span>
          </label>
        </fieldset>

        <fieldset className="field">
          <legend>Agents it can ask</legend>
          <p className="hint">It can hand a question to these, and use their answer. They run as whoever is chatting.</p>
          <div className="document-picks">
            {agents.filter(a => a.id !== agent?.id).length === 0 && <p className="hint">No other agents yet.</p>}
            {agents.filter(a => a.id !== agent?.id).map(other => (
              <label key={other.id} className="check">
                <input type="checkbox" checked={draft.delegates.includes(other.id)} onChange={() => toggle("delegates", other.id)} />
                <span>
                  {other.emoji} <b>{other.name}</b> <span className="muted">{other.description}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset className="field">
          <legend>Actions it can use</legend>
          <p className="hint">Studio's actions, e.g. an HTTP request to another app. Make them in Studio → Actions.</p>
          <div className="document-picks">
            {actions.length === 0 && <p className="hint">No actions yet.</p>}
            {actions.map(action => (
              <label key={action.id} className="check">
                <input type="checkbox" checked={draft.actions.includes(action.id)} onChange={() => toggle("actions", action.id)} />
                <span>
                  {action.emoji} <b>{action.name}</b> <span className="muted">{action.description}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset className="field">
          <legend>Conversation starters</legend>
          {draft.starters.map((starter, i) => (
            <input
              key={i}
              value={starter}
              maxLength={200}
              aria-label={`Conversation starter ${i + 1}`}
              placeholder={i === 0 ? "e.g. What can I make with eggs and spinach?" : ""}
              onChange={e => set("starters", draft.starters.map((s, j) => (j === i ? e.target.value : s)))}
            />
          ))}
        </fieldset>

        <label className="check share">
          <input type="checkbox" checked={draft.shared} onChange={e => set("shared", e.target.checked)} />
          <span>
            <b>Share with everyone on this server</b>{" "}
            <span className="muted">They can chat with it and copy it; only you can change it</span>
          </span>
        </label>

        {error && <div className="problem">{error}</div>}
        <div className="editor-actions">
          <button type="submit" className="primary" disabled={saving}>
            {saving ? "Saving…" : agent ? "Save" : "Create"}
          </button>
          {agent && (
            <button
              type="button"
              disabled={saving}
              onClick={async () => {
                try {
                  onChat(await updateAgent(agent.id, fields()));
                } catch (e) {
                  setError((e as Error).message);
                }
              }}
            >
              Save and chat
            </button>
          )}
          <button type="button" onClick={() => onDone(null)}>
            Cancel
          </button>
        </div>
      </form>
      <TryChat
        options={{ draft: { ...fields(), name: draft.name.trim() || "Agent" } }}
        who={{ emoji: draft.emoji ?? "🤖", name: draft.name.trim() || "Your agent", description: draft.description ?? "", starters: draft.starters ?? [] }}
      />
    </div>
  );
}

/** A chat with something as it's in its editor: each message uses the latest changes. It isn't kept. */
export function TryChat({ options, who, note = "Uses your changes as you make them; not saved", placeholder = "Message your agent" }: {
  options: SendOptions;
  who: { emoji: string; name: string; description: string; starters: string[] };
  note?: string;
  placeholder?: string;
}) {
  const [turns, dispatch] = useReducer(turnsReducer, []);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [text, setText] = useState("");
  const controller = useRef<AbortController | null>(null);
  const thread = useRef<string | null>(null);
  thread.current = threadId;

  // The server forgets the trial when the editor closes
  useEffect(
    () => () => {
      controller.current?.abort();
      if (thread.current) endTrial(thread.current);
    },
    [],
  );

  const send = async (message: string) => {
    if (!message.trim() || streaming) return;
    setText("");
    const abort = new AbortController();
    controller.current = abort;
    setStreaming(true);
    dispatch({ type: "event", event: { type: "user", content: message }, now: Date.now() });
    try {
      await streamChat(
        message,
        thread.current,
        options,
        event => {
          if (event.type === "thread") setThreadId((thread.current = event.thread_id));
          else dispatch({ type: "event", event, now: Date.now() });
        },
        abort.signal,
      );
    } catch (e) {
      if (!abort.signal.aborted) dispatch({ type: "event", event: { type: "error", message: (e as Error).message } });
    } finally {
      dispatch({ type: "aborted" });
      setStreaming(false);
    }
  };

  const restart = () => {
    controller.current?.abort();
    if (thread.current) endTrial(thread.current);
    setThreadId((thread.current = null));
    dispatch({ type: "reset" });
  };

  return (
    <section className="try-chat" aria-label="Try it">
      <div className="try-head">
        <b>Try it</b>
        <span className="muted">{note}</span>
        {turns.length > 0 && (
          <button type="button" className="link" onClick={restart}>
            Start over
          </button>
        )}
      </div>
      <MessageList
        turns={turns}
        loading={false}
        error={null}
        threadId={threadId}
        name=""
        agent={who}
        canChange={false}
        editing={null}
        onEdit={() => {}}
        onSaveEdit={() => {}}
        onRegenerate={() => {}}
        onAsk={send}
      />
      <form
        className="try-input"
        onSubmit={event => {
          event.preventDefault();
          send(text);
        }}
      >
        <input value={text} onChange={e => setText(e.target.value)} placeholder={placeholder} aria-label={placeholder} />
        {streaming ? (
          <button type="button" className="round-button stop" aria-label="Stop" onClick={() => controller.current?.abort()}>
            <StopIcon />
          </button>
        ) : (
          <button type="submit" className="round-button send" aria-label="Send" disabled={!text.trim()}>
            <SendIcon />
          </button>
        )}
      </form>
    </section>
  );
}
