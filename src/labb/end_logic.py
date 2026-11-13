from typing import Literal

from langgraph.graph import END

from labb.state import MessagesState


def should_continue(state: MessagesState) -> Literal["tool_node", "compact", END]:
    """Decide if we should continue the loop or stop based upon whether the LLM made a tool call"""

    messages = state["messages"]
    # The list can be empty after the terminal takes back the only message (Esc Esc)
    last_message = messages[-1] if messages else None

    # If the LLM makes a tool call, then perform an action
    if getattr(last_message, "tool_calls", None):
        return "tool_node"

    # The reply ended too early (continuation.py): round again, with a note saying why
    if state.get("continuing"):
        return "compact"

    # Otherwise, we stop (reply to the user)
    return END
