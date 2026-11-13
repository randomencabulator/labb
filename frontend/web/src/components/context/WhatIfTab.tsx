import { useEffect, useState } from "react";
import { type AnswerMode, type ContextInspection, type ContextOptions, type Engine, type Model, getContext } from "../../api";
import { ANSWER_MODES } from "../AnswerModePicker";

const change = (n: number) => (n === 0 ? "–" : `${n > 0 ? "+" : "−"}${Math.abs(n).toLocaleString()}`);

/** Try other choices for the next message and see what they'd do. Nothing is sent to the model,
 * and the message box keeps what it has. */
export function WhatIfTab({ threadId, seen, options, models, engines, currentEngine, webAvailable }: {
  threadId: string;
  seen: ContextInspection;
  options: ContextOptions;
  models: Model[];
  engines: Engine[];
  currentEngine: string;
  webAvailable: boolean;
}) {
  const [model, setModel] = useState(seen.model);
  const [mode, setMode] = useState<AnswerMode>(options.answerMode ?? "auto");
  const [web, setWeb] = useState(options.web !== false);
  const [engineId, setEngineId] = useState(currentEngine);
  const [other, setOther] = useState<ContextInspection | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The chat's model isn't always listed (an agent's, or one no longer offered)
  const modelChoices = models.some(m => m.spec === seen.model) ? models : [{ spec: seen.model, name: seen.model, vision: null }, ...models];
  const changed = model !== seen.model || mode !== (options.answerMode ?? "auto") || web !== (options.web !== false) || engineId !== currentEngine;

  useEffect(() => {
    if (!changed) {
      setOther(null);
      setError(null);
      return;
    }
    let current = true;
    // Only what's different from the next message's own choices is a picked engine: what the chat has stays as it decides
    getContext(threadId, { model, answerMode: mode, web, engineId: engineId !== currentEngine ? engineId : options.engineId }, seen.scale).then(
      found => {
        if (!current) return;
        setOther(found);
        setError(null);
      },
      e => {
        if (!current) return;
        setOther(null);
        setError(e instanceof Error ? e.message : String(e));
      },
    );
    return () => {
      current = false;
    };
  }, [threadId, changed, model, mode, web, engineId, seen.used, seen.total_messages, seen.scale]);

  const tokensOf = (of: ContextInspection, kind: string) => of.parts.find(part => part.kind === kind)?.tokens ?? 0;
  const kinds = other ? [...new Map([...seen.parts, ...other.parts].map(part => [part.kind, part.label])).entries()] : [];
  const namesOf = (list: { name?: string; label?: string }[]) => new Set(list.map(item => item.name ?? item.label ?? ""));
  const differing = (a: Set<string>, b: Set<string>) => [...a].filter(name => !b.has(name));
  const toolsAdded = other ? differing(namesOf(other.tools), namesOf(seen.tools)) : [];
  const toolsGone = other ? differing(namesOf(seen.tools), namesOf(other.tools)) : [];
  const sectionsAdded = other ? differing(namesOf(other.system), namesOf(seen.system)) : [];
  const sectionsGone = other ? differing(namesOf(seen.system), namesOf(other.system)) : [];

  return (
    <>
      <div className="context-whatif-choices">
        <label>
          Model
          <select value={model} onChange={e => setModel(e.target.value)}>
            {modelChoices.map(m => <option key={m.spec} value={m.spec}>{m.name}</option>)}
          </select>
        </label>
        <label>
          Answer mode
          <select value={mode} onChange={e => setMode(e.target.value as AnswerMode)}>
            {ANSWER_MODES.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        </label>
        {engines.length > 1 && (
          <label>
            Engine
            <select value={engineId} onChange={e => setEngineId(e.target.value)}>
              {engines.some(h => h.id === currentEngine) ? null : <option value={currentEngine}>{currentEngine}</option>}
              {engines.map(h => <option key={h.id} value={h.id}>{h.name}</option>)}
            </select>
          </label>
        )}
        {webAvailable && (
          <label className="context-whatif-web">
            <input type="checkbox" checked={web} onChange={e => setWeb(e.target.checked)} /> Web
          </label>
        )}
      </div>
      <p className="muted context-count">Nothing is sent to the model, and the message box keeps its choices.</p>

      {error && <p className="error">{error}</p>}
      {!changed && <p className="muted">Change something above to compare it with the next message as it stands.</p>}
      {changed && !other && !error && <p className="muted">Loading…</p>}
      {other && (
        <>
          <table className="context-parts context-compare">
            <thead>
              <tr><th /><th>Now</th><th>What-if</th><th>Change</th></tr>
            </thead>
            <tbody>
              {kinds.map(([kind, label]) => (
                <tr key={kind}>
                  <td>{label}</td>
                  <td>{tokensOf(seen, kind).toLocaleString()}</td>
                  <td>{tokensOf(other, kind).toLocaleString()}</td>
                  <td>{change(tokensOf(other, kind) - tokensOf(seen, kind))}</td>
                </tr>
              ))}
              <tr className="context-compare-total">
                <td>Total</td>
                <td>{seen.used.toLocaleString()}</td>
                <td>{other.used.toLocaleString()}</td>
                <td>{change(other.used - seen.used)}</td>
              </tr>
              <tr>
                <td>Window</td>
                <td>{seen.window.toLocaleString()}</td>
                <td>{other.window.toLocaleString()}</td>
                <td>{change(other.window - seen.window)}</td>
              </tr>
              <tr>
                <td>Share of the window</td>
                <td>{Math.round(seen.share * 100)}%</td>
                <td>{Math.round(other.share * 100)}%</td>
                <td />
              </tr>
              <tr>
                <td>Summarizes first</td>
                <td>{seen.will_compact ? "Yes" : "No"}</td>
                <td>{other.will_compact ? "Yes" : "No"}</td>
                <td />
              </tr>
            </tbody>
          </table>
          <ul className="context-differences">
            {toolsAdded.length > 0 && <li>Tools added: {toolsAdded.join(", ")}</li>}
            {toolsGone.length > 0 && <li>Tools gone: {toolsGone.join(", ")}</li>}
            {sectionsAdded.length > 0 && <li>System sections added: {sectionsAdded.join(", ")}</li>}
            {sectionsGone.length > 0 && <li>System sections gone: {sectionsGone.join(", ")}</li>}
          </ul>
        </>
      )}
    </>
  );
}
