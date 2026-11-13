"""The whole graph and runner with a fake model: tool calls, saving, stats, taking back, titles."""

import threading
from dataclasses import replace
from uuid import uuid4

from langchain_core.messages import AIMessage

from labb import chats
from labb import llm
from labb import runner
from labb.agent import chat_agent
from conftest import FakeModel, ai


def reply(thread_id: str, text: str, config: dict | None = None, **kwargs) -> list[dict]:
    events = []
    runner.run_reply(thread_id, text, threading.Event(), events.append, configurable=(config or {}).get("configurable"), **kwargs)
    return events


def messages(thread_id: str) -> list:
    return chat_agent.get_state({"configurable": {"thread_id": thread_id}}).values["messages"]


def test_a_tool_call_then_an_answer(fake_model, config, tmp_path):
    (tmp_path / "hello.txt").write_text("hi")
    fake_model(ai(tool_calls=[("list_files", {"path": "."})]), ai("There's one file, hello.txt."))
    thread = f"cli-{uuid4()}"
    events = reply(thread, "What's here?", config, name=False)

    kinds = [e["type"] for e in events]
    assert kinds[0] == "tool_call" and "tool_result" in kinds and kinds[-1] == "done"
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["id"] == "call-0-list_files" and not result["error"]
    assert [m.type for m in messages(thread)] == ["human", "ai", "tool", "ai"]
    assert "hello.txt" in messages(thread)[2].content
    # The stats are saved under the final reply's id
    stats = chats.get_stats(thread)
    assert list(stats) == [messages(thread)[-1].id] and stats[messages(thread)[-1].id]["tool_calls"] == 1


def test_unknown_tools_and_tool_errors_go_back_to_the_model(fake_model, config):
    fake_model(ai(tool_calls=[("no_such_tool", {})]), ai("Sorry."))
    thread = f"cli-{uuid4()}"
    reply(thread, "Try it", config, name=False)
    tool_message = messages(thread)[2]
    assert tool_message.status == "error" and "unknown tool" in tool_message.content


def test_an_edited_message_replaces_the_rest(fake_model):
    fake_model(ai("one"), ai("two"), ai("three"))
    thread = str(uuid4())
    reply(thread, "first", name=False)
    reply(thread, "second", name=False)
    reply(thread, "second, edited", replace_from=1, name=False)
    assert [m.text for m in messages(thread)] == ["first", "one", "second, edited", "three"]
    assert len(chats.get_stats(thread)) == 2


def test_replacing_a_message_that_isnt_there_is_refused(fake_model):
    fake_model(ai("one"))
    thread = str(uuid4())
    reply(thread, "first", name=False)
    events = reply(thread, "x", replace_from=3)
    assert events[0]["type"] == "error" and "no such message" in events[0]["message"]


def test_the_first_reply_titles_the_chat(fake_model, monkeypatch):
    fake_model(ai("Magnets attract iron."))
    titler = replace(llm.default, summary_model=FakeModel(messages=iter([AIMessage('"Why Magnets Attract Iron."')])))
    monkeypatch.setattr(llm, "for_config", lambda config: titler)
    thread = str(uuid4())
    chats.touch(thread, "why do magnets attract iron?")
    events = reply(thread, "why do magnets attract iron?")
    assert {"type": "title", "title": "Why Magnets Attract Iron"} in events
    assert next(c for c in chats.list_chats() if c["thread_id"] == thread)["title"] == "Why Magnets Attract Iron"


def test_a_renamed_chat_keeps_its_title(fake_model, monkeypatch):
    fake_model(ai("Sure."))
    titler = replace(llm.default, summary_model=FakeModel(messages=iter([AIMessage("Something Else")])))
    monkeypatch.setattr(llm, "for_config", lambda config: titler)
    thread = str(uuid4())
    chats.touch(thread, "hello")
    chats.update_chat(thread, title="My chat")
    events = reply(thread, "hello")
    assert not any(e["type"] == "title" for e in events)
    assert next(c for c in chats.list_chats() if c["thread_id"] == thread)["title"] == "My chat"


def test_attachments_round_trip():
    message = runner.HumanMessage(runner.with_attachments("look at these", {"a.py": "print(1)", "b/c.md": "# hi"}))
    assert runner.split_user_message(message) == ("look at these", ["a.py", "b/c.md"], [])
