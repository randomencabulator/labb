"""Settings → Health, for admins: whether the server's parts are working, in one view. Each
check says `ok` (true, false, or null for a warning) and what it found, in a line an admin
can act on. They run at once, each with a short timeout, so a part that's down can't hold
up the page."""

from concurrent.futures import ThreadPoolExecutor

import httpx

from labb import db, llm, memories, usage
from labb.chats import conn, lock
from labb.server import install, mcp
from labb.server import embedding
from labb.server.studio import sandbox
from labb.tools.web import CRAWLER_URL, crawler_token

TIMEOUT = 4


def check(name: str, ok: bool | None, detail: str) -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def ollama() -> list[dict]:
    """The Ollama server, the default model and the embedding model on it"""

    try:
        response = httpx.get(f"{llm.ollama_host()}/api/version", timeout=TIMEOUT)
        response.raise_for_status()
        version = response.json().get("version", "?")
    except (httpx.HTTPError, ValueError) as e:
        return [check("Ollama", False, f"Can't reach {llm.ollama_host()}: {e}. Check that it's running and OLLAMA_HOST.")]
    names = llm.ollama_models()
    found = [check("Ollama", True, f"Version {version} at {llm.ollama_host()}, {len(names)} models pulled")]
    embed = embedding.current()
    pulled = embed in names or f"{embed}:latest" in names
    found.append(check("Embedding model", pulled, f"{embed} is pulled" if pulled else
                       f"{embed} isn't pulled, so documents and memories are searched by keywords only: ollama pull {embed}, "
                       "or pick another in Settings → Models"))
    default = llm.default
    if default.provider == "ollama":
        cloud = default.name.endswith("cloud")
        there = default.name in names or cloud or f"{default.name}:latest" in names
        found.append(check("Default model", there, f"{default.spec}" + (" (a cloud model)" if cloud else "") if there else
                           f"{default.name} isn't pulled: ollama pull {default.name}, or pick another in Settings → Models"))
    else:
        found.append(check("Default model", True, default.spec))
    return found


def crawler() -> list[dict]:
    if not CRAWLER_URL:
        return [check("Crawler", None, "No CRAWLER_URL: web pages are read without it, and searches can't read the results")]
    headers = {"Authorization": f"Bearer {token}"} if (token := crawler_token()) else {}
    try:
        httpx.get(f"{CRAWLER_URL}/health", headers=headers, timeout=TIMEOUT).raise_for_status()
    except httpx.HTTPError as e:
        return [check("Crawler", False, f"Can't reach {CRAWLER_URL}: {e}")]
    return [check("Crawler", True, f"Answering at {CRAWLER_URL}")]


def python_sandbox() -> list[dict]:
    if not sandbox.available():
        return [check("Sandbox", None, "No SANDBOX_URL: Studio's Python steps can't run")]
    try:
        response = httpx.get(f"{sandbox.SANDBOX_URL}/health", timeout=TIMEOUT)
        response.raise_for_status()
        isolated = response.json().get("isolated")
    except (httpx.HTTPError, ValueError) as e:
        return [check("Sandbox", False, f"Can't reach {sandbox.SANDBOX_URL}: {e}")]
    if not sandbox.token():
        return [check("Sandbox", None, "Answering, but without a token: anything that can reach it can run code")]
    return [check("Sandbox", True if isolated else None,
                  "Answering, with a user of its own per run" if isolated else "Answering, but not isolated (not run as root in Docker)")]


def database() -> list[dict]:
    stored = usage.storage()
    kind = "Postgres" if db.POSTGRES else "SQLite"
    size = stored["bytes"] / 1_000_000
    return [check("Database", True, f"{kind}, {size:,.1f} MB: {stored['chats']:,} chats, {stored['usage']:,} replies counted")]


def search_index() -> list[dict]:
    """Documents still indexing or that failed, and memories not embedded with the current model"""

    with lock:
        statuses = dict(conn.execute("SELECT status, COUNT(*) FROM documents GROUP BY status").fetchall())
        stale = conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'ready' AND embed_model IS DISTINCT FROM ?",
                             (embedding.index_id(),)).fetchone()[0]
    memory_conn, memory_lock = memories.db()
    with memory_lock:
        unindexed = memory_conn.execute("SELECT COUNT(*) FROM memories WHERE embed_model IS DISTINCT FROM ?",
                                        (embedding.index_id(),)).fetchone()[0]
    failed, indexing, ready = statuses.get("error", 0), statuses.get("indexing", 0), statuses.get("ready", 0)
    parts = [f"{ready:,} documents ready"]
    if indexing:
        parts.append(f"{indexing:,} indexing")
    if failed:
        parts.append(f"{failed:,} failed (their owners see why on the Documents page)")
    if stale:
        parts.append(f"{stale:,} from another embedding model (indexed again at the next start)")
    if unindexed:
        parts.append(f"{unindexed:,} memories not embedded yet (found by keywords until then)")
    return [check("Search index", False if failed else None if indexing or stale or unindexed else True, "; ".join(parts))]


def machines() -> list[dict]:
    version = install.version()
    seen = [m for m in usage.machines(version) if m["version"]]
    old = [m for m in seen if m["outdated"]]
    if not old:
        return [check("PCs", True, f"{len(seen)} PCs on labb {version}" if seen else "No PCs have connected yet")]
    listed = ", ".join(f"{m['machine']} ({m['username']}, {m['version']})" for m in old[:10])
    return [check("PCs", None, f"{len(old)} of {len(seen)} PCs aren't on {version}: {listed}. On each: labb --update")]


def mcp_servers() -> list[dict]:
    status = mcp.status()["servers"]
    if not status:
        return []
    down = {name: state for name, state in status.items() if not isinstance(state, int)}
    if down:
        return [check("MCP servers", False, "; ".join(f"{name}: {state}" for name, state in down.items()))]
    return [check("MCP servers", True, ", ".join(f"{name} ({tools} tools)" for name, tools in status.items()))]


CHECKS = [ollama, crawler, python_sandbox, database, search_index, machines, mcp_servers]


def report() -> list[dict]:
    """Every check's result, in a fixed order; one that fails to run says so instead"""

    def run(probe):
        try:
            return probe()
        except Exception as e:
            return [check(probe.__name__.replace("_", " ").capitalize(), False, f"The check failed: {e}")]

    with ThreadPoolExecutor(len(CHECKS)) as pool:
        return [found for results in pool.map(run, CHECKS) for found in results]
