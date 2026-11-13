"""A todo list for the work in hand.

The model rewrites the whole list as it goes; the terminal and the web chat show it under the
call. It's kept per conversation (work.py): in the terminal <workdir>/.labb/todos/<thread>.json
(git-ignored), so a continued session (`labb -c`, `/todos`) still has it; elsewhere in the chats
database, so the web chat, agents and tasks keep one too.

Steps can't vanish: a rewrite that leaves out an open step gets it back, with a note to mark it
`skipped` (or `blocked`) with the reason instead. A reply that ends with open steps goes round
again (continuation.py).
"""

from typing import Literal

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from labb import work

MAX_ITEMS = 30
MAX_NOTE_CHARS = 300
MARKS = {"pending": "[ ]", "in_progress": "[>]", "done": "[x]", "blocked": "[!]", "skipped": "[-]"}
# Steps still to do; the others are resolved, done or not
OPEN = ("pending", "in_progress")
# Over this many steps, the reminder shows only what's next (short_show)
SHORT_OVER = 8
SHORT_NEXT = 3


class Todo(BaseModel):
    task: str = Field(description="One step, short and specific, e.g. \"Add a test for empty input\"")
    status: Literal["pending", "in_progress", "done", "blocked", "skipped"] = "pending"
    note: str = Field(default="", description="Why, for a blocked or skipped step (needed for those)")
    id: int | None = Field(default=None, description="The step's number from the list, to keep it when you reword it")


def config_of(workdir: str, thread_id: str | None) -> dict:
    return {"configurable": {"workdir": workdir, "thread_id": thread_id}}


def todo_file(workdir: str, thread_id: str | None):
    return work.file_of(workdir, thread_id, "todos")


def items_for(config: RunnableConfig | None) -> list[dict]:
    """The conversation's list, or [] if it has none or it's broken"""
    items = work.read_json(config, "todos", [])
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("task")]


def save_for(config: RunnableConfig | None, items: list[dict]):
    work.write_json(config, "todos", items)


def load(workdir: str, thread_id: str | None) -> list[dict]:
    """A project's list for this conversation (the terminal's file)"""
    return items_for(config_of(workdir, thread_id))


def save(workdir: str, thread_id: str | None, items: list[dict]):
    save_for(config_of(workdir, thread_id), items)


def usable(config: RunnableConfig | None) -> bool:
    return work.usable(config)


def open_items(items: list[dict]) -> list[dict]:
    return [item for item in items if item.get("status", "pending") in OPEN]


def line(item: dict, number: int) -> str:
    note = f" ({item['note']})" if item.get("note") else ""
    return f"{MARKS.get(item.get('status'), '[ ]')} {item.get('id', number)}. {item['task']}{note}"


def show(items: list[dict]) -> str:
    if not items:
        return "The todo list is empty."
    done = sum(item.get("status") == "done" for item in items)
    lines = [f"Todo list ({done} of {len(items)} done):"]
    lines += [line(item, i) for i, item in enumerate(items, 1)]
    return "\n".join(lines)


def short_show(items: list[dict]) -> str:
    """For the reminder each call: a long list only by what's in progress, next and blocked"""

    if len(items) <= SHORT_OVER:
        return show(items)
    counts = {status: sum(item.get("status", "pending") == status for item in items) for status in MARKS}
    numbered = list(enumerate(items, 1))
    shown = [(i, item) for i, item in numbered if item.get("status") in ("in_progress", "blocked")]
    shown += [(i, item) for i, item in numbered if item.get("status", "pending") == "pending"][:SHORT_NEXT]
    said = ", ".join(f"{count} {status.replace('_', ' ')}" for status, count in counts.items() if count)
    lines = [f"Todo list ({said}); in progress, blocked and next:"]
    lines += [line(item, i) for i, item in sorted(shown, key=lambda pair: pair[0])]
    return "\n".join(lines)


def _key(text: str) -> str:
    return " ".join(text.lower().split())


def merged(old: list[dict], new: list[dict]) -> tuple[list[dict], list[dict]]:
    """The new list with ids (kept from the old steps they match, by id or by text), and the old
    open steps it left out, which go back at the end"""

    # Lists from before steps had ids get them by their place
    if any(not isinstance(item.get("id"), int) for item in old):
        old = [{**item, "id": i} for i, item in enumerate(old, 1)]
    by_id ={item["id"]: item for item in old if isinstance(item.get("id"), int)}
    by_text = {}
    for item in old:
        by_text.setdefault(_key(item["task"]), item)
    taken, items = set(), []
    next_id = max([*by_id, 0]) + 1
    for item in new:
        item_id = item.get("id")
        if item_id not in by_id or item_id in taken:
            found = by_text.get(_key(item["task"]))
            item_id = found.get("id") if found else None
        if not isinstance(item_id, int) or item_id in taken:
            item_id, next_id = next_id, next_id + 1
        taken.add(item_id)
        items.append({**{k: v for k, v in item.items() if k != "id"}, "id": item_id})
    dropped = [item for item in old if item.get("status", "pending") in OPEN and item.get("id") not in taken]
    return items + dropped, dropped


@tool
def update_todos(todos: list[Todo], explanation: str = "", config: RunnableConfig = None) -> str:
    """Write the todo list for the job in hand, replacing the previous one. Send the whole list
    every time, with each step's status; keep exactly one step in_progress while you work. A step
    you won't do is `skipped`, one you can't do is `blocked`, each with the reason in `note`.

    Args:
        todos: Every step, in order
        explanation: Why the plan changed, in a sentence, if it did
    """
    if not usable(config):
        return "Error: this conversation has nowhere to keep a todo list"
    items = []
    for t in todos[:MAX_ITEMS]:
        data = t.model_dump() if isinstance(t, BaseModel) else dict(t)
        task = str(data.get("task", "")).strip()
        if not task:
            continue
        status = data.get("status") if data.get("status") in MARKS else "pending"
        note = " ".join(str(data.get("note") or "").split())[:MAX_NOTE_CHARS]
        if status in ("blocked", "skipped") and not note:
            return f"Error: say why \"{task}\" is {status} in its `note`. Nothing was saved."
        item = {"task": task, "status": status, **({"note": note} if note else {})}
        if isinstance(data.get("id"), int):
            item["id"] = data["id"]
        items.append(item)
    items, dropped = merged(items_for(config), items)
    try:
        save_for(config, items[:MAX_ITEMS + len(dropped)])
    except (OSError, ValueError) as e:
        return f"Error: can't save the todo list: {e}"
    result = show(items)
    if dropped:
        result += (f"\nYou left out {len(dropped)} open step{'s' if len(dropped) > 1 else ''} ("
                   + "; ".join(item["task"] for item in dropped)
                   + "): kept at the end. If they're no longer needed, mark them skipped with the reason in `note`.")
    if sum(item["status"] == "in_progress" for item in items) > 1:
        result += "\nNote: more than one step is in_progress; finish one before starting the next."
    if explanation.strip():
        result += f"\nWhy it changed: {' '.join(explanation.split())[:MAX_NOTE_CHARS]}"
    return result


TOOLS = [update_todos]

PROMPT = (
    "For a job of 3 steps or more, your plan is the todo list: write the steps with `update_todos` "
    "before you start, then work through them one at a time. Mark a step in_progress when you "
    "start it and done as soon as it's finished (checked, not just written), adding steps you "
    "discover. A step you won't or can't do is `skipped` or `blocked`, with the reason in `note`: "
    "never leave it out. You're shown the open list as you work, and a reply with open steps "
    "isn't the end of the job. Don't use it for small, one-step requests."
)
