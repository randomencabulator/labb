"""Settings → Health's resources: the machine, the server, the database, Ollama's loaded models and
the containers (from the spawner), each read on its own so one that's down doesn't hide the rest."""

import httpx

from labb.server import apps, system


def test_admins_see_what_everything_uses(client, accounts, monkeypatch):
    from fastapi.testclient import TestClient
    from labb.server import app as server
    monkeypatch.setattr(system, "SAMPLE_SECONDS", 0)
    monkeypatch.setattr(apps, "SPAWNER_URL", "http://spawner")
    real_get = httpx.get

    def get(url, **kwargs):
        if url.endswith("/api/ps"):
            return httpx.Response(200, request=httpx.Request("GET", url), json={"models": [
                {"name": "gemma4:e4b", "size": 9_000_000_000, "size_vram": 9_000_000_000, "expires_at": "2026-11-12T10:05:00Z"}]})
        if url == "http://spawner/system":
            return httpx.Response(200, request=httpx.Request("GET", url), json={"services": [{"name": "labb-web", "cpu": 3.5}],
                                                                                  "apps": {"running": 0, "top": []}})
        return real_get(url, **kwargs)

    monkeypatch.setattr(system.httpx, "get", get)
    found = client.get("/admin/system").json()
    assert found["problems"] == []
    assert found["ollama"]["models"][0] == {"name": "gemma4:e4b", "memory": 9_000_000_000, "gpu_memory": 9_000_000_000,
                                            "until": "2026-11-12T10:05:00Z", "context": None}
    assert found["containers"]["services"][0]["name"] == "labb-web"
    assert found["server"]["up_seconds"] >= 0 and found["server"]["threads"] >= 1
    assert found["database"]["kind"] in ("SQLite", "Postgres") and found["database"]["bytes"] > 0
    # On Linux, the machine from /proc
    assert found["host"]["processors"] >= 1 and found["host"]["memory_total"] >= found["host"]["memory_used"] > 0
    assert 0 <= found["host"].get("cpu", 0) <= 100 and found["host"]["disk_total"] > 0

    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert member.get("/admin/system").status_code == 403


def test_a_part_that_cant_be_read_says_so_and_the_rest_are_there(client, monkeypatch):
    monkeypatch.setattr(system, "SAMPLE_SECONDS", 0)
    monkeypatch.setattr(apps, "SPAWNER_URL", "")

    def down(url, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(system.httpx, "get", down)
    found = client.get("/admin/system").json()
    assert found["ollama"] is None and found["containers"] is None
    assert len(found["problems"]) == 1 and found["problems"][0].startswith("Couldn't read Ollama: refused")
    assert found["host"]["processors"] >= 1
