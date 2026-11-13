"""Sending labb's sessions to the server, and the server showing them."""

import threading
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, messages_to_dict

from labb import chats
from labb import runner
from labb.server import app as server
from labb.cli import sync
from conftest import ai


@pytest.fixture
def to_server(accounts, monkeypatch):
    """labb's sends go to the test server, with the admin's key; returns a switch to make
    the server unreachable"""

    state = {"up": True}
    server_client = TestClient(server.app)

    def post(url, json, headers, timeout):
        if not state["up"]:
            raise httpx.ConnectError("server down")
        response = server_client.post(url.removeprefix("http://server"), json=json, headers=headers)
        response.request = httpx.Request("POST", url)
        return response

    monkeypatch.setenv("LABB_SERVER", "http://server/")
    monkeypatch.setenv("LABB_API_KEY", accounts["admin"][1])
    monkeypatch.setattr(sync.httpx, "post", post)
    return state


def session_payload(**overrides):
    messages = [HumanMessage("hi", id="h1"), AIMessage("", id="a1", tool_calls=[{"name": "grep", "args": {"pattern": "x"}, "id": "t1"}])]
    return {"machine": "laptop", "directory": "/home/me/app", "title": "hi", "updated_at": "2026-10-23T10:00:00+00:00",
            "messages": messages_to_dict(messages), "stats": {}, **overrides}


def test_a_synced_session_is_listed_and_readable(client):
    thread = f"cli-{uuid4()}"
    assert client.post(f"/sync/{thread}", json=session_payload()).status_code == 204
    listed = next(s for s in client.get("/cli_sessions").json() if s["thread_id"] == thread)
    assert listed["machine"] == "laptop" and listed["directory"] == "/home/me/app" and listed["synced"]
    events = client.get("/history", params={"thread_id": thread}).json()
    assert events[0] == {"type": "user", "content": "hi", "files": [], "images": []}
    assert client.get("/tool_result", params={"thread_id": thread, "tool_call_id": "t1"}).json()["name"] == "grep"
    assert client.get("/export", params={"thread_id": thread}).text.startswith("# hi")


def test_syncing_again_replaces_the_session(client):
    thread = f"cli-{uuid4()}"
    client.post(f"/sync/{thread}", json=session_payload())
    client.post(f"/sync/{thread}", json=session_payload(title="hello again", messages=messages_to_dict([HumanMessage("only this")])))
    assert [e["content"] for e in client.get("/history", params={"thread_id": thread}).json()] == ["only this"]
    assert next(s for s in client.get("/cli_sessions").json() if s["thread_id"] == thread)["title"] == "hello again"


def test_bad_syncs_are_refused_and_copies_can_be_deleted(client):
    assert client.post(f"/sync/{uuid4()}", json=session_payload()).status_code == 400
    assert client.post(f"/sync/cli-{uuid4()}", json=session_payload(messages=[{"type": "nonsense"}])).status_code == 400
    thread = f"cli-{uuid4()}"
    client.post(f"/sync/{thread}", json=session_payload())
    assert client.delete(f"/sync/{thread}").status_code == 204
    assert client.delete(f"/sync/{thread}").status_code == 404
    assert all(s["thread_id"] != thread for s in client.get("/cli_sessions").json())


def labb_session(fake_model, config, text: str = "hello") -> str:
    """A terminal session with one reply, as labb makes it"""

    fake_model(ai("Hello!"))
    thread = f"cli-{uuid4()}"
    chats.touch_cli(thread, config["configurable"]["workdir"], text)
    runner.run_reply(thread, text, threading.Event(), lambda event: None, configurable=config["configurable"], name=False)
    return thread


def test_labb_sends_its_sessions(to_server, client, fake_model, config):
    thread = labb_session(fake_model, config)
    syncer = sync.Syncer()
    syncer.changed(thread)
    syncer.finish()
    assert thread not in chats.list_unsynced()
    synced = chats.get_synced(thread)
    assert [m["data"]["content"] for m in synced["messages"]] == ["hello", "Hello!"]
    assert synced["directory"] == config["configurable"]["workdir"]


def test_unsent_sessions_wait_and_go_out_later(to_server, fake_model, config):
    to_server["up"] = False
    thread = labb_session(fake_model, config)
    syncer = sync.Syncer()
    syncer.changed(thread)
    syncer.finish()
    assert thread in chats.list_unsynced() and chats.get_synced(thread) is None

    # The server is back: labb sends it when it starts again
    to_server["up"] = True
    sync.Syncer().finish()
    assert thread not in chats.list_unsynced() and chats.get_synced(thread) is not None


def test_sessions_belong_to_who_sent_them(client, accounts):
    thread = f"cli-{uuid4()}"
    client.post(f"/sync/{thread}", json=session_payload())
    bob = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert all(s["thread_id"] != thread for s in bob.get("/cli_sessions").json())
    assert bob.get("/history", params={"thread_id": thread}).status_code == 404
    assert bob.post(f"/sync/{thread}", json=session_payload()).status_code == 403
    assert bob.delete(f"/sync/{thread}").status_code == 404


def test_without_a_server_nothing_is_sent(monkeypatch):
    monkeypatch.delenv("LABB_SERVER", raising=False)
    assert sync.start() is None


def test_only_admins_take_over_the_servers_own_sessions(client, accounts):
    """A session run on the server itself has no owner, and only admins see it: a member can't claim it"""
    thread = f"cli-{uuid4()}"
    chats.touch_cli(thread, "/srv", "hello")
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert member.post(f"/sync/{thread}", json=session_payload()).status_code == 403
    assert client.post(f"/sync/{thread}", json=session_payload()).status_code == 204
