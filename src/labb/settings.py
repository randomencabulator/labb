"""labb's settings: ~/.labb/settings.json, then the project's .labb/settings.json,
which wins.

    {
      "model": "ollama:gemma4:e4b",          // the model to start with
      "models": ["openai:gpt-5.5"],          // more models to offer in /model and the web's picker
      "defaultMode": "plan",                 // start in plan mode ("acceptEdits": edits don't ask)
      "answerMode": "fast",                  // how much the model thinks first: fast, auto or thinking (also /answer)
      "ollamaNumCtx": 65536,                 // tokens Ollama keeps in memory for a model (also --num-ctx, /num-ctx)
      "theme": "monokai",                    // code highlighting in the terminal (a Pygments style)
      "showThinking": true,                  // show the model's reasoning as it streams (also /thinking, Ctrl-T)
      "permissions": {"allow": ["npm test"]},  // command prefixes and MCP tools allowed without asking
      "tools": {"deny": ["write_file"]},     // tools to turn off ("deny") or the only ones on ("allow")
      "browser": true,                       // let the agent use a browser (also --browser, /browser)
      "browserSites": ["example.com"],       // sites it clicks and types on without asking first; only in your own file
      "mcpServers": {"name": {"command": "npx", "args": [...]}},  // or {"url": "..."}
      "hooks": {"PreToolUse": [{"matcher": "run_command", "command": "..."}], "PostToolUse": [...]},
      "prices": {"openai:gpt-5.5": [1.25, 10]},  // $ per million tokens [input, output(, cached input, cache write)], for /cost
      "maxCostPerSession": 2,                // stop a session's replies once they've cost $2
      "parallelTools": 5,                    // tool calls that only read run at once, at most this many; 1 for one at a time
      "engine": "terse-coder"               // the engine to start with (also --engine, /engine)
    }

Lists (models, permissions, hooks, tools.deny) are combined, `mcpServers` are merged by name,
and anything else in the project file (tools.allow too) replaces the user's.

A project's file came with the folder (a repository you cloned, say), so what in it would run code or
skip asking (`hooks`, `mcpServers`, `permissions.allow`, `"defaultMode": "acceptEdits"`, and the
commands remembered in `.labb/permissions.json`) counts only once you've agreed to it, for its exact
content: `trusted-projects.json` in ~/.labb keeps what you agreed to, per folder. The terminal asks
when it starts; until then, those parts are left out.
"""

import hashlib
import json
import os
from pathlib import Path

USER_DIR = Path.home() / ".labb"


def user_file() -> Path:
    return USER_DIR / "settings.json"


def project_file(workdir: str) -> Path:
    return Path(workdir) / ".labb" / "settings.json"


def permissions_file(workdir: str) -> Path:
    """The commands remembered in a project (permissions.py)"""
    return Path(workdir) / ".labb" / "permissions.json"


# The type of each known setting; a value of the wrong type is left out, with a problem reported
TYPES = {
    "model": str,
    "models": list,
    "defaultMode": str,
    "answerMode": str,
    "ollamaNumCtx": int,
    "theme": str,
    "showThinking": bool,
    "permissions": dict,
    "mcpServers": dict,
    "hooks": dict,
    "tools": dict,
    "browser": bool,
    "browserSites": list,
    "prices": dict,
    "maxCostPerSession": (int, float),
    "parallelTools": int,
    "engine": str,
}


KIND_NAMES = {int: "a whole number", bool: "true or false", str: "a string", list: "a list", dict: "an object", (int, float): "a number"}


def read(path: Path, problems: list[str]) -> dict:
    """A settings file, or {} if there's none; problems with it are added to `problems`"""

    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        problems.append(f"{path}: {e}")
        return {}
    if not isinstance(data, dict):
        problems.append(f"{path}: should be a JSON object")
        return {}
    for key, kind in TYPES.items():
        if key in data and not isinstance(data[key], kind):
            problems.append(f"{path}: {key} should be {KIND_NAMES[kind]}")
            del data[key]
    if not isinstance(data.get("permissions", {}).get("allow", []), list):
        problems.append(f"{path}: permissions.allow should be a list")
        del data["permissions"]
    for key in ("allow", "deny"):
        names = data.get("tools", {}).get(key, [])
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            problems.append(f"{path}: tools.{key} should be a list of tool names")
            data["tools"].pop(key)
    if data.get("defaultMode") not in (None, "default", "acceptEdits", "plan"):
        problems.append(f'{path}: defaultMode should be "default", "acceptEdits" or "plan"')
        del data["defaultMode"]
    return data


def merge(base: dict, override: dict) -> dict:
    merged = {**base, **override}
    for key in ("models",):
        merged[key] = list(dict.fromkeys([*base.get(key, []), *override.get(key, [])]))
    merged["permissions"] = {
        "allow": list(dict.fromkeys([*base.get("permissions", {}).get("allow", []), *override.get("permissions", {}).get("allow", [])])),
    }
    tools = {"deny": list(dict.fromkeys([*base.get("tools", {}).get("deny", []), *override.get("tools", {}).get("deny", [])]))}
    if "allow" in override.get("tools", {}) or "allow" in base.get("tools", {}):
        tools["allow"] = override.get("tools", {}).get("allow", base.get("tools", {}).get("allow"))
    merged["tools"] = tools
    merged["mcpServers"] = {**base.get("mcpServers", {}), **override.get("mcpServers", {})}
    hooks = {}
    for source in (base.get("hooks", {}), override.get("hooks", {})):
        for event, entries in source.items():
            hooks[event] = hooks.get(event, []) + list(entries)
    merged["hooks"] = hooks
    return merged


def load(workdir: str | None = None) -> tuple[dict, list[str]]:
    """The settings for a directory (just the user's for None), and any problems reading them"""

    problems: list[str] = []
    settings = merge({}, read(user_file(), problems))
    if workdir and not is_users_own(workdir):
        project = read(project_file(workdir), problems)
        # A repository you cloned doesn't get to say what the browser may do without asking
        if project.pop("browserSites", None) is not None:
            problems.append(f"{project_file(workdir)}: browserSites only counts in {user_file()}")
        if (asked := needs_trust(project)) and not is_trusted(workdir, "settings", asked):
            for key in asked:
                if key == "permissions":
                    project["permissions"] = {**project["permissions"], "allow": []}
                else:
                    project.pop(key)
            problems.append(f"{project_file(workdir)}: {', '.join(asked)} left out until you agree to them "
                            "(run labb in this folder in a terminal to be asked)")
        settings = merge(settings, project)
    return settings, problems


# --- Agreeing to what a project's own files ask for ------------------------------

def needs_trust(project: dict) -> dict:
    """The parts of a project's settings that run code or skip asking, which need agreeing to"""

    found = {key: project[key] for key in ("hooks", "mcpServers") if project.get(key)}
    if allow := project.get("permissions", {}).get("allow"):
        found["permissions"] = {"allow": allow}
    if project.get("defaultMode") == "acceptEdits":
        found["defaultMode"] = "acceptEdits"
    return found


def is_users_own(workdir: str) -> bool:
    """The home folder: its .labb is the user's own"""
    try:
        return Path(workdir).resolve() == Path.home().resolve()
    except OSError:
        return False


def trust_file() -> Path:
    return USER_DIR / "trusted-projects.json"


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _trusted() -> dict:
    try:
        found = json.loads(trust_file().read_text())
        return found if isinstance(found, dict) else {}
    except (OSError, ValueError):
        return {}


def is_trusted(workdir: str, part: str, value) -> bool:
    """Whether the user agreed to this part ("settings" or "permissions") of a folder's .labb, as it is now"""
    if not value or is_users_own(workdir):
        return True
    return _trusted().get(os.path.realpath(workdir), {}).get(part) == _digest(value)


def trust(workdir: str, part: str, value):
    known = _trusted()
    folder = known.setdefault(os.path.realpath(workdir), {})
    folder[part] = _digest(value)
    file = trust_file()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(known, indent=1) + "\n")


def remembered_commands(workdir: str) -> list[str]:
    """.labb/permissions.json's commands as written, trusted or not"""
    try:
        rules = json.loads(permissions_file(workdir).read_text()).get("allow", [])
        return [rule for rule in rules if isinstance(rule, str)]
    except (OSError, ValueError, AttributeError):
        return []


def untrusted(workdir: str) -> dict:
    """What a folder's .labb asks for that the user hasn't agreed to: {"settings": …, "permissions": [...]}"""

    found = {}
    if is_users_own(workdir):
        return found
    asked = needs_trust(read(project_file(workdir), []))
    if asked and not is_trusted(workdir, "settings", asked):
        found["settings"] = asked
    commands = remembered_commands(workdir)
    if commands and not is_trusted(workdir, "permissions", commands):
        found["permissions"] = commands
    return found


def trust_all(workdir: str, parts: dict):
    """Agree to what `untrusted` found"""
    for part, value in parts.items():
        trust(workdir, part, value)
