// Which engine a chat uses: a pill next to the model picker, shown once there's a choice
import { useEffect, useRef, useState } from "react";
import type { Engine } from "../api";
import { CheckIcon, ChevronDownIcon } from "./Icons";

type Props = {
  engines: Engine[];
  // The one the next message uses
  current: string;
  onPick: (id: string) => void;
  onManage: () => void;
};

export function EnginePicker({ engines, current, onPick, onManage }: Props) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const shown = engines.find(h => h.id === current);

  useEffect(() => {
    if (!open) return;
    const onClick = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="model-picker" ref={root}>
      <button type="button" className="model-trigger" aria-haspopup="listbox" aria-expanded={open} aria-label={`Engine: ${shown?.name ?? "labb default"}`} title="The version of the agent's core this chat uses" onClick={() => setOpen(!open)}>
        <span className="model-name">{shown?.name ?? "labb default"}</span>
        <ChevronDownIcon />
      </button>
      {open && (
        <ul className="model-list" role="listbox" aria-label="Engines">
          {engines.map(h => {
            const blocked = h.web_problems.length > 0;
            return (
              <li key={h.id} role="option" aria-selected={h.id === current} aria-disabled={blocked} className={active === h.id ? "active" : undefined}
                style={blocked ? { opacity: 0.5, cursor: "default" } : undefined} title={blocked ? h.web_problems.join(". ") : h.description}
                onMouseEnter={() => setActive(h.id)} onClick={() => { if (!blocked) { onPick(h.id); setOpen(false); } }}>
                <span className="model-name">{h.name}{blocked && <small>Terminal only</small>}</span>
                <span className="model-check">{h.id === current && <CheckIcon />}</span>
              </li>
            );
          })}
          <li role="option" aria-selected={false} className={active === "manage" ? "active" : undefined} onMouseEnter={() => setActive("manage")} onClick={() => { setOpen(false); onManage(); }}>
            <span className="model-name muted">Edit engines…</span>
          </li>
        </ul>
      )}
    </div>
  );
}
