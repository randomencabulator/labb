"""The Artifacts page: your web pages listed with their links and chats, looked through, and
deleted with their published site. The spawner and labb-sites are faked where the server calls them."""

import json
from urllib.parse import unquote

import pytest

from labb import chats
from labb.server import app_tools, artifact_tools


class Response:
    def __init__(self, body=None):
        self.body = body
        self.content = json.dumps(body).encode() if body is not None else b""

    def json(self):
        return self.body


@pytest.fixture
def spawner(monkeypatch, accounts):
    admin = accounts["admin"][0]["id"]
    made = {
        "sales-chart-k3f9qa": {"name": "sales-chart-k3f9qa", "owner": admin, "thread": "t-sales", "title": "Sales chart",
                               "created": "2026-11-12T09:00:00+00:00", "site": True,
                               "build": {"ok": True, "at": "2026-11-12T09:01:00+00:00", "error": None}},
        "draft-x1y2z3": {"name": "draft-x1y2z3", "owner": admin, "thread": "t-gone", "title": "Draft", "created": "2026-11-11T09:00:00+00:00",
                         "site": False, "build": {"ok": False, "at": "2026-11-11T09:01:00+00:00", "error": "Type error in app/page.tsx"}},
    }
    calls = []

    def call(method, path, user_id, timeout=60, **kwargs):
        calls.append((method, path))
        mine = {n: a for n, a in made.items() if a["owner"] == user_id}
        if path == "/artifacts":
            return Response(list(mine.values()))
        name = path.split("/")[2]
        if name not in mine:
            raise ValueError(f"No artifact {name!r}")
        if method == "DELETE" and "/files/" in path:
            return Response()
        if method == "DELETE":
            del made[name]
            return Response()
        if method == "PUT":
            return Response({"path": unquote(path.split("/files/")[1]), "size": len(kwargs["json"]["content"]), "created": False})
        if path.endswith("/search"):
            return Response({"matches": [{"path": "app/page.tsx", "line": 3, "column": 0, "text": "Sales", "ranges": [[0, 5]]},
                                         {"path": "examples/dashboard.tsx", "line": 9, "column": 0, "text": "Sales", "ranges": [[0, 5]]}],
                             "files": ["examples/sales.tsx"], "more": False})
        if path.endswith("/build"):
            mine[name]["build"] = {"ok": True, "at": "2026-11-13T10:00:00+00:00", "error": None}
            return Response({"ok": True, "error": None})
        if path.endswith("/site"):
            return Response({"tar": "gz"})
        if path.endswith("/files"):
            return Response([{"path": "app/page.tsx", "size": 120}, {"path": "examples/dashboard.tsx", "size": 900}])
        if "/files/" in path:
            return Response({"content": f"// {unquote(path.split('/files/')[1])}"})
        return Response(mine[name])

    sites = []
    monkeypatch.setattr(artifact_tools, "enabled", lambda: True)
    monkeypatch.setattr(app_tools, "call", call)
    monkeypatch.setattr(artifact_tools, "sites", lambda method, path, **kwargs: sites.append((method, path)))
    monkeypatch.setattr(artifact_tools, "SITES_PUBLIC_URL", "https://pages.example.com")
    chats.touch("t-sales", "Chart my sales", admin)
    return {"made": made, "calls": calls, "sites": sites}


def test_your_pages_are_listed_with_their_links_and_chats(client, spawner):
    found = client.get("/artifacts").json()
    assert found["available"]
    listed = {a["name"]: a for a in found["artifacts"]}
    assert listed["sales-chart-k3f9qa"]["url"] == "https://pages.example.com/artifacts/sales-chart-k3f9qa/"
    assert listed["sales-chart-k3f9qa"]["chat"] == "Chart my sales"
    # A failed build and no site yet: no link; its chat was deleted
    assert listed["draft-x1y2z3"]["url"] is None and listed["draft-x1y2z3"]["chat"] is None
    assert listed["draft-x1y2z3"]["build"]["error"] == "Type error in app/page.tsx"


def test_one_is_looked_through_without_the_templates_examples(client, spawner):
    found = client.get("/artifacts/sales-chart-k3f9qa").json()
    assert found["files"] == [{"path": "app/page.tsx", "size": 120}] and found["examples"] == 1
    assert client.get("/artifacts/sales-chart-k3f9qa/files/app/page.tsx").json() == {"content": "// app/page.tsx"}


def test_its_files_are_changed_searched_and_published_from_the_page(client, spawner):
    written = client.put("/artifacts/sales-chart-k3f9qa/files/app/page.tsx", json={"content": "// changed"})
    assert written.status_code == 200 and written.json()["size"] == len("// changed")
    assert ("PUT", "/artifacts/sales-chart-k3f9qa/files/app/page.tsx") in spawner["calls"]
    assert client.delete("/artifacts/sales-chart-k3f9qa/files/data/old.ts").status_code == 204
    assert "sales-chart-k3f9qa" in spawner["made"]
    # The search leaves out the template's examples, as the file list does
    found = client.get("/artifacts/sales-chart-k3f9qa/search", params={"q": "sales"}).json()
    assert [m["path"] for m in found["matches"]] == ["app/page.tsx"] and found["files"] == []
    published = client.post("/artifacts/sales-chart-k3f9qa/publish").json()
    assert published == {"ok": True, "message": "Published"}
    assert spawner["sites"] == [("PUT", "/artifacts/sales-chart-k3f9qa")]


def test_a_publish_that_doesnt_build_says_why(client, spawner, monkeypatch):
    real = app_tools.call

    def failing(method, path, user_id, timeout=60, **kwargs):
        if path.endswith("/build"):
            return Response({"ok": False, "error": "Type error: nope"})
        return real(method, path, user_id, timeout, **kwargs)

    monkeypatch.setattr(app_tools, "call", failing)
    published = client.post("/artifacts/sales-chart-k3f9qa/publish").json()
    assert not published["ok"] and "Type error: nope" in published["message"] and spawner["sites"] == []


def test_someone_else_cant_change_or_publish_it(accounts, spawner):
    from fastapi.testclient import TestClient
    from labb.server import app as server
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert member.put("/artifacts/sales-chart-k3f9qa/files/app/page.tsx", json={"content": "x"}).status_code == 404
    assert member.post("/artifacts/sales-chart-k3f9qa/publish").status_code == 502
    assert spawner["sites"] == []


def test_deleting_one_removes_its_files_and_its_site(client, spawner):
    assert client.delete("/artifacts/sales-chart-k3f9qa").status_code == 204
    assert "sales-chart-k3f9qa" not in spawner["made"]
    assert spawner["sites"] == [("DELETE", "/artifacts/sales-chart-k3f9qa")]


def test_someone_elses_or_a_made_up_name_isnt_found(client, accounts, spawner):
    from fastapi.testclient import TestClient
    from labb.server import app as server
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert member.get("/artifacts").json()["artifacts"] == []
    assert member.get("/artifacts/sales-chart-k3f9qa").status_code == 404
    assert member.delete("/artifacts/sales-chart-k3f9qa").status_code == 404 and "sales-chart-k3f9qa" in spawner["made"]
    assert client.get("/artifacts/..%2Fetc").status_code == 404


def test_a_browser_loading_the_address_gets_the_page(client, spawner):
    from labb.server import app as server
    if not (server.WEB_DIST / "index.html").is_file():
        pytest.skip("the web app isn't built")
    page = client.get("/artifacts/sales-chart-k3f9qa", headers={"Sec-Fetch-Mode": "navigate", "Accept": "text/html"})
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    # The app fetching it gets JSON
    assert client.get("/artifacts/sales-chart-k3f9qa").json()["name"] == "sales-chart-k3f9qa"


def test_without_the_spawner_the_page_says_so(client, monkeypatch):
    monkeypatch.setattr(artifact_tools, "enabled", lambda: False)
    assert client.get("/artifacts").json() == {"available": False, "artifacts": []}
