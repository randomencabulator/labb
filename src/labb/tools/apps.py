"""Building one of the user's apps (labb-spawner's: see server/apps.py) from the chat that belongs
to it, beside the app on its page: read and change its files, build it, read its logs, look at it.

Offered only in that chat (`configurable["app"]`, which server/app.py's /chat sets after checking
the app is the user's), and every call goes to the spawner as that user, so it only ever reaches
their own app. This module stays free of the server's code: server/app_tools.py sets `backend`."""

import difflib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

LABEL = "This app"
DESCRIPTION = "Change, build and check the app this chat belongs to"
# Not in "all tools": only the app's own chat has it
ONLY_IF_ALLOWED = True
RUN_TOGETHER = ("list_app_files", "read_app_file", "app_logs", "look_at_app", "app_history", "app_version_diff", "db_schema", "db_query")

# Set by the server: has `files`, `read_text`, `write`, `delete`, `build`, `logs` and `look`, each
# `(config, ...)` returning text, or raising ValueError with what went wrong
backend = None

# What a tool gives back of a file or a page, at most
MAX_TEXT = 60_000

PROMPT = (
    "This chat belongs to one of the user's apps, shown beside it on its page. The app is two parts, each built by itself:\n"
    "- Its page, in frontend/: React (Vite: index.html, src/main.jsx, src/App.jsx) with shadcn/ui on Tailwind CSS. A change "
    "to these files is built and goes live by itself a few seconds later: there's nothing to deploy, and nothing to change "
    "anywhere else for it. A page that doesn't build leaves the last good one up; look_at_app says when it failed and why. "
    "Read frontend/AGENTS.md before changing the page: it lists the components (frontend/src/components/ui/, imported as "
    "`@/components/ui/button`), the theme and the rules. In short: components first, and only the theme's colours as "
    "Tailwind classes (bg-primary, text-muted-foreground, bg-card…), never colours of your own; the look is data-color, "
    "data-style and data-font on <html> in frontend/index.html. Packages for the page go in frontend/package.json with "
    "exact versions (no lockfile: never write one).\n"
    "- Its server, if it has one, in backend/: FastAPI (main.py) or Express (server.js), only for the JSON API under /api/ "
    "that the page calls (`api('/api/items')` from `@/lib/api`). It never serves the page. Read backend/AGENTS.md before "
    "changing it: items.py (or items.js) is the example to copy for each thing the app keeps, with its table in "
    "db/migrations/. `build_app` builds the server and runs the new one; leave backend/Dockerfile as it is. Packages go "
    "in backend/requirements.txt or backend/package.json, with exact versions that exist. An app without backend/ is "
    "its page only.\n"
    "The server runs as an unprivileged user with a read-only filesystem but /tmp (wiped at each start) and /data, kept "
    "across restarts and builds. The user sets environment variables and secrets on the app's page (list_app_files names "
    "them): read them from the environment, never write secrets into files. The app is only reachable through "
    "look_at_app: it has no address on the internet to fetch.\n"
    "`read_app_file` shows a file with its line numbers (`12| `) before each line, and can show only some lines "
    "(start_line, end_line), e.g. around the line an error names; the numbers aren't in the file, so leave them out of "
    "what you write.\n"
    "Work like this: `list_app_files` and `read_app_file` to see what's there before changing it; `edit_app_file` for "
    "a change in a file, `write_app_file` for a new file or rewriting one. For the page, then `look_at_app('/')`, which "
    "waits for its build. For the server, `build_app`, then `look_at_app('/api/…')` and `app_logs` (its errors). A build "
    "that crashes as it starts fails, with the error it wrote, and the last good one runs again: fix that line of that "
    "file. Don't stop at a change that isn't checked.\n"
    "Data: the starters' backend/db.py (db.js) keeps it in SQLite at /data/app.db, or in Postgres once the app has a "
    "database (DATABASE_URL; list_app_files says), with the same calls either way: db.query, db.one, db.execute, `?` "
    "for values. Tables only from migrations, db/migrations/0001_name.sql, 0002_….sql, each run once in name order (for "
    "Postgres at each build, where one that fails fails the build; for SQLite as the server starts). A migration that ran "
    "can't be edited: write the next one. Write SQL both take: TEXT ids made by the server, BOOLEAN, TIMESTAMP. "
    "`db_schema` shows the Postgres database's tables and migrations, `db_query` runs a read-only query on it.\n"
    "An app the user let use labb (list_app_files says so) can ask labb's models, run the user's agents, search their "
    "documents and use their memories, from its server, with labb.py (Python) or labb.js (Node): `add_labb_helper` puts "
    "one in the app's backend/ if it hasn't got it; e.g. `labb.ask(\"...\")` gives {\"text\", \"conversation\"}. Never from "
    "the app's pages or JavaScript in the browser: LABB_API_KEY is the app's secret, so pages ask the app's own server.\n"
    "An app whose database API is on (db_schema says) can also use its tables straight from its page, with no server "
    "code (Postgres only, so its migrations can be Postgres's own SQL). In the React "
    "code: `const api = await import(/* @vite-ignore */ `${location.protocol}//${location.hostname}:${Number(location.port) + 100}"
    "/client.js`)` (the comment keeps Vite from bundling it), then `api.from('items').select('select=*&order=id')`, `.insert({...})`, `.update('id=eq.3', {...})`, "
    "`.remove('id=eq.3')`, `api.rpc('fn', {...})` (PostgREST underneath: /rest/<table>?select=…&name=eq.…). Requests run as "
    "the `anon` role, or `authenticated` once someone has signed in, and have only what a migration grants them, e.g. "
    "`GRANT SELECT ON items TO anon;`: nothing by default. Grant anon only what anyone may see or do. Use identity columns "
    "(`id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY`), which need no sequence grant. Functions are only on it when "
    "granted (GRANT EXECUTE … TO anon).\n"
    "Its own users (not labb's) sign up and in on that API: `api.signUp(email, password)`, `api.signIn(email, password)`, "
    "`api.signOut()`, `api.user()` (or null), `api.onUser(user => …)` when that changes, `api.changePassword(now, next)`; "
    "errors are thrown with a message to show. After that, the client sends their token itself. They're in auth.users (never "
    "change the auth schema, nor expose it in a view); `auth.uid()` is the signed-in user's id, null otherwise. For data "
    "that's each user's own, use this migration, as it is, with the table's own columns:\n"
    "CREATE TABLE notes (\n  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,\n"
    "  user_id uuid NOT NULL DEFAULT auth.uid() REFERENCES auth.users ON DELETE CASCADE,\n"
    "  body text NOT NULL,\n  created_at timestamptz NOT NULL DEFAULT now()\n);\n"
    "ALTER TABLE notes ENABLE ROW LEVEL SECURITY;\n"
    "CREATE POLICY own_rows ON notes FOR ALL TO authenticated USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());\n"
    "GRANT SELECT, INSERT, UPDATE, DELETE ON notes TO authenticated;\n"
    "Each signed-in user then sees and changes only their rows, whatever the page sends. Never grant such a table to anon. "
    "For anything anon may write, turn on row-level security with a policy too. The page shows a sign-in form while "
    "`api.user()` is null. The user sees, disables and removes the app's users on its Database tab.\n"
    "Every build saves the app's files as a version: `app_history` lists them, `app_version_diff` shows what one "
    "changed, and `restore_app_version` puts the files back as they were (the page goes live again by itself; build for "
    "the server). Use it to undo a change that broke the app, or when the user asks to go back.\n"
    "Keep the app's own name, and keep it small and working at every step. Tell the user what you changed, briefly, "
    "once it works.\n"
    "Your notes here are this app's own, kept for its sessions: how it's built, what was decided, what's left. The "
    "user's other notes aren't in them, and nothing you write here reaches their other chats."
)


def enabled() -> bool:
    return backend is not None


def _refusal(config: RunnableConfig | None) -> str | None:
    if backend is None or not ((config or {}).get("configurable") or {}).get("app"):
        return "Error: this only works in the chat of one of your apps, on its page."
    return None


def _run(config: RunnableConfig | None, action: str, *args) -> str:
    if refused := _refusal(config):
        return refused
    try:
        return getattr(backend, action)(config, *args)
    except ValueError as e:
        return f"Error: {e}"


# Files node can check without running them
NODE_CHECKED = (".js", ".cjs", ".mjs")
CHECK_SECONDS = 10


def syntax_problem(path: str, content: str) -> str | None:
    """What's wrong with a file's syntax (Python, JSON, and JavaScript when node is here), or None; for
    the model to fix before it builds, since a small mistake there crashes the app as it starts"""
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        try:
            compile(content, path, "exec")
        except SyntaxError as e:
            line = (e.text or "").rstrip()
            return f"{path}, line {e.lineno}: {e.msg}" + (f"\n{line}" if line else "")
        except ValueError:
            return None
    elif suffix == ".json":
        try:
            json.loads(content)
        except ValueError as e:
            return f"{path}: not valid JSON: {e}"
    elif suffix in NODE_CHECKED and (node := shutil.which("node")):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / Path(path).name
            file.write_text(content)
            try:
                done = subprocess.run([node, "--check", str(file)], capture_output=True, text=True, timeout=CHECK_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                return None
        if done.returncode == 0:
            return None
        # Its stack and version say nothing about the file
        lines = [line.replace(str(file), path) for line in done.stderr.splitlines()
                 if not line.startswith("    at ") and not line.startswith("Node.js v")]
        return "\n".join(lines).strip() or None
    return None


# How read_app_file numbers lines, and how a line copied from it is known
NUMBER = re.compile(r"^ *\d+\|(?: |$)")


def numbered(content: str, start: int = 1, end: int | None = None) -> str:
    """The lines from start to end (1-based, inclusive), each after its number, as read_app_file shows them"""
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    start = max(1, start)
    end = len(lines) if end is None else min(end, len(lines))
    width = len(str(end))
    return "\n".join(f"{n:>{width}}| {lines[n - 1]}" for n in range(start, end + 1))


def unnumbered(text: str) -> str:
    """Text copied from read_app_file without its line numbers: when every line that has anything
    starts with one, which code never does"""
    lines = text.split("\n")
    filled = [line for line in lines if line.strip()]
    if not filled or not all(NUMBER.match(line) for line in filled):
        return text
    return "\n".join(NUMBER.sub("", line, count=1) for line in lines)


def around(content: str, line: int, before: int = 4, after: int = 2) -> str:
    """The lines around one, numbered, to show where an error is"""
    total = content.count("\n") + (0 if content.endswith("\n") else 1)
    # "Unexpected end of input" is the line after the last: the file's end, then
    if not 1 <= line <= total + 1 or not total:
        return ""
    line = min(line, total)
    return numbered(content, line - before, line + after)


def error_line(problem: str) -> int | None:
    """The line an error says it's on: Node's `file.js:12`, Python's `line 12`"""
    found = re.search(r"\.(?:js|jsx|cjs|mjs|ts|tsx|css):(\d+)\b|\bline (\d+)\b", problem)
    return int(found.group(1) or found.group(2)) if found else None


def with_lines(problem: str, content: str) -> str:
    """An error with the lines it's about, numbered as read_app_file shows them"""
    if (line := error_line(problem)) and (shown := around(content, line)):
        return f"{problem}\nThe file around line {line}:\n{shown}"
    return problem


def checked(path: str, content: str, written: str) -> str:
    """What writing a file says, and its syntax error if it has one"""
    if problem := syntax_problem(path, content):
        return (f"{written}\nBut it has a syntax error now, so the app would crash as it starts: fix it before building.\n"
                + with_lines(problem, content))
    return written


def loose_spans(current: str, old_text: str) -> list[tuple[int, int]]:
    """Where old_text is in a file when spaces and tabs at the ends of lines (and carriage returns)
    don't count: small models often add or drop one there"""
    pattern = r"[ \t]*\r?\n".join(re.escape(line.rstrip(" \t\r")) for line in old_text.split("\n"))
    return [m.span() for m in re.finditer(pattern, current)]


def closest(current: str, old_text: str) -> str:
    """The stretch of the file most like old_text, to copy from, with its line numbers; "" without one"""
    lines = current.split("\n")
    size = old_text.count("\n") + 1
    if len(lines) > 5000:
        return ""
    best, score = 0, 0.0
    for start in range(max(1, len(lines) - size + 1)):
        matcher = difflib.SequenceMatcher(None, "\n".join(lines[start:start + size]), old_text, autojunk=False)
        if matcher.real_quick_ratio() <= score or matcher.quick_ratio() <= score:
            continue
        if (ratio := matcher.ratio()) > score:
            best, score = start, ratio
    if score < 0.5:
        return ""
    last = best + min(size, 40)
    return f"\nThe closest part of it is:\n{numbered(current, best + 1, last)}"


def cut(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… ({len(text) - limit:,} more characters not shown)"


@tool
def list_app_files(config: RunnableConfig = None) -> str:
    """List the app's files with their sizes, and say which build of them runs and how it's doing."""
    return _run(config, "files")


@tool
def read_app_file(path: str, start_line: int | None = None, end_line: int | None = None, config: RunnableConfig = None) -> str:
    """Read one of the app's files, each line after its number (`12| `), which isn't part of the file.

    Args:
        path: the file's path in the app, as list_app_files gives it, e.g. "frontend/src/App.jsx"
        start_line: the first line to show (from 1); leave out for the start of the file
        end_line: the last line to show; leave out for the end of the file
    """
    if refused := _refusal(config):
        return refused
    try:
        content = backend.read_text(config, path)
    except ValueError as e:
        return f"Error: {e}"
    total = len(content.split("\n")) - (1 if content.endswith("\n") else 0)
    if not content:
        return f"{path} is empty."
    start, end = start_line or 1, end_line or total
    if start > total:
        return f"Error: {path} has {total} lines, so there's no line {start}."
    head = f"{path}, lines {start}-{min(end, total)} of {total}:" if (start_line or end_line) else f"{path} ({total} lines):"
    return cut(f"{head}\n{numbered(content, start, end)}")


@tool
def write_app_file(path: str, content: str, config: RunnableConfig = None) -> str:
    """Write a whole file of the app: a new one, or one to rewrite completely. The page (frontend/) goes live by
    itself; the server (backend/, db/) at the next build_app.

    Args:
        path: the file's path in the app, e.g. "frontend/src/App.jsx" or "backend/notes.py"; folders are made as needed
        content: everything the file should contain
    """
    content = unnumbered(content)
    written = _run(config, "write", path, content)
    return written if written.startswith("Error:") else checked(path, content, written)


@tool
def edit_app_file(path: str, old_text: str, new_text: str, replace_all: bool = False, config: RunnableConfig = None) -> str:
    """Change part of one of the app's files: `old_text`, which must be in it exactly once (or every time,
    with replace_all), becomes `new_text`. The page (frontend/) goes live by itself; the server (backend/, db/)
    at the next build_app.

    Args:
        path: the file's path in the app
        old_text: the exact text to replace, with enough around it to be found only once
        new_text: what goes in its place
        replace_all: replace every time old_text is in the file
    """
    if refused := _refusal(config):
        return refused
    if not old_text:
        return "Error: old_text can't be empty: use write_app_file for a whole file"
    # Copied from read_app_file with its line numbers
    old_text, new_text = unnumbered(old_text), unnumbered(new_text)
    if old_text == new_text:
        return "Error: new_text is the same as old_text, so nothing would change: put the changed text in new_text"
    try:
        current = backend.read_text(config, path)
        count, note = current.count(old_text), ""
        if count:
            changed = current.replace(old_text, new_text)
        else:
            spans = loose_spans(current, old_text)
            count = len(spans)
            if count == 0:
                return (f"Error: that text isn't in {path}. Copy old_text exactly from the file, or read it again."
                        + closest(current, old_text))
            changed = current
            for begin, end in reversed(spans if replace_all else spans[:1]):
                changed = changed[:begin] + new_text + changed[end:]
            note = " (old_text matched once spaces at the ends of lines were left out)"
        if count > 1 and not replace_all:
            return f"Error: that text is in {path} {count} times: give more around it, or replace_all"
        return checked(path, changed, backend.write(config, path, changed) + note)
    except ValueError as e:
        return f"Error: {e}"


@tool
def delete_app_file(path: str, config: RunnableConfig = None) -> str:
    """Delete one of the app's files.

    Args:
        path: the file's path in the app
    """
    return _run(config, "delete", path)


@tool
def add_labb_helper(language: str, config: RunnableConfig = None) -> str:
    """Put labb's helper into the app's backend/: labb.py ("python") or labb.js ("node"), with ask, ask_agent, agents,
    search_documents, memories, recall and remember, for the app's server code to call labb as the user. It only
    works once the user lets the app use labb (list_app_files says whether they have).

    Args:
        language: "python" or "node"
    """
    return _run(config, "helper", language)


@tool
def build_app(config: RunnableConfig = None) -> str:
    """Build the app's server (backend/) and, if that works, run the new build in place of the old one, with its
    page built first if it changed; then check it answers. Gives back whether it worked and the end of the build's
    log. Can take a minute or more. The page alone doesn't need it: it goes live by itself."""
    return _run(config, "build")


@tool
def app_history(config: RunnableConfig = None) -> str:
    """The app's versions (one is saved at each build), newest first: each one's id, when, what it was, and which
    files it changed; and the files changed since the last one."""
    return _run(config, "history")


@tool
def app_version_diff(version: str, config: RunnableConfig = None) -> str:
    """What one version changed, as a diff; or "unsaved" for what changed since the last version.

    Args:
        version: a version's id (its start, as app_history lists it), or "unsaved"
    """
    return _run(config, "version_diff", version)


@tool
def restore_app_version(version: str, config: RunnableConfig = None) -> str:
    """Put the app's files back as they were in a version (saved as a new version, so it can be undone too; changes
    not built are kept in a version first). Build afterwards to run them.

    Args:
        version: the version's id (its start, as app_history lists it)
    """
    return _run(config, "restore", version)


@tool
def db_schema(config: RunnableConfig = None) -> str:
    """The app's database: its tables and views with their columns, the migrations applied and not yet, and its size."""
    return _run(config, "db_schema")


@tool
def db_query(sql: str, config: RunnableConfig = None) -> str:
    """Run one read-only SQL statement on the app's database and see its rows (at most 500). Changes go in migrations.

    Args:
        sql: one SELECT (or other read-only) statement
    """
    return _run(config, "db_query", sql)


@tool
def app_logs(lines: int = 80, config: RunnableConfig = None) -> str:
    """What the running app wrote lately (its output and errors), newest last.

    Args:
        lines: how many of the last lines, up to 500
    """
    return _run(config, "logs", max(1, min(int(lines), 500)))


@tool
def look_at_app(path: str = "/", config: RunnableConfig = None) -> str:
    """Fetch a page or API path of the running app, as a browser would, and give back its status and
    what it sent (text only, cut if long). For a page, it waits for the page's build first, and says if it failed.

    Args:
        path: the path in the app, starting with "/", e.g. "/" or "/api/items?limit=5"
    """
    return _run(config, "look", path)


TOOLS = [list_app_files, read_app_file, write_app_file, edit_app_file, delete_app_file, add_labb_helper, build_app, app_logs,
         look_at_app, app_history, app_version_diff, restore_app_version, db_schema, db_query]
