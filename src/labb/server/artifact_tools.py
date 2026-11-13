"""The `artifacts` toolset's backend (tools/artifacts.py): the spawner, asked as the chat's user
(server/app_tools.call), builds the page; this server then publishes the site to labb-sites
(sites/), which only it can write to, with SITES_TOKEN. The spawner never reaches the sites.

Where people open a page is labb-sites' address: SITES_PUBLIC_URL, else this server's own host
name on SITES_PORT (SITES_HTTPS_PORT over https), worked out per chat in `public_base`."""

import os
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from labb.server import app_tools, apps
from labb.tools import apps as app_toolset
from labb.tools import artifacts as artifact_toolset

SITES_URL = os.getenv("SITES_URL", "").rstrip("/")
SITES_TOKEN = os.getenv("SITES_TOKEN", "")
# Or the file labb-sites writes its token to on its first start (a volume both share in Docker)
SITES_TOKEN_FILE = os.getenv("SITES_TOKEN_FILE", "")
SITES_PUBLIC_URL = os.getenv("SITES_PUBLIC_URL", "").rstrip("/")
SITES_PORT = os.getenv("SITES_PORT", "8300")
SITES_HTTPS_PORT = os.getenv("SITES_HTTPS_PORT", "8301")
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
# A build waits for the builder's image the first time (minutes), then takes seconds
BUILD_TIMEOUT = 600
# Where a build error says it is: TypeScript's `app/page.tsx(4,64)`, Turbopack's `./app/page.tsx:2:48`
PLACE = re.compile(r"(?:^|[\s(])\.?/?((?:app|components|lib|data|examples)/[\w./\[\]-]+\.(?:tsx?|jsx?|css|json))(?:\((\d+),\d+\)|:(\d+):\d+)", re.M)


def enabled() -> bool:
    return bool(apps.SPAWNER_URL and SITES_URL)


def token() -> str:
    """Read on every call: labb-sites may have made its file after this server started"""
    if SITES_TOKEN or not SITES_TOKEN_FILE:
        return SITES_TOKEN
    try:
        return Path(SITES_TOKEN_FILE).read_text().strip()
    except OSError:
        return ""


def public_base(scheme: str, host: str) -> str:
    """Where labb-sites is for someone who reached this server at scheme://host"""
    if SITES_PUBLIC_URL:
        return SITES_PUBLIC_URL
    hostname = urlsplit(f"//{host}").hostname or "localhost"
    if ":" in hostname:
        hostname = f"[{hostname}]"
    return f"{scheme}://{hostname}:{SITES_HTTPS_PORT if scheme == 'https' else SITES_PORT}"


def server_base() -> str:
    """Where labb-sites is, without a request to go by (a workflow's page): SITES_PUBLIC_URL, else
    LABB_PUBLIC_URL's host on the sites' port, else localhost's"""
    from labb.server import connections
    if SITES_PUBLIC_URL or not connections.PUBLIC_URL:
        return public_base("http", "localhost")
    parts = urlsplit(connections.PUBLIC_URL)
    return public_base(parts.scheme or "http", parts.netloc)


def sites(method: str, path: str, **kwargs) -> httpx.Response:
    """labb-sites' upload service; what it refuses as ValueError"""
    if not SITES_URL:
        raise ValueError("this server can't publish pages (SITES_URL isn't set)")
    headers = {"Authorization": f"Bearer {key}"} if (key := token()) else {}
    try:
        response = httpx.request(method, f"{SITES_URL}{path}", headers=headers, timeout=120, **kwargs)
    except httpx.HTTPError as e:
        raise ValueError(f"couldn't reach labb-sites: {e}")
    if response.status_code >= 400 and response.status_code != 404:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise ValueError(f"labb-sites refused it: {str(detail)[:500]}")
    return response


def _who(config) -> tuple[str, str | None, str]:
    configurable = (config or {}).get("configurable") or {}
    return configurable["user_id"], configurable.get("thread_id"), configurable.get("sites_base") or public_base("http", "localhost")


def _checked(name: str) -> str:
    if not NAME.match(name or ""):
        raise ValueError(f"{name!r} isn't an artifact's name: list_artifacts names them")
    return name


def _file(name: str, path: str) -> str:
    return f"/artifacts/{_checked(name)}/files/{quote(path)}"


def url_of(base: str, name: str) -> str:
    return f"{base}/artifacts/{name}/"


def with_file_lines(user_id: str, name: str, error: str) -> str:
    """A build error with the lines of the file it names, numbered"""
    found = PLACE.search(error)
    if not found:
        return error
    path, line = found.group(1), int(found.group(2) or found.group(3))
    try:
        content = app_tools.call("GET", _file(name, path), user_id).json()["content"]
    except (ValueError, KeyError):
        return error
    if shown := app_toolset.around(content, line):
        return f"{error}\n{path} around line {line}:\n{shown}"
    return error


def remove_all(user_id: str, thread_id: str):
    """The artifacts a chat made, gone with it: their files and their published sites"""
    try:
        made = app_tools.call("GET", "/artifacts", user_id, params={"thread": thread_id}).json()
    except ValueError:
        return
    for artifact in made:
        try:
            app_tools.call("DELETE", f"/artifacts/{_checked(artifact['name'])}", user_id)
            sites("DELETE", f"/artifacts/{artifact['name']}")
        except ValueError:
            pass


class Backend:
    def make(self, config, title: str) -> str:
        user_id, thread_id, _ = _who(config)
        made = app_tools.call("POST", "/artifacts", user_id, json={"owner": user_id, "thread": thread_id, "title": title}).json()
        listed = app_tools.call("GET", f"/artifacts/{made['name']}/files", user_id).json()
        return (f"Made the artifact {made['name']!r} from the template. Its files: "
                + ", ".join(f["path"] for f in listed if not f["path"].startswith("examples/"))
                + ", and examples/ (a dashboard, diagrams and a form to copy from).\n"
                "Next: read AGENTS.md, write the page in app/page.tsx, then publish_artifact.")

    def listing(self, config) -> str:
        user_id, thread_id, base = _who(config)
        made = app_tools.call("GET", "/artifacts", user_id, params={"thread": thread_id} if thread_id else {}).json()
        if not made:
            return "No web pages made in this chat yet: new_artifact makes one."
        lines = []
        for artifact in made:
            build = artifact.get("build") or {}
            state = (f"published at {url_of(base, artifact['name'])}" if build.get("ok") else
                     "its last build failed" if build.get("ok") is False else "not published yet")
            lines.append(f"{artifact['name']}: {artifact['title']} ({state})")
        return "\n".join(lines)

    def files(self, config, name: str) -> str:
        user_id, _, _ = _who(config)
        listed = app_tools.call("GET", f"/artifacts/{_checked(name)}/files", user_id).json()
        return "\n".join(f"{f['path']}  ({f['size']:,} bytes)" for f in listed) or "(no files)"

    def read_text(self, config, name: str, path: str) -> str:
        user_id, _, _ = _who(config)
        return app_tools.call("GET", _file(name, path), user_id).json()["content"]

    def write(self, config, name: str, path: str, content: str) -> str:
        user_id, _, _ = _who(config)
        written = app_tools.call("PUT", _file(name, path), user_id, json={"content": content}).json()
        return f"{'Made' if written.get('created') else 'Wrote'} {path} ({written['size']:,} bytes). Publish to see it."

    def delete(self, config, name: str, path: str) -> str:
        user_id, _, _ = _who(config)
        app_tools.call("DELETE", _file(name, path), user_id)
        return f"Deleted {path}."

    def publish(self, config, name: str) -> str:
        user_id, _, base = _who(config)
        built = app_tools.call("POST", f"/artifacts/{_checked(name)}/build", user_id, timeout=BUILD_TIMEOUT).json()
        if not built["ok"]:
            return ("Error: it didn't build, so nothing was published (the link still shows the last good build, if any). "
                    "Fix this and publish again:\n" + with_file_lines(user_id, name, built["error"] or "the build failed"))
        site = app_tools.call("GET", f"/artifacts/{name}/site", user_id).content
        sites("PUT", f"/artifacts/{name}", content=site)
        return f"Published at {url_of(base, name)}\nGive the user this link."


artifact_toolset.backend = Backend() if enabled() else None
