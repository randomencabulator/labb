"""Replies that end too early go round again (continuation.py): cut off, empty, said but not
done, a self-paced loop's run that didn't say when to run again."""

import threading
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from labb import continuation, engine, model, runner
from labb.agent import chat_agent
from labb.tools import todo
from labb.tools.loop import Loop
from conftest import FakeModel, ai


class Recording(FakeModel):
    """Remembers what each call was sent"""

    seen: list = []

    def _generate(self, messages, *args, **kwargs):
        self.seen.append([m.text for m in messages])
        return super()._generate(messages, *args, **kwargs)


@pytest.fixture
def recording(monkeypatch):
    def use(*replies):
        fake = Recording(messages=iter(list(replies)), seen=[])
        monkeypatch.setattr(model, "model_with_tools", lambda *args: fake)
        monkeypatch.setattr(model, "constrainer", lambda *args: None)
        return fake
    return use


def cut(text: str = "", tool_calls=None, field: str = "done_reason", value: str = "length") -> AIMessage:
    message = ai(text, tool_calls)
    message.response_metadata[field] = value
    return message


def reply(thread_id: str, text: str, configurable: dict | None = None) -> list[dict]:
    events = []
    runner.run_reply(thread_id, text, threading.Event(), events.append, configurable=configurable, name=False)
    return events


def messages(thread_id: str) -> list:
    return chat_agent.get_state({"configurable": {"thread_id": thread_id}}).values["messages"]


def stats_of(events: list[dict]) -> dict:
    return events[-1]["stats"]


@pytest.mark.parametrize("field, value", [("done_reason", "length"), ("stop_reason", "max_tokens"), ("finish_reason", "length")])
def test_a_cut_off_reply_goes_on_where_it_stopped(recording, field, value):
    fake = recording(cut("The first half", field=field, value=value), ai(" and the second."))
    thread = str(uuid4())
    events = reply(thread, "Write it")
    assert [m.text for m in messages(thread)] == ["Write it", "The first half", " and the second."]
    assert {"type": "continue", "reason": "truncated"} in events
    # The note is for the second call only, after the cut-off reply
    assert "cut off" in fake.seen[1][-1] and "cut off" not in " ".join(fake.seen[0])
    assert stats_of(events)["continued"] == 1 and stats_of(events)["end_reason"] == "done"


def test_it_gives_up_going_on_after_the_engines_number(recording):
    recording(*[cut(f"part {i}") for i in range(5)])
    thread = str(uuid4())
    events = reply(thread, "Write it")
    # The first reply and 3 more (engine.CONTINUE["truncated"])
    assert len([m for m in messages(thread) if m.type == "ai"]) == 4
    assert stats_of(events)["end_reason"] == "truncated" and stats_of(events)["continued"] == 3


def test_an_engine_can_turn_it_off(recording):
    recording(cut("part one"), ai("never"))
    thread = str(uuid4())
    events = reply(thread, "Write it", {"engine": engine.resolve({"continue": {"truncated": 0}}, "off")})
    assert [m.text for m in messages(thread)] == ["Write it", "part one"]
    assert not any(e["type"] == "continue" for e in events)


def test_a_cut_off_tool_call_is_dropped_and_asked_again(recording, config):
    fake = recording(cut("Writing it.", [("write_file", {"path": "a.txt"})], "stop_reason", "max_tokens"), ai("Done."))
    thread = f"cli-{uuid4()}"
    reply(thread, "Write a.txt", config["configurable"])
    first = messages(thread)[1]
    assert first.text == "Writing it." and not first.tool_calls
    assert "tool call" in fake.seen[1][-1]
    assert [m.type for m in messages(thread)] == ["human", "ai", "ai"]


def test_an_empty_reply_is_asked_once_more_and_not_kept(recording):
    fake = recording(ai(""), ai("Here it is."))
    thread = str(uuid4())
    events = reply(thread, "Hello?")
    assert [m.text for m in messages(thread)] == ["Hello?", "Here it is."]
    assert "empty" in fake.seen[1][-1]
    stats = stats_of(events)
    # The dropped call still counts
    assert stats["llm_calls"] == 2 and stats["end_reason"] == "done"


def test_saying_what_comes_next_with_open_todos_is_nudged(recording, config):
    thread = f"cli-{uuid4()}"
    todo.save(config["configurable"]["workdir"], thread, [{"task": "Run the tests", "status": "in_progress"}])
    fake = recording(ai("The change is in. Now I'll run the tests."), ai("All tests pass."))
    events = reply(thread, "Fix it", config["configurable"])
    assert {"type": "continue", "reason": "announced"} in events
    assert "didn't do it" in fake.seen[1][-1]
    assert messages(thread)[-1].text == "All tests pass."


def test_without_open_todos_saying_what_comes_next_ends_the_reply(recording, config):
    recording(ai("Let me know if you want more. I'll wait."), ai("never"))
    thread = f"cli-{uuid4()}"
    events = reply(thread, "Fix it", config["configurable"])
    assert not any(e["type"] == "continue" for e in events)


@pytest.mark.parametrize("text, expected", [
    ("Now I'll run the tests.", True), ("Next, I update the README:", True), ("Let me check the logs.", True),
    ("Done. Let me know if you want more.", False), ("Should I run them?", False), ("All 12 tests pass.", False),
])
def test_what_counts_as_announcing(text, expected):
    assert continuation.announced(text) is expected


def test_a_self_paced_loop_run_is_reminded_to_schedule(recording, config):
    loop = Loop("check it")
    fake = recording(ai("Nothing changed."), ai(tool_calls=[("schedule_next", {"minutes": 5, "reason": "waiting"})]), ai("Checked."))
    thread = f"cli-{uuid4()}"
    events = reply(thread, "check it", {**config["configurable"], "loop": loop})
    assert {"type": "continue", "reason": "loop_unscheduled"} in events
    assert "schedule_next" in fake.seen[1][-1] and loop.scheduled == 300


def test_a_loop_every_few_minutes_isnt_reminded(recording, config):
    recording(ai("Nothing changed."), ai("never"))
    thread = f"cli-{uuid4()}"
    events = reply(thread, "check it", {**config["configurable"], "loop": Loop("check it", every=60)})
    assert not any(e["type"] == "continue" for e in events)


def test_an_engines_own_loop_goes_round_again_too(recording):
    minimal = engine.resolve({"loop": engine.PRESETS["minimal"]["loop"]}, "minimal")
    recording(cut("half"), ai(" whole"))
    thread = str(uuid4())
    reply(thread, "Write it", {"engine": minimal})
    assert [m.text for m in messages(thread)] == ["Write it", "half", " whole"]


def test_a_stop_while_going_round_leaves_the_chat_ready(recording, monkeypatch):
    thread = str(uuid4())
    stop = threading.Event()
    events = []

    def emit(event):
        events.append(event)
        if event["type"] == "continue":
            stop.set()

    recording(cut("half"), ai("never"), ai("Next answer."))
    runner.run_reply(thread, "Write it", stop, emit, name=False)
    assert events[-1]["type"] == "stopped"
    state = chat_agent.get_state({"configurable": {"thread_id": thread}})
    assert not state.values.get("continuing") and not state.next
    reply(thread, "Again")
    assert messages(thread)[-1].type == "ai"


def test_the_engine_checks_its_numbers():
    assert engine.check({"continue": {"truncated": 2, "empty": 0}}) == []
    assert engine.check({"continue": {"truncated": -1}}) and engine.check({"continue": {"nope": 1}})
    assert engine.check({"continue": {"empty": True}})


# --- Model calls that fail for a moment are tried again (model.call_model) ----------------

class Flaky:
    """Fails with these errors first, then answers like the fake model"""

    def __init__(self, errors: list[Exception], fake):
        self.errors, self.fake, self.calls = list(errors), fake, 0

    def invoke(self, messages, *args, **kwargs):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.fake.invoke(messages, *args, **kwargs)


class StatusError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


@pytest.fixture
def flaky(monkeypatch):
    monkeypatch.setattr(model, "RETRY_WAITS", (0, 0, 0))

    def use(errors, *replies):
        flaky = Flaky(errors, FakeModel(messages=iter(list(replies))))
        monkeypatch.setattr(model, "model_with_tools", lambda *args: flaky)
        monkeypatch.setattr(model, "constrainer", lambda *args: None)
        return flaky
    return use


def test_a_busy_provider_is_tried_again(flaky):
    fake = flaky([StatusError("Overloaded", 529), ConnectionError("Connection reset by peer")], ai("Here."))
    thread = str(uuid4())
    events = reply(thread, "Hi")
    retries = [e for e in events if e["type"] == "retry"]
    assert len(retries) == 2 and retries[0]["message"] == "Overloaded"
    assert messages(thread)[-1].text == "Here." and events[-1]["type"] == "done" and fake.calls == 3


def test_it_gives_up_after_the_last_wait(flaky):
    flaky([StatusError("Overloaded", 529)] * 4, ai("never"))
    events = reply(str(uuid4()), "Hi")
    assert len([e for e in events if e["type"] == "retry"]) == 3
    assert any(e["type"] == "error" for e in events) and events[-1]["stats"]["end_reason"] == "error"


@pytest.mark.parametrize("error", [StatusError("insufficient_quota: You exceeded your current quota", 429),
                                   StatusError("invalid x-api-key", 401), StatusError("model not found", 404)])
def test_what_wont_get_better_isnt_tried_again(flaky, error):
    fake = flaky([error], ai("never"))
    events = reply(str(uuid4()), "Hi")
    assert not any(e["type"] == "retry" for e in events) and fake.calls == 1


def test_a_stop_ends_the_wait(monkeypatch, flaky):
    monkeypatch.setattr(model, "RETRY_WAITS", (30, 30, 30))
    flaky([StatusError("Overloaded", 529)] * 4, ai("never"))
    stop = threading.Event()
    events = []

    def emit(event):
        events.append(event)
        if event["type"] == "retry":
            stop.set()

    runner.run_reply(str(uuid4()), "Hi", stop, emit, name=False)
    assert events[-1]["type"] == "stopped" and events[-1]["stats"]["duration_ms"] < 10_000


def test_retry_after_is_followed(monkeypatch):
    from labb import llm

    class Response:
        headers = {"retry-after": "7"}

    error = StatusError("rate limited", 429)
    error.response = Response()
    assert llm.is_transient(error) and llm.retry_after(error) == 7


# --- Open steps, goals, Stop hooks and the check before done --------------------------------

def test_a_final_report_with_open_steps_goes_back_to_work(recording, config):
    thread = f"cli-{uuid4()}"
    todo.save(config["configurable"]["workdir"], thread, [{"task": "Fix the bug", "status": "in_progress"}, {"task": "Add a test"}])
    fake = recording(ai("", [("list_files", {"path": "."})]), ai("All done! Here's a summary of the fix " + "x" * 800),
                     ai("", [("update_todos", {"todos": [{"task": "Fix the bug", "status": "done"},
                                                         {"task": "Add a test", "status": "skipped", "note": "no test setup"}]})]),
                     ai("Fixed; the test was skipped."))
    events = reply(thread, "Fix it", config["configurable"])
    assert {"type": "continue", "reason": "open_steps"} in events
    assert "Add a test" in fake.seen[2][-1] and "skipped" in fake.seen[2][-1]
    assert messages(thread)[-1].text == "Fixed; the test was skipped." and stats_of(events)["end_reason"] == "done"


def test_a_question_or_a_plain_answer_ends_the_reply_despite_open_steps(recording, config):
    thread = f"cli-{uuid4()}"
    todo.save(config["configurable"]["workdir"], thread, [{"task": "Fix the bug"}])
    recording(ai("", [("list_files", {"path": "."})]), ai("Should I also update the docs?"), ai("never"))
    assert not any(e["type"] == "continue" for e in reply(thread, "Fix it", config["configurable"]))
    # Only talking (the user asked something in between): the list is from earlier work
    recording(ai("It's in auth.py, line 12."), ai("never"))
    assert not any(e["type"] == "continue" for e in reply(thread, "Where's the login?", config["configurable"]))


def test_without_a_list_a_short_announcement_after_some_work_is_nudged(recording, config):
    fake = recording(ai("", [("list_files", {"path": "."})]), ai("Now let me run the tests."), ai("They pass."))
    events = reply(f"cli-{uuid4()}", "Fix it", config["configurable"])
    assert {"type": "continue", "reason": "announced"} in events and "didn't do it" in fake.seen[2][-1]
    # A greeting has done no work: it's an answer
    recording(ai("I'll be happy to help!"), ai("never"))
    assert not any(e["type"] == "continue" for e in reply(f"cli-{uuid4()}", "Hi", config["configurable"]))


def test_a_goal_keeps_the_model_working_until_it_says_its_done(recording, config):
    from labb.tools import goal

    thread = f"cli-{uuid4()}"
    run = {**config["configurable"], "thread_id": thread}
    goal.start({"configurable": run}, "Make the tests pass")
    fake = recording(ai("I fixed the first failure."),
                     ai("", [("update_goal", {"status": "complete", "note": "pytest: 12 passed"})]), ai("All 12 tests pass."))
    events = reply(thread, "Go", config["configurable"])
    assert {"type": "continue", "reason": "goal"} in events
    assert "<objective>\nMake the tests pass\n</objective>" in fake.seen[1][-1] and "evidence" in fake.seen[1][-1]
    kept = goal.get({"configurable": run})
    # The call after it was marked complete is no longer the goal's
    assert kept["status"] == "complete" and kept["used"] == 2 and kept["round"] == 1
    # The goal's tools were offered while it was active, and not after
    from labb.tools import offered_for
    assert "goal" not in offered_for({"configurable": run})


def test_blocked_needs_the_same_blocker_three_rounds_in_a_row(config):
    from labb.tools import goal

    run = {"configurable": {**config["configurable"], "thread_id": "cli-blocked"}}
    goal.start(run, "Deploy it")
    said = goal.update_goal.invoke({"status": "blocked", "note": "no key"}, run)
    assert "1 of 3" in said and goal.get(run)["status"] == "active"
    for _ in range(2):
        goal.next_round(run)
        said = goal.update_goal.invoke({"status": "blocked", "note": "no key"}, run)
    assert goal.get(run)["status"] == "blocked" and "Tell the user" in said


def test_a_goal_that_used_its_budget_wraps_up(recording, config):
    from labb.tools import goal

    thread = f"cli-{uuid4()}"
    run = {"configurable": {**config["configurable"], "thread_id": thread}}
    goal.start(run, "Refactor everything", budget=2)
    fake = recording(ai("", [("list_files", {"path": "."})]), ai("", [("list_files", {"path": "."})]), ai("Here's where it stands."), ai("never"))
    events = reply(thread, "Go", config["configurable"])
    assert "used its budget" in fake.seen[2][-1] and goal.get(run)["status"] == "budget_limited"
    assert messages(thread)[-1].text == "Here's where it stands." and not any(e["type"] == "continue" for e in events)


def test_a_stop_hook_can_send_the_model_back(recording, config):
    hook = {"Stop": [{"command": "grep -q '\"continued\": 0' && { echo 'make test fails' >&2; exit 2; } || exit 0"}]}
    fake = recording(ai("Done."), ai("Fixed the failing test."), ai("never"))
    events = reply(f"cli-{uuid4()}", "Fix it", {**config["configurable"], "hooks": hook})
    assert {"type": "continue", "reason": "hook"} in events and "make test fails" in fake.seen[1][-1]
    assert sum(e["type"] == "continue" for e in events) == 1


def test_the_check_before_done_asks_for_evidence_when_its_on(recording, config):
    checking = engine.resolve({"continue": {"audit": 1}}, "checking")
    fake = recording(ai("", [("update_todos", {"todos": [{"task": "Fix it", "status": "done"}]})]), ai("Done."), ai("Checked: 3 passed."), ai("never"))
    events = reply(f"cli-{uuid4()}", "Fix it", {**config["configurable"], "engine": checking})
    assert {"type": "continue", "reason": "audit"} in events and "evidence" in fake.seen[2][-1]
    # Off by default
    recording(ai("", [("update_todos", {"todos": [{"task": "Fix it", "status": "done"}]})]), ai("Done."), ai("never"))
    assert not any(e["type"] == "continue" for e in reply(f"cli-{uuid4()}", "Fix it", config["configurable"]))


def test_a_task_gets_more_rounds_unless_its_engine_says():
    assert engine.DEFAULT.continues("open_steps") == 2 and engine.DEFAULT.continues("open_steps", task=True) == 4
    assert engine.resolve({"continue": {"open_steps": 1}}, "x").continues("open_steps", task=True) == 1
