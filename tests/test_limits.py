"""Settings → Limits applied to the web chat"""

import itertools
import json

from conftest import ai


def test_a_chat_reply_stops_at_its_model_calls(client, fake_model, monkeypatch):
    from labb import runner
    from labb.server import limits
    monkeypatch.setattr(runner, "name_chat", lambda thread_id, config: None)
    monkeypatch.setattr(limits, "_saved", {"chat_model_calls": 2})
    fake_model(*itertools.islice(itertools.repeat(ai("", tool_calls=[("list_files", {})])), 10))
    with client.stream("POST", "/chat", json={"message": "go on forever"}) as response:
        events = [json.loads(line) for line in response.iter_lines()]
    assert sum(e["type"] == "tool_call" for e in events) == 2
    error = next(e["message"] for e in events if e["type"] == "error")
    assert "Settings → Limits" in error and "recursion limit" in error


def test_the_hidden_limits_are_there_and_apply(client, accounts, monkeypatch):
    from fastapi.testclient import TestClient
    from labb.server import app as server
    from labb.server import limits
    monkeypatch.setattr(limits, "_saved", None)
    listed = {l["key"]: l for l in client.get("/admin/limits").json()}
    assert listed["agents_per_person"]["value"] == 100 and listed["attachments_per_message"]["group"] == "Chat"
    from labb.chats import conn
    had = conn.execute("SELECT COUNT(*) FROM agents WHERE user_id = ?", (accounts["admin"][0]["id"],)).fetchone()[0]
    try:
        client.put("/admin/limits", json={"attachments_per_message": 1, "agents_per_person": had + 1})
        # Anyone signed in sees the ones the pages check before sending
        member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
        assert member.get("/limits").json()["attachments_per_message"] == 1
        attached = [{"name": f"{i}.txt", "kind": "text", "data": "hi"} for i in range(2)]
        answer = client.post("/chat", json={"message": "hi", "attachments": attached})
        assert answer.status_code == 400 and answer.json()["detail"] == "At most 1 attachments"
        assert client.post("/agents", json={"name": "A"}).status_code == 201
        refused = client.post("/agents", json={"name": "B"})
        assert refused.status_code == 400 and f"You have {had + 1} agents" in refused.json()["detail"]
    finally:
        client.put("/admin/limits", json={"attachments_per_message": None, "agents_per_person": None})


class Answer:
    def __init__(self, status, body):
        self.status_code, self.body, self.text = status, body, json.dumps(body)

    def json(self):
        return self.body


def test_the_spawners_and_sites_limits_are_shown_and_saved_through_it(client, monkeypatch):
    from labb.server import apps, artifact_tools, limits
    monkeypatch.setattr(apps, "SPAWNER_URL", "http://spawner")
    monkeypatch.setattr(artifact_tools, "SITES_URL", "")
    spawner = [{"key": "app_count", "group": "Apps", "label": "Apps per person", "description": "", "default": 10,
                "min": 1, "max": 1000, "unit": "", "value": 10}]
    sent = []

    def request(method, url, headers=None, json=None, timeout=None):
        assert url == "http://spawner/limits"
        if method == "PUT":
            sent.append(json)
            if json.get("app_count") == 0:
                return Answer(400, {"detail": "Apps: Apps per person is a whole number from 1 to 1000"})
            spawner[0]["value"] = json["app_count"]
        return Answer(200, spawner)

    monkeypatch.setattr(limits.httpx, "request", request)
    listed = {l["key"]: l for l in client.get("/admin/limits").json()}
    assert listed["app_count"]["group"] == "Apps"
    changed = {l["key"]: l["value"] for l in client.put("/admin/limits", json={"app_count": 3, "task_minutes": 20}).json()}
    assert changed["app_count"] == 3 and changed["task_minutes"] == 20 and sent == [{"app_count": 3}]
    refused = client.put("/admin/limits", json={"app_count": 0, "task_minutes": 21})
    assert refused.status_code == 400 and "1 to 1000" in refused.json()["detail"] and limits.get("task_minutes") == 20
    client.put("/admin/limits", json={"task_minutes": None})

    # A spawner that doesn't answer: the rest is there, and the page says why its part isn't
    def down(*args, **kwargs):
        raise limits.httpx.ConnectError("refused")

    monkeypatch.setattr(limits.httpx, "request", down)
    shown = client.get("/admin/limits", params={"problems": True}).json()
    assert "app_count" not in {l["key"] for l in shown["limits"]} and "task_minutes" in {l["key"] for l in shown["limits"]}
    assert shown["problems"] == ["Couldn't reach the app spawner: refused, so its limits aren't here"]
