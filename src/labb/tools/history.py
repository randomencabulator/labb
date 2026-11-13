"""Looking back at this conversation: the parts the model no longer sees in full.

The saved conversation stays complete; only the model's input is cut down (context.py:
old tool results cleared, older turns summarized). `look_back` reads a message again by its
number (the `#12` in a cleared result's note) or searches the whole conversation. tool_node
passes the thread's messages in `configurable["history"]`, so this module needs no graph.
"""

import json

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig

# How much of a message one call returns, and how many matching lines a search does
PAGE_CHARS = 8_000
MAX_HITS = 20
HIT_CHARS = 300


def text_of(message) -> str:
    """What's searchable in a message: its text, and an AI message's tool calls"""

    text = message.text
    for call in getattr(message, "tool_calls", None) or []:
        text += f"\nCalled {call['name']}({json.dumps(call.get('args'), ensure_ascii=False, default=str)})"
    return text.strip()


def who(message) -> str:
    if message.type == "tool":
        return f"result of {message.name or 'a tool'}"
    return {"human": "user", "ai": "you"}.get(message.type, message.type)


def read(messages: list, number: int, page: int) -> str:
    if not 0 <= number < len(messages):
        return f"Error: there's no message #{number}; this conversation has #0 to #{len(messages) - 1}."
    text = text_of(messages[number])
    pages = max(1, -(-len(text) // PAGE_CHARS))
    if not 1 <= page <= pages:
        return f"Error: message #{number} has {pages} page{'s' if pages > 1 else ''}."
    part = text[(page - 1) * PAGE_CHARS:page * PAGE_CHARS]
    more = f" (page {page} of {pages}: look_back(number={number}, page={page + 1}) for more)" if page < pages else ""
    return f"Message #{number}, {who(messages[number])}{more}:\n\n{part}"


def search(messages: list, query: str) -> str:
    words = query.lower().split()
    hits = []
    for number, message in enumerate(messages):
        # Earlier look-ups would only find themselves
        if message.type == "tool" and message.name == "look_back":
            continue
        lines = [line.strip() for line in text_of(message).splitlines()]
        # The whole phrase, else every word on the line
        found = [line for line in lines if query.lower() in line.lower()] or \
                [line for line in lines if all(word in line.lower() for word in words)]
        hits += [(number, message, line) for line in found if line]
    if not hits:
        return f'Nothing in this conversation matches "{query}".'
    shown = hits[-MAX_HITS:]
    lines = [f"#{number} ({who(message)}): {line[:HIT_CHARS]}" for number, message, line in shown]
    note = f"\n({len(hits) - len(shown)} earlier matches not shown: search for something narrower)" if len(hits) > len(shown) else ""
    return (f'Matches for "{query}", oldest first; look_back(number=N) reads a whole message:\n'
            + "\n".join(lines) + note)


@tool
def look_back(number: int | None = None, query: str = "", page: int = 1, config: RunnableConfig = None) -> str:
    """Look up earlier parts of this conversation that were cleared or summarized to save
    context: read a message by its number (e.g. the #12 in a "[Cleared … #12]" note), or
    search the whole conversation for words, like an error message, a path or a name.

    Args:
        number: The message's number, from a cleared note or a search result
        query: Words to search for, when you don't know the number
        page: For a long message, which part to read (1 is the start)
    """
    messages = ((config or {}).get("configurable") or {}).get("history")
    if messages is None:
        return "Error: this conversation's history isn't available here."
    if number is not None:
        return read(messages, number, page)
    if query.strip():
        return search(messages, query.strip())
    return "Error: give a message `number` to read, or a `query` to search for."


RUN_TOGETHER = ("look_back",)
TOOLS = [look_back]

PROMPT = (
    "Old tool results and older turns are cut from what you see, but nothing is lost: "
    "`look_back` reads a cleared result again by its #number, or searches the conversation for "
    "an error, path or name you saw earlier. Prefer it to running a command again."
)
