"""labb's Settings → Services changes the browser service's settings, at once"""

from fastapi.testclient import TestClient

from labb_browser import app as service
from labb_browser import driver, settings


def test_private_addresses_and_the_rest_change_at_once(monkeypatch, tmp_path):
    monkeypatch.setenv("BROWSER_DATA", str(tmp_path))
    monkeypatch.setattr(service, "TOKEN", "t")
    monkeypatch.setattr(settings, "_defaults", {"allow_private": False, "idle_minutes": 30.0, "profiles": 4, "snapshot_chars": 10_000})
    monkeypatch.setattr(service, "ALLOW_PRIVATE", False)
    monkeypatch.setattr(service.driver, "allow_private", False)
    monkeypatch.setattr(driver, "IDLE_SECONDS", 1800.0)
    client, headers = TestClient(service.app), {"Authorization": "Bearer t"}
    assert client.get("/settings").status_code == 401

    shown = {s["key"]: s for s in client.put("/settings", json={"allow_private": True, "idle_minutes": 5}, headers=headers).json()}
    assert shown["allow_private"]["value"] is True and shown["idle_minutes"]["value"] == 5
    assert service.driver.allow_private is True and driver.IDLE_SECONDS == 300
    assert (tmp_path / "settings.json").exists()
    client.put("/settings", json={"allow_private": None, "idle_minutes": None}, headers=headers)
    assert service.driver.allow_private is False and driver.IDLE_SECONDS == 1800
