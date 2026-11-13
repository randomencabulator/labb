"""The models offered in the pickers: OpenAI's and Anthropic's when their keys are set (asked
of their APIs, chat models only), or the ones MODELS names"""

import httpx
import pytest

from labb import llm


@pytest.fixture
def providers(monkeypatch):
    """Fake OpenAI and Anthropic model lists; returns the requests they got"""

    seen = []

    def get(url, headers=None, params=None, timeout=None):
        seen.append(url)
        request = httpx.Request("GET", url)
        if "openai" in url:
            if headers["Authorization"] != "Bearer sk-good":
                return httpx.Response(401, request=request)
            ids = ["gpt-4.1", "gpt-5.5", "gpt-5.5-2026-01-15", "text-embedding-3-large", "gpt-4o-mini-tts", "o3",
                   "dall-e-3", "gpt-realtime", "whisper-1", "o4-mini", "gpt-5.5-pro"]
            return httpx.Response(200, request=request, json={"data": [{"id": i, "created": n} for n, i in enumerate(ids)]})
        return httpx.Response(200, request=request, json={"data": [{"id": "claude-sonnet-5-5"}, {"id": "claude-haiku-5-5"}]})

    monkeypatch.setattr(llm.httpx, "get", get)
    monkeypatch.setattr(llm, "_provider_cache", {})
    monkeypatch.delenv("MODELS", raising=False)
    return seen


def test_the_keys_bring_their_providers_chat_models(providers, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "providers", llm.env_providers())
    assert llm.provider_models() == [] and providers == []

    monkeypatch.setenv("OPENAI_API_KEY", "sk-good")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setattr(llm, "providers", llm.env_providers())
    assert llm.provider_models() == ["openai:gpt-5.5-pro", "openai:o4-mini", "openai:o3", "openai:gpt-5.5", "openai:gpt-4.1",
                                     "anthropic:claude-sonnet-5-5", "anthropic:claude-haiku-5-5"]
    # Kept: asked once
    llm.provider_models()
    assert len(providers) == 2


def test_a_wrong_key_or_no_network_offers_nothing(providers, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-wrong")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "providers", llm.env_providers())
    assert llm.provider_models() == []


def test_the_server_offers_them_unless_models_names_some(client, providers, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-good")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "ollama_models", lambda: ["qwen3.5:4b"])
    # OpenAI is off until an admin turns it on, key or not
    assert not any(m["spec"].startswith("openai:") for m in client.get("/models").json()["models"])
    client.put("/admin/providers/openai", json={"enabled": True})
    specs = [m["spec"] for m in client.get("/models").json()["models"]]
    # Yours first: the default, then Ollama's, then the cloud's
    assert specs[0] == llm.default.spec and specs.index("ollama:qwen3.5:4b") < specs.index("openai:gpt-5.5")
    assert "openai:gpt-5.5" in specs and "openai:text-embedding-3-large" not in specs

    # Admins can offer only some (MODELS is where a new server starts)
    before = client.get("/admin/models").json()
    client.put("/admin/models", json={"offer": "chosen", "chosen": [{"spec": "openai:gpt-5.5"}, {"spec": "openai:o3"}]})
    specs = [m["spec"] for m in client.get("/models").json()["models"]]
    assert "openai:gpt-5.5" in specs and "openai:o3" in specs and "openai:gpt-4.1" not in specs
    client.put("/admin/models", json={k: before[k] for k in ("default", "offer", "chosen")})
    client.delete("/admin/providers/openai")
    monkeypatch.setenv("MODELS", "openai:gpt-5.5, openai:o3")
    from labb.server import providers as server_providers
    assert server_providers._env_settings()["offer"] == "chosen"


def test_a_refused_provider_key_says_where_it_came_from(monkeypatch):
    from labb import agent
    from labb.cli import render

    openai = "Error code: 401 - {'error': {'message': 'Incorrect API key provided: proxy-ma*aged.', 'code': 'invalid_api_key'}}"
    monkeypatch.setattr(render.remote, "server", lambda: "http://server")
    monkeypatch.setattr(agent, "FROM_ENVIRONMENT", frozenset({"OPENAI_API_KEY"}))
    told = render.explain(openai)
    assert "OpenAI didn't accept OPENAI_API_KEY" in told and "environment of this shell, which wins over" in told
    assert "labb --login" not in told
    monkeypatch.setattr(agent, "FROM_ENVIRONMENT", frozenset())
    assert f"read from {agent.ENV_FILE}." in render.explain(openai)
    anthropic = "Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}}"
    assert "Anthropic didn't accept ANTHROPIC_API_KEY" in render.explain(anthropic)
    # The server refusing this PC's key is still that
    assert "labb --login" in render.explain("Client error '401 Unauthorized' for url 'http://server/ollama/api/chat'")


def test_ollama_cloud_lists_the_cloud_models_that_use_tools(monkeypatch):
    asked = []

    def get(url, headers=None, params=None, timeout=None):
        asked.append(headers)
        return httpx.Response(200, request=httpx.Request("GET", url), json={"models": [
            {"name": "gpt-oss:120b", "capabilities": ["completion", "tools"]},
            {"name": "kimi-k2.6", "capabilities": ["completion", "tools", "vision"]},
            {"name": "no-tools:7b", "capabilities": ["completion"]},
        ]})

    monkeypatch.setattr(llm.httpx, "get", get)
    monkeypatch.setattr(llm, "_provider_cache", {})
    monkeypatch.setattr(llm, "OLLAMA_CLOUD_CATALOG", "https://ollama.example/api/tags")
    cloud = llm.Provider(llm.CLOUD, "ollama", "https://ollama.example", "k")
    monkeypatch.setattr(llm, "providers", {"ollama": llm.Provider("ollama", "ollama", "http://localhost:11434"), llm.CLOUD: cloud})
    assert llm.ollama_cloud_models() == ["ollama-cloud:gpt-oss:120b", "ollama-cloud:kimi-k2.6"]
    # In the pickers' list, and kept for an hour
    assert llm.provider_models() == llm.ollama_cloud_models()
    assert len(asked) == 1 and asked[0] == {"Authorization": "Bearer k"}

    # Turned off, or the list is: none
    monkeypatch.setattr(llm, "providers", {llm.CLOUD: llm.Provider(llm.CLOUD, "ollama", "https://ollama.example", enabled=False)})
    assert llm.ollama_cloud_models() == [] and llm.provider_models() == []
    monkeypatch.setattr(llm, "providers", {llm.CLOUD: cloud})
    monkeypatch.setattr(llm, "_provider_cache", {})
    monkeypatch.setattr(llm, "OLLAMA_CLOUD_CATALOG", "off")
    assert llm.ollama_cloud_models() == []


def test_a_cloud_model_runs_on_ollama_com_with_a_key_or_on_the_signed_in_server(monkeypatch):
    monkeypatch.setattr(llm, "providers", {"ollama": llm.Provider("ollama", "ollama", "http://box:11434"),
                                           llm.CLOUD: llm.Provider(llm.CLOUD, "ollama", "https://ollama.com", "k")})
    assert llm.ollama_target(llm.CLOUD, "gpt-oss:120b") == ("https://ollama.com", "gpt-oss:120b", {"Authorization": "Bearer k"})
    monkeypatch.setitem(llm.providers, llm.CLOUD, llm.Provider(llm.CLOUD, "ollama", "https://ollama.com"))
    base, name, _ = llm.ollama_target(llm.CLOUD, "kimi-k2.6")
    assert (base, name) == ("http://box:11434", "kimi-k2.6:cloud")
    assert llm.is_ollama(llm.CLOUD) and not llm.is_ollama("openai")



class StatusError(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.parametrize("error, spec, says", [
    (StatusError("Add usage credits or subscribe and save up to 66%: https://ollama.com/plans", 402),
     "ollama:gpt-oss:120b-cloud", "out of credits"),
    (StatusError("Error code: 429 - {'error': {'code': 'insufficient_quota'}}", 429), "openai:gpt-5.5", "out of credits"),
    (StatusError("Error code: 401 - invalid x-api-key", 401), "anthropic:claude-sonnet-5-5", "refused the API key"),
    (StatusError("Error code: 429 - rate limit reached", 429), "openai:gpt-5.5", "rate-limited"),
    (StatusError("model 'qwen9:1b' not found", 404), "ollama:qwen9:1b", "ollama pull qwen9:1b"),
    (ConnectionError("[Errno 111] Connection refused"), "ollama:qwen3.5:4b", "Couldn't reach ollama"),
])
def test_usual_model_errors_say_what_to_do(error, spec, says):
    explained = llm.explain_error(error, spec)
    assert says in explained and str(error)[:40] in explained


def test_other_errors_are_left_as_they_are():
    assert llm.explain_error(ValueError("the graph broke"), "ollama:qwen3.5:4b") == "the graph broke"


def test_speaches_is_a_provider_when_compose_runs_it_and_its_speech_models_arent_chat_models(monkeypatch):
    monkeypatch.setattr(llm, "SPEACHES_URL", "")
    assert "speaches" not in llm.env_providers()
    monkeypatch.setattr(llm, "SPEACHES_URL", "http://speaches:8000/v1")
    speaches = llm.env_providers()["speaches"]
    assert speaches.kind == "openai_compatible" and speaches.base_url == "http://speaches:8000/v1"

    def get(url, headers=None, timeout=None):
        ids = ["Systran/faster-whisper-small", "speaches-ai/Kokoro-82M-v1.0-ONNX", "speaches-ai/piper-en_US-amy-low", "qwen3-8b"]
        return httpx.Response(200, request=httpx.Request("GET", url), json={"data": [{"id": i} for i in ids]})

    monkeypatch.setattr(llm.httpx, "get", get)
    assert llm.models_of(speaches) == ["speaches:qwen3-8b"]


def test_ollama_gets_only_content_it_takes():
    """A chat can hold what Ollama's adapter refuses: another provider's thinking (the model was
    changed), a tool's resource, a file. They go, or become a note, instead of failing the call."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    messages = [
        HumanMessage([{"type": "text", "text": "hi"}, {"type": "file", "mime_type": "application/pdf", "base64": "eA=="},
                      {"type": "image", "base64": "aW1n", "mime_type": "image/png"}]),
        AIMessage([{"type": "thinking", "thinking": "hmm", "signature": "s"}, {"type": "text", "text": "hello"},
                   {"type": "tool_use", "id": "1", "name": "x", "input": {}}]),
        AIMessage([{"type": "reasoning", "summary": []}, {"type": "text", "text": "ok", "annotations": [], "id": "msg_1"}]),
        ToolMessage([{"type": "resource", "resource": {"uri": "file:///x"}}, {"type": "text", "text": "result"}], tool_call_id="1"),
        HumanMessage("plain"),
    ]
    sent = llm.OllamaChat(model="m")._convert_messages_to_ollama_messages(messages)
    assert [m["content"] for m in sent] == [
        "\nhi\n[application/pdf left out: this model can't read it]", "\nhello", "\nok",
        "\n[resource left out: this model can't read it]\nresult", "plain"]
    assert sent[0]["images"] == ["aW1n"]
    # The chat itself keeps them
    assert messages[1].content[0]["type"] == "thinking"


def test_openai_models_use_responses_and_other_servers_chat_completions(monkeypatch):
    """Newer OpenAI models take tools with a reasoning effort only through the Responses API"""
    from langchain_core.messages import HumanMessage

    monkeypatch.setattr(llm, "providers", {"openai": llm.Provider("openai", "openai", None, "sk-test"),
                                           "lm": llm.Provider("lm", "openai_compatible", "http://localhost:1234/v1")})
    llm.load.cache_clear()
    try:
        models = llm.load("openai:gpt-5.6-terra")
        tools = [{"type": "function", "function": {"name": "search", "description": "Search",
                                                   "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}]
        payload = models.model._get_request_payload([HumanMessage("hi")], tools=tools, reasoning_effort="high")
        assert models.model.use_responses_api and payload["reasoning"] == {"effort": "high"} and "input" in payload
        assert not llm.load("lm:qwen").model.use_responses_api
    finally:
        llm.load.cache_clear()
