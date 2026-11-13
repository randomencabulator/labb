"""The heartbeat: off by default; once on, each beat takes the oldest open to-dos (per_beat at most)
and runs each in a chat of its own, and a beat with nothing open does nothing. It's a task of its
own kind, so the task pages leave it out, and a restart puts a cut-off to-do back on the list."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from labb import memories, model, runner
from labb.server import app as server
from labb.server import auth, heartbeat, tasks

from conftest import FakeModel, ai


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(memories, "index", None)
    monkeypatch.setattr(runner, "name_chat", lambda thread_id, config: None)


@pytest.fixture
def alice(accounts):
    user = auth.create_user(f"u{uuid4().hex[:10]}", "a-password")
    key, _ = auth.create_key(user["id"], "laptop")
    return user, TestClient(server.app, headers={"Authorization": f"Bearer {key}"})


def mine(user_id: str, queued: list[str]) -> list[str]:
    return [r for r in queued if tasks.get_run(r)["user_id"] == user_id]


def test_its_off_until_turned_on_and_takes_the_oldest_to_dos_each_beat(alice, monkeypatch):
    user, client = alice
    shown = client.get("/tasks/heartbeat").json()
    assert shown["enabled"] is False and shown["open"] == 0 and shown["every_minutes"] == 30
    for text in ("Check the weather in Vienna", "Summarize yesterday's notes", "Find a recipe with leeks"):
        assert client.post("/tasks/heartbeat/todos", json={"text": text}).status_code == 201
    # Off: nothing runs, however long it waits
    assert mine(user["id"], tasks.due(utc("2030-01-01T00:00:00"))) == []

    on = heartbeat.save(user["id"], {"enabled": True, "every_minutes": 15, "per_beat": 2}, now=utc("2026-11-12T10:00:00"))
    assert on["enabled"] and on["next_beat"] == "2026-11-12T10:15:00+00:00" and on["open"] == 3
    # Not a task in the task pages, and not counted as one
    assert client.get("/tasks").json() == []

    first = mine(user["id"], tasks.due(utc("2026-11-12T10:15:10")))
    assert len(first) == 2
    monkeypatch.setattr(model, "model_with_tools", lambda *args: FakeModel(messages=iter([ai("Sunny, 14°."), ai("Three notes.")])))
    done = [tasks.execute(r) for r in first]
    assert [d["status"] for d in done] == ["done", "done"]
    listed = client.get("/tasks/heartbeat/todos").json()
    assert [t["status"] for t in listed] == ["open", "done", "done"]
    assert listed[0]["text"] == "Find a recipe with leeks"
    weather = next(t for t in listed if t["text"].startswith("Check the weather"))
    chat = next(c for c in client.get("/threads").json() if c["thread_id"] == weather["thread_id"])
    assert chat["title"] == "Check the weather in Vienna" and chat["unread"]
    history = client.get("/history", params={"thread_id": weather["thread_id"]}).json()
    assert [e["content"] for e in history if e["type"] in ("user", "token")] == ["Check the weather in Vienna", "Sunny, 14°."]

    # The next beat takes the one left; the one after has nothing to do and runs nothing
    [last] = mine(user["id"], tasks.due(utc("2026-11-12T10:30:10")))
    monkeypatch.setattr(model, "model_with_tools", lambda *args: FakeModel(messages=iter([ai("Leek gratin.")])))
    assert tasks.execute(last)["status"] == "done"
    assert mine(user["id"], tasks.due(utc("2026-11-12T10:45:10"))) == []
    assert client.get("/tasks/heartbeat").json()["open"] == 0


def test_a_failed_to_do_says_why_and_can_go_back_on_the_list(alice, monkeypatch):
    user, client = alice
    heartbeat.save(user["id"], {"enabled": True, "every_minutes": 10}, now=utc("2026-11-12T10:00:00"))
    todo = client.post("/tasks/heartbeat/todos", json={"text": "Do something impossible"}).json()

    def broken(*args):
        raise RuntimeError("the model is down")

    monkeypatch.setattr(model, "model_with_tools", broken)
    [run] = mine(user["id"], tasks.due(utc("2026-11-12T10:10:10")))
    assert tasks.execute(run)["status"] == "failed"
    failed = heartbeat.get(user["id"], todo["id"])
    assert failed["status"] == "failed" and "the model is down" in failed["error"]
    again = client.patch(f"/tasks/heartbeat/todos/{todo['id']}", json={"reopen": True}).json()
    assert again["status"] == "open" and again["error"] is None


def test_beat_now_and_a_restart(alice, monkeypatch):
    user, client = alice
    assert client.post("/tasks/heartbeat/beat").status_code == 400
    client.put("/tasks/heartbeat", json={"enabled": True, "every_minutes": 60})
    client.post("/tasks/heartbeat/todos", json={"text": "Tidy my notes"})
    assert client.post("/tasks/heartbeat/beat").json() == {"queued": 1}
    assert client.post("/tasks/heartbeat/beat").status_code == 400

    # A restart cuts its run off: the to-do goes back on the list
    run = next(r for r in [tasks._queue.get_nowait() for _ in range(tasks._queue.qsize())] if tasks.get_run(r)["user_id"] == user["id"])
    todo = heartbeat.claim(user["id"], run)
    assert heartbeat.get(user["id"], todo["id"])["status"] == "running"
    tasks.recover(utc("2026-11-12T12:00:00"))
    assert heartbeat.get(user["id"], todo["id"])["status"] == "open"


def test_whats_refused(alice, accounts):
    user, client = alice
    assert client.put("/tasks/heartbeat", json={"every_minutes": 1}).status_code == 400
    assert client.put("/tasks/heartbeat", json={"per_beat": 0}).status_code == 400
    assert client.put("/tasks/heartbeat", json={"agent_id": "nope"}).status_code == 400
    assert client.post("/tasks/heartbeat/todos", json={"text": "  "}).status_code == 400
    todo = client.post("/tasks/heartbeat/todos", json={"text": "Mine"}).json()
    # Someone else's isn't there for others
    other = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert other.patch(f"/tasks/heartbeat/todos/{todo['id']}", json={"text": "Theirs"}).status_code == 404
    assert other.delete(f"/tasks/heartbeat/todos/{todo['id']}").status_code == 404
    assert other.get("/tasks/heartbeat/todos").json() == []
    assert client.delete(f"/tasks/heartbeat/todos/{todo['id']}").status_code == 204
