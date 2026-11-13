"""Tasks: schedules and their next times (across daylight saving time), the scheduler queuing
due tasks once, runs writing to chats, missed runs after a restart, the limits, failures
turning a task off, run now and stop, waiting for web replies, and isolation between users.
The clock is fake: `due`, `recover` and `create` take the time."""

import itertools
import threading
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import SystemMessage
from pydantic import Field

from labb import chats, memories, model, runner, usage
from labb.server import app as server
from labb.server import auth, tasks
from labb.server import limits

from conftest import FakeModel, ai

VIENNA = "Europe/Vienna"


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class SeeingModel(FakeModel):
    seen: list = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(memories, "index", None)
    monkeypatch.setattr(runner, "name_chat", lambda thread_id, config: None)


def person() -> tuple[dict, TestClient]:
    user = auth.create_user(f"u{uuid4().hex[:10]}", "a-password")
    key, _ = auth.create_key(user["id"], "laptop")
    return user, TestClient(server.app, headers={"Authorization": f"Bearer {key}"})


@pytest.fixture
def alice(accounts):
    return person()


@pytest.fixture
def bob(accounts):
    return person()


def replies(monkeypatch, *answers) -> SeeingModel:
    fake = SeeingModel(messages=iter(answers) if not isinstance(answers[0], itertools.repeat) else answers[0])
    monkeypatch.setattr(model, "model_with_tools", lambda *args: fake)
    return fake


def daily(user_id: str, now: str = "2026-10-24T05:00:00", **fields) -> dict:
    return tasks.create(user_id, {"name": "Morning news", "prompt": "Summarize the news.", "tz": VIENNA,
                                  "schedule": {"kind": "daily", "time": "08:00"}, **fields}, now=utc(now))


def test_next_times_follow_the_local_clock_across_daylight_saving_time():
    # Clocks in Vienna go back on Sunday 25 October 2026: 08:00 is 06:00 UTC before, 07:00 after
    every_day = tasks.clean_schedule({"kind": "daily", "time": "8:00"}, utc("2026-10-23T12:00:00"))
    assert every_day == {"kind": "daily", "time": "08:00"}
    first = tasks.next_after(every_day, VIENNA, utc("2026-10-23T12:00:00"))
    assert first == utc("2026-10-24T06:00:00")
    assert tasks.next_after(every_day, VIENNA, first) == utc("2026-10-25T07:00:00")

    assert tasks.next_after({"kind": "weekdays", "time": "08:00"}, VIENNA, utc("2026-10-23T12:00:00")) == utc("2026-10-26T07:00:00")
    weekly = tasks.clean_schedule({"kind": "weekly", "days": [4, 2, 2], "time": "18:30"}, utc("2026-10-23T12:00:00"))
    assert weekly["days"] == [2, 4] and tasks.describe(weekly) == "Wed and Fri at 18:30"
    assert tasks.next_after(weekly, VIENNA, utc("2026-10-23T12:00:00")) == utc("2026-10-23T16:30:00")

    hours = tasks.clean_schedule({"kind": "hours", "every": 3}, utc("2026-10-24T00:00:00"))
    assert tasks.next_after(hours, VIENNA, utc("2026-10-24T07:30:00")) == utc("2026-10-24T09:00:00")
    once = tasks.clean_schedule({"kind": "once", "at": "2026-10-25T08:00"}, utc("2026-10-24T00:00:00"))
    assert tasks.next_after(once, VIENNA, utc("2026-10-24T00:00:00")) == utc("2026-10-25T07:00:00")
    assert tasks.next_after(once, VIENNA, utc("2026-10-25T07:00:00")) is None

    assert tasks.preview({"kind": "weekdays", "time": "08:00"}, VIENNA, now=utc("2026-10-23T12:00:00")) == {
        "when": "Weekdays at 08:00",
        "next": ["2026-10-26T07:00:00+00:00", "2026-10-27T07:00:00+00:00", "2026-10-28T07:00:00+00:00"],
    }
    for bad in [{"kind": "monthly"}, {"kind": "daily", "time": "25:00"}, {"kind": "weekly", "time": "08:00", "days": []},
                {"kind": "hours", "every": 0}, {"kind": "once", "at": "soon"}]:
        with pytest.raises(tasks.TaskError):
            tasks.clean_schedule(bad, utc("2026-10-24T00:00:00"))


def test_tasks_are_checked(alice, bob):
    user, client = alice
    _, bobs = bob
    base = {"prompt": "Check the weather.", "tz": VIENNA, "schedule": {"kind": "daily", "time": "07:00"}}
    created = client.post("/tasks", json=base).json()
    assert created["name"] == "Check the weather." and created["when"] == "Every day at 07:00" and created["enabled"]
    assert created["next_run"] and created["mode"] == "new" and created["last_run"] is None

    for bad in [{**base, "prompt": " "}, {**base, "tz": "Mars/Olympus"}, {k: v for k, v in base.items() if k != "schedule"},
                {**base, "schedule": {"kind": "once", "at": "2020-01-01T08:00"}}, {**base, "agent_id": "nope"}]:
        assert client.post("/tasks", json=bad).status_code == 400, bad
    private = bobs.post("/agents", json={"name": "Bob's own"}).json()
    assert client.post("/tasks", json={**base, "agent_id": private["id"]}).status_code == 400

    # Others can't see, change, run or delete it
    assert all(t["id"] != created["id"] for t in bobs.get("/tasks").json())
    for method, path in [("get", ""), ("patch", ""), ("delete", ""), ("post", "/run"), ("get", "/runs")]:
        kwargs = {"json": {"name": "mine"}} if method == "patch" else {}
        assert getattr(bobs, method)(f"/tasks/{created['id']}{path}", **kwargs).status_code == 404

    off = client.patch(f"/tasks/{created['id']}", json={"enabled": False}).json()
    assert not off["enabled"] and off["next_run"] is None
    changed = client.patch(f"/tasks/{created['id']}", json={"enabled": True, "schedule": {"kind": "weekdays", "time": "09:15"}}).json()
    assert changed["enabled"] and changed["when"] == "Weekdays at 09:15" and changed["next_run"]

    for _ in range(limits.get("tasks_per_person") - 1):
        tasks.create(user["id"], base)
    assert client.post("/tasks", json=base).status_code == 400
    assert client.delete(f"/tasks/{created['id']}").status_code == 204


def test_the_scheduler_runs_due_tasks_once_into_new_unread_chats(alice, monkeypatch):
    user, client = alice
    task = daily(user["id"])
    assert task["next_run"] == "2026-10-24T06:00:00+00:00"
    assert tasks.due(utc("2026-10-24T05:59:00")) == []

    [run_id] = [r for r in tasks.due(utc("2026-10-24T06:00:20")) if tasks.get_run(r)["task_id"] == task["id"]]
    assert tasks.get(user["id"], task["id"])["next_run"] == "2026-10-25T07:00:00+00:00"
    assert all(tasks.get_run(r)["task_id"] != task["id"] for r in tasks.due(utc("2026-10-24T06:00:50")))

    fake = replies(monkeypatch, ai("Three things happened.", tokens=(100, 20)))
    run = tasks.execute(run_id, now=lambda: utc("2026-10-24T06:00:30"))
    assert run["status"] == "done" and run["error"] is None and run["output_tokens"] == 20
    assert model.TASK_PROMPT in fake.seen[0][0].content

    chat = next(c for c in client.get("/threads").json() if c["thread_id"] == run["thread_id"])
    assert chat["title"] == "Morning news · Sat 24 Oct, 08:00" and chat["unread"]
    history = client.get("/history", params={"thread_id": run["thread_id"]}).json()
    assert [e["content"] for e in history if e["type"] in ("user", "token")] == ["Summarize the news.", "Three things happened."]
    assert not next(c for c in client.get("/threads").json() if c["thread_id"] == run["thread_id"])["unread"]
    assert usage.summary(user_id=user["id"])["by_source"][0]["source"] == "task"

    assert client.get(f"/tasks/{task['id']}").json()["last_run"]["status"] == "done"
    assert [r["id"] for r in client.get(f"/tasks/{task['id']}/runs").json()] == [run_id]


def test_a_task_can_keep_writing_in_the_same_chat(alice, monkeypatch):
    user, client = alice
    task = daily(user["id"], mode="same")
    replies(monkeypatch, ai("First."), ai("Second."), ai("Third."))
    first = tasks.execute(tasks.run_now(user["id"], task["id"])["id"])
    second = tasks.execute(tasks.run_now(user["id"], task["id"])["id"])
    assert first["thread_id"] == second["thread_id"]
    assert next(c for c in client.get("/threads").json() if c["thread_id"] == first["thread_id"])["title"] == "Morning news"

    # Its chat was deleted: the next run starts another
    client.delete(f"/threads/{first['thread_id']}")
    third = tasks.execute(tasks.run_now(user["id"], task["id"])["id"])
    assert third["thread_id"] != first["thread_id"]


def test_after_a_restart_recent_misses_run_and_old_ones_wait(alice):
    user, _ = alice
    recent = daily(user["id"], now="2026-10-24T05:00:00")   # due 06:00
    old = daily(user["id"], now="2026-10-24T05:00:00", schedule={"kind": "hours", "every": 1})
    with chats.lock, chats.conn:
        chats.conn.execute("UPDATE tasks SET next_run = '2026-10-24T03:00:00+00:00' WHERE id = ?", (old["id"],))
    cut_off = tasks.run_now(user["id"], recent["id"])

    assert tasks.recover(utc("2026-10-24T06:30:00")) >= 1
    assert tasks.get_run(cut_off["id"])["status"] == "failed"
    assert tasks.get(user["id"], old["id"])["next_run"] > "2026-10-24T06:30:00+00:00"
    queued = {tasks.get_run(r)["task_id"] for r in tasks.due(utc("2026-10-24T06:30:00"))}
    assert recent["id"] in queued and old["id"] not in queued


def test_runs_have_limits(alice, monkeypatch):
    user, _ = alice
    task = daily(user["id"])

    # A model that never stops calling tools
    monkeypatch.setattr(limits, "_saved", {"task_model_calls": 2})
    replies(monkeypatch, itertools.repeat(ai("", [("search_documents", {"query": "anything"})])))
    run = tasks.execute(tasks.run_now(user["id"], task["id"])["id"])
    assert run["status"] == "failed" and run["error"] == "Stopped after 2 model calls"

    # One that takes too long
    monkeypatch.setattr(tasks, "run_seconds", lambda minutes: 0.2)
    monkeypatch.setattr(tasks, "run_reply", lambda thread_id, content, stop, emit, **kw:
                        (stop.wait(5), emit({"type": "stopped", "stats": {}})))
    run = tasks.execute(tasks.run_now(user["id"], task["id"])["id"])
    assert run["status"] == "failed" and run["error"].startswith("Took longer than")


def test_failures_in_a_row_turn_a_task_off(alice, monkeypatch):
    user, client = alice
    task = daily(user["id"])
    monkeypatch.setattr(tasks, "run_reply", lambda thread_id, content, stop, emit, **kw:
                        (emit({"type": "error", "message": "model down"}), emit({"type": "done", "stats": {}})))
    for _ in range(limits.get("task_failures_to_pause")):
        tasks.execute(tasks.run_now(user["id"], task["id"])["id"])
    off = client.get(f"/tasks/{task['id']}").json()
    assert not off["enabled"] and off["next_run"] is None and off["disabled_reason"] == "Failed 3 times in a row: model down"

    again = client.patch(f"/tasks/{task['id']}", json={"enabled": True}).json()
    assert again["enabled"] and again["failures"] == 0 and again["disabled_reason"] is None


def test_a_task_with_an_agent_uses_it_and_stops_when_its_gone(alice, monkeypatch):
    user, client = alice
    agent = client.post("/agents", json={"name": "Newsroom", "instructions": "Use headlines."}).json()
    task = client.post("/tasks", json={"prompt": "News?", "agent_id": agent["id"], "tz": VIENNA,
                                       "schedule": {"kind": "daily", "time": "08:00"}}).json()
    fake = replies(monkeypatch, ai("HEADLINE: calm day."))
    run = tasks.execute(client.post(f"/tasks/{task['id']}/run").json()["id"])
    system = fake.seen[0][0]
    assert isinstance(system, SystemMessage) and "You are Newsroom." in system.content and "Use headlines." in system.content
    assert next(c for c in client.get("/threads").json() if c["thread_id"] == run["thread_id"])["agent_id"] == agent["id"]

    client.delete(f"/agents/{agent['id']}")
    run = tasks.execute(client.post(f"/tasks/{task['id']}/run").json()["id"])
    assert run["status"] == "failed" and run["error"] == tasks.AGENT_GONE
    assert client.get(f"/tasks/{task['id']}").json()["disabled_reason"] == tasks.AGENT_GONE


def test_run_now_and_stop(alice, bob, monkeypatch):
    user, client = alice
    _, bobs = bob
    task = daily(user["id"])
    queued = client.post(f"/tasks/{task['id']}/run").json()
    assert queued["status"] == "queued" and queued["trigger"] == "manual"
    assert client.post(f"/tasks/{task['id']}/run").status_code == 409
    assert bobs.post(f"/tasks/runs/{queued['id']}/stop").status_code == 404
    assert client.post(f"/tasks/runs/{queued['id']}/stop").status_code == 204
    assert tasks.execute(queued["id"]) is None and tasks.get_run(queued["id"])["status"] == "stopped"
    assert client.post(f"/tasks/runs/{queued['id']}/stop").status_code == 409

    started = threading.Event()

    def slow(thread_id, content, stop, emit, **kw):
        started.set()
        stop.wait(5)
        emit({"type": "stopped", "stats": {}})

    monkeypatch.setattr(tasks, "run_reply", slow)
    for stop_it in (lambda run: client.post(f"/tasks/runs/{run['id']}/stop"),
                    lambda run: client.post("/stop", json={"thread_id": tasks.get_run(run["id"])["thread_id"]})):
        started.clear()
        run = client.post(f"/tasks/{task['id']}/run").json()
        worker = threading.Thread(target=tasks.execute, args=(run["id"],))
        worker.start()
        assert started.wait(5)
        assert stop_it(run).status_code == 204
        worker.join(5)
        assert tasks.get_run(run["id"])["status"] == "stopped"


def test_tasks_wait_for_web_replies(monkeypatch):
    monkeypatch.setattr(tasks, "BUSY_WAIT_SECONDS", 1)
    chatting = iter([True, True, False])
    monkeypatch.setattr(tasks, "busy", lambda: next(chatting))
    monkeypatch.setattr(tasks.time, "sleep", lambda seconds: None)
    assert tasks.wait_until_quiet(step=0.25) == 0.5
    # Not for ever
    monkeypatch.setattr(tasks, "busy", lambda: True)
    assert tasks.wait_until_quiet(step=0.25) == 1
    # The server's web chat replies are what it waits for
    assert server.tasks.busy is not None


def test_minutes_days_and_monthly_schedules():
    now = utc("2026-10-24T07:02:30")
    minutes = tasks.clean_schedule({"kind": "minutes", "every": 1}, now)
    assert tasks.describe(minutes) == "Every minute"
    assert tasks.next_after(minutes, VIENNA, now) == utc("2026-10-24T07:03:30")
    quarter = tasks.clean_schedule({"kind": "minutes", "every": 15, "start": "2026-10-24T07:00:00+00:00"}, now)
    assert tasks.describe(quarter) == "Every 15 minutes"
    assert tasks.next_after(quarter, VIENNA, now) == utc("2026-10-24T07:15:00")
    assert tasks.describe(tasks.clean_schedule({"kind": "hours", "every": 1}, now)) == "Every hour"

    # Every 3 days from the local day it was set, at 08:00 Vienna time (across the clock change)
    days = tasks.clean_schedule({"kind": "days", "every": 3, "time": "08:00"}, now, VIENNA)
    assert days["start"] == "2026-10-24" and tasks.describe(days) == "Every 3 days at 08:00"
    assert tasks.next_after(days, VIENNA, now) == utc("2026-10-27T07:00:00")
    assert tasks.next_after(days, VIENNA, utc("2026-10-24T05:00:00")) == utc("2026-10-24T06:00:00")

    # The 31st is the last day in shorter months
    monthly = tasks.clean_schedule({"kind": "monthly", "day": 31, "time": "09:00"}, now)
    assert tasks.describe(monthly) == "Monthly on the 31st (or the last day) at 09:00"
    assert tasks.next_after(monthly, VIENNA, now) == utc("2026-10-31T08:00:00")
    assert tasks.next_after(monthly, VIENNA, utc("2026-10-31T09:00:00")) == utc("2026-11-30T08:00:00")
    assert tasks.next_after(monthly, VIENNA, utc("2027-01-31T09:00:00")) == utc("2027-02-28T08:00:00")
    assert tasks.describe(tasks.clean_schedule({"kind": "monthly", "day": 2, "time": "09:00"}, now)) == "Monthly on the 2nd at 09:00"

    for bad in [{"kind": "minutes", "every": 0}, {"kind": "minutes", "every": 24 * 60 + 1},
                {"kind": "days", "every": 0, "time": "08:00"}, {"kind": "monthly", "day": 32, "time": "08:00"}]:
        with pytest.raises(tasks.TaskError):
            tasks.clean_schedule(bad, now)


def test_an_every_minute_task_doesnt_pile_up_runs(alice):
    user, _ = alice
    task = tasks.create(user["id"], {"prompt": "Ping", "schedule": {"kind": "minutes", "every": 1}}, now=utc("2026-10-24T07:00:00"))
    mine = lambda ids: [r for r in ids if tasks.get_run(r)["task_id"] == task["id"]]
    assert len(mine(tasks.due(utc("2026-10-24T07:01:00")))) == 1
    # Still queued a minute later: no second run, but the next time moves on
    assert mine(tasks.due(utc("2026-10-24T07:02:00"))) == []
    assert tasks.get(user["id"], task["id"])["next_run"] == "2026-10-24T07:03:00+00:00"


def test_admins_change_the_limits(client, accounts, monkeypatch):
    from fastapi.testclient import TestClient
    from labb.server import app as server
    monkeypatch.setattr(limits, "_saved", None)
    listed = {l["key"]: l for l in client.get("/admin/limits").json()}
    assert listed["task_model_calls"]["value"] == listed["task_model_calls"]["default"] == 25

    changed = {l["key"]: l["value"] for l in client.put("/admin/limits", json={"task_model_calls": 40, "chat_model_calls": 10}).json()}
    assert changed["task_model_calls"] == 40 and limits.get("chat_model_calls") == 10
    assert limits.recursion(10) == 31 and limits.recursion(0) is None
    # Out of range, unknown, or not a number: refused, nothing changed
    assert client.put("/admin/limits", json={"workflow_parallel": 500}).status_code == 400
    assert client.put("/admin/limits", json={"nope": 1}).status_code == 400
    assert client.put("/admin/limits", json={"task_minutes": "lots"}).status_code == 422
    # Kept, and read again from the database
    monkeypatch.setattr(limits, "_saved", None)
    assert limits.get("task_model_calls") == 40
    # null goes back to the default
    client.put("/admin/limits", json={"task_model_calls": None, "chat_model_calls": None})
    assert limits.get("task_model_calls") == 25 and limits.get("chat_model_calls") == 0

    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert member.get("/admin/limits").status_code == 403


def test_a_task_can_be_made_before_anything_read_the_limits(alice, monkeypatch):
    # Just after the server starts the limits aren't in memory yet, and reading them takes the
    # database's lock, so they must be read before it's held
    import threading
    from labb.server import limits
    monkeypatch.setattr(limits, "_saved", None)
    user, _ = alice
    made = {}
    worker = threading.Thread(target=lambda: made.update(task=daily(user["id"]),
                                                         runs=tasks.runs_for(user["id"], "nope")), daemon=True)
    worker.start()
    worker.join(10)
    assert not worker.is_alive(), "creating a task hung"
    assert made["task"]["name"] and made["runs"] == []


def test_a_task_can_keep_going_until_its_done(alice, monkeypatch):
    from labb.tools import goal

    user, client = alice
    task = daily(user["id"], goal=True)
    assert task["goal"] is True and client.get(f"/tasks/{task['id']}").json()["goal"] is True
    monkeypatch.setattr(limits, "_saved", {"task_model_calls": 10})
    # It stops after its first answer, is sent back to its goal, and only ends once it says it's done
    fake = replies(monkeypatch, ai("Here are two headlines."), ai("", [("update_goal", {"status": "complete", "note": "five headlines"})]),
                   ai("Here are five headlines."))
    run = tasks.execute(tasks.run_now(user["id"], task["id"])["id"])
    assert run["status"] == "done" and len(fake.seen) == 3
    kept = goal.get({"configurable": {"thread_id": run["thread_id"]}})
    assert kept["status"] == "complete" and kept["budget"] == 9 and kept["objective"] == "Summarize the news."
    # The run says how its goal ended
    assert run["goal"] == {"status": "complete", "note": "five headlines", "used": 2, "budget": 9}
    assert client.get(f"/tasks/{task['id']}").json()["last_run"]["goal"]["status"] == "complete"
    assert tasks.update(user["id"], task["id"], {"goal": False})["goal"] is False


def test_deleting_a_chat_deletes_its_work():
    from labb import chats
    thread = f"web-{uuid4()}"
    chats.set_work(thread, "todos", "[]")
    chats.delete_chat(thread)
    assert chats.get_work(thread, "todos") is None
