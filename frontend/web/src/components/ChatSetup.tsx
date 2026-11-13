// On a phone the message box has no room for a row of pickers: one chip says who, which model
// and how it thinks, and opens a sheet to change them, with the Web switch and voice mode
import { type CSSProperties, type ReactNode, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import type { Agent, AnswerMode, Model } from "../api";
import { ANSWER_MODES } from "./AnswerModePicker";
import { CheckIcon, ChevronDownIcon, EyeIcon, CursorIcon, GlobeIcon, Logo, VoiceIcon } from "./Icons";
import { PROVIDERS } from "./ModelPicker";

type Props = {
  open: boolean;
  onOpen: (open: boolean) => void;
  agents: Agent[];
  agent: Agent | null;
  onAgent: (agent: Agent | null) => void;
  agentLocked: boolean;
  models: Model[];
  model: string | null;
  onModel: (spec: string) => void;
  mode: AnswerMode;
  onMode: (mode: AnswerMode) => void;
  webAvailable: boolean;
  web: boolean;
  onWeb: (on: boolean) => void;
  browserAvailable: boolean;
  browser: boolean;
  onBrowser: (on: boolean) => void;
  browserOn: boolean;
  browserAuto: boolean;
  onBrowserAuto: (on: boolean) => void;
  // Voice mode, when the server can listen and speak
  onVoice?: () => void;
  // Which engine: only when there's a choice
  enginePicker?: ReactNode;
};

const provider = (spec: string) => spec.split(":", 1)[0];

export function ChatSetup(props: Props) {
  const { open, onOpen, agents, agent, onAgent, agentLocked, models, model, onModel, mode, onMode, webAvailable, web, onWeb, browserAvailable, browser, onBrowser, browserOn, browserAuto, onBrowserAuto, onVoice, enginePicker } = props;
  const current = models.find(m => m.spec === model);
  const modeName = ANSWER_MODES.find(m => m.id === mode)?.name ?? "Auto";

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onOpen(false);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open]);

  // Where the chip is, for the sheet to come up there on a wider screen
  const chip = useRef<HTMLButtonElement>(null);
  const sheetX = { "--sheet-x": `${chip.current?.getBoundingClientRect().left ?? 0}px` } as CSSProperties;

  const groups = [...new Set(models.map(m => provider(m.spec)))].map(p => ({ id: p, models: models.filter(m => provider(m.spec) === p) }));
  const showAgents = agents.length > 0 || agent !== null;

  return (
    <>
      <button ref={chip} type="button" className="setup-chip" aria-haspopup="dialog" aria-expanded={open}
        aria-label={`Chat settings: ${agent?.name ?? "labb"}, ${current?.name ?? "default model"}, ${modeName}${web ? ", web on" : ""}${browserOn && browserAuto ? ", the browser doesn't ask" : ""}`}
        onClick={() => onOpen(!open)}>
        <span className="setup-who" aria-hidden>{agent ? agent.emoji : <Logo size={14} />}</span>
        <span className="setup-text">
          <b>{agent?.name ?? "labb"}</b>
          <span>{current?.name ?? "Default model"}{mode !== "auto" && ` · ${modeName}`}</span>
        </span>
        {webAvailable && web && <span className="setup-web" title="Web is on"><GlobeIcon /></span>}
        {browserOn && browserAuto && <span className="setup-web" title="The browser doesn't ask in this chat"><CursorIcon /></span>}
        <ChevronDownIcon />
      </button>
      {/* In the page's body: the message box measures its row by hiding the chip for a moment (Composer's
          fitBar), which would start the sheet's animation again and lose its scroll */}
      {open && createPortal(
        <div className="sheet-backdrop" style={sheetX} onClick={() => onOpen(false)}>
          <div className="setup-sheet" role="dialog" aria-label="Chat settings" onClick={event => event.stopPropagation()}>
            <div className="sheet-grip" aria-hidden />
            <div className="sheet-head">
              <h2>This chat</h2>
              <button type="button" className="primary" onClick={() => onOpen(false)}>Done</button>
            </div>
            <div className="sheet-body">
              {showAgents && (
                <section>
                  <h3>Who</h3>
                  {agentLocked ? (
                    <p className="sheet-note">{agent ? <>This chat is with <b>{agent.emoji} {agent.name}</b>.</> : <>This chat is with <b>labb</b>.</>} A new chat can be with someone else.</p>
                  ) : (
                    <div className="sheet-chips" role="radiogroup" aria-label="Who to chat with">
                      {[null, ...agents].map(a => (
                        <button key={a?.id ?? "labb"} type="button" role="radio" aria-checked={(a?.id ?? null) === (agent?.id ?? null)}
                          onClick={() => onAgent(a)}>
                          <span aria-hidden>{a ? a.emoji : <Logo size={14} />}</span> {a?.name ?? "labb"}
                        </button>
                      ))}
                    </div>
                  )}
                </section>
              )}
              <section>
                <h3>How it thinks</h3>
                <div className="sheet-modes" role="radiogroup" aria-label="Answer mode">
                  {ANSWER_MODES.map(m => (
                    <button key={m.id} type="button" role="radio" aria-checked={m.id === mode} onClick={() => onMode(m.id)}>
                      <b>{m.name}</b>
                      <span>{m.description}</span>
                    </button>
                  ))}
                </div>
              </section>
              {(webAvailable || browserAvailable || browserOn || onVoice) && (
                <section className="sheet-switches">
                  {webAvailable && (
                    <button type="button" className="sheet-row" role="switch" aria-checked={web} onClick={() => onWeb(!web)}>
                      <GlobeIcon />
                      <span><b>Web</b><small>{web ? "Replies can search the web and read pages" : "Only what the model knows and your documents"}</small></span>
                      <span className="switch-knob" aria-hidden />
                    </button>
                  )}
                  {browserAvailable && (
                    <button type="button" className="sheet-row" role="switch" aria-checked={browser} onClick={() => onBrowser(!browser)}>
                      <CursorIcon />
                      <span><b>Browser</b><small>{browser ? "Replies can click, fill in forms and work in web apps" : "Replies don't use a browser"}</small></span>
                      <span className="switch-knob" aria-hidden />
                    </button>
                  )}
                  {browserOn && (
                    <button type="button" className="sheet-row" role="switch" aria-checked={browserAuto} onClick={() => onBrowserAuto(!browserAuto)}>
                      <CursorIcon />
                      <span><b>Don't ask</b><small>{browserAuto ? "The browser doesn't ask in this chat" : "The browser asks before it clicks on a new site, sends, pays or deletes"}</small></span>
                      <span className="switch-knob" aria-hidden />
                    </button>
                  )}
                  {onVoice && (
                    <button type="button" className="sheet-row" onClick={() => (onOpen(false), onVoice())}>
                      <VoiceIcon />
                      <span><b>Voice mode</b><small>Talk, and hear the replies</small></span>
                    </button>
                  )}
                </section>
              )}
              {enginePicker && (
                <section>
                  <h3>Engine</h3>
                  {enginePicker}
                </section>
              )}
              {models.length > 0 && (
                <section>
                  <h3>Model</h3>
                  {groups.map(group => (
                    <div key={group.id} className="sheet-group">
                      <div className="sheet-group-name">{PROVIDERS[group.id] ?? group.id}</div>
                      <ul className="sheet-list" role="radiogroup" aria-label={PROVIDERS[group.id] ?? group.id}>
                        {group.models.map(m => (
                          <li key={m.spec}>
                            <button type="button" role="radio" aria-checked={m.spec === model} onClick={() => onModel(m.spec)}>
                              <span className="sheet-model">{m.name}</span>
                              {m.vision && <span className="model-tag"><EyeIcon /> vision</span>}
                              {m.tools === false && <span className="model-tag">no tools</span>}
                              <span className="model-check">{m.spec === model && <CheckIcon />}</span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </section>
              )}
            </div>
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}
