// The browser the model uses, live: what its current tab shows, its tabs and address, and, when
// `interactive`, the user's clicks and keys go to the page. What they type there goes straight to
// the page: the model doesn't see it, and it's not kept.
import { type FormEvent, type KeyboardEvent, type MouseEvent, type WheelEvent, useEffect, useRef, useState } from "react";
import { type BrowserState, browserFrame, browserInput, browserSignOut, browserState } from "../api";

const FRAME_MS = 500;
const FRAME_MS_ACTIVE = 150;
// Nothing open, or nothing moving: a panel that's always open shouldn't keep asking quickly
const FRAME_MS_STILL = 2000;
const STATE_MS = 2000;

const KEY_NAMES = new Set(["Enter", "Tab", "Backspace", "Delete", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"]);

export function BrowserLive({ threadId, interactive: canTakeOver, onClose, note }: { threadId: string; interactive?: boolean; onClose?: () => void; note?: string }) {
  const [frame, setFrame] = useState<string | null>(null);
  const [state, setState] = useState<BrowserState | null>(null);
  const [control, setControl] = useState(Boolean(canTakeOver));
  const [problem, setProblem] = useState<string | null>(null);
  const [address, setAddress] = useState("");
  const [editing, setEditing] = useState(false);
  const image = useRef<HTMLImageElement>(null);
  const lastInput = useRef(0);

  useEffect(() => {
    if (canTakeOver) setControl(true);
  }, [canTakeOver]);

  // Frames one after another, sooner just after the user did something; none while the tab is hidden
  useEffect(() => {
    let gone = false;
    let url: string | null = null;
    let timer: ReturnType<typeof setTimeout>;
    let size = -1;
    const next = async () => {
      if (gone) return;
      let still = false;
      if (!document.hidden) {
        try {
          const blob = await browserFrame(threadId);
          if (gone) return;
          still = (blob?.size ?? 0) === size || !blob;
          size = blob?.size ?? 0;
          const previous = url;
          url = blob ? URL.createObjectURL(blob) : null;
          setFrame(url);
          if (previous) URL.revokeObjectURL(previous);
        } catch {
          // The next one may work
        }
      }
      const active = Date.now() - lastInput.current < 2000;
      timer = setTimeout(next, active ? FRAME_MS_ACTIVE : still ? FRAME_MS_STILL : FRAME_MS);
    };
    next();
    return () => {
      gone = true;
      clearTimeout(timer);
      if (url) URL.revokeObjectURL(url);
    };
  }, [threadId]);

  useEffect(() => {
    let gone = false;
    const load = () =>
      browserState(threadId)
        .then(next => {
          if (!gone) setState(next);
        })
        .catch(() => {});
    load();
    const timer = setInterval(load, STATE_MS);
    return () => {
      gone = true;
      clearInterval(timer);
    };
  }, [threadId]);

  const current = state?.tabs.find(tab => tab.current);
  // With nothing open there's nothing to disturb, so the address bar is free to use
  const nothingOpen = state !== null && state.tabs.length === 0;
  useEffect(() => {
    if (!editing) setAddress(current?.url ?? "");
  }, [current?.url, editing]);

  const send = (event: Record<string, unknown>) => {
    lastInput.current = Date.now();
    browserInput(threadId, event).then(() => setProblem(null), e => setProblem((e as Error).message));
  };

  // Where on the page, from where on its picture
  const at = (event: MouseEvent | WheelEvent) => {
    const box = image.current!.getBoundingClientRect();
    return {
      x: Math.round(((event.clientX - box.left) / box.width) * (state?.width || 1280)),
      y: Math.round(((event.clientY - box.top) / box.height) * (state?.height || 800)),
    };
  };

  const onKey = (event: KeyboardEvent) => {
    if (!control) return;
    const combo = event.ctrlKey || event.metaKey || event.altKey;
    if (event.key.length === 1 && !combo) {
      send({ type: "text", text: event.key });
    } else if (combo && event.key.length === 1) {
      const held = [event.ctrlKey && "Control", event.metaKey && "Meta", event.altKey && "Alt", event.shiftKey && "Shift"].filter(Boolean);
      send({ type: "key", key: [...held, event.key].join("+") });
    } else if (KEY_NAMES.has(event.key)) {
      send({ type: "key", key: event.shiftKey ? `Shift+${event.key}` : event.key });
    } else {
      return;
    }
    event.preventDefault();
  };

  const go = (event: FormEvent) => {
    event.preventDefault();
    setEditing(false);
    if (!address.trim()) return;
    setControl(true);
    send({ type: "open", url: address.trim() });
  };

  const signOut = async () => {
    if (!window.confirm("Sign out of every site in this browser? Its open tabs close too.")) return;
    try {
      await browserSignOut();
    } catch (e) {
      setProblem((e as Error).message);
    }
  };

  return (
    <div className="browser-live">
      <div className="browser-bar">
        <button type="button" className="icon-button" aria-label="Back" title="Back" disabled={!control} onClick={() => send({ type: "back" })}>←</button>
        <button type="button" className="icon-button" aria-label="Forward" title="Forward" disabled={!control} onClick={() => send({ type: "forward" })}>→</button>
        <button type="button" className="icon-button" aria-label="Reload" title="Reload" disabled={!control} onClick={() => send({ type: "reload" })}>↻</button>
        <form onSubmit={go}>
          <input
            aria-label="Address"
            value={address}
            readOnly={!control && !nothingOpen}
            placeholder="Type an address"
            onFocus={() => setEditing(true)}
            onBlur={() => setEditing(false)}
            onChange={event => setAddress(event.target.value)}
          />
        </form>
        <button
          type="button"
          className={control ? "toggle-pill on" : "toggle-pill"}
          aria-pressed={control}
          title={control ? "Your clicks and keys go to the page. Click to only watch" : "Only watching. Click to use the page yourself"}
          onClick={() => setControl(!control)}
        >
          {control ? "You're in control" : "Take over"}
        </button>
        {onClose && <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>×</button>}
      </div>
      {state && state.tabs.length > 1 && (
        <div className="browser-tabs" role="tablist">
          {state.tabs.map(tab => (
            <button
              key={tab.tab}
              type="button"
              role="tab"
              aria-selected={tab.current}
              className={tab.current ? "on" : ""}
              disabled={!control}
              title={tab.url}
              onClick={() => send({ type: "tab", tab: tab.tab })}
            >
              {tab.title || tab.url}
            </button>
          ))}
        </div>
      )}
      <div
        className={control ? "browser-screen control" : "browser-screen"}
        tabIndex={control ? 0 : -1}
        onKeyDown={onKey}
        onPaste={event => {
          if (!control) return;
          event.preventDefault();
          send({ type: "text", text: event.clipboardData.getData("text/plain") });
        }}
      >
        {frame ? (
          <img
            ref={image}
            src={frame}
            alt={current ? `The browser, showing ${current.title || current.url}` : "The browser"}
            draggable={false}
            onClick={event => control && send({ type: "click", ...at(event) })}
            onDoubleClick={event => control && send({ type: "click", clicks: 2, ...at(event) })}
            onContextMenu={event => {
              if (!control) return;
              event.preventDefault();
              send({ type: "click", button: "right", ...at(event) });
            }}
            onWheel={event => control && send({ type: "wheel", dx: event.deltaX, dy: event.deltaY, ...at(event) })}
          />
        ) : (
          <p className="hint">Nothing is open yet. Type an address above, or let the agent open a page.</p>
        )}
      </div>
      {note && <p className="hint browser-note">{note}</p>}
      <div className="browser-foot">
        {problem ? <span className="bad">{problem}</span> : <span className="hint">{control ? "What you type here goes to the page, not to the model." : "Watching."}</span>}
        <span className="spacer" />
        <button type="button" className="link-button" onClick={signOut}>Sign out of every site</button>
      </div>
    </div>
  );
}
