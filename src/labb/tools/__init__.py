"""Tool registry. To add tools, create a module in this package with a `TOOLS`
list (and optionally a `PROMPT` string for the system prompt) and add it to
`TOOLSETS`. Set `NEEDS_WORKDIR = True` in the module for tools that work on
the terminal's working directory; they're left out where there's none (the web).
A module can have a `WEB_PROMPT`, used instead of `PROMPT` where there's no working directory.
A module can also have an `enabled()` function: it's left out while that's false, and a
`LABEL` (and `DESCRIPTION`) for the web chat's agent editor, and `RUN_TOGETHER`, the tools that only read. One that uses an app the user
connects (see connections.py) names it in `CONNECTION`, and is offered only to those who have.

An agent (see server/agents.py) may use only some toolsets: the run's config then has
`agent`, and `allowed_for` names the toolsets it allows.

The terminal can also turn tools on and off for a session (`--tools`, `--disallowed-tools`,
`tools` in the settings, `/tools`): `configurable["tools"]` is then a `ToolRules`."""

import fnmatch
from typing import NamedTuple

from labb import connections, engine
from labb.tools import (apps, artifacts, browser, delegate, documents, edits, files, github, gmail, goal, google_calendar, history, loop, memory,
                            notes, notion, outlook_calendar, outlook_mail, pcs, questions, shell, skill_writing, skills, task, todo, tool_search, web)

TOOLSETS = [web, browser, documents, google_calendar, gmail, outlook_calendar, outlook_mail, github, notion, memory, skills, skill_writing,
            delegate, files, edits, shell, task, todo, goal, notes, questions, history, loop, pcs, apps, artifacts, tool_search]

# Every tool, for running the tool calls
tools = [tool for toolset in TOOLSETS for tool in toolset.TOOLS]
tools_by_name = {tool.name: tool for tool in tools}

# Tools that may run at the same time as each other: each toolset names its own in `RUN_TOGETHER`.
# Only tools that just read belong here (a stop gives up on them halfway), and nothing's in
# unless it's named, so a new tool or an MCP server's runs in order.
run_together = frozenset(name for toolset in TOOLSETS for name in getattr(toolset, "RUN_TOGETHER", ()))
DEFAULT_PARALLEL = 5


def parallel_limit() -> int:
    """How many tool calls of a reply run at once; 1 for one at a time. The server's Settings →
    Limits and the terminal's `parallelTools` replace this."""
    return DEFAULT_PARALLEL


# Tools added at runtime: labb's MCP servers (see mcp_servers.py). Like the file tools,
# they're only offered where there's a working directory.
extra_tools: list = []


def set_extra_tools(new_tools: list):
    for tool in extra_tools:
        tools_by_name.pop(tool.name, None)
    extra_tools[:] = new_tools
    tools_by_name.update({tool.name: tool for tool in new_tools})


# The server's MCP tools (Settings → MCP servers, see server/mcp.py): offered in the web chat,
# where there's no working directory, to the default assistant (the "mcp" toolset), not to agents
server_tools: list = []


def set_server_tools(new_tools: list):
    for tool in server_tools:
        tools_by_name.pop(tool.name, None)
    server_tools[:] = new_tools
    tools_by_name.update({tool.name: tool for tool in new_tools})


def name_of(toolset) -> str:
    return toolset.__name__.rsplit(".", 1)[-1]


# The toolset of each tool, to check an agent's calls against what it's allowed
toolset_of = {tool.name: name_of(toolset) for toolset in TOOLSETS for tool in toolset.TOOLS}

# An agent's documents and memories have switches of their own, not tool checkboxes; every
# agent can look back at its own conversation and keep a todo list for its work
OWN_SWITCHES = {"documents", "memory", "skills", "skill_writing", "delegate", "history", "todo"}


def choosable() -> list:
    """The toolsets an agent in the web chat can be given (no working directory there)"""
    usual = [toolset for toolset in available(False) if name_of(toolset) not in OWN_SWITCHES]
    # Not in "all tools", but it can be given by name
    return usual + [toolset for toolset in TOOLSETS if getattr(toolset, "CHOOSABLE", False) and getattr(toolset, "enabled", lambda: True)()]


def allowed_for(config: dict | None) -> frozenset[str] | None:
    """The toolsets the run's agent may use; None (all of them) without an agent"""

    agent = ((config or {}).get("configurable") or {}).get("agent")
    if not agent:
        return None
    toolsets = agent.get("toolsets")
    names = set(toolsets) if toolsets is not None else {name_of(t) for t in TOOLSETS if not getattr(t, "ONLY_IF_ALLOWED", False)} - OWN_SWITCHES
    names -= OWN_SWITCHES
    # "mine", or the documents it was given
    if agent.get("documents") not in ("none", []):
        names.add("documents")
    # "mine", or the skills it was given; only with the chatter's can it write new ones
    if agent.get("skills", "mine") not in ("none", []):
        names.add("skills")
    if agent.get("skills", "mine") == "mine":
        names.add("skill_writing")
    if agent.get("memories", True):
        names.add("memory")
    if agent.get("delegates") or agent.get("actions"):
        names.add("delegate")
    names |= {"history", "todo"}
    return frozenset(names)


# What an app's own chat isn't offered
APP_CHATS_WITHOUT = frozenset({"skills", "skill_writing"})


def offered_for(config: dict | None) -> frozenset[str] | None:
    """The toolsets to offer the model: the agent's, without the skill tools when there are no
    skills to load (small models do better with fewer tools), nor those of apps the user hasn't
    connected"""

    from labb import skills as skill_store

    allowed = allowed_for(config)
    everything = frozenset(name_of(t) for t in TOOLSETS if not getattr(t, "ONLY_IF_ALLOWED", False)) | {"mcp", "custom"}
    if (allowed is None or "skills" in allowed) and not skill_store.available(config):
        allowed = (allowed if allowed is not None else everything) - {"skills"}
    # The loop's tools are for its runs only (ONLY_IF_ALLOWED keeps them out of "all")
    if ((config or {}).get("configurable") or {}).get("loop"):
        allowed = (allowed if allowed is not None else everything) | {"loop"}
    # The browser is for those who turned it on (ONLY_IF_ALLOWED keeps it out of "all"); an agent has what it was given
    configurable = (config or {}).get("configurable") or {}
    if configurable.get("browser") and not configurable.get("agent"):
        allowed = (allowed if allowed is not None else everything) | {"browser"}
    # Likewise your PCs, once one is connected and you're there to approve it
    if configurable.get("pcs") and not configurable.get("agent"):
        allowed = (allowed if allowed is not None else everything) | {"pcs"}
    # Web pages, where the server can build and serve them (an agent has them if it was given them)
    if configurable.get("artifacts") and not configurable.get("agent"):
        allowed = (allowed if allowed is not None else everything) | {"artifacts"}
    # An app's tools are for the chat that belongs to it, which /chat checked is the user's; without
    # skills, which a small model only wanders into (its notes are the app's own: see tools/notes.py)
    if configurable.get("app") and not configurable.get("agent"):
        allowed = ((allowed if allowed is not None else everything) | {"apps"}) - APP_CHATS_WITHOUT
    # Notes are a project's, or the person's on the server: a run with neither has none
    if not notes.usable(config):
        allowed = (allowed if allowed is not None else everything) - {"notes"}
    # Todos and goals are kept per conversation: a run that isn't one has none
    if not todo.usable(config):
        allowed = (allowed if allowed is not None else everything) - {"todo"}
    # The goal's tools are for a conversation with an active goal (ONLY_IF_ALLOWED keeps them out of "all")
    elif not configurable.get("preview") and goal.active(config):
        allowed = (allowed if allowed is not None else everything) | {"goal"}
    needing = {name_of(t): t.CONNECTION for t in TOOLSETS if getattr(t, "CONNECTION", None)}
    if needing and any(t.enabled() for t in TOOLSETS if name_of(t) in needing):
        connected = connections.of(config)
        if missing := {name for name, app in needing.items() if app not in connected}:
            allowed = (allowed if allowed is not None else everything) - missing
    return allowed


def is_allowed(tool_name: str, config: dict | None) -> bool:
    allowed = allowed_for(config)
    # A goal belongs to the conversation, not the agent: its tools are offered only while it's active;
    # find_tools only loads what the run may use anyway
    return allowed is None or toolset_of.get(tool_name) in allowed | {"goal", "tool_search"}


def available(has_workdir: bool, allowed: frozenset[str] | None = None) -> list:
    return [
        toolset for toolset in TOOLSETS
        if (has_workdir or not getattr(toolset, "NEEDS_WORKDIR", False)) and getattr(toolset, "enabled", lambda: True)()
        # A toolset that's ONLY_IF_ALLOWED (asking agents) needs naming: "all" doesn't include it
        and (name_of(toolset) in allowed if allowed is not None else not getattr(toolset, "ONLY_IF_ALLOWED", False))
    ]


class ToolRules(NamedTuple):
    """Which tools a session may use, by tool or toolset name, or a wildcard like `mcp__github__*`:
    only those in `allow` (None: all), and none of those in `deny`, which wins"""

    allow: tuple[str, ...] | None = None
    deny: tuple[str, ...] = ()
    # A second "only those", from the run's engine: both have to leave a tool on
    within: tuple[str, ...] | None = None

    @classmethod
    def of(cls, allow=None, deny=()) -> "ToolRules | None":
        """None when nothing is restricted, so runs without rules share one cached model"""
        clean = lambda names: tuple(dict.fromkeys(n.strip() for n in names if n and n.strip()))
        rules = cls(clean(allow) if allow is not None else None, clean(deny))
        return rules if rules != cls() else None

    def matches(self, patterns: tuple[str, ...], tool_name: str) -> bool:
        toolset = toolset_of.get(tool_name, "mcp" if tool_name.startswith("mcp__") else "custom" if tool_name.startswith(engine.CUSTOM_PREFIX) else "")
        return any(fnmatch.fnmatchcase(tool_name, p) or p == toolset for p in patterns)

    def is_on(self, tool_name: str) -> bool:
        if self.matches(self.deny, tool_name):
            return False
        if self.within is not None and not self.matches(self.within, tool_name):
            return False
        return self.allow is None or self.matches(self.allow, tool_name)


def rules_for(config: dict | None) -> ToolRules | None:
    """The session's rules and its engine's, which can only turn more off"""

    rules = ((config or {}).get("configurable") or {}).get("tools")
    h = engine.of(config)
    if h.allow is None and not h.deny:
        return rules
    rules = rules or ToolRules()
    return ToolRules(rules.allow, (*rules.deny, *h.deny), h.allow)


def is_on(tool_name: str, config: dict | None) -> bool:
    """Whether the session's rules leave this tool on"""
    rules = rules_for(config)
    return rules is None or rules.is_on(tool_name)


def tools_for(has_workdir: bool, allowed: frozenset[str] | None = None, rules: ToolRules | None = None) -> list:
    """The tools to offer the model"""
    offered = [tool for toolset in available(has_workdir, allowed) for tool in toolset.TOOLS]
    if has_workdir:
        offered += extra_tools
    elif allowed is None or "mcp" in allowed:
        offered += server_tools
    return [tool for tool in offered if rules is None or rules.is_on(tool.name)]


def prompts_for(has_workdir: bool, allowed: frozenset[str] | None = None, rules: ToolRules | None = None) -> list[str]:
    """The toolsets' instructions for the system prompt, leaving out toolsets that are all turned off"""
    return [prompt for _, prompt in named_prompts_for(has_workdir, allowed, rules)]


def named_prompts_for(has_workdir: bool, allowed: frozenset[str] | None = None, rules: ToolRules | None = None) -> list[tuple[str, str]]:
    """prompts_for's instructions with the name of their toolset"""
    prompt_of = lambda toolset: (None if has_workdir else getattr(toolset, "WEB_PROMPT", None)) or getattr(toolset, "PROMPT", None)
    return [
        (name_of(toolset), prompt_of(toolset)) for toolset in available(has_workdir, allowed) if prompt_of(toolset)
        and (rules is None or any(rules.is_on(tool.name) for tool in toolset.TOOLS))
    ]
