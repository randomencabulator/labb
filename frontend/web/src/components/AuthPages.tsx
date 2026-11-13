import { type FormEvent, type ReactNode, useCallback, useEffect, useState } from "react";
import {
  LOGGED_OUT,
  type User,
  acceptInvite,
  answerDeviceLogin,
  getDeviceLogin,
  getInvite,
  getStatus,
  logIn,
  setUp,
  type AuthStatus,
} from "../api";
import { Logo, Wordmark } from "./Icons";

// Links into the page: ?invite=<token> (joining), ?device=<code> (approving labb --login) and
// ?setup=<token> (creating the first account, the link the server prints when it starts)
const params = new URLSearchParams(window.location.search);
const inviteToken = params.get("invite");
const deviceCode = params.get("device");
const setupToken = params.get("setup");

/** Drop ?invite / ?device / ?setup from the address once they're done with */
function clearLink() {
  window.history.replaceState(null, "", window.location.pathname);
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="auth-page">
      <div className="auth-card">
        <h1>
          <Logo size={22} />
          <Wordmark />
        </h1>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}

/** Username and password, for setting up, logging in and joining */
function CredentialsForm({ submitLabel, newAccount, onSubmit }: {
  submitLabel: string;
  newAccount: boolean;
  onSubmit: (username: string, password: string) => Promise<void>;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (newAccount && password !== repeat) {
      setError("The passwords don't match");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSubmit(username, password);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <form className="auth-form" onSubmit={submit}>
      <label>
        Username
        <input
          value={username}
          onChange={e => setUsername(e.target.value)}
          autoComplete="username"
          autoCapitalize="none"
          autoFocus
          required
        />
      </label>
      <label>
        Password
        <input
          type="password"
          value={password}
          onChange={e => setPassword(e.target.value)}
          autoComplete={newAccount ? "new-password" : "current-password"}
          minLength={newAccount ? 8 : undefined}
          required
        />
      </label>
      {newAccount && (
        <label>
          Password again
          <input type="password" value={repeat} onChange={e => setRepeat(e.target.value)} autoComplete="new-password" required />
        </label>
      )}
      {error && <div className="problem">{error}</div>}
      <button type="submit" className="primary" disabled={busy}>
        {busy ? "…" : submitLabel}
      </button>
    </form>
  );
}

function DevicePage({ code, user, onDone }: { code: string; user: User; onDone: () => void }) {
  const [machine, setMachine] = useState<string | null>(null);
  const [state, setState] = useState<"loading" | "waiting" | "approved" | "denied" | "gone">("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getDeviceLogin(code)
      .then(found => {
        setMachine(found.machine);
        setState("waiting");
      })
      .catch(() => setState("gone"));
  }, [code]);

  const answer = async (approve: boolean) => {
    try {
      await answerDeviceLogin(code, approve);
      setState(approve ? "approved" : "denied");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Card title="Log in labb">
      {state === "loading" && <p>…</p>}
      {state === "gone" && <p>The code {code} isn't waiting for approval any more. Run <code>labb --login</code> again.</p>}
      {state === "waiting" && (
        <>
          <p>
            labb on <b>{machine}</b> wants to use this server as <b>{user.username}</b>. Check that it shows the code{" "}
            <b className="code">{code}</b>.
          </p>
          <p className="hint">Approving makes an API key for that PC, which you can revoke in Settings → API keys.</p>
          {error && <div className="problem">{error}</div>}
          <div className="auth-actions">
            <button type="button" onClick={() => answer(false)}>
              Deny
            </button>
            <button type="button" className="primary" onClick={() => answer(true)}>
              Approve
            </button>
          </div>
        </>
      )}
      {state === "approved" && <p>Done: labb on {machine} is logged in. You can close this page, or go on to the chat.</p>}
      {state === "denied" && <p>Denied: labb on {machine} isn't logged in.</p>}
      {(state === "approved" || state === "denied" || state === "gone") && (
        <button type="button" className="primary" onClick={onDone}>
          Go to the chat
        </button>
      )}
    </Card>
  );
}

function InvitePage({ token, onJoined }: { token: string; onJoined: () => void }) {
  const [valid, setValid] = useState<boolean | null>(null);

  useEffect(() => {
    getInvite(token).then(() => setValid(true), () => setValid(false));
  }, [token]);

  if (valid === null) return <Card title="Join">…</Card>;
  if (!valid) {
    return (
      <Card title="Join">
        <p>This invite link was used already or has expired. Ask whoever sent it for a new one.</p>
        <button type="button" onClick={() => { clearLink(); window.location.reload(); }}>
          Log in instead
        </button>
      </Card>
    );
  }
  return (
    <Card title="Create your account">
      <CredentialsForm
        submitLabel="Create account"
        newAccount
        onSubmit={async (username, password) => {
          await acceptInvite(token, username, password);
          clearLink();
          onJoined();
        }}
      />
    </Card>
  );
}

/**
 * Shows the app to a logged-in user, and otherwise what it takes to get there: setting up a
 * fresh server, logging in, or joining with an invite. Also approves labb --login.
 */
export function AuthGate({ children }: { children: (user: User, onLogOut: () => void) => ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [device, setDevice] = useState(deviceCode);

  const refresh = useCallback(() => {
    getStatus().then(
      found => {
        setStatus(found);
        setError(null);
      },
      e => setError(`Couldn't reach the server: ${(e as Error).message}`),
    );
  }, []);

  useEffect(refresh, [refresh]);

  // A request answered 401: the session ended (logged out elsewhere, or the account was disabled)
  useEffect(() => {
    window.addEventListener(LOGGED_OUT, refresh);
    return () => window.removeEventListener(LOGGED_OUT, refresh);
  }, [refresh]);

  if (error) {
    return (
      <Card title="Can't connect">
        <div className="problem">{error}</div>
        <button type="button" onClick={refresh}>
          Try again
        </button>
      </Card>
    );
  }
  if (status === null) return null;

  if (status.setup_needed) {
    if (status.setup_token_needed && !setupToken) {
      return (
        <Card title="Set up this server">
          <p className="hint">
            Creating the first account takes the setup link the server printed when it started. Find it with{" "}
            <code>docker compose logs labb-web</code> (a line starting “labb: no accounts yet”) and open it.
          </p>
        </Card>
      );
    }
    return (
      <Card title="Set up this server">
        <p className="hint">Create the first account. It's the admin: it invites everyone else, and gets the chats saved so far.</p>
        <CredentialsForm
          submitLabel="Create admin account"
          newAccount
          onSubmit={async (username, password) => {
            await setUp(username, password, setupToken);
            if (setupToken) clearLink();
            refresh();
          }}
        />
      </Card>
    );
  }

  if (!status.user) {
    if (inviteToken) return <InvitePage token={inviteToken} onJoined={refresh} />;
    return (
      <Card title={device ? "Log in to approve labb" : "Log in"}>
        <CredentialsForm
          submitLabel="Log in"
          newAccount={false}
          onSubmit={async (username, password) => {
            await logIn(username, password);
            refresh();
          }}
        />
        <p className="hint">New here? Ask an admin for an invite link.</p>
      </Card>
    );
  }

  if (device) {
    return (
      <DevicePage
        code={device}
        user={status.user}
        onDone={() => {
          clearLink();
          setDevice(null);
        }}
      />
    );
  }
  if (inviteToken) clearLink();

  return <>{children(status.user, refresh)}</>;
}
