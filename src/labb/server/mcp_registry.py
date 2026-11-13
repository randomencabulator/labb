"""Finding MCP servers to add (Settings → MCP servers): well-known ones, and a search of the
official MCP Registry (registry.modelcontextprotocol.io).

Each server comes back with the ways to run it ("options"): a package for npx, uvx or docker, or a
remote URL. An option is a config in the mcpServers format with {{FIELD}} where something is
needed (a token, a folder), and the fields to ask for. The page fills them in and saves the
config like any other (mcp.save); nothing here saves or runs anything.
"""

import re
import shutil
import threading
import time

import httpx

REGISTRY = "https://registry.modelcontextprotocol.io/v0.1/servers"
LIMIT = 100
CACHE_SECONDS = 600
# Hosts that re-publish other people's servers behind their own key: listed after the rest
RESELLERS = ("server.smithery.ai",)

http = httpx.Client(timeout=httpx.Timeout(12, connect=6), headers={"Accept": "application/json"})
_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


class RegistryError(RuntimeError):
    """The registry couldn't be asked, said for the admin"""


# Well-known servers, in the registry's format: shown before a search, and when it can't be reached
FEATURED: list[dict] = [
    {"name": "io.github.github/github-mcp-server", "title": "GitHub",
     "description": "Repositories, issues, pull requests and code search, from GitHub's own hosted server.",
     "repository": {"url": "https://github.com/github/github-mcp-server"},
     "remotes": [{"type": "streamable-http", "url": "https://api.githubcopilot.com/mcp/", "headers": [
         {"name": "Authorization", "value": "Bearer {github_token}", "isRequired": True, "isSecret": True,
          "description": "A GitHub personal access token (github.com/settings/tokens)"}]}]},
    {"name": "com.context7/context7", "title": "Context7",
     "description": "Up-to-date documentation and code examples for libraries, looked up as the model needs them.",
     "repository": {"url": "https://github.com/upstash/context7"},
     "remotes": [{"type": "streamable-http", "url": "https://mcp.context7.com/mcp", "headers": [
         {"name": "CONTEXT7_API_KEY", "isSecret": True, "description": "Optional: an API key for higher limits"}]}]},
    {"name": "com.deepwiki/deepwiki", "title": "DeepWiki",
     "description": "Ask about any public GitHub repository: its docs, structure and how things work.",
     "repository": {"url": "https://docs.devin.ai/work-with-devin/deepwiki-mcp"},
     "remotes": [{"type": "streamable-http", "url": "https://mcp.deepwiki.com/mcp"}]},
    {"name": "co.huggingface/hf-mcp-server", "title": "Hugging Face",
     "description": "Search models, datasets, papers and Spaces on the Hugging Face Hub.",
     "repository": {"url": "https://github.com/evalstate/hf-mcp-server"},
     "remotes": [{"type": "streamable-http", "url": "https://huggingface.co/mcp", "headers": [
         {"name": "Authorization", "value": "Bearer {hf_token}", "isSecret": True,
          "description": "Optional: a Hugging Face token (huggingface.co/settings/tokens)"}]}]},
    {"name": "io.github.modelcontextprotocol/time", "title": "Time",
     "description": "The current time, and converting between time zones.",
     "repository": {"url": "https://github.com/modelcontextprotocol/servers/tree/main/src/time"},
     "packages": [{"registryType": "pypi", "identifier": "mcp-server-time", "transport": {"type": "stdio"}}]},
    {"name": "io.github.modelcontextprotocol/fetch", "title": "Fetch",
     "description": "Fetch a web page and read it as markdown.",
     "repository": {"url": "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch"},
     "packages": [{"registryType": "pypi", "identifier": "mcp-server-fetch", "transport": {"type": "stdio"}}]},
    {"name": "io.github.modelcontextprotocol/git", "title": "Git",
     "description": "Read, search and change a Git repository on the server.",
     "repository": {"url": "https://github.com/modelcontextprotocol/servers/tree/main/src/git"},
     "packages": [{"registryType": "pypi", "identifier": "mcp-server-git", "transport": {"type": "stdio"},
                   "packageArguments": [{"type": "named", "name": "--repository", "isRequired": True,
                                         "description": "The repository's folder on the server"}]}]},
    {"name": "io.github.modelcontextprotocol/filesystem", "title": "Filesystem",
     "description": "Read and write files in a folder on the server, and only there.",
     "repository": {"url": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem"},
     "packages": [{"registryType": "npm", "identifier": "@modelcontextprotocol/server-filesystem", "transport": {"type": "stdio"},
                   "packageArguments": [{"type": "positional", "valueHint": "folder", "isRequired": True,
                                         "description": "The folder it may use, on the server"}]}]},
    {"name": "io.github.modelcontextprotocol/memory", "title": "Memory",
     "description": "A knowledge graph the model keeps: people, things and how they relate.",
     "repository": {"url": "https://github.com/modelcontextprotocol/servers/tree/main/src/memory"},
     "packages": [{"registryType": "npm", "identifier": "@modelcontextprotocol/server-memory", "transport": {"type": "stdio"}}]},
    {"name": "io.github.modelcontextprotocol/sequentialthinking", "title": "Sequential thinking",
     "description": "A scratchpad for thinking a problem through step by step, revising as it goes.",
     "repository": {"url": "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking"},
     "packages": [{"registryType": "npm", "identifier": "@modelcontextprotocol/server-sequential-thinking", "transport": {"type": "stdio"}}]},
    {"name": "com.microsoft/playwright-mcp", "title": "Playwright",
     "description": "Drive a browser: open pages, click, fill in forms, take snapshots.",
     "repository": {"url": "https://github.com/microsoft/playwright-mcp"},
     "packages": [{"registryType": "npm", "identifier": "@playwright/mcp", "transport": {"type": "stdio"},
                   "packageArguments": [{"type": "named", "name": "--headless", "value": ""}]}]},
]


def runtimes() -> dict[str, bool]:
    """Which of the programs that run packages this server has"""
    return {name: shutil.which(name) is not None for name in ("npx", "uvx", "docker")}


def slug(text: str) -> str:
    """A name for the server in the settings: "Sequential thinking" is sequential-thinking"""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "server"


def field_key(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", text).strip("_") or "value"


class _Fields:
    """The fields an option asks for, by key, in the order they're first needed"""

    def __init__(self):
        self.items: list[dict] = []

    def add(self, key: str, *, label: str = "", help: str = "", required: bool = False, secret: bool = False,
            default: str = "", choices: list | None = None) -> str:
        key = field_key(key)
        if not any(f["key"] == key for f in self.items):
            # "github_token" reads as "Github token"; names like GITHUB_TOKEN stay as they are
            label = label or key
            if label == label.lower():
                label = label.replace("_", " ").replace("-", " ").strip().capitalize()
            self.items.append({"key": key, "label": label, "help": help or "", "required": bool(required),
                               "secret": bool(secret), "default": "" if default is None else str(default),
                               "choices": [str(c) for c in choices or []]})
        return "{{" + key + "}}"

    def braces(self, text: str, spec: dict) -> str:
        """{name} in a value or URL as fields: their details from `variables`, or the value's own"""
        variables = spec.get("variables") or {}

        def one(match):
            name = match.group(1)
            about = variables.get(name, {})
            return self.add(name, label=name, help=about.get("description") or spec.get("description", ""),
                            required=about.get("isRequired", spec.get("isRequired", False)),
                            secret=about.get("isSecret", spec.get("isSecret", False)), default=about.get("default", ""),
                            choices=about.get("choices"))
        return re.sub(r"\{([A-Za-z_][A-Za-z0-9_.-]*)\}", one, text)


def _arguments(arguments: list, fields: _Fields) -> list[str]:
    out: list[str] = []
    for argument in arguments or []:
        kind = argument.get("type")
        value = argument.get("value")
        name = argument.get("name") or argument.get("valueHint") or "argument"
        if value is None or (value == "" and kind == "positional"):
            if not argument.get("isRequired") and argument.get("default") is None:
                continue  # Optional and with nothing to say: left out
            value = fields.add(name, label=argument.get("valueHint") or name.lstrip("-"), help=argument.get("description", ""),
                               required=argument.get("isRequired", False), secret=argument.get("isSecret", False),
                               default=argument.get("default", ""), choices=argument.get("choices"))
        else:
            value = fields.braces(str(value), argument)
        if kind == "named":
            out.append(name)
            if value != "":
                out.append(value)
        else:
            out.append(value)
    return out


def _env(variables: list, fields: _Fields) -> dict[str, str]:
    env = {}
    for variable in variables or []:
        name = variable.get("name")
        if not name:
            continue
        value = variable.get("value")
        env[name] = fields.braces(value, variable) if value else fields.add(
            name, label=name, help=variable.get("description", ""), required=variable.get("isRequired", False),
            secret=variable.get("isSecret", False), default=variable.get("default", ""), choices=variable.get("choices"))
    return env


def package_option(package: dict, have: dict[str, bool]) -> dict | None:
    """How to run a package (npm, PyPI or a container image) as a stdio server; None if labb can't"""

    if (package.get("transport") or {}).get("type", "stdio") != "stdio":
        return None
    kind = package.get("registryType")
    identifier = package.get("identifier")
    version = package.get("version")
    if not identifier:
        return None
    fields = _Fields()
    runtime_args = [a for a in _arguments(package.get("runtimeArguments"), fields) if a != "-y"]
    args = _arguments(package.get("packageArguments"), fields)
    env = _env(package.get("environmentVariables"), fields)
    if kind == "npm":
        runtime = "npx"
        spec = f"{identifier}@{version}" if version and version != "latest" else identifier
        config = {"command": "npx", "args": ["-y", *runtime_args, spec, *args]}
    elif kind == "pypi":
        runtime = "uvx"
        spec = f"{identifier}=={version}" if version and version != "latest" else identifier
        config = {"command": "uvx", "args": [*runtime_args, spec, *args]}
    elif kind == "oci":
        runtime = "docker"
        image = identifier if ":" in identifier.rsplit("/", 1)[-1] or not version else f"{identifier}:{version}"
        passed = [part for name in env for part in ("-e", name)]
        config = {"command": "docker", "args": ["run", "-i", "--rm", *passed, *runtime_args, image, *args]}
    else:
        return None
    if env:
        config["env"] = env
    return {"kind": kind, "runtime": runtime, "available": have.get(runtime, False),
            "label": {"npm": "npm package", "pypi": "Python package", "oci": "Docker image"}[kind],
            "detail": identifier, "config": config, "fields": fields.items}


def remote_option(remote: dict) -> dict | None:
    """A server that runs elsewhere, by URL"""

    transport = {"streamable-http": "streamable_http", "sse": "sse"}.get(remote.get("type", ""))
    if not transport or not remote.get("url"):
        return None
    fields = _Fields()
    config: dict = {"url": fields.braces(remote["url"], remote)}
    if transport != "streamable_http":
        config["transport"] = transport
    headers = {}
    for header in remote.get("headers") or []:
        name = header.get("name")
        if not name:
            continue
        value = header.get("value")
        headers[name] = fields.braces(value, header) if value else fields.add(
            name, label=name, help=header.get("description", ""), required=header.get("isRequired", False),
            secret=header.get("isSecret", False), default=header.get("default", ""), choices=header.get("choices"))
    if headers:
        config["headers"] = headers
    return {"kind": "remote", "runtime": None, "available": True, "label": "Hosted" if transport == "streamable_http" else "Hosted (SSE)",
            "detail": remote["url"].split("//", 1)[-1].split("/", 1)[0], "config": config, "fields": fields.items}


def entry(server: dict, have: dict[str, bool], featured: bool = False) -> dict:
    """A server as the page shows it: what it is, and its ways to run, the ones that work here first"""

    options, seen = [], set()
    remotes = sorted(server.get("remotes") or [], key=lambda r: r.get("type") != "streamable-http")
    for option in [*(remote_option(r) for r in remotes), *(package_option(p, have) for p in server.get("packages") or [])]:
        # The same server at one address over two transports: the newer one is enough
        where = (option or {}).get("detail"), (option or {}).get("kind")
        if option and where not in seen:
            seen.add(where)
            options.append(option)
    options.sort(key=lambda o: not o["available"])
    name = server.get("name", "")
    short = name.rsplit("/", 1)[-1]
    repository = (server.get("repository") or {}).get("url") or server.get("websiteUrl")
    return {"id": name, "title": server.get("title") or short, "suggested_name": slug(server.get("title") or short),
            "description": server.get("description", ""), "version": server.get("version"), "url": repository,
            "featured": featured, "options": options}


def featured(query: str = "") -> list[dict]:
    have = runtimes()
    q = query.strip().lower()
    return [entry(s, have, featured=True) for s in FEATURED
            if not q or q in s["name"].lower() or q in s["title"].lower() or q in s["description"].lower()]


def _resold(found: dict) -> bool:
    return bool(found["options"]) and all(o["kind"] == "remote" and any(r in str(o["config"].get("url")) for r in RESELLERS)
                                          for o in found["options"])


def rank(found: dict, query: str) -> tuple:
    """Best first: named just that ("postgres", "postgres-mcp"), then with it as a word in the name;
    ones that run here, with a repository and a description of their own; re-hosted copies last"""

    q = query.strip().lower()
    short = found["id"].rsplit("/", 1)[-1].lower()
    words = re.split(r"[^a-z0-9]+", short)
    bare = re.sub(r"(^|[-_.])(mcp|server|mcp-server|server-mcp)($|[-_.])", "-", short).strip("-_.")
    closeness = 0 if q in (short, bare) else 1 if short.startswith(q) or bare.startswith(q) else 2 if q in words else 3
    runs = any(o["available"] for o in found["options"])
    return (_resold(found), closeness, not runs, not found["url"], len(found["description"]) < 30, short)


def search(query: str, cursor: str | None = None) -> dict:
    """The registry's servers whose name has `query`: {"servers": [...], "next": cursor or None}"""

    params = {"search": query.strip(), "limit": LIMIT, "version": "latest"}
    if cursor:
        params["cursor"] = cursor
    key = tuple(sorted(params.items()))
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
    try:
        response = http.get(REGISTRY, params=params)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as e:
        raise RegistryError(f"Couldn't search the MCP Registry: {e}") from e
    have = runtimes()
    seen, found = set(), []
    for item in data.get("servers") or []:
        server = item.get("server") or {}
        meta = (item.get("_meta") or {}).get("io.modelcontextprotocol.registry/official") or {}
        if meta.get("status", "active") != "active" or server.get("name") in seen:
            continue
        seen.add(server.get("name"))
        one = entry(server, have)
        if one["options"]:
            found.append(one)
    found.sort(key=lambda s: rank(s, query))
    result = {"servers": found, "next": (data.get("metadata") or {}).get("nextCursor")}
    with _cache_lock:
        if len(_cache) > 200:
            _cache.clear()
        _cache[key] = (time.monotonic(), result)
    return result
