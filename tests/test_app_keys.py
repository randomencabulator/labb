"""Apps that use labb (server/app_keys.py, the /apps/{id}/labb routes): a key per app, opening
/app-api only, and only what the owner turned on."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from labb.server import app_keys, apps

APP = "0123456789ab"
JSON = {"Accept": "application/json"}


@pytest.fixture
def spawner(monkeypatch, accounts):
    """A stand-in spawner: the admin's app, and the env labb gave it"""
    owner = accounts["admin"][0]["id"]
    given = []

    def handle(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        if request.url.params.get("owner") != owner or parts[1] != APP:
            return httpx.Response(404, json={"detail": "No such app"})
        app = {"id": APP, "name": "Board", "owner": owner, "port": 9100, "status": "running", "detail": "", "restarts": 0,
               "created": "2026-11-08", "build": 1, "legacy": False, "upstream": "http://x", "memory_limit": "256m"}
        if parts[2:] == ["labb"]:
            given.append(json.loads(request.content)["env"])
            return httpx.Response(200, json=app)
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json=app)

    monkeypatch.setattr(apps, "SPAWNER_URL", "http://spawner:4100")
    monkeypatch.setattr(apps, "client", httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    # The database is the session's: each test starts with the app having no key
    app_keys.revoke(APP)
    return given


@pytest.fixture
def server():
    from labb.server import app as server
    return server


def with_key(server, key):
    return TestClient(server.app, headers={"Authorization": f"Bearer {key}", **JSON})


def turn_on(client, *scopes):
    return client.put(f"/apps/{APP}/labb", json={"scopes": list(scopes)})


def test_turning_labb_on_gives_the_app_a_key(client, spawner, server):
    assert client.get(f"/apps/{APP}/labb", headers=JSON).json()["key"] is None
    state = turn_on(client, "memories", "ask").json()
    # In the order labb lists them; only the start of the key is shown
    assert state["scopes"] == ["ask", "memories"] and state["key"]["shown"].startswith("lba_")
    assert {s["name"] for s in state["available"]} == set(app_keys.SCOPES)
    env = spawner[-1]
    assert env["LABB_URL"] == app_keys.LABB_URL and env["LABB_API_KEY"].startswith("lba_")
    assert state["key"]["shown"] == env["LABB_API_KEY"][:len(state["key"]["shown"])] != env["LABB_API_KEY"]
    assert with_key(server, env["LABB_API_KEY"]).get("/app-api/me").json() == {"app_id": APP, "scopes": ["ask", "memories"]}


def test_an_apps_key_opens_only_the_app_api_and_only_its_scopes(client, spawner, server):
    turn_on(client, "memories")
    app = with_key(server, spawner[-1]["LABB_API_KEY"])
    assert app.get("/app-api/memories").status_code == 200
    # Not what it wasn't given
    refused = app.post("/app-api/memories", json={"text": "x"})
    assert refused.status_code == 403 and "turn on" in refused.json()["detail"]
    assert app.post("/app-api/documents/search", json={"query": "x"}).status_code == 403
    assert app.post("/app-api/ask", json={"message": "hi"}).status_code == 403
    # Nor anything else of labb's
    for path in ("/apps", "/memories", "/threads", "/keys", "/admin/users", "/documents"):
        assert app.get(path).status_code == 401, path
    # And /app-api needs an app's key: not a session, not a user's key
    assert client.get("/app-api/me").status_code == 401
    assert TestClient(server.app).get("/app-api/me").status_code == 401
    # More, when the owner turns it on, with the same key
    turn_on(client, "memories", "remember")
    assert app.post("/app-api/memories", json={"text": "The app was here"}).status_code == 201
    assert any(m["text"] == "The app was here" for m in client.get("/memories", headers=JSON).json())


def test_a_new_key_or_none_stops_the_old_one(client, spawner, server):
    turn_on(client, "memories")
    old = spawner[-1]["LABB_API_KEY"]
    client.post(f"/apps/{APP}/labb/key")
    new = spawner[-1]["LABB_API_KEY"]
    assert new != old
    assert with_key(server, old).get("/app-api/me").status_code == 401
    assert with_key(server, new).get("/app-api/me").status_code == 200
    # Everything off: its key goes, and the app's variables with it
    assert turn_on(client).json()["key"] is None and spawner[-1] is None
    assert with_key(server, new).get("/app-api/me").status_code == 401
    assert client.post(f"/apps/{APP}/labb/key").status_code == 409


def test_removing_the_app_or_disabling_its_owner_stops_its_key(client, spawner, server, accounts, monkeypatch):
    from labb.server import auth
    turn_on(client, "memories")
    key = spawner[-1]["LABB_API_KEY"]
    real = auth.get_user
    monkeypatch.setattr(auth, "get_user", lambda user_id: {**real(user_id), "disabled": True})
    assert with_key(server, key).get("/app-api/me").status_code == 401
    monkeypatch.setattr(auth, "get_user", real)
    assert with_key(server, key).get("/app-api/me").status_code == 200
    client.delete(f"/apps/{APP}")
    assert with_key(server, key).get("/app-api/me").status_code == 401


def test_someone_elses_app_and_unknown_scopes(client, spawner, server, accounts):
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}", **JSON})
    assert member.get(f"/apps/{APP}/labb").status_code == 404
    assert member.put(f"/apps/{APP}/labb", json={"scopes": ["ask"]}).status_code == 404
    assert turn_on(client, "everything").status_code == 400
    assert spawner == []


@pytest.fixture
def replies(monkeypatch):
    """run_reply and the saved conversation, stood in for: what each ask ran with"""
    runs = []

    def run_reply(thread_id, content, stop, emit, configurable, name, recursion_limit, source):
        runs.append({"thread_id": thread_id, "content": content, "configurable": configurable, "source": source})
        emit({"type": "done", "stats": {"model": "ollama:small", "input_tokens": 10, "output_tokens": 3}})

    class Saved:
        def get_state(self, config):
            return type("State", (), {"values": {"messages": [AIMessage("Hello from the model")]}})()

    monkeypatch.setattr(app_keys, "run_reply", run_reply)
    monkeypatch.setattr(app_keys, "chat_agent", Saved())
    return runs


def test_asking_a_model_runs_as_the_owner_without_tools(client, spawner, server, replies, accounts):
    turn_on(client, "ask")
    app = with_key(server, spawner[-1]["LABB_API_KEY"])
    answer = app.post("/app-api/ask", json={"message": "Say hello"}).json()
    assert answer["text"] == "Hello from the model" and answer["model"] == "ollama:small" and answer["input_tokens"] == 10
    run = replies[-1]
    assert run["source"] == "app" and run["configurable"]["user_id"] == accounts["admin"][0]["id"]
    assert run["configurable"]["tools"].allow == () and run["thread_id"] == f"appk-{APP}-ask-{answer['conversation']}"
    # Going on with it
    app.post("/app-api/ask", json={"message": "Again", "conversation": answer["conversation"]})
    assert replies[-1]["thread_id"] == run["thread_id"]
    assert app.post("/app-api/ask", json={"message": "x", "conversation": "../other"}).status_code == 400


def test_running_an_agent(client, spawner, server, replies):
    agent = client.post("/agents", json={"name": "Helper", "instructions": "Be brief."}).json()
    turn_on(client, "agents")
    app = with_key(server, spawner[-1]["LABB_API_KEY"])
    assert {"id": agent["id"], "name": "Helper", "description": ""} in app.get("/app-api/agents").json()
    assert app.post(f"/app-api/agents/{agent['id']}/ask", json={"message": "Hi"}).json()["text"] == "Hello from the model"
    run = replies[-1]
    assert run["configurable"]["agent"]["id"] == agent["id"] and "tools" not in run["configurable"]
    assert app.post("/app-api/agents/nope/ask", json={"message": "Hi"}).status_code == 404
