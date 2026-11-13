// A code editor for the files of an app or an artifact (CodeMirror 6): highlighting by the file's kind, line numbers,
// a find bar (Ctrl/Cmd-F; findBar below), undo, brackets, several cursors, Tab to indent, Ctrl/Cmd-S to save.
// Its colours are the page's (CSS variables), so it follows light and dark.
// Loaded only when a file is opened (FileEditor.tsx, which apps and artifacts share, imports it lazily).
import { indentWithTab } from "@codemirror/commands";
import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { StreamLanguage, syntaxHighlighting } from "@codemirror/language";
import { dockerFile } from "@codemirror/legacy-modes/mode/dockerfile";
import { shell } from "@codemirror/legacy-modes/mode/shell";
import { toml } from "@codemirror/legacy-modes/mode/toml";
import { yaml } from "@codemirror/legacy-modes/mode/yaml";
import {
  SearchQuery,
  closeSearchPanel,
  findNext,
  findPrevious,
  getSearchQuery,
  openSearchPanel,
  replaceAll,
  replaceNext,
  search,
  searchPanelOpen,
  setSearchQuery,
} from "@codemirror/search";
import { EditorSelection, type EditorState, type Extension } from "@codemirror/state";
import { EditorView, type Panel, type ViewUpdate, keymap } from "@codemirror/view";
import { classHighlighter } from "@lezer/highlight";
import { basicSetup } from "codemirror";
import { type Ref, useEffect, useImperativeHandle, useRef } from "react";

/** The language for a file, by its name */
function languageOf(path: string): Extension | null {
  const name = path.split("/").pop()?.toLowerCase() ?? "";
  const extension = name.includes(".") ? name.split(".").pop() : "";
  if (name === "dockerfile" || name.startsWith("dockerfile.") || extension === "dockerfile") return StreamLanguage.define(dockerFile);
  switch (extension) {
    case "html":
    case "htm":
      return html();
    case "css":
      return css();
    case "js":
    case "mjs":
    case "cjs":
      return javascript();
    case "jsx":
      return javascript({ jsx: true });
    case "ts":
      return javascript({ typescript: true });
    case "tsx":
      return javascript({ typescript: true, jsx: true });
    case "json":
      return json();
    case "py":
      return python();
    case "md":
    case "markdown":
      return markdown();
    case "sh":
    case "bash":
      return StreamLanguage.define(shell);
    case "yml":
    case "yaml":
      return StreamLanguage.define(yaml);
    case "toml":
      return StreamLanguage.define(toml);
    default:
      return null;
  }
}

// Counting matches stops here, so a huge file stays quick
const MOST_COUNTED = 9999;

/** How many matches the query has, and which of them is selected (0: none) */
function countMatches(state: EditorState, query: SearchQuery): { count: number; current: number } {
  if (!query.search || !query.valid) return { count: 0, current: 0 };
  const { from, to } = state.selection.main;
  const cursor = query.getCursor(state);
  let count = 0;
  let current = 0;
  for (let next = cursor.next(); !next.done && count < MOST_COUNTED; next = cursor.next()) {
    count++;
    if (next.value.from === from && next.value.to === to) current = count;
  }
  return { count, current };
}

const svg = (paths: string) =>
  `<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;

/**
 * The find bar, in place of CodeMirror's own form: one row with the text, how many matches and
 * which one, case / whole word / regular expression as toggles, previous and next, and a second
 * row to replace when asked. Enter: next, Shift-Enter: previous, Escape: close.
 */
function findBar(view: EditorView): Panel {
  const dom = document.createElement("div");
  dom.className = "find-bar";
  dom.setAttribute("role", "search");

  const element = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Record<string, string>, parent: HTMLElement) => {
    const made = document.createElement(tag);
    for (const [name, value] of Object.entries(props)) made.setAttribute(name, value);
    parent.appendChild(made);
    return made;
  };
  const button = (parent: HTMLElement, label: string, inner: string, run: () => void, className = "") => {
    const made = element("button", { type: "button", "aria-label": label, title: label, class: className }, parent);
    made.innerHTML = inner;
    // Clicking keeps the focus where it was, so typing goes on in the field
    made.addEventListener("mousedown", event => event.preventDefault());
    made.addEventListener("click", run);
    return made;
  };

  const row = element("div", { class: "find-row" }, dom);
  const expand = button(row, "Replace", svg('<path d="M6 4l4 4-4 4"/>'), () => setReplacing(!replacing), "find-expand");
  const field = element("label", { class: "find-field" }, row);
  const input = element("input", { "main-field": "true", placeholder: "Find", "aria-label": "Find", spellcheck: "false", autocomplete: "off" }, field);
  const count = element("span", { class: "find-count", "aria-live": "polite" }, field);
  const toggle = (label: string, text: string, key: "caseSensitive" | "wholeWord" | "regexp") => {
    const made = button(field, label, text, () => commit({ [key]: !getSearchQuery(view.state)[key] }), "find-toggle");
    made.setAttribute("aria-pressed", "false");
    return made;
  };
  const caseToggle = toggle("Match case", "Aa", "caseSensitive");
  const wordToggle = toggle("Whole word", "<u>ab</u>", "wholeWord");
  const regexToggle = toggle("Regular expression", ".*", "regexp");
  const previous = button(row, "Previous match (Shift-Enter)", svg('<path d="M8 13V3M4 7l4-4 4 4"/>'), () => findPrevious(view));
  const next = button(row, "Next match (Enter)", svg('<path d="M8 3v10M4 9l4 4 4-4"/>'), () => findNext(view));
  button(row, "Close (Escape)", svg('<path d="M4 4l8 8M12 4l-8 8"/>'), () => closeSearchPanel(view));

  const replaceRow = element("div", { class: "find-row find-replace" }, dom);
  const replaceInput = element("input", { placeholder: "Replace", "aria-label": "Replace with", spellcheck: "false", autocomplete: "off" }, replaceRow);
  const replaceOne = element("button", { type: "button", title: "Replace this match and go to the next" }, replaceRow);
  replaceOne.textContent = "Replace";
  replaceOne.addEventListener("click", () => replaceNext(view));
  const replaceEvery = element("button", { type: "button", title: "Replace every match" }, replaceRow);
  replaceEvery.textContent = "All";
  replaceEvery.addEventListener("click", () => replaceAll(view));

  let replacing = false;
  function setReplacing(on: boolean) {
    replacing = on;
    replaceRow.hidden = !on;
    expand.classList.toggle("on", on);
    expand.setAttribute("aria-expanded", String(on));
    if (on) replaceInput.focus();
  }
  setReplacing(false);

  /** The query with these changes, given to the editor */
  function commit(changes: Partial<{ search: string; replace: string; caseSensitive: boolean; wholeWord: boolean; regexp: boolean }>) {
    const old = getSearchQuery(view.state);
    const query = new SearchQuery({
      search: old.search,
      replace: old.replace,
      caseSensitive: old.caseSensitive,
      wholeWord: old.wholeWord,
      regexp: old.regexp,
      literal: true,
      ...changes,
    });
    if (!query.eq(old)) view.dispatch({ effects: setSearchQuery.of(query) });
  }

  /** What the bar shows, from the editor's query and selection */
  function show(state: EditorState) {
    const query = getSearchQuery(state);
    if (input.value !== query.search) input.value = query.search;
    if (replaceInput.value !== query.replace) replaceInput.value = query.replace;
    caseToggle.setAttribute("aria-pressed", String(query.caseSensitive));
    wordToggle.setAttribute("aria-pressed", String(query.wholeWord));
    regexToggle.setAttribute("aria-pressed", String(query.regexp));
    const bad = Boolean(query.search) && !query.valid;
    const { count: found, current } = countMatches(state, query);
    const many = `${found}${found >= MOST_COUNTED ? "+" : ""}`;
    field.classList.toggle("bad", bad || (Boolean(query.search) && found === 0));
    count.textContent = !query.search ? "" : bad ? "Bad pattern" : found === 0 ? "No results" : current ? `${current} of ${many}` : `${many} match${found === 1 ? "" : "es"}`;
    previous.disabled = next.disabled = found === 0;
    replaceOne.disabled = replaceEvery.disabled = found === 0 || state.readOnly;
  }

  input.addEventListener("input", () => commit({ search: input.value }));
  replaceInput.addEventListener("input", () => commit({ replace: replaceInput.value }));
  const keys = (event: KeyboardEvent, enter: () => void) => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (event.shiftKey) findPrevious(view);
      else enter();
    }
  };
  input.addEventListener("keydown", event => keys(event, () => findNext(view)));
  // In the replace field, Alt-Enter replaces them all
  replaceInput.addEventListener("keydown", event => keys(event, () => (event.altKey ? replaceAll(view) : replaceNext(view))));

  return {
    dom,
    top: true,
    mount: () => {
      show(view.state);
      input.focus();
      input.select();
    },
    update: (update: ViewUpdate) => {
      const changedQuery = update.transactions.some(t => t.effects.some(e => e.is(setSearchQuery)));
      if (changedQuery || update.docChanged || update.selectionSet) show(update.state);
    },
  };
}

/** Select the first match of `query` on line `line` (from 1), scroll to it, and open the find bar with it */
function revealMatch(view: EditorView, line: number, query: SearchQuery) {
  const lines = view.state.doc.lines;
  const at = view.state.doc.line(Math.min(Math.max(line, 1), lines));
  const cursor = query.valid ? query.getCursor(view.state, at.from, at.to).next() : null;
  const range = cursor && !cursor.done ? EditorSelection.range(cursor.value.from, cursor.value.to) : EditorSelection.cursor(at.from);
  view.dispatch({
    selection: range,
    effects: [setSearchQuery.of(query), EditorView.scrollIntoView(range, { y: "center" })],
  });
  if (!searchPanelOpen(view.state)) openSearchPanel(view);
  view.focus();
}

/** What a file's editor can be told to do from outside it */
export type CodeEditorHandle = {
  /** Open the find bar (with what's selected, if anything) */
  find: () => void;
  /** Go to a match on a line, with the find bar showing that search */
  reveal: (line: number, find: FindOptions) => void;
};

export type FindOptions = { search: string; caseSensitive: boolean; wholeWord: boolean; regexp: boolean };

// The editor's chrome in the page's colours; the syntax colours are .tok-* classes in styles.css
const look = EditorView.theme({
  "&": { height: "100%", backgroundColor: "var(--code-bg)", color: "var(--text)", fontSize: "12.5px" },
  ".cm-scroller": { fontFamily: "var(--mono, ui-monospace, SFMono-Regular, Menlo, monospace)", lineHeight: "1.5" },
  ".cm-content": { caretColor: "var(--text)" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--text)" },
  ".cm-gutters": { backgroundColor: "var(--code-bg)", color: "var(--muted)", border: "none", borderRight: "1px solid var(--border)" },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--text) 4%, transparent)" },
  ".cm-activeLineGutter": { backgroundColor: "color-mix(in srgb, var(--text) 7%, transparent)", color: "var(--text)" },
  "&.cm-focused": { outline: "none" },
  "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, ::selection": {
    backgroundColor: "color-mix(in srgb, var(--accent) 28%, transparent)",
  },
  ".cm-matchingBracket": { backgroundColor: "color-mix(in srgb, var(--accent) 30%, transparent)", outline: "none" },
  // Matches in hazard yellow (it reads on both themes); the one selected, stronger and outlined
  ".cm-searchMatch": { backgroundColor: "color-mix(in srgb, var(--accent-fill) 35%, transparent)", borderRadius: "2px" },
  ".cm-searchMatch.cm-searchMatch-selected": {
    backgroundColor: "color-mix(in srgb, var(--accent-fill) 75%, transparent)",
    outline: "1px solid var(--accent)",
  },
  // Where else the selected word is: quieter than a search match
  ".cm-selectionMatch": { backgroundColor: "color-mix(in srgb, var(--text) 10%, transparent)" },
  ".cm-panels": { backgroundColor: "var(--surface)", color: "var(--text)" },
  ".cm-panels.cm-panels-top": { borderBottom: "1px solid var(--border)" },
  ".cm-tooltip": { backgroundColor: "var(--surface)", border: "1px solid var(--border)" },
  ".cm-foldPlaceholder": { backgroundColor: "transparent", border: "1px solid var(--border)", color: "var(--muted)" },
});

/**
 * The editor for one file: give it the file's text once (a new `key` for another file), and it
 * says each change through `onChange`
 */
export default function CodeEditor({
  path,
  value,
  onChange,
  onSave,
  startAt,
  ref,
}: {
  path: string;
  value: string;
  // Where to start: a match on a line (from 1), with the find bar showing that search
  startAt?: { line: number; find: FindOptions } | null;
  onChange: (text: string) => void;
  onSave: () => void;
  ref?: Ref<CodeEditorHandle>;
}) {
  const box = useRef<HTMLDivElement>(null);
  const editor = useRef<EditorView | null>(null);
  // The latest callbacks, for the editor made once
  const changed = useRef(onChange);
  const save = useRef(onSave);
  changed.current = onChange;
  save.current = onSave;

  useEffect(() => {
    const language = languageOf(path);
    const view = new EditorView({
      doc: value,
      parent: box.current!,
      extensions: [
        basicSetup,
        search({ top: true, createPanel: findBar }),
        keymap.of([
          indentWithTab,
          {
            key: "Mod-s",
            preventDefault: true,
            run: () => {
              save.current();
              return true;
            },
          },
        ]),
        ...(language ? [language] : []),
        syntaxHighlighting(classHighlighter),
        look,
        EditorView.lineWrapping,
        EditorView.contentAttributes.of({ "aria-label": `The contents of ${path}` }),
        EditorView.updateListener.of(update => {
          if (update.docChanged) changed.current(update.state.doc.toString());
        }),
      ],
    });
    editor.current = view;
    if (startAt) revealMatch(view, startAt.line, new SearchQuery({ ...startAt.find, literal: true }));
    else view.focus();
    return () => {
      editor.current = null;
      view.destroy();
    };
    // A file's editor is made once; another file gets a new one (its key)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useImperativeHandle(
    ref,
    () => ({
      find: () => {
        if (editor.current) openSearchPanel(editor.current);
      },
      reveal: (line, find) => {
        if (editor.current) revealMatch(editor.current, line, new SearchQuery({ ...find, literal: true }));
      },
    }),
    [],
  );

  return <div ref={box} className="app-code" />;
}
