import { useEffect, useRef, useState } from "react";
import type { Pc } from "../api";
import { moment } from "../format";
import { CheckIcon, ChevronDownIcon, PcIcon, ServerIcon } from "./Icons";

type Where = { id: string; folder: string } | null;

type Props = {
  // The PCs connected with labb connect, online or not
  pcs: Pc[];
  where: Where;
  onWhere: (where: Where) => void;
  // Opens the PCs page, where a PC's state is explained
  onManage: () => void;
};

/** One destination: this server (no PC behind it), or a folder of a connected PC */
type Destination = { key: string; pc: Pc | null; folder: string };

const SERVER: Destination = { key: "", pc: null, folder: "" };

/**
 * Where the next chat runs: on this server, or in a folder on a PC connected with `labb connect`.
 * A pill above the message box opens the list, grouped by PC. A PC that isn't connected is still
 * listed, greyed with when it was last seen, so it doesn't just vanish; only connected ones can be
 * picked. Arrow keys, Enter and Esc work like a select, the same as the model picker.
 */
export function WherePicker({ pcs, where, onWhere, onManage }: Props) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  const chosenPc = where ? pcs.find(pc => pc.id === where.id) : undefined;
  const chosen = where ? `${where.id}\n${where.folder}` : "";
  // What the keyboard moves over: this server, then the folders of the connected PCs
  const targets: Destination[] = [SERVER, ...pcs.flatMap(pc => (pc.online ? pc.folders.map(folder => ({ key: `${pc.id}\n${folder}`, pc, folder })) : []))];

  useEffect(() => {
    if (!open) return;
    setActive(Math.max(0, targets.findIndex(target => target.key === chosen)));
    list.current?.focus();
    const onClick = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
    // Only when it opens: the highlight starts at the chosen destination
  }, [open]);

  // Keep the highlighted one in view
  useEffect(() => {
    if (open) list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  const choose = (target: Destination) => {
    onWhere(target.pc ? { id: target.pc.id, folder: target.folder } : null);
    setOpen(false);
    trigger.current?.focus();
  };

  return (
    <div className="where-picker" ref={root}>
      <button
        ref={trigger}
        type="button"
        className="where-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Where it runs: ${chosenPc && where ? `${chosenPc.name}, ${where.folder}` : "this server"}`}
        title="Where the next message runs"
        onClick={() => setOpen(!open)}
      >
        {chosenPc ? <PcIcon /> : <ServerIcon />}
        <span className="where-name">{chosenPc ? chosenPc.name : "This server"}</span>
        {chosenPc && where && (
          <span className="where-folder" title={where.folder}>
            ▸ {where.folder}
          </span>
        )}
        {chosenPc && !chosenPc.online && <span className="where-tag offline">offline</span>}
        <ChevronDownIcon />
      </button>
      {open && (
        <div
          ref={list}
          className="where-list"
          role="listbox"
          aria-label="Where the next message runs"
          tabIndex={-1}
          aria-activedescendant={`where-${active}`}
          onKeyDown={event => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              const step = event.key === "ArrowDown" ? 1 : -1;
              setActive(i => (i + step + targets.length) % targets.length);
            } else if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              if (targets[active]) choose(targets[active]);
            } else if (event.key === "Escape" || event.key === "Tab") {
              // Esc here closes the list, not the reply
              event.stopPropagation();
              event.preventDefault();
              setOpen(false);
              trigger.current?.focus();
            }
          }}
        >
          <div role="presentation">
            <div className="where-group">This server</div>
            <button
              type="button"
              id="where-0"
              role="option"
              aria-selected={!where}
              data-index={0}
              tabIndex={-1}
              className={active === 0 ? "where-option active" : "where-option"}
              onMouseEnter={() => setActive(0)}
              onClick={() => choose(SERVER)}
            >
              <ServerIcon />
              <span className="where-option-text">
                This server
                <small>Documents, memories and the web. No files on a PC.</small>
              </span>
              <span className="where-check">{!where && <CheckIcon />}</span>
            </button>
          </div>
          {pcs.map(pc => (
            <div key={pc.id} role="presentation">
              <div className="where-group pc">
                <span className={pc.online ? "where-dot on" : "where-dot"} aria-hidden="true" />
                {pc.name}
                <small>{pc.online ? "Connected" : `Last seen ${moment(pc.last_seen)}`}</small>
              </div>
              {pc.folders.map(folder => {
                const key = `${pc.id}\n${folder}`;
                const at = pc.online ? targets.findIndex(target => target.key === key) : -1;
                const selected = chosen === key;
                return (
                  <button
                    key={key}
                    type="button"
                    id={at >= 0 ? `where-${at}` : undefined}
                    role="option"
                    aria-selected={selected}
                    aria-disabled={at < 0}
                    data-index={at >= 0 ? at : undefined}
                    tabIndex={-1}
                    className={`where-option${at >= 0 ? "" : " disabled"}${at === active ? " active" : ""}`}
                    onMouseEnter={() => at >= 0 && setActive(at)}
                    onClick={() => at >= 0 && choose(targets[at])}
                  >
                    <PcIcon />
                    <span className="where-option-text">
                      <span className="where-path">{folder}</span>
                      {!pc.online && <small>Not connected: run labb connect there</small>}
                    </span>
                    <span className="where-check">{selected && <CheckIcon />}</span>
                  </button>
                );
              })}
              {!pc.folders.length && <p className="where-empty">No folders from it yet.</p>}
              {pc.online && pc.allow_all && <p className="where-empty problem-text">Nothing there asks first (--allow-all).</p>}
            </div>
          ))}
          <div className="where-foot">
            {!pcs.some(pc => pc.online) && (
              <span>
                Run <code>labb connect</code> on a PC to work in its files.
              </span>
            )}
            <button type="button" className="link-button" onClick={onManage}>
              See your PCs
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
