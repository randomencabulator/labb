import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  type AppClient,
  type AppConnection,
  disconnectApp,
  finishConnection,
  listAppClients,
  listConnections,
  removeAppClient,
  saveAppClient,
  startConnection,
} from "../api";
import { relativeTime } from "../format";
import { CopyButton } from "./CopyButton";

/** What the app sent back after connecting (?connected= or ?error=), once; the address is tidied */
function useReturnNote() {
  const [note] = useState(() => {
    const query = new URLSearchParams(window.location.search);
    const error = query.get("error");
    const connected = query.get("connected");
    if (error || connected) window.history.replaceState(window.history.state, "", window.location.pathname);
    return error ? { ok: false, text: error } : connected ? { ok: true, text: "Connected." } : null;
  });
  return note;
}

function AppCard({ app, onChange }: { app: AppConnection; onChange: () => void }) {
  const [pasting, setPasting] = useState(false);
  const [pasted, setPasted] = useState("");
  const [error, setError] = useState<string | null>(null);
  const connection = app.connection;
  const status = !connection ? "not connected" : connection.status === "ok" ? "connected" : "needs connecting again";

  const connect = async () => {
    try {
      setError(null);
      const started = await startConnection(app.name);
      if (started.mode === "redirect") {
        window.location.href = started.url;
        return;
      }
      window.open(started.url, "_blank", "noopener");
      setPasting(true);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const finish = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await finishConnection(app.name, pasted);
      setPasting(false);
      setPasted("");
      setError(null);
      onChange();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="provider-card">
      <div className="provider-head">
        <div>
          <b>{app.label}</b> {connection?.account && <span className="hint">{connection.account}</span>}
        </div>
        <span className={`provider-status ${connection?.status === "ok" ? "on" : ""}`}>{status}</span>
      </div>
      <ul className="hint app-uses">
        {app.uses.map(use => (
          <li key={use}>{use}</li>
        ))}
      </ul>
      {connection && connection.status === "ok" && connection.missing.length > 0 && (
        <div className="problem">You didn't allow everything, so some of it won't work. Connect again and tick every box.</div>
      )}
      {connection && <div className="hint">Connected {relativeTime(connection.connected_at)}</div>}
      {!app.available && <div className="hint">An admin has to set {app.label} up first, in the server's Settings → Integrations.</div>}
      {pasting && (
        <form className="auth-form" onSubmit={finish}>
          <div className="hint">
            Allow labb in the tab that opened. {app.label} then sends you to a page that doesn't load (its address starts with{" "}
            <code>http://localhost</code>). Copy that whole address from the address bar and paste it here.
          </div>
          <label>
            The address you ended up at
            <input value={pasted} onChange={e => setPasted(e.target.value)} placeholder="http://localhost:8000/connections/…" required autoFocus />
          </label>
          <div className="auth-actions">
            <button type="button" onClick={() => setPasting(false)}>
              Cancel
            </button>
            <button type="submit" className="primary">
              Finish
            </button>
          </div>
        </form>
      )}
      {error && <div className="problem">{error}</div>}
      {!pasting && (
        <div className="provider-actions">
          {app.available && (
            <button type="button" className={connection?.status === "ok" ? undefined : "primary"} onClick={connect}>
              {connection ? "Connect again" : `Connect ${app.label}`}
            </button>
          )}
          {connection && (
            <button
              type="button"
              className="danger"
              onClick={async () => {
                if (!confirm(`Disconnect ${app.label}? The assistant, your agents and tasks can't use it any more.`)) return;
                await disconnectApp(app.name).catch(e => setError((e as Error).message));
                onChange();
              }}
            >
              Disconnect
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** Settings → Integrations (yours): your own accounts, for the assistant's calendar, mail, GitHub and Notion tools */
export function IntegrationsTab() {
  const [apps, setApps] = useState<AppConnection[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const note = useReturnNote();
  const reload = useCallback(() => {
    listConnections().then(setApps, e => setError((e as Error).message));
  }, []);
  useEffect(reload, [reload]);

  return (
    <>
      <p className="hint">
        Connect your accounts and the assistant can use them in your chats, with your agents and in your tasks: only yours, never
        anyone else's. What it reads goes only to the model you're chatting with. It never sends mail: it writes drafts for you to send.
        Integrations an admin hasn't set up show here once they are.
      </p>
      {note && <div className={note.ok ? "ok-note" : "problem"}>{note.text}</div>}
      {error && <div className="problem">{error}</div>}
      <div className="provider-grid">
        {apps?.map(app => (
          <AppCard key={app.name} app={app} onChange={reload} />
        ))}
      </div>
    </>
  );
}

function ClientCard({ app, onChange }: { app: AppClient; onChange: (apps?: AppClient[]) => void }) {
  const [editing, setEditing] = useState(!app.configured);
  const [clientId, setClientId] = useState(app.client_id ?? "");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      onChange(await saveAppClient(app.name, { client_id: clientId, ...(secret ? { client_secret: secret } : {}) }));
      setSecret("");
      setEditing(false);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="provider-card">
      <div className="provider-head">
        <b>{app.label}</b>
        <span className={`provider-status ${app.configured ? "on" : ""}`}>{app.configured ? "set up" : "not set up"}</span>
      </div>
      <ul className="hint app-uses">
        {app.uses.map(use => (
          <li key={use}>{use}</li>
        ))}
      </ul>
      <ol className="hint app-steps">
        {app.steps.map((step, i) => (
          <li key={step}>
            {step}
            {/* The step that ends asking for it */}
            {step.endsWith(":") && i === app.steps.findIndex(s => s.endsWith(":")) && (
              <div className="app-uri">
                <code>{app.redirect_uri}</code> <CopyButton getText={() => app.redirect_uri} />
              </div>
            )}
          </li>
        ))}
        <li>
          Paste its client ID and secret here.{" "}
          <a href={app.console} target="_blank" rel="noreferrer">
            Open {app.label}'s developer settings
          </a>
        </li>
      </ol>
      {app.mode === "paste" && (
        <div className="hint">
          {app.label} only sends people back to <code>localhost</code> or an <code>https</code> address on a public domain, and this server
          is at <code>{app.public_url}</code>. So people finish connecting by pasting the address they end up at. With an https address (a
          reverse proxy, Tailscale…), set <code>LABB_PUBLIC_URL</code> to it and they come straight back.
        </div>
      )}
      {editing ? (
        <form className="auth-form" onSubmit={submit}>
          <label>
            Client ID
            <input value={clientId} onChange={e => setClientId(e.target.value)} placeholder={app.id_hint} required autoComplete="off" />
          </label>
          <label>
            Client secret
            <input
              type="password"
              value={secret}
              onChange={e => setSecret(e.target.value)}
              placeholder={app.configured ? "Set: leave empty to keep it" : app.secret_hint}
              required={!app.configured}
              autoComplete="off"
            />
          </label>
          {error && <div className="problem">{error}</div>}
          <div className="auth-actions">
            {app.configured && (
              <button type="button" onClick={() => setEditing(false)}>
                Cancel
              </button>
            )}
            <button type="submit" className="primary">
              Save
            </button>
          </div>
        </form>
      ) : (
        <>
          <div className="provider-detail">
            <code>{app.client_id}</code>
            <span className="hint"> · {app.source === "saved" ? "saved here" : "from .env"}</span>
          </div>
          <div className="hint">
            {app.connected_users} {app.connected_users === 1 ? "person has" : "people have"} connected
          </div>
          {error && <div className="problem">{error}</div>}
          <div className="provider-actions">
            <button type="button" onClick={() => setEditing(true)}>
              Edit
            </button>
            {app.source === "saved" && (
              <button
                type="button"
                className="danger"
                onClick={async () => {
                  if (!confirm(`Remove ${app.label}'s client? Until it's set up again, nobody's connection works.`)) return;
                  try {
                    await removeAppClient(app.name);
                    onChange();
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              >
                Remove
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/** Settings → Integrations (the server's, admins): the OAuth clients people connect their accounts through */
export function IntegrationsSetupTab() {
  const [apps, setApps] = useState<AppClient[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    listAppClients().then(setApps, e => setError((e as Error).message));
  }, []);
  useEffect(reload, [reload]);

  return (
    <>
      <p className="hint">
        Set up the integrations people can connect their own accounts to, in their own Settings → Integrations. Each person allows access
        for themselves, and only what's set up here is offered.
      </p>
      {error && <div className="problem">{error}</div>}
      <div className="provider-grid">
        {apps?.map(app => (
          <ClientCard key={app.name} app={app} onChange={next => (next ? setApps(next) : reload())} />
        ))}
      </div>
    </>
  );
}
