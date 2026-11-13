"""Remembering things about the user across conversations (see memories.py)"""

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig

from labb import memories

# How close an existing memory has to be to be shown as one this may repeat
SIMILAR_SCORE = 0.75


@tool
def remember(text: str, always: bool = False, replace_id: str | None = None, config: RunnableConfig = None) -> str:
    """Save a lasting fact or preference about the user for future conversations.

    Args:
        text: One short fact in the third person, e.g. "Prefers answers in German"
        always: True only for core facts that matter in every conversation (their name,
            language, how they want answers); others are recalled when relevant
        replace_id: The id of a memory this updates or corrects, instead of adding a new one
    """
    try:
        similar = [] if replace_id else memories.recall(config, text, k=3, min_score=SIMILAR_SCORE, pinned=True)
        memory = memories.remember(config, text, pinned=always, replace_id=replace_id, by_agent=True)
    except memories.MemoryError as e:
        return f"Error: {e}"
    if replace_id:
        return f"Updated memory [{memory['id']}]."
    if memory.get("existed"):
        return f"Already remembered as [{memory['id']}]."
    note = f"Remembered as [{memory['id']}]{' (always)' if always else ''}."
    if others := [m for m in similar if m["id"] != memory["id"]]:
        note += " Similar memories: " + "; ".join(f"[{m['id']}] {m['text']}" for m in others)
        note += ". If this replaces one of them, forget the old one."
    return note


@tool
def forget(memory_id: str, config: RunnableConfig = None) -> str:
    """Forget a memory: when the user asks, or it's no longer true.

    Args:
        memory_id: The memory's id, as shown in brackets
    """
    try:
        memories.forget(config, memory_id.strip("[] "))
    except memories.MemoryError as e:
        return f"Error: {e}"
    return f"Forgot [{memory_id.strip('[] ')}]."


# For the web chat's agent editor
LABEL = "Memories"
DESCRIPTION = "Remember and forget things about the user"

TOOLS = [remember, forget]

PROMPT = (
    "You have a memory across conversations. Use `remember` for lasting facts and preferences the "
    "user shares or asks you to keep (their name, work, projects, tools, how they like answers), "
    "not for details of the current task, and never for passwords, keys or other secrets. Keep each "
    "memory to one fact. Pinned memories are in this prompt and relevant ones come with the user's "
    "message in <memories>; to correct one, pass its id as `replace_id`, and `forget` what's no longer true. "
    "Don't mention your memory unless it matters or you're asked."
)
