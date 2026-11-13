"""The server's MCP servers: saved encrypted by admins, their tools offered in the web chat, and
the tools that change something asked about in the chat"""

import sys
import threading
from pathlib import Path

import pytest

from labb.tools import server_tools, tools_for

NOTES_SERVER = {"notes": {"command": sys.executable, "args": [str(Path(__file__).parent / "fixtures" / "mcp_notes_server.py")]}}


@pytest.fixture
def connected(client):
    from labb.server import mcp
    status = client.put("/admin/mcp", json={"servers": NOTES_SERVER}).json()["status"]
    yield status
    mcp.save({})


def test_admins_save_servers_and_the_web_chat_gets_their_tools(client, connected, accounts):
    from fastapi.testclient import TestClient
    from labb.server import app as server
    assert connected["servers"] == {"notes": 2}
    names = {tool.name for tool in tools_for(False)}
    assert {"mcp__notes__count_notes", "mcp__notes__add_note"} <= names
    # Not in the terminal (it has its own), nor for agents
    assert "mcp__notes__add_note" not in {tool.name for tool in tools_for(True)}
    assert "mcp__notes__add_note" not in {tool.name for tool in tools_for(False, frozenset({"web"}))}
    # Encrypted where they're kept, and only for admins
    from labb.chats import conn, lock
    with lock:
        stored = conn.execute("SELECT value FROM server_settings WHERE key = 'mcp_servers'").fetchone()[0]
    assert "mcp_notes_server" not in stored
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert member.get("/admin/mcp").status_code == 403


def test_bad_settings_are_refused(client):
    assert client.put("/admin/mcp", json={"servers": {"x": {"nothing": 1}}}).status_code == 400


def test_read_only_tools_run_and_the_others_ask_in_the_chat(connected):
    from labb.server import app as server
    by_name = {tool.name: tool for tool in server_tools}
    events = []
    stop = threading.Event()
    approve = server.chat_approver("user-1", events.append, stop)
    config = {"configurable": {"approve": approve}}
    assert by_name["mcp__notes__count_notes"].invoke({}, config) == "0 notes" and not events

    # Answered from another thread, like POST /chat/approve
    def answer(allow):
        while not events or events[-1]["id"] not in server.approvals:
            pass
        server.approvals[events[-1]["id"]][1].put("yes" if allow else ("no", "not now"))

    threading.Thread(target=answer, args=(False,)).start()
    refused = by_name["mcp__notes__add_note"].invoke({"text": "milk"}, config)
    assert "didn't allow" in refused and "not now" in refused
    assert events[-1]["type"] == "approval" and '"milk"' in events[-1]["detail"]

    threading.Thread(target=answer, args=(True,)).start()
    assert by_name["mcp__notes__add_note"].invoke({"text": "eggs"}, config) == "Added: eggs"
    # A stop while it waits is a no; without a way to ask (a task), it doesn't run
    stop.set()
    assert "didn't allow" in by_name["mcp__notes__add_note"].invoke({"text": "x"}, config)
    assert "isn't possible" in by_name["mcp__notes__add_note"].invoke({"text": "x"}, {"configurable": {}})


def test_only_the_asker_can_answer(client, accounts):
    from fastapi.testclient import TestClient
    from labb.server import app as server
    import queue
    server.approvals["q1"] = (accounts["admin"][0]["id"], queue.Queue(maxsize=1))
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert member.post("/chat/approve", json={"id": "q1", "allow": True}).status_code == 404
    assert client.post("/chat/approve", json={"id": "q1", "allow": True}).status_code == 204
    assert client.post("/chat/approve", json={"id": "q1", "allow": True}).status_code == 409
    server.approvals.pop("q1")


def test_a_web_chat_asks_and_goes_on_after_the_answer(client, connected, fake_model, monkeypatch):
    import json as json_
    from labb import runner
    monkeypatch.setattr(runner, "name_chat", lambda thread_id, config: None)
    from conftest import ai
    fake_model(ai(tool_calls=[("mcp__notes__add_note", {"text": "bread"})]), ai("Added bread to your notes."))
    events = []

    def answer_when_asked():
        while not any(e["type"] == "approval" for e in events):
            pass
        asked = next(e for e in events if e["type"] == "approval")
        assert client.post("/chat/approve", json={"id": asked["id"], "allow": True}).status_code == 204

    answering = threading.Thread(target=answer_when_asked)
    answering.start()
    with client.stream("POST", "/chat", json={"message": "note bread"}) as response:
        for line in response.iter_lines():
            events.append(json_.loads(line))
    answering.join(timeout=10)
    kinds = [e["type"] for e in events]
    assert kinds.index("tool_call") < kinds.index("approval") < kinds.index("tool_result") and kinds[-1] == "done"
    assert next(e for e in events if e["type"] == "tool_result")["error"] is False
