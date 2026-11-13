import { useEffect, useRef, useState } from "react";
import type { Agent } from "../api";
import { CheckIcon, ChevronDownIcon, Logo } from "./Icons";

type Props = {
  // Recently used first
  agents: Agent[];
  agent: Agent | null;
  onAgent: (agent: Agent | null) => void;
  // Once the chat has messages, its agent stays
  locked: boolean;
};

/** Who a new chat is with: labb or one of the agents. Works like the model picker. */
export function AgentPicker({ agents, agent, onAgent, locked }: Props) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const options: (Agent | null)[] = [null, ...agents];

  useEffect(() => {
    if (!open) return;
    setActive(Math.max(0, options.findIndex(a => (a?.id ?? null) === (agent?.id ?? null))));
    list.current?.focus();
    const onClick = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
    // Only when it opens: the highlight starts at the current agent
  }, [open]);

  useEffect(() => {
    if (open) list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  // Nothing to choose from: no pill for the default assistant
  if (!agents.length && !agent) return null;

  const choose = (choice: Agent | null) => {
    onAgent(choice);
    setOpen(false);
    trigger.current?.focus();
  };

  const label = agent ? agent.name : "labb";

  if (locked) {
    return (
      <span className="model-trigger agent-trigger locked" title={agent ? `This chat is with ${agent.name}` : undefined}>
        {agent && <span aria-hidden>{agent.emoji}</span>}
        <span className="model-name">{label}</span>
      </span>
    );
  }

  return (
    <div className="model-picker" ref={root}>
      <button
        ref={trigger}
        type="button"
        className="model-trigger agent-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Agent: ${label}`}
        title="Choose who to chat with"
        onClick={() => setOpen(!open)}
      >
        <span aria-hidden>{agent ? agent.emoji : <Logo size={14} />}</span>
        <span className="model-name">{label}</span>
        <ChevronDownIcon />
      </button>
      {open && (
        <ul
          ref={list}
          className="model-list"
          role="listbox"
          aria-label="Agents"
          tabIndex={-1}
          aria-activedescendant={`agent-${active}`}
          onKeyDown={event => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              const step = event.key === "ArrowDown" ? 1 : -1;
              setActive(i => (i + step + options.length) % options.length);
            } else if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              choose(options[active]);
            } else if (event.key === "Escape" || event.key === "Tab") {
              event.stopPropagation();
              event.preventDefault();
              setOpen(false);
              trigger.current?.focus();
            }
          }}
        >
          {options.map((option, index) => (
            <li
              key={option?.id ?? "labb"}
              id={`agent-${index}`}
              data-index={index}
              role="option"
              aria-selected={(option?.id ?? null) === (agent?.id ?? null)}
              className={index === active ? "active" : undefined}
              onMouseEnter={() => setActive(index)}
              onClick={() => choose(option)}
            >
              <span className="agent-option-emoji" aria-hidden>
                {option ? option.emoji : <Logo size={16} />}
              </span>
              <span className="model-name">
                {option ? option.name : "labb"}
                <small>{option ? option.description || (option.mine ? "" : `by ${option.owner}`) : "The default assistant"}</small>
              </span>
              <span className="model-check">{(option?.id ?? null) === (agent?.id ?? null) && <CheckIcon />}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
