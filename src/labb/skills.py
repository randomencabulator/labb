"""Skills: folders of instructions (and the files they use) that the model loads when a task
calls for one, in the open Agent Skills format (agentskills.io):

    my-skill/
      SKILL.md        ---\\nname: my-skill\\ndescription: What it does and when to use it\\n---\\n<instructions>
      reference.md    anything else it mentions: references, templates, scripts

The model sees each skill's name and description in its system prompt, loads a skill's
instructions with `use_skill` when a task matches, and reads its other files with
`read_skill_file` (tools/skills.py). Typing `/name` uses one on purpose.

Where they come from, first found wins on a name:
- In the terminal (a run with a `workdir`): the project's `.labb/skills/` and
  `.claude/skills/`, then the user's `~/.labb/skills/` and `~/.claude/skills/`.
- On the server: the account's skills and the ones others shared (server/skills.py sets
  `store`). labb with a server also gets those, through `/skills` with its API key.
"""

import re
import threading
import time
from pathlib import Path
from typing import Protocol

import httpx
import yaml
from langchain_core.runnables import RunnableConfig

from labb import remote

SKILL_FILE = "SKILL.md"
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
# In the system prompt: skills listed, and characters of their descriptions
PROMPT_SKILLS = 50
PROMPT_CHARS = 8000
MAX_FILES = 200
MAX_READ_CHARS = 100_000
# How long the lists are kept: a folder's skills, and the server's
LOCAL_SECONDS = 5
SERVER_SECONDS = 60
# When a cache entry that isn't there was made: long ago. Not 0: time.monotonic() counts from the
# machine's start, so on one up for less than a cache's seconds, 0 would pass for a fresh entry
NEVER = float("-inf")
SKIPPED = {".git", "__pycache__", "node_modules", ".venv"}


class SkillError(ValueError):
    """A SKILL.md that can't be used, said for whoever wrote it"""


class Store(Protocol):
    """The server's skills (server/skills.py)"""

    def list_for(self, user_id: str) -> list[dict]: ...
    def files(self, user_id: str, name: str) -> list[str] | None: ...
    def read(self, user_id: str, name: str, path: str) -> str | None: ...
    def save(self, user_id: str, contents: dict[str, str], shared: bool | None = None, replace: bool = True) -> dict: ...


# Set by server/app.py: skills are the accounts' own there
on_server = False
store: Store | None = None


def parse(text: str, folder: str | None = None) -> tuple[dict, str]:
    """A SKILL.md's front matter (name, description, …) and its instructions. The name
    defaults to the folder's; raises SkillError."""

    meta, body = {}, text
    if text.startswith("---"):
        _, sep, rest = text.partition("\n")
        header, sep, body = rest.partition("\n---")
        if not sep:
            raise SkillError("The front matter (between --- lines) isn't closed")
        body = body.partition("\n")[2]
        try:
            meta = yaml.safe_load(header) or {}
        except yaml.YAMLError as e:
            raise SkillError(f"The front matter isn't valid YAML: {e}")
        if not isinstance(meta, dict):
            raise SkillError("The front matter should be `key: value` lines")
    name = str(meta.get("name") or folder or "").strip()
    description = " ".join(str(meta.get("description") or "").split())
    if not name:
        raise SkillError("A skill needs a `name:` in its front matter")
    if len(name) > MAX_NAME or not NAME.match(name):
        raise SkillError(f"“{name}” isn't a skill name: lowercase letters, digits and hyphens, up to {MAX_NAME}")
    if not description:
        raise SkillError("A skill needs a `description:` that says what it does and when to use it")
    if len(description) > MAX_DESCRIPTION:
        raise SkillError(f"The description is over {MAX_DESCRIPTION:,} characters")
    return {**meta, "name": name, "description": description}, body.strip()


# --- In the terminal: folders ----------------------------------------------------------


def folders(workdir: str) -> list[tuple[Path, str]]:
    """Where a terminal's skills are, the ones that win first, with what they're called"""
    home = Path.home()
    work = Path(workdir)
    return [(work / ".labb" / "skills", "project"), (work / ".claude" / "skills", "project"),
            (home / ".labb" / "skills", "personal"), (home / ".claude" / "skills", "personal")]


_local_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def local(workdir: str) -> list[dict]:
    """The skills in the terminal's folders; ones that can't be read are left out"""

    with _lock:
        at, found = _local_cache.get(workdir, (NEVER, []))
    if time.monotonic() - at < LOCAL_SECONDS:
        return found
    found, seen = [], set()
    for folder, source in folders(workdir):
        for path in sorted(folder.glob(f"*/{SKILL_FILE}")) if folder.is_dir() else []:
            try:
                meta, _ = parse(path.read_text(errors="replace"), path.parent.name)
            except (OSError, SkillError):
                continue
            if meta["name"] not in seen:
                seen.add(meta["name"])
                found.append({"name": meta["name"], "description": meta["description"], "source": source,
                              "path": str(path.parent)})
    with _lock:
        _local_cache[workdir] = (time.monotonic(), found)
    return found


def problems(workdir: str) -> list[str]:
    """What's wrong with the terminal's skill folders that can't be used, for /skills"""

    found = []
    for folder, _ in folders(workdir):
        for path in sorted(folder.glob(f"*/{SKILL_FILE}")) if folder.is_dir() else []:
            try:
                parse(path.read_text(errors="replace"), path.parent.name)
            except (OSError, SkillError) as e:
                found.append(f"{path}: {e}")
    return found


def _local_files(folder: Path) -> list[str]:
    files = []
    for path in sorted(folder.rglob("*")):
        if path.is_file() and not SKIPPED & set(path.relative_to(folder).parts):
            files.append(path.relative_to(folder).as_posix())
            if len(files) >= MAX_FILES:
                break
    return files


def _local_read(folder: Path, path: str) -> str | None:
    target = (folder / path).resolve()
    if not target.is_relative_to(folder.resolve()) or not target.is_file():
        return None
    data = target.read_bytes()
    if b"\0" in data[:8000]:
        return "(a binary file)"
    return data.decode(errors="replace")


# --- On the server, and labb asking it ------------------------------------------------


_server_cache: dict[str, tuple[float, list[dict]]] = {}


def forget_cached():
    with _lock:
        _server_cache.clear()
        _local_cache.clear()


def _remote(endpoint: str, **params):
    response = httpx.get(f"{remote.server()}{endpoint}", params=params, headers=remote.headers(), timeout=30)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def _agent_choice(config: RunnableConfig | None):
    """An agent's skills: "mine" (the chatter's), "none", or a list of its owner's (server/agents.py)"""
    agent = (config or {}).get("configurable", {}).get("agent") or {}
    return agent.get("skills", "mine")


def _user(config: RunnableConfig | None) -> str | None:
    """Whose skills a run uses: the chatter's, or the owner's for an agent given some"""
    configurable = (config or {}).get("configurable", {})
    if isinstance(_agent_choice(config), list):
        return configurable["agent"]["owner_id"]
    return configurable.get("user_id")


def server_skills(config: RunnableConfig | None) -> list[dict]:
    """The account's skills on the server (and the shared ones); [] if they can't be reached"""

    try:
        if on_server:
            choice, user_id = _agent_choice(config), _user(config)
            if choice == "none" or not (store and user_id):
                return []
            found = [{**s, "source": "server"} for s in store.list_for(user_id)]
            return [s for s in found if s["name"] in choice] if isinstance(choice, list) else found
        if remote.server() is None:
            return []
        with _lock:
            at, found = _server_cache.get("server", (NEVER, []))
        if time.monotonic() - at > SERVER_SECONDS:
            found = [{"name": s["name"], "description": s["description"], "source": "server"} for s in _remote("/skills") or []]
            with _lock:
                _server_cache["server"] = (time.monotonic(), found)
        return found
    except (httpx.HTTPError, ValueError, KeyError):
        return []


def available(config: RunnableConfig | None) -> list[dict]:
    """The skills a run can use: the terminal's folders first, then the server's"""

    configurable = (config or {}).get("configurable", {})
    workdir = configurable.get("workdir")
    found, seen = [], set()
    # A preview on the server has no project, and the server's own files aren't one
    for skill in (local(workdir) if workdir and not configurable.get("preview") else []) + server_skills(config):
        if skill["name"] not in seen:
            seen.add(skill["name"])
            found.append(skill)
    return found


def find(config: RunnableConfig | None, name: str) -> dict | None:
    name = name.strip().lstrip("/")
    return next((s for s in available(config) if s["name"] == name), None)


def files(config: RunnableConfig | None, skill: dict) -> list[str]:
    """A skill's files, SKILL.md among them"""

    if "path" in skill:
        return _local_files(Path(skill["path"]))
    if on_server:
        return (store.files(_user(config), skill["name"]) or []) if store else []
    return (_remote(f"/skills/by-name/{skill['name']}/files") or {}).get("files", [])


def read(config: RunnableConfig | None, skill: dict, path: str = SKILL_FILE) -> str | None:
    """One of a skill's files; None if it has no such file"""

    path = path.strip().lstrip("/") or SKILL_FILE
    if "path" in skill:
        return _local_read(Path(skill["path"]), path)
    if on_server:
        return store.read(_user(config), skill["name"], path) if store else None
    found = _remote(f"/skills/by-name/{skill['name']}/file", path=path)
    return found["content"] if found else None


def instructions(config: RunnableConfig | None, skill: dict) -> str:
    """What `use_skill` and `/name` give the model: the instructions without the front matter"""

    text = read(config, skill) or ""
    try:
        _, body = parse(text, skill["name"])
    except SkillError:
        body = text
    return body


def prompt_section(config: RunnableConfig | None) -> str | None:
    """The skills' names and descriptions for the system prompt"""

    lines, size = [], 0
    for skill in available(config)[:PROMPT_SKILLS]:
        line = f"- {skill['name']}: {skill['description']}"
        if size + len(line) > PROMPT_CHARS:
            break
        lines.append(line)
        size += len(line)
    if not lines:
        return None
    return ("Skills you can use. When a task matches a skill's description, load it with `use_skill` "
            "before you start, and follow its instructions. A message that starts with /name uses that "
            "skill on purpose: its SKILL.md comes attached.\n" + "\n".join(lines))


def compose(name: str, description: str, instructions: str) -> str:
    """A SKILL.md; raises SkillError if it isn't a valid one"""

    header = yaml.safe_dump({"name": name.strip(), "description": " ".join(description.split())},
                            allow_unicode=True, sort_keys=False, width=1000)
    text = f"---\n{header}---\n{instructions.strip()}\n"
    parse(text)
    return text


def save(config: RunnableConfig | None, name: str, description: str, instructions: str,
         personal: bool = True, replace: bool = False) -> str:
    """Write a new skill: the account's on the server, or in the terminal a folder in
    ~/.labb/skills/ (`personal`) or the project's .labb/skills/. Where it went;
    raises SkillError."""

    text = compose(name, description, instructions)
    name = name.strip()
    configurable = (config or {}).get("configurable", {})
    if workdir := configurable.get("workdir"):
        root = (Path.home() / ".labb" if personal else Path(workdir) / ".labb") / "skills"
        target = root / name / SKILL_FILE
        if target.exists() and not replace:
            raise SkillError(f"There's a skill called {name} already ({target})")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        forget_cached()
        return str(target)
    if on_server and store and (user_id := configurable.get("user_id")):
        store.save(user_id, {SKILL_FILE: text}, replace=replace)
        forget_cached()
        return "the Skills page"
    raise SkillError("Skills can only be saved in the terminal or in someone's web chat")


def invoke_note(name: str, body: str) -> dict[str, str]:
    """A skill used on purpose (`/name`), as an attached file of the message"""
    return {f"skills/{name}/{SKILL_FILE}": body}
