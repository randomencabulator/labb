"""Usage stats: the ledger, gateway metering, client versions, and who sees which numbers."""

from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, messages_to_dict

from labb.server import auth
from labb.server import gateway
from labb.server import install
from labb.server import app as server
from labb import usage
from conftest import ai


@pytest.fixture
def person(accounts):
    """A new member with a key, so their numbers start at zero: (user, client)"""

    user = auth.create_user(f"u{uuid4().hex[:10]}", "a-password")
    key, _ = auth.create_key(user["id"], "laptop")
    return user, TestClient(server.app, headers={"Authorization": f"Bearer {key}"})


def chat(client: TestClient, message: str, **fields) -> str:
    lines = client.post("/chat", json={"message": message, **fields}).text.splitlines()
    return next((line.split('"thread_id": "')[1].split('"')[0] for line in lines if '"thread_id"' in line), fields.get("thread_id"))


def test_web_replies_stay_counted_after_regenerating_and_deleting(person, fake_model, monkeypatch):
    from labb import runner

    user, client = person
    # Titles would ask the real model
    monkeypatch.setattr(runner, "name_chat", lambda thread_id, config: None)
    fake_model(ai("", [("web_search", {"query": "x"})], tokens=(100, 10)), ai("Found it", tokens=(150, 20)), ai("Again", tokens=(90, 9)))
    thread_id = chat(client, "look it up")
    totals = client.get("/usage").json()["totals"]
    assert (totals["replies"], totals["input_tokens"], totals["output_tokens"], totals["tool_calls"]) == (1, 250, 30, 1)

    # Regenerating replaces the reply in the chat, but both count
    chat(client, "look it up", thread_id=thread_id, replace_from=0)
    assert client.get("/usage").json()["totals"]["replies"] == 2

    client.delete(f"/threads/{thread_id}")
    numbers = client.get("/usage").json()
    assert numbers["totals"]["replies"] == 2 and numbers["totals"]["input_tokens"] == 340
    assert numbers["tools"] == [{"name": "web_search", "calls": 1}]
    assert numbers["by_source"][0]["source"] == "web"
    assert numbers["top_conversations"][0] == {"thread_id": thread_id, "title": "(deleted)", "source": "web",
                                               "replies": 2, "tokens": 379, "exists": False, "cost": None}


def session(stats: dict, **overrides) -> dict:
    messages = [HumanMessage("hi", id="h1"), AIMessage("hello", id="a1"), HumanMessage("more", id="h2"), AIMessage("sure", id="a2")]
    return {"machine": "laptop", "directory": "/home/me/app", "title": "hi", "updated_at": "2026-10-20T10:00:00+00:00",
            "messages": messages_to_dict(messages), "stats": stats, **overrides}


def test_synced_sessions_are_counted_once(person):
    user, client = person
    stats = {
        "a1": {"model": "qwen3.5:4b", "input_tokens": 10, "output_tokens": 5, "tools": {"read_file": 2}},
        "a2": {"model": "openai:gpt-5.5", "input_tokens": 20, "output_tokens": 8, "at": "2026-10-21T08:30:00+00:00"},
    }
    thread = f"cli-{uuid4()}"
    for _ in range(2):
        assert client.post(f"/sync/{thread}", json=session(stats)).status_code == 204
    # After /undo the session has fewer replies, but the ledger keeps them
    client.post(f"/sync/{thread}", json=session({"a1": stats["a1"]}, updated_at="2026-10-22T10:00:00+00:00"))

    numbers = client.get("/usage").json()
    assert numbers["totals"]["replies"] == 2 and numbers["totals"]["input_tokens"] == 30
    # A reply without its own time gets the session's first-seen update
    assert {d["day"] for d in numbers["by_day"]} == {"2026-10-20", "2026-10-21"}
    assert {d["source"] for d in numbers["by_day"]} == {"laptop"}
    assert numbers["tools"] == [{"name": "read_file", "calls": 2}]
    assert [m["model"] for m in numbers["by_model"]] == ["openai:gpt-5.5", "qwen3.5:4b"]
    # A time range
    assert client.get("/usage", params={"since": "2026-10-21T00:00:00Z"}).json()["totals"]["replies"] == 1


def test_days_and_hours_follow_the_viewers_time_zone(person):
    user, client = person
    usage.record(f"t-{uuid4()}", "m1", {"model": "x", "at": "2026-10-21T23:30:00Z"}, user_id=user["id"], source="web")
    assert client.get("/usage").json()["by_day"][0]["day"] == "2026-10-21"
    berlin = client.get("/usage", params={"tz": 120}).json()
    assert berlin["by_day"][0]["day"] == "2026-10-22" and berlin["by_hour"][0]["hour"] == 1
    assert client.get("/usage", params={"since": "yesterday"}).status_code == 400


@pytest.fixture
def ollama(monkeypatch):
    """A fake Ollama behind the gateway that answers chats with token counts"""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            body = (b'{"model":"qwen3.5:4b","message":{"content":"hi"},"done":false}\n'
                    b'{"model":"qwen3.5:4b","done":true,"prompt_eval_count":120,"eval_count":30,"eval_duration":1500000000}\n')
            return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})
        if request.url.path == "/read":
            return httpx.Response(200, text="# A page")
        return httpx.Response(500)

    monkeypatch.setattr(gateway, "client", httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(gateway, "pages", httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(gateway, "CRAWLER_URL", "http://crawler:3000")


def test_the_gateway_counts_model_calls_and_labb_versions(person, ollama, client):
    user, labb = person
    labb.headers["X-Labb-Client"] = "0.0.1 (old-laptop)"
    before = client.get("/admin/usage/gateway").json()["totals"]
    for _ in range(2):
        assert labb.post("/ollama/api/chat", json={"model": "qwen3.5:4b", "messages": []}).status_code == 200
    labb.get("/crawler/read", params={"url": "https://example.com"})

    after = client.get("/admin/usage/gateway").json()
    assert after["totals"]["requests"] - before["requests"] == 2
    assert after["totals"]["input_tokens"] - before["input_tokens"] == 240
    assert after["totals"]["pages"] - before["pages"] == 1
    model = next(m for m in after["by_model"] if m["model"] == "qwen3.5:4b")
    assert model["tokens_per_second"] == 20.0

    machines = client.get("/admin/usage/machines").json()
    mine = next(m for m in machines["machines"] if m["username"] == user["username"])
    assert (mine["machine"], mine["version"], mine["requests"], mine["pages"]) == ("old-laptop", "0.0.1", 2, 1)
    assert mine["outdated"] and machines["server_version"] == install.version()


def test_members_only_see_their_own_numbers(person, client, accounts):
    user, member = person
    usage.record(f"t-{uuid4()}", "m1", {"model": "x", "input_tokens": 5}, user_id=user["id"], source="web")
    for path in ["/admin/usage", "/admin/usage/users", "/admin/usage/gateway", "/admin/usage/machines"]:
        assert member.get(path).status_code == 403

    # Admins see a user's numbers, but not their chats' titles
    theirs = client.get("/admin/usage", params={"user_id": user["id"]}).json()
    assert theirs["totals"]["input_tokens"] == 5 and "top_conversations" not in theirs
    assert client.get("/admin/usage", params={"user_id": "nobody"}).status_code == 404
    overview = client.get("/admin/usage").json()
    assert overview["active_users"]["day"] >= 1 and overview["storage"]["usage"] >= 1
    row = next(u for u in client.get("/admin/usage/users").json() if u["id"] == user["id"])
    assert row["input_tokens"] == 5 and row["sources"] == ["web"]


def test_numbers_come_as_csv(person):
    user, client = person
    usage.record(f"t-{uuid4()}", "m1", {"model": "x", "input_tokens": 5}, user_id=user["id"], source="web")
    response = client.get("/usage", headers={"Accept": "text/csv"})
    assert response.headers["content-type"].startswith("text/csv")
    header, row = response.text.splitlines()
    assert header.startswith("created_at,user_id,source") and ",x," in row


def test_labb_says_which_version_it_is(monkeypatch):
    from labb import remote

    monkeypatch.delenv("LABB_API_KEY", raising=False)
    assert remote.headers() == {"X-Labb-Client": remote.client()}
    assert usage.parse_client(remote.client())[0] == install.version()
    assert usage.parse_client("0.1.1 (my laptop)") == ("0.1.1", "my laptop")
