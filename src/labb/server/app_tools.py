"""The `apps` toolset's backend (tools/apps.py): the spawner, asked as the chat's user about the
chat's app, so a tool only ever reaches that person's own app. Tools run in the reply's thread,
so these calls wait (a plain HTTP client, not the server's async one)."""

import re
import time
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from labb.server import apps
from labb.tools import apps as app_toolset

# A build can take minutes (its base image, packages)
BUILD_TIMEOUT = 660
LOG_LINES_BACK = 40
# How far back app_logs and look_at_app look for the latest error, and how much before it they show
ERROR_SEARCH_LINES = 300
BEFORE_ERROR = 5
# A line that says what went wrong: Node's and Python's errors, and Python's tracebacks
ERROR_LINE = re.compile(r"\b(\w*Error|\w*Exception|Traceback \(most recent call last\))\b")
# After a build that runs: how often, and how far apart, to ask it before saying it doesn't answer
ANSWER_TRIES, ANSWER_WAIT = 5, 1.0
# What a write says next, by where the file is: the page goes live by itself, the server at a build
NEXT = {"page": "The page builds and goes live by itself in a few seconds: check it with look_at_app.",
        "server": "Build the app (build_app) to run it."}


def part_of(path: str) -> str | None:
    """Which part of the app a file is: its page (frontend/), its server (backend/, db/), or neither"""
    if path.startswith("frontend/"):
        return "page"
    if path.startswith(("backend/", "db/")) or path == "Dockerfile":
        return "server"
    return None


def latest_error(lines: list[str]) -> str:
    """The latest error in the lines, with the few before it (where Node says the file and line), or """""
    found = next((i for i in range(len(lines) - 1, -1, -1) if ERROR_LINE.search(lines[i])), None)
    return "" if found is None else "\n".join(lines[max(0, found - BEFORE_ERROR):found + 1])


# Where an error is in the app's own files: a server's (/app/… in its container: Python's `File "/app/main.py",
# line 9`, Node's `/app/server.js:12`), or the page's build's (`src/App.jsx:3:5`)
SERVER_PLACE = re.compile(r"/app/([\w./-]+\.py)\", line (\d+)|/app/([\w./-]+\.(?:js|cjs|mjs|py))(?::(\d+))?")
PAGE_PLACE = re.compile(r"(?<![\w/])(?:frontend/)?(src/[\w./-]+\.(?:jsx|js|tsx|ts|mjs|css)):(\d+)")


def where_in_app(error: str) -> tuple[str, int | None, bool] | None:
    """(the file, its line, whether it's the page's) an error is about: for Python, the last of the app's
    frames (where it went wrong), else the first place named"""
    server = list(SERVER_PLACE.finditer(error))
    python = [m for m in server if m.group(1)]
    if python:
        return python[-1].group(1), int(python[-1].group(2)), False
    if server:
        first = server[0]
        return first.group(3), int(first.group(4)) if first.group(4) else app_toolset.error_line(error), False
    if page := PAGE_PLACE.search(error):
        return page.group(1), int(page.group(2)), True
    return None


def _who(config) -> tuple[str, str]:
    configurable = (config or {}).get("configurable") or {}
    return configurable["app"]["id"], configurable["user_id"]


def call(method: str, path: str, user_id: str, timeout: float = 60, **kwargs) -> httpx.Response:
    """The spawner, as `user_id`; what it refuses as ValueError, said for the model"""
    if not apps.SPAWNER_URL:
        raise ValueError("this server can't run apps (SPAWNER_URL isn't set)")
    headers = {"Authorization": f"Bearer {key}"} if (key := apps.token()) else {}
    params = {"owner": user_id, **kwargs.pop("params", {})}
    try:
        response = httpx.request(method, f"{apps.SPAWNER_URL}{path}", params=params, headers=headers, timeout=timeout, **kwargs)
    except httpx.HTTPError as e:
        raise ValueError(f"couldn't reach the spawner: {e}")
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise ValueError(str(detail)[:1000])
    return response


def owned(app_id: str, user_id: str) -> dict | None:
    """The app, if it's this user's"""
    try:
        return call("GET", f"/apps/{apps.checked(app_id)}", user_id).json()
    except (ValueError, HTTPException):
        return None


def _file(app_id: str, path: str) -> str:
    return f"/apps/{app_id}/files/{quote(path)}"


class Backend:
    def files(self, config) -> str:
        app_id, user_id = _who(config)
        app = call("GET", f"/apps/{app_id}", user_id).json()
        listed = call("GET", f"/apps/{app_id}/files", user_id).json()
        state = app["status"] + (f" ({app['detail']})" if app.get("detail") else "")
        lines = [f"App {app['name']!r}: build {app['build']} runs, {state}."]
        if not app.get("legacy"):
            page = self._page(app_id, user_id)
            if page and page.get("page"):
                how = ("its latest build failed (look_at_app says why), so the one before is what's served" if page.get("ok") is False
                       else "it's building now" if page.get("pending") else "it's built and live")
                lines.append(f"Its page (frontend/) builds and goes live by itself after each change: {how}.")
            lines.append("Its server is backend/ (only its /api/), which build_app builds and runs." if app.get("server")
                         else "It has no server (no backend/): it's its page only.")
        if app.get("legacy"):
            lines.append("It was made before apps had files of their own, so it can't be changed: the user can make a new one.")
        lines += [f"{f['path']}  ({f['size']:,} bytes)" for f in listed] or ["(no files)"]
        if not app.get("legacy"):
            found = call("GET", f"/apps/{app_id}/settings", user_id).json()
            names = [v["name"] + (" (secret)" if v["secret"] else "") for v in found["variables"]]
            lines.append(f"Its environment variables, set by the user on its page: {', '.join(names) if names else 'none'}.")
            lines.append(f"Its data in /data: {found['data']['size']:,} of {found['data']['limit']:,} bytes.")
            lines.append("It has a database (DATABASE_URL in its environment): see db_schema." if app.get("database")
                         else "It has no database (the user can turn one on, on its page).")
            from labb.server import app_keys
            key = app_keys.current(app_id, user_id)
            if key and key["scopes"]:
                lines.append(f"It may use labb, as the user ({', '.join(key['scopes'])}): LABB_URL and LABB_API_KEY are in its "
                             "environment. Call it from the app's server with the starters' labb.py or labb.js.")
            else:
                lines.append("It may not use labb (the user turns that on, on its page: Settings → Use labb).")
        return "\n".join(lines)

    def read_text(self, config, path: str) -> str:
        app_id, user_id = _who(config)
        return call("GET", _file(app_id, path), user_id).json()["content"]

    def write(self, config, path: str, content: str) -> str:
        app_id, user_id = _who(config)
        written = call("PUT", _file(app_id, path), user_id, json={"content": content}).json()
        return f"Wrote {written['path']} ({written['size']:,} bytes). {NEXT.get(part_of(path), '')}".strip()

    def delete(self, config, path: str) -> str:
        app_id, user_id = _who(config)
        call("DELETE", _file(app_id, path), user_id)
        return f"Deleted {path}. {NEXT.get(part_of(path), '')}".strip()

    def _page(self, app_id: str, user_id: str, wait: bool = False) -> dict | None:
        """Its page's last build (waiting for one that's pending, with `wait`), or None if it can't say"""
        try:
            return call("GET", f"/apps/{app_id}/page", user_id, timeout=BUILD_TIMEOUT,
                        params={"wait": "true" if wait else "false"}).json()
        except ValueError:
            return None

    def _page_failed(self, app_id: str, user_id: str, error: str) -> str:
        return (f"The page didn't build, so the last good one is still served (the user sees that one): "
                f"{self._with_file_lines(app_id, user_id, error)}\nFix that, and the page builds again by itself.")

    def _not_answering(self, app: dict, page: bool) -> str | None:
        """What's wrong when a build that runs doesn't answer: its page at /, its server at /api/health
        (any answer but a 5xx will do, as some servers have no such path)"""
        paths = (["/"] if page else []) + (["/api/health"] if app.get("server") else [])
        problem = None
        for attempt in range(ANSWER_TRIES):
            if attempt:
                time.sleep(ANSWER_WAIT)
            problem = None
            for path in paths:
                try:
                    response = httpx.get(f"{app['upstream']}{path}", timeout=10, follow_redirects=False)
                except httpx.HTTPError as e:
                    problem = f"{path} didn't answer ({e})"
                    break
                if response.status_code >= 500:
                    problem = f"{path} answered {response.status_code} {response.reason_phrase}: {app_toolset.cut(response.text, 400)}"
                    break
            if problem is None:
                return None
        return problem

    def build(self, config) -> str:
        app_id, user_id = _who(config)
        built = call("POST", f"/apps/{app_id}/build", user_id, timeout=BUILD_TIMEOUT, params={"note": "by labb in its chat"}).json()
        page = built.get("page")
        server = built.get("server") or {"ok": built["ok"], "error": built["error"]}
        said = self._server_built(app_id, user_id, built, server, page)
        if page and not page["ok"]:
            said += "\n\n" + self._page_failed(app_id, user_id, page["error"] or "")
        return said

    def _server_built(self, app_id: str, user_id: str, built: dict, server: dict, page: dict | None) -> str:
        """What a build says about its server (and, for an app that's only its page, about it)"""
        log = built["log"].split("\n\nThe server:\n", 1)[-1] if page and not page["ok"] else built["log"]
        tail = "".join(log.splitlines(keepends=True)[-LOG_LINES_BACK:]).strip()
        if server["ok"]:
            number = built["app"]["build"]
            if problem := self._not_answering(built["app"], page is not None):
                why = f"Build {number} runs, but doesn't answer as it should: {problem}"
                if error := latest_error(self._log_lines(app_id, user_id, ERROR_SEARCH_LINES)):
                    why += f"\nIts latest error:\n{self._with_file_lines(app_id, user_id, error)}"
                return why + "\nFix that, then build again."
            if not built["app"].get("server") and page is not None:
                return f"Build {number} worked: the page is built and live (the user's page shows it). Check it with look_at_app."
            return (f"Build {number} worked: it started and runs now (the user's page shows it). "
                    f"Check it with look_at_app and app_logs.\nThe end of its log:\n{tail}")
        built = {**built, "error": server["error"]}
        if (built["error"] or "").startswith("It built, but"):
            # The build's log says nothing about it: what the app wrote does, and the lines it names
            error = self._with_file_lines(app_id, user_id, built["error"])
            back = built["app"]["build"]
            if built.get("before_crashing"):
                after = (f"Build {back}, the one before it, runs again, but it was crashing too, so app_logs shows build "
                         f"{back}'s errors, not this one's: go by the error above.")
            else:
                after = f"So the last build that worked (build {back}) runs again."
            return f"The build failed. {error}\n{after} Fix the error, then build again."
        return (f"The build failed: {built['error']}\nThe last good build (build {built['app']['build']}) still runs.\n"
                f"The end of its log:\n{tail}")

    def _with_file_lines(self, app_id: str, user_id: str, error: str) -> str:
        """An error with the lines of the app's file it names, when that's one file: a crash's (/app/server.js
        is backend/server.js in the files, as the starters' Dockerfiles copy backend/ to /app), or the
        page's build's (src/App.jsx:12:5 is frontend/src/App.jsx)"""
        found = where_in_app(error)
        if found is None:
            return error
        name, line, page = found
        try:
            paths = [f["path"] for f in call("GET", f"/apps/{app_id}/files", user_id).json()]
            matches = [p for p in paths if p == "frontend/" + name] if page else [p for p in paths if p == name or p.endswith("/" + name)]
            if len(matches) != 1:
                return error
            content = call("GET", _file(app_id, matches[0]), user_id).json()["content"]
        except ValueError:
            return error
        if line is None or not (shown := app_toolset.around(content, line)):
            return error
        return f"{error}\n{matches[0]} around line {line}:\n{shown}"

    def helper(self, config, language: str) -> str:
        app_id, user_id = _who(config)
        written = call("POST", f"/apps/{app_id}/labb-helper", user_id, params={"language": language}).json()
        return f"Wrote {written['path']} ({written['size']:,} bytes): import it in the app's server code. Build to see it run."

    def history(self, config) -> str:
        app_id, user_id = _who(config)
        found = call("GET", f"/apps/{app_id}/versions", user_id).json()
        lines = []
        if found["unsaved"]:
            lines.append(f"Changed since the last version (not built): {', '.join(found['unsaved'])}")
        for v in found["versions"][:30]:
            files = ", ".join(v["files"][:6]) + (f" and {len(v['files']) - 6} more" if len(v["files"]) > 6 else "")
            lines.append(f"{v['id'][:12]}  {v['at']}  {v['message']}  ({files or 'no files'})")
        return "\n".join(lines) or "No versions yet."

    def _version(self, app_id: str, user_id: str, version: str) -> str:
        """A version's whole id, from its start as app_history gives it"""
        version = version.strip().lower()
        if len(version) < 7:
            raise ValueError("give at least the first 7 characters of the version, as app_history lists it")
        matches = [v["id"] for v in call("GET", f"/apps/{app_id}/versions", user_id).json()["versions"] if v["id"].startswith(version)]
        if len(matches) != 1:
            raise ValueError(f"no single version starts with {version!r}: see app_history")
        return matches[0]

    def version_diff(self, config, version: str) -> str:
        app_id, user_id = _who(config)
        if version.strip().lower() in ("unsaved", "changes", ""):
            found = call("GET", f"/apps/{app_id}/versions/unsaved/diff", user_id).json()["diff"]
            return found or "Nothing changed since the last version."
        full = self._version(app_id, user_id, version)
        return call("GET", f"/apps/{app_id}/versions/{full}/diff", user_id).json()["diff"] or "(no changes)"

    def restore(self, config, version: str) -> str:
        app_id, user_id = _who(config)
        full = self._version(app_id, user_id, version)
        call("POST", f"/apps/{app_id}/versions/{full}/restore", user_id)
        return (f"The files are as they were in {full[:12]} again (saved as a new version; changes not built were kept in "
                "one before). Build the app to run them.")

    def db_schema(self, config) -> str:
        app_id, user_id = _who(config)
        found = call("GET", f"/apps/{app_id}/database", user_id).json()
        if not found.get("enabled"):
            return ("It has no database. The user turns one on, on its page (Settings → Database, or the Database tab); "
                    "then it gets DATABASE_URL." if found.get("available") else "This server has no databases for apps.")
        lines = [f"Database {found['name']}: {found['size']:,} of {found['limit']:,} bytes."]
        api = found.get("api") or {}
        if api.get("enabled"):
            users = call("GET", f"/apps/{app_id}/users", user_id).json()["total"]
            lines.append("Its API is on, at its port + 100: /rest/ for what migrations grant to anon and authenticated, "
                         f"/auth/ sign-in for its own users ({users:,} so far; new ones "
                         f"{'can' if api.get('signups') else 'can not'} sign up), /client.js for its pages.")
        else:
            lines.append("Its API is off (the user turns it on, on its Database tab). Migrations can still refer to "
                         "auth.users and auth.uid().")
        applied = [m["name"] for m in found["migrations"]]
        lines.append(f"Migrations applied: {', '.join(applied) or 'none'}" + (f"; not yet (build to apply): {', '.join(found['pending'])}" if found["pending"] else ""))
        for t in found["tables"]:
            columns = ", ".join(f"{c['name']} {c['type']}{'' if c['nullable'] else ' not null'}" for c in t["columns"])
            lines.append(f"{t['kind']} {t['schema']}.{t['name']} (~{t['rows']} rows): {columns}")
        return "\n".join(lines)

    def db_query(self, config, sql: str) -> str:
        app_id, user_id = _who(config)
        found = call("POST", f"/apps/{app_id}/database/query", user_id, json={"sql": sql, "write": False}).json()
        if not found["columns"]:
            return f"Done ({found['rowcount']} rows)."
        rows = [" | ".join(found["columns"])] + [" | ".join("NULL" if v is None else str(v) for v in r) for r in found["rows"]]
        return app_toolset.cut("\n".join(rows) + ("\n… (more rows not shown)" if found["truncated"] else ""))

    def _log_lines(self, app_id: str, user_id: str, lines: int) -> list[str]:
        return call("GET", f"/apps/{app_id}/logs", user_id, params={"tail": lines}).json()["lines"].splitlines()

    def logs(self, config, lines: int) -> str:
        app_id, user_id = _who(config)
        found = self._log_lines(app_id, user_id, max(lines, ERROR_SEARCH_LINES))
        shown = found[-lines:]
        if not "\n".join(shown).strip():
            return "(nothing written yet)"
        text = "\n".join(shown)
        # Node writes an error before its stack, so the last lines can be only the stack
        if not any(ERROR_LINE.search(line) for line in shown) and (error := latest_error(found[:-lines])):
            text = f"The latest error, from before these lines:\n{error}\n…\nThe last {len(shown)} lines:\n{text}"
        return app_toolset.cut(text)

    def look(self, config, path: str) -> str:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError('the path has to start with one "/", e.g. "/" or "/api/items"')
        app_id, user_id = _who(config)
        app = call("GET", f"/apps/{app_id}", user_id).json()
        # A page that's being built is waited for, and one that failed says why
        failed = ""
        if not path.startswith("/api") and not app.get("legacy"):
            page = self._page(app_id, user_id, wait=True)
            if page and page.get("page") and page.get("ok") is False:
                failed = self._page_failed(app_id, user_id, page.get("error") or "") + "\n\nWhat's served now:\n"
        if app["status"] != "running":
            why = f"the app isn't running ({app['status']}{': ' + app['detail'] if app.get('detail') else ''})"
            if error := latest_error(self._log_lines(app_id, user_id, ERROR_SEARCH_LINES)):
                why += f". Its latest error:\n{error}"
            raise ValueError(why)
        try:
            response = httpx.get(f"{app['upstream']}{path}", timeout=20, follow_redirects=False)
        except httpx.HTTPError as e:
            raise ValueError(f"the app didn't answer: {e}. Look at app_logs")
        kind = response.headers.get("content-type", "")
        head = f"{response.status_code} {response.reason_phrase} · {kind or 'no content type'}"
        if location := response.headers.get("location"):
            head += f" · goes to {location}"
        if kind and not any(t in kind for t in ("text", "json", "javascript", "xml", "svg")):
            return f"{failed}{head}\n({len(response.content):,} bytes, not text)"
        return f"{failed}{head}\n\n{app_toolset.cut(response.text)}"


app_toolset.backend = Backend()


# What a fresh start carries over from the session it leaves: the user's last messages, each cut
FRESH_ASKS, FRESH_ASK_CHARS, FRESH_ERROR_CHARS = 4, 400, 1200


def fresh_start(app_id: str, user_id: str, thread_id: str) -> str:
    """The first message of a session that starts again from a stuck one: what the user asked
    there, the steps left, and where the app is now, but none of what the model concluded"""
    from labb.agent import chat_agent
    from labb.tools import todo

    app = call("GET", f"/apps/{app_id}", user_id).json()
    messages = chat_agent.get_state({"configurable": {"thread_id": thread_id}}).values.get("messages", [])
    asked = [m.text.strip() for m in messages if m.type == "human" and m.text.strip()]
    # The first (what it's for) and the latest ones
    picked = asked[:1] + [a for a in asked[1:][-(FRESH_ASKS - 1):] if a != asked[0]] if asked else []
    lines = ["Let's start fresh on this app. An earlier session got stuck going round in circles, so don't go by what "
             "it concluded or by notes it wrote: look at the files as they are now."]
    if picked:
        lines.append("What I asked for there:\n" + "\n".join(
            "- " + (a if len(a) <= FRESH_ASK_CHARS else a[:FRESH_ASK_CHARS] + "…").replace("\n", " ") for a in picked))
    steps = todo.open_items(todo.items_for({"configurable": {"thread_id": thread_id, "user_id": user_id}}))
    if steps:
        lines.append("Steps it hadn't finished:\n" + "\n".join(f"- {step['task']}" for step in steps))
    state = app["status"] + (f" ({app['detail']})" if app.get("detail") else "")
    now = f"Where the app is now: build {app['build']} runs, {state}."
    build = call("GET", f"/apps/{app_id}/build", user_id).json()
    error = ""
    if build.get("ok") is False and build.get("error"):
        error = f"Its last build (build {build['build']}) failed: {build['error']}"
    elif app["status"] != "running" or app.get("detail"):
        if found := latest_error(Backend()._log_lines(app_id, user_id, ERROR_SEARCH_LINES)):
            error = f"Its latest error:\n{found}"
    if error:
        # Node's stack and version say nothing about the app: the lines of its file do
        kept = [line for line in error.splitlines() if not line.lstrip().startswith("at ") and not line.startswith("Node.js v")]
        error = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
        error = error if len(error) <= FRESH_ERROR_CHARS else error[:FRESH_ERROR_CHARS] + "…"
        now += "\n" + Backend()._with_file_lines(app_id, user_id, error)
        lines.append(now)
        lines.append("Start with that error: read the file it names around the line it names, fix it, build, and check "
                     "the app with look_at_app. Then go on with what I asked for.")
    else:
        lines.append(now)
        lines.append("Check how it works now with look_at_app, then go on with what I asked for.")
    return "\n\n".join(lines)
