"""Tool calls that run at the same time: which may, that they really do, that the results keep
their order and reach the model, and what a stop leaves. Overlap is proved with barriers, which only
open if the calls are running together, so these don't depend on how fast the machine is."""

import contextvars
import threading
import time
from uuid import uuid4

import pytest
from conftest import ai
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool as make_tool

from labb import chats, context, engine, model, runner, settings, tools
from labb.agent import chat_agent
from labb.tool_node import CANCELLED, batches, tool_node

PATIENCE = 5


def calls_of(*names):
    return [{"name": name, "args": {"text": str(i)}, "id": f"call-{i}", "type": "tool_call"} for i, name in enumerate(names)]


def run_node(*names, configurable=None, writer=None):
    state = {"messages": [AIMessage(content="", tool_calls=calls_of(*names))]}
    config = {"configurable": {"thread_id": "t", **(configurable or {})}}
    return tool_node(state, config, writer or (lambda _: None))["messages"]


@pytest.fixture
def fake_tools(monkeypatch):
    """Put these functions in place of real tools, by name: they take `text`"""

    def use(**functions):
        for name, function in functions.items():
            fake = make_tool(name, _plain(function))
            monkeypatch.setitem(tools.tools_by_name, name, fake)

    return use


def _plain(function):
    def stand_in(text: str = "", url: str = "") -> str:
        """A stand-in"""
        return function(text or url)
    return stand_in


# Which may run together

def test_exactly_these_run_together():
    # A list to read when one is added: everything here must only read, since a stop gives up on them halfway
    assert tools.run_together == {
        "web_search", "fetch_page", "list_files", "read_file", "glob", "grep", "look_back", "read_notes",
        "use_skill", "read_skill_file", "search_documents", "search_mail", "read_mail", "list_calendars", "list_events",
        "list_app_files", "read_app_file", "app_logs", "look_at_app", "app_history", "app_version_diff", "db_schema", "db_query",
        "list_artifacts", "list_artifact_files", "read_artifact_file", "find_tools",
        "search_outlook_mail", "read_outlook_mail", "list_outlook_events",
        "search_github", "list_github_repos", "read_github_issue", "read_github_file", "search_notion", "read_notion_page",
    }
    assert tools.run_together <= set(tools.tools_by_name)


def test_neighbours_run_together_and_anything_else_is_a_barrier():
    calls = calls_of("fetch_page", "read_file", "write_file", "fetch_page", "run_command", "run_command", "grep", "glob")
    assert batches(calls, 5) == [[0, 1], [2], [3], [4], [5], [6, 7]]


def test_an_mcp_servers_tool_doesnt_run_together():
    assert batches(calls_of("mcp__github__read", "mcp__github__read"), 5) == [[0], [1]]


def test_a_limit_of_one_runs_them_in_order():
    assert batches(calls_of("fetch_page", "fetch_page"), 1) == [[0], [1]]


# Running together

def test_they_really_overlap_and_the_results_keep_their_order(fake_tools, monkeypatch):
    monkeypatch.setattr(tools, "parallel_limit", lambda: 5)
    together = threading.Barrier(5, timeout=PATIENCE)
    finishing = [threading.Event() for _ in range(5)]

    def page(text):
        i = int(text)
        together.wait()  # Only opens with all five running
        # Finish last to first
        if i < 4:
            assert finishing[i + 1].wait(PATIENCE)
        finishing[i].set()
        return f"page {i}"

    fake_tools(fetch_page=page)
    seen = []
    messages = run_node(*["fetch_page"] * 5, writer=seen.append)
    assert [m.content for m in messages] == [f"page {i}" for i in range(5)]
    assert [m.tool_call_id for m in messages] == [f"call-{i}" for i in range(5)]
    # Reported as each finished
    assert [item["tool_result"].content for item in seen] == [f"page {i}" for i in reversed(range(5))]


def test_no_more_than_the_limit_at_once_and_none_left_out(fake_tools, monkeypatch):
    monkeypatch.setattr(tools, "parallel_limit", lambda: 2)
    lock = threading.Lock()
    now = [0]
    peak = [0]

    def page(text):
        with lock:
            now[0] += 1
            peak[0] = max(peak[0], now[0])
        time.sleep(0.03)
        with lock:
            now[0] -= 1
        return text

    fake_tools(fetch_page=page)
    messages = run_node(*["fetch_page"] * 7)
    assert peak[0] == 2
    assert [m.content for m in messages] == [str(i) for i in range(7)]


def test_nothing_passes_a_call_that_changes_something(fake_tools, monkeypatch):
    monkeypatch.setattr(tools, "parallel_limit", lambda: 5)
    log = []
    both = threading.Barrier(2, timeout=PATIENCE)

    def read(text):
        both.wait()
        log.append(f"read {text}")
        return text

    def write(text):
        # The reads before it are done, and the one after hasn't started
        log.append(f"write {text}")
        return text

    def read_alone(text):
        log.append(f"read {text}")
        return text

    fake_tools(read_file=read, write_file=write, grep=read_alone)
    run_node("read_file", "read_file", "write_file", "grep")
    assert sorted(log[:2]) == ["read 0", "read 1"]
    assert log[2:] == ["write 2", "read 3"]


def test_one_failing_doesnt_stop_the_others(fake_tools):
    def page(text):
        if text == "1":
            raise ValueError("no such page")
        return f"page {text}"

    fake_tools(fetch_page=page)
    messages = run_node("fetch_page", "fetch_page", "fetch_page")
    assert [m.status for m in messages] == ["success", "error", "success"]
    assert messages[1].content == "Error: no such page"


def test_a_thread_dying_doesnt_hang_the_reply(fake_tools):
    def page(text):
        if text == "0":
            raise SystemExit
        return text

    fake_tools(fetch_page=page)
    messages = run_node("fetch_page", "fetch_page")
    assert messages[0].status == "error" and messages[1].content == "1"


def test_they_get_the_runs_config_and_context(monkeypatch):
    marker = contextvars.ContextVar("marker", default="unset")
    marker.set("set")
    together = threading.Barrier(2, timeout=PATIENCE)
    seen = []

    from langchain_core.runnables import RunnableConfig

    @make_tool
    def fetch_page(text: str = "", config: RunnableConfig = None) -> str:
        """A stand-in"""
        together.wait()
        seen.append((marker.get(), config["configurable"]["user_id"], len(config["configurable"]["history"])))
        return text

    monkeypatch.setitem(tools.tools_by_name, "fetch_page", fetch_page)
    run_node("fetch_page", "fetch_page", configurable={"user_id": "u1"})
    assert seen == [("set", "u1", 1)] * 2


def test_unknown_and_turned_off_tools_are_answered_in_place(fake_tools):
    fake_tools(fetch_page=lambda text: "page")
    rules = tools.ToolRules.of(deny=("grep",))
    messages = run_node("fetch_page", "grep", "no_such", configurable={"tools": rules})
    assert messages[0].content == "page"
    assert "turned off" in messages[1].content
    assert "unknown tool" in messages[2].content


def test_two_can_ask_at_once_and_each_gets_its_own_answer(tmp_path):
    # Reading a file that may hold secrets asks, and these run together
    (tmp_path / ".env").write_text("KEY=1")
    (tmp_path / "secret.key").write_text("KEY=2")
    both_asking = threading.Barrier(2, timeout=PATIENCE)
    asked = []

    def approve(detail, needed, always_ask, title="?", **kwargs):
        both_asking.wait()
        asked.append(detail)
        return "yes" if ".env" in detail else ("no", "not that one")

    state = {"messages": [AIMessage(content="", tool_calls=[
        {"name": "read_file", "args": {"path": path}, "id": path, "type": "tool_call"} for path in (".env", "secret.key")])]}
    messages = tool_node(state, {"configurable": {"workdir": str(tmp_path), "approve": approve, "thread_id": "t"}})["messages"]
    assert len(asked) == 2
    assert "KEY=1" in messages[0].content
    assert "KEY=2" not in messages[1].content and "not that one" in messages[1].content


# Hooks

def test_a_hook_can_block_one_and_hooks_never_overlap(fake_tools, monkeypatch, tmp_path):
    from labb import hooks

    fake_tools(fetch_page=lambda text: f"page {text}")
    running = [0]
    overlapped = [False]
    log = []

    def run(event, configured, name, args, workdir, result=None):
        running[0] += 1
        overlapped[0] = overlapped[0] or running[0] > 1
        time.sleep(0.01)
        running[0] -= 1
        log.append((event, args["text"]))
        return ["not this one"] if event == "PreToolUse" and args["text"] == "1" else []

    monkeypatch.setattr(hooks, "run", run)
    monkeypatch.setattr(hooks, "of", lambda config: {"PreToolUse": []})
    messages = run_node("fetch_page", "fetch_page", "fetch_page", configurable={"workdir": str(tmp_path)})
    assert not overlapped[0]
    assert messages[0].content == "page 0" and messages[2].content == "page 2"
    assert messages[1].content == "Not run: a hook blocked this call. not this one"
    # All the checks come first, in order; a call that didn't run has no check after it
    assert log[:3] == [("PreToolUse", "0"), ("PreToolUse", "1"), ("PreToolUse", "2")]
    assert sorted(log[3:]) == [("PostToolUse", "0"), ("PostToolUse", "2")]


# Stopping

def test_a_stop_doesnt_wait_for_a_stuck_call_and_keeps_what_finished(fake_tools):
    never = threading.Event()
    first_done = threading.Event()
    stop = threading.Event()

    def page(text):
        if text == "0":
            first_done.set()
            return "page 0"
        assert first_done.wait(PATIENCE)
        threading.Timer(0.05, stop.set).start()
        never.wait(30)
        return "too late"

    fake_tools(fetch_page=page)
    started = time.monotonic()
    messages = run_node("fetch_page", "fetch_page", configurable={"stop_requested": stop})
    never.set()
    assert time.monotonic() - started < PATIENCE
    assert messages[0].content == "page 0"
    assert messages[1].content == CANCELLED and messages[1].status == "error"


def test_a_stop_before_a_call_means_it_never_runs(fake_tools):
    ran = []
    fake_tools(write_file=lambda text: ran.append(text) or "written")
    stop = threading.Event()
    stop.set()
    messages = run_node("write_file", configurable={"stop_requested": stop})
    assert not ran and messages[0].content == CANCELLED


def run_reply(thread_id, stop=None, message="read them", **configurable):
    events = []
    runner.run_reply(thread_id, message, stop or threading.Event(), events.append, name=False, configurable=configurable)
    return events


def test_after_a_stop_the_chat_goes_on(fake_model, fake_tools):
    stop = threading.Event()
    never = threading.Event()

    def page(text):
        if text == "url-1":
            threading.Timer(0.05, stop.set).start()
            never.wait(30)
        return f"page {text}"

    fake_tools(fetch_page=page)
    fake_model(ai(tool_calls=[("fetch_page", {"url": "url-0"}), ("fetch_page", {"url": "url-1"})]), ai("Carrying on"))
    thread_id = str(uuid4())
    events = run_reply(thread_id, stop)
    never.set()
    assert events[-1]["type"] == "stopped"

    # Every call has a result, or the next model call would fail
    messages = chat_agent.get_state({"configurable": {"thread_id": thread_id}}).values["messages"]
    results = {m.tool_call_id: m for m in messages if m.type == "tool"}
    calls = [c["id"] for m in messages if m.type == "ai" for c in m.tool_calls]
    assert set(calls) == set(results)
    contents = sorted(m.content for m in results.values())
    assert CANCELLED in contents and "page url-0" in contents

    assert run_reply(thread_id, message="go on")[-1]["type"] == "done"
    messages = chat_agent.get_state({"configurable": {"thread_id": thread_id}}).values["messages"]
    assert messages[-1].content == "Carrying on"


# The events

def test_each_result_comes_once_after_its_call(fake_model, fake_tools):
    fake_tools(fetch_page=lambda text: f"page {text}")
    fake_model(ai(tool_calls=[("fetch_page", {"text": str(i)}) for i in range(3)]), ai("Read them"))
    events = run_reply(str(uuid4()))
    order = [(e["type"], e["id"]) for e in events if e["type"] in ("tool_call", "tool_result")]
    assert [kind for kind, _ in order[:3]] == ["tool_call"] * 3
    assert sorted(id_ for kind, id_ in order if kind == "tool_result") == sorted(id_ for kind, id_ in order if kind == "tool_call")
    assert len(order) == 6
    assert all(e["name"] == "fetch_page" for e in events if e["type"] == "tool_result")


def test_the_terminal_puts_results_on_their_own_calls():
    from labb.cli.render import ReplyView

    view = ReplyView()
    for i in range(3):
        view.handle({"type": "tool_call", "id": f"c{i}", "name": "fetch_page", "args": {"url": f"u{i}"}})
    # Last to first, and with the same tool's name
    for i in reversed(range(3)):
        view.handle({"type": "tool_result", "id": f"c{i}", "name": "fetch_page", "chars": i, "lines": 1, "first_line": "", "error": False})
    assert [block.result["id"] for block in view.blocks] == ["c0", "c1", "c2"]


# The waterfall

def test_the_trace_shows_them_side_by_side(fake_model, fake_tools):
    together = threading.Barrier(2, timeout=PATIENCE)

    def page(text):
        together.wait()
        time.sleep(0.02)
        return text

    fake_tools(fetch_page=page)
    fake_model(ai(tool_calls=[("fetch_page", {"text": "a"}), ("fetch_page", {"text": "b"})]), ai("Done"))
    thread_id = str(uuid4())
    run_reply(thread_id)
    [found] = chats.get_traces(thread_id, 10)
    spans = [s for s in found["spans"] if s["kind"] == "tool"]
    assert len(spans) == 2
    # They overlap, and neither is inside the other
    assert spans[0]["start"] < spans[1]["end"] and spans[1]["start"] < spans[0]["end"]
    assert all(s["parent"] is None for s in spans)


# What reaches the model

PAGE = "x" * 20_000


def five_pages(before=()):
    calls = [{"name": "fetch_page", "args": {"url": f"u{i}"}, "id": f"c{i}"} for i in range(5)]
    return [*before, HumanMessage("read 5 sites"), AIMessage(content="", tool_calls=calls),
            *[ToolMessage(content=f"page {i} {PAGE}", name="fetch_page", tool_call_id=f"c{i}") for i in range(5)]]


def test_results_the_model_hasnt_read_are_never_cleared():
    sent = context.clear_old_tool_results(five_pages())
    assert sum(m.content.startswith("page ") for m in sent if m.type == "tool") == 5


def test_once_read_they_go_like_any_other():
    messages = [*five_pages(), AIMessage(content="", tool_calls=[{"name": "grep", "args": {}, "id": "g"}]),
                ToolMessage(content="y" * 2000, name="grep", tool_call_id="g")]
    sent = [m for m in context.clear_old_tool_results(messages) if m.type == "tool"]
    assert [m.content.startswith("[Cleared") for m in sent] == [True, True, True, False, False, False]


def test_unread_results_count_towards_those_kept():
    old = [AIMessage(content="", tool_calls=[{"name": "grep", "args": {}, "id": "old"}]), ToolMessage(content="o" * 2000, name="grep", tool_call_id="old")]
    # One new result and one old: as it always was
    new = [AIMessage(content="", tool_calls=[{"name": "grep", "args": {}, "id": "new"}]), ToolMessage(content="n" * 2000, name="grep", tool_call_id="new")]
    sent = context.clear_old_tool_results([HumanMessage("x"), *old, *new])
    assert not any(m.content.startswith("[Cleared") for m in sent if m.type == "tool")
    # Five new ones take every place
    sent = [m for m in context.clear_old_tool_results(five_pages(old)) if m.type == "tool"]
    assert sent[0].content.startswith("[Cleared") and all(m.content.startswith("page ") for m in sent[1:])


def test_an_engine_that_keeps_none_still_keeps_none():
    original = engine.DEFAULT.context

    class NoneKept:
        def context(self, name):
            return 0 if name == "keep_tool_results" else original(name)

    sent = [m for m in context.clear_old_tool_results(five_pages(), engine=NoneKept()) if m.type == "tool"]
    assert all(m.content.startswith("[Cleared") for m in sent)


def test_the_inspector_agrees_on_what_goes_next():
    report = context.inspect({"messages": five_pages()}, system_parts=[("Base", "You help")], tools=[])
    assert report["cleared"]["count"] == 0
    # The oldest shown in full
    assert report["next_to_clear"] == 2


def fit(messages, window, start=0):
    return context.fit_unread_results([context.with_summary("You help", "", start), *messages], start=start, tools=[], window=window, scale=1.0)[1:]


def test_they_stay_whole_when_they_fit():
    messages = five_pages()
    assert fit(messages, 200_000) == messages


def test_they_share_the_room_when_they_dont():
    window = 16_000
    sent = fit(five_pages(), window)
    results = [m for m in sent if m.type == "tool"]
    assert all("[Cut to fit the context" in m.content for m in results)
    # Each keeps its start, and they're the same size
    assert [m.content.startswith(f"page {i} ") for i, m in enumerate(results)] == [True] * 5
    assert len({len(m.content.split("\n\n[Cut")[0]) for m in results}) == 1
    # Under the line that keeps room for the reply
    from langchain_core.messages.utils import count_tokens_approximately
    assert count_tokens_approximately(sent) <= engine.COMPACT_AT * window * 1.02


def test_a_short_one_stays_whole_beside_long_ones():
    messages = five_pages()
    messages[3] = messages[3].model_copy(update={"content": "short " * 300})
    results = [m for m in fit(messages, 16_000) if m.type == "tool"]
    assert results[1].content == "short " * 300
    assert "[Cut" in results[0].content


def test_each_keeps_something_however_small_the_room():
    results = [m for m in fit(five_pages(), 100) if m.type == "tool"]
    assert all(m.content.startswith("page ") and len(m.content) > context.MIN_SHARE_CHARS for m in results)


def test_a_cut_result_says_how_to_read_it_and_that_works():
    from labb.tools.history import look_back

    before = [HumanMessage("earlier"), AIMessage("sure")]
    messages = five_pages(before)
    cut = [m for m in fit(messages, 16_000) if m.type == "tool"][0]
    number = 4
    assert f"#{number}. look_back(number={number})" in cut.content
    assert messages[number].tool_call_id == "c0"
    answer = look_back.invoke({"number": number}, config={"configurable": {"history": messages}})
    assert "page 0" in answer


def test_a_single_large_result_is_left_alone():
    messages = [HumanMessage("read"), AIMessage(content="", tool_calls=[{"name": "fetch_page", "args": {}, "id": "c"}]),
                ToolMessage(content="x" * 100_000, name="fetch_page", tool_call_id="c")]
    assert fit(messages, 1_000) == messages


def test_the_model_gets_all_five_in_the_real_run_and_the_chat_keeps_them_whole(fake_model, fake_tools, monkeypatch):
    fake_tools(fetch_page=lambda text: f"page {text} {'x' * 3000}")
    fake_model(ai(tool_calls=[("fetch_page", {"url": str(i)}) for i in range(5)]), ai("Read all five"))
    sent = []
    real = model.model_input

    def recording(state, config):
        found = real(state, config)
        sent.append(found)
        return found

    monkeypatch.setattr(model, "model_input", recording)
    thread_id = str(uuid4())
    assert run_reply(thread_id)[-1]["type"] == "done"
    results = [m for m in sent[-1] if m.type == "tool"]
    assert [m.content.split()[1] for m in results] == [str(i) for i in range(5)]
    assert not any("[Cleared" in m.content for m in results)
    saved = chat_agent.get_state({"configurable": {"thread_id": thread_id}}).values["messages"]
    assert sum(len(m.content) > 3000 for m in saved if m.type == "tool") == 5


# The settings

def test_the_server_has_a_limit_for_it():
    from labb.server import app, limits  # noqa: F401
    assert any(limit.key == "tool_parallel" and limit.default == 5 and limit.min == 1 for limit in limits.LIMITS)


def test_the_terminal_has_a_setting_for_it():
    assert settings.TYPES["parallelTools"] is int


def test_the_eval_scores_calling_them_together():
    from labb.cli.evals import score
    from labb.tools.web import fetch_page

    case = {"prompt": "Read three", "tool": "fetch_page", "args": {"url": "example"}, "together": 3}
    three = ai(tool_calls=[("fetch_page", {"url": f"https://example.com/{i}"}) for i in range(3)])
    one = ai(tool_calls=[("fetch_page", {"url": "https://example.com/0"})])
    assert score(case, three, [fetch_page])["together"] is True
    assert score(case, one, [fetch_page])["together"] is False
    assert "together" not in score({"prompt": "x", "tool": "fetch_page", "args": {}}, one, [fetch_page])
