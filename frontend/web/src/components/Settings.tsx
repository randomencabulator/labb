import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  type ApiKey,
  type Invite,
  type MissingDefaults,
  type Role,
  type User,
  cancelInvite,
  changePassword,
  createInvite,
  createKey,
  distrustSite,
  listAllKeys,
  listInvites,
  listKeys,
  listTrustedSites,
  listUsers,
  missingDefaults,
  restoreDefaults,
  revokeKey,
  trustSite,
  updateUser,
} from "../api";
import { relativeTime } from "../format";
import { IntegrationsSetupTab, IntegrationsTab } from "./Integrations";
import { ServicesTab } from "./Services";
import { CopyButton } from "./CopyButton";
import { HealthTab } from "./Health";
import { LimitsTab } from "./Limits";
import { McpServersTab } from "./McpServers";
import { ModelsTab } from "./Models";
import { StatsTab } from "./Usage";

type Tab =
  | "account"
  | "keys"
  | "integrations"
  | "browser-sites"
  | "health"
  | "stats"
  | "models"
  | "limits"
  | "services"
  | "mcp"
  | "integrations-setup"
  | "users"
  | "invites"
  | "all-keys";

// The tabs' old addresses, from before integrations were called that
const RENAMED: Record<string, Tab> = { "connected-apps": "integrations", apps: "integrations-setup" };

// Your own settings, then the server's (admins only). Documents, memories and usage are pages of the sidebar.
const GROUPS: { label: string; admin: boolean; tabs: { id: Tab; label: string }[] }[] = [
  {
    label: "You",
    admin: false,
    tabs: [
      { id: "account", label: "Account" },
      { id: "keys", label: "API keys" },
      { id: "integrations", label: "Integrations" },
      { id: "browser-sites", label: "Trusted sites" },
    ],
  },
  {
    label: "Server",
    admin: true,
    tabs: [
      { id: "health", label: "Health" },
      { id: "stats", label: "Stats" },
      { id: "models", label: "Models" },
      { id: "limits", label: "Limits" },
      { id: "services", label: "Services" },
      { id: "mcp", label: "MCP servers" },
      { id: "integrations-setup", label: "Integrations" },
      { id: "users", label: "Users" },
      { id: "invites", label: "Invites" },
      { id: "all-keys", label: "All keys" },
    ],
  },
];

const when = (iso: string | null) => (iso ? relativeTime(iso) : "never");

/** Loads a list, and reloads it after changes */
function useList<T>(load: () => Promise<T[]>) {
  const [items, setItems] = useState<T[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    load().then(setItems, e => setError((e as Error).message));
  }, [load]);
  useEffect(reload, [reload]);
  return { items, error, setError, reload };
}

/** A secret shown once (a new key, an invite link), with a Copy button */
function Secret({ label, value, onDone }: { label: string; value: string; onDone: () => void }) {
  return (
    <div className="secret">
      <div>{label}</div>
      <code>{value}</code>
      <div className="secret-actions">
        <CopyButton getText={() => value} />
        <button type="button" onClick={onDone}>
          Done
        </button>
      </div>
    </div>
  );
}

/** Puts back the agents and skills every account starts with, the ones you deleted */
function DefaultsSection({ onRestored }: { onRestored: () => void }) {
  const [gone, setGone] = useState<MissingDefaults | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    missingDefaults().then(setGone, e => setMessage({ ok: false, text: (e as Error).message }));
  }, []);
  useEffect(load, [load]);

  const count = gone ? gone.agents.length + gone.skills.length : 0;
  const restore = async () => {
    setBusy(true);
    try {
      const added = await restoreDefaults();
      const [a, k] = [added.agents.length, added.skills.length];
      const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
      setMessage({ ok: true, text: a + k ? `Put back ${plural(a, "agent")} and ${plural(k, "skill")}.` : "Nothing was missing." });
      onRestored();
      load();
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="auth-form">
      <h3>Default agents and skills</h3>
      <p className="hint">
        Your account starts with ready-made agents (News Desk, Researcher, Shopping Scout, Trip Planner, Writing Editor) and skills they
        use. They're yours to change or delete. Restoring adds back the ones you deleted; the ones you kept stay as they are.
      </p>
      {gone && (
        <p className="hint">
          {count === 0
            ? `You have all ${gone.total.agents} agents and ${gone.total.skills} skills.`
            : `Missing: ${gone.agents.length} of ${gone.total.agents} agents, ${gone.skills.length} of ${gone.total.skills} skills.`}
        </p>
      )}
      {message && <div className={message.ok ? "ok-note" : "problem"}>{message.text}</div>}
      <button type="button" onClick={restore} disabled={busy || count === 0}>
        {busy ? "Restoring…" : "Restore defaults"}
      </button>
    </section>
  );
}

function AccountTab({ user, onLogOut, onDefaultsRestored }: { user: User; onLogOut: () => void; onDefaultsRestored: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await changePassword(current, next);
      setCurrent("");
      setNext("");
      setMessage({ ok: true, text: "Password changed. Your other browsers are logged out." });
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    }
  };

  return (
    <>
      <p>
        Logged in as <b>{user.username}</b> ({user.role}).
      </p>
      <form className="auth-form" onSubmit={submit}>
        <h3>Change password</h3>
        <label>
          Current password
          <input type="password" value={current} onChange={e => setCurrent(e.target.value)} autoComplete="current-password" required />
        </label>
        <label>
          New password
          <input type="password" value={next} onChange={e => setNext(e.target.value)} autoComplete="new-password" minLength={8} required />
        </label>
        {message && <div className={message.ok ? "ok-note" : "problem"}>{message.text}</div>}
        <button type="submit">Change password</button>
      </form>
      <DefaultsSection onRestored={onDefaultsRestored} />
      <button
        type="button"
        className="log-out"
        onClick={onLogOut}
      >
        Log out
      </button>
    </>
  );
}

function KeysTable({ keys, showOwner, onRevoke }: { keys: ApiKey[]; showOwner: boolean; onRevoke: (key: ApiKey) => void }) {
  if (!keys.length) return <p className="hint">No keys yet.</p>;
  return (
    <table className="settings-table">
      <thead>
        <tr>
          <th>Name</th>
          {showOwner && <th>User</th>}
          <th>Key</th>
          <th>Created</th>
          <th>Last used</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {keys.map(key => (
          <tr key={key.id} className={key.revoked_at ? "revoked" : undefined}>
            <td>{key.name}</td>
            {showOwner && <td>{key.username}</td>}
            <td>
              <code>{key.shown}…</code>
            </td>
            <td>{when(key.created_at)}</td>
            <td>{when(key.last_used_at)}</td>
            <td>
              {key.revoked_at ? (
                "revoked"
              ) : (
                <button type="button" className="danger" onClick={() => onRevoke(key)}>
                  Revoke
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function TrustedSitesTab() {
  const { items, error, setError, reload } = useList(listTrustedSites);
  const [site, setSite] = useState("");

  const add = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await trustSite(site);
      setSite("");
      setError(null);
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <>
      <p className="hint">
        The browser clicks and types on these sites and their subdomains without asking first, in all your chats. It still asks before
        anything that sends, pays or deletes, and passwords, cards and codes stay with you. Only add sites you'd let someone else click around in.
      </p>
      <form className="inline-form" onSubmit={add}>
        <input placeholder="A site, e.g. example.com" aria-label="Site" value={site} onChange={e => setSite(e.target.value)} required />
        <button type="submit" className="primary">Add</button>
      </form>
      {error && <div className="problem">{error}</div>}
      {items && (items.length === 0 ? <p className="hint">No trusted sites yet.</p> : (
        <table className="settings-table">
          <tbody>
            {items.map(trusted => (
              <tr key={trusted}>
                <td>{trusted}</td>
                <td>
                  <button type="button" className="danger" onClick={() => distrustSite(trusted).then(reload, e => setError((e as Error).message))}>
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ))}
    </>
  );
}

function KeysTab() {
  const { items, error, setError, reload } = useList(listKeys);
  const [name, setName] = useState("");
  const [created, setCreated] = useState<string | null>(null);

  const create = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const key = await createKey(name);
      setCreated(key.key);
      setName("");
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <>
      <p className="hint">
        Keys let labb and scripts use this server as you. On a PC, <code>labb --login</code> makes one for you; make one here for
        scripts, CI, or a PC without a browser (put it in <code>~/.labb/.env</code> as <code>LABB_API_KEY=…</code>).
      </p>
      {created ? (
        <Secret label="Your new key. Copy it now: it won't be shown again." value={created} onDone={() => setCreated(null)} />
      ) : (
        <form className="inline-form" onSubmit={create}>
          <input placeholder="Name, e.g. CI or my laptop" value={name} onChange={e => setName(e.target.value)} required />
          <button type="submit" className="primary">
            Create key
          </button>
        </form>
      )}
      {error && <div className="problem">{error}</div>}
      {items && (
        <KeysTable
          keys={items}
          showOwner={false}
          onRevoke={async key => {
            if (!confirm(`Revoke “${key.name}”? Whatever uses it stops working.`)) return;
            await revokeKey(key.id).catch(e => setError((e as Error).message));
            reload();
          }}
        />
      )}
    </>
  );
}

function UsersTab({ me }: { me: User }) {
  const { items, error, setError, reload } = useList(listUsers);

  const change = async (user: User, change: { role?: Role; disabled?: boolean }) => {
    try {
      await updateUser(user.id, change);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
    reload();
  };

  return (
    <>
      {error && <div className="problem">{error}</div>}
      <table className="settings-table">
        <thead>
          <tr>
            <th>User</th>
            <th>Role</th>
            <th>Joined</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items?.map(user => (
            <tr key={user.id} className={user.disabled ? "revoked" : undefined}>
              <td>
                {user.username}
                {user.id === me.id && " (you)"}
              </td>
              <td>
                <select value={user.role} onChange={e => change(user, { role: e.target.value as Role })} aria-label={`Role of ${user.username}`}>
                  <option value="member">member</option>
                  <option value="admin">admin</option>
                </select>
              </td>
              <td>{when(user.created_at)}</td>
              <td>
                <button type="button" className={user.disabled ? undefined : "danger"} onClick={() => change(user, { disabled: !user.disabled })}>
                  {user.disabled ? "Enable" : "Disable"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint">A disabled user can't log in, and their keys stop working until they're enabled again.</p>
    </>
  );
}

function InvitesTab() {
  const { items, error, setError, reload } = useList(listInvites);
  const [role, setRole] = useState<Role>("member");
  const [link, setLink] = useState<string | null>(null);

  const create = async () => {
    try {
      setLink((await createInvite(role)).url);
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <>
      <p className="hint">An invite link creates one account. It works once, for 7 days.</p>
      {link ? (
        <Secret label="Send this link to the person. Copy it now: it won't be shown again." value={link} onDone={() => setLink(null)} />
      ) : (
        <div className="inline-form">
          <select value={role} onChange={e => setRole(e.target.value as Role)} aria-label="Role">
            <option value="member">as a member</option>
            <option value="admin">as an admin</option>
          </select>
          <button type="button" className="primary" onClick={create}>
            Create invite link
          </button>
        </div>
      )}
      {error && <div className="problem">{error}</div>}
      {items && items.length > 0 && (
        <table className="settings-table">
          <thead>
            <tr>
              <th>Role</th>
              <th>Made by</th>
              <th>Expires</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((invite: Invite) => (
              <tr key={invite.id}>
                <td>{invite.role}</td>
                <td>{invite.created_by}</td>
                <td>{new Date(invite.expires_at).toLocaleDateString()}</td>
                <td>
                  <button type="button" className="danger" onClick={async () => { await cancelInvite(invite.id); reload(); }}>
                    Cancel
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function AllKeysTab() {
  const { items, error, setError, reload } = useList(listAllKeys);
  return (
    <>
      {error && <div className="problem">{error}</div>}
      {items && (
        <KeysTable
          keys={items}
          showOwner
          onRevoke={async key => {
            if (!confirm(`Revoke ${key.username}'s key “${key.name}”?`)) return;
            await revokeKey(key.id, true).catch(e => setError((e as Error).message));
            reload();
          }}
        />
      )}
    </>
  );
}

/** Your account, keys and connected apps; for admins also the server's stats, models, apps, users, invites and every key */
export function Settings({ user, browserOffered, route, onRoute, onClose, onLogOut, onDefaultsRestored }: {
  user: User;
  // Whether the server has a browser, so whether Trusted sites means anything
  browserOffered: boolean;
  // The address after /settings: the tab, then what's open in it (["stats", "users"])
  route: string[];
  onRoute: (route: string[]) => void;
  onClose: () => void;
  onLogOut: () => void;
  // After the default agents and skills were put back, so their lists load again
  onDefaultsRestored: () => void;
}) {
  const groups = GROUPS.filter(group => !group.admin || user.role === "admin");
  const shown = (t: { id: Tab }) => t.id !== "browser-sites" || browserOffered;
  const tabs = groups.flatMap(group => group.tabs).filter(shown);
  // A tab that isn't there (or isn't yours) shows the first
  const asked = RENAMED[route[0] ?? ""] ?? route[0];
  const tab = tabs.find(t => t.id === asked)?.id ?? "account";
  // On a phone, /settings is the list of sections and a section is a page of its own
  const atList = !route[0];
  const setTab = (next: Tab) => onRoute([next]);
  const label = tabs.find(t => t.id === tab)?.label;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className={`modal settings${atList ? " at-list" : ""}`} role="dialog" aria-label="Settings" onClick={e => e.stopPropagation()}>
        <nav className="settings-nav" aria-label="Settings">
          <div className="settings-title">
            <span>Settings</span>
            <button type="button" className="close" aria-label="Close" onClick={onClose}>×</button>
          </div>
          {groups.map(group => (
            <div key={group.label} className="settings-group" role="tablist" aria-label={group.label}>
              <div className="settings-group-name">{group.label}</div>
              {group.tabs.map(t => (
                <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}>
                  {t.label}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="settings-main">
          <div className="modal-head">
            <button type="button" className="settings-back" aria-label="All settings" onClick={() => onRoute([])}>←</button>
            <h2>{label}</h2>
            <button type="button" className="close" aria-label="Close" onClick={onClose}>
              ×
            </button>
          </div>
          <div className="modal-body">
            {tab === "account" && <AccountTab user={user} onLogOut={onLogOut} onDefaultsRestored={onDefaultsRestored} />}
            {tab === "keys" && <KeysTab />}
            {tab === "integrations" && <IntegrationsTab />}
            {tab === "browser-sites" && <TrustedSitesTab />}
            {tab === "stats" && <StatsTab section={route[1]} onSection={section => onRoute(["stats", section])} />}
            {tab === "health" && <HealthTab />}
            {tab === "models" && <ModelsTab />}
            {tab === "limits" && <LimitsTab />}
            {tab === "services" && <ServicesTab />}
            {tab === "mcp" && <McpServersTab />}
            {tab === "integrations-setup" && <IntegrationsSetupTab />}
            {tab === "users" && <UsersTab me={user} />}
            {tab === "invites" && <InvitesTab />}
            {tab === "all-keys" && <AllKeysTab />}
          </div>
        </div>
      </div>
    </div>
  );
}
