"""One chat can move between graphs of different shapes on the real checkpointer: what an engine
with its own loop relies on (the messages and the channels stay the same, only the steps differ)."""

import threading
from uuid import uuid4

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from labb import model, runner
from labb.agent import chat_agent, checkpointer
from labb.end_logic import should_continue
from labb.state import MessagesState
from labb.tool_node import tool_node
from conftest import ai


def other_shape():
    """No recall or compact, and tool results go straight back to the model"""
    builder = StateGraph(MessagesState)
    builder.add_node("llm_call", model.llm_call)
    builder.add_node("tool_node", tool_node)
    builder.add_edge(START, "llm_call")
    builder.add_conditional_edges("llm_call", should_continue, ["tool_node", END])
    builder.add_edge("tool_node", "llm_call")
    return builder.compile(checkpointer=checkpointer)


def reply(thread: str, text: str, config: dict, stop: threading.Event | None = None, **kwargs) -> list[dict]:
    events = []
    runner.run_reply(thread, text, stop or threading.Event(), events.append, configurable=config["configurable"], name=False, **kwargs)
    return events


def on_other(thread: str, text: str, config: dict) -> list[str]:
    """A message through the other graph; the steps it took"""
    run = {"configurable": {**config["configurable"], "thread_id": thread}}
    updates = other_shape().stream({"messages": [HumanMessage(text)]}, run, stream_mode="updates")
    return [step for update in updates for step in update]


def saved(thread: str) -> list[str]:
    return [m.type for m in chat_agent.get_state({"configurable": {"thread_id": thread}}).values["messages"]]


def test_a_chat_moves_to_another_shape_and_back(fake_model, config, tmp_path):
    (tmp_path / "a.txt").write_text("hi")
    fake_model(ai("one"), ai(tool_calls=[("list_files", {"path": "."})]), ai("two"), ai("three"))
    thread = f"cli-{uuid4()}"

    reply(thread, "first", config)
    assert on_other(thread, "second", config) == ["llm_call", "tool_node", "llm_call"]
    events = reply(thread, "third", config)

    assert events[-1]["type"] == "done"
    assert saved(thread) == ["human", "ai", "human", "ai", "tool", "ai", "human", "ai"]


def test_after_a_stop_a_chat_goes_on_in_another_shape(fake_model, config):
    fake_model(ai("never seen"), ai("after the stop"), ai("back again"))
    thread = f"cli-{uuid4()}"
    stop = threading.Event()
    stop.set()

    assert reply(thread, "first", config, stop)[-1]["type"] == "stopped"
    before = len(saved(thread))
    assert on_other(thread, "second", config) == ["llm_call"]
    assert saved(thread)[before:] == ["human", "ai"]
    # Seen through the default graph, a stop can leave a step named as next: nothing may run it
    assert reply(thread, "third", config)[-1]["type"] == "done"
    assert saved(thread)[-2:] == ["human", "ai"]


def test_a_turn_of_another_shape_can_be_taken_back(fake_model, config):
    fake_model(ai("one"), ai("two"), ai("again"))
    thread = f"cli-{uuid4()}"

    reply(thread, "first", config)
    on_other(thread, "second", config)
    assert runner.can_take_back(thread, 1) is None
    runner.take_back(thread, 1)
    assert saved(thread) == ["human", "ai"]

    assert reply(thread, "third", config)[-1]["type"] == "done"
    assert saved(thread) == ["human", "ai", "human", "ai"]
