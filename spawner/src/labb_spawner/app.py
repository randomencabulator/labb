"""labb-spawner: starts and stops people's apps, built from each app's own source (a folder made
from a starter: see sources.py), and runs each as two containers:
- its front, `labb-app-<id>` (front/: Caddy, the same image for every app), on the app's port: it
  serves the app's page, built from frontend/ apart from everything else (pages.py: a change there
  builds and swaps it in by itself), and passes `/api/…` to its server;
- its server, `labb-app-<id>-server` (label `labb.app-server`), built from backend/Dockerfile as
  `labb-app-<id>:<build>`, on the apps' network only; an app with only a page has none. An app from
  before fronts (a Dockerfile at its top, serving its page too) is the same, with that Dockerfile.

`POST /apps {"owner", "name", "starter", "choices"}` makes one, builds it and starts it; `GET /apps?owner=` lists someone's,
`GET /apps/{id}?owner=` is one (with its memory use), `DELETE /apps/{id}?owner=` removes it.
`POST /apps/{id}/stop|start|restart?owner=` and `GET /apps/{id}/logs?owner=&tail=` are its
lifecycle. `GET /starters` lists what a new app can start from; `GET|PUT|DELETE
/apps/{id}/files[/{path}]?owner=` are its source; `POST /apps/{id}/build?owner=` builds it again
and, if that worked, runs the new image (a failed build leaves the last good one running), with
the page built first if it changed; `GET /apps/{id}/build?owner=` is the last build's result and
log, `GET /apps/{id}/page?owner=&wait=` the page's (waiting for one pending, with `wait`). `GET|PUT /apps/{id}/settings?owner=`
are its environment variables (secrets never given back) and how much data it keeps;
`GET /apps/{id}/data/download?owner=` is its data, as a tar. Changing its settings restarts it with them.
`GET /apps/{id}/versions?owner=` is its history (versions.py: a version at each build), with
`.../versions/{version}/diff`, `.../versions/unsaved/diff` and `POST .../versions/{version}/restore`;
`GET /apps/{id}/source/download?owner=&name=` is its files with that history as their .git, as a zip.
`GET|POST|DELETE /apps/{id}/database?owner=` is its database (database.py: its own, on the apps'
Postgres): what's in it, turning it on (it gets DATABASE_URL) or removing it; `.../database/rows`,
`.../database/query` and `.../database/dump` read it. Its `db/migrations/*.sql` are applied at each
build, after the image builds and before it runs: one that fails fails the build, and the last good
build runs on.
`POST|DELETE /apps/{id}/api?owner=` turn its API on or off (an app with a database): a container of its
own (backend/: PostgREST, behind a small gateway), at its port + APP_API_PORT_OFFSET, serving
`/rest/…` its tables as its migrations grant them to `anon` and `authenticated`, and `/auth/…` sign-in
for the app's own users (kept in its database's `auth` schema). `PATCH /apps/{id}/api?owner=
{"signups"}` says whether new users may sign up there; `GET /apps/{id}/users?owner=` lists them,
`PATCH|DELETE /apps/{id}/users/{user}?owner=` disables (`{"disabled"}`) or removes one.
`PUT /apps/{id}/labb?owner= {"env"}` is the server's: LABB_URL and LABB_API_KEY for an app that may use
labb (null: none), kept like a secret; the app restarts with them.

An app is `{"id", "name", "owner", "port", "status", "detail", "restarts", "created", "build",
"legacy", "upstream", "memory_limit", "page", "page_build"}`:
- `port`: its own port of APP_PORT_FIRST–APP_PORT_LAST, where people open it (http://labb.lan:9100).
  The app's front is published on it itself, straight to the browser.
- `upstream`: where the server checks it answers.
- `status`: Docker's (`running`, `exited`, `restarting`, `created`), the front's, or the server's
  when the front runs and it doesn't; `detail` says why one isn't running (stopped by its owner,
  idle, out of memory, crashed with an exit code), or that it was restarted after crashing.
- `server`: whether it has a server (backend/).
- `build`: which build of its source runs (image `labb-app-<id>:<build>`); `legacy` apps were made
  before apps had a source (build 0), and can't be built.

With APP_IDLE_MINUTES, an app whose container sent and got nothing for that long is stopped (its
network counters, so it counts however it's reached); opening it from labb starts it again. An app
whose data grows past APP_DATA_MB is stopped too, until some is cleared.

Each app's data (appdata.py) is a folder on the spawner's own volume, mounted at /data in its
container: the one place it can write that outlives a restart or a rebuild.

With https (the `https` compose profile, or APP_PUBLISH=0), Caddy has those ports instead, for TLS:
the containers aren't published, and Caddy reaches each on the APP_NETWORK by its alias
`labb-app-<port>` (see caddy/Caddyfile).

Everything about an app but its source is kept on its front, as labels: the containers labelled
`labb.app` are the apps (`labb.app.front` marks a front; one without is an app from before them,
which heal() makes a front and a server of). The server says whose app it is
(`owner`), and only sees and changes that person's.

Artifacts (artifacts.py) are pages the agent builds in a labb chat, in a folder of their own
(SPAWNER_DATA/artifacts/<name>/): `GET|POST /artifacts`, `GET|DELETE /artifacts/{name}`,
`GET|PUT|DELETE /artifacts/{name}/files[/{path}]` and `POST /artifacts/{name}/build`, which builds
the site in the artifacts' builder (no network, no capabilities); `GET /artifacts/{name}/site` is
the last good build as a .tar.gz, which labb publishes to labb-sites. Every route takes `owner`.

This is the only part of labb with the Docker socket, which is as good as root on the machine:
keep it unpublished, reachable only by the server, with a token (see compose.yaml).
"""

import hashlib
import os
import secrets
import socket
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import docker
import psycopg
import uvicorn
from docker.errors import APIError, ImageNotFound, NotFound
from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import re

from labb_spawner import appdata, artifacts, database, limits, pages, sources, system, versions

TOKEN_FILE = os.getenv("SPAWNER_TOKEN_FILE", "")
TOKEN_OWNER = int(os.getenv("SPAWNER_TOKEN_OWNER", "1000"))
# The Docker network apps are on, with the server (and Caddy): optional from a checkout
NETWORK = os.getenv("APP_NETWORK", "")
# Apps' ports, one each, first to last
FIRST_PORT = int(os.getenv("APP_PORT_FIRST", "9100"))
LAST_PORT = int(os.getenv("APP_PORT_LAST", "9199"))
# Whether containers are published on their ports: yes, unless Caddy has them (https)
_profiles = [p.strip() for p in os.getenv("LABB_PROFILES", "").split(",")]
PUBLISH = os.getenv("APP_PUBLISH", "0" if "https" in _profiles else "1").strip().lower() not in ("0", "false", "no", "")
# The address they're published on: 0.0.0.0 for the network, 127.0.0.1 for this machine only
BIND = os.getenv("APP_BIND", "0.0.0.0")
# Without a network: where the server finds a published app
UPSTREAM_HOST = os.getenv("APP_UPSTREAM_HOST", "127.0.0.1")
# The port the app serves on, inside its container
APP_PORT = 8080
MEMORY = os.getenv("APP_MEMORY", "256m")
CPUS = float(os.getenv("APP_CPUS", "0.5"))
PIDS = int(os.getenv("APP_PIDS", "128"))
MAX_NAME = 60
MAX_APPS = int(os.getenv("APP_MAX_PER_OWNER", "10"))
# Apps on this server in all; 0: as many as there are ports
MAX_TOTAL = int(os.getenv("APP_MAX_TOTAL", "0"))
# Stop apps unused this long (minutes); 0: never
IDLE_MINUTES = float(os.getenv("APP_IDLE_MINUTES", "0"))
IDLE_CHECK_SECONDS = 60
MAX_LOG_LINES = 2000
# A build: at most this long, this much memory for its steps, and this many at once on the server
BUILD_SECONDS = int(os.getenv("APP_BUILD_SECONDS", "600"))
BUILD_MEMORY = int(os.getenv("APP_BUILD_MEMORY_MB", "1024")) * 1024 * 1024
BUILDS_AT_ONCE = int(os.getenv("APP_BUILDS_AT_ONCE", "2"))
MAX_BUILD_LOG = 200_000
# Apps' APIs: their image (built from backend/ once), and their ports: the app's own + this
BACKEND_DIR = Path(os.getenv("SPAWNER_BACKEND", Path(__file__).resolve().parents[2] / "backend"))
API_OFFSET = int(os.getenv("APP_API_PORT_OFFSET", "100"))
API_LABEL = "labb.app-api"
# Which image an API container runs, so a spawner with a newer one renews it at the app's next build
API_IMAGE_LABEL = "labb.app-api.image"
# How long the spawner waits for a new API container to answer before saying it's on
API_WAIT_SECONDS = float(os.getenv("APP_API_WAIT_SECONDS", "20"))
API_MEMORY = os.getenv("APP_API_MEMORY", "128m")
# Every app's front (front/: Caddy), and the network fronts reach servers on, from a checkout without APP_NETWORK
FRONT_DIR = Path(os.getenv("SPAWNER_FRONT", Path(__file__).resolve().parents[2] / "front"))
FRONT_MEMORY = os.getenv("APP_FRONT_MEMORY", "64m")
LOCAL_NETWORK = "labb-apps"
SERVER_LABEL = "labb.app-server"
# Pages are built in a container of this image, with npm (pages.py)
PAGE_IMAGE = os.getenv("APP_PAGE_IMAGE", "node:24-alpine")
PAGE_COMMAND = "npm install --no-audit --no-fund --loglevel=error && npm run build"
# Node and esbuild use threads, which count as processes
PAGE_PIDS = 1024
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Exit codes of a container that was asked to stop (SIGTERM, then SIGKILL), not one that crashed
STOP_CODES = (0, 137, 143)
# How long a new build has to stay up before it counts as started: one that crashes in that time
# (a syntax error, a missing package) goes back to the build before, and the build fails
SETTLE_SECONDS = float(os.getenv("APP_SETTLE_SECONDS", "5"))
# How much of a crashed build's output the build's answer gives: from its start, with its last lines
CRASH_LINES, CRASH_TAIL = 40, 12

LABEL = "labb.app"
FRONT_LABEL = f"{LABEL}.front"
# Artifacts' builder (artifacts.py): its image is built from this folder (the template and build.sh)
ARTIFACT_DIR = Path(os.getenv("SPAWNER_ARTIFACTS", Path(__file__).resolve().parents[2] / "artifacts"))
ARTIFACT_BUILD_SECONDS = int(os.getenv("ARTIFACT_BUILD_SECONDS", "180"))
ARTIFACT_MEMORY = int(os.getenv("ARTIFACT_BUILD_MEMORY_MB", "2048")) * 1024 * 1024

def minutes(count: float) -> str:
    return f"{count:g} minute{'' if count == 1 else 's'}"


# Picking a port and starting the container on it go together, so two new apps can't pick the same one
_starting = threading.Lock()


class _Slots:
    """Builds at once, at most limits' builds_at_once, read as each one waits (an admin may change it)"""

    def __init__(self):
        self._busy = 0
        self._changed = threading.Condition()

    def __enter__(self):
        with self._changed:
            self._changed.wait_for(lambda: self._busy < limits.get("builds_at_once"), timeout=None)
            self._busy += 1

    def __exit__(self, *_):
        with self._changed:
            self._busy -= 1
            self._changed.notify_all()


_builds = _Slots()
# Apps being built now: one build per app at a time
_building: set[str] = set()
_building_lock = threading.Lock()
_docker: docker.DockerClient | None = None
# Who stopped an app, while this runs: "owner" or "idle" (Docker only keeps the exit code)
_stopped_by: dict[str, str] = {}
# An app's network bytes so far, and when they last changed: for idle stops
_traffic: dict[str, tuple[int, float]] = {}


@asynccontextmanager
async def lifespan(_):
    threading.Thread(target=watch_loop, daemon=True, name="watch").start()
    yield


app = FastAPI(lifespan=lifespan)


def client() -> docker.DockerClient:
    global _docker
    if _docker is None:
        try:
            _docker = docker.from_env()
        except docker.errors.DockerException as e:
            raise HTTPException(503, f"Can't reach Docker: {e}")
    return _docker


def load_token() -> str:
    """SPAWNER_TOKEN, or the one in TOKEN_FILE, made there if there's none yet; "" without either"""

    if token := os.getenv("SPAWNER_TOKEN", ""):
        return token
    if not TOKEN_FILE:
        return ""
    path = Path(TOKEN_FILE)
    try:
        if token := path.read_text().strip():
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(token + "\n")
    if os.geteuid() == 0:
        os.chown(partial, TOKEN_OWNER, TOKEN_OWNER)
    os.replace(partial, path)
    return token


TOKEN = load_token()
if not TOKEN:
    print("labb-spawner: no SPAWNER_TOKEN or SPAWNER_TOKEN_FILE, so anything that can reach it can start containers",
          file=sys.stderr)


def authorized(request: Request):
    if TOKEN and not secrets.compare_digest(request.headers.get("authorization", ""), f"Bearer {TOKEN}"):
        raise HTTPException(401, "A token is needed")


class NewApp(BaseModel):
    owner: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=MAX_NAME)
    starter: str = sources.DEFAULT_STARTER
    # The starter's choices, by name (color, style, font for the React frontend); the defaults otherwise
    choices: dict[str, str] = {}


class File(BaseModel):
    content: str


class Variable(BaseModel):
    name: str
    # None for a secret that keeps its value
    value: str | None = None
    secret: bool = False


class Settings(BaseModel):
    variables: list[Variable]


class LabbEnv(BaseModel):
    env: dict[str, str] | None = None


def describe(container) -> dict:
    labels = container.labels
    app_id, port = labels.get(f"{LABEL}.id", ""), int(labels.get(f"{LABEL}.port", "0"))
    server = server_container(app_id) if has_server(container) else None
    # The front says whether it's up; while it is, the server says how the app is
    shown = server if server is not None and container.status == "running" else container
    return {
        "id": app_id,
        "name": labels.get(f"{LABEL}.name", ""),
        "owner": labels.get(f"{LABEL}.owner", ""),
        "status": shown.status,
        "detail": detail(shown, app_id),
        "restarts": int(shown.attrs.get("RestartCount") or 0),
        "server": has_server(container),
        # Which page build it serves, so its page can show a new one at once
        "page": (page := pages.last(app_id)).get("served"),
        # How its page's last build went (labb's inbox tells the owner when one fails on its own)
        "page_build": {key: page.get(key) for key in ("build", "ok", "error")},
        "created": labels.get(f"{LABEL}.created", ""),
        "build": int(labels.get(f"{LABEL}.build", "0")),
        "legacy": f"{LABEL}.build" not in labels,
        "port": port,
        "upstream": upstream(container, app_id),
        "memory_limit": limits.get("app_memory_mb"),
        "database": appdata.database_password(app_id) is not None,
    }


def detail(container, app_id: str) -> str:
    """Why an app isn't running, or that it crashed and was started again"""
    state = container.attrs.get("State") or {}
    restarts = int(container.attrs.get("RestartCount") or 0)
    if container.status == "running":
        return f"Crashed and restarted {restarts} time{'s' if restarts != 1 else ''}" if restarts else ""
    if container.status == "restarting":
        return f"Keeps crashing (exit code {state.get('ExitCode')}): see its logs"
    if container.status == "created":
        return "Not started"
    by = _stopped_by.get(app_id)
    if by == "database":
        return f"Stopped: its database is over {limits.get('app_database_mb') // (1024 * 1024)} MB. Clear some, then start it"
    if by == "data":
        return f"Stopped: its data is over {limits.get('app_data_mb') // (1024 * 1024)} MB. Download it, or clear some, then start it"
    if by == "idle":
        return f"Stopped after {minutes(limits.get('app_idle_minutes'))} without use; opening it starts it again"
    if by == "owner":
        return "Stopped"
    if state.get("OOMKilled"):
        return f"Ran out of memory ({limits.get('app_memory_mb')})"
    code = state.get("ExitCode")
    return "Stopped" if code in STOP_CODES else f"Crashed (exit code {code}): see its logs"


def upstream(container, app_id: str) -> str:
    if NETWORK:
        return f"http://labb-app-{app_id}:{APP_PORT}"
    port = container.labels.get(f"{LABEL}.port", "")
    return f"http://{UPSTREAM_HOST}:{port}" if port else ""


def containers(**labels: str) -> list:
    filters = [LABEL] + [f"{LABEL}.{key}={value}" for key, value in labels.items()]
    return client().containers.list(all=True, filters={"label": filters})


def find(app_id: str, owner: str):
    found = containers(id=app_id, owner=owner)
    if not found:
        raise HTTPException(404, "No such app")
    return found[0]


def free_ports(taken: set[int]) -> list[int]:
    return [port for port in range(FIRST_PORT, LAST_PORT + 1) if port not in taken]


def spawner_image(folder: Path, name: str, what: str) -> str:
    """An image of the spawner's own, built from `folder` the first time (its tag is its files' hash,
    so a new spawner builds anew)"""
    digest = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(folder.iterdir()) if p.is_file())).hexdigest()[:12]
    tag = f"{name}:{digest}"
    try:
        client().images.get(tag)
    except ImageNotFound:
        error = None
        for chunk in client().api.build(path=str(folder), tag=tag, rm=True, forcerm=True, decode=True, timeout=limits.get("app_build_seconds")):
            if "error" in chunk:
                error = chunk["error"].strip()
        if error:
            raise HTTPException(502, f"{what} didn't build: {error}")
    return tag


def backend_image() -> str:
    """The API image, built from backend/"""
    return spawner_image(BACKEND_DIR, "labb-app-api", "The API's image")


def front_image() -> str:
    """Every app's front, built from front/"""
    return spawner_image(FRONT_DIR, "labb-app-front", "The apps' front image")


def is_front(container) -> bool:
    return container.labels.get(FRONT_LABEL) == "1"


def has_server(front) -> bool:
    return front.labels.get(f"{LABEL}.server") == "1"


def server_name(app_id: str) -> str:
    return f"labb-app-{app_id}-server"


def server_container(app_id: str):
    found = client().containers.list(all=True, filters={"label": [f"{SERVER_LABEL}={app_id}"]})
    return found[0] if found else None


def server_image(app_id: str, build: int) -> bool:
    """Whether that build has a server (an app with only a page has no image)"""
    try:
        client().images.get(image_of(app_id, build))
        return True
    except ImageNotFound:
        return False


def server_follows(app_id: str, action: str):
    """Its server stops, starts again or goes with its front"""
    held = server_container(app_id)
    if held is None:
        return
    try:
        if action == "stop":
            held.stop(timeout=10)
        elif action == "restart":
            held.restart(timeout=10)
        elif action == "remove":
            held.remove(force=True)
    except (APIError, NotFound) as e:
        print(f"labb-spawner: app {app_id}'s server didn't {action}: {e}", file=sys.stderr)


def stop_all(app_id: str, front, why: str):
    """The app stopped: its front, its server and its API"""
    front.stop(timeout=10)
    server_follows(app_id, "stop")
    api_follows(app_id, "stop")
    _stopped_by[app_id] = why
    _traffic.pop(app_id, None)


def api_container(app_id: str):
    found = client().containers.list(all=True, filters={"label": [f"{API_LABEL}={app_id}"]})
    return found[0] if found else None


def api_port(port: int) -> int:
    return port + API_OFFSET


def run_api(app_id: str, port: int):
    """Its API's container, as locked down as the app's, at its port + API_OFFSET"""
    held = appdata.api_secrets(app_id)
    where = {}
    if PUBLISH:
        where["ports"] = {f"{APP_PORT}/tcp": (BIND, api_port(port))}
    # On the apps' network like its front and server (also from a checkout, without APP_NETWORK):
    # heal() makes again an API that isn't on it
    ensure_network()
    where["network"] = apps_network()
    where["networking_config"] = {apps_network(): client().api.create_endpoint_config(aliases=[f"labb-app-api-{api_port(port)}"])}
    image = backend_image()
    return client().containers.run(
        image, name=f"labb-app-{app_id}-api", detach=True, labels={API_LABEL: app_id, API_IMAGE_LABEL: image},
        environment={"API_DB_URI": database.api_url(app_id, held["password"]), "API_JWT_SECRET": held["jwt_secret"],
                     "AUTH_DB_URI": database.auth_url(app_id, held["auth_password"]),
                     "AUTH_SIGNUPS": "1" if held.get("signups", True) else "0", "PORT": str(APP_PORT)},
        mem_limit=API_MEMORY, nano_cpus=int(limits.get("app_cpu_percent") * 1e9), pids_limit=limits.get("app_processes"),
        cap_drop=["ALL"], security_opt=["no-new-privileges:true"], read_only=True, tmpfs={"/tmp": "size=16m"},
        user="65534:65534", restart_policy={"Name": "unless-stopped"}, init=True, **where,
    )


HEALTH_CHECK = ("import sys, urllib.request\n"
                "try: sys.exit(urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status != 200)\n"
                "except Exception: sys.exit(1)")


def wait_for_api(held):
    """Until its new API container answers, asked inside it (the spawner isn't on the apps' network), so a
    page that calls it right after hears it; at most API_WAIT_SECONDS"""
    deadline = time.monotonic() + API_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            if held.exec_run(["python", "-I", "-c", HEALTH_CHECK], user="65534:65534").exit_code == 0:
                return
        except (APIError, NotFound):
            pass
        time.sleep(0.3)
    if API_WAIT_SECONDS:
        print(f"labb-spawner: {held.name} didn't answer in {API_WAIT_SECONDS:.0f}s", file=sys.stderr)


def api_follows(app_id: str, action: str):
    """Its API stops and goes with the app (it starts with it through heal, which makes it again if it must)"""
    held = api_container(app_id)
    if held is None:
        return
    try:
        if action == "stop":
            held.stop(timeout=10)
        elif action == "remove":
            held.remove(force=True)
    except (APIError, NotFound) as e:
        print(f"labb-spawner: app {app_id}'s API didn't {action}: {e}", file=sys.stderr)


# Changes to an app's API, one at a time per app: turning it on or off, making it again, heal()
_app_locks: dict[str, threading.RLock] = {}
_app_locks_lock = threading.Lock()


def app_lock(app_id: str) -> threading.RLock:
    with _app_locks_lock:
        return _app_locks.setdefault(app_id, threading.RLock())


def on_network(container) -> bool:
    """Whether a container is on the apps' network as it is now: one made again since (labb's compose
    project brought down and up, say) has another id, and a container on the old one is out of reach"""
    try:
        current = client().networks.get(apps_network()).id
    except NotFound:
        return False
    joined = (container.attrs.get("NetworkSettings") or {}).get("Networks") or {}
    return (joined.get(apps_network()) or {}).get("NetworkID") == current


def heal(app_id: str, container) -> list[str]:
    """The app and its API as they should be, after anything that left them otherwise (the server
    restarted, the apps' network made again, an API stopped while its app ran): a running app off
    the network runs again on it, and an app's API that's on is there, on the network, and running
    when the app is. What it did, to log."""
    with app_lock(app_id):
        return _heal(app_id, container)


def _heal(app_id: str, container) -> list[str]:
    done = []
    up = container.status in ("running", "restarting")
    legacy = f"{LABEL}.build" not in container.labels
    try:
        if not legacy and not is_front(container):
            container = to_front(app_id, container)
            done.append("made it a front and a server")
        if not legacy and has_server(container):
            held = server_container(app_id)
            if up and (not on_network(container) or (held is not None and not on_network(held))):
                ensure_network()
                container = replace(container, int(container.labels[f"{LABEL}.build"]))
                done.append("ran the app again on the apps' network")
            elif held is None:
                made = run_server(app_id, int(container.labels[f"{LABEL}.build"]), container.labels.get(f"{LABEL}.name", ""))
                if not up:
                    made.stop(timeout=10)
                done.append("made its missing server")
            elif up and held.status not in ("running", "restarting"):
                held.start()
                done.append("started its server")
        elif not legacy and up and not on_network(container):
            ensure_network()
            container = replace(container, int(container.labels[f"{LABEL}.build"]))
            done.append("ran the app again on the apps' network")
        if appdata.api_secrets(app_id) is None:
            return done
        held = api_container(app_id)
        if held is None or not on_network(held):
            if held is not None:
                held.remove(force=True)
            made = run_api(app_id, int(container.labels[f"{LABEL}.port"]))
            if up:
                wait_for_api(made)
            else:
                made.stop(timeout=10)
            done.append("made its API again" if held is not None else "made its missing API")
        elif up and held.status not in ("running", "restarting"):
            try:
                held.start()
            except APIError:
                # Its container can't start (what it was made with is gone): a new one
                held.remove(force=True)
                wait_for_api(run_api(app_id, int(container.labels[f"{LABEL}.port"])))
            done.append("started its API")
    except (APIError, NotFound, HTTPException) as e:
        print(f"labb-spawner: couldn't put app {app_id} right: {e}", file=sys.stderr)
    return done


def heal_all():
    """heal() each app, saying what it did"""
    for container in containers():
        app_id = container.labels.get(f"{LABEL}.id", "")
        # A build replaces its container as it ends, and puts it right then
        with _building_lock:
            if app_id in _building:
                continue
        if done := heal(app_id, container):
            print(f"labb-spawner: app {app_id}: {'; '.join(done)}", file=sys.stderr)


def reload_api(app_id: str):
    """Its API reads its tables again, after migrations"""
    if appdata.api_secrets(app_id) and (password := appdata.database_password(app_id)):
        try:
            database.reload_api(app_id, password)
        except (database.DatabaseError, psycopg.Error) as e:
            print(f"labb-spawner: app {app_id}'s API wasn't told to reload: {e}", file=sys.stderr)


def renew_api(app_id: str, container):
    """Its API's container made again (with its settings now, and this spawner's image), running if it was"""
    held = api_container(app_id)
    if held is None:
        return
    running = held.status in ("running", "restarting")
    held.remove(force=True)
    made = run_api(app_id, int(container.labels[f"{LABEL}.port"]))
    if not running:
        made.stop(timeout=10)
    else:
        wait_for_api(made)


def update_api(app_id: str, container):
    """After a build's migrations: an API from an older spawner (another image, or no sign-in yet) is
    renewed; otherwise it reads its tables again"""
    held, stored = api_container(app_id), appdata.api_secrets(app_id)
    if held is None or stored is None:
        return
    try:
        if "auth_password" not in stored:
            appdata.set_api_secrets(app_id, {**stored, **database.enable_api(app_id)})
        if "auth_password" not in stored or held.labels.get(API_IMAGE_LABEL) != backend_image():
            return renew_api(app_id, container)
    except (database.DatabaseError, psycopg.Error, APIError, HTTPException) as e:
        print(f"labb-spawner: app {app_id}'s API wasn't renewed: {e}", file=sys.stderr)
    reload_api(app_id)


def apps_network() -> str:
    """The network fronts reach their servers on (and the server and Caddy reach fronts on, with
    APP_NETWORK): APP_NETWORK, or one of the spawner's own from a checkout"""
    return NETWORK or LOCAL_NETWORK


def ensure_network():
    try:
        client().networks.get(apps_network())
    except NotFound:
        client().networks.create(apps_network(), driver="bridge")


def image_of(app_id: str, build: int) -> str:
    return f"labb-app-{app_id}:{build}"


_host_data: Path | None = None


def host_path(path: Path) -> str:
    """Where a path under SPAWNER_DATA is on the Docker host, to mount it in an app's container: in
    Docker, under the spawner's own volume (from its container's mounts); from a checkout, itself"""
    global _host_data
    if _host_data is None:
        found = sources.DATA.resolve()
        try:
            me = client().containers.get(socket.gethostname())
            for mount in me.attrs.get("Mounts") or []:
                if Path(mount.get("Destination", "")) == sources.DATA:
                    found = Path(mount["Source"])
        except (NotFound, APIError):
            pass
        _host_data = found
    return str(_host_data / path.resolve().relative_to(sources.DATA.resolve()))


LOCKED_DOWN = {"cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"], "read_only": True,
               "user": "65534:65534", "restart_policy": {"Name": "unless-stopped"}, "init": True}


def run_server(app_id: str, build: int, name: str):
    """The app's server, locked down: no capabilities, read-only but /tmp and its data at /data, an
    unprivileged user; its settings as its environment, with labb's own last. Only on the apps'
    network, where its front reaches it by its name."""
    data = appdata.ensure_data(app_id)
    return client().containers.run(
        image_of(app_id, build), name=server_name(app_id), detach=True,
        labels={SERVER_LABEL: app_id, f"{SERVER_LABEL}.build": str(build)},
        environment={**appdata.environment(app_id), "APP_NAME": name, "PORT": str(APP_PORT)},
        volumes={host_path(data): {"bind": "/data", "mode": "rw"}},
        mem_limit=limits.get("app_memory_mb"), nano_cpus=int(limits.get("app_cpu_percent") * 1e9), pids_limit=limits.get("app_processes"), tmpfs={"/tmp": "size=64m"},
        network=apps_network(), **LOCKED_DOWN,
    )


def run_front(app_id: str, labels: dict, port: int):
    """The app's front, as locked down: its built pages read-only at /srv, and nothing of the app's
    settings (they're its server's); on its port"""
    folder = pages.site(app_id)
    folder.mkdir(parents=True, exist_ok=True)
    return client().containers.run(
        front_image(), name=f"labb-app-{app_id}", detach=True, labels=labels,
        environment={"SERVER": f"{server_name(app_id)}:{APP_PORT}"},
        volumes={host_path(folder): {"bind": "/srv", "mode": "ro"}},
        mem_limit=FRONT_MEMORY, nano_cpus=int(limits.get("app_cpu_percent") * 1e9), pids_limit=limits.get("app_processes"), tmpfs={"/tmp": "size=16m"},
        **LOCKED_DOWN, **where(port),
    )


def where(port: int) -> dict:
    """Published on its port, and on the apps' network with the alias Caddy reaches it by"""
    found = {}
    if PUBLISH:
        found["ports"] = {f"{APP_PORT}/tcp": (BIND, port)}
    found["network"] = apps_network()
    found["networking_config"] = {apps_network(): client().api.create_endpoint_config(aliases=[f"labb-app-{port}"])}
    return found


def server_dockerfile(app_id: str) -> str | None:
    """What its server is built from: backend/Dockerfile, or a Dockerfile at the top (an app from
    before pages were built apart); None for an app that's only its page"""
    root = sources.source(app_id)
    return next((name for name in ("backend/Dockerfile", "Dockerfile") if (root / name).is_file()), None)


def build_image(app_id: str, number: int) -> tuple[bool, str, str | None]:
    """Build the app's server as labb-app-<id>:<number>: (it worked, its log, what went wrong). An app
    without one has nothing to build, which works."""
    lines, error = [], None
    dockerfile = server_dockerfile(app_id)
    if dockerfile is None:
        lines.append("It has no server (no backend/Dockerfile): it's its page only.\n")
    elif sources.size(app_id) > limits.get("app_source_mb"):
        error = f"The app's files are over {limits.get('app_source_mb') // 1_000_000} MB"
    else:
        with _builds:
            try:
                for chunk in client().api.build(
                    path=str(sources.source(app_id)), dockerfile=dockerfile, tag=image_of(app_id, number), rm=True,
                    forcerm=True, decode=True, labels={LABEL: "1", f"{LABEL}.id": app_id},
                    container_limits={"memory": limits.get("app_build_memory_mb")}, timeout=limits.get("app_build_seconds"),
                ):
                    if "stream" in chunk:
                        lines.append(chunk["stream"])
                    elif "status" in chunk and not chunk.get("progressDetail"):
                        lines.append(f"{chunk.get('id', '')} {chunk['status']}".strip() + "\n")
                    if "error" in chunk:
                        error = chunk["error"].strip()
            except Exception as e:  # Docker's errors, and the HTTP client's when the build takes too long
                error = f"The build stopped: {e}"
    if error:
        lines.append(f"\nThe build failed: {error}\n")
    log = "".join(lines)[-MAX_BUILD_LOG:]
    sources.save_build(app_id, number, error is None, log, error)
    return error is None, log, error


def build_page(app_id: str, work: Path) -> tuple[bool, str, str | None]:
    """pages.py's runner: `npm install` and `npm run build` in page-work/, in a container of PAGE_IMAGE
    with no capabilities, as the spawner's own user (so it can clear what the build leaves)"""
    box = None
    with _builds:
        try:
            box = client().containers.run(
                PAGE_IMAGE, ["sh", "-c", PAGE_COMMAND], detach=True, working_dir="/work",
                volumes={host_path(work): {"bind": "/work", "mode": "rw"}}, user=f"{os.getuid()}:{os.getgid()}",
                environment={"HOME": "/tmp", "npm_config_cache": "/work/.npm", "npm_config_update_notifier": "false",
                             "NO_COLOR": "1", "FORCE_COLOR": "0"},
                labels={f"{LABEL}.page-build": app_id}, mem_limit=limits.get("app_build_memory_mb"), pids_limit=PAGE_PIDS,
                cap_drop=["ALL"], security_opt=["no-new-privileges:true"], tmpfs={"/tmp": "size=256m"},
            )
            code = box.wait(timeout=limits.get("app_build_seconds")).get("StatusCode")
            log = box.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
        except Exception as e:  # Docker's errors, and the HTTP client's when the build takes too long
            return False, "", f"The page's build stopped: {e}"
        finally:
            if box is not None:
                try:
                    box.remove(force=True)
                except (APIError, NotFound):
                    pass
    # Rolldown colours its errors whatever it's told
    log = ANSI.sub("", log).replace("/work/", "frontend/")
    if code != 0:
        return False, log, page_error(log, code)
    return True, log, None


def page_error(log: str, code: int) -> str:
    """What a failed page build's log says went wrong: Vite's error (without its stack), else npm's,
    else its last lines; paths in it are in frontend/"""
    lines = log.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("error during build")), None)
    if start is not None:
        found = []
        for line in lines[start + 1:]:
            if line.lstrip().startswith("at ") or line.strip() == "}":
                break
            found.append(line.rstrip())
        said = "\n".join(found).strip()
        return f"`vite build` failed (paths are in frontend/):\n{said}" if said else f"`vite build` failed (exit code {code})"
    npm = [line for line in lines if line.startswith(("npm error", "npm ERR!"))]
    if npm:
        return "`npm install` failed (frontend/package.json):\n" + "\n".join(npm[:20])
    return f"frontend/'s build failed (exit code {code}):\n" + "\n".join(lines[-15:])


def artifact_image() -> str:
    """The artifacts' builder, built from ARTIFACT_DIR the first time (tagged by a hash of every
    file in it but the template's local install, so a changed template builds anew)"""
    skipped = {"node_modules", ".next", "out"}
    digest = hashlib.sha256()
    for path in sorted(ARTIFACT_DIR.rglob("*")):
        relative = path.relative_to(ARTIFACT_DIR)
        if path.is_file() and not skipped & set(relative.parts):
            digest.update(relative.as_posix().encode() + b"\0" + path.read_bytes())
    tag = f"labb-artifact-builder:{digest.hexdigest()[:12]}"
    try:
        client().images.get(tag)
    except ImageNotFound:
        error = None
        for chunk in client().api.build(path=str(ARTIFACT_DIR), tag=tag, rm=True, forcerm=True, decode=True, timeout=limits.get("app_build_seconds")):
            if "error" in chunk:
                error = chunk["error"].strip()
        if error:
            raise HTTPException(502, f"The artifacts' builder didn't build: {error}")
    return tag


def build_artifact(name: str, src: Path, out: Path) -> tuple[bool, str, str | None]:
    """artifacts.py's runner: `next build` in the builder image, its files read-only, no network, no
    capabilities, writing the site to `out`"""
    box = None
    image = artifact_image()
    with _builds:
        try:
            box = client().containers.run(
                image, detach=True, network_mode="none",
                volumes={host_path(src): {"bind": "/src", "mode": "ro"}, host_path(out): {"bind": "/out", "mode": "rw"}},
                environment={"ARTIFACT_BASE_PATH": f"/artifacts/{name}", "NO_COLOR": "1", "FORCE_COLOR": "0"},
                labels={f"{LABEL}.artifact-build": name}, mem_limit=limits.get("artifact_build_memory_mb"), pids_limit=PAGE_PIDS,
                cap_drop=["ALL"], security_opt=["no-new-privileges:true"], tmpfs={"/tmp": "size=256m"},
            )
            code = box.wait(timeout=limits.get("artifact_build_seconds")).get("StatusCode")
            log = box.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
        except Exception as e:  # Docker's errors, and the HTTP client's when the build takes too long
            return False, "", f"The build stopped: {e}"
        finally:
            if box is not None:
                try:
                    box.remove(force=True)
                except (APIError, NotFound):
                    pass
    log = ANSI.sub("", log).replace("/template/", "")
    if code != 0:
        return False, log, artifacts.next_error(log, code)
    return True, log, None


def page_changed(app_id: str):
    """frontend/ changed: its page builds again by itself"""
    if pages.has_page(app_id):
        pages.changed(app_id, build_page)


def remove_images(app_id: str, keep: set[int] = frozenset()):
    """The app's images but the builds in `keep`"""
    for image in client().images.list(filters={"label": f"{LABEL}.id={app_id}"}):
        for tag in image.tags:
            if tag.startswith(f"labb-app-{app_id}:") and int(tag.rsplit(":", 1)[1]) not in keep:
                try:
                    client().images.remove(tag, noprune=False)
                except (APIError, NotFound):
                    pass


def remove_named(*names: str):
    for name in names:
        try:
            client().containers.get(name).remove(force=True)
        except NotFound:
            pass


def start(owner: str, name: str, starter: str, choices: dict[str, str] | None = None) -> dict:
    ensure_network()
    app_id = uuid.uuid4().hex[:12]
    created = datetime.now(UTC).isoformat(timespec="seconds")
    sources.make(app_id, starter, name, choices)
    versions.save(app_id, f"Made from the {starter} starter")
    if pages.has_page(app_id):
        page = pages.ensure(app_id, build_page)
        if not page["ok"]:
            pages.forget(app_id)
            sources.remove(app_id)
            raise HTTPException(502, f"The {starter} starter's page didn't build: {page['error']}\n{tail_of(page['log'])}".strip())
    ok, log, error = build_image(app_id, 1)
    if not ok:
        pages.forget(app_id)
        sources.remove(app_id)
        raise HTTPException(502, f"The {starter} starter didn't build: {error}\n{tail_of(log)}".strip())
    server = server_image(app_id, 1)
    # Picking its port and starting it go together, so two new apps can't get the same one
    with _starting:
        for port in free_ports({describe(c)["port"] for c in containers()}):
            labels = {LABEL: "1", f"{LABEL}.id": app_id, f"{LABEL}.owner": owner, f"{LABEL}.name": name,
                      f"{LABEL}.port": str(port), f"{LABEL}.created": created, f"{LABEL}.build": "1",
                      FRONT_LABEL: "1", f"{LABEL}.server": "1" if server else "0"}
            try:
                container = run_front(app_id, labels, port)
            except APIError as e:
                # Something else on this machine has the port: the next one
                remove_named(f"labb-app-{app_id}")
                if "port is already allocated" in str(e) or "address already in use" in str(e):
                    continue
                raise HTTPException(502, f"Docker couldn't start the app: {e.explanation or e}")
            if server:
                try:
                    run_server(app_id, 1, name)
                except APIError as e:
                    remove_named(f"labb-app-{app_id}", server_name(app_id))
                    pages.forget(app_id)
                    sources.remove(app_id)
                    remove_images(app_id)
                    raise HTTPException(502, f"Docker couldn't start the app's server: {e.explanation or e}")
            container.reload()
            return describe(container)
    pages.forget(app_id)
    sources.remove(app_id)
    remove_images(app_id)
    raise HTTPException(503, f"No free port left between {FIRST_PORT} and {LAST_PORT}")


def tail_of(log: str, lines: int = 16) -> str:
    """The end of a build's log, but the line that says it failed"""
    return "".join(line for line in log.splitlines(keepends=True)[-lines:] if "build failed" not in line).strip()


def replace(container, number: int):
    """Run the app's build `number` in place of the one it runs (its front and its server, if that build
    has one), on the same port; the old one again if it can't"""
    labels = dict(container.labels)
    app_id, name, port = labels[f"{LABEL}.id"], labels[f"{LABEL}.name"], int(labels[f"{LABEL}.port"])
    before = int(labels.get(f"{LABEL}.build", "0"))
    server = server_image(app_id, number)
    container.remove(force=True)
    remove_named(server_name(app_id))
    try:
        if server:
            run_server(app_id, number, name)
        fresh = run_front(app_id, {**labels, f"{LABEL}.build": str(number), FRONT_LABEL: "1",
                                   f"{LABEL}.server": "1" if server else "0"}, port)
    except APIError as e:
        remove_named(f"labb-app-{app_id}", server_name(app_id))
        if labels.get(f"{LABEL}.server") == "1" and server_image(app_id, before):
            run_server(app_id, before, name)
        run_front(app_id, labels, port)
        raise HTTPException(502, f"The new build didn't start, so the last one runs again: {e.explanation or e}")
    _stopped_by.pop(app_id, None)
    _traffic.pop(app_id, None)
    # This build and the one before, to go back to
    remove_images(app_id, keep={number, before})
    fresh.reload()
    return fresh


def to_front(app_id: str, old):
    """An app from before fronts (one container, serving its page too) as a front and a server: the
    same build as its server, and its page built apart, which the front serves once it's there (until
    then, the server answers everything as it did). A stopped one stays stopped."""
    labels = dict(old.labels)
    build, up = int(labels[f"{LABEL}.build"]), old.status in ("running", "restarting")
    if not server_image(app_id, build):
        raise NotFound(f"its image {image_of(app_id, build)} is gone")
    old.remove(force=True)
    remove_named(server_name(app_id))
    server = run_server(app_id, build, labels.get(f"{LABEL}.name", ""))
    front = run_front(app_id, {**labels, FRONT_LABEL: "1", f"{LABEL}.server": "1"}, int(labels[f"{LABEL}.port"]))
    if not up:
        front.stop(timeout=10)
        server.stop(timeout=10)
    page_changed(app_id)
    front.reload()
    return front


def crashed(container) -> str | None:
    """Watches a new container for SETTLE_SECONDS: None while it stays up, else the start of what it
    wrote before it went down (where an error says what it is; the stack after it says less)"""
    deadline = time.monotonic() + SETTLE_SECONDS
    while True:
        container.reload()
        if container.status in ("restarting", "exited", "dead") or int(container.attrs.get("RestartCount") or 0):
            break
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.25)
    try:
        output = container.logs(tail=CRASH_LINES * 4, timestamps=False).decode("utf-8", errors="replace")
    except (APIError, NotFound):
        output = ""
    state = container.attrs.get("State") or {}
    lines = output.splitlines()
    # Its start (Node says what's wrong first) and its end (Python says it last)
    if len(lines) > CRASH_LINES:
        shown = "\n".join(lines[:CRASH_LINES - CRASH_TAIL] + [f"… ({len(lines) - CRASH_LINES} more lines)"] + lines[-CRASH_TAIL:])
    else:
        shown = "\n".join(lines)
    wrote = f"What it wrote:\n{shown.strip() or '(nothing)'}"
    if state.get("OOMKilled"):
        return f"It built, but crashed as it started: it ran out of memory ({limits.get('app_memory_mb')}). {wrote}"
    if state.get("ExitCode") == 0:
        # Nothing went wrong, it just ended: a server that never started listening
        return (f"It built, but its program ended as it started, without an error (exit code 0): an app's server has "
                f"to keep running, listening on port {APP_PORT}. {wrote}")
    return f"It built, but crashed as it started (exit code {state.get('ExitCode')}). {wrote}"


def traffic(container) -> int | None:
    """Bytes the container sent and got so far, or None when Docker can't say"""
    try:
        stats = container.stats(stream=False, one_shot=True)
    except (APIError, NotFound):
        return None
    networks = stats.get("networks") or {}
    return sum(n.get("rx_bytes", 0) + n.get("tx_bytes", 0) for n in networks.values()) if networks else None


def memory_used(*held) -> int | None:
    """What the containers use in all (the front and the server)"""
    found = None
    for container in held:
        try:
            used = (container.stats(stream=False, one_shot=True).get("memory_stats") or {}).get("usage")
        except (APIError, NotFound):
            continue
        if used is not None:
            found = (found or 0) + used
    return found


def idle_sweep(now: float) -> list[str]:
    """Stop running apps whose traffic hasn't changed for IDLE_MINUTES; the ids it stopped"""
    stopped = []
    for container in containers():
        app_id = container.labels.get(f"{LABEL}.id", "")
        if container.status != "running":
            _traffic.pop(app_id, None)
            continue
        seen = traffic(container)
        if seen is None:
            continue
        before = _traffic.get(app_id)
        if before is None or seen != before[0]:
            _traffic[app_id] = (seen, now)
        elif now - before[1] >= limits.get("app_idle_minutes") * 60:
            stop_all(app_id, container, "idle")
            stopped.append(app_id)
    return stopped


def data_sweep() -> list[str]:
    """Stop running apps whose data is over APP_DATA_MB; the ids it stopped"""
    stopped = []
    for container in containers():
        app_id = container.labels.get(f"{LABEL}.id", "")
        if container.status != "running":
            continue
        if appdata.data_size(app_id) > limits.get("app_data_mb"):
            stop_all(app_id, container, "data")
            stopped.append(app_id)
        elif database.available() and appdata.database_password(app_id) is not None:
            try:
                over = database.size(app_id) > limits.get("app_database_mb")
            except Exception as e:  # the apps' Postgres away for a moment: checked again next minute
                print(f"labb-spawner: couldn't measure app {app_id}'s database: {e}", file=sys.stderr)
                continue
            if over:
                stop_all(app_id, container, "database")
                stopped.append(app_id)
    return stopped


def watch_loop():
    """At the start and every minute: apps and their APIs are put right (heal), apps over their data
    limit, and (with APP_IDLE_MINUTES) idle ones, are stopped"""
    first = True
    while True:
        if not first:
            time.sleep(IDLE_CHECK_SECONDS)
        first = False
        try:
            heal_all()
        except Exception as e:
            print(f"labb-spawner: putting the apps right failed: {e}", file=sys.stderr)
        try:
            for app_id in data_sweep():
                print(f"labb-spawner: stopped app {app_id}, its data is over the limit", file=sys.stderr)
            if limits.get("app_idle_minutes") > 0:
                for app_id in idle_sweep(time.time()):
                    print(f"labb-spawner: stopped app {app_id}, unused for {minutes(limits.get('app_idle_minutes'))}", file=sys.stderr)
        except Exception as e:
            print(f"labb-spawner: the minute's check failed: {e}", file=sys.stderr)


@app.get("/system", dependencies=[Depends(authorized)])
def get_system():
    """What labb's containers and people's apps use, for Settings → Health (system.py)"""
    return system.report(client(), LABEL, SERVER_LABEL, API_LABEL)


@app.get("/limits", dependencies=[Depends(authorized)])
def get_limits():
    """The spawner's limits, for labb's Settings → Limits (limits.py)"""
    return limits.described()


@app.put("/limits", dependencies=[Depends(authorized)])
def save_limits(values: dict[str, int | None]):
    """Change some; null puts one back to its default"""
    return limits.save(values)


@app.get("/apps", dependencies=[Depends(authorized)])
def list_apps(owner: str):
    found = [describe(c) for c in containers(owner=owner)]
    return sorted(found, key=lambda a: a["created"], reverse=True)


@app.post("/apps", dependencies=[Depends(authorized)], status_code=201)
def new_app(body: NewApp):
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(400, "An app needs a name")
    if len(containers(owner=body.owner)) >= limits.get("app_count"):
        raise HTTPException(409, f"You have {limits.get('app_count')} apps already; remove one first")
    if limits.get("app_total") and len(containers()) >= limits.get("app_total"):
        raise HTTPException(409, f"This server has its most apps ({limits.get('app_total')}); someone has to remove one first")
    return start(body.owner, name, body.starter, body.choices)


@app.get("/starters", dependencies=[Depends(authorized)])
def list_starters():
    return [{key: s[key] for key in ("name", "title", "description", "choices")} for s in sources.starters()]


@app.get("/apps/{app_id}", dependencies=[Depends(authorized)])
def get_app(app_id: str, owner: str):
    container = find(app_id, owner)
    found = describe(container)
    if container.status == "running":
        found["memory_used"] = memory_used(container, *filter(None, [server_container(app_id)]))
    return found


@app.post("/apps/{app_id}/stop", dependencies=[Depends(authorized)])
def stop_app(app_id: str, owner: str):
    container = find(app_id, owner)
    stop_all(app_id, container, "owner")
    container.reload()
    return describe(container)


@app.post("/apps/{app_id}/start", dependencies=[Depends(authorized)])
def start_app(app_id: str, owner: str):
    container = find(app_id, owner)
    try:
        container.start()
    except APIError as e:
        if "port is already allocated" in str(e) or "address already in use" in str(e):
            raise HTTPException(409, "Something else on the server took this app's port while it was stopped")
        if not ("network" in str(e).lower() and "not found" in str(e).lower()):
            raise HTTPException(502, f"Docker couldn't start the app: {e.explanation or e}")
        # The apps' network was made again while it was stopped: it runs again on the new one
        ensure_network()
        container = replace(container, int(container.labels.get(f"{LABEL}.build", "0")))
    _stopped_by.pop(app_id, None)
    _traffic.pop(app_id, None)
    container.reload()
    # Its server and API start with it, made again if they can't
    heal(app_id, container)
    container.reload()
    return describe(container)


@app.post("/apps/{app_id}/restart", dependencies=[Depends(authorized)])
def restart_app(app_id: str, owner: str):
    container = find(app_id, owner)
    container.restart(timeout=10)
    server_follows(app_id, "restart")
    _stopped_by.pop(app_id, None)
    container.reload()
    # Restarting puts its API right too (running, on the network), which may be what it was restarted for
    heal(app_id, container)
    container.reload()
    return describe(container)


@app.get("/apps/{app_id}/logs", dependencies=[Depends(authorized)])
def app_logs(app_id: str, owner: str, tail: int = 300):
    """The end of what the app's server wrote (stdout and stderr), with Docker's timestamps; its front's,
    for an app with only a page"""
    lines = max(1, min(tail, MAX_LOG_LINES))
    container = find(app_id, owner)
    held = server_container(app_id) if has_server(container) else None
    output = (held or container).logs(tail=lines, timestamps=True)
    return {"lines": output.decode("utf-8", errors="replace")}


def with_source(app_id: str, owner: str):
    container = find(app_id, owner)
    if not sources.has_source(app_id):
        raise HTTPException(409, "This app was made before apps had files of their own: make a new one to change it")
    return container


@app.get("/apps/{app_id}/files", dependencies=[Depends(authorized)])
def list_files(app_id: str, owner: str):
    find(app_id, owner)
    return sources.files(app_id)


@app.get("/apps/{app_id}/search", dependencies=[Depends(authorized)])
def search_files(app_id: str, owner: str, q: str, case: bool = False, regex: bool = False, word: bool = False):
    """Where some text is in the app's files (sources.search)"""
    with_source(app_id, owner)
    return sources.search(app_id, q, case, regex, word)


@app.get("/apps/{app_id}/files/{path:path}", dependencies=[Depends(authorized)])
def read_file(app_id: str, path: str, owner: str):
    with_source(app_id, owner)
    return sources.read(app_id, path)


@app.put("/apps/{app_id}/files/{path:path}", dependencies=[Depends(authorized)])
def write_file(app_id: str, path: str, owner: str, body: File = Body()):
    with_source(app_id, owner)
    written = sources.write(app_id, path, body.content)
    if path.startswith(pages.FRONTEND + "/"):
        page_changed(app_id)
    return written


@app.delete("/apps/{app_id}/files/{path:path}", dependencies=[Depends(authorized)], status_code=204)
def delete_file(app_id: str, path: str, owner: str):
    with_source(app_id, owner)
    sources.delete(app_id, path)
    if path.startswith(pages.FRONTEND + "/"):
        page_changed(app_id)
    return Response(status_code=204)


@app.post("/apps/{app_id}/build", dependencies=[Depends(authorized)])
def build_app(app_id: str, owner: str, note: str = ""):
    """Build the app's files again; if that works, run the new build in its place. The files are
    saved as a version either way; `note` says who built it, for that version's message."""
    container = with_source(app_id, owner)
    with _building_lock:
        if app_id in _building:
            raise HTTPException(409, "It's being built already")
        _building.add(app_id)
    try:
        number = int(container.labels.get(f"{LABEL}.build", "0")) + 1
        # The page first, from its files as they are now (it's served as soon as it's built)
        page = pages.settle(app_id, build_page, again=True) if pages.has_page(app_id) else None
        ok, log, error = build_image(app_id, number)
        if ok and appdata.database_password(app_id) is not None:
            # Its database changes only for a build that's about to run
            migrated, migration_log, error = apply_migrations(app_id)
            log += "\n" + migration_log
            if not migrated:
                ok = False
                # The one that runs, and the one before it, as after any build
                remove_images(app_id, keep={number - 1, number - 2})
            else:
                update_api(app_id, container)
        built, crash = ok, None
        # Whether the build that goes back in, if this one crashes, was crashing too
        before_crashing = describe(container)["status"] == "restarting"
        try:
            if ok:
                container = replace(container, number)
                held = server_container(app_id) if has_server(container) else None
                if held is not None and (crash := crashed(held)):
                    # The one before runs again (its image is the one that ran); this build failed, with
                    # what it wrote as the reason
                    ok, error = False, crash
                    log += f"\n{crash}\n"
                    container = replace(container, number - 1)
                heal(app_id, container)
        finally:
            what = " (crashed on start)" if crash else ("" if built else " (didn't build)")
            versions.save(app_id, f"Build {number}{what}" + (f", {note[:80]}" if note.strip() else ""))
        server_ok, server_error = ok, error
        if page is not None and not page["ok"]:
            # Its log first: that's what to fix
            log = f"The page (frontend/) didn't build: {page['error']}\n{tail_of(page['log'], 60)}\n\nThe server:\n{log}"
            ok, error = False, error or f"The page didn't build: {page['error']}"
        sources.save_build(app_id, number, ok, log, error)
        return {"ok": ok, "error": error, "log": log, "app": describe(container), "before_crashing": bool(crash) and before_crashing,
                "server": {"ok": server_ok, "error": server_error},
                "page": None if page is None else {key: page[key] for key in ("build", "ok", "error", "log")}}
    finally:
        with _building_lock:
            _building.discard(app_id)


def migrations_of(app_id: str) -> list[tuple[str, str]]:
    """Its db/migrations/*.sql, by name"""
    prefix = database.MIGRATIONS + "/"
    found = []
    for f in sources.files(app_id):
        name = f["path"][len(prefix):]
        if f["path"].startswith(prefix) and "/" not in name and name.endswith(".sql"):
            found.append((name, sources.read(app_id, f["path"])["content"]))
    return sorted(found)


def apply_migrations(app_id: str) -> tuple[bool, str, str | None]:
    """The migrations not applied yet: (it worked, what happened, what went wrong)"""
    password = appdata.database_password(app_id)
    try:
        # Its `auth` schema first, made or brought up to date: migrations may refer to its users
        database.ensure_auth(app_id)
        done = database.migrate(app_id, password, migrations_of(app_id))
    except database.MigrationError as e:
        return False, f"The migration {e.name} failed, and was undone: {e}\n", f"Migration {e}"
    except (database.DatabaseError, psycopg.Error) as e:
        return False, f"Couldn't reach its database: {e}\n", f"Couldn't reach its database: {e}"
    return True, "".join(f"Applied the migration {name}\n" for name in done) or "No migrations to apply\n", None


def database_state(app_id: str) -> dict:
    password = appdata.database_password(app_id)
    if password is None:
        return {"available": database.available(), "enabled": False}
    applied = database.applied(app_id, password)
    done = {m["name"] for m in applied}
    held = api_container(app_id)
    container = containers(id=app_id)
    port = int(container[0].labels.get(f"{LABEL}.port", "0")) if container else 0
    stored = appdata.api_secrets(app_id)
    api = {"enabled": stored is not None, "port": api_port(port) if port else 0, "status": held.status if held else None,
           "signups": bool(stored.get("signups", True)) if stored else None}
    return {"available": True, "enabled": True, "name": database.name_of(app_id), "size": database.size(app_id), "api": api,
            "limit": limits.get("app_database_mb"), "tables": database.tables(app_id, password), "migrations": applied,
            "pending": [name for name, _ in migrations_of(app_id) if name not in done]}


def with_database(app_id: str, owner: str) -> str:
    """The app's database role's password; 409 if it has no database"""
    with_source(app_id, owner)
    password = appdata.database_password(app_id)
    if password is None:
        raise HTTPException(409, "This app has no database: turn it on first")
    return password


def database_errors(work):
    try:
        return work()
    except database.DatabaseError as e:
        raise HTTPException(400, str(e))
    except psycopg.OperationalError as e:
        raise HTTPException(503, f"Couldn't reach the apps' database: {e}")


@app.get("/apps/{app_id}/database", dependencies=[Depends(authorized)])
def get_database(app_id: str, owner: str):
    with_source(app_id, owner)
    return database_errors(lambda: database_state(app_id))


@app.post("/apps/{app_id}/database", dependencies=[Depends(authorized)])
def make_database(app_id: str, owner: str):
    """Its own database: made, its migrations applied, and the app started again with DATABASE_URL"""
    container = with_source(app_id, owner)
    if not database.available():
        raise HTTPException(409, "This server has no database for apps (APPS_DATABASE_ADMIN_URL isn't set)")
    if appdata.database_password(app_id) is not None:
        raise HTTPException(409, "It has a database already")
    password = database_errors(lambda: database.create(app_id))
    appdata.set_database_password(app_id, password)
    migrated, log, error = apply_migrations(app_id)
    container = restart_with_settings(app_id, container)
    return {**database_errors(lambda: database_state(app_id)), "app": describe(container),
            "migrated": {"ok": migrated, "log": log, "error": error}}


@app.delete("/apps/{app_id}/database", dependencies=[Depends(authorized)])
def remove_database(app_id: str, owner: str):
    """Its database and everything in it, gone; the app starts again without DATABASE_URL"""
    container = with_source(app_id, owner)
    if appdata.database_password(app_id) is None:
        raise HTTPException(409, "This app has no database")
    api_follows(app_id, "remove")
    appdata.set_api_secrets(app_id, None)
    database_errors(lambda: database.drop(app_id))
    appdata.set_database_password(app_id, None)
    return {"app": describe(restart_with_settings(app_id, container))}


@app.post("/apps/{app_id}/api", dependencies=[Depends(authorized)])
def make_api(app_id: str, owner: str):
    """Its API: a container of its own, at its port + APP_API_PORT_OFFSET, serving /rest/ its tables"""
    container = with_source(app_id, owner)
    with app_lock(app_id):
        return _make_api(app_id, container)


def _make_api(app_id: str, container):
    if appdata.database_password(app_id) is None:
        raise HTTPException(409, "It needs a database first")
    if api_container(app_id) is not None:
        raise HTTPException(409, "It has an API already")
    made = database_errors(lambda: database.enable_api(app_id))
    appdata.set_api_secrets(app_id, {**made, "jwt_secret": secrets.token_urlsafe(48), "signups": True})
    port = int(container.labels[f"{LABEL}.port"])
    try:
        held = run_api(app_id, port)
    except APIError as e:
        appdata.set_api_secrets(app_id, None)
        database_errors(lambda: database.disable_api(app_id))
        raise HTTPException(502, f"Its API didn't start: {e.explanation or e}")
    if container.status not in ("running", "restarting"):
        held.stop(timeout=10)
    else:
        wait_for_api(held)
    return database_errors(lambda: database_state(app_id))


@app.delete("/apps/{app_id}/api", dependencies=[Depends(authorized)])
def remove_api(app_id: str, owner: str):
    with_source(app_id, owner)
    with app_lock(app_id):
        api_follows(app_id, "remove")
        appdata.set_api_secrets(app_id, None)
    if appdata.database_password(app_id) is not None:
        database_errors(lambda: database.disable_api(app_id))
    return database_errors(lambda: database_state(app_id))


class ApiSettings(BaseModel):
    signups: bool


@app.patch("/apps/{app_id}/api", dependencies=[Depends(authorized)])
def change_api(app_id: str, owner: str, wanted: ApiSettings):
    """Whether new users may sign up on its API: its container is made again with it"""
    container = with_source(app_id, owner)
    stored = appdata.api_secrets(app_id)
    if stored is None or api_container(app_id) is None:
        raise HTTPException(409, "It has no API")
    try:
        with app_lock(app_id):
            appdata.set_api_secrets(app_id, {**stored, "signups": wanted.signups})
            renew_api(app_id, container)
    except APIError as e:
        raise HTTPException(502, f"Its API didn't start again: {e.explanation or e}")
    return database_errors(lambda: database_state(app_id))


@app.get("/apps/{app_id}/users", dependencies=[Depends(authorized)])
def list_users(app_id: str, owner: str, offset: int = 0, limit: int = database.USERS_PAGE):
    """Its own users, who signed up on its API (never their passwords' hashes)"""
    password = with_database(app_id, owner)
    return database_errors(lambda: database.users(app_id, password, offset, limit))


class UserChange(BaseModel):
    disabled: bool


@app.patch("/apps/{app_id}/users/{user_id}", dependencies=[Depends(authorized)])
def change_user(app_id: str, user_id: str, owner: str, change: UserChange):
    """A user disabled (signed out everywhere, at once) or enabled again"""
    password = with_database(app_id, owner)
    if not database_errors(lambda: database.set_user_disabled(app_id, password, user_id, change.disabled)):
        raise HTTPException(404, "No such user")
    return Response(status_code=204)


@app.delete("/apps/{app_id}/users/{user_id}", dependencies=[Depends(authorized)])
def remove_user(app_id: str, user_id: str, owner: str):
    """A user removed, with their sign-ins (and their rows where the app's tables cascade)"""
    password = with_database(app_id, owner)
    if not database_errors(lambda: database.delete_user(app_id, password, user_id)):
        raise HTTPException(404, "No such user")
    return Response(status_code=204)


class Query(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)
    write: bool = False


@app.get("/apps/{app_id}/database/rows", dependencies=[Depends(authorized)])
def database_rows(app_id: str, owner: str, table: str, schema: str = "public", offset: int = 0, limit: int = 100):
    password = with_database(app_id, owner)
    return database_errors(lambda: database.rows(app_id, password, schema, table, offset, limit))


@app.post("/apps/{app_id}/database/query", dependencies=[Depends(authorized)])
def database_query(app_id: str, owner: str, body: Query):
    """SQL as the app: read-only unless `write`"""
    password = with_database(app_id, owner)
    return database_errors(lambda: database.query(app_id, password, body.sql, body.write))


DUMP_IMAGE = os.getenv("APPS_DATABASE_IMAGE", "pgvector/pgvector:pg18-trixie")
DUMP_NETWORK = os.getenv("APPS_DATABASE_NETWORK", "")


@app.get("/apps/{app_id}/database/dump", dependencies=[Depends(authorized)])
def dump_database(app_id: str, owner: str):
    """Its database as SQL (pg_dump, in a container of the apps' Postgres's image that's gone after)"""
    with_database(app_id, owner)
    from psycopg.conninfo import conninfo_to_dict
    admin = conninfo_to_dict(database.ADMIN_URL)
    dumper = client().containers.create(
        DUMP_IMAGE, ["pg_dump", "--no-owner", "--no-privileges", "-h", admin.get("host", "localhost"),
                     "-p", str(admin.get("port", 5432)), "-U", admin.get("user", "postgres"), "-d", database.name_of(app_id)],
        environment={"PGPASSWORD": admin.get("password", "")}, network=DUMP_NETWORK or None,
        network_mode=None if DUMP_NETWORK else "host", labels={f"{LABEL}.dump": app_id},
    )

    def chunks():
        try:
            dumper.start()
            yield from dumper.attach(stdout=True, stderr=False, stream=True, logs=True)
            if dumper.wait(timeout=600).get("StatusCode") != 0:
                yield b"\n-- The dump stopped: it isn't complete\n"
        finally:
            dumper.remove(force=True)

    return StreamingResponse(chunks(), media_type="application/sql")


def settings_of(app_id: str) -> dict:
    return {"variables": appdata.settings(app_id), "uses_labb": appdata.uses_labb(app_id),
            "data": {"path": "/data", "size": appdata.data_size(app_id), "limit": limits.get("app_data_mb")}}


def restart_with_settings(app_id: str, container):
    """The app's server again from the build it runs, with its environment as it is now (its front has
    none of it); a stopped app's stays stopped. Its front, as it is."""
    if not has_server(container):
        return container
    remove_named(server_name(app_id))
    made = run_server(app_id, int(container.labels.get(f"{LABEL}.build", "0")), container.labels.get(f"{LABEL}.name", ""))
    if container.status not in ("running", "restarting"):
        made.stop(timeout=10)
    _traffic.pop(app_id, None)
    container.reload()
    return container


@app.get("/apps/{app_id}/settings", dependencies=[Depends(authorized)])
def get_settings(app_id: str, owner: str):
    find(app_id, owner)
    return settings_of(app_id)


@app.put("/apps/{app_id}/settings", dependencies=[Depends(authorized)])
def put_settings(app_id: str, owner: str, body: Settings):
    """New variables; the app is started again with them, from the build it runs"""
    container = with_source(app_id, owner)
    appdata.save_settings(app_id, [v.model_dump() for v in body.variables])
    container = restart_with_settings(app_id, container)
    return {"app": describe(container), **settings_of(app_id)}


# The starters' helpers for calling labb from an app's server
HELPERS = {"python": ("python", "labb.py"), "node": ("node", "labb.js")}


@app.post("/apps/{app_id}/labb-helper", dependencies=[Depends(authorized)])
def add_labb_helper(app_id: str, owner: str, language: str):
    """labb.py or labb.js, from the starters, into the app's backend/ (or its top folder, for an app
    from before backend/), replacing one it has"""
    with_source(app_id, owner)
    if language not in HELPERS:
        raise HTTPException(400, f"There's a helper for {' and '.join(HELPERS)}")
    starter, name = HELPERS[language]
    target = f"backend/{name}" if (sources.source(app_id) / "backend").is_dir() else name
    return sources.write(app_id, target, (sources.STARTERS / starter / "backend" / name).read_text())


@app.put("/apps/{app_id}/labb", dependencies=[Depends(authorized)])
def put_labb(app_id: str, owner: str, body: LabbEnv):
    """labb's variables for an app that may use it (the server's to set); it restarts with them"""
    container = with_source(app_id, owner)
    appdata.set_labb(app_id, body.env)
    return describe(restart_with_settings(app_id, container))


@app.get("/apps/{app_id}/data/download", dependencies=[Depends(authorized)])
def download_data(app_id: str, owner: str):
    """The app's data folder as a tar, made as it's sent"""
    find(app_id, owner)
    read, write = os.pipe()

    def pack():
        with os.fdopen(write, "wb") as out:
            try:
                appdata.pack_data(app_id, out)
            except (OSError, BrokenPipeError):
                pass

    threading.Thread(target=pack, daemon=True).start()

    def chunks():
        with os.fdopen(read, "rb") as source:
            while chunk := source.read(64 * 1024):
                yield chunk

    return StreamingResponse(chunks(), media_type="application/x-tar")


@app.get("/apps/{app_id}/source/download", dependencies=[Depends(authorized)])
def download_source(app_id: str, owner: str, name: str = "app"):
    """Its files with their history as a .git, in a folder `name`, as a zip made as it's sent"""
    with_source(app_id, owner)
    name = "".join(c if (c.isascii() and c.isalnum()) or c in "-_" else "-" for c in name).strip("-") or "app"
    read, write = os.pipe()

    def pack():
        with os.fdopen(write, "wb") as out:
            try:
                versions.pack_source(app_id, out, name)
            except (OSError, BrokenPipeError):
                pass

    threading.Thread(target=pack, daemon=True).start()

    def chunks():
        with os.fdopen(read, "rb") as source:
            while chunk := source.read(64 * 1024):
                yield chunk

    return StreamingResponse(chunks(), media_type="application/zip")


VERSION = re.compile(r"[0-9a-f]{40}")


def checked_version(version: str) -> str:
    if not VERSION.fullmatch(version):
        raise HTTPException(404, "No such version")
    return version


@app.get("/apps/{app_id}/versions", dependencies=[Depends(authorized)])
def list_versions(app_id: str, owner: str):
    """Its versions, newest first, and which files changed since the last one"""
    with_source(app_id, owner)
    found = versions.history(app_id)
    if not found["versions"]:
        # An app from before versions: its files as they are, to start from
        versions.save(app_id, "The files as they were before versions")
        found = versions.history(app_id)
    return found


@app.get("/apps/{app_id}/versions/unsaved/diff", dependencies=[Depends(authorized)])
def unsaved_diff(app_id: str, owner: str):
    with_source(app_id, owner)
    return {"diff": versions.unsaved_diff(app_id)}


@app.get("/apps/{app_id}/versions/{version}/diff", dependencies=[Depends(authorized)])
def version_diff(app_id: str, version: str, owner: str):
    with_source(app_id, owner)
    try:
        return {"diff": versions.diff(app_id, checked_version(version))}
    except KeyError:
        raise HTTPException(404, "No such version")


@app.post("/apps/{app_id}/versions/{version}/restore", dependencies=[Depends(authorized)])
def restore_version(app_id: str, version: str, owner: str):
    """Its files as they were in that version (saved as a new one); build to run them"""
    with_source(app_id, owner)
    try:
        saved = versions.restore(app_id, checked_version(version))
    except KeyError:
        raise HTTPException(404, "No such version")
    # Its page as it was then, too (its server, at the next build)
    page_changed(app_id)
    return {"version": saved, **versions.history(app_id)}


@app.get("/apps/{app_id}/page", dependencies=[Depends(authorized)])
def page_state(app_id: str, owner: str, wait: bool = False):
    """Its page's last build (`pages.state`), after building what's pending with `wait`"""
    with_source(app_id, owner)
    if wait and pages.has_page(app_id):
        pages.settle(app_id, build_page)
    return pages.state(app_id)


@app.get("/apps/{app_id}/build", dependencies=[Depends(authorized)])
def last_build(app_id: str, owner: str):
    find(app_id, owner)
    return {**sources.last_build(app_id), "building": app_id in _building}


@app.delete("/apps/{app_id}", dependencies=[Depends(authorized)], status_code=204)
def remove_app(app_id: str, owner: str):
    find(app_id, owner).remove(force=True)
    server_follows(app_id, "remove")
    api_follows(app_id, "remove")
    pages.forget(app_id)
    if appdata.database_password(app_id) is not None:
        database_errors(lambda: database.drop(app_id))
    _stopped_by.pop(app_id, None)
    _traffic.pop(app_id, None)
    remove_images(app_id)
    sources.remove(app_id)
    return Response(status_code=204)


class NewArtifact(BaseModel):
    owner: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    thread: str | None = Field(default=None, max_length=200)


@app.get("/artifacts", dependencies=[Depends(authorized)])
def list_artifacts(owner: str, thread: str | None = None):
    """Each with `site`: whether it has a good build (what labb publishes)"""
    return [{**about, "site": artifacts.has_site(about["name"])} for about in artifacts.listing(owner, thread)]


@app.post("/artifacts", dependencies=[Depends(authorized)], status_code=201)
def new_artifact(body: NewArtifact):
    return artifacts.make(body.owner, body.thread, body.title)


@app.get("/artifacts/{name}", dependencies=[Depends(authorized)])
def get_artifact(name: str, owner: str):
    return {**artifacts.mine(name, owner), "site": artifacts.has_site(name)}


@app.delete("/artifacts/{name}", dependencies=[Depends(authorized)], status_code=204)
def delete_artifact(name: str, owner: str):
    artifacts.remove(name, owner)
    return Response(status_code=204)


@app.get("/artifacts/{name}/files", dependencies=[Depends(authorized)])
def artifact_files(name: str, owner: str):
    artifacts.mine(name, owner)
    return artifacts.files(name)


@app.get("/artifacts/{name}/search", dependencies=[Depends(authorized)])
def search_artifact_files(name: str, owner: str, q: str, case: bool = False, regex: bool = False, word: bool = False):
    """Where some text is in the artifact's files, as the apps' search gives it"""
    artifacts.mine(name, owner)
    return sources.search_in(artifacts.source(name), artifacts.files(name), q, case, regex, word, limits.get("artifact_file_mb"))


@app.get("/artifacts/{name}/files/{path:path}", dependencies=[Depends(authorized)])
def read_artifact_file(name: str, path: str, owner: str):
    artifacts.mine(name, owner)
    return artifacts.read(name, path)


@app.put("/artifacts/{name}/files/{path:path}", dependencies=[Depends(authorized)])
def write_artifact_file(name: str, path: str, owner: str, body: File = Body()):
    artifacts.mine(name, owner)
    return artifacts.write(name, path, body.content)


@app.delete("/artifacts/{name}/files/{path:path}", dependencies=[Depends(authorized)], status_code=204)
def delete_artifact_file(name: str, path: str, owner: str):
    artifacts.mine(name, owner)
    artifacts.delete(name, path)
    return Response(status_code=204)


@app.post("/artifacts/{name}/build", dependencies=[Depends(authorized)])
def build_artifact_now(name: str, owner: str):
    """Builds its site: {"ok", "error", "log", "at", "files"}; the site is then GET .../site"""
    return artifacts.build(name, owner, build_artifact)


@app.get("/artifacts/{name}/site", dependencies=[Depends(authorized)])
def artifact_site(name: str, owner: str):
    """The last good build's static site, as a .tar.gz"""
    return Response(artifacts.site(name, owner), media_type="application/gzip")


@app.get("/health")
def health():
    try:
        client().ping()
        return {"ok": True, "docker": True}
    except Exception:
        return {"ok": True, "docker": False}


def main():
    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "4100")))


if __name__ == "__main__":
    main()
