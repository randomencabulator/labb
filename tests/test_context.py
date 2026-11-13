from dataclasses import replace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from labb import context
from labb import llm
from conftest import FakeModel


def models(window: int, summary: str = "They talked about magnets.") -> llm.Models:
    """The default models with a small window and a fake summary model"""
    return replace(llm.default, context_window=window, summary_model=FakeModel(messages=iter([AIMessage(summary)])))


def turn(i: int, size: int = 400) -> list:
    return [HumanMessage(f"question {i} " + "x" * size, id=f"h{i}"), AIMessage(f"answer {i} " + "y" * size, id=f"a{i}")]


def test_old_large_tool_results_are_cleared():
    messages = [ToolMessage("r" * 2000, tool_call_id=str(i), name="fetch_page") for i in range(4)]
    messages.append(ToolMessage("small", tool_call_id="5", name="grep"))
    cleared = context.clear_old_tool_results(messages)
    assert [m.content.startswith("[Cleared") for m in cleared] == [True, False, False, False, False]
    # The saved messages stay as they were
    assert messages[0].content == "r" * 2000


def test_nothing_is_summarized_while_it_fits():
    state = {"messages": turn(1) + turn(2)}
    # Only where clearing starts is noted, the first time
    assert context.compact(state, system_prompt="You help.", tools=[], models=models(100_000)) == {"cleared_before": 0}
    assert context.compact({**state, "cleared_before": 0}, system_prompt="You help.", tools=[], models=models(100_000)) == {}


def test_older_turns_are_summarized_when_it_gets_full():
    state = {"messages": [m for i in range(1, 6) for m in turn(i)] + [HumanMessage("the current question", id="h6")]}
    update = context.compact(state, system_prompt="You help.", tools=[], models=models(1_200))
    assert update["summary"] == "They talked about magnets."
    # It cuts at the start of a turn, and the current one is kept
    assert state["messages"][update["summarized"]].type == "human"
    assert 0 < update["summarized"] < len(state["messages"])
    sent = context.model_messages({**state, **update}, "You help.")
    assert "They talked about magnets." in sent[0].content
    assert sent[-1].content == "the current question"


def test_forced_compaction_summarizes_everything():
    state = {"messages": turn(1) + turn(2)}
    update = context.compact(state, system_prompt="You help.", tools=[], models=models(100_000), force=True)
    assert update["summarized"] == 4
    assert context.compact({**state, **update}, system_prompt="You help.", tools=[], models=models(100_000), force=True) == {}


def test_context_used_is_a_share_of_the_window():
    state = {"messages": turn(1)}
    small = context.context_used(state, system_prompt="You help.", tools=[], models=models(1_000))
    large = context.context_used(state, system_prompt="You help.", tools=[], models=models(100_000))
    assert small > large > 0


def test_inspect_breaks_down_what_the_model_gets():
    calls = [{"name": "fetch_page", "args": {"url": f"u{i}"}, "id": f"t{i}"} for i in range(3)]
    messages = [HumanMessage("read these", id="h1")]
    for call in calls:
        messages += [AIMessage("", tool_calls=[call]), ToolMessage("r" * 2000, tool_call_id=call["id"], name="fetch_page")]
    state = {"messages": turn(0) + messages, "summary": "They said hello.", "summarized": 2, "cleared_before": 5}
    seen = context.inspect(state, system_parts=[("Base", "You help."), ("Skills", "Some skills.")], tools=[],
                           models=models(100_000))

    # The summary stands in for the first turn, and the parts add up to what's used
    assert seen["summarized"] == 2 and seen["summary"] == "They said hello."
    assert [m["index"] for m in seen["messages"]] == list(range(2, len(state["messages"])))
    assert [p["kind"] for p in seen["parts"]] == ["system", "summary", "messages"]
    assert sum(p["tokens"] for p in seen["parts"]) == seen["used"] > 0
    assert [s["label"] for s in seen["system"]] == ["Base", "Skills"]
    # The oldest large tool result is cleared, with its size kept
    tools = [m for m in seen["messages"] if m["role"] == "tool"]
    assert [m["cleared"] for m in tools] == [True, False, False]
    assert tools[0]["original_chars"] == 2000 and seen["cleared"] == {"count": 1, "chars": 2000 - len(tools[0]["text"])}
    assert not seen["will_compact"]


def test_inspect_shows_recalled_memories_and_when_it_will_compact():
    state = {"messages": turn(1) + [HumanMessage("what's my cat called?", id="h2")]}
    sent = context.model_messages(state, "You help.")
    sent[-1] = sent[-1].model_copy(update={"content": sent[-1].content + "\n\n(You remember: the cat is Tom.)"})
    seen = context.inspect(state, system_parts=[("Base", "You help.")], tools=[], models=models(300), sent=sent)
    assert seen["messages"][-1]["recalled"] and "Tom" in seen["messages"][-1]["text"]
    assert next(p for p in seen["parts"] if p["kind"] == "recalled")["tokens"] > 0
    assert seen["will_compact"]


def test_the_model_gets_what_the_inspector_shows(config, monkeypatch):
    from labb import model

    sent = []
    monkeypatch.setattr(model, "call_model", lambda models, messages, *args: sent.append(messages) or AIMessage("hi"))
    state = {"messages": turn(1) + [HumanMessage("and now?", id="h2")], "summary": "Earlier.", "summarized": 2}
    model.llm_call(state, config)
    assert sent[0] == model.model_input(state, config)
    assert model.system_prompt(config) == "\n\n".join(text for _, text in model.system_prompt_parts(config))
    seen = model.inspect_context(state, config)
    assert seen["system"][0]["label"] == "Base" and any(s["label"] == "How to work" for s in seen["system"])
    assert seen["tools"] and seen["parts"][1]["kind"] == "tools"


def test_the_web_chat_can_inspect_its_own_chats_only(client, accounts, fake_model):
    from uuid import uuid4
    from fastapi.testclient import TestClient
    from conftest import ai
    from labb.server import app as server

    fake_model(ai("Hi there"))
    lines = client.post("/chat", json={"message": "hello"}).text.splitlines()
    thread_id = next(line for line in lines if "thread_id" in line).split('"thread_id": "')[1].split('"')[0]

    seen = client.get("/context", params={"thread_id": thread_id}).json()
    assert [m["role"] for m in seen["messages"]] == ["human", "ai"]
    assert seen["window"] == 32768 and seen["used"] == sum(p["tokens"] for p in seen["parts"])

    bob = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert bob.get("/context", params={"thread_id": thread_id}).status_code == 404
    assert client.get("/context", params={"thread_id": "nope"}).status_code == 404
    synced = f"cli-{uuid4()}"
    assert client.post(f"/sync/{synced}", json={"machine": "laptop", "directory": "/app", "title": "hi",
                                                 "updated_at": "2026-10-23T10:00:00+00:00", "messages": []}).status_code == 204
    assert client.get("/context", params={"thread_id": synced}).status_code == 400


def chat_with_tokens(client, fake_model, *replies):
    from conftest import ai

    fake_model(*(replies or [ai("Hi there")]))
    lines = client.post("/chat", json={"message": "hello"}).text.splitlines()
    return next(line for line in lines if "thread_id" in line).split('"thread_id": "')[1].split('"')[0]


def tool_names(seen):
    return {tool["name"] for tool in seen["tools"]}


def test_the_inspection_follows_the_message_boxs_choices(client, accounts, fake_model):
    thread_id = chat_with_tokens(client, fake_model)
    usual = client.get("/context", params={"thread_id": thread_id}).json()
    web_off = client.get("/context", params={"thread_id": thread_id, "web": False}).json()
    assert "Web off" not in {s["label"] for s in usual["system"]}
    assert "Web off" in {s["label"] for s in web_off["system"]}
    assert "web_search" in tool_names(usual) and not {"web_search", "fetch_page"} & tool_names(web_off)
    assert "Answer mode" not in {s["label"] for s in usual["system"]}
    thinking = client.get("/context", params={"thread_id": thread_id, "answer_mode": "thinking"}).json()
    assert "Answer mode" in {s["label"] for s in thinking["system"]}
    other = client.get("/context", params={"thread_id": thread_id, "engine_id": "labb-local"}).json()
    assert [s["text"] for s in other["system"]] != [s["text"] for s in usual["system"]]
    # A look doesn't pick it for the chat
    assert client.get("/context", params={"thread_id": thread_id}).json()["system"] == usual["system"]
    assert client.get("/context", params={"thread_id": thread_id, "engine_id": "nope"}).status_code == 400
    assert client.get("/context", params={"thread_id": thread_id, "answer_mode": "nope"}).status_code == 422


def test_tools_come_with_their_definitions(client, accounts, fake_model):
    thread_id = chat_with_tokens(client, fake_model)
    tool = next(t for t in client.get("/context", params={"thread_id": thread_id}).json()["tools"] if t["name"] == "web_search")
    assert tool["description"] and tool["parameters"]["type"] == "object"


def test_the_timeline_has_every_reply_with_its_real_tokens():
    usage = {"input_tokens": 100, "output_tokens": 7, "total_tokens": 107, "input_token_details": {"cache_read": 60}}
    messages = [HumanMessage("a", id="h1"), AIMessage("b", id="a1", usage_metadata=usage),
                HumanMessage("c", id="h2"), AIMessage("d", id="a2")]
    seen = context.inspect({"messages": messages, "summary": "Earlier.", "summarized": 2},
                           system_parts=[("Base", "You help.")], tools=[], models=models(100_000))
    assert [(c["index"], c["input_tokens"], c["cached_tokens"], c["summarized"]) for c in seen["timeline"]] == [
        (1, 100, 60, True), (3, None, None, False)]


def test_a_reminder_isnt_taken_for_recalled_memories():
    from labb import model

    saved = [HumanMessage("hello", id="h1")]
    sent = model.with_reminder(model.with_recalled(context.model_messages({"messages": saved}, "You help."), []), "todo list")
    seen = context.inspect({"messages": saved}, system_parts=[("Base", "You help.")], tools=[], models=models(100_000), sent=sent)
    assert seen["messages"][0]["reminder"] and not seen["messages"][0]["recalled"]
    assert not any(p["kind"] == "recalled" for p in seen["parts"])

    recalled = [{"id": "m1", "text": "Likes magnets", "name": "likes"}]
    sent = model.with_reminder(model.with_recalled(context.model_messages({"messages": saved}, "You help."), recalled), "todo list")
    seen = context.inspect({"messages": saved}, system_parts=[("Base", "You help.")], tools=[], models=models(100_000), sent=sent)
    assert seen["messages"][0]["reminder"] and seen["messages"][0]["recalled"]
    assert any(p["kind"] == "recalled" for p in seen["parts"])


def test_a_note_after_the_reply_is_counted():
    from labb import model

    saved = [HumanMessage("hello", id="h1"), AIMessage("hi", id="a1")]
    plain = context.model_messages({"messages": saved}, "You help.")
    sent = model.with_reminder(plain, "x" * 4000, after_reply=True)
    seen = context.inspect({"messages": saved}, system_parts=[("Base", "You help.")], tools=[], models=models(100_000), sent=sent)
    without = context.inspect({"messages": saved}, system_parts=[("Base", "You help.")], tools=[], models=models(100_000))
    assert len(seen["messages"]) == 3 and seen["messages"][2]["reminder"] and seen["messages"][2]["index"] == 2
    assert seen["used"] > without["used"] + 900


def test_a_cleared_result_is_told_apart_however_long_the_note():
    engine = context.DEFAULT
    small = ToolMessage("r", tool_call_id="1", name="grep", id="t1")
    keep_none = type("H", (), {"context": staticmethod(lambda key: {"keep_tool_results": 0, "min_cleared_chars": 0}.get(key, engine.context(key)))})()
    seen = context.inspect({"messages": [small]}, system_parts=[("Base", "You help.")], tools=[], models=models(100_000), engine=keep_none)
    assert seen["messages"][0]["cleared"]


def test_the_raw_input_is_what_the_model_gets_without_image_data(client, accounts, fake_model):
    from labb import inspection
    from labb.agent import chat_agent

    thread_id = chat_with_tokens(client, fake_model)
    raw = client.get("/context/raw", params={"thread_id": thread_id}).json()
    config = {"configurable": {"thread_id": thread_id}}
    assert [m["role"] for m in raw["messages"]] == ["system", "user", "assistant"]
    assert raw["messages"][1]["content"] == chat_agent.get_state(config).values["messages"][0].text
    assert any(t["function"]["name"] == "web_search" for t in raw["tools"])
    assert raw["model"]

    shown = inspection.without_image_data({"role": "user", "content": [{"type": "text", "text": "x"},
                                                                   {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]})
    assert "AAAA" not in str(shown) and shown["content"][0]["text"] == "x"


def test_a_saved_message_is_read_by_its_number_by_its_owner_only(client, accounts, fake_model):
    from fastapi.testclient import TestClient
    from labb.server import app as server

    thread_id = chat_with_tokens(client, fake_model)
    found = client.get("/context/message", params={"thread_id": thread_id, "number": 0}).json()
    assert found["role"] == "human" and found["text"] == "hello"
    assert client.get("/context/message", params={"thread_id": thread_id, "number": 9}).status_code == 404
    assert client.get("/context/message", params={"thread_id": thread_id, "number": -1}).status_code == 404
    bob = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert bob.get("/context/message", params={"thread_id": thread_id, "number": 0}).status_code == 404


def test_summarize_now_covers_every_turn_and_keeps_the_messages(client, accounts, fake_model, monkeypatch):
    from fastapi.testclient import TestClient
    from labb.agent import chat_agent
    from labb.runner import can_take_back, lock_for
    from labb.server import app as server

    thread_id = chat_with_tokens(client, fake_model)
    monkeypatch.setattr(llm, "for_config", lambda config: models(32768, "They said hello."))

    bob = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert bob.post("/context/summarize", json={"thread_id": thread_id}).status_code == 404

    with lock_for(thread_id):
        assert client.post("/context/summarize", json={"thread_id": thread_id}).status_code == 409
    server.runs[thread_id] = object()
    try:
        assert client.post("/context/summarize", json={"thread_id": thread_id}).status_code == 409
    finally:
        del server.runs[thread_id]

    seen = client.post("/context/summarize", json={"thread_id": thread_id}).json()
    assert seen["summary"] == "They said hello." and seen["summarized"] == 2 and seen["messages"] == []
    assert seen["total_messages"] == 2 and len(seen["timeline"]) == 1
    state = chat_agent.get_state({"configurable": {"thread_id": thread_id}})
    assert state.next == () and len(state.values["messages"]) == 2
    assert "summarized" in can_take_back(thread_id, 0)
    # Nothing left: nothing happens, and no model is asked
    assert client.post("/context/summarize", json={"thread_id": thread_id}).json()["summarized"] == 2
    assert not lock_for(thread_id).locked()


def test_a_failed_summary_says_so_and_frees_the_chat(client, accounts, fake_model, monkeypatch):
    from labb import inspection
    from labb.runner import lock_for

    thread_id = chat_with_tokens(client, fake_model)
    def fails(*args, **kwargs):
        raise RuntimeError("the model is away")
    monkeypatch.setattr(inspection, "compact_context", fails)
    response = client.post("/context/summarize", json={"thread_id": thread_id})
    assert response.status_code == 502 and "the model is away" in response.json()["detail"]
    assert not lock_for(thread_id).locked()


def test_the_inspection_says_which_result_goes_next():
    results = [ToolMessage("r" * context.MIN_CLEARED_CHARS, tool_call_id=str(i), name="fetch_page", id=f"t{i}") for i in range(4)]
    inspect = lambda messages: context.inspect({"messages": messages}, system_parts=[("Base", "You help.")], tools=[], models=models(100_000))
    keep = context.KEEP_TOOL_RESULTS
    assert inspect(results[:keep - 1])["next_to_clear"] is None
    assert inspect(results[:keep])["next_to_clear"] == 0
    assert inspect(results[:keep + 1])["next_to_clear"] == 1


def test_a_what_if_with_the_chats_scale_shows_what_the_tools_cost(client, accounts, fake_model):
    from conftest import ai

    thread_id = chat_with_tokens(client, fake_model, ai("Hi there", tokens=(3000, 10)))
    params = {"thread_id": thread_id}
    usual = client.get("/context", params=params).json()
    assert 1 < usual["scale"] < context.MAX_SCALE
    tools = lambda seen: next(p["tokens"] for p in seen["parts"] if p["kind"] == "tools")
    messages = lambda seen: next(p["tokens"] for p in seen["parts"] if p["kind"] == "messages")

    compared = client.get("/context", params={**params, "web": False, "scale": usual["scale"]}).json()
    assert tools(compared) < tools(usual)
    assert compared["used"] < usual["used"]
    # The messages are the same messages: only rounding of the shown scale can move them
    assert abs(messages(compared) - messages(usual)) <= 2

    # Without it the scale rises to fill the gap, and the saving disappears
    recomputed = client.get("/context", params={**params, "web": False}).json()
    assert recomputed["scale"] > usual["scale"]
    # A scale can't go beyond what the engine allows
    assert client.get("/context", params={**params, "scale": 999}).json()["scale"] == context.MAX_SCALE


def read_results(count: int, size: int = 3000) -> list:
    """`count` large results the model has read, each after its call, and the reply after them"""
    messages = [HumanMessage("go", id="h")]
    for i in range(count):
        messages += [AIMessage("", tool_calls=[{"name": "read_file", "args": {"path": f"{i}.py"}, "id": f"t{i}"}], id=f"c{i}"),
                     ToolMessage(f"{i} " + "r" * size, tool_call_id=f"t{i}", name="read_file", id=f"r{i}")]
    return messages + [AIMessage("done", id="end")]


def test_results_are_cleared_in_one_go_once_they_take_too_much_room():
    window = 100_000
    # 3 results of 3,000 characters are far below clear_at: nothing moves
    state = {"messages": read_results(5), "cleared_before": 0}
    assert context.clearing_boundary(state, window, 1.0) == 0
    # Over a quarter of a small window: cleared up to the last keep_tool_results, in one jump
    boundary = context.clearing_boundary(state, 10_000, 1.0)
    numbers = [i for i, m in enumerate(state["messages"]) if m.type == "tool"]
    assert boundary == numbers[-context.KEEP_TOOL_RESULTS]
    sent = context.model_messages({**state, "cleared_before": boundary}, "You help.")[1:]
    assert [m.content.startswith("[Cleared") for m in sent if m.type == "tool"] == [True, True, False, False, False]
    # Between jumps, a new result doesn't change what was sent before it: the prompt cache holds
    later = {**state, "messages": [*state["messages"], HumanMessage("more", id="h2"), *read_results(1)[1:]], "cleared_before": boundary}
    before = context.model_messages({**state, "cleared_before": boundary}, "You help.")
    after = context.model_messages(later, "You help.")
    assert [m.content for m in after[:len(before)]] == [m.content for m in before]


def test_clearing_comes_before_a_summary():
    state = {"messages": [*read_results(6, 2000), HumanMessage("next", id="h2")], "cleared_before": 0}
    # Too full to send, but clearing makes it fit: no summary
    update = context.compact(state, system_prompt="You help.", tools=[], models=models(4_500))
    assert "summary" not in update and update["cleared_before"] > 0


def test_one_huge_result_is_cut_to_its_share_of_the_window():
    messages = [context.SystemMessage("You help."), HumanMessage("read it"),
                AIMessage("", tool_calls=[{"name": "mcp__big__dump", "args": {}, "id": "1"}]),
                ToolMessage("z" * 500_000, tool_call_id="1", name="mcp__big__dump")]
    capped = context.cap_results(messages, start=0, window=32_768, scale=1.0)
    assert len(capped[-1].content) < 40_000 and "look_back(number=2)" in capped[-1].content
    assert len(messages[-1].content) == 500_000


def test_a_summary_keeps_the_users_words():
    state = {"messages": [m for i in range(1, 6) for m in turn(i)] + [HumanMessage("the current question", id="h6")]}
    update = context.compact(state, system_prompt="You help.", tools=[], models=models(1_200))
    # As many of the latest as fit the share of this small window, word for word
    words = update["user_words"]
    assert words.startswith("What the user wrote") and "#4: question 3 " + "x" * 400 in words and "earlier messages left out" in words
    sent = context.model_messages({**state, **update}, "You help.")
    assert "#4: question 3" in sent[0].content


def test_the_users_words_name_attached_files_and_keep_the_latest():
    messages = [HumanMessage("first " + "a" * 300), AIMessage("ok"),
                HumanMessage('look at this\n\n<file path="notes.txt">\nlong file\n</file>')]
    words = context.user_words(messages, 100)
    assert "look at this [attached: notes.txt]" in words and "a" * 300 not in words and "1 earlier message left out" in words


def test_summaries_go_in_parts_and_list_the_files():
    seen = []

    class Summarizer:
        def invoke(self, messages):
            seen.append(messages)
            return AIMessage(f"summary {len(seen)}")

    state = {"messages": read_results(8, 3000)}
    small = replace(llm.default, context_window=4_000, summary_model=Summarizer())
    summary = context.summarize(state, state["messages"], small)
    assert len(seen) > 1 and summary.startswith(f"summary {len(seen)}")
    assert "Files worked on (the latest last): " in summary and "7.py (read, #15)" in summary


def test_the_work_tab_shows_what_a_chat_works_on(client, accounts, fake_model):
    from fastapi.testclient import TestClient
    from conftest import ai
    from labb.server import app as server
    from labb.tools import goal

    thread_id = chat_with_tokens(client, fake_model, ai("", [("update_todos", {"todos": [{"task": "Find flights", "status": "done"},
                                                                                         {"task": "Book a hotel"}]})]), ai("Flights found."))
    found = client.get("/context/work", params={"thread_id": thread_id}).json()
    assert [item["task"] for item in found["todos"]] == ["Find flights", "Book a hotel"] and found["open"] == 1
    assert found["goal"] is None and found["plan"] is None
    goal.start({"configurable": {"thread_id": thread_id}}, "Plan the trip")
    assert client.get("/context/work", params={"thread_id": thread_id}).json()["goal"]["objective"] == "Plan the trip"
    # Nobody else's
    other = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert other.get("/context/work", params={"thread_id": thread_id}).status_code == 404
