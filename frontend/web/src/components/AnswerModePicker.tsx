import { useEffect, useRef, useState } from "react";
import type { AnswerMode } from "../api";
import { CheckIcon, ChevronDownIcon } from "./Icons";

// The server's llm.ANSWER_MODES
export const ANSWER_MODES: { id: AnswerMode; name: string; description: string }[] = [
  { id: "fast", name: "Fast", description: "Answers right away" },
  { id: "auto", name: "Auto", description: "The model decides how long to think" },
  { id: "thinking", name: "Thinking", description: "Thinks longer, for hard questions" },
];

type Props = { mode: AnswerMode; onMode: (mode: AnswerMode) => void };

/** How much the model thinks before it answers: a pill next to the model picker that opens
 * upwards. Arrow keys, Enter and Esc work like a select. */
export function AnswerModePicker({ mode, onMode }: Props) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  const current = ANSWER_MODES.find(m => m.id === mode) ?? ANSWER_MODES[1];

  useEffect(() => {
    if (!open) return;
    setActive(Math.max(0, ANSWER_MODES.findIndex(m => m.id === mode)));
    list.current?.focus();
    const onClick = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
    // Only when it opens: the highlight starts at the current mode
  }, [open]);

  const choose = (id: AnswerMode) => {
    onMode(id);
    setOpen(false);
    trigger.current?.focus();
  };

  return (
    <div className="model-picker" ref={root}>
      <button
        ref={trigger}
        type="button"
        className="model-trigger mode-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Answer mode: ${current.name}`}
        title="How much the model thinks before it answers"
        onClick={() => setOpen(!open)}
      >
        <span className="model-name">{current.name}</span>
        <ChevronDownIcon />
      </button>
      {open && (
        <ul
          ref={list}
          className="model-list"
          role="listbox"
          aria-label="Answer modes"
          tabIndex={-1}
          aria-activedescendant={`answer-mode-${active}`}
          onKeyDown={event => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              const step = event.key === "ArrowDown" ? 1 : -1;
              setActive(i => (i + step + ANSWER_MODES.length) % ANSWER_MODES.length);
            } else if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              choose(ANSWER_MODES[active].id);
            } else if (event.key === "Escape" || event.key === "Tab") {
              // Esc here closes the list, not the reply
              event.stopPropagation();
              event.preventDefault();
              setOpen(false);
              trigger.current?.focus();
            }
          }}
        >
          {ANSWER_MODES.map((m, index) => (
            <li
              key={m.id}
              id={`answer-mode-${index}`}
              role="option"
              aria-selected={m.id === mode}
              className={index === active ? "active" : undefined}
              onMouseEnter={() => setActive(index)}
              onClick={() => choose(m.id)}
            >
              <span className="model-name">
                {m.name}
                <small>{m.description}</small>
              </span>
              <span className="model-check">{m.id === mode && <CheckIcon />}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
