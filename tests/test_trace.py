"""What a reply did and when: the waterfall's data, recorded from a real run of the graph."""

import threading
import time
from uuid import uuid4

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import AIMessage

from conftest import ai
from labb import chats, llm, runner, trace, tools as toolset
from labb.trace import Trace


def run(thread_id, stop=None, **kwargs):
    events = []
    runner.run_reply(thread_id, "hello", stop or threading.Event(), events.append, name=False, **kwargs)
    return events


def saved(thread_id):
    return chats.get_traces(thread_id, 10)


def test_a_reply_saves_its_model_and_tool_calls_in_order(fake_model):
    fake_model(ai(tool_calls=[("look_back", {"number": 0})], tokens=(100, 5)), ai("Done", tokens=(200, 9)))
    thread_id = str(uuid4())
    run(thread_id)
    [found] = saved(thread_id)
    assert found["asked"] == "hello" and not found["live"] and thread_id not in trace.live
    calls = [s for s in found["spans"] if s["kind"] != "step"]
    assert [s["kind"] for s in calls] == ["model", "tool", "model"]
    assert calls[1]["name"] == "look_back" and "number" in calls[1]["detail"]
    assert (calls[0]["input_tokens"], calls[0]["output_tokens"]) == (100, 5)
    assert calls[2]["input_tokens"] == 200
    assert all(s["status"] == "ok" and s["end"] >= s["start"] for s in calls)
    # One after the other, and none inside another
    assert calls[0]["end"] <= calls[1]["start"] and calls[1]["end"] <= calls[2]["start"]
    assert all(s["parent"] is None for s in calls)


def test_a_call_made_from_inside_a_tool_has_it_as_its_parent(fake_model, monkeypatch):
    from conftest import FakeModel

    inner = FakeModel(messages=iter([AIMessage("inner")]))

    @tool
    def asks_a_model(config: RunnableConfig = None) -> str:
        """Asks a model"""
        return inner.invoke("hi", config).text

    monkeypatch.setitem(toolset.tools_by_name, "asks_a_model", asks_a_model)
    monkeypatch.setattr("labb.tool_node.is_allowed", lambda *a: True)
    monkeypatch.setattr("labb.tool_node.is_on", lambda *a: True)
    fake_model(ai(tool_calls=[("asks_a_model", {})]), ai("Done"))
    thread_id = str(uuid4())
    run(thread_id)
    spans = saved(thread_id)[0]["spans"]
    outer = next(s for s in spans if s["kind"] == "tool")
    nested = [s for s in spans if s["parent"] == outer["id"]]
    assert [s["kind"] for s in nested] == ["model"]
    # Only what ran inside it: the reply's own model calls aren't
    assert sum(s["parent"] is None and s["kind"] == "model" for s in spans) == 2
    assert outer["start"] <= nested[0]["start"] and nested[0]["end"] <= outer["end"]


def test_calls_on_other_threads_overlap_without_being_inside_each_other():
    recording = Trace()
    together = threading.Barrier(2)

    def call(name):
        run_id = uuid4()
        recording.on_tool_start({"name": name}, "x", run_id=run_id, name=name)
        together.wait()
        time.sleep(0.01)
        recording.on_tool_end("done", run_id=run_id)

    threads = [threading.Thread(target=call, args=(name,)) for name in "ab"]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    first, second = recording.to_dict()["spans"]
    assert first["parent"] is None and second["parent"] is None
    assert second["start"] < first["end"] and first["start"] < second["end"]


def test_a_failed_call_says_so_and_an_unfinished_one_is_cut_off(fake_model, monkeypatch):
    from labb import model

    def fails(*args, **kwargs):
        raise RuntimeError("the model is away")

    monkeypatch.setattr(model, "call_model", fails)
    thread_id = str(uuid4())
    events = run(thread_id)
    assert any(e["type"] == "error" for e in events)
    # No reply was written, and the attempt is kept all the same
    assert len(saved(thread_id)) == 1 and not saved(thread_id)[0]["live"]

    recording = Trace()
    recording.on_tool_start({"name": "a"}, "x", run_id=uuid4(), name="a")
    assert recording.to_dict()["live"] and recording.to_dict()["spans"][0]["status"] == "running"
    recording.finish()
    found = recording.to_dict()
    assert not found["live"] and found["spans"][0]["status"] == "stopped" and found["spans"][0]["end"] is not None


def test_quick_steps_are_left_out_and_slow_ones_kept():
    recording = Trace()
    quick, slow = uuid4(), uuid4()
    recording.on_chain_start({}, {}, run_id=quick, name="compact", metadata={"langgraph_node": "compact"})
    recording.on_chain_end({}, run_id=quick)
    recording.on_chain_start({}, {}, run_id=slow, name="recall", metadata={"langgraph_node": "recall"})
    time.sleep(trace.MIN_STEP_MS / 1000 + 0.01)
    recording.on_chain_end({}, run_id=slow)
    # What only holds calls, or isn't a step of the graph, isn't a row of its own
    recording.on_chain_start({}, {}, run_id=uuid4(), name="llm_call", metadata={"langgraph_node": "llm_call"})
    recording.on_chain_start({}, {}, run_id=uuid4(), name="RunnableSequence", metadata={"langgraph_node": "recall"})
    assert [s["name"] for s in recording.to_dict()["spans"]] == ["recall"]


def test_a_summary_written_during_a_reply_is_on_the_waterfall(fake_model, monkeypatch):
    from dataclasses import replace
    from conftest import FakeModel

    summarizer = FakeModel(messages=iter([AIMessage("Short.")]))
    monkeypatch.setattr(llm, "for_config", lambda config: replace(llm.default, context_window=600, summary_model=summarizer))
    thread_id = str(uuid4())
    fake_model(ai("one " * 400), ai("two " * 400))
    run(thread_id)
    run(thread_id)
    spans = saved(thread_id)[-1]["spans"]
    assert sum(s["kind"] == "model" for s in spans) == 2, spans
    compact = next(s for s in spans if s["kind"] == "step" and s["name"] == "compact")
    assert any(s["parent"] == compact["id"] for s in spans if s["kind"] == "model")


def test_a_reply_being_written_can_be_watched_by_its_owner_only(client, accounts, fake_model):
    from fastapi.testclient import TestClient
    from labb.server import app as server

    fake_model(ai("Hi"))
    lines = client.post("/chat", json={"message": "hello"}).text.splitlines()
    thread_id = next(line for line in lines if "thread_id" in line).split('"thread_id": "')[1].split('"')[0]
    found = client.get("/context/traces", params={"thread_id": thread_id}).json()
    assert len(found) == 1 and found[0]["id"] != "live"

    trace.live[thread_id] = recording = Trace("now")
    recording.on_tool_start({"name": "a"}, "x", run_id=uuid4(), name="a")
    try:
        found = client.get("/context/traces", params={"thread_id": thread_id}).json()
    finally:
        del trace.live[thread_id]
    assert found[-1]["id"] == "live" and found[-1]["live"] and found[-1]["spans"][0]["end"] is None
    bob = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert bob.get("/context/traces", params={"thread_id": thread_id}).status_code == 404


def test_traces_go_with_their_reply_and_their_chat(fake_model):
    fake_model(ai("One"), ai("Two"))
    thread_id = str(uuid4())
    run(thread_id)
    run(thread_id)
    assert len(saved(thread_id)) == 2
    runner.take_back(thread_id, 1)
    assert len(saved(thread_id)) == 1
    chats.delete_chat(thread_id)
    assert saved(thread_id) == []
