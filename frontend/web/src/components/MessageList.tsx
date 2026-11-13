import { ArtifactCard, publishedUrl } from "./ArtifactCard";
import { BrowserLive } from "./BrowserLive";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { answerApproval, answerQuestion, getHeartbeat, getToolResult } from "../api";
import { Link, pathOf } from "../router";
import type { Part, Turn } from "../useChat";
import { CopyButton } from "./CopyButton";
import { ArrowDownIcon, RegenerateIcon } from "./Icons";
import { Markdown } from "./Markdown";
import { ReadAloudButton } from "./ReadAloudButton";
import { StatsLine } from "./StatsLine";
import { type TodoItem, TodoList } from "./TodoList";
import { useSpeech } from "../speech";

const number = new Intl.NumberFormat();

function formatArgs(args: Record<string, unknown>) {
  return Object.values(args)
    .map(value => (typeof value === "string" ? value : JSON.stringify(value)))
    .join(", ");
}

/** The model's reasoning, folded into one line */
function ThinkingView({ part }: { part: Extract<Part, { kind: "thinking" }> }) {
  const label = !part.done ? "Thinking…" : part.seconds !== undefined ? `Thought for ${part.seconds.toFixed(1)}s` : "Thought";
  return (
    <details className="thought">
      <summary>{label}</summary>
      <div className="thought-text">{part.text.trim()}</div>
    </details>
  );
}

/** A tool call; click it to see its arguments and what it returned */
function ToolView({ part, threadId }: { part: Extract<Part, { kind: "tool" }>; threadId: string | null }) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<{ content: string | null } | { error: string } | null>(null);
  const finished = part.chars !== undefined;
  const published = part.name === "publish_artifact" ? publishedUrl(part.firstLine) : null;

  useEffect(() => {
    if (!open || result || !finished || !part.id || !threadId) return;
    getToolResult(threadId, part.id)
      .then(r => setResult({ content: r.content }))
      .catch(e => setResult({ error: (e as Error).message }));
  }, [open, result, finished, part.id, threadId]);

  return (
    <div className={open ? "tool open" : "tool"}>
      <button type="button" className="tool-line" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="caret">{open ? "▾" : "▸"}</span> {part.name.startsWith("step: ") ? part.name : `${part.name}(${formatArgs(part.args)})`}
        {finished && (
          <span className={part.error ? "bad" : "ok"}>
            {part.error ? " ✗" : " ✓"} {number.format(part.chars!)} chars
          </span>
        )}
      </button>
      {part.name === "update_todos" && !open && Array.isArray(part.args.todos) && <TodoList items={part.args.todos as TodoItem[]} />}
      {part.name === "publish_artifact" && !part.error && published && <ArtifactCard url={published} name={String(part.args.name ?? "")} />}
      {open && (
        <div className="tool-detail">
          <div className="label">Arguments</div>
          <pre>{JSON.stringify(part.args, null, 2)}</pre>
          <div className="label">Result</div>
          {!finished ? (
            <pre className="muted">Running…</pre>
          ) : !part.id || !threadId ? (
            <pre className="muted">Not available</pre>
          ) : result === null ? (
            <pre className="muted">Loading…</pre>
          ) : "error" in result ? (
            <pre className="bad">{result.error}</pre>
          ) : (
            <pre>{result.content ?? "(no result)"}</pre>
          )}
        </div>
      )}
    </div>
  );
}

/** A tool that changes something asks first (an MCP server's): Allow or Don't allow */
/** What an agent on a PC asks: pick an option, or say something else */
function QuestionView({ part }: { part: Extract<Part, { kind: "question" }> }) {
  const [text, setText] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const send = (answer: string) => {
    setError(null);
    setSent(answer);
    answerQuestion(part.id, answer).catch(e => {
      setSent(null);
      setError((e as Error).message);
    });
  };

  const closed = part.answer ?? sent ?? (part.expired ? "Not answered" : null);
  return (
    <div className="approval" role="group" aria-label="A question">
      <div className="approval-title">{part.question}</div>
      {closed !== null ? (
        <div className="hint">{closed}</div>
      ) : (
        <>
          <div className="approval-actions question-options">
            {part.options.map((option, index) => (
              <button key={option} type="button" className={index === 0 ? "primary" : undefined} onClick={() => send(option)}>
                {option}
              </button>
            ))}
          </div>
          <form
            onSubmit={event => {
              event.preventDefault();
              if (text.trim()) send(text.trim());
            }}
          >
            <input placeholder="Or type an answer, then Enter" aria-label="Your answer" value={text} onChange={event => setText(event.target.value)} />
          </form>
        </>
      )}
      {error && <div className="bad">{error}</div>}
    </div>
  );
}

function ApprovalView({ part }: { part: Extract<Part, { kind: "approval" }> }) {
  const [answer, setAnswer] = useState<"allowed" | "denied" | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const send = async (allow: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await answerApproval(part.id, allow, allow ? undefined : reason.trim());
      setAnswer(allow ? "allowed" : "denied");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const closed = answer ?? part.answer ?? null;
  return (
    <div className="approval" role="group" aria-label={part.title}>
      <div className="approval-title">{part.title}</div>
      <pre>{part.detail}</pre>
      {closed ? (
        <div className="hint">{closed === "expired" ? "Not answered" : part.handover ? (closed === "allowed" ? "Done" : "Handed back") : closed === "allowed" ? "Allowed" : "Not allowed"}</div>
      ) : (
        <>
          <input
            placeholder={part.handover ? "If you can't: why, or what to do instead (optional)" : "Why not, or what to do instead (optional)"}
            value={reason}
            onChange={event => setReason(event.target.value)}
            aria-label="Why not"
          />
          <div className="approval-actions">
            <button type="button" onClick={() => send(false)} disabled={busy}>
              {part.handover ? "I can't do this" : "Don't allow"}
            </button>
            <button type="button" className="primary" onClick={() => send(true)} disabled={busy}>
              {part.handover ? "I'm done" : "Allow"}
            </button>
          </div>
        </>
      )}
      {error && <div className="bad">{error}</div>}
      {/* Under the answer, so a tall page never pushes it out of reach */}
      {part.view && !closed && (
        <div className={part.handover ? undefined : "approval-preview"}>
          <BrowserLive threadId={part.view} interactive={part.handover} />
        </div>
      )}
    </div>
  );
}

function PartView({ part, threadId, writing }: { part: Part; threadId: string | null; writing: boolean }) {
  switch (part.kind) {
    case "text":
      return (
        // `writing`: the text the model is still writing, which ends with a cursor
        <div className={writing ? "msg assistant writing" : "msg assistant"}>
          <Markdown text={part.text} />
        </div>
      );
    case "thinking":
      return <ThinkingView part={part} />;
    case "tool":
      return <ToolView part={part} threadId={threadId} />;
    case "compact":
      return <div className="tool">🗜 Summarized the earlier messages to fit the model's context</div>;
    case "note":
      return <div className="tool">↻ {part.text}</div>;
    case "approval":
      return <ApprovalView part={part} />;
    case "question":
      return <QuestionView part={part} />;
    case "error":
      return <div className="msg error">Error: {part.message}</div>;
  }
}

/** Editing a sent message in place */
function EditBox({ turn, onSave, onCancel }: { turn: Turn; onSave: (text: string) => void; onCancel: () => void }) {
  const [text, setText] = useState(turn.user);
  const input = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = "";
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);

  useEffect(() => {
    const el = input.current;
    el?.focus();
    el?.setSelectionRange(el.value.length, el.value.length);
  }, []);

  const save = () => text.trim() && onSave(text.trim());
  const kept = turn.files.length + turn.images.length;

  return (
    <div className="edit-box">
      <textarea
        ref={input}
        value={text}
        rows={1}
        onChange={event => setText(event.target.value)}
        onKeyDown={event => {
          if (event.key === "Escape") onCancel();
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            save();
          }
        }}
      />
      <div className="edit-actions">
        <span className="hint">
          Replaces this message and everything after it{kept ? `, keeping its ${kept === 1 ? "attachment" : "attachments"}` : ""}.
        </span>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className="primary" onClick={save} disabled={!text.trim()}>
          Send
        </button>
      </div>
    </div>
  );
}

type TurnProps = {
  turn: Turn;
  threadId: string | null;
  isLast: boolean;
  // Editing and regenerating; not for terminal sessions, or while a reply streams
  canChange: boolean;
  editing: boolean;
  onEdit: () => void;
  onCancelEdit: () => void;
  onSaveEdit: (text: string) => void;
  onRegenerate: () => void;
  // The server has a speaking model: replies can be read aloud
  canSpeak: boolean;
  index: number;
};

function TurnView({ turn, threadId, isLast, canChange, editing, onEdit, onCancelEdit, onSaveEdit, onRegenerate, canSpeak, index }: TurnProps) {
  const streaming = turn.status === "streaming";
  const text = turn.parts
    .filter(part => part.kind === "text")
    .map(part => part.text)
    .join("\n\n");

  return (
    <>
      {editing ? (
        <EditBox turn={turn} onSave={onSaveEdit} onCancel={onCancelEdit} />
      ) : (
        <div className="user-row">
          {canChange && (
            <button type="button" className="edit" onClick={onEdit} title="Edit and send again">
              Edit
            </button>
          )}
          <div className="msg user">
            {turn.images.length > 0 && (
              <div className="images">
                {turn.images.map((src, i) => (
                  <a key={i} href={src} target="_blank" rel="noopener noreferrer">
                    <img src={src} alt={`Attached image ${i + 1}`} />
                  </a>
                ))}
              </div>
            )}
            {turn.user}
            {turn.files.length > 0 && (
              <div className="files">
                {turn.files.map(name => (
                  <span key={name} className="file-chip">
                    📄 {name}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
      {!editing &&
        turn.parts.map((part, i) => (
          <PartView key={i} part={part} threadId={threadId} writing={streaming && i === turn.parts.length - 1} />
        ))}
      {!editing && streaming && turn.parts.length === 0 && <div className="thinking">thinking…</div>}
      {!editing && (
        <div className="turn-footer">
          {turn.status === "stopped" && <span className="stopped">Stopped</span>}
          <StatsLine stats={turn.stats} startedAt={turn.startedAt} streaming={streaming} />
          <span className="turn-actions">
            {!streaming && text && <CopyButton getText={() => text} />}
            {!streaming && text && canSpeak && <ReadAloudButton getText={() => text} id={`${threadId}:${index}`} />}
            {isLast && canChange && (
              <button type="button" className="copy" onClick={onRegenerate} title="Write this reply again">
                <RegenerateIcon /> Regenerate
              </button>
            )}
          </span>
        </div>
      )}
    </>
  );
}

// Shown in an empty chat; clicking one asks it
const SUGGESTIONS = [
  { title: "Top security stories", prompt: "What are the top security stories in the news today?" },
  { title: "Explain a concept", prompt: "Explain how public-key cryptography works, with a simple example." },
  { title: "Read a page for me", prompt: "Read https://example.com and summarize it in three bullet points." },
  { title: "Write some code", prompt: "Write a Python function that removes duplicate lines from a file, keeping their order." },
];

function greeting(name: string) {
  const hour = new Date().getHours();
  const time = hour < 5 ? "Good night" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  return `${time}, ${name}`;
}

// What an empty chat with an agent shows of it
export type AgentIntro = { emoji: string; name: string; description: string; starters: string[] };

const TIP_KEY = "heartbeatTip";
// Asked once per page load: whether to tell about the heartbeat (it's off, and the tip wasn't put away)
let heartbeatOff: Promise<boolean> | null = null;

/** Under a new chat's suggestions, until it's put away: that labb can work through to-dos on its own */
function HeartbeatTip() {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    try {
      if (localStorage.getItem(TIP_KEY) === "dismissed") return;
    } catch {
      return;
    }
    let live = true;
    heartbeatOff ??= getHeartbeat().then(found => !found.enabled, () => false);
    heartbeatOff.then(off => live && setShown(off));
    return () => {
      live = false;
    };
  }, []);
  if (!shown) return null;
  const dismiss = () => {
    setShown(false);
    try {
      localStorage.setItem(TIP_KEY, "dismissed");
    } catch {
      // A private window: it shows again next time
    }
  };
  return (
    <p className="heartbeat-tip">
      <span aria-hidden>♥</span> labb can also work on its own: leave it to-dos and turn on its{" "}
      <Link to={pathOf("tasks")} onClick={dismiss}>
        heartbeat
      </Link>
      , and every so often it picks them up.{" "}
      <button type="button" className="link" onClick={dismiss}>
        Got it
      </button>
    </p>
  );
}

function EmptyChat({ name, agent, onAsk }: { name: string; agent?: AgentIntro; onAsk: (prompt: string) => void }) {
  if (agent) {
    const starters = agent.starters.filter(s => s.trim());
    return (
      <div className="empty">
        <div className="empty-emoji" aria-hidden>
          {agent.emoji}
        </div>
        <h2>{agent.name}</h2>
        <p>{agent.description || `Ask ${agent.name} anything.`}</p>
        {starters.length > 0 && (
          <div className="suggestions">
            {starters.map(s => (
              <button key={s} type="button" onClick={() => onAsk(s)}>
                <span className="starter">{s}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="empty">
      <h2>{greeting(name)}</h2>
      <p>What can I help with? Attach files or images by dropping or pasting them.</p>
      <div className="suggestions">
        {SUGGESTIONS.map(s => (
          <button key={s.title} type="button" onClick={() => onAsk(s.prompt)}>
            <b>{s.title}</b>
            <span>{s.prompt}</span>
          </button>
        ))}
      </div>
      <HeartbeatTip />
    </div>
  );
}

type Props = {
  turns: Turn[];
  loading: boolean;
  error: string | null;
  threadId: string | null;
  // For the greeting of an empty chat; null for a chat that can't be written in
  name: string | null;
  // The chat's agent, for an empty chat
  agent?: AgentIntro;
  canChange: boolean;
  editing: number | null;
  onEdit: (index: number | null) => void;
  onSaveEdit: (index: number, text: string) => void;
  onRegenerate: () => void;
  onAsk: (prompt: string) => void;
};

export function MessageList(props: Props) {
  const { turns, loading, error, threadId, name, agent, canChange, editing, onEdit, onSaveEdit, onRegenerate, onAsk } = props;
  const log = useRef<HTMLElement>(null);
  const atBottom = useRef(true);
  // Scrolled up from the end: offer a button back down
  const [away, setAway] = useState(false);
  const speech = useSpeech();

  // Follow the reply as it streams, unless you've scrolled up to read
  useLayoutEffect(() => {
    if (atBottom.current && log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [turns]);

  const toBottom = () => {
    log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" });
    atBottom.current = true;
  };

  return (
    <div className="log-wrap">
      <main
        id="log"
        ref={log}
        onScroll={event => {
          const el = event.currentTarget;
          const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
          atBottom.current = distance < 40;
          setAway(distance > 300);
        }}
      >
        <div className="inner">
          {error && <div className="msg error">{error}</div>}
          {turns.length === 0 && !error && loading && <div className="loading">Loading…</div>}
          {turns.length === 0 && !error && !loading && name !== null && <EmptyChat name={name} agent={agent} onAsk={onAsk} />}
          {turns.map((turn, i) => (
            <TurnView
              key={i}
              turn={turn}
              threadId={threadId}
              isLast={i === turns.length - 1}
              canChange={canChange}
              editing={editing === i}
              onEdit={() => onEdit(i)}
              onCancelEdit={() => onEdit(null)}
              onSaveEdit={text => onSaveEdit(i, text)}
              onRegenerate={onRegenerate}
              canSpeak={speech.speak}
              index={i}
            />
          ))}
        </div>
      </main>
      {away && (
        <button type="button" className="jump" aria-label="Jump to the latest" title="Jump to the latest" onClick={toBottom}>
          <ArrowDownIcon />
        </button>
      )}
    </div>
  );
}
