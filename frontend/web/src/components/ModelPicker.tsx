import { useEffect, useRef, useState } from "react";
import type { Model } from "../api";
import { CheckIcon, ChevronDownIcon, EyeIcon } from "./Icons";

export const PROVIDERS: Record<string, string> = {
  ollama: "Ollama · on your server",
  "ollama-cloud": "Ollama Cloud",
  openai: "OpenAI",
  anthropic: "Anthropic",
  google_genai: "Google",
  mistralai: "Mistral",
  groq: "Groq",
};

const provider = (spec: string) => spec.split(":", 1)[0];

type Props = { models: Model[]; model: string | null; onModel: (spec: string) => void };

/** The model for the next message: a pill that opens a list grouped by provider (it opens
 * upwards, above the message box). Arrow keys, Enter and Esc work like a select. */
export function ModelPicker({ models, model, onModel }: Props) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  const current = models.find(m => m.spec === model);
  // In groups, in the order the server listed the providers
  const groups = [...new Set(models.map(m => provider(m.spec)))].map(p => ({ id: p, models: models.filter(m => provider(m.spec) === p) }));
  const ordered = groups.flatMap(g => g.models);

  useEffect(() => {
    if (!open) return;
    setActive(Math.max(0, ordered.findIndex(m => m.spec === model)));
    list.current?.focus();
    const onClick = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
    // Only when it opens: the highlight starts at the current model
  }, [open]);

  // Keep the highlighted one in view
  useEffect(() => {
    if (open) list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  const choose = (spec: string) => {
    onModel(spec);
    setOpen(false);
    trigger.current?.focus();
  };

  if (!models.length) return null;

  return (
    <div className="model-picker" ref={root}>
      <button
        ref={trigger}
        type="button"
        className="model-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Model: ${current?.name ?? "default"}`}
        title="Choose the model"
        onClick={() => setOpen(!open)}
      >
        <span className="model-name">{current?.name ?? "Model"}</span>
        {current?.vision && <EyeIcon />}
        <ChevronDownIcon />
      </button>
      {open && (
        <ul
          ref={list}
          className="model-list"
          role="listbox"
          aria-label="Models"
          tabIndex={-1}
          aria-activedescendant={`model-${active}`}
          onKeyDown={event => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              const step = event.key === "ArrowDown" ? 1 : -1;
              setActive(i => (i + step + ordered.length) % ordered.length);
            } else if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              choose(ordered[active].spec);
            } else if (event.key === "Escape" || event.key === "Tab") {
              // Esc here closes the list, not the reply
              event.stopPropagation();
              event.preventDefault();
              setOpen(false);
              trigger.current?.focus();
            }
          }}
        >
          {groups.map(group => (
            <li key={group.id} role="presentation">
              <div className="model-group">{PROVIDERS[group.id] ?? group.id}</div>
              <ul role="group" aria-label={PROVIDERS[group.id] ?? group.id}>
                {group.models.map(m => {
                  const index = ordered.indexOf(m);
                  return (
                    <li
                      key={m.spec}
                      id={`model-${index}`}
                      data-index={index}
                      role="option"
                      aria-selected={m.spec === model}
                      className={index === active ? "active" : undefined}
                      onMouseEnter={() => setActive(index)}
                      onClick={() => choose(m.spec)}
                    >
                      <span className="model-name">{m.name}</span>
                      {m.vision && (
                        <span className="model-tag" title="Can see images">
                          <EyeIcon /> vision
                        </span>
                      )}
                      {m.tools === false && (
                        <span className="model-tag" title="Can't call tools: no web search, documents or memories">
                          no tools
                        </span>
                      )}
                      <span className="model-check">{m.spec === model && <CheckIcon />}</span>
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
