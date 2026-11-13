"""Tools that wait to be found. When the tools a run is offered would take more than the engine's
`tool_share` of the context window, those that aren't core (MCP servers', and toolsets marked
DEFERRABLE: mail, calendar, the browser, apps, PCs) aren't sent to the model with their schemas.
The system prompt lists them by name and what they're for, and `find_tools` loads the ones the
model needs: from then on they're bound like any other for the rest of the conversation.

What's loaded is read from the conversation itself (the "Loaded: …" line of each find_tools
result), so it needs no state of its own and a loaded tool stays loaded; the list of tools only
grows, so the provider's prompt cache is broken once per load, not every call. An MCP server
can keep its tools always loaded with "alwaysLoad": true in its settings.
"""

import json
import re

from langchain.tools import tool
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables import RunnableConfig
from langchain_core.utils.function_calling import convert_to_openai_tool

# Offered only when some tools wait to be found (model.tools_of adds it)
ONLY_IF_ALLOWED = True

MAX_FOUND = 5
# How much of each waiting tool's description the system prompt lists
LISTED_CHARS = 100
LOADED = re.compile(r"^Loaded: (.+)$", re.MULTILINE)

_tokens: dict[tuple, int] = {}


def tool_tokens(tools: list) -> int:
    """What the tools' schemas cost, remembered by their names"""

    key = tuple(tool.name for tool in tools)
    if key not in _tokens:
        if len(_tokens) > 256:
            _tokens.clear()
        _tokens[key] = count_tokens_approximately([], tools=tools) if tools else 0
    return _tokens[key]


def deferrable(tool) -> bool:
    from labb.tools import TOOLSETS, name_of, toolset_of

    if (getattr(tool, "metadata", None) or {}).get("always_load"):
        return False
    if tool.name.startswith("mcp__"):
        return True
    toolset = toolset_of.get(tool.name)
    return any(getattr(t, "DEFERRABLE", False) for t in TOOLSETS if name_of(t) == toolset)


def waiting(tools: list, window: int, share: float) -> list:
    """The tools that wait to be found, if the whole list is too big for its share of the window"""

    if not window or tool_tokens(tools) <= share * window:
        return []
    return [tool for tool in tools if deferrable(tool)]


def loaded(messages: list | None) -> set[str]:
    """The tools find_tools loaded in this conversation"""

    names = set()
    for message in messages or []:
        if message.type == "tool" and message.name == "find_tools" and isinstance(message.content, str):
            for found in LOADED.findall(message.content):
                names |= {name.strip() for name in found.split(",") if name.strip()}
    return names


def listing(tools: list) -> str:
    """For the system prompt: what waits to be found"""

    lines = []
    for each in tools:
        about = " ".join((each.description or "").split())
        first = re.split(r"(?<=[.!?])\s", about)[0]
        lines.append(f"- {each.name}: {first[:LISTED_CHARS]}")
    return ("These tools are ready but not loaded, to save room: load the ones you need with "
            "`find_tools` (by name or by what you want to do), then call them.\n" + "\n".join(lines))


def score(tool, words: list[str]) -> int:
    text = f"{tool.name} {tool.description or ''}".lower().replace("_", " ")
    return sum(word in text for word in words) + 3 * any(word == tool.name.lower() for word in words)


@tool
def find_tools(query: str, config: RunnableConfig = None) -> str:
    """Load tools that are ready but not loaded yet (listed in your instructions), by name or by
    what you want to do, e.g. "send an email" or "mcp__github__create_issue". Once loaded, call
    them like your other tools.

    Args:
        query: Tool names, or words for what you want to do
    """
    from labb import model

    candidates = [each for each in model.tools_of(config, everything=True) if deferrable(each)]
    if not candidates:
        return "No tools are waiting to be loaded."
    words = [word for word in re.split(r"[\s,]+", query.lower()) if word]
    ranked = sorted(((score(each, words), i, each) for i, each in enumerate(candidates)), key=lambda found: (-found[0], found[1]))
    found = [each for points, _, each in ranked if points > 0][:MAX_FOUND]
    if not found:
        return "No tool matches that. Try other words, or one of the names listed in your instructions."
    shown = []
    for each in found:
        function = convert_to_openai_tool(each)["function"]
        shown.append(f"{each.name}: {function.get('description', '')}\nParameters: "
                     f"{json.dumps(function.get('parameters', {}), ensure_ascii=False)}")
    return "\n\n".join(shown) + f"\n\nLoaded: {', '.join(each.name for each in found)}"


RUN_TOGETHER = ("find_tools",)
TOOLS = [find_tools]
