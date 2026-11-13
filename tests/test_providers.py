"""Settings → Models: providers (built-in and custom, keys encrypted and never shown, .env under
what's saved), the default model, and which models people can use."""

from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from labb import llm
from labb.server import agents, auth, providers
from labb.server import app as server


@pytest.fixture(autouse=True)
def clean(client):
    """Each test starts from .env's providers and settings, and leaves them that way"""

    def reset():
        from labb.chats import conn, lock

        with lock, conn:
            conn.execute("DELETE FROM providers")
            conn.execute("DELETE FROM server_settings")
        providers._settings = None
        providers.apply()

    reset()
    yield
    reset()


@pytest.fixture
def member(accounts):
    user = auth.create_user(f"u{uuid4().hex[:10]}", "a-password")
    key, _ = auth.create_key(user["id"], "laptop")
    return user, TestClient(server.app, headers={"Authorization": f"Bearer {key}"})


def test_keys_are_kept_secret_and_dot_env_is_the_fallback(client, monkeypatch):
    listed = client.get("/admin/providers").json()
    assert [p["name"] for p in listed][:4] == ["ollama", "ollama-cloud", "openai", "anthropic"]
    # The cloud ones start turned off, keys in .env or not
    assert [p["enabled"] for p in listed][:4] == [True, False, False, False]
    assert not llm.providers["openai"].enabled and not llm.providers["ollama-cloud"].enabled

    saved = client.put("/admin/providers/openai", json={"api_key": "sk-from-the-ui", "base_url": "https://example.test/v1/"}).json()
    openai = next(p for p in saved if p["name"] == "openai")
    assert openai["key_set"] and openai["key_from"] == "saved" and openai["base_url"] == "https://example.test/v1"
    assert "sk-from-the-ui" not in str(saved)
    assert llm.providers["openai"].api_key == "sk-from-the-ui"
    # Saving its key doesn't turn it on; turning it on does
    assert not openai["enabled"]
    client.put("/admin/providers/openai", json={"enabled": True})
    assert llm.providers["openai"].enabled
    from labb.chats import conn, lock

    with lock:
        stored = conn.execute("SELECT api_key FROM providers WHERE name = 'openai'").fetchone()[0]
    assert stored and "sk-from-the-ui" not in stored

    assert client.delete("/admin/providers/openai").status_code == 204
    back = next(p for p in client.get("/admin/providers").json() if p["name"] == "openai")
    assert back["base_url_from"] in (".env", "default") and back["base_url"] == llm.env_providers()["openai"].base_url


def test_a_custom_provider_is_tested_saved_and_used(client, monkeypatch):
    seen = []

    def get(url, headers=None, params=None, timeout=None):
        seen.append((url, headers))
        return httpx.Response(200, request=httpx.Request("GET", url), json={"data": [{"id": "qwen2.5-7b"}, {"id": "llama-3.1-8b"}]})

    monkeypatch.setattr(llm.httpx, "get", get)
    assert client.put("/admin/providers/lmstudio", json={}).status_code == 400  # it needs an address
    assert client.put("/admin/providers/Bad Name", json={"base_url": "http://x"}).status_code == 400
    tried = client.post("/admin/providers/lmstudio/test", json={"base_url": "http://192.168.1.20:1234/v1"}).json()
    assert tried == {"ok": True, "models": ["lmstudio:qwen2.5-7b", "lmstudio:llama-3.1-8b"], "count": 2}
    assert seen[-1][0] == "http://192.168.1.20:1234/v1/models"

    client.put("/admin/providers/lmstudio", json={"base_url": "http://192.168.1.20:1234/v1"})
    model = llm.load("lmstudio:qwen2.5-7b").model
    assert type(model).__name__ == "ChatOpenAI" and str(model.openai_api_base) == "http://192.168.1.20:1234/v1"
    assert "lmstudio:qwen2.5-7b" in llm.provider_models()
    specs = [m["spec"] for m in client.get("/models").json()["models"]]
    assert "lmstudio:qwen2.5-7b" in specs

    client.put("/admin/providers/lmstudio", json={"enabled": False})
    with pytest.raises(ValueError, match="turned off"):
        llm.load("lmstudio:qwen2.5-7b")


def test_the_default_model_changes_without_a_restart(client):
    before = llm.default.spec
    changed = client.put("/admin/models", json={"default": "ollama:another-model"}).json()
    assert changed["current_default"] == "ollama:another-model" == llm.default.spec
    assert client.get("/models").json()["default"] == "ollama:another-model"
    assert client.put("/admin/models", json={"default": "nonsense"}).status_code == 400
    client.put("/admin/models", json={"default": None})
    assert llm.default.spec == before


def test_the_context_window_is_set_in_the_settings_over_the_env(client):
    # The tests' .env value (conftest) until one is saved
    assert client.get("/admin/models").json()["env_window"] == 32768 == llm.default.context_window
    changed = client.put("/admin/models", json={"context_window": 8000}).json()
    assert changed["context_window"] == 8000 == changed["current_window"] == llm.default.context_window
    assert llm.load("ollama:another-model").context_window == 8000
    for bad in (10, "lots", 10**9):
        assert client.put("/admin/models", json={"context_window": bad}).status_code in (400, 422)
    assert client.put("/admin/models", json={"context_window": None}).json()["current_window"] == 32768


def test_ollamas_num_ctx_is_set_in_the_settings_over_the_env(client):
    settings = client.get("/admin/models").json()
    assert settings["ollama_num_ctx"] is None and settings["current_num_ctx"] == llm.ENV_NUM_CTX == settings["env_num_ctx"]
    changed = client.put("/admin/models", json={"ollama_num_ctx": 65536}).json()
    assert changed["ollama_num_ctx"] == 65536 == changed["current_num_ctx"] == llm.ollama_num_ctx
    assert llm.load("ollama:another-model").model.num_ctx == 65536
    for bad in (10, "lots", 10**9):
        assert client.put("/admin/models", json={"ollama_num_ctx": bad}).status_code in (400, 422)
    assert client.put("/admin/models", json={"ollama_num_ctx": None}).json()["current_num_ctx"] == llm.ENV_NUM_CTX


def test_admins_only_models_and_the_chosen_list(client, member, fake_model):
    user, member_client = member
    client.put("/admin/models", json={"offer": "chosen", "chosen": [
        {"spec": "ollama:small", "label": "Small and quick"},
        {"spec": "ollama:huge", "label": "Huge", "admins_only": True}]})
    mine = {m["spec"]: m["name"] for m in member_client.get("/models").json()["models"]}
    assert "ollama:small" in mine and mine["ollama:small"] == "Small and quick" and "ollama:huge" not in mine
    assert "ollama:huge" in {m["spec"] for m in client.get("/models").json()["models"]}

    assert providers.why_not("ollama:huge", "member") == "ollama:huge is for admins only"
    assert "isn't one of the models" in providers.why_not("ollama:other", "member")
    assert providers.why_not("ollama:other", "admin") is None
    with pytest.raises(agents.AgentError, match="admins only"):
        agents.create(user["id"], {"name": "Big", "model": "ollama:huge"})

    fake_model()
    refused = member_client.post("/chat", json={"message": "hi", "model": "ollama:huge"})
    assert refused.status_code == 403 and "admins only" in refused.json()["detail"]
    # Through a run that asks for it all the same (a task, a Studio step), llm refuses it too
    with pytest.raises(ValueError, match="admins only"):
        llm.for_config({"configurable": {"model": "ollama:huge", "user_id": user["id"]}})

    client.put("/admin/models", json={"offer": "all", "chosen": []})
    assert providers.why_not("ollama:other", "member") is None
