import { type FormEvent, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type App,
  type AppLabb,
  type AppSettings,
  type AppVersion,
  type AppDatabase,
  type AppUser,
  type QueryResult,
  type Build,
  type PageBuild,
  type Starter,
  addAppApi,
  addAppDatabase,
  appDataUrl,
  appSourceUrl,
  appDatabase,
  appDatabaseDownloadUrl,
  appDatabaseQuery,
  appDatabaseRows,
  appFiles,
  appLabb,
  appLogs,
  appSettings,
  appUsers,
  appVersionDiff,
  appVersions,
  buildApp,
  changeApp,
  createApp,
  deleteApp,
  deleteAppFile,
  getApp,
  lastBuild,
  pageBuild,
  listApps,
  listStarters,
  newAppLabbKey,
  openApp,
  readAppFile,
  removeAppApi,
  removeAppDatabase,
  removeAppUser,
  restoreAppVersion,
  searchAppFiles,
  saveAppSettings,
  setAppLabb,
  setAppSignups,
  setAppUserDisabled,
  writeAppFile,
} from "../api";
import { moment } from "../format";
import { Link, navigate, pathOf } from "../router";
import { AppChat } from "./AppChat";
import {
  ChatsIcon,
  ChevronDownIcon,
  ClockIcon,
  CopyIcon,
  DatabaseIcon,
  DownloadIcon,
  ExternalIcon,
  FileIcon,
  GearIcon,
  HammerIcon,
  MoreIcon,
  PlayIcon,
  RegenerateIcon,
  StopIcon,
  TerminalIcon,
  TrashIcon,
  UsersIcon,
} from "./Icons";
import { toast } from "./Toaster";
import { FileEditor, type FileStore } from "./FileEditor";

const LIST_MS = 5000;
// How often an open app's state and, when shown, its logs are looked at
const STATUS_MS = 4000;
const LOGS_MS = 3000;

const isUp = (app: App) => app.status === "running" || app.status === "restarting";

/** The dot and word for an app's state */
function State({ app }: { app: App }) {
  return (
    <>
      <span className={app.status === "running" ? "app-dot on" : app.status === "restarting" ? "app-dot bad" : "app-dot"} />{" "}
      {app.status === "exited" ? "stopped" : app.status}
    </>
  );
}

// In 1024s, as Docker states memory ("256m") and the spawner the data limit
const megabytes = (bytes: number) =>
  bytes < 1024 * 1024 ? `${Math.max(bytes ? 1 : 0, Math.round(bytes / 1024))} KB` : `${Math.round(bytes / (1024 * 1024))} MB`;
/** Docker's memory limit as people write it: "256m" → "256 MB" */
const limit = (value: string) => value.replace(/^(\d+)([kmg])b?$/i, (_, n, unit) => `${n} ${unit.toUpperCase()}B`);

/** Your apps, as tiles, and the button to start a new one. An app is a container of its own. */
export function AppsTab() {
  const [apps, setApps] = useState<App[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [starters, setStarters] = useState<Starter[]>([]);
  const [starter, setStarter] = useState("react");
  // What's been picked for the starter's questions, by name; the rest are their defaults
  const [picks, setPicks] = useState<Record<string, string>>({});
  const [starting, setStarting] = useState(false);

  const reload = useCallback(() => {
    listApps().then(found => (setApps(found), setError(null)), e => setError((e as Error).message));
  }, []);
  useEffect(() => {
    reload();
    const timer = setInterval(reload, LIST_MS);
    return () => clearInterval(timer);
  }, [reload]);
  useEffect(() => {
    if (naming && starters.length === 0) listStarters().then(setStarters, e => toast((e as Error).message));
  }, [naming, starters.length]);

  const choices = starters.find(s => s.name === starter)?.choices ?? [];
  const chosen = Object.fromEntries(
    choices.map(c => [c.name, picks[c.name] ?? (c.options.find(o => o.default) ?? c.options[0]).name]),
  );

  const start = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim() || starting) return;
    setStarting(true);
    try {
      const app = await createApp(name.trim(), starter, chosen);
      setName("");
      setNaming(false);
      navigate(pathOf("apps", app.id));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setStarting(false);
    }
  };

  return (
    <>
      <div className="agents-intro">
        <p className="hint">Each app runs in a container of its own, built from its own files. Open one to use it here.</p>
        {!naming && (
          <button type="button" className="primary" onClick={() => setNaming(true)}>
            New app
          </button>
        )}
      </div>
      {naming && (
        <form className="app-new" onSubmit={start}>
          <div className="app-new-row">
            <input
              autoFocus
              value={name}
              maxLength={60}
              placeholder="Name"
              aria-label="The new app's name"
              onChange={event => setName(event.target.value)}
              onKeyDown={event => event.key === "Escape" && !starting && setNaming(false)}
              disabled={starting}
            />
            <button type="submit" className="primary" disabled={!name.trim() || starting}>
              {starting ? "Building…" : "Create"}
            </button>
            <button type="button" onClick={() => setNaming(false)} disabled={starting}>
              Cancel
            </button>
          </div>
          <div className="app-starters" role="radiogroup" aria-label="Start from">
            {starters.map(s => (
              <label key={s.name} className={s.name === starter ? "app-starter on" : "app-starter"}>
                <input type="radio" name="starter" value={s.name} checked={s.name === starter} onChange={() => setStarter(s.name)} disabled={starting} />
                <b>{s.title}</b>
                <span className="muted">{s.description}</span>
              </label>
            ))}
          </div>
          {choices.map(choice => (
            <fieldset key={choice.name} className="app-choice">
              <legend>{choice.title}</legend>
              <div className="app-starters" role="radiogroup" aria-label={choice.title}>
                {choice.options.map(option => (
                  <label key={option.name} className={chosen[choice.name] === option.name ? "app-starter on" : "app-starter"}>
                    <input
                      type="radio"
                      name={choice.name}
                      value={option.name}
                      checked={chosen[choice.name] === option.name}
                      onChange={() => setPicks({ ...picks, [choice.name]: option.name })}
                      disabled={starting}
                    />
                    <b>
                      {option.swatch && <span className="app-swatch" style={{ background: option.swatch }} aria-hidden />}
                      {option.title}
                    </b>
                    <span className="muted">{option.description}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          ))}
          {starting && <p className="hint">Building it from the starter: the first build of one downloads its base images and the page's packages, so it can take a minute.</p>}
        </form>
      )}
      {error && <div className="problem">{error}</div>}
      {apps && apps.length === 0 && !naming && <p className="hint">No apps yet.</p>}
      <div className="app-tiles">
        {apps?.map(app => (
          <Link key={app.id} to={pathOf("apps", app.id)} className="app-tile">
            <b>{app.name}</b>
            <span className="muted">
              <State app={app} /> · :{app.port}
            </span>
            {app.detail && <span className="muted app-tile-detail">{app.detail}</span>}
            <span className="muted">Made {moment(app.created)}</span>
          </Link>
        ))}
      </div>
    </>
  );
}

/** Whether this browser reaches the app's port: a firewall in the way, or a certificate it doesn't trust, fails here */
async function reaches(origin: string) {
  for (let tries = 0; tries < 3; tries++) {
    try {
      // Opaque, but it only resolves once something answered there
      await fetch(`${origin}/`, { mode: "no-cors", cache: "no-store" });
      return true;
    } catch {
      await new Promise(done => setTimeout(done, 400));
    }
  }
  return false;
}

type Opening =
  | { state: "opening" }
  | { state: "open"; url: string; origin: string }
  | { state: "unreachable"; origin: string }
  | { state: "not-answering"; url: string; origin: string };

/** The end of an app's output, kept up to date while it's shown */
function Logs({ id }: { id: string }) {
  const [lines, setLines] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let gone = false;
    const load = () =>
      appLogs(id).then(
        found => !gone && (setLines(found.lines), setError(null)),
        e => !gone && setError((e as Error).message),
      );
    load();
    const timer = setInterval(load, LOGS_MS);
    return () => {
      gone = true;
      clearInterval(timer);
    };
  }, [id]);

  return <Output text={lines === null ? "Loading…" : lines || "Nothing yet."} error={error} label="The app's logs" />;
}

/** Text that follows its end, unless it was scrolled up to read */
function Output({ text, error, label }: { text: string; error?: string | null; label: string }) {
  const box = useRef<HTMLPreElement>(null);
  useEffect(() => {
    const el = box.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 60) el.scrollTop = el.scrollHeight;
  }, [text]);
  return (
    <>
      {error && <div className="problem">{error}</div>}
      <pre ref={box} className="app-output" aria-label={label}>
        {text}
      </pre>
    </>
  );
}

/** The app's files, in the editor artifacts share. Changes show after a rebuild. */
function Files({ app, onChanged }: { app: App; onChanged: (path: string) => void }) {
  const store = useMemo<FileStore>(
    () => ({
      list: () => appFiles(app.id),
      read: path => readAppFile(app.id, path).then(file => file.content),
      write: (path, content) => writeAppFile(app.id, path, content),
      remove: path => deleteAppFile(app.id, path),
      search: (query, options, signal) => searchAppFiles(app.id, query, options, signal),
    }),
    [app.id],
  );
  if (app.legacy) {
    return <p className="hint app-message">This app was made before apps had files of their own. Make a new one to change what it does.</p>;
  }
  return (
    <FileEditor
      store={store}
      what="the app's"
      hint="Pick a file to read or change it. Rebuild to see changes in the app."
      keep={path => (path === "Dockerfile" ? "An app needs its Dockerfile" : null)}
      onChanged={onChanged}
      footer={
        <a className="app-download-source" href={appSourceUrl(app.id)} download title="The files as a zip, with their history as a .git folder: unzip it and use git as usual">
          <DownloadIcon /> Download as zip (with git)
        </a>
      }
    />
  );
}

/** The last build's result and log, and its page's last build (made by itself after each change) */
function BuildOutput({ build, building, page }: { build: Build | null; building: boolean; page: PageBuild | null }) {
  const pageLine = page?.page && page.ok !== null && (
    <div className={page.ok ? "hint app-build-result" : "problem app-build-result"}>
      {page.pending
        ? "The page is being built…"
        : page.ok
          ? `The page (frontend/) builds by itself after each change: its build ${page.build} is live`
          : `The page's build ${page.build} failed, so the one before is still live: ${page.error}`}
      {page.at && !page.pending ? ` · ${moment(page.at)}` : ""}
    </div>
  );
  if (building) return <Output text={"Building…\n" + (build?.log ?? "")} label="The build's log" />;
  if (!build || build.ok === null) return <>{pageLine}<p className="hint app-message">No build yet.</p></>;
  return (
    <>
      {pageLine}
      {page?.page && page.ok === false && !page.pending && <Output text={page.log || "No log."} label="The page's build log" />}
      <div className={build.ok ? "hint app-build-result" : "problem app-build-result"}>
        Build {build.build} {build.ok ? "worked and runs now" : `failed: ${build.error}. The last build that worked still runs.`}
        {build.at ? ` · ${moment(build.at)}` : ""}
      </div>
      <Output text={build.log || "No log."} label="The build's log" />
    </>
  );
}

/** A unified diff, each line coloured by what it is */
function Diff({ text }: { text: string }) {
  if (!text.trim()) return <p className="hint app-message">No changes.</p>;
  return (
    <pre className="app-output app-diff" aria-label="What changed">
      {text.split("\n").map((line, i) => {
        const kind = line.startsWith("diff --git") || line.startsWith("+++") || line.startsWith("---") || line.startsWith("index ")
          ? "head"
          : line.startsWith("@@")
            ? "hunk"
            : line.startsWith("+")
              ? "add"
              : line.startsWith("-")
                ? "del"
                : "";
        return (
          <span key={i} className={kind ? `diff-${kind}` : undefined}>
            {line}
            {"\n"}
          </span>
        );
      })}
    </pre>
  );
}

/** Its versions (one at each build): what each changed, and going back to one */
function History({ app, onRestored }: { app: App; onRestored: () => void }) {
  const [found, setFound] = useState<{ versions: AppVersion[]; unsaved: string[] } | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => appVersions(app.id).then(setFound, e => toast((e as Error).message)), [app.id]);
  // Again after each build, which saves a version
  useEffect(() => {
    load();
  }, [load, app.build]);
  useEffect(() => {
    if (!found || picked) return;
    setPicked(found.unsaved.length ? "unsaved" : (found.versions[0]?.id ?? null));
  }, [found, picked]);
  useEffect(() => {
    setDiff(null);
    if (picked) appVersionDiff(app.id, picked).then(d => setDiff(d.diff), e => toast((e as Error).message));
  }, [app.id, picked]);

  const restore = async (version: AppVersion) => {
    if (!window.confirm(`Put ${app.name}'s files back as they were in "${version.message}"? Changes not built yet are kept in a version first. Rebuild afterwards to run them.`)) return;
    setBusy(true);
    try {
      const after = await restoreAppVersion(app.id, version.id);
      setFound(after);
      setPicked(after.versions[0]?.id ?? null);
      onRestored();
      toast("The files are back as they were: Rebuild to run them");
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (app.legacy) return <p className="hint app-message">This app was made before apps had files of their own, so it has no history.</p>;
  if (!found) return <p className="hint app-message">Loading…</p>;
  const version = found.versions.find(v => v.id === picked);
  return (
    <div className="app-files">
      <div className="app-file-list app-versions">
        <ul>
          {found.unsaved.length > 0 && (
            <li>
              <button type="button" className={picked === "unsaved" ? "on" : undefined} onClick={() => setPicked("unsaved")}>
                <b>Changes not built yet</b>
                <span className="muted">{found.unsaved.length} file{found.unsaved.length === 1 ? "" : "s"}</span>
              </button>
            </li>
          )}
          {found.versions.map(v => (
            <li key={v.id}>
              <button type="button" className={picked === v.id ? "on" : undefined} onClick={() => setPicked(v.id)} title={v.files.join("\n")}>
                <b>{v.message}</b>
                <span className="muted">
                  {moment(v.at)} · {v.files.length} file{v.files.length === 1 ? "" : "s"} · {v.id.slice(0, 8)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="app-file">
        {version && (
          <div className="app-file-head">
            <span>
              <b>{version.message}</b> <span className="muted">{version.files.join(", ")}</span>
            </span>
            <span className="app-bar-gap" />
            <button type="button" onClick={() => restore(version)} disabled={busy} title="Put the files back as they were in this version">
              {busy ? "Restoring…" : "Restore this version"}
            </button>
          </div>
        )}
        {picked === "unsaved" && (
          <div className="app-file-head">
            <span>
              <b>Changes not built yet</b> <span className="muted">{found.unsaved.join(", ")}: Rebuild saves them as a version</span>
            </span>
          </div>
        )}
        {diff === null ? <p className="hint app-message">Loading…</p> : <Diff text={diff} />}
      </div>
    </div>
  );
}

type Row = { key: number; name: string; value: string; secret: boolean; kept: boolean };

/** What the app may use of labb, as its owner: a key of its own, in its environment */
function UseLabb({ app, onChanged }: { app: App; onChanged: () => void }) {
  const [state, setState] = useState<AppLabb | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    appLabb(app.id).then(found => (setState(found), setChosen(found.scopes)), e => toast((e as Error).message));
  }, [app.id]);

  const apply = async (change: () => Promise<AppLabb>, said: string) => {
    setSaving(true);
    try {
      const found = await change();
      setState(found);
      setChosen(found.scopes);
      toast(said);
      onChanged();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  if (!state) return null;
  const changed = chosen.length !== state.scopes.length || chosen.some(s => !state.scopes.includes(s));
  const save = () =>
    apply(
      () => setAppLabb(app.id, chosen),
      chosen.length === 0 ? "It can't use labb any more: its key is gone" : state.key ? "Saved" : `${app.name} got a key, and started again with it`,
    );
  return (
    <section>
      <h4>Use labb</h4>
      <p className="hint">
        Let the app use labb as you: its server gets <code>LABB_URL</code> and <code>LABB_API_KEY</code>, and <code>labb.py</code> /{" "}
        <code>labb.js</code> call it. Only from its server: anyone who sees the key can use what it's given.
      </p>
      <div className="app-scopes">
        {state.available.map(s => (
          <label key={s.name}>
            <input
              type="checkbox"
              checked={chosen.includes(s.name)}
              onChange={e => setChosen(all => (e.target.checked ? [...all, s.name] : all.filter(x => x !== s.name)))}
            />{" "}
            {s.description}
          </label>
        ))}
      </div>
      <div className="app-settings-actions">
        <button type="button" className="primary" onClick={save} disabled={!changed || saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        {state.key && (
          <>
            <span className="muted">
              Key <code>{state.key.shown}…</code> · {state.key.last_used_at ? `used ${moment(state.key.last_used_at)}` : "not used yet"}
            </span>
            <button
              type="button"
              disabled={saving}
              onClick={() =>
                window.confirm(`A new key for ${app.name}? The old one stops working now, and the app starts again with the new one.`) &&
                apply(() => newAppLabbKey(app.id), "New key given; the old one stopped working")
              }
            >
              New key
            </button>
          </>
        )}
      </div>
    </section>
  );
}

/** Its environment variables and secrets, and the data it keeps in /data */
function SettingsPanel({ app, onSaved }: { app: App; onSaved: (app: App) => void }) {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [changed, setChanged] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const next = useRef(0);

  const show = useCallback((found: AppSettings) => {
    setSettings(found);
    // A kept secret shows empty: leaving it so keeps its value
    setRows(found.variables.map(v => ({ key: next.current++, name: v.name, value: v.value ?? "", secret: v.secret, kept: v.secret })));
    setChanged(false);
  }, []);
  useEffect(() => {
    appSettings(app.id).then(show, e => setError((e as Error).message));
  }, [app.id, show]);

  const change = (key: number, update: Partial<Row>) => {
    setRows(all => all.map(r => (r.key === key ? { ...r, ...update } : r)));
    setChanged(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      const saved = await saveAppSettings(
        app.id,
        rows
          .filter(r => r.name.trim())
          .map(r => ({ name: r.name.trim(), secret: r.secret, value: r.secret && r.kept && r.value === "" ? null : r.value })),
      );
      show(saved);
      onSaved(saved.app);
      toast(`Saved: ${app.name} started again with them`);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  if (app.legacy) return <p className="hint app-message">This app was made before apps had settings. Make a new one to use them.</p>;
  if (error) return <div className="problem app-message">{error}</div>;
  if (!settings) return <p className="hint app-message">Loading…</p>;
  const used = settings.data.size / settings.data.limit;
  return (
    <div className="app-settings">
      <section>
        <h4>Environment variables</h4>
        <p className="hint">
          Given to the app when it starts, as its environment. A secret's value is kept encrypted and never shown again: leave it
          empty to keep it. Saving starts the app again with them.
        </p>
        <table className="app-variables">
          <tbody>
            {rows.map(r => (
              <tr key={r.key}>
                <td>
                  <input value={r.name} placeholder="NAME" aria-label="Variable name" spellCheck={false} onChange={e => change(r.key, { name: e.target.value.replace(/[^A-Za-z0-9_]/g, "_") })} />
                </td>
                <td>
                  <input
                    type={r.secret ? "password" : "text"}
                    value={r.value}
                    placeholder={r.secret && r.kept ? "•••••• (kept)" : "value"}
                    aria-label={`${r.name || "Variable"}'s value`}
                    autoComplete="off"
                    onChange={e => change(r.key, { value: e.target.value })}
                  />
                </td>
                <td>
                  <label className="app-secret">
                    <input type="checkbox" checked={r.secret} onChange={e => change(r.key, { secret: e.target.checked, kept: false })} /> Secret
                  </label>
                </td>
                <td>
                  <button type="button" className="link" aria-label={`Remove ${r.name || "this variable"}`} onClick={() => (setRows(all => all.filter(x => x.key !== r.key)), setChanged(true))}>
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="app-settings-actions">
          <button type="button" onClick={() => (setRows(all => [...all, { key: next.current++, name: "", value: "", secret: false, kept: false }]), setChanged(true))}>
            Add a variable
          </button>
          <button type="button" className="primary" onClick={save} disabled={!changed || saving}>
            {saving ? "Saving…" : "Save and restart"}
          </button>
        </div>
      </section>
      <div className="app-settings-side">
      <UseLabb app={app} onChanged={() => getApp(app.id).then(onSaved, () => {})} />
      <section>
        <h4>Data</h4>
        <p className="hint">
          What the app keeps in <code>/data</code>: the one place it can write that stays after a restart or a rebuild. It goes when
          the app is removed.
        </p>
        <div className="app-data">
          <div className="app-meter" role="meter" aria-valuenow={settings.data.size} aria-valuemin={0} aria-valuemax={settings.data.limit} aria-label="Data used">
            <span style={{ width: `${Math.min(100, used * 100)}%` }} className={used > 0.9 ? "bad" : undefined} />
          </div>
          <span className="muted">
            {megabytes(settings.data.size)} of {megabytes(settings.data.limit)}
          </span>
          <a className="button" href={appDataUrl(app.id)} download>
            Download (.tar)
          </a>
        </div>
      </section>
      </div>
    </div>
  );
}

/** A table of rows, as a query or a table page gives them */
function ResultTable({ result }: { result: QueryResult }) {
  if (!result.columns.length) return <p className="hint app-message">Done{result.rowcount !== undefined && result.rowcount >= 0 ? ` (${result.rowcount} rows)` : ""}.</p>;
  const shown = (value: unknown) =>
    value === null ? <span className="muted">NULL</span> : typeof value === "object" ? JSON.stringify(value) : String(value);
  return (
    <div className="app-result">
      <table>
        <thead>
          <tr>
            {result.columns.map((c, i) => (
              <th key={i}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, i) => (
            <tr key={i}>
              {row.map((value, j) => (
                <td key={j} title={typeof value === "string" ? value : undefined}>
                  {shown(value)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {result.rows.length === 0 && <p className="hint app-message">No rows.</p>}
    </div>
  );
}

/** Its database: its tables, rows page by page, SQL as the app, migrations; adding or removing it */
/** Its own users (who signed up on its API): disable or remove them, and whether new ones may sign up */
function UsersView({ app, db, onDb }: { app: App; db: Extract<AppDatabase, { enabled: true }>; onDb: (db: AppDatabase) => void }) {
  const [found, setFound] = useState<{ users: AppUser[]; total: number } | null>(null);
  const [offset, setOffset] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // While its API starts again with a new setting, the box shows what was asked
  const [asked, setAsked] = useState<boolean | null>(null);

  const load = useCallback(
    () =>
      appUsers(app.id, offset).then(
        got => (setFound(got), setProblem(null)),
        e => setProblem((e as Error).message),
      ),
    [app.id, offset],
  );
  useEffect(() => {
    load();
  }, [load]);

  const act = async (work: () => Promise<unknown>, said: string) => {
    setBusy(true);
    try {
      await work();
      toast(said);
      await load();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const signups = async (on: boolean) => {
    setBusy(true);
    setAsked(on);
    try {
      onDb(await setAppSignups(app.id, on));
      toast(on ? "New users can sign up" : "Sign-ups are closed: only the users there are can sign in");
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setAsked(null);
      setBusy(false);
    }
  };

  const remove = (u: AppUser) => {
    if (!window.confirm(`Remove ${u.email} from ${app.name}? Their sign-ins end, and their rows go where its tables say ON DELETE CASCADE. It can't be undone.`)) return;
    act(() => removeAppUser(app.id, u.id), `${u.email} was removed`);
  };

  return (
    <>
      <div className="app-file-head">
        <span className="app-file-path">
          <b>Users</b> <span className="muted">{found ? found.total.toLocaleString() : ""}</span>
        </span>
        <span className="app-bar-gap" />
        <label className="app-signups" title="Off: only the users there are can sign in">
          <input type="checkbox" checked={asked ?? !!db.api.signups} disabled={busy} onChange={e => signups(e.target.checked)} />
          {asked === null ? "New users can sign up" : "Saving…"}
        </label>
        {found && found.total > 100 && (
          <>
            <button type="button" disabled={offset === 0} onClick={() => setOffset(o => Math.max(0, o - 100))}>
              Previous
            </button>
            <button type="button" disabled={offset + 100 >= found.total} onClick={() => setOffset(o => o + 100)}>
              Next
            </button>
          </>
        )}
      </div>
      {problem && <div className="problem app-message">{problem}</div>}
      {found && found.users.length === 0 && (
        <p className="hint app-message">
          No one has signed up yet. Its pages sign people up with its API: <code>api.signUp(email, password)</code> from{" "}
          <code>{db.api.origin ?? "its API"}/client.js</code>. Ask the chat for sign-in, with each user seeing only their own rows.
        </p>
      )}
      {found && found.users.length > 0 && (
        <div className="app-result">
          <table className="app-users">
            <thead>
              <tr>
                <th>Email</th>
                <th>Signed up</th>
                <th>Last signed in</th>
                <th>Signed in on</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {found.users.map(u => (
                <tr key={u.id} className={u.disabled ? "off" : undefined}>
                  <td title={u.id}>
                    {u.email} {u.disabled && <span className="app-chip">disabled</span>}
                  </td>
                  <td>{moment(u.created_at)}</td>
                  <td>{u.last_sign_in_at ? moment(u.last_sign_in_at) : <span className="muted">never</span>}</td>
                  <td>{u.sessions === 0 ? <span className="muted">nothing</span> : `${u.sessions} device${u.sessions === 1 ? "" : "s"}`}</td>
                  <td className="app-user-actions">
                    <button
                      type="button"
                      disabled={busy}
                      title={u.disabled ? "They can sign in again" : "They're signed out everywhere, at once, and can't sign in"}
                      onClick={() => act(() => setAppUserDisabled(app.id, u.id, !u.disabled), u.disabled ? `${u.email} can sign in again` : `${u.email} is disabled and signed out`)}
                    >
                      {u.disabled ? "Enable" : "Disable"}
                    </button>
                    <button type="button" className="danger" disabled={busy} aria-label={`Remove ${u.email}`} onClick={() => remove(u)}>
                      Remove…
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!found && !problem && <p className="hint app-message">Loading…</p>}
    </>
  );
}

function DatabasePanel({ app, onChanged }: { app: App; onChanged: (app: App) => void }) {
  const [db, setDb] = useState<AppDatabase | null>(null);
  const [picked, setPicked] = useState<{ schema: string; name: string } | "sql" | "users" | null>(null);
  const [page, setPage] = useState<QueryResult | null>(null);
  const [offset, setOffset] = useState(0);
  const [sql, setSql] = useState("SELECT * FROM ");
  const [write, setWrite] = useState(false);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => appDatabase(app.id).then(setDb, e => setProblem((e as Error).message)), [app.id]);
  // Again after each build, which applies its migrations
  useEffect(() => {
    load();
  }, [load, app.build]);
  useEffect(() => {
    if (!db?.enabled || picked) return;
    const first = db.tables.find(t => t.name !== "labb_migrations");
    setPicked(first ? { schema: first.schema, name: first.name } : "sql");
  }, [db, picked]);
  useEffect(() => {
    setPage(null);
    setProblem(null);
    if (!picked || picked === "sql" || picked === "users") return;
    appDatabaseRows(app.id, picked.schema, picked.name, offset).then(setPage, e => setProblem((e as Error).message));
  }, [app.id, picked, offset]);

  const add = async () => {
    setBusy(true);
    try {
      const made = await addAppDatabase(app.id);
      setDb(made);
      onChanged(made.app);
      toast(made.migrated.ok ? `${app.name} has a database, and started again with DATABASE_URL` : `Made, but a migration failed: ${made.migrated.error}`);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    const typed = window.prompt(`Remove ${app.name}'s database and everything in it? It can't be undone (download it first). Type the app's name to remove it:`);
    if (typed === null) return;
    if (typed.trim() !== app.name) return toast("The name didn't match: nothing was removed");
    setBusy(true);
    try {
      onChanged((await removeAppDatabase(app.id)).app);
      setPicked(null);
      await load();
      toast("Its database is gone");
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggleApi = async (on: boolean) => {
    if (!on && !window.confirm(`Turn off ${app.name}'s API? Pages that use it stop working until it's on again.`)) return;
    setBusy(true);
    try {
      setDb(await (on ? addAppApi(app.id) : removeAppApi(app.id)));
      if (!on && picked === "users") setPicked(null);
      toast(on ? "Its API is on" : "Its API is off");
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const run = async () => {
    setBusy(true);
    setProblem(null);
    setResult(null);
    try {
      setResult(await appDatabaseQuery(app.id, sql, write));
      if (write) load();
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (app.legacy) return <p className="hint app-message">This app was made before apps had files of their own, so it can't have a database.</p>;
  if (!db) return <p className="hint app-message">{problem ?? "Loading…"}</p>;
  if (!db.enabled) {
    return (
      <div className="app-db-empty">
        {db.available ? (
          <>
            <p>
              <b>Give {app.name} a database of its own:</b> Postgres, on this server, only for this app. It gets <code>DATABASE_URL</code>{" "}
              in its environment, and its tables come from migrations, <code>db/migrations/0001_….sql</code>, applied at each build.
              The chat beside it can write them.
            </p>
            <button type="button" className="primary icon-text" onClick={add} disabled={busy}>
              <DatabaseIcon /> {busy ? "Adding…" : "Add a database"}
            </button>
          </>
        ) : (
          <p className="hint">This server has no databases for apps.</p>
        )}
      </div>
    );
  }
  const used = db.size / db.limit;
  const tables = db.tables.filter(t => t.name !== "labb_migrations");
  return (
    <div className="app-files">
      <div className="app-file-list app-db-side">
        <ul>
          {tables.map(t => (
            <li key={`${t.schema}.${t.name}`}>
              <button
                type="button"
                className={typeof picked === "object" && picked?.name === t.name && picked.schema === t.schema ? "on" : undefined}
                onClick={() => (setOffset(0), setPicked({ schema: t.schema, name: t.name }))}
                title={t.columns.map(c => `${c.name} ${c.type}`).join("\n")}
              >
                <DatabaseIcon /> {t.schema === "public" ? t.name : `${t.schema}.${t.name}`}
                <span className="muted app-db-count">{t.kind === "view" ? "view" : `${t.rows.toLocaleString()}${t.rows_more ? "+" : ""}`}</span>
              </button>
            </li>
          ))}
          {tables.length === 0 && <li className="hint app-db-note">No tables yet: they come from migrations.</li>}
          {db.api.enabled && (
            <li>
              <button type="button" className={picked === "users" ? "on" : undefined} onClick={() => setPicked("users")} title="Who signed up on its API">
                <UsersIcon /> Users
              </button>
            </li>
          )}
          <li>
            <button type="button" className={picked === "sql" ? "on" : undefined} onClick={() => setPicked("sql")}>
              <TerminalIcon /> SQL
            </button>
          </li>
        </ul>
        <div className="app-db-meta">
          <div className="app-meter" role="meter" aria-valuenow={db.size} aria-valuemin={0} aria-valuemax={db.limit} aria-label="Database size">
            <span style={{ width: `${Math.min(100, used * 100)}%` }} className={used > 0.9 ? "bad" : undefined} />
          </div>
          <span className="muted">
            {megabytes(db.size)} of {megabytes(db.limit)}
          </span>
          <b>Migrations</b>
          {db.migrations.map(m => (
            <span key={m.name} className="muted" title={`Applied ${m.applied_at}`}>
              ✓ {m.name}
            </span>
          ))}
          {db.pending.map(name => (
            <span key={name} title="Applied at the next build">
              ○ {name} <span className="muted">(next build)</span>
            </span>
          ))}
          {db.migrations.length + db.pending.length === 0 && <span className="muted">None yet</span>}
          <b>API</b>
          {db.api.enabled ? (
            <>
              {db.api.url && (
                <a className="app-api-url" href={db.api.url} target="_blank" rel="noreferrer" title="Its tables, as the API describes them">
                  {db.api.url.replace(/^https?:\/\//, "")}
                </a>
              )}
              <span className="muted">
                Only what its migrations grant: <code>anon</code> for anyone, <code>authenticated</code> for its signed-in users, with
                row-level security for whose rows.
              </span>
              <span className="muted">Sign-in for its own users: /auth/, and its pages use {"/client.js"}.</span>
              <button type="button" className="link" onClick={() => toggleApi(false)} disabled={busy}>
                Turn it off
              </button>
            </>
          ) : (
            <>
              <span className="muted">Its tables for its pages, with no server code, at its port + 100.</span>
              <button type="button" onClick={() => toggleApi(true)} disabled={busy}>
                Turn on its API
              </button>
            </>
          )}
          <div className="app-db-actions">
            <a className="button" href={appDatabaseDownloadUrl(app.id)} download>
              Download (.sql)
            </a>
            <button type="button" className="danger" onClick={remove} disabled={busy}>
              Remove…
            </button>
          </div>
        </div>
      </div>
      <div className="app-file">
        {picked === "users" ? (
          <UsersView app={app} db={db} onDb={setDb} />
        ) : picked === "sql" ? (
          <>
            <div className="app-sql">
              <textarea
                value={sql}
                spellCheck={false}
                aria-label="SQL to run"
                onChange={e => setSql(e.target.value)}
                onKeyDown={e => {
                  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                    e.preventDefault();
                    run();
                  }
                }}
              />
              <div className="app-sql-actions">
                <label className={write ? "app-write on" : "app-write"} title="Off: one statement, read-only, as a role that can't change anything">
                  <input type="checkbox" checked={write} onChange={e => setWrite(e.target.checked)} /> Allow changes
                </label>
                <button type="button" className={write ? "danger" : "primary"} onClick={run} disabled={busy || !sql.trim()} title="Run (Ctrl/Cmd-Enter)">
                  {busy ? "Running…" : write ? "Run, changing it" : "Run"}
                </button>
              </div>
            </div>
            {problem && <div className="problem app-message">{problem}</div>}
            {result && <ResultTable result={result} />}
          </>
        ) : (
          <>
            {picked && (
              <div className="app-file-head">
                <span className="app-file-path">
                  <b>{picked.name}</b>
                </span>
                <span className="app-bar-gap" />
                <button type="button" disabled={offset === 0} onClick={() => setOffset(o => Math.max(0, o - 100))}>
                  Previous
                </button>
                <span className="muted">rows {offset + 1}–{offset + (page?.rows.length ?? 0)}</span>
                <button type="button" disabled={!page?.more} onClick={() => setOffset(o => o + 100)}>
                  Next
                </button>
              </div>
            )}
            {problem && <div className="problem app-message">{problem}</div>}
            {page ? <ResultTable result={page} /> : !problem && <p className="hint app-message">Loading…</p>}
          </>
        )}
      </div>
    </div>
  );
}

type Panel = "files" | "history" | "database" | "build" | "logs" | "settings";

const PANELS: { id: Panel; label: string; icon: ReactNode }[] = [
  { id: "files", label: "Files", icon: <FileIcon /> },
  { id: "history", label: "History", icon: <ClockIcon /> },
  { id: "database", label: "Database", icon: <DatabaseIcon /> },
  { id: "build", label: "Build", icon: <HammerIcon /> },
  { id: "logs", label: "Logs", icon: <TerminalIcon /> },
  { id: "settings", label: "Settings", icon: <GearIcon /> },
];

/** A setting of the page kept in this browser (localStorage), or its default */
function stored<T>(key: string, fallback: T): T {
  try {
    const found = localStorage.getItem(key);
    return found === null ? fallback : (JSON.parse(found) as T);
  } catch {
    return fallback;
  }
}

function store(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage can be unavailable
  }
}

/**
 * A handle to drag, to make something bigger or smaller: `onMove` gets how far it moved since the
 * drag started. Frames don't take the pointer while dragging, so it isn't lost over one.
 */
function Resizer({ axis, onStart, onMove, onEnd, label }: {
  axis: "x" | "y";
  onStart: () => void;
  onMove: (delta: number) => void;
  onEnd: () => void;
  label: string;
}) {
  return (
    <div
      className={`app-resizer ${axis}`}
      role="separator"
      aria-orientation={axis === "x" ? "vertical" : "horizontal"}
      aria-label={label}
      title={label}
      onPointerDown={event => {
        event.preventDefault();
        const from = axis === "x" ? event.clientX : event.clientY;
        onStart();
        document.body.classList.add(axis === "x" ? "resizing-x" : "resizing-y");
        const move = (e: PointerEvent) => onMove((axis === "x" ? e.clientX : e.clientY) - from);
        const up = () => {
          document.body.classList.remove("resizing-x", "resizing-y");
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", up);
          onEnd();
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
      }}
    />
  );
}

type MenuItem = { label: string; icon: ReactNode; onClick: () => void; danger?: boolean; disabled?: boolean } | "line";

/** The ⋯ button and its menu: closes on a pick, a click elsewhere or Escape */
function MoreMenu({ items, label }: { items: MenuItem[]; label: string }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    const escape = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  return (
    <div className="app-more" ref={box}>
      <button type="button" className="icon-button bordered" aria-label={label} title={label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        <MoreIcon />
      </button>
      {open && (
        <div className="menu" role="menu">
          {items.map((item, i) =>
            item === "line" ? (
              <hr key={i} />
            ) : (
              <button
                key={item.label}
                type="button"
                role="menuitem"
                className={item.danger ? "danger" : undefined}
                disabled={item.disabled}
                onClick={() => {
                  setOpen(false);
                  item.onClick();
                }}
              >
                {item.icon} {item.label}
              </button>
            ),
          )}
        </div>
      )}
    </div>
  );
}

/** Whether the app's chat is shown beside it: yes, unless it was closed */
function chatShown() {
  return stored<string>("appChat", "open") !== "closed";
}

/** One app, filling the page: what it serves, in a frame, from its own port; its controls, files, builds and logs */
export function AppView({ id }: { id: string }) {
  const [app, setApp] = useState<App | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [opening, setOpening] = useState<Opening>({ state: "opening" });
  // Changed to load it again (and start it, if it's stopped)
  const [loads, setLoads] = useState(0);
  const [panel, setPanel] = useState<Panel | null>(() => stored<Panel | null>("appPanel", null));
  const [busy, setBusy] = useState(false);
  const [build, setBuild] = useState<Build | null>(null);
  const [page, setPage] = useState<PageBuild | null>(null);
  const [building, setBuilding] = useState(false);
  // Files saved since the last build
  const [unbuilt, setUnbuilt] = useState(false);
  const [chatOpen, setChatOpen] = useState(chatShown);
  // The sizes of the panel and the chat, dragged and kept
  const [panelHeight, setPanelHeight] = useState(() => stored("appPanelHeight", 340));
  const [chatWidth, setChatWidth] = useState(() => stored("appChatWidth", 380));
  const dragFrom = useRef(0);
  // The build the frame shows: a new one (from here, or from the chat) loads it again
  const shownBuild = useRef<string | null>(null);

  useEffect(() => {
    let gone = false;
    setApp(null);
    setError(null);
    setUnbuilt(false);
    const load = () => getApp(id).then(found => !gone && setApp(found), e => !gone && setError((e as Error).message));
    load();
    const timer = setInterval(load, STATUS_MS);
    return () => {
      gone = true;
      clearInterval(timer);
    };
  }, [id]);

  // The last build's result, and the page's: the Build tab says when one failed
  useEffect(() => {
    lastBuild(id).then(setBuild, () => {});
    pageBuild(id).then(setPage, () => {});
  }, [id, panel, app?.page]);

  useEffect(() => {
    shownBuild.current = null;
  }, [id]);
  useEffect(() => {
    if (!app || app.id !== id) return;
    // A new build, or a new page (built by itself after a change), reloads the frame
    const shown = `${app.build}:${app.page ?? ""}`;
    if (shownBuild.current !== null && shown !== shownBuild.current) {
      if (app.build !== Number(shownBuild.current.split(":")[0])) setUnbuilt(false);
      setLoads(n => n + 1);
      lastBuild(id).then(setBuild, () => {});
    }
    shownBuild.current = shown;
  }, [app, id]);

  const toggleChat = () =>
    setChatOpen(open => {
      store("appChat", open ? "closed" : "open");
      return !open;
    });

  const showPanel = (which: Panel | null) => {
    setPanel(which);
    store("appPanel", which);
  };

  // Opening it starts it, if it's stopped
  useEffect(() => {
    let gone = false;
    setOpening({ state: "opening" });
    openApp(id).then(
      async ({ url, origin, ready }) => {
        const reached = await reaches(origin);
        if (gone) return;
        getApp(id).then(setApp, () => {});
        if (!reached) setOpening({ state: "unreachable", origin });
        else setOpening(ready ? { state: "open", url, origin } : { state: "not-answering", url, origin });
      },
      e => !gone && setError((e as Error).message),
    );
    return () => {
      gone = true;
    };
  }, [id, loads]);

  /** A tab of its own; opened before asking where, so it isn't taken for a pop-up */
  const openTab = () => {
    const tab = window.open("about:blank", "_blank");
    openApp(id).then(
      ({ url }) => tab && (tab.location.href = url),
      e => (tab?.close(), toast((e as Error).message)),
    );
  };

  const change = async (action: "stop" | "restart") => {
    setBusy(true);
    try {
      setApp(await changeApp(id, action));
      if (action === "restart") setLoads(n => n + 1);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const rebuild = async () => {
    setBuilding(true);
    showPanel("build");
    try {
      const result = await buildApp(id);
      setApp(result.app);
      // The build's own record: when it failed, its number isn't the one that runs
      setBuild(await lastBuild(id).catch(() => ({ build: result.app.build, ok: result.ok, error: result.error, log: result.log })));
      // A new build that runs reloads the frame (see shownBuild)
    } catch (e) {
      toast((e as Error).message);
      lastBuild(id).then(setBuild, () => {});
    } finally {
      setBuilding(false);
    }
  };

  const remove = async () => {
    if (!app || !window.confirm(`Remove ${app.name}? Its container, its files, its history and its data go.`)) return;
    try {
      await deleteApp(app.id);
      navigate("/apps", { replace: true });
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const origin = opening.state === "opening" ? null : opening.origin;
  const up = app ? isUp(app) : true;
  const failing = app && (app.status === "restarting" || app.detail.startsWith("Crashed"));
  const badge = (which: Panel) =>
    which === "files" && unbuilt ? <span className="app-badge" title="Saved changes not built yet" />
      : which === "build" && building ? <span className="app-badge busy" title="Building" />
        : which === "build" && build?.ok === false ? <span className="app-badge bad" title="The last build failed" />
          : which === "logs" && failing ? <span className="app-badge bad" title="It crashed: see its logs" />
            : null;

  return (
    <div className="app-view">
      <div className="app-bar">
        <Link to="/apps" className="button icon-text" title="All your apps">
          ← Apps
        </Link>
        <div className="app-title">
          <b className="app-bar-name">{app?.name ?? (error ? "" : "…")}</b>
          {app && (
            <span
              className={`app-status ${app.status === "running" ? "ok" : failing ? "bad" : ""}`}
              title={app.memory_used ? `${megabytes(app.memory_used)} of ${limit(app.memory_limit)} memory` : undefined}
            >
              <State app={app} />
              {app.build > 0 && <span className="muted"> · build {app.build}</span>}
            </span>
          )}
        </div>
        <span className="app-bar-gap" />
        {app && (
          <>
            {!app.legacy && (
              <button
                type="button"
                className={unbuilt ? "primary icon-text" : "icon-text"}
                onClick={rebuild}
                disabled={building}
                title={unbuilt ? "Build the saved changes and run them" : "Build its files again and run the new build"}
              >
                <HammerIcon /> {building ? "Building…" : "Rebuild"}
              </button>
            )}
            {up ? (
              <button type="button" className="icon-text" onClick={() => change("stop")} disabled={busy} title="Stop its container">
                <StopIcon /> Stop
              </button>
            ) : (
              <button type="button" className="primary icon-text" onClick={() => setLoads(n => n + 1)} disabled={busy}>
                <PlayIcon /> Start
              </button>
            )}
            <MoreMenu
              label="More"
              items={[
                { label: "Restart", icon: <RegenerateIcon />, onClick: () => change("restart"), disabled: busy || !up },
                { label: "Open in a new tab", icon: <ExternalIcon />, onClick: openTab, disabled: !up },
                {
                  label: "Copy its address",
                  icon: <CopyIcon />,
                  onClick: () => origin && navigator.clipboard.writeText(origin).then(() => toast("Copied")),
                  disabled: !origin,
                },
                {
                  label: "Download its files (with git)",
                  icon: <DownloadIcon />,
                  onClick: () => (window.location.href = appSourceUrl(id)),
                  disabled: app.legacy,
                },
                { label: "Download its data", icon: <DownloadIcon />, onClick: () => (window.location.href = appDataUrl(id)), disabled: app.legacy },
                "line",
                { label: "Remove the app…", icon: <TrashIcon />, onClick: remove, danger: true },
              ]}
            />
            <button
              type="button"
              className={chatOpen ? "icon-text on" : "icon-text"}
              aria-pressed={chatOpen}
              onClick={toggleChat}
              title={chatOpen ? "Hide the chat" : "Build it with labb, in a chat beside it"}
            >
              <ChatsIcon /> Chat
            </button>
          </>
        )}
      </div>
      {/* Why it isn't running is on the stopped app's own card; a running one's note (restarted after crashing) here */}
      {app?.detail && up && <div className="hint app-detail">{app.detail}</div>}
      <div className="app-main">
        <div className="app-body">
          {up && origin && (
            <div className="app-address">
              <button type="button" className="icon-button" onClick={() => setLoads(n => n + 1)} title="Load the page again" aria-label="Reload">
                <RegenerateIcon />
              </button>
              <span className="app-url" title={origin}>
                {origin.replace(/^https?:\/\//, "")}
              </span>
              {app?.memory_used ? <span className="muted">{megabytes(app.memory_used)} of {limit(app.memory_limit)}</span> : null}
              <button type="button" className="icon-button" onClick={openTab} title="Open in a new tab" aria-label="Open in a new tab">
                <ExternalIcon />
              </button>
            </div>
          )}
          <div className="app-stage">
            {error && <div className="problem app-message">{error}</div>}
            {!error && app && !up && (
              <div className="app-stopped">
                <p>
                  <b>{app.name}</b> is stopped.
                </p>
                <p className={failing || app.detail.startsWith("Ran out") ? "problem" : "muted"}>
                  {app.detail && app.detail !== "Stopped" ? app.detail : "Start it to use it here."}
                </p>
                <div className="app-stopped-actions">
                  <button type="button" className="primary icon-text" onClick={() => setLoads(n => n + 1)}>
                    <PlayIcon /> Start
                  </button>
                  {failing || app.detail.startsWith("Crashed") ? (
                    <button type="button" onClick={() => showPanel("logs")}>
                      Look at its logs
                    </button>
                  ) : null}
                </div>
              </div>
            )}
            {!error && up && opening.state === "opening" && <div className="hint app-message">Starting {app?.name ?? "the app"}…</div>}
            {!error && up && opening.state === "unreachable" && (
              <div className="problem app-message">
                This browser can't reach {opening.origin}. Each app has a port of its own on labb's host, so that port needs to be
                open to this device: is a firewall on the server letting labb's port through but not the apps' ones?
              </div>
            )}
            {!error && up && opening.state === "not-answering" && (
              <div className="problem app-message">{app?.name ?? "The app"} isn't answering yet. Try reloading in a moment, or look at its logs.</div>
            )}
            {!error && up && opening.state === "open" && (
              // Sandboxed so an app can't navigate labb's tab away (to a page that looks like labb's login,
              // say); it keeps its own origin (another port), so it still can't reach labb's page
              <iframe
                key={loads}
                className="app-frame"
                src={opening.url}
                title={app?.name ?? "App"}
                sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads allow-modals"
              />
            )}
          </div>
          {app && (
            <div className="app-dock" style={panel ? { height: panelHeight } : undefined}>
              {panel && (
                <Resizer
                  axis="y"
                  label="Drag to resize the panel"
                  onStart={() => (dragFrom.current = panelHeight)}
                  onMove={delta => setPanelHeight(Math.max(140, Math.min(window.innerHeight - 220, dragFrom.current - delta)))}
                  onEnd={() => setPanelHeight(h => (store("appPanelHeight", h), h))}
                />
              )}
              <div className="app-tabs" role="tablist" aria-label="The app's files, history, builds, logs and settings">
                {PANELS.map(p => (
                  <button
                    key={p.id}
                    type="button"
                    role="tab"
                    aria-selected={panel === p.id}
                    className={panel === p.id ? "on" : undefined}
                    onClick={() => showPanel(panel === p.id ? null : p.id)}
                  >
                    {p.icon} {p.label}
                    {badge(p.id)}
                  </button>
                ))}
                <span className="app-bar-gap" />
                <button
                  type="button"
                  className="icon-button"
                  onClick={() => showPanel(panel ? null : "files")}
                  aria-label={panel ? "Hide the panel" : "Show the panel"}
                  title={panel ? "Hide the panel" : "Show the panel"}
                >
                  <span className={panel ? "app-chevron" : "app-chevron up"}>
                    <ChevronDownIcon />
                  </span>
                </button>
              </div>
              {panel && (
                <div className="app-panel" role="tabpanel">
                  {panel === "files" && <Files app={app} onChanged={path => !path.startsWith("frontend/") && setUnbuilt(true)} />}
                  {panel === "build" && <BuildOutput build={build} building={building} page={page} />}
                  {panel === "logs" && <Logs id={id} />}
                  {panel === "history" && <History app={app} onRestored={() => setUnbuilt(true)} />}
                  {panel === "database" && (
                    <DatabasePanel
                      app={app}
                      onChanged={changed => {
                        setApp(changed);
                        setLoads(n => n + 1);
                      }}
                    />
                  )}
                  {panel === "settings" && (
                    <SettingsPanel
                      app={app}
                      onSaved={saved => {
                        setApp(saved);
                        setLoads(n => n + 1);
                      }}
                    />
                  )}
                </div>
              )}
            </div>
          )}
        </div>
        {chatOpen && app && (
          <div className="app-chat-side" style={{ width: chatWidth }}>
            <Resizer
              axis="x"
              label="Drag to resize the chat"
              onStart={() => (dragFrom.current = chatWidth)}
              onMove={delta => setChatWidth(Math.max(280, Math.min(window.innerWidth * 0.6, dragFrom.current - delta)))}
              onEnd={() => setChatWidth(w => (store("appChatWidth", w), w))}
            />
            <AppChat app={app} onReplyEnded={() => getApp(id).then(setApp, () => {})} />
          </div>
        )}
      </div>
    </div>
  );
}
