// The steps as a list, top to bottom, for small screens (and anyone who'd rather read than pan):
// tap one to set it up; what it comes after, how the last run went and what's wrong show on each
import { useEffect, useRef, useState } from "react";
import type { Catalog, Definition, StepState } from "../../studioApi";
import { StepIcon } from "../Icons";
import { type Diff, inOrder, summary } from "./graph";
import type { Names } from "./Preview";

const STATUS_LABEL: Record<string, string> = {
  running: "Running", done: "Done", skipped: "Skipped", failed: "Failed", waiting: "Waiting", stopped: "Stopped",
};

export function StepList({ definition, catalog, names, selected, picked, problems, statuses, diffs, onSelect }: {
  definition: Definition;
  catalog: Catalog;
  names: Names;
  selected: string | null;
  picked: string[];
  problems: Record<string, string[]>;
  statuses: Map<string, StepState["status"]>;
  diffs: Map<string, Diff>;
  onSelect: (id: string, add: boolean) => void;
}) {
  const steps = inOrder(definition);
  const label = (id: string) => definition.nodes.find(n => n.id === id)?.label ?? "a step";
  return (
    <ol className="step-list">
      {steps.map(step => {
        const spec = catalog.nodes[step.type];
        const from = definition.edges.filter(e => e.target === step.id);
        // Worth saying only when it isn't simply "after the one above"
        const after = from.length > 1 || from.some(e => e.handle) ?
          from.map(e => `${label(e.source)}${e.handle ? ` (${e.handle})` : ""}`).join(" and ") : null;
        const status = statuses.get(step.id);
        const wrong = problems[step.id] ?? [];
        const diff = diffs.get(step.id);
        const classes = ["step-item", `cat-${spec?.category ?? "logic"}`, step.id === selected ? "selected" : "",
          picked.includes(step.id) ? "picked" : "", status ? `is-${status}` : "", diff ? `diff-${diff}` : "", wrong.length ? "has-problems" : ""];
        return (
          <li key={step.id} data-step={step.id}>
            <button type="button" className={classes.filter(Boolean).join(" ")} aria-pressed={step.id === selected}
              onClick={e => onSelect(step.id, e.shiftKey)}>
              <span className="step-item-icon" aria-hidden><StepIcon type={step.type} /></span>
              <span className="step-item-main">
                <span className="step-item-head">
                  <b>{step.label || spec?.label}</b>
                  {diff && <span className="diff-tag">{diff === "added" ? "New" : diff === "changed" ? "Changed" : "Removed"}</span>}
                  {status && <span className={`step-status ${status}`}>{STATUS_LABEL[status]}</span>}
                  {!status && wrong.length > 0 && <span className="step-problem" aria-label={`${wrong.length} to fix`}>!</span>}
                </span>
                <span className="step-item-kind">{spec?.label ?? step.type}{step.type !== "trigger" && <> · <code>{`{{${step.key}}}`}</code></>}</span>
                {after && <span className="step-item-after">After {after}</span>}
                {summary(step, names) && <span className="step-item-summary">{summary(step, names)}</span>}
                {wrong.length > 0 && <span className="step-item-problem">{wrong[0]}</span>}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

/** Steps to add, by kind, with a search: a popover over the canvas (a sheet on a phone) */
export function StepPicker({ catalog, after, draggable, onAdd, onClose }: {
  catalog: Catalog;
  // The step it'll go after, if one is selected
  after: string | null;
  draggable: boolean;
  onAdd: (type: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);
  useEffect(() => {
    // Not on touch screens: the keyboard would cover the list
    if (window.matchMedia("(hover: hover)").matches) search.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    const onDown = (e: PointerEvent) => {
      if (box.current && !box.current.contains(e.target as Node) && !(e.target as HTMLElement).closest(".add-step-button")) onClose();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [onClose]);

  const q = query.trim().toLowerCase();
  const groups = catalog.categories.filter(([c]) => c !== "trigger").map(([category, label]) => ({
    category, label,
    items: Object.entries(catalog.nodes).filter(([type, spec]) => spec.category === category &&
      (!q || spec.label.toLowerCase().includes(q) || spec.description.toLowerCase().includes(q) || type.includes(q))),
  })).filter(g => g.items.length);
  const first = groups[0]?.items[0]?.[0];

  return (
    <div className="step-picker" ref={box} role="dialog" aria-label="Add a step">
      <div className="step-picker-head">
        <input ref={search} value={query} onChange={e => setQuery(e.target.value)} placeholder="Find a step: email, approval, python…"
          aria-label="Find a step" onKeyDown={e => e.key === "Enter" && first && (e.preventDefault(), onAdd(first))} />
        <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>×</button>
      </div>
      <p className="step-picker-where">{after ? <>Goes after <b>{after}</b>, connected</> : draggable ? "Click to add, or drag onto the canvas" : "Tap to add"}</p>
      <div className="step-picker-groups">
        {groups.map(g => (
          <section key={g.category}>
            <h4>{g.label}</h4>
            <div className="step-picker-items">
              {g.items.map(([type, spec]) => (
                <button key={type} type="button" className={`picker-item cat-${g.category}`} draggable={draggable}
                  onDragStart={e => (e.dataTransfer.setData("application/labb-step", type), setTimeout(onClose))}
                  onClick={() => onAdd(type)}>
                  <span className="step-icon" aria-hidden><StepIcon type={type} /></span>
                  <span className="picker-item-text">
                    <b>{spec.label}</b>
                    <span>{spec.description}</span>
                  </span>
                </button>
              ))}
            </div>
          </section>
        ))}
        {groups.length === 0 && <p className="muted small">No step like that.</p>}
      </div>
    </div>
  );
}
