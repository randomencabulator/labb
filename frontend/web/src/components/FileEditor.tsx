// The file editor that apps and artifacts share: a tree of the files, a search of them all
// (Ctrl/Cmd-Shift-F, when the files can be searched) and the code editor for the one picked.
// Where the files are and what saving them does comes from a FileStore.
import { type FormEvent, type ReactNode, type RefObject, Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AppSearch, AppSearchMatch, AppSearchOptions } from "../api";
import type { CodeEditorHandle, FindOptions } from "./CodeEditor";
import { ChevronDownIcon, FileIcon, FolderIcon, SearchIcon, TrashIcon } from "./Icons";
import { toast } from "./Toaster";

// The code editor is only loaded when a file is opened
const CodeEditor = lazy(() => import("./CodeEditor"));

export type FileEntry = { path: string; size: number };

/** Where a FileEditor's files are, and how to change them */
export type FileStore = {
  list: () => Promise<FileEntry[]>;
  read: (path: string) => Promise<string>;
  write: (path: string, content: string) => Promise<unknown>;
  remove: (path: string) => Promise<unknown>;
  // Searching them all; without it, there's no search field
  search?: (query: string, options: AppSearchOptions, signal: AbortSignal) => Promise<AppSearch>;
};

type TreeRow = { kind: "folder"; path: string; name: string; depth: number } | { kind: "file"; file: FileEntry; name: string; depth: number };

/** The files as a tree's rows: each folder's folders first, then its files, by name; a folded folder's rows left out */
function treeRows(files: FileEntry[], folded: Set<string>): TreeRow[] {
  type Node = { folders: Map<string, Node>; files: FileEntry[] };
  const root: Node = { folders: new Map(), files: [] };
  for (const file of files) {
    const parts = file.path.split("/");
    let node = root;
    for (const part of parts.slice(0, -1)) {
      if (!node.folders.has(part)) node.folders.set(part, { folders: new Map(), files: [] });
      node = node.folders.get(part)!;
    }
    node.files.push(file);
  }
  const rows: TreeRow[] = [];
  const walk = (node: Node, prefix: string, depth: number) => {
    for (const [name, child] of [...node.folders].sort(([a], [b]) => a.localeCompare(b))) {
      const path = prefix + name;
      rows.push({ kind: "folder", path, name, depth });
      if (!folded.has(path)) walk(child, path + "/", depth + 1);
    }
    for (const file of [...node.files].sort((a, b) => a.path.localeCompare(b.path))) {
      rows.push({ kind: "file", file, name: file.path.split("/").pop()!, depth });
    }
  };
  walk(root, "", 0);
  return rows;
}

// How long typing has to stop before the files are searched
const SEARCH_MS = 250;

/** A line of a search result, with what matched marked */
function Marked({ text, ranges }: { text: string; ranges: [number, number][] }) {
  const parts: ReactNode[] = [];
  let at = 0;
  ranges.forEach(([from, to], i) => {
    if (from > at) parts.push(text.slice(at, from));
    parts.push(<mark key={i}>{text.slice(from, to)}</mark>);
    at = to;
  });
  if (at < text.length) parts.push(text.slice(at));
  return <>{parts}</>;
}

/** A path with its folder dimmed */
function PathName({ path }: { path: string }) {
  const cut = path.lastIndexOf("/") + 1;
  return (
    <span className="app-search-path">
      <b>{path.slice(cut)}</b>
      {cut > 0 && <span className="muted"> {path.slice(0, cut - 1)}</span>}
    </span>
  );
}

/**
 * Searching all the files: a field with case / whole word / regular expression, then the
 * files whose path has it and the lines it's on, by file. Picking a line opens the file there.
 */
function FileSearch({
  search,
  what,
  query,
  options,
  version,
  onQuery,
  onOptions,
  onOpen,
  field,
}: {
  search: NonNullable<FileStore["search"]>;
  what: string;
  query: string;
  options: AppSearchOptions;
  // Changes when a file is saved, to search again
  version: number;
  onQuery: (query: string) => void;
  onOptions: (options: AppSearchOptions) => void;
  onOpen: (path: string, match?: AppSearchMatch) => void;
  field: RefObject<HTMLInputElement | null>;
}) {
  const [found, setFound] = useState<AppSearch | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [folded, setFolded] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!query) {
      setFound(null);
      setError(null);
      setSearching(false);
      return;
    }
    const stop = new AbortController();
    setSearching(true);
    const wait = window.setTimeout(() => {
      search(query, options, stop.signal).then(
        result => {
          setFound(result);
          setError(null);
          setSearching(false);
        },
        e => {
          if (stop.signal.aborted) return;
          setFound(null);
          setError((e as Error).message);
          setSearching(false);
        },
      );
    }, SEARCH_MS);
    return () => {
      window.clearTimeout(wait);
      stop.abort();
    };
  }, [search, query, options, version]);

  // The matches by file, in the order they came
  const byFile = useMemo(() => {
    const groups = new Map<string, AppSearchMatch[]>();
    for (const match of found?.matches ?? []) {
      if (!groups.has(match.path)) groups.set(match.path, []);
      groups.get(match.path)!.push(match);
    }
    return [...groups];
  }, [found]);

  const toggle = (key: keyof AppSearchOptions, label: string, text: ReactNode) => (
    <button
      type="button"
      className="find-toggle"
      aria-pressed={options[key]}
      aria-label={label}
      title={label}
      onMouseDown={e => e.preventDefault()}
      onClick={() => onOptions({ ...options, [key]: !options[key] })}
    >
      {text}
    </button>
  );

  // Matches, not lines: a line can have several
  const total = found?.matches.reduce((sum, match) => sum + match.ranges.length, 0) ?? 0;
  const empty = found && total === 0 && found.files.length === 0;
  return (
    <>
      <div className="app-search">
        <label className={error || empty ? "find-field bad" : "find-field"}>
          <SearchIcon />
          <input
            ref={field}
            value={query}
            placeholder="Search all files"
            aria-label={`Search all ${what} files`}
            title="Search all files (Ctrl/Cmd-Shift-F)"
            spellCheck={false}
            autoComplete="off"
            onChange={e => onQuery(e.target.value)}
            onKeyDown={e => {
              if (e.key === "Escape") onQuery("");
              if (e.key === "Enter" && found) {
                const first = found.matches[0];
                if (first) onOpen(first.path, first);
                else if (found.files[0]) onOpen(found.files[0]);
              }
            }}
          />
          {toggle("case", "Match case", "Aa")}
          {toggle("word", "Whole word", <u>ab</u>)}
          {toggle("regex", "Regular expression", ".*")}
        </label>
      </div>
      {query && (
        <div className="app-search-results" aria-busy={searching}>
          <p className="app-search-summary muted" aria-live="polite">
            {error
              ? error
              : !found
                ? "Searching…"
                : empty
                  ? "No results"
                  : [
                      total ? `${total}${found.more ? "+" : ""} result${total === 1 ? "" : "s"} in ${byFile.length} file${byFile.length === 1 ? "" : "s"}` : "",
                      found.files.length ? `${found.files.length} file name${found.files.length === 1 ? "" : "s"}` : "",
                    ]
                      .filter(Boolean)
                      .join(" · ")}
          </p>
          {found && found.files.length > 0 && (
            <>
              <h4>File names</h4>
              <ul>
                {found.files.map(path => (
                  <li key={`n:${path}`}>
                    <button type="button" className="app-search-file" onClick={() => onOpen(path)} title={path}>
                      <FileIcon /> <PathName path={path} />
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
          {byFile.length > 0 && found!.files.length > 0 && <h4>In files</h4>}
          <ul>
            {byFile.map(([path, matches]) => (
              <li key={path}>
                <button
                  type="button"
                  className="app-search-file"
                  aria-expanded={!folded.has(path)}
                  title={path}
                  onClick={() =>
                    setFolded(all => {
                      const next = new Set(all);
                      if (next.has(path)) next.delete(path);
                      else next.add(path);
                      return next;
                    })
                  }
                >
                  <span className={folded.has(path) ? "app-chevron side" : "app-chevron"}>
                    <ChevronDownIcon />
                  </span>
                  <PathName path={path} />
                  <span className="app-search-count">{matches.reduce((sum, match) => sum + match.ranges.length, 0)}</span>
                </button>
                {!folded.has(path) && (
                  <ul>
                    {matches.map(match => (
                      <li key={match.line}>
                        <button type="button" className="app-search-line" onClick={() => onOpen(path, match)} title={`${path}:${match.line}`}>
                          <span className="app-search-number">{match.line}</span>
                          <span className="app-search-text">
                            <Marked text={match.text.trimEnd()} ranges={match.ranges} />
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
          {found?.more && <p className="app-search-summary muted">Only the first {found.matches.length} matching lines are shown: search for something more precise.</p>}
        </div>
      )}
    </>
  );
}

/**
 * Files to read and change: a tree, and the one picked in the code editor. `what` names whose
 * files they are in labels ("the app's"); `keep` says why a file can't be deleted, if it can't.
 */
export function FileEditor({
  store,
  what,
  hint,
  keep,
  footer,
  onChanged,
}: {
  store: FileStore;
  what: string;
  // What the editor says before a file is picked
  hint: ReactNode;
  keep?: (path: string) => string | null;
  // Under the tree, e.g. a download link
  footer?: ReactNode;
  // After a file is saved, made or deleted
  onChanged?: (path: string) => void;
}) {
  const [files, setFiles] = useState<FileEntry[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [text, setText] = useState("");
  // Each opening of a file gets a fresh editor
  const [opened, setOpened] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const [newPath, setNewPath] = useState("");
  // Searching all the files: what for, how, and a count of saves (to search again after one)
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<AppSearchOptions>({ case: false, regex: false, word: false });
  const [saves, setSaves] = useState(0);
  const searchField = useRef<HTMLInputElement>(null);
  const editor = useRef<CodeEditorHandle>(null);
  // Where a file opened from a search result starts: that match, shown in its find bar
  const [startAt, setStartAt] = useState<{ line: number; find: FindOptions } | null>(null);
  const searching = Boolean(store.search && query);

  const reload = useCallback(() => store.list().then(setFiles, e => setError((e as Error).message)), [store]);
  useEffect(() => {
    reload();
  }, [reload]);

  // Ctrl/Cmd-Shift-F: search all the files
  useEffect(() => {
    if (!store.search) return;
    const keys = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.shiftKey && event.key.toLowerCase() === "f") {
        event.preventDefault();
        searchField.current?.focus();
        searchField.current?.select();
      }
    };
    window.addEventListener("keydown", keys);
    return () => window.removeEventListener("keydown", keys);
  }, [store.search]);

  const pick = async (path: string, match?: AppSearchMatch) => {
    const find: FindOptions | undefined = match && { search: query, caseSensitive: options.case, wholeWord: options.word, regexp: options.regex };
    // The file already open: just go to the match
    if (path === open && saved !== null) {
      if (match && find) editor.current?.reveal(match.line, find);
      return;
    }
    if (saved !== null && text !== saved && !window.confirm(`Leave ${open} without saving?`)) return;
    try {
      const content = await store.read(path);
      setOpen(path);
      setSaved(content);
      setStartAt(match && find ? { line: match.line, find } : null);
      setOpened(n => n + 1);
      setText(content);
      setError(null);
    } catch (e) {
      setOpen(path);
      setSaved(null);
      setError((e as Error).message);
    }
  };

  const save = async () => {
    if (!open) return;
    try {
      await store.write(open, text);
      setSaved(text);
      setSaves(n => n + 1);
      reload();
      onChanged?.(open);
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const add = async (event: FormEvent) => {
    event.preventDefault();
    const path = newPath.trim().replace(/^\/+/, "");
    if (!path) return;
    try {
      await store.write(path, "");
      setAdding(false);
      setNewPath("");
      await reload();
      setOpen(path);
      setSaved("");
      setText("");
      setOpened(n => n + 1);
      onChanged?.(path);
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const remove = async () => {
    if (!open || !window.confirm(`Delete ${open}?`)) return;
    try {
      await store.remove(open);
      setOpen(null);
      setSaved(null);
      reload();
      onChanged?.(open);
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const changed = saved !== null && text !== saved;
  const kept = open ? (keep?.(open) ?? null) : null;
  return (
    <div className="app-files">
      <div className={searching ? "app-file-list searching" : "app-file-list"}>
        {store.search && (
          <FileSearch
            search={store.search}
            what={what}
            query={query}
            options={options}
            version={saves}
            onQuery={setQuery}
            onOptions={setOptions}
            onOpen={pick}
            field={searchField}
          />
        )}
        <ul role="tree" aria-label={`${what[0].toUpperCase()}${what.slice(1)} files`} hidden={searching}>
          {files &&
            treeRows(files, folded).map(row =>
              row.kind === "folder" ? (
                <li key={`d:${row.path}`} role="treeitem" aria-expanded={!folded.has(row.path)}>
                  <button
                    type="button"
                    className="app-tree-folder"
                    style={{ paddingLeft: 10 + row.depth * 14 }}
                    onClick={() =>
                      setFolded(all => {
                        const next = new Set(all);
                        if (next.has(row.path)) next.delete(row.path);
                        else next.add(row.path);
                        return next;
                      })
                    }
                  >
                    <span className={folded.has(row.path) ? "app-chevron side" : "app-chevron"}>
                      <ChevronDownIcon />
                    </span>
                    <FolderIcon /> {row.name}
                  </button>
                </li>
              ) : (
                <li key={row.file.path} role="treeitem" aria-selected={row.file.path === open}>
                  <button
                    type="button"
                    className={row.file.path === open ? "on" : undefined}
                    style={{ paddingLeft: 10 + row.depth * 14 + 16 }}
                    onClick={() => pick(row.file.path)}
                    title={`${row.file.path} · ${row.file.size.toLocaleString()} bytes`}
                  >
                    <FileIcon /> {row.name}
                    {row.file.path === open && changed && <span className="app-badge" title="Not saved" />}
                  </button>
                </li>
              ),
            )}
        </ul>
        {searching ? null : adding ? (
          <form onSubmit={add}>
            <input autoFocus value={newPath} placeholder="folder/name.ext" aria-label="The new file's path" onChange={e => setNewPath(e.target.value)} onKeyDown={e => e.key === "Escape" && setAdding(false)} />
          </form>
        ) : (
          <button type="button" className="link" onClick={() => setAdding(true)}>
            + New file
          </button>
        )}
        {!searching && footer}
      </div>
      <div className="app-file">
        {!open && <p className="hint">{hint}</p>}
        {open && error && <div className="problem">{error}</div>}
        {open && saved !== null && (
          <>
            <div className="app-file-head">
              <span className="app-file-path">
                {open.includes("/") && <span className="muted">{open.slice(0, open.lastIndexOf("/") + 1)}</span>}
                <b>{open.split("/").pop()}</b>
              </span>
              <span className="app-bar-gap" />
              <span className={changed ? "app-save-state changed" : "app-save-state"}>{changed ? "Not saved" : "Saved"}</span>
              <button type="button" className="icon-button bordered" onClick={() => editor.current?.find()} aria-label="Find in this file" title="Find in this file (Ctrl/Cmd-F)">
                <SearchIcon />
              </button>
              <button type="button" className="primary" onClick={save} disabled={!changed} title="Save (Ctrl/Cmd-S)">
                Save
              </button>
              <button
                type="button"
                className="icon-button bordered danger"
                onClick={remove}
                disabled={Boolean(kept)}
                aria-label={`Delete ${open}`}
                title={kept ?? `Delete ${open}`}
              >
                <TrashIcon />
              </button>
            </div>
            <Suspense fallback={<p className="hint">Loading the editor…</p>}>
              <CodeEditor
                key={`${open}:${opened}`}
                ref={editor}
                path={open}
                value={saved}
                startAt={startAt}
                onChange={setText}
                onSave={() => changed && save()}
              />
            </Suspense>
          </>
        )}
      </div>
    </div>
  );
}
