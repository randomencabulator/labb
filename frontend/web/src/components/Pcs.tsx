import { useCallback, useEffect, useState } from "react";
import { type Pc, forgetPc, listPcs } from "../api";
import { moment } from "../format";
import { toast } from "./Toaster";

const LIST_MS = 3000;

/** The PCs you connected with labb connect. Working on one is a chat: see Where, in a new chat. */
export function PcsTab() {
  const [pcs, setPcs] = useState<Pc[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    listPcs().then(found => (setPcs(found), setError(null)), e => setError((e as Error).message));
  }, []);
  useEffect(() => {
    reload();
    const timer = setInterval(reload, LIST_MS);
    return () => clearInterval(timer);
  }, [reload]);

  return (
    <>
      <div className="agents-intro">
        <p className="hint">
          Chat with labb on your own PCs from here. On a PC, run <code>labb connect ~/code/project</code> and leave it running: only you can
          reach it, only in the folders you named, and its commands and edits ask you here first. Then pick it under Where in a new chat.
          Ctrl-C there disconnects it.
        </p>
      </div>
      {error && <div className="problem">{error}</div>}
      {pcs && pcs.length === 0 && <p className="hint">No PCs yet.</p>}
      <ul className="task-list">
        {pcs?.map(pc => (
          <li key={pc.id} className={pc.online ? "task" : "task off"}>
            <div className="task-main">
              <div className="task-text">
                <b>{pc.name}</b>
                <span className="muted">{pc.online ? "Connected" : `Last seen ${moment(pc.last_seen)}`}{pc.version ? ` · labb ${pc.version}` : ""}</span>
                {pc.allow_all && <span className="problem-text">Connected with --allow-all: nothing there asks first</span>}
                <span className="muted">{pc.folders.join(", ")}</span>
              </div>
              {!pc.online && (
                <button
                  type="button"
                  onClick={() => {
                    if (window.confirm(`Take ${pc.name} off the list? Its sessions stay, read-only.`)) {
                      forgetPc(pc.id).then(reload, e => toast((e as Error).message));
                    }
                  }}
                >
                  Forget
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
