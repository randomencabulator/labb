"""Settings → Health: each part of the server checked, said so an admin can act on it"""

import httpx
from fastapi.testclient import TestClient

from labb import llm
from labb.server import health


def fake_get(down: set[str]):
    def get(url, headers=None, timeout=None):
        request = httpx.Request("GET", url)
        if any(part in url for part in down):
            raise httpx.ConnectError("Connection refused", request=request)
        body = {"version": "0.40.4"} if url.endswith("/api/version") else {"ok": True, "isolated": True}
        return httpx.Response(200, request=request, json=body)
    return get


def test_admins_see_each_part_and_what_to_do(client, accounts, monkeypatch):
    from labb.server import app as server
    monkeypatch.setattr(health.httpx, "get", fake_get(down={"crawler"}))
    monkeypatch.setattr(llm, "ollama_models", lambda: ["qwen3.5:4b"])
    monkeypatch.setattr(health, "CRAWLER_URL", "http://crawler:3000")
    monkeypatch.setattr(health.sandbox, "SANDBOX_URL", "http://sandbox:4000")
    monkeypatch.setattr(health.sandbox, "SANDBOX_TOKEN", "t")
    checks = {c["name"]: c for c in client.get("/admin/health").json()["checks"]}

    assert checks["Ollama"]["ok"] is True and "0.40.4" in checks["Ollama"]["detail"]
    # The embedding model isn't pulled: say how to pull it
    assert checks["Embedding model"]["ok"] is False and "ollama pull" in checks["Embedding model"]["detail"]
    assert checks["Crawler"]["ok"] is False and "Can't reach" in checks["Crawler"]["detail"]
    assert checks["Sandbox"]["ok"] is True
    assert checks["Database"]["ok"] is True and "MB" in checks["Database"]["detail"]
    assert {"Search index", "PCs", "Default model"} <= set(checks)

    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert member.get("/admin/health").status_code == 403


def test_ollama_down_and_a_check_that_breaks(monkeypatch):
    monkeypatch.setattr(health.httpx, "get", fake_get(down={"/api/version"}))
    assert health.ollama()[0]["ok"] is False and "OLLAMA_HOST" in health.ollama()[0]["detail"]

    def broken():
        raise RuntimeError("boom")
    monkeypatch.setattr(health, "CHECKS", [broken])
    assert health.report() == [{"name": "Broken", "ok": False, "detail": "The check failed: boom"}]
