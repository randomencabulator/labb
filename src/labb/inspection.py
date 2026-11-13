"""What the context panel asks about a chat, answered the same way wherever the chat runs: on the
server (server/app.py's /context routes) or, for a session on a PC, by that session's own process
(cli/app.py's `answer_context`, asked through labb connect), which has the prompt, tools and
settings the server can't rebuild."""

from langchain_core.messages.utils import convert_to_openai_messages
from langchain_core.utils.function_calling import convert_to_openai_tool

from labb import chats, llm, trace
from labb import work as kept
from labb.agent import chat_agent
from labb.context import image_count
from labb.model import compact_context, inspect_context, model_input, plan_file, tools_of
from labb.tools import goal, todo
from labb.runner import lock_for

# Replies a chat's waterfall can go back through
TRACES_SHOWN = 50


class Busy(Exception):
    """A reply or task is writing in the chat"""


def state_of(config: dict) -> dict:
    return {"messages": [], **chat_agent.get_state(config).values}


def without_image_data(message: dict) -> dict:
    """A sent message with its images' data left out: it would swamp what's being looked at"""

    if not isinstance(message.get("content"), list):
        return message
    content = [{"type": "image", "data": "(left out)"} if isinstance(block, dict) and block.get("type") in ("image", "image_url") else block
               for block in message["content"]]
    return {**message, "content": content}


def work(config: dict) -> dict:
    """What the conversation is working on, as it stands now (work.py): its todo list with how many
    steps are open, its goal, and the plan the user approved with where it's kept"""

    items = todo.items_for(config) if todo.usable(config) else []
    return {"todos": items, "open": len(todo.open_items(items)), "goal": goal.get(config),
            "plan": kept.read(config, "plan"), "plan_file": plan_file(config)}


def seen(config: dict, scale: float | None = None) -> dict:
    """What the next model call would get, part by part with estimated tokens"""
    return inspect_context(state_of(config), config, scale)


def raw(config: dict) -> dict:
    """The next model call's whole input as JSON, before each provider's own formatting"""
    state = state_of(config)
    return {
        "model": llm.for_config(config).spec,
        "messages": [without_image_data(m) for m in convert_to_openai_messages(model_input(state, config))],
        "tools": [convert_to_openai_tool(tool) for tool in tools_of(config)],
    }


def message(thread_id: str, number: int) -> dict | None:
    """A message as it's saved, by its index; None if there's no such one"""

    messages = chat_agent.get_state({"configurable": {"thread_id": thread_id}}).values.get("messages", [])
    if not 0 <= number < len(messages):
        return None
    found = messages[number]
    return {
        "index": number,
        "role": found.type,
        "name": found.name if found.type == "tool" else None,
        "text": found.text,
        "images": image_count(found),
        "tool_calls": [{"name": c["name"], "args": c["args"]} for c in getattr(found, "tool_calls", [])],
    }


def traces(thread_id: str) -> list[dict]:
    """What each of the chat's replies did and when, with the one being written last"""
    found = chats.get_traces(thread_id, TRACES_SHOWN)
    if recording := trace.live.get(thread_id):
        found.append({"id": "live", **recording.to_dict()})
    return found


def summarize(config: dict, busy: bool = False) -> dict:
    """Summarize every turn not summarized yet, now; the inspection afterwards. Raises Busy while a
    reply writes in the chat, and the model's error if it fails."""

    thread_id = config["configurable"]["thread_id"]
    lock = lock_for(thread_id)
    if busy or not lock.acquire(blocking=False):
        raise Busy("This chat is busy: wait for the reply to finish")
    try:
        update = compact_context(state_of(config), config, force=True)
        if update:
            # As llm_call, so the graph sees a finished reply and the next message starts a new run
            chat_agent.update_state(config, update, as_node="llm_call")
        return seen(config)
    finally:
        lock.release()
