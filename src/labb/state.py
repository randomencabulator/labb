from langchain.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict, Annotated, NotRequired


def merged(old: dict | None, new: dict | None) -> dict:
    return {**(old or {}), **(new or {})}


class MessagesState(TypedDict):
    # add_messages appends, and also lets the terminal remove messages (RemoveMessage) to edit its last one
    messages: Annotated[list[AnyMessage], add_messages]
    llm_calls: int
    # Summary of the first `summarized` messages, sent to the model instead of them (see context.py)
    summary: NotRequired[str]
    summarized: NotRequired[int]
    # The user's messages among those, word for word as far as they fit, sent next to the summary
    user_words: NotRequired[str]
    # Large tool results before this message number are cleared in what the model gets; it only
    # moves on, in jumps, so the input changes rarely (see context.py)
    cleared_before: NotRequired[int]
    # The model was told older turns are about to be summarized, so it could save notes first
    flushed: NotRequired[bool]
    # The memories the latest user message may be about, added to it for the model only (see memories.py)
    recalled: NotRequired[list[dict]]
    # For an engine's own steps (engine_steps.py). They're channels of every engine, so a chat
    # can move between engines. Saved values by name, kept across replies:
    vars: NotRequired[Annotated[dict, merged]]
    # What the model's next call gets besides the conversation, only then
    notes: NotRequired[list[str]]
    # How often each step ran in this reply, and the way the last branch chose
    visits: NotRequired[dict]
    route: NotRequired[str]
    # What the last step did, for the runner's stats and events
    step: NotRequired[dict]
    # A reply that ended too early (continuation.py): why the model goes round again, None when
    # it doesn't, and how often it went round for each reason in this reply
    continuing: NotRequired[str | None]
    continued: NotRequired[dict]
