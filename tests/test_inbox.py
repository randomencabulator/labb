"""The inbox: notifications per person, keys that replace unread ones, marking read, the live
stream, and what tells it (tasks, Studio, documents, invites, the apps and services watch)."""

import asyncio
import threading
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from labb import chats, memories, runner
from labb.server import app as server
from labb.server import auth, documents, inbox, limits, tasks, watch
from labb.server.studio import engine, store

from conftest import ai
from test_tasks import daily, replies


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(memories, "index", None)
    monkeypatch.setattr(runner, "name_chat", lambda thread_id, config: None)


def person(role: str = "member") -> tuple[dict, TestClient]:
    user = auth.create_user(f"u{uuid4().hex[:10]}", "a-password", role=role)
    key, _ = auth.create_key(user["id"], "laptop")
    return user, TestClient(server.app, headers={"Authorization": f"Bearer {key}"})


@pytest.fixture
def alice(accounts):
    return person()


@pytest.fixture
def bob(accounts):
    return person()


def test_each_person_has_their_own_inbox(alice, bob):
    (a, client), (b, other) = alice, bob
    first = inbox.add(a["id"], "task", "Morning news", "Task finished", link="/chat/t1", level="success")
    inbox.add(a["id"], "studio", "Approval needed", level="warning")
    inbox.add(b["id"], "task", "Not Alice's")

    listed = client.get("/inbox").json()
    assert listed["unread"] == 2
    assert [i["title"] for i in listed["items"]] == ["Approval needed", "Morning news"]
    assert listed["items"][1] == {**first, "read": False}
    assert other.get("/inbox").json()["unread"] == 1

    assert client.post("/inbox/read", json={"ids": [first["id"]]}).json() == {"unread": 1}
    assert [i["title"] for i in client.get("/inbox", params={"unread": True}).json()["items"]] == ["Approval needed"]
    # Someone else's can't be read or deleted
    other.post("/inbox/read", json={"ids": [listed["items"][0]["id"]]})
    assert other.delete(f"/inbox/{first['id']}").status_code == 404
    assert client.get("/inbox").json()["unread"] == 1

    assert client.post("/inbox/read", json={}).json() == {"unread": 0}
    assert client.delete(f"/inbox/{first['id']}").status_code == 204
    assert client.delete("/inbox").status_code == 204
    assert client.get("/inbox").json() == {"items": [], "unread": 0}


def test_a_key_replaces_the_unread_one_and_not_a_read_one(alice):
    user, client = alice
    first = inbox.add(user["id"], "health", "Crawler isn't working", key="health:Crawler", level="error")
    again = inbox.add(user["id"], "health", "Crawler is working again", key="health:Crawler", level="success")
    assert again["id"] == first["id"]
    assert [(i["title"], i["level"]) for i in client.get("/inbox").json()["items"]] == [("Crawler is working again", "success")]

    inbox.mark_read(user["id"])
    inbox.add(user["id"], "health", "Crawler isn't working", key="health:Crawler", level="error")
    assert [i["read"] for i in client.get("/inbox").json()["items"]] == [False, True]


def test_only_the_newest_are_kept(alice, monkeypatch):
    user, client = alice
    real = limits.get
    monkeypatch.setattr(limits, "get", lambda key: 3 if key == "inbox_kept" else real(key))
    for n in range(5):
        inbox.add(user["id"], "task", f"Number {n}")
    assert [i["title"] for i in client.get("/inbox").json()["items"]] == ["Number 4", "Number 3", "Number 2"]


def test_marking_read_what_was_dealt_with_elsewhere(alice, bob):
    (a, client), (b, _) = alice, bob
    inbox.add(a["id"], "studio", "Approval needed", key="approval:run1:n1")
    inbox.add(a["id"], "studio", "Approval needed", key="approval:run1:n10")
    inbox.add(b["id"], "studio", "Approval needed", key="approval:run1:n1")
    # The key exactly, for everyone
    assert inbox.mark_read_about(key="approval:run1:n1") == 2
    assert [i["key"] for i in client.get("/inbox", params={"unread": True}).json()["items"]] == ["approval:run1:n10"]
    # A prefix taken literally: % and _ aren't wildcards
    inbox.add(a["id"], "studio", "Other", key="approval:run2:n1")
    assert inbox.mark_read_about(key_prefix="approval:run_:") == 0
    assert inbox.mark_read_about(key_prefix="approval:run1:") == 1
    assert inbox.mark_read_about() == 0


def test_the_stream_says_the_count_then_each_new_one(alice):
    user, _ = alice
    inbox.add(user["id"], "task", "Before")

    async def follow():
        stream = inbox.follow(user["id"])
        seen = [await anext(stream)]
        threading.Thread(target=lambda: inbox.add(user["id"], "task", "During", key="k")).start()
        seen.append(await asyncio.wait_for(anext(stream), 5))
        threading.Thread(target=lambda: inbox.mark_read(user["id"])).start()
        seen.append(await asyncio.wait_for(anext(stream), 5))
        await stream.aclose()
        return seen

    first, added, read = asyncio.run(follow())
    assert first == {"type": "unread", "unread": 1}
    assert added["type"] == "notification" and added["item"]["title"] == "During" and added["unread"] == 2
    assert read == {"type": "unread", "unread": 0}
    assert user["id"] not in inbox._followers


def test_tasks_tell_their_results_failures_and_pausing(alice, monkeypatch):
    user, client = alice
    task = daily(user["id"])
    replies(monkeypatch, ai("Three things happened."))
    run = tasks.execute(tasks.run_now(user["id"], task["id"])["id"])
    [done] = client.get("/inbox").json()["items"]
    assert (done["title"], done["link"], done["level"]) == ("Morning news", f"/chat/{run['thread_id']}", "success")
    # Opening its chat reads it
    client.get("/history", params={"thread_id": run["thread_id"]})
    assert client.get("/inbox").json()["unread"] == 0

    monkeypatch.setattr(tasks, "run_reply", lambda thread_id, content, stop, emit, **kw:
                        (emit({"type": "error", "message": "model down"}), emit({"type": "done", "stats": {}})))
    for _ in range(limits.get("task_failures_to_pause")):
        tasks.execute(tasks.run_now(user["id"], task["id"])["id"])
    unread = client.get("/inbox", params={"unread": True}).json()["items"]
    # The failures replace each other while unread
    assert [(i["title"], i["body"]) for i in unread] == [
        ("Task paused: Morning news", "Failed 3 times in a row: model down"),
        ("Task failed: Morning news", "model down"),
    ]
    assert unread[0]["link"] == f"/tasks/{task['id']}"


def test_reading_a_tasks_result_in_the_inbox_reads_its_chat(alice, bob, monkeypatch):
    (user, client), (other, _) = alice, bob
    task = daily(user["id"])
    replies(monkeypatch, ai("First."), ai("Second."))
    first = tasks.execute(tasks.run_now(user["id"], task["id"])["id"])
    [item] = client.get("/inbox").json()["items"]
    unread = lambda: {c["thread_id"] for c in client.get("/threads").json() if c["unread"]}
    assert unread() == {first["thread_id"]}
    client.post("/inbox/read", json={"ids": [item["id"]]})
    assert unread() == set()

    # Mark all read too
    second = tasks.execute(tasks.run_now(user["id"], task["id"])["id"])
    assert unread() == {second["thread_id"]}
    client.post("/inbox/read", json={})
    assert unread() == set()

    # A link to someone else's chat doesn't read it
    theirs = daily(other["id"])
    replies(monkeypatch, ai("Theirs."))
    run = tasks.execute(tasks.run_now(other["id"], theirs["id"])["id"])
    planted = inbox.add(user["id"], "task", "Not mine", link=f"/chat/{run['thread_id']}")
    inbox.mark_read(user["id"], [planted["id"]])
    assert chats_unread(run["thread_id"])


def test_studio_runs_tell_approvals_failures_and_results(alice, bob, monkeypatch):
    (a, client), (b, other) = alice, bob
    workflow = {"name": "Invoices", "owner_id": b["id"]}
    by_id = {"n2": {"type": "approval", "label": "Pay it?"}}
    run = {"id": "r1", "workflow_id": "w1", "user_id": a["id"], "status": "waiting", "trigger": "manual", "error": None}
    monkeypatch.setattr(store, "steps", lambda run_id: [
        {"node_id": "n2", "key": "pay", "status": "waiting", "input": {"message": "€120 to ACME"}}])
    engine._tell(run, workflow, "Invoices", by_id)
    for c in (client, other):
        [item] = c.get("/inbox").json()["items"]
        assert (item["title"], item["body"], item["link"]) == ("Approval needed: Invoices", "Pay it?: €120 to ACME",
                                                               "/studio/workflows/w1/runs/r1")
    inbox.mark_read_about(key=engine.approval_key("r1", "n2"))
    assert client.get("/inbox").json()["unread"] == other.get("/inbox").json()["unread"] == 0

    engine._tell({**run, "status": "failed", "error": "The HTTP step got 500"}, workflow, "Invoices", by_id)
    engine._tell({**run, "status": "failed", "trigger": "test"}, workflow, "Invoices", by_id)
    engine._tell({**run, "status": "done"}, workflow, "Invoices", by_id)
    engine._tell({**run, "status": "done", "trigger": "schedule"}, workflow, "Invoices", by_id)
    engine._tell({**run, "workflow_id": "try-x", "status": "failed"}, workflow, "Invoices", by_id)
    assert [i["title"] for i in client.get("/inbox", params={"unread": True}).json()["items"]] == [
        "Invoices", "Workflow failed: Invoices"]


def test_a_document_that_wont_index_tells_its_owner(alice, monkeypatch):
    user, client = alice
    monkeypatch.setattr(documents, "start_indexing", lambda doc_id: None)
    doc = documents.add(user["id"], "notes.txt", b"Some notes about tomatoes.")
    monkeypatch.setattr(documents, "chunks", lambda *a: (_ for _ in ()).throw(RuntimeError("model not found")))
    # Indexed again at a start: not again
    documents.index(doc["id"])
    assert client.get("/inbox").json()["unread"] == 0
    documents.index(doc["id"], tell=True)
    [item] = client.get("/inbox").json()["items"]
    assert item["title"] == "Couldn't index notes.txt" and "model not found" in item["body"]
    assert item["link"] == f"/documents/{doc['id']}"


def test_an_invite_used_tells_who_made_it(alice):
    user, client = alice
    token, _ = auth.create_invite(user["id"])
    joined = auth.accept_invite(token, f"c{uuid4().hex[:8]}", "a-long-password")
    [item] = client.get("/inbox").json()["items"]
    assert item["title"] == f"{joined['username']} joined" and item["link"] == "/settings/users"


def test_the_watch_tells_owners_about_crashed_apps(monkeypatch):
    monkeypatch.setattr(watch, "_apps", None)
    monkeypatch.setattr(watch, "_pages", {})

    def app(status="running", detail="", build=1, ok=True):
        return {"id": "a1", "name": "Shop", "status": status, "detail": detail,
                "page_build": {"build": build, "ok": ok, "error": None if ok else "Type error in App.tsx"}}

    # The first look only takes note
    assert watch.check_apps({"u1": [app(status="exited", detail="Crashed (exit code 1): see its logs")]}) == []
    assert watch.check_apps({"u1": [app()]}) == []
    # Idle and stopped by its owner: nothing to say
    assert watch.check_apps({"u1": [app("exited", "Stopped after 30 minutes without use; opening it starts it again")]}) == []
    assert watch.check_apps({"u1": [app("exited", "Stopped")]}) == []
    [(owner, crashed)] = watch.check_apps({"u1": [app("exited", "Crashed (exit code 1): see its logs")]})
    assert owner == "u1" and crashed["title"] == "Shop crashed" and crashed["link"] == "/apps/a1"
    # The same again isn't news
    assert watch.check_apps({"u1": [app("exited", "Crashed (exit code 1): see its logs")]}) == []
    [(_, oom)] = watch.check_apps({"u1": [app("exited", "Ran out of memory (512)")]})
    assert oom["title"] == "Shop ran out of memory"
    [(_, page)] = watch.check_apps({"u1": [app("exited", "Ran out of memory (512)", build=2, ok=False)]})
    assert page["title"] == "Shop's page didn't build" and page["body"] == "Type error in App.tsx"


def test_the_watch_tells_admins_when_a_service_goes_and_comes_back(monkeypatch):
    monkeypatch.setattr(watch, "_health", None)
    down = {"name": "Crawler", "ok": False, "detail": "Can't reach http://crawler:3000"}
    up = {"name": "Crawler", "ok": True, "detail": "Answering"}
    warning = {"name": "Sandbox", "ok": None, "detail": "No token"}
    # Down at the first look is said
    assert [t["title"] for t in watch.check_health([down, warning])] == ["Crawler isn't working"]
    assert watch.check_health([down]) == []
    [back] = watch.check_health([up])
    assert back["title"] == "Crawler is working again" and back["key"] == "health:Crawler"
    assert watch.check_health([up]) == []


def test_admins_hear_from_the_watch(accounts, monkeypatch):
    admin, admin_client = person("admin")
    member, member_client = person()
    monkeypatch.setattr(watch, "_health", None)
    monkeypatch.setattr(watch, "_list_apps", lambda: None)
    monkeypatch.setattr(watch, "_health_results", lambda: [{"name": "Ollama", "ok": False, "detail": "Can't reach it"}])
    watch.tick(0)
    assert [i["title"] for i in admin_client.get("/inbox").json()["items"]] == ["Ollama isn't working"]
    assert member_client.get("/inbox").json()["unread"] == 0


def chats_unread(thread_id: str) -> bool:
    with chats.lock:
        return bool(chats.conn.execute("SELECT unread FROM chats WHERE thread_id = ?", (thread_id,)).fetchone()[0])
