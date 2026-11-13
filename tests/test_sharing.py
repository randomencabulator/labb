"""Sharing a chat: everyone on the server can read it, only its owner can change it"""

import json

from fastapi.testclient import TestClient

from conftest import ai


def send(client, message: str) -> str:
    with client.stream("POST", "/chat", json={"message": message}) as response:
        events = [json.loads(line) for line in response.iter_lines()]
    return next(e["thread_id"] for e in events if e["type"] == "thread")


def test_a_shared_chat_can_be_read_by_everyone_and_changed_by_its_owner(client, accounts, fake_model, monkeypatch):
    from labb import runner
    from labb.server import app as server
    monkeypatch.setattr(runner, "name_chat", lambda thread_id, config: None)
    fake_model(ai("Let me look.", tool_calls=[("list_files", {})]), ai("Paris."))
    thread_id = send(client, "Capital of France?")
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})

    # Not shared yet: nobody else can see it
    assert member.get(f"/shared/{thread_id}").status_code == 404
    assert member.get("/history", params={"thread_id": thread_id}).status_code == 404

    assert client.patch(f"/threads/{thread_id}", json={"shared": True}).status_code == 204
    assert next(c for c in client.get("/threads").json() if c["thread_id"] == thread_id)["shared"] is True
    seen = member.get(f"/shared/{thread_id}").json()
    assert seen["owner"] == "admin" and seen["mine"] is False and seen["title"]
    kinds = [e["type"] for e in seen["events"]]
    assert kinds[0] == "user" and "token" in kinds and "tool_call" in kinds
    assert client.get(f"/shared/{thread_id}").json()["mine"] is True
    # Its tool results too, but nothing that changes it
    call = next(e for e in seen["events"] if e["type"] == "tool_call")
    assert member.get("/tool_result", params={"thread_id": thread_id, "tool_call_id": call["id"]}).status_code == 200
    assert member.patch(f"/threads/{thread_id}", json={"title": "mine now"}).status_code == 404
    assert member.delete(f"/threads/{thread_id}").status_code == 404
    with member.stream("POST", "/chat", json={"message": "hi", "thread_id": thread_id}) as response:
        assert response.status_code == 404

    # Unshared: gone for the others
    assert client.patch(f"/threads/{thread_id}", json={"shared": False}).status_code == 204
    assert member.get(f"/shared/{thread_id}").status_code == 404
    assert member.get("/tool_result", params={"thread_id": thread_id, "tool_call_id": call["id"]}).status_code == 404
