// The Engines page: the built-in, yours and shared ones; opens the editor and the comparison.
import { useCallback, useEffect, useState } from "react";
import { type Engine, type EngineCatalog, type EngineList, type Model, type User, engineApi } from "../../api";
import { NotFound } from "../NotFound";
import { toast } from "../Toaster";
import { Compare } from "./Compare";
import { Editor } from "./Editor";
import "./engine.css";

type Props = {
  // What the address opens: an id, "compare", or nothing for the cards
  route: string[];
  onRoute: (rest: string[]) => void;
  models: Model[];
  model: string | null;
  user: User;
  onChanged: () => void;
};

export default function Engines({ route, onRoute, models, model, user, onChanged }: Props) {
  const [list, setList] = useState<EngineList | null>(null);
  const [catalog, setCatalog] = useState<EngineCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setList(await engineApi.list());
    } catch (e) {
      setError((e as Error).message);
    }
    onChanged();
  }, [onChanged]);

  useEffect(() => {
    refresh();
    engineApi.catalog().then(setCatalog, e => setError((e as Error).message));
  }, []);

  if (error) return <div className="page-inner"><p className="error">{error}</p></div>;
  if (!list || !catalog) return <div className="page-inner"><p className="muted">Loading…</p></div>;

  const opened = route[0];
  if (opened === "compare") return <Compare engines={list.engines} model={model} onBack={() => onRoute([])} />;
  if (opened) {
    const found = list.engines.find(h => h.id === opened);
    if (!found) return <div className="page-inner"><NotFound text="There's no such engine: it was deleted, or it isn't shared with you." back="Back to the engines" onBack={() => onRoute([])} /></div>;
    return <Editor key={found.id} engine={found} catalog={catalog} models={models} onBack={() => onRoute([])} onSaved={refresh} onOpen={id => onRoute([id])} />;
  }

  const run = async (action: () => Promise<unknown>, failed: string) => {
    try {
      await action();
      await refresh();
    } catch (e) {
      toast(`${failed}: ${(e as Error).message}`);
    }
  };
  const fork = async (id: string) => {
    try {
      const copy = await engineApi.duplicate(id);
      await refresh();
      onRoute([copy.id]);
    } catch (e) {
      toast(`Couldn't fork it: ${(e as Error).message}`);
    }
  };
  const importFile = async (file: File) => {
    try {
      const { description, ...overlay } = JSON.parse(await file.text());
      const made = await engineApi.create({ name: file.name.replace(/\.json$/i, "") || "Imported", description: description ?? "", overlay });
      await refresh();
      onRoute([made.id]);
    } catch (e) {
      toast(`Couldn't import it: ${(e as Error).message}`);
    }
  };

  const mine = list.engines.filter(h => h.mine);
  const others = list.engines.filter(h => !h.mine && !h.built_in);
  const builtIns = list.engines.filter(h => h.built_in);

  const card = (h: Engine) => {
    const isMine = (list.my_default ?? list.server_default ?? "default") === h.id;
    return (
      <li key={h.id} className={`agent-card engine-card${isMine ? " current" : ""}`}>
        <button type="button" className="agent-main" onClick={() => onRoute([h.id])}>
          <span className="agent-text">
            <b>{h.name}</b>
            <span>{h.description || (h.changes.length ? h.changes.slice(0, 3).join(", ") : "No description")}</span>
          </span>
        </button>
        <div className="agent-meta">
          {h.built_in ? <span className="model-tag">Built-in</span> : <span className="model-tag">{h.changes.length} change{h.changes.length === 1 ? "" : "s"}</span>}
          {!h.built_in && <span className="model-tag">v{h.version}</span>}
          {h.overlay.loop && <span className="model-tag">Own loop</span>}
          {h.web_problems.length > 0 && <span className="model-tag warn" title={h.web_problems.join(". ")}>Terminal only</span>}
          {h.mine && h.shared && <span className="model-tag">Shared</span>}
          {!h.mine && !h.built_in && <span className="muted">by {h.owner ?? "someone"}</span>}
          {list.my_default === h.id && <span className="model-tag on">Your default</span>}
          {list.server_default === h.id && <span className="model-tag on">Server default</span>}
          {!list.my_default && !list.server_default && h.id === "default" && <span className="model-tag on">Your default</span>}
        </div>
        <div className="agent-actions">
          <button type="button" className={h.mine ? "primary" : ""} onClick={() => onRoute([h.id])}>{h.mine ? "Edit" : "Look"}</button>
          <button type="button" className={h.mine ? "" : "primary"} onClick={() => fork(h.id)} title="Your own copy to change">Fork</button>
          {list.my_default !== h.id && !h.web_problems.length && !(h.id === "default" && !list.my_default && !list.server_default) && (
            <button type="button" title="New chats use it, unless an agent has its own" onClick={() => run(() => engineApi.setMyDefault(h.id === "default" && !list.server_default ? null : h.id), "Couldn't set it")}>Use by default</button>
          )}
          {user.role === "admin" && list.server_default !== h.id && !h.web_problems.length && (h.id === "default" ? list.server_default : h.shared) && (
            <button type="button" title="For everyone who hasn't chosen their own" onClick={() => run(() => engineApi.setServerDefault(h.id === "default" ? null : h.id), "Couldn't set it")}>For the server</button>
          )}
          {h.mine && (
            <button type="button" className="danger" onClick={() => {
              if (confirm(`Delete ${h.name}, with its versions? Chats and agents that use it go on with the default.`)) run(() => engineApi.remove(h.id), "Couldn't delete it");
            }}>Delete</button>
          )}
        </div>
      </li>
    );
  };

  return (
    <div className="page-inner">
      <div className="page-head"><h2>Engines</h2></div>
      <div className="agents-intro">
        <p className="hint">
          An engine is a version of the agent's core: what it's told, the steps of a reply, what stays in its context, its tools. labb's own is one
          of them. Fork it, change anything, try it next to the editor, and compare versions on the same prompts. Pick one for a chat in the message box, for an agent in its editor, or in the terminal with <code>/engine</code>.
        </p>
        <div className="engine-intro-actions">
          <button type="button" className="primary" onClick={() => fork("default")}>Fork the default</button>
          <button type="button" onClick={() => onRoute(["compare"])}>Compare</button>
          <label className="button-like">Import<input type="file" accept="application/json,.json" hidden onChange={e => { const file = e.target.files?.[0]; if (file) importFile(file); e.target.value = ""; }} /></label>
        </div>
      </div>
      <ul className="agent-grid">{[...builtIns, ...mine].map(card)}</ul>
      {others.length > 0 && (
        <>
          <h3>Shared on this server</h3>
          <ul className="agent-grid">{others.map(card)}</ul>
        </>
      )}
    </div>
  );
}
