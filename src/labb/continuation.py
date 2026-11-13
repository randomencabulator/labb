"""Replies that end too early. The graph ends a reply when the model makes no tool call, but
that isn't always because the work is done: the reply was cut off at the model's length limit,
it was empty, the model said what it'd do next and stopped (small models do), its todo list
still has open steps, or its goal isn't done. `review` tells these apart after each model call
(`model.llm_call`); for each, the model is told why in a note for its next call only and goes
round again, at most `Engine.continues(reason)` times per reply.

A reply that ended as it should still passes some gates before it's the end, in this order:
open todo steps, an announcement without the doing, a self-paced loop that didn't schedule, an
active goal, a Stop hook (hooks.py) and, when an engine turns it on, a check of the evidence
(`audit`). The first that applies and hasn't used up its rounds sends the model back.

The counts are in the state's `continued`, which the runner empties at the start of a reply;
`continuing` is the reason of the round in progress, which the graph's routers read.
"""

import re

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from labb import engine, hooks
from labb.tools import goal as goals
from labb.tools import todo
from labb.tools.loop import loop_of

# What providers say when a reply hit the length limit: Ollama's done_reason, Anthropic's
# stop_reason, OpenAI's finish_reason, and the Responses API's incomplete reason
CUT_OFF = {"length", "max_tokens", "model_length", "max_output_tokens"}
# What says the model was about to do something, at the end of a short reply
INTENT = re.compile(r"\b(i'll|i will|let me(?! know)|let's|i'm going to|i am going to|next,? i|now i|i'm now)\b", re.IGNORECASE)
ANNOUNCE_MAX_CHARS = 600
# Without a todo list, only a very short announcement after doing some work counts
BARE_ANNOUNCE_MAX_CHARS = 300
REASONS = tuple(engine.CONTINUE)
# The open steps named in the note
MAX_STEPS_NAMED = 10


def stop_field(message) -> str | None:
    """Why the provider says the reply ended, as it says it"""

    meta = getattr(message, "response_metadata", None) or {}
    for key in ("done_reason", "stop_reason", "finish_reason"):
        if meta.get(key):
            return str(meta[key])
    if meta.get("status") == "incomplete":
        return str((meta.get("incomplete_details") or {}).get("reason") or "incomplete")
    return None


def was_cut_off(message) -> bool:
    return stop_field(message) in CUT_OFF


def announced(text: str, most: int = ANNOUNCE_MAX_CHARS) -> bool:
    """A short reply that ends by saying what the model will do, not with a result or a question"""

    text = text.strip()
    if not text or len(text) > most or text.endswith("?"):
        return False
    if text.endswith(":"):
        return True
    last = re.split(r"(?<=[.!])\s+", text)[-1]
    return bool(INTENT.search(last))


def asks(text: str) -> bool:
    """A reply that ends with a question for the user, who has to answer before the work goes on"""
    return text.strip().endswith("?")


def configurable_of(config: RunnableConfig | None) -> dict:
    return (config or {}).get("configurable") or {}


def todos_of(config: RunnableConfig | None) -> list[dict]:
    try:
        return todo.items_for(config) if todo.usable(config) else []
    except Exception:
        return []


def open_todos(config: RunnableConfig | None) -> bool:
    return bool(todo.open_items(todos_of(config)))


def this_reply(state: dict) -> list:
    """The reply's messages so far: those after the user's latest message"""

    messages = state.get("messages") or []
    start = max((i for i, m in enumerate(messages) if m.type == "human"), default=-1) + 1
    return messages[start:]


def tools_used(state: dict) -> set[str]:
    """The tools this reply called before its last model call"""
    return {call["name"] for m in this_reply(state) if m.type == "ai" for call in m.tool_calls}


def unscheduled_loop(config: RunnableConfig | None) -> bool:
    """A self-paced loop's run whose model said neither when to run again nor to stop"""
    loop = loop_of(config)
    return loop is not None and not loop.every and loop.scheduled is None and loop.ended is None


def end_reason(reply: AIMessage, config: RunnableConfig | None) -> str:
    """Why the reply ended, by itself: "tools", "done", "truncated" or "empty". The gates
    (`gates`) decide whether a "done" is the end."""

    if was_cut_off(reply):
        return "truncated"
    if reply.tool_calls:
        return "tools"
    if not reply.text.strip():
        return "empty"
    return "done"


def gates(reply: AIMessage, state: dict, config: RunnableConfig | None, counts: dict):
    """The reasons a finished reply may not be the end yet, in order, each with its note; each is
    only worked out when the ones before it don't apply (a Stop hook runs a command)"""

    text = reply.text
    configurable = configurable_of(config)
    used = tools_used(state)
    items = todos_of(config)
    still_open = todo.open_items(items)
    planning = configurable.get("mode") == "plan"

    # Open steps: unless the model asks the user something, plans, or the list is from earlier
    # work and this reply only talked (an answer to a question in between)
    if still_open and not planning and not asks(text) and used:
        steps = "\n".join(todo.line(item, i) for i, item in enumerate(still_open[:MAX_STEPS_NAMED], 1))
        yield "open_steps", ("continue_open_steps", {"steps": steps})
    # Said but not done: with open steps, any short announcement; without a list, a very short one after some work
    if not planning and (announced(text) if still_open else used and announced(text, BARE_ANNOUNCE_MAX_CHARS)):
        yield "announced", ("continue_announced", {})
    if unscheduled_loop(config):
        yield "loop_unscheduled", ("continue_loop", {})
    goal = None if planning else goals.active(config)
    # In the terminal the user is there to answer a question; a task's goal goes on regardless
    if goal is not None and (configurable.get("task") or not asks(text)):
        yield "goal", ("continue_goal", {"objective": goal["objective"], "usage": goals.usage(goal)})
    if hooks.has("Stop", config):
        said = hooks.around("Stop", config, extra={
            "reply": text, "todos": items, "goal": goals.get(config), "continued": counts.get("hook", 0)})
        if said:
            yield "hook", (None, {"text": "A check said the work isn't done yet: " + " ".join(said)})
    if "update_todos" in used or goal is not None:
        yield "audit", ("continue_audit", {})


def note_for(h: engine.Engine, prompt: str | None, values: dict) -> str:
    if prompt is None:
        return values["text"]
    return engine.render(h.prompt(prompt), **values)


def without_tool_calls(reply: AIMessage) -> AIMessage:
    """The reply's text only: a tool call cut off halfway can't run, and a provider would refuse
    a call without its result. Its thinking goes too, as it led to that call."""
    return AIMessage(content=reply.text, id=reply.id, response_metadata=reply.response_metadata,
                     usage_metadata=reply.usage_metadata)


def review(reply: AIMessage, state: dict, config: RunnableConfig | None) -> tuple[AIMessage | None, dict]:
    """After a model call: the reply to keep (None to drop an empty one, so the model gets the
    same input again with the note) and what to add to the state's update; a "note" in it is
    for the next model call"""

    reason = end_reason(reply, config)
    counts = dict(state.get("continued") or {})
    h = engine.of(config)
    task = bool(configurable_of(config).get("task"))
    under_cap = lambda why: counts.get(why, 0) < h.continues(why, task)

    # A goal counts its model calls; the call that uses up its budget gets the model to wrap up
    goal, spent = goals.count_call(config, reply) if todo.usable(config) else (None, False)
    budget_note = engine.render(h.prompt("continue_goal_budget"), usage=goals.usage(goal)) if spent else None

    chosen = None
    if reason in ("truncated", "empty") and under_cap(reason):
        chosen = (reason, f"continue_{reason}", {})
    elif reason == "done" and spent and under_cap("goal_budget"):
        chosen = ("goal_budget", None, {"text": budget_note})
        budget_note = None
    elif reason == "done":
        for why, (prompt, values) in gates(reply, state, config, counts):
            if under_cap(why):
                chosen = (why, prompt, values)
                break
    if chosen is not None and chosen[0] in ("open_steps", "announced", "loop_unscheduled", "goal", "hook", "audit"):
        reason = chosen[0]
    reply.response_metadata["end_reason"] = reason

    if chosen is None:
        update = {"continuing": None}
        if budget_note:
            update["note"] = budget_note
        return reply, update
    why, prompt, values = chosen
    counts[why] = counts.get(why, 0) + 1
    if why == "goal":
        goals.next_round(config)
    if why == "truncated" and (reply.tool_calls or getattr(reply, "invalid_tool_calls", None)):
        reply, prompt = without_tool_calls(reply), "continue_truncated_tool"
    note = note_for(h, prompt, values)
    if budget_note:
        note = f"{note}\n\n{budget_note}"
    kept = reply if reply.text.strip() or reply.tool_calls else None
    return kept, {"continuing": why, "continued": counts, "note": note}
