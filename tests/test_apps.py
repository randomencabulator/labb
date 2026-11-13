"""Apps (server/apps.py, apps_routes.py): the server is the way to labb-spawner, says whose app it is,
and where to open one: its own port on labb's host."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from labb.server import apps

ID = "0123456789ab"
PORT = 9100
USER = "0c656493-6ca9-4658-bf18-6da62bc91ab5"
JSON = {"Accept": "application/json"}


@pytest.fixture
def spawner(monkeypatch):
    """A stand-in for labb-spawner: what it was asked, apps kept by owner, and where apps were checked"""
    calls, kept, probed, files = [], {}, [], {"Dockerfile": "FROM busybox"}
    settings = {"variables": [], "data": {"path": "/data", "size": 0, "limit": 1024}}

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        owner = request.url.params.get("owner")
        parts = request.url.path.strip("/").split("/")
        if parts == ["starters"]:
            return httpx.Response(200, json=[{"name": "react", "title": "React app", "description": "A React page"}])
        if request.method == "POST" and len(parts) == 1:
            body = json.loads(request.content)
            app = {"id": ID, "name": body["name"], "owner": body["owner"], "port": PORT, "status": "running",
                   "detail": "", "restarts": 0, "created": "2026-11-08T12:00:00+00:00", "build": 1, "legacy": False,
                   "starter": body.get("starter"),
                   "upstream": f"http://labb-app-{ID}:8080", "memory_limit": "256m"}
            kept[ID] = app
            return httpx.Response(201, json=app)
        if len(parts) == 1:
            return httpx.Response(200, json=[a for a in kept.values() if a["owner"] == owner])
        app = kept.get(parts[1])
        if not app or app["owner"] != owner:
            return httpx.Response(404, json={"detail": "No such app"})
        if request.method == "DELETE" and len(parts) == 2:
            del kept[parts[1]]
            return httpx.Response(204)
        if len(parts) >= 3 and parts[2] == "files":
            path = "/".join(parts[3:])
            if request.method == "PUT":
                files[path] = json.loads(request.content)["content"]
                return httpx.Response(200, json={"path": path, "size": len(files[path])})
            if request.method == "DELETE":
                files.pop(path)
                return httpx.Response(204)
            if path:
                return httpx.Response(200, json={"path": path, "content": files[path]})
            return httpx.Response(200, json=[{"path": p, "size": len(c)} for p, c in sorted(files.items())])
        if len(parts) == 3 and parts[2] == "search":
            q = request.url.params["q"]
            return httpx.Response(200, json={"matches": [{"path": p, "line": 1, "column": c.index(q), "text": c, "ranges": [[c.index(q), c.index(q) + len(q)]]}
                                                         for p, c in sorted(files.items()) if q in c], "files": [], "more": False})
        if len(parts) == 3 and parts[2] == "settings":
            if request.method == "PUT":
                settings["variables"] = [{**v, "value": None if v["secret"] else v["value"]} for v in json.loads(request.content)["variables"]]
                return httpx.Response(200, json={"app": app, **settings})
            return httpx.Response(200, json=settings)
        if len(parts) == 4 and parts[2:] == ["source", "download"]:
            return httpx.Response(200, stream=httpx.ByteStream(b"zip bytes"), headers={"content-type": "application/zip"})
        if len(parts) == 4 and parts[2:] == ["data", "download"]:
            return httpx.Response(200, stream=httpx.ByteStream(b"tar bytes"), headers={"content-type": "application/x-tar"})
        if len(parts) == 3 and parts[2] == "api":
            on = request.method in ("POST", "PATCH")
            signups = json.loads(request.content)["signups"] if request.method == "PATCH" else True
            return httpx.Response(200, json={"enabled": True, "api": {"enabled": on, "port": PORT + 100, "status": "running" if on else None,
                                                                        "signups": signups if on else None}})
        if len(parts) >= 3 and parts[2] == "users":
            if len(parts) == 3:
                return httpx.Response(200, json={"users": [{"id": USER, "email": "ann@example.com"}], "total": 1,
                                                 "offset": int(request.url.params.get("offset", 0))})
            return httpx.Response(204)
        if len(parts) >= 3 and parts[2] == "database":
            if parts[3:] == ["dump"]:
                return httpx.Response(200, stream=httpx.ByteStream(b"-- dump"), headers={"content-type": "application/sql"})
            if parts[3:] == ["rows"]:
                return httpx.Response(200, json={"columns": ["id"], "rows": [[1]], "more": False, "offset": 0,
                                                 "asked": dict(request.url.params)})
            if parts[3:] == ["query"]:
                return httpx.Response(200, json={"columns": [], "rows": [], "rowcount": 0, "truncated": False,
                                                 "written": json.loads(request.content)["write"]})
            if request.method == "POST":
                app["database"] = True
                return httpx.Response(200, json={"enabled": True, "tables": [], "migrations": [], "pending": [], "app": app,
                                                 "migrated": {"ok": True, "log": "", "error": None}})
            if request.method == "DELETE":
                app["database"] = False
                return httpx.Response(200, json={"app": app})
            return httpx.Response(200, json={"available": True, "enabled": app.get("database", False)})
        if len(parts) >= 3 and parts[2] == "versions":
            if request.method == "POST":
                return httpx.Response(200, json={"version": "d" * 40, "versions": [], "unsaved": []})
            if len(parts) == 3:
                return httpx.Response(200, json={"versions": [{"id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "message": "Made", "at": "x", "files": []}], "unsaved": []})
            return httpx.Response(200, json={"diff": "+x"})
        if len(parts) == 3 and parts[2] == "build":
            if request.method == "GET":
                return httpx.Response(200, json={"build": app["build"], "ok": True, "log": "built", "building": False})
            app["build"] += 1
            return httpx.Response(200, json={"ok": True, "error": None, "log": "Successfully built", "app": app})
        if request.method == "POST":
            app.update({"stop": {"status": "exited", "detail": "Stopped"}}.get(parts[2], {"status": "running", "detail": ""}))
            return httpx.Response(200, json=app)
        if len(parts) == 3 and parts[2] == "logs":
            return httpx.Response(200, json={"lines": f"last {request.url.params['tail']} lines"})
        return httpx.Response(200, json={**app, "memory_used": 30_000_000} if app["status"] == "running" else app)

    async def answers(upstream, seconds=15):
        probed.append(upstream)
        return True

    monkeypatch.setattr(apps, "SPAWNER_URL", "http://spawner:4100")
    monkeypatch.setattr(apps, "SPAWNER_TOKEN", "secret")
    monkeypatch.setattr(apps, "client", httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(apps, "answers", answers)
    return {"calls": calls, "kept": kept, "probed": probed}


def test_your_apps(client, spawner, accounts):
    made = client.post("/apps", json={"name": "Todo", "owner": "someone-else"})
    assert made.status_code == 201
    app = made.json()
    # Where it runs inside isn't the page's business; its port and state are
    assert app == {"id": ID, "name": "Todo", "port": PORT, "status": "running", "detail": "", "restarts": 0,
                   "created": "2026-11-08T12:00:00+00:00", "build": 1, "legacy": False, "memory_limit": "256m"}
    # The owner is the signed-in user, whatever was sent; the spawner gets the server's token
    first = spawner["calls"][0]
    assert json.loads(first.content) == {"owner": accounts["admin"][0]["id"], "name": "Todo", "starter": "react", "choices": {}}
    assert first.headers["authorization"] == "Bearer secret"

    assert client.get("/apps", headers=JSON).json() == [app]
    assert client.get(f"/apps/{ID}", headers=JSON).json() == {**app, "memory_used": 30_000_000}
    assert client.delete(f"/apps/{ID}").status_code == 204
    assert client.get(f"/apps/{ID}", headers=JSON).status_code == 404


def test_nobody_else_sees_or_opens_them(client, spawner, accounts):
    from labb.server import app as server
    client.post("/apps", json={"name": "Mine"})
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}", **JSON})
    assert member.get("/apps").json() == []
    assert member.get(f"/apps/{ID}").status_code == 404
    assert member.post(f"/apps/{ID}/open", headers={"Host": "labb.lan"}).status_code == 404
    assert member.delete(f"/apps/{ID}").status_code == 404


def test_ids_that_cant_be_the_spawners_arent_sent(client, spawner):
    assert client.get("/apps/a0?owner=x", headers=JSON).status_code == 404
    assert client.get("/apps/..%3Fowner=x", headers=JSON).status_code == 404
    assert spawner["calls"] == []


def test_without_a_spawner(client, monkeypatch):
    monkeypatch.setattr(apps, "SPAWNER_URL", "")
    response = client.get("/apps", headers=JSON)
    assert response.status_code == 503 and "SPAWNER_URL" in response.json()["detail"]


@pytest.mark.parametrize("host, origin", [
    ("labb.lan", f"http://labb.lan:{PORT}"),
    ("labb.lan:8000", f"http://labb.lan:{PORT}"),
    ("192.168.1.5:8000", f"http://192.168.1.5:{PORT}"),
    ("[fd00::5]:8000", f"http://[fd00::5]:{PORT}"),
])
def test_an_app_opens_on_its_own_port_of_labbs_host(client, spawner, host, origin):
    client.post("/apps", json={"name": "Mine"})
    opened = client.post(f"/apps/{ID}/open", headers={"Host": host}).json()
    assert opened == {"url": f"{origin}/", "origin": origin, "ready": True}
    # Checked to answer first, where the server reaches it
    assert spawner["probed"] == [f"http://labb-app-{ID}:8080"]


def test_on_https_it_opens_with_https(client, spawner):
    from labb.server import app as server
    client.post("/apps", json={"name": "Mine"})
    behind_caddy = TestClient(server.app, base_url="https://labb.lan", headers={"Authorization": client.headers["Authorization"]})
    assert behind_caddy.post(f"/apps/{ID}/open").json()["url"] == f"https://labb.lan:{PORT}/"


def test_an_app_from_before_ports_says_to_make_it_again(client, spawner):
    client.post("/apps", json={"name": "Old"})
    spawner["kept"][ID]["port"] = 0
    refused = client.post(f"/apps/{ID}/open", headers={"Host": "labb.lan"})
    assert refused.status_code == 409 and "make a new one" in refused.json()["detail"]


def test_stop_start_restart_and_logs(client, spawner):
    client.post("/apps", json={"name": "Mine"})
    stopped = client.post(f"/apps/{ID}/stop").json()
    assert stopped["status"] == "exited" and stopped["detail"] == "Stopped" and "upstream" not in stopped
    assert client.post(f"/apps/{ID}/start").json()["status"] == "running"
    assert client.post(f"/apps/{ID}/restart").json()["status"] == "running"
    assert client.post(f"/apps/{ID}/explode").status_code == 422
    assert client.get(f"/apps/{ID}/logs", params={"tail": 50}, headers=JSON).json() == {"lines": "last 50 lines"}
    # As the signed-in user, to the spawner
    assert all(call.url.params.get("owner") for call in spawner["calls"][1:])


def test_someone_elses_app_cant_be_stopped_or_read(client, spawner, accounts):
    from labb.server import app as server
    client.post("/apps", json={"name": "Mine"})
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}", **JSON})
    assert member.post(f"/apps/{ID}/stop").status_code == 404
    assert member.get(f"/apps/{ID}/logs").status_code == 404


def test_opening_a_stopped_app_starts_it(client, spawner):
    client.post("/apps", json={"name": "Mine"})
    client.post(f"/apps/{ID}/stop")
    opened = client.post(f"/apps/{ID}/open", headers={"Host": "labb.lan"}).json()
    assert opened["ready"] is True and spawner["kept"][ID]["status"] == "running"
    assert [c.url.path for c in spawner["calls"] if c.method == "POST"][-1] == f"/apps/{ID}/start"


def test_starters_and_making_from_one(client, spawner):
    assert client.get("/apps/starters", headers=JSON).json()[0]["name"] == "react"
    client.post("/apps", json={"name": "Mine", "starter": "python", "choices": {"color": "blue", "font": "inter"}})
    sent = json.loads(spawner["calls"][-1].content)
    assert sent["starter"] == "python" and sent["choices"] == {"color": "blue", "font": "inter"}


def test_an_apps_files_and_rebuilding_it(client, spawner):
    client.post("/apps", json={"name": "Mine"})
    assert client.get(f"/apps/{ID}/files", headers=JSON).json() == [{"path": "Dockerfile", "size": 12}]
    saved = client.put(f"/apps/{ID}/files/site/index.html", json={"content": "<h1>Hi</h1>"})
    assert saved.json() == {"path": "site/index.html", "size": 11}
    assert client.get(f"/apps/{ID}/files/site/index.html", headers=JSON).json()["content"] == "<h1>Hi</h1>"
    # Paths are passed on encoded, as a path in the app, not something else of the spawner's
    client.put(f"/apps/{ID}/files/a%20b%3Fowner%3Dx.txt", json={"content": "x"})
    assert spawner["calls"][-1].url.raw_path.endswith(b"/files/a%20b%3Fowner%3Dx.txt?owner=" + spawner["calls"][-1].url.params["owner"].encode())
    assert client.delete(f"/apps/{ID}/files/site/index.html").status_code == 204
    built = client.post(f"/apps/{ID}/build").json()
    assert built["ok"] is True and built["app"]["build"] == 2 and "upstream" not in built["app"]
    assert client.get(f"/apps/{ID}/build", headers=JSON).json()["build"] == 2


def test_searching_an_apps_files(client, spawner):
    client.post("/apps", json={"name": "Mine"})
    found = client.get(f"/apps/{ID}/search", params={"q": "busy", "case": True}, headers=JSON).json()
    assert found["matches"][0]["path"] == "Dockerfile" and found["matches"][0]["ranges"] == [[5, 9]]
    asked = spawner["calls"][-1].url.params
    assert asked["q"] == "busy" and asked["case"] == "true" and asked["regex"] == "false" and asked["owner"]


def test_an_apps_sessions_are_listed_newest_first(client, spawner, accounts):
    from labb import chats
    client.post("/apps", json={"name": "Mine"})
    me = accounts["admin"][0]["id"]
    chats.touch(f"app-{ID}", "first session", user_id=me)
    chats.touch(f"app-{ID}--second01", "a fresh start", user_id=me)
    chats.touch("some-other-chat", "not the app's", user_id=me)
    listed = client.get(f"/apps/{ID}/sessions", headers=JSON).json()
    assert [s["thread_id"] for s in listed] == [f"app-{ID}--second01", f"app-{ID}"]
    assert listed[0]["title"] and listed[0]["updated_at"]


def test_someone_elses_files_and_builds_are_out_of_reach(client, spawner, accounts):
    from labb.server import app as server
    client.post("/apps", json={"name": "Mine"})
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}", **JSON})
    assert member.get(f"/apps/{ID}/files").status_code == 404
    assert member.get(f"/apps/{ID}/sessions").status_code == 404
    assert member.get(f"/apps/{ID}/files/Dockerfile").status_code == 404
    assert member.get(f"/apps/{ID}/search", params={"q": "busy"}).status_code == 404
    assert member.put(f"/apps/{ID}/files/Dockerfile", json={"content": "x"}).status_code == 404
    assert member.post(f"/apps/{ID}/build").status_code == 404


def test_settings_and_downloading_the_data(client, spawner):
    client.post("/apps", json={"name": "My Board!"})
    assert client.get(f"/apps/{ID}/settings", headers=JSON).json()["variables"] == []
    saved = client.put(f"/apps/{ID}/settings", json={"variables": [{"name": "KEY", "value": "s", "secret": True}]}).json()
    assert saved["variables"] == [{"name": "KEY", "value": None, "secret": True}] and "upstream" not in saved["app"]
    sent = json.loads(spawner["calls"][-1].content)
    assert sent == {"variables": [{"name": "KEY", "value": "s", "secret": True}]}
    # A download is a file, even when the browser navigates to it
    got = client.get(f"/apps/{ID}/data/download", headers={"Sec-Fetch-Mode": "navigate", "Accept": "text/html"})
    assert got.content == b"tar bytes" and got.headers["content-disposition"] == 'attachment; filename="My-Board-data.tar"'
    # Its files with their history, as a zip named after it
    got = client.get(f"/apps/{ID}/source/download", headers={"Sec-Fetch-Mode": "navigate", "Accept": "text/html"})
    assert got.content == b"zip bytes" and got.headers["content-disposition"] == 'attachment; filename="My-Board.zip"'
    assert spawner["calls"][-1].url.params["name"] == "My-Board"


def test_someone_elses_settings_and_data_are_out_of_reach(client, spawner, accounts):
    from labb.server import app as server
    client.post("/apps", json={"name": "Mine"})
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}", **JSON})
    assert member.get(f"/apps/{ID}/settings").status_code == 404
    assert member.put(f"/apps/{ID}/settings", json={"variables": []}).status_code == 404
    assert member.get(f"/apps/{ID}/data/download").status_code == 404
    assert member.get(f"/apps/{ID}/source/download").status_code == 404


def test_versions_through_the_server(client, spawner, accounts):
    from labb.server import app as server
    client.post("/apps", json={"name": "Mine"})
    assert client.get(f"/apps/{ID}/versions", headers=JSON).json()["versions"][0]["id"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert client.get(f"/apps/{ID}/versions/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/diff", headers=JSON).json() == {"diff": "+x"}
    assert client.get(f"/apps/{ID}/versions/unsaved/diff", headers=JSON).json() == {"diff": "+x"}
    assert client.post(f"/apps/{ID}/versions/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/restore").json()["version"] == "d" * 40
    # Only whole version ids are passed on
    asked = len(spawner["calls"])
    assert client.get(f"/apps/{ID}/versions/HEAD/diff", headers=JSON).status_code == 404
    assert client.post(f"/apps/{ID}/versions/..%2F..%2Fx/restore").status_code in (404, 405)
    assert len(spawner["calls"]) == asked
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}", **JSON})
    assert member.get(f"/apps/{ID}/versions").status_code == 404
    assert member.post(f"/apps/{ID}/versions/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/restore").status_code == 404


def test_a_database_through_the_server(client, spawner, accounts):
    from labb.server import app as server
    client.post("/apps", json={"name": "Mine"})
    assert client.get(f"/apps/{ID}/database", headers=JSON).json() == {"available": True, "enabled": False}
    made = client.post(f"/apps/{ID}/database").json()
    assert made["enabled"] and made["app"]["database"] is True and "upstream" not in made["app"]
    page = client.get(f"/apps/{ID}/database/rows", params={"table": "items", "offset": 50}, headers=JSON).json()
    assert page["asked"]["table"] == "items" and page["asked"]["offset"] == "50" and page["asked"]["schema"] == "public"
    assert client.post(f"/apps/{ID}/database/query", json={"sql": "select 1"}).json()["written"] is False
    assert client.post(f"/apps/{ID}/database/query", json={"sql": "delete", "write": True}).json()["written"] is True
    # A download, even when the browser navigates to it
    got = client.get(f"/apps/{ID}/database/download", headers={"Sec-Fetch-Mode": "navigate", "Accept": "text/html"})
    assert got.content == b"-- dump" and got.headers["content-disposition"] == 'attachment; filename="Mine-database.sql"'
    assert client.delete(f"/apps/{ID}/database").json()["app"]["database"] is False
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}", **JSON})
    for method, path in [("GET", "/database"), ("POST", "/database"), ("DELETE", "/database"), ("GET", "/database/rows?table=x"),
                         ("GET", "/database/download")]:
        assert member.request(method, f"/apps/{ID}{path}").status_code == 404, path
    assert member.post(f"/apps/{ID}/database/query", json={"sql": "select 1"}).status_code == 404


def test_an_api_through_the_server(client, spawner, accounts):
    from labb.server import app as server
    client.post("/apps", json={"name": "Mine"})
    made = client.post(f"/apps/{ID}/api", headers={"Host": "labb.lan:8000"}).json()
    # Its address: this page's host, on its port
    assert made["api"] == {"enabled": True, "port": PORT + 100, "status": "running", "signups": True,
                           "origin": f"http://labb.lan:{PORT + 100}", "url": f"http://labb.lan:{PORT + 100}/rest/"}
    assert client.patch(f"/apps/{ID}/api", json={"signups": False}).json()["api"]["signups"] is False
    assert client.delete(f"/apps/{ID}/api", headers={"Host": "labb.lan"}).json()["api"]["enabled"] is False
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}", **JSON})
    assert member.post(f"/apps/{ID}/api").status_code == 404
    assert member.delete(f"/apps/{ID}/api").status_code == 404
    assert member.patch(f"/apps/{ID}/api", json={"signups": True}).status_code == 404


def test_an_apps_users_through_the_server(client, spawner, accounts):
    from labb.server import app as server
    client.post("/apps", json={"name": "Mine"})
    assert client.get(f"/apps/{ID}/users", params={"offset": 100}, headers=JSON).json()["offset"] == 100
    assert client.patch(f"/apps/{ID}/users/{USER}", json={"disabled": True}).status_code == 204
    assert client.delete(f"/apps/{ID}/users/{USER}").status_code == 204
    sent = [c for c in spawner["calls"] if "/users/" in c.url.path]
    assert [(c.method, c.url.path, json.loads(c.content) if c.content else None) for c in sent] == [
        ("PATCH", f"/apps/{ID}/users/{USER}", {"disabled": True}), ("DELETE", f"/apps/{ID}/users/{USER}", None)]
    # Only a user's id goes into the spawner's URL
    for bad in ("..%2F..%2Fapps", "x", "1;drop"):
        assert client.delete(f"/apps/{ID}/users/{bad}").status_code in (404, 405)
    assert len([c for c in spawner["calls"] if "/users/" in c.url.path]) == 2
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}", **JSON})
    assert member.get(f"/apps/{ID}/users").status_code == 404
    assert member.delete(f"/apps/{ID}/users/{USER}").status_code == 404


def test_an_apps_notes_are_its_own_and_go_with_it(client, spawner, accounts):
    from labb.server import app as server
    client.post("/apps", json={"name": "Mine"})
    client.post("/notes", json={"action": "replace", "text": "## Mine\nNot the app's."})
    assert client.get(f"/apps/{ID}/notes", headers=JSON).json()["text"] == ""
    added = client.post(f"/apps/{ID}/notes", json={"action": "add", "topic": "Build", "text": "Vite."}).json()
    assert added["topics"][0].startswith("Build") and "Not the app's" not in added["text"]
    assert "Vite." not in client.get("/notes").json()["text"]
    # A change over an older read is refused, as with your own notes
    stale = client.post(f"/apps/{ID}/notes", json={"action": "replace", "text": "x", "base": "0" * 16})
    assert stale.status_code == 409
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}", **JSON})
    assert member.get(f"/apps/{ID}/notes").status_code == 404
    assert member.post(f"/apps/{ID}/notes", json={"action": "add", "text": "mine now"}).status_code == 404
    # Removing the app removes its notes
    assert client.delete(f"/apps/{ID}").status_code == 204
    from labb.server import notes
    assert notes.store.read(accounts["admin"][0]["id"], ID) == ""
    # Your own notes as the other tests expect them
    client.post("/notes", json={"action": "replace", "text": ""})
