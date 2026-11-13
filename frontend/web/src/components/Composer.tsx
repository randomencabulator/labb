import { type ReactNode, forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import type { Agent, AnswerMode, Attachment, Model } from "../api";
import { AgentPicker } from "./AgentPicker";
import { CloseIcon, FileIcon, CursorIcon, GlobeIcon, MicIcon, PaperclipIcon, SendIcon, StopIcon, VoiceIcon } from "./Icons";
import { type Recording, readAloud, record, stopPlaying, toText, usePlayer, useSpeech } from "../speech";
import { NowPlaying } from "./NowPlaying";
import { AnswerModePicker } from "./AnswerModePicker";
import { ModelPicker } from "./ModelPicker";
import { ChatSetup } from "./ChatSetup";
import { useLimits } from "../useLimits";
import type { PublicLimits } from "../api";

const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];

/** What a chat lets you pick in its message box: the model and how it answers, and, where the chat
 * has them, who it's with, Web, the browser, the engine and skills */
export type ComposerSettings = {
  models: Model[];
  model: string | null;
  onModel: (spec: string) => void;
  answerMode: AnswerMode;
  onAnswerMode: (mode: AnswerMode) => void;
  // Which engine the chat uses, when there's a choice
  enginePicker?: ReactNode;
  // The Web toggle: search the web and read pages in replies; hidden when the chat's agent has no web tools
  web?: boolean;
  onWeb?: (on: boolean) => void;
  webAvailable?: boolean;
  // The Browser switch: letting replies use a browser; hidden without one, or when the agent decides
  browser?: boolean;
  onBrowser?: (on: boolean) => void;
  browserAvailable?: boolean;
  // The Don't ask switch, for this chat; shown while replies can use the browser
  browserOn?: boolean;
  browserAuto?: boolean;
  onBrowserAuto?: (on: boolean) => void;
  // Who the chat is with; it can only be changed before the first message
  agents?: Agent[];
  agent?: Agent | null;
  onAgent?: (agent: Agent | null) => void;
  agentLocked?: boolean;
  // Typing /name uses a skill: these are suggested
  skills?: { name: string; description: string }[];
  // What the empty box says
  placeholder?: string;
};

type Props = ComposerSettings & {
  streaming: boolean;
  stopping: boolean;
  onSend: (message: string, attachments: Attachment[]) => void;
  onStop: () => void;
  // ↑ in the empty box: edit the last message
  onEditLast: () => void;
  // The last reply's text, which voice mode reads aloud when it's written
  lastReply: string;
};

const nothing = () => {};

/**
 * Whether the bar's row of pickers and switches fits in it, on one line: if not (beside the
 * sidebar and a panel, or in an app's chat), the form gets `compact` and they go behind one chip,
 * as on a phone. Measured with them all at their full width, before the page is painted.
 */
function fitBar(form: HTMLFormElement, bar: HTMLElement): boolean {
  const was = form.classList.contains("compact");
  form.classList.remove("compact");
  form.classList.add("measuring");
  const overflows = bar.scrollWidth > bar.clientWidth + 1;
  form.classList.remove("measuring");
  form.classList.toggle("compact", was);
  return overflows;
}

/** Whether the focus is in another place to type than `box` */
function typingElsewhere(box: HTMLElement | null) {
  const focused = document.activeElement as HTMLElement | null;
  if (!focused || focused === box) return false;
  return focused.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(focused.tagName);
}

// Voice mode: listening for you, transcribing, waiting for the reply, reading it aloud
type VoicePhase = "off" | "listening" | "hearing" | "waiting" | "speaking";

function micProblem(error: unknown) {
  const e = error as Error;
  if (e.name === "NotAllowedError") return "The browser wasn't allowed to use the microphone";
  if (e.name === "NotFoundError") return "There's no microphone";
  return e.message;
}

export type ComposerHandle = { focus: () => void };

function size(attachment: Attachment) {
  const chars = attachment.data.length;
  return chars < 1000 ? `${chars} chars` : `${Math.round(chars / 1000)}k chars`;
}

// The server checks these too (Settings → Limits)
function readFile(file: File, limits: PublicLimits): Promise<Attachment> {
  return new Promise((resolve, reject) => {
    if (IMAGE_TYPES.includes(file.type)) {
      if (file.size > limits.attachment_image_mb * 1_000_000) return reject(new Error(`${file.name} is over ${limits.attachment_image_mb} MB`));
      const reader = new FileReader();
      reader.onload = () => {
        const url = reader.result as string;
        resolve({ name: file.name, kind: "image", mime_type: file.type, data: url.slice(url.indexOf(",") + 1) });
      };
      reader.onerror = () => reject(new Error(`Couldn't read ${file.name}`));
      reader.readAsDataURL(file);
      return;
    }
    if (file.type.startsWith("image/")) return reject(new Error(`${file.name}: only PNG, JPEG, GIF and WebP images work`));
    file.text().then(text => {
      // Text files only: a NUL character means it's binary (a PDF, an archive…)
      if (text.includes("\u0000")) return reject(new Error(`${file.name} isn't a text file`));
      if (text.length > limits.attachment_text_chars) {
        return reject(new Error(`${file.name} is over ${limits.attachment_text_chars.toLocaleString()} characters`));
      }
      resolve({ name: file.name, kind: "text", data: text });
    }, reject);
  });
}

/** The message box, with attachments and the model picker; Send turns into Stop while a reply is written */
export const Composer = forwardRef<ComposerHandle, Props>(function Composer(
  {
    streaming, stopping, models, model, onModel, answerMode, onAnswerMode, web = false, onWeb = nothing, webAvailable = false,
    browser = false, onBrowser = nothing, browserAvailable = false, browserOn = false, browserAuto = false, onBrowserAuto = nothing,
    agents = [], agent = null, onAgent = nothing, agentLocked = true, skills = [], placeholder, onSend, onStop, onEditLast, lastReply, enginePicker,
  },
  ref,
) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  // The phone's sheet for who, the model, the answer mode and Web
  const [setupOpen, setSetupOpen] = useState(false);
  // The highlighted /skill suggestion, and whether Esc closed them for what's typed
  const [suggested, setSuggested] = useState(0);
  const [hideSuggestions, setHideSuggestions] = useState<string | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const filePicker = useRef<HTMLInputElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  // The row of pickers doesn't fit: they go behind one chip (fitBar)
  const [compact, setCompact] = useState(false);

  // Its room changes with the page
  useEffect(() => {
    const observer = new ResizeObserver(() => form.current && bar.current && setCompact(fitBar(form.current, bar.current)));
    if (form.current) observer.observe(form.current);
    return () => observer.disconnect();
  }, []);

  useImperativeHandle(ref, () => ({ focus: () => input.current?.focus() }), []);

  // --- Speech: dictating into the box, and voice mode -------------------------------------
  const speech = useSpeech();
  const [mic, setMic] = useState<"idle" | "recording" | "transcribing">("idle");
  const [voice, setVoice] = useState<VoicePhase>("off");
  // What voice mode heard last
  const [heard, setHeard] = useState("");
  const recording = useRef<Recording | null>(null);
  const meter = useRef<HTMLSpanElement>(null);
  const voiceOn = useRef(false);
  const sawReply = useRef(false);
  const latestReply = useRef(lastReply);
  latestReply.current = lastReply;
  // Voice mode sends seconds after it starts listening: with the chat as it is then
  const latestSend = useRef(onSend);
  latestSend.current = onSend;
  // The loudness, set on the element rather than in state (it changes every frame)
  const showLevel = (level: number) => meter.current?.style.setProperty("--level", String(Math.min(1, level * 12)));

  const dictate = async () => {
    if (mic === "recording") return recording.current?.stop();
    if (mic !== "idle") return;
    setProblem(null);
    try {
      const rec = await record({ onLevel: showLevel });
      recording.current = rec;
      setMic("recording");
      const got = await rec.done;
      recording.current = null;
      if (!got) return;
      setMic("transcribing");
      const said = await toText(got);
      if (said) setText(current => (current.trim() ? `${current.trimEnd()} ${said}` : said));
    } catch (e) {
      setProblem(micProblem(e));
    } finally {
      setMic("idle");
      input.current?.focus();
    }
  };

  const endVoice = () => {
    voiceOn.current = false;
    recording.current?.cancel();
    recording.current = null;
    stopPlaying();
    setVoice("off");
  };

  const listen = async () => {
    if (!voiceOn.current) return;
    setVoice("listening");
    try {
      const rec = await record({ untilPause: true, onLevel: showLevel });
      if (!voiceOn.current) return rec.cancel();
      recording.current = rec;
      const got = await rec.done;
      recording.current = null;
      if (!voiceOn.current) return;
      if (!got) {
        // Nothing said for a while: voice mode ends rather than keep the mic open
        endVoice();
        setProblem("Voice mode ended: nothing was said for a while.");
        return;
      }
      setVoice("hearing");
      const said = await toText(got);
      if (!voiceOn.current) return;
      if (!said) return listen();
      setHeard(said);
      sawReply.current = false;
      setVoice("waiting");
      latestSend.current(said, []);
    } catch (e) {
      endVoice();
      setProblem(micProblem(e));
    }
  };

  const startVoice = () => {
    setProblem(null);
    setHeard("");
    voiceOn.current = true;
    listen();
  };

  // Cutting in: while it thinks, stop the reply and listen again (it isn't read)
  const interrupt = () => {
    sawReply.current = false;
    if (streaming) onStop();
    listen();
  };

  // What the big button does, and what the stage says, in each phase
  const player = usePlayer();
  const preparing = voice === "speaking" && player?.status === "loading" && player.part === 0;
  const orb: { label: string; action?: () => void } =
    voice === "listening" ? { label: "Send what you said now", action: () => recording.current?.stop() }
    : voice === "waiting" ? { label: "Stop the reply and talk", action: interrupt }
    : voice === "speaking" ? { label: "Stop it and talk", action: stopPlaying }
    : { label: "Writing down what you said" };
  const stageTitle = voice === "listening" ? (heard ? "Go on, I'm listening" : "Listening")
    : voice === "hearing" ? "Writing it down…"
    : voice === "waiting" ? "Thinking…"
    : preparing ? "Getting the voice ready…" : "Speaking";
  const stageLine = voice === "listening" ? (heard ? `You said: “${heard}”` : "Talk, then pause. Or tap to send it now.")
    : voice === "waiting" ? (heard ? `“${heard}” · tap to stop` : "Tap to stop and talk")
    : voice === "speaking" ? "Tap to cut in"
    : "One moment";

  // The reply to what was said is written: read it aloud, then listen again
  useEffect(() => {
    if (voice !== "waiting") return;
    if (streaming) {
      sawReply.current = true;
      return;
    }
    if (!sawReply.current) return;
    setVoice("speaking");
    const reply = latestReply.current.trim();
    (reply ? readAloud(reply, "voice") : Promise.resolve())
      .catch(e => setProblem((e as Error).message))
      .finally(() => voiceOn.current && listen());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming, voice]);

  // Leaving the chat ends voice mode
  useEffect(() => () => {
    voiceOn.current = false;
    recording.current?.cancel();
    stopPlaying();
  }, []);

  // Grow with the text, up to the max-height in CSS
  useEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = "";
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);

  // Back to the box when a reply ends, unless you're typing somewhere else meanwhile (an app's files)
  useEffect(() => {
    if (!streaming && !typingElsewhere(input.current)) input.current?.focus();
  }, [streaming]);

  // Esc stops the reply from anywhere on the page, unless what has the focus used it (an editor)
  useEffect(() => {
    if (!streaming) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && !event.defaultPrevented && onStop();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [streaming, onStop]);

  // What's in the bar changes with the chat (its agent, the switches it has) and with voice: measured again then,
  // not at each render (a reply re-renders it for every word)
  useLayoutEffect(() => {
    if (form.current && bar.current) setCompact(fitBar(form.current, bar.current));
  }, [models.length, model, agents.length, agent?.id, webAvailable, browserAvailable, browserOn, Boolean(enginePicker), speech.listen, speech.speak, voice, mic]);

  const limits = useLimits();
  const current = models.find(m => m.spec === model);
  const hasImages = attachments.some(a => a.kind === "image");
  const blind = hasImages && current?.vision === false;

  const add = async (files: FileList | File[]) => {
    const list = [...files];
    if (!list.length) return;
    if (attachments.length + list.length > limits.attachments_per_message) {
      setProblem(`At most ${limits.attachments_per_message} attachments`);
      return;
    }
    const results = await Promise.allSettled(list.map(file => readFile(file, limits)));
    const added = results.flatMap(r => (r.status === "fulfilled" ? [r.value] : []));
    const failed = results.flatMap(r => (r.status === "rejected" ? [(r.reason as Error).message] : []));
    setAttachments(current => [...current, ...added]);
    setProblem(failed.length ? failed.join("; ") : null);
  };

  const submit = () => {
    const message = text.trim();
    if ((!message && !attachments.length) || streaming || blind) return;
    setText("");
    setAttachments([]);
    setProblem(null);
    onSend(message || "(see the attachments)", attachments);
  };

  const canSend = (Boolean(text.trim()) || attachments.length > 0) && !blind;

  // "/" and the start of a name, before a space: the skills that match
  const typedName = /^\/([\w-]*)$/.exec(text)?.[1];
  const suggestions =
    typedName !== undefined && hideSuggestions !== text ? skills.filter(s => s.name.startsWith(typedName.toLowerCase())).slice(0, 8) : [];
  const pick = (name: string) => {
    setText(`/${name} `);
    setSuggested(0);
    input.current?.focus();
  };

  let chips: ReactNode = null;
  if (attachments.length) {
    chips = (
      <div className="attachments">
        {attachments.map((a, i) => (
          <span key={i} className={a.kind === "image" ? "file-chip image" : "file-chip"} title={a.name}>
            {a.kind === "image" ? (
              <img src={`data:${a.mime_type};base64,${a.data}`} alt={a.name} />
            ) : (
              <>
                <span className="file-icon">
                  <FileIcon />
                </span>
                <span className="file-text">
                  <span className="file-name">{a.name}</span>
                  <span className="file-size">{size(a)}</span>
                </span>
              </>
            )}
            <button
              type="button"
              className="remove"
              aria-label={`Remove ${a.name}`}
              title="Remove"
              onClick={() => setAttachments(current => current.filter((_, j) => j !== i))}
            >
              <CloseIcon />
            </button>
          </span>
        ))}
      </div>
    );
  }

  return (
    <form
      ref={form}
      className={["composer-form", dragging && "dragging", compact && "compact"].filter(Boolean).join(" ")}
      onSubmit={event => {
        event.preventDefault();
        submit();
      }}
      onDragOver={event => {
        if (!event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={event => {
        // Only when leaving the box, not when moving over what's inside it
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
      }}
      onDrop={event => {
        event.preventDefault();
        setDragging(false);
        add(event.dataTransfer.files);
      }}
    >
      <div className="inner">
        <NowPlaying />
        {voice !== "off" && (
          <div className={`voice-stage ${voice}${voice === "speaking" && player?.status === "loading" && player.part === 0 ? " preparing" : ""}`} role="status" aria-live="polite">
            <button type="button" className="voice-orb" onClick={orb.action} disabled={!orb.action} aria-label={orb.label} title={orb.label}>
              <span className="voice-meter" ref={meter} aria-hidden />
              <span className="voice-ring" aria-hidden />
            </button>
            <div className="voice-text">
              <b>{stageTitle}</b>
              <span>{stageLine}</span>
            </div>
            <button type="button" className="voice-end" onClick={endVoice} aria-label="End voice mode" title="End voice mode">
              <CloseIcon />
            </button>
          </div>
        )}
        <div className="composer" onClick={event => event.target === event.currentTarget && input.current?.focus()}>
          {dragging && <div className="drop-hint">Drop files to attach</div>}
          {suggestions.length > 0 && (
            <ul className="model-list skill-suggestions" role="listbox" aria-label="Skills">
              <li className="model-group" role="presentation">
                Skills
              </li>
              {suggestions.map((s, i) => (
                <li
                  key={s.name}
                  role="option"
                  aria-selected={i === suggested}
                  className={i === suggested ? "active" : undefined}
                  onMouseEnter={() => setSuggested(i)}
                  onMouseDown={event => {
                    event.preventDefault();
                    pick(s.name);
                  }}
                >
                  <span className="model-name">
                    /{s.name}
                    <small>{s.description}</small>
                  </span>
                </li>
              ))}
            </ul>
          )}
          {chips}
          {(problem || blind) && (
            <div className="composer-problem" role="alert">
              <span>{blind ? `${current?.name} can't see images: pick a model with vision, or remove them.` : problem}</span>
              {problem && !blind && (
                <button type="button" aria-label="Dismiss" onClick={() => setProblem(null)}>
                  <CloseIcon />
                </button>
              )}
            </div>
          )}
          <textarea
            ref={input}
            rows={1}
            value={text}
            aria-label="Message"
            placeholder={streaming ? "Write your next message…" : (placeholder ?? `Message ${agent?.name ?? "labb"}`)}
            autoFocus
            onChange={event => {
              setText(event.target.value);
              setSuggested(0);
            }}
            onPaste={event => {
              if (event.clipboardData.files.length) {
                event.preventDefault();
                add(event.clipboardData.files);
              }
            }}
            onKeyDown={event => {
              if (suggestions.length > 0) {
                if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                  event.preventDefault();
                  const step = event.key === "ArrowDown" ? 1 : -1;
                  setSuggested(i => (i + step + suggestions.length) % suggestions.length);
                  return;
                }
                if ((event.key === "Enter" && !event.shiftKey) || event.key === "Tab") {
                  event.preventDefault();
                  pick(suggestions[Math.min(suggested, suggestions.length - 1)].name);
                  return;
                }
                if (event.key === "Escape") {
                  // Not the reply's Esc
                  event.stopPropagation();
                  setHideSuggestions(text);
                  return;
                }
              }
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                submit();
              } else if (event.key === "ArrowUp" && !text && !attachments.length) {
                event.preventDefault();
                onEditLast();
              }
            }}
          />
          <div className="composer-bar" ref={bar}>
            <button
              type="button"
              className="tool-button"
              aria-label="Attach files or images"
              title="Attach files or images (or drop and paste them)"
              onClick={() => filePicker.current?.click()}
            >
              <PaperclipIcon />
            </button>
            <input
              ref={filePicker}
              type="file"
              multiple
              hidden
              onChange={event => {
                if (event.target.files) add(event.target.files);
                event.target.value = "";
              }}
            />
            {speech.listen && voice === "off" && (
              <button
                type="button"
                className={`tool-button mic ${mic}`}
                aria-label={mic === "recording" ? "Stop recording" : "Dictate"}
                title={mic === "recording" ? "Stop and write down what you said" : mic === "transcribing" ? "Writing it down…" : "Dictate a message"}
                disabled={mic === "transcribing"}
                onClick={dictate}
              >
                {mic === "recording" ? <span className="voice-meter small" ref={meter} aria-hidden /> : <MicIcon />}
              </button>
            )}
            {speech.listen && speech.speak && voice === "off" && mic === "idle" && (
              <button type="button" className="tool-button wide-bar" aria-label="Voice mode" title="Voice mode: talk, and hear the replies" onClick={startVoice}>
                <VoiceIcon />
              </button>
            )}
            {/* On a phone, one chip and a sheet instead of this row */}
            <span className="wide-bar">
              <AgentPicker agents={agents} agent={agent} onAgent={onAgent} locked={agentLocked} />
              <ModelPicker models={models} model={model} onModel={onModel} />
              <AnswerModePicker mode={answerMode} onMode={onAnswerMode} />
              {enginePicker}
            </span>
            <span className="phone-bar">
              <ChatSetup open={setupOpen} onOpen={setSetupOpen} agents={agents} agent={agent} onAgent={onAgent} agentLocked={agentLocked}
                models={models} model={model} onModel={onModel} mode={answerMode} onMode={onAnswerMode}
                webAvailable={webAvailable} web={web} onWeb={onWeb} browserAvailable={browserAvailable} browser={browser} onBrowser={onBrowser} browserOn={browserOn} browserAuto={browserAuto} onBrowserAuto={onBrowserAuto} enginePicker={enginePicker}
                onVoice={speech.listen && speech.speak && voice === "off" && mic === "idle" ? startVoice : undefined} />
            </span>
            {webAvailable && (
              <button
                type="button"
                className={web ? "toggle-pill wide-bar on" : "toggle-pill wide-bar"}
                aria-pressed={web}
                title={web ? "Web is on: replies can search the web and read pages. Click to turn it off" : "Web is off: replies use only what the model knows and your documents. Click to turn it on"}
                onClick={() => onWeb(!web)}
              >
                <GlobeIcon /> <span>Web</span>
              </button>
            )}
            {browserAvailable && (
              <button
                type="button"
                className={browser ? "toggle-pill wide-bar on" : "toggle-pill wide-bar"}
                aria-pressed={browser}
                title={browser ? "Browser is on: replies can use a browser, click and fill in forms, and ask before anything that sends, pays or deletes. Click to turn it off" : "Browser is off. Click to let replies use a browser: click, fill in and submit forms, work in web apps"}
                onClick={() => onBrowser(!browser)}
              >
                <CursorIcon /> <span>Browser</span>
              </button>
            )}
            {browserOn && (
              <button
                type="button"
                className={browserAuto ? "toggle-pill wide-bar on" : "toggle-pill wide-bar"}
                aria-pressed={browserAuto}
                title={browserAuto ? "The browser doesn't ask in this chat. Click for it to ask again" : "The browser asks before it clicks on a new site, sends, pays or deletes. Click to stop it asking in this chat"}
                onClick={() => onBrowserAuto(!browserAuto)}
              >
                <span>Don't ask</span>
              </button>
            )}
            <span className="spacer" />
            {streaming ? (
              <button
                type="button"
                className={stopping ? "round-button stop stopping" : "round-button stop"}
                onClick={onStop}
                disabled={stopping}
                aria-label={stopping ? "Stopping" : "Stop the reply"}
                title="Stop (Esc)"
              >
                <StopIcon />
              </button>
            ) : (
              <button type="submit" className="round-button send" disabled={!canSend} aria-label="Send" title="Send (Enter)">
                <SendIcon />
              </button>
            )}
          </div>
        </div>
        <div className="composer-hint">
          {streaming ? (
            <>
              <kbd>Esc</kbd> stops the reply
            </>
          ) : (
            <>
              <kbd>Enter</kbd> to send · <kbd>Shift</kbd>+<kbd>Enter</kbd> for a new line · <kbd>↑</kbd> edits your last message
            </>
          )}
        </div>
      </div>
    </form>
  );
});
