"""Settings → Services: the server's own switches, and the crawler's, browser's and sandbox's
shown and saved through it (each service faked), each service's keys named with it."""

import json

import httpx

from labb.server import services
from labb.server.studio import sandbox
from labb.tools import browser, web


class Answer:
    def __init__(self, status, body):
        self.status_code, self.body, self.text = status, body, json.dumps(body)

    def json(self):
        return self.body


def test_the_servers_own_switch_reads_private_pages_without_the_crawler(client, accounts, monkeypatch):
    from fastapi.testclient import TestClient
    from labb.server import app as server
    for name in ("CRAWLER_URL",):
        monkeypatch.setattr(web, name, None)
    monkeypatch.setattr(browser, "BROWSER_URL", None)
    monkeypatch.setattr(sandbox, "SANDBOX_URL", "")
    monkeypatch.setattr(services, "_saved", None)
    monkeypatch.setattr(web, "READ_PRIVATE", False)
    shown = {s["key"]: s for s in client.get("/admin/services").json()}
    assert shown["read_private"]["value"] is False and shown["read_private"]["kind"] == "bool"
    try:
        assert {s["key"]: s["value"] for s in client.put("/admin/services", json={"read_private": True}).json()}["read_private"] is True
        assert web.READ_PRIVATE is True
        # Kept, and put in place again at start
        monkeypatch.setattr(services, "_saved", None)
        web.READ_PRIVATE = False
        services.apply()
        assert web.READ_PRIVATE is True
        assert client.put("/admin/services", json={"read_private": 3}).status_code == 400
        assert client.put("/admin/services", json={"nope": True}).status_code == 400
    finally:
        client.put("/admin/services", json={"read_private": None})
    assert web.READ_PRIVATE is False
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert member.get("/admin/services").status_code == 403


def test_the_services_settings_are_shown_and_saved_through_the_server(client, monkeypatch):
    monkeypatch.setattr(web, "CRAWLER_URL", "http://crawler")
    monkeypatch.setattr(browser, "BROWSER_URL", "http://browser")
    monkeypatch.setattr(sandbox, "SANDBOX_URL", "")
    monkeypatch.setattr(services, "_saved", None)
    state = {"crawler": False, "browser": False}
    sent = []

    def request(method, url, headers=None, json=None, timeout=None):
        name = url.split("//")[1].split("/")[0]
        if method == "PUT":
            sent.append((name, json))
            if json.get("allow_private") == "no":
                return Answer(400, {"detail": "Crawler: Read private addresses is on or off"})
            state[name] = json["allow_private"]
        return Answer(200, [{"key": "allow_private", "group": name.title(), "label": "Private addresses", "description": "",
                             "kind": "bool", "min": 0, "max": 0, "unit": "", "default": False, "value": state[name]}])

    monkeypatch.setattr(services.httpx, "request", request)
    shown = {s["key"]: s for s in client.get("/admin/services").json()}
    assert {"crawler.allow_private", "browser.allow_private", "read_private"} <= set(shown)
    changed = {s["key"]: s["value"] for s in client.put("/admin/services", json={"crawler.allow_private": True}).json()}
    # Only the crawler's changed, sent under its own name
    assert sent == [("crawler", {"allow_private": True})]
    assert changed["crawler.allow_private"] is True and changed["browser.allow_private"] is False

    def down(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(services.httpx, "request", down)
    page = client.get("/admin/services", params={"problems": True}).json()
    assert page["problems"] == ["Couldn't reach the crawler: refused, so its settings aren't here",
                                "Couldn't reach the browser: refused, so its settings aren't here"]
    assert [s["key"] for s in page["settings"]] == ["read_private"]
