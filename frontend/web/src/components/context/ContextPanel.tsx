import { useEffect, useRef, useState } from "react";
import type { ContextInspection, ContextOptions, Engine, Model } from "../../api";
import { InputTab } from "./InputTab";
import { OverviewTab } from "./OverviewTab";
import { RawTab } from "./RawTab";
import { TimelineTab } from "./TimelineTab";
import { ToolsTab } from "./ToolsTab";
import { WhatIfTab } from "./WhatIfTab";
import { WorkTab } from "./WorkTab";
import "./context.css";

export const TABS = [
  { id: "overview", label: "Overview" },
  { id: "input", label: "Input" },
  { id: "tools", label: "Tools" },
  { id: "timeline", label: "Timeline" },
  { id: "work", label: "Work" },
  { id: "whatif", label: "What-if" },
  { id: "raw", label: "Raw" },
] as const;
export type ContextTab = (typeof TABS)[number]["id"];

const STORAGE = "contextPanel";
const MIN_WIDTH = 320;
const RESIZE_STEP = 24;

function loadLayout(): { width: number; expanded: boolean } {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE) ?? "{}");
    return { width: typeof saved.width === "number" ? saved.width : 460, expanded: saved.expanded === true };
  } catch {
    return { width: 460, expanded: false };
  }
}

type Props = {
  threadId: string;
  seen: ContextInspection | null;
  error: string | null;
  updating: boolean;
  streaming: boolean;
  options: ContextOptions;
  models: Model[];
  engines: Engine[];
  currentEngine: string;
  webAvailable: boolean;
  tab: ContextTab;
  onTab: (tab: ContextTab) => void;
  onClose: () => void;
  summarizing: boolean;
  onSummarizing: (summarizing: boolean) => void;
  onSeen: (seen: ContextInspection) => void;
};

/** Beside the chat: what its next model call gets, how that got here and what would change it */
export function ContextPanel(props: Props) {
  const { threadId, seen, error, updating, streaming, options, tab, onTab, onClose } = props;
  const [layout, setLayout] = useState(loadLayout);
  // The message Input should open, from the other tabs
  const [focus, setFocus] = useState<number | null>(null);
  const panel = useRef<HTMLElement>(null);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE, JSON.stringify(layout));
    } catch {
      // Storage can be unavailable: it's kept for this page
    }
  }, [layout]);
  // It's for the visit it was picked for: coming back to Input later doesn't jump or lose a search
  useEffect(() => {
    if (tab !== "input") setFocus(null);
  }, [tab]);
  useEffect(() => setFocus(null), [threadId]);

  const resize = (width: number) => {
    const most = Math.max(MIN_WIDTH, window.innerWidth * 0.7);
    setLayout(current => ({ ...current, width: Math.round(Math.min(most, Math.max(MIN_WIDTH, width))) }));
  };
  const startResize = (event: React.PointerEvent) => {
    event.preventDefault();
    const right = panel.current?.getBoundingClientRect().right ?? window.innerWidth;
    const move = (e: PointerEvent) => resize(right - e.clientX);
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      document.body.classList.remove("context-resizing");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    document.body.classList.add("context-resizing");
  };
  const openMessage = (index: number) => {
    setFocus(index);
    onTab("input");
  };

  return (
    <section ref={panel} className={`context-panel${layout.expanded ? " expanded" : ""}`} style={{ "--context-width": `${layout.width}px` } as React.CSSProperties} aria-label="Context">
      {!layout.expanded && (
        <div
          className="context-resize"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize the context panel"
          aria-valuenow={layout.width}
          tabIndex={0}
          onPointerDown={startResize}
          onKeyDown={event => {
            if (event.key === "ArrowLeft") resize(layout.width + RESIZE_STEP);
            else if (event.key === "ArrowRight") resize(layout.width - RESIZE_STEP);
          }}
        />
      )}
      <div className="context-head">
        <h2>Context</h2>
        {updating && <span className="context-updating muted" role="status">updating…</span>}
        <span className="spacer" />
        <button type="button" className="context-expand" aria-pressed={layout.expanded} onClick={() => setLayout({ ...layout, expanded: !layout.expanded })}>
          {layout.expanded ? "Beside the chat" : "Full width"}
        </button>
        <button type="button" className="close" aria-label="Close the context panel" onClick={onClose}>×</button>
      </div>
      <div className="context-tabs" role="tablist" aria-label="Context views">
        {TABS.map(item => (
          <button key={item.id} type="button" role="tab" aria-selected={tab === item.id} className={tab === item.id ? "active" : ""} onClick={() => onTab(item.id)}>
            {item.label}
          </button>
        ))}
      </div>
      <div className="context-body context-inspector" role="tabpanel">
        {/* What the chat works on doesn't wait for the inspection: it's asked on its own */}
        {tab === "work" && <WorkTab threadId={threadId} streaming={streaming} />}
        {tab !== "work" && error && <p className="error">{error}</p>}
        {tab !== "work" && !seen && !error && <p className="muted">Loading…</p>}
        {seen && tab === "overview" && (
          <OverviewTab threadId={threadId} seen={seen} options={options} streaming={streaming} summarizing={props.summarizing}
            onSummarizing={props.onSummarizing} onSeen={props.onSeen} onOpenMessage={openMessage} onTab={onTab} />
        )}
        {seen && tab === "input" && <InputTab threadId={threadId} seen={seen} focus={focus} />}
        {seen && tab === "tools" && <ToolsTab seen={seen} />}
        {seen && tab === "timeline" && <TimelineTab threadId={threadId} seen={seen} streaming={streaming} onOpenMessage={openMessage} />}
        {seen && tab === "whatif" && (
          <WhatIfTab threadId={threadId} seen={seen} options={options} models={props.models} engines={props.engines}
            currentEngine={props.currentEngine} webAvailable={props.webAvailable} />
        )}
        {seen && tab === "raw" && <RawTab threadId={threadId} seen={seen} options={options} streaming={streaming} />}
      </div>
    </section>
  );
}
