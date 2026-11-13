"""labb-spawner against a stand-in for Docker: apps are containers with labels, each on a port of the range."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from labb_spawner import app as spawner



def starter_files(name: str) -> set[str]:
    """What a new app from a starter has: the React frontend as frontend/, then the starter's own files"""
    root = spawner.sources.STARTERS
    files = lambda folder, skip: {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file() and p.name != skip}
    return {f"frontend/{f}" for f in files(root / "_frontend", "choices.json")} | files(root / name, "starter.json")


# What a new app from the React starter (the default) has
REACT_FILES = starter_files("react")


def fronts(docker) -> list[dict]:
    return [r for r in docker.runs if r["labels"].get("labb.app.front") == "1"]


def servers(docker) -> list[dict]:
    return [r for r in docker.runs if "labb.app-server" in r["labels"]]


def server_of(docker, app):
    return docker.containers.get(f"labb-app-{app['id']}-server")


def test_a_new_app_gets_the_next_port_and_is_listed(http, docker):
    made = http.post("/apps", json={"owner": "u1", "name": "  My   app "})
    assert made.status_code == 201
    app = made.json()
    assert app["name"] == "My app" and app["owner"] == "u1" and app["port"] == spawner.FIRST_PORT
    assert app["status"] == "running" and len(app["id"]) == 12
    # Its front is published on its port, for the browser; it serves the page built from frontend/
    [run] = docker.runs
    assert run["ports"] == {"8080/tcp": (spawner.BIND, spawner.FIRST_PORT)} and spawner.BIND == "0.0.0.0"
    assert app["upstream"] == f"http://{spawner.UPSTREAM_HOST}:{spawner.FIRST_PORT}"
    assert run["image"].startswith("labb-app-front:") and run["labels"]["labb.app.front"] == "1"
    assert run["cap_drop"] == ["ALL"] and run["read_only"] and run["user"] == "65534:65534"
    assert run["environment"] == {"SERVER": f"labb-app-{app['id']}-server:8080"}
    site = spawner.pages.site(app["id"])
    assert run["volumes"] == {str(site.resolve()): {"bind": "/srv", "mode": "ro"}}
    assert (site / "current" / "index.html").is_file() and docker.page_builds == [app["id"]]
    # The React starter (the default) is its page only: no server to build or run
    assert app["server"] is False and app["build"] == 1 and app["legacy"] is False and docker.builds == [
        b for b in docker.builds if b["tag"].startswith("labb-app-front:")]
    files = {f["path"] for f in http.get(f"/apps/{app['id']}/files", params={"owner": "u1"}).json()}
    assert files == REACT_FILES
    assert {"frontend/src/App.jsx", "frontend/src/themes.css", "frontend/src/components/ui/button.jsx", "frontend/AGENTS.md"} <= files
    assert not any(f.endswith("Dockerfile") or f.endswith("serve.py") for f in files)

    # One with a server: built from backend/Dockerfile, and run beside its front, on the network only
    second = http.post("/apps", json={"owner": "u1", "name": "Two", "starter": "python"}).json()
    [server] = servers(docker)
    assert second["server"] is True and server["image"] == f"labb-app-{second['id']}:1"
    assert server["ports"] is None and server["network"] == spawner.LOCAL_NETWORK
    assert server["cap_drop"] == ["ALL"] and server["read_only"] and server["environment"] == {"APP_NAME": "Two", "PORT": "8080"}
    data = spawner.appdata.data_dir(second["id"])
    assert server["volumes"] == {str(data.resolve()): {"bind": "/data", "mode": "rw"}} and data.is_dir()
    build = next(b for b in docker.builds if b["tag"] == f"labb-app-{second['id']}:1")
    assert build["labels"] == {"labb.app": "1", "labb.app.id": second["id"]} and build["dockerfile"] == "backend/Dockerfile"
    assert second["port"] == spawner.FIRST_PORT + 1
    assert {a["id"] for a in http.get("/apps", params={"owner": "u1"}).json()} == {app["id"], second["id"]}
    assert http.get(f"/apps/{app['id']}", params={"owner": "u1"}).json()["name"] == "My app"


def test_a_freed_port_is_used_again(http):
    first = http.post("/apps", json={"owner": "u1", "name": "A"}).json()
    http.post("/apps", json={"owner": "u1", "name": "B"})
    http.delete(f"/apps/{first['id']}", params={"owner": "u1"})
    assert http.post("/apps", json={"owner": "u1", "name": "C"}).json()["port"] == spawner.FIRST_PORT


def test_people_only_see_and_remove_their_own(http):
    app = http.post("/apps", json={"owner": "u1", "name": "Mine"}).json()
    assert http.get("/apps", params={"owner": "u2"}).json() == []
    assert http.get(f"/apps/{app['id']}", params={"owner": "u2"}).status_code == 404
    assert http.delete(f"/apps/{app['id']}", params={"owner": "u2"}).status_code == 404
    assert http.delete(f"/apps/{app['id']}", params={"owner": "u1"}).status_code == 204
    assert http.get("/apps", params={"owner": "u1"}).json() == []


def test_a_port_something_else_has_is_skipped(http, docker):
    docker.busy.add(spawner.FIRST_PORT)
    app = http.post("/apps", json={"owner": "u1", "name": "A"}).json()
    assert app["port"] == spawner.FIRST_PORT + 1
    # The container that couldn't be published isn't left behind
    assert len(docker.running) == 1


def test_limits(http, monkeypatch):
    monkeypatch.setattr(spawner, "MAX_APPS", 1)
    assert http.post("/apps", json={"owner": "u1", "name": "A"}).status_code == 201
    assert http.post("/apps", json={"owner": "u1", "name": "B"}).status_code == 409
    assert http.post("/apps", json={"owner": "u2", "name": "   "}).status_code == 400
    monkeypatch.setattr(spawner, "LAST_PORT", spawner.FIRST_PORT)
    assert http.post("/apps", json={"owner": "u2", "name": "C"}).status_code == 503


def test_an_admin_changes_the_limits_from_labb(http, docker):
    shown = {limit["key"]: limit for limit in http.get("/limits").json()}
    assert shown["app_count"]["value"] == shown["app_count"]["default"] == spawner.MAX_APPS
    assert shown["app_memory_mb"]["value"] == 256 and shown["app_memory_mb"]["unit"] == "MB"
    assert shown["artifact_count"]["group"] == "Artifacts"
    assert TestClient(spawner.app).get("/limits").status_code == 401

    saved = {limit["key"]: limit["value"] for limit in http.put("/limits", json={"app_count": 1, "app_memory_mb": 512}).json()}
    assert saved["app_count"] == 1 and saved["app_memory_mb"] == 512
    app = http.post("/apps", json={"owner": "u1", "name": "A"}).json()
    assert app["memory_limit"] == "512m"
    assert http.post("/apps", json={"owner": "u1", "name": "B"}).json()["detail"] == "You have 1 apps already; remove one first"

    # Refused whole: nothing changes
    assert http.put("/limits", json={"app_count": 3, "app_files": 0}).status_code == 400
    assert http.put("/limits", json={"nope": 3}).status_code == 400
    assert {l["key"]: l["value"] for l in http.get("/limits").json()}["app_count"] == 1
    # Back to the default
    http.put("/limits", json={"app_count": None, "app_memory_mb": None})
    assert http.post("/apps", json={"owner": "u1", "name": "B"}).status_code == 201


def test_on_the_network_with_the_alias_caddy_reaches_it_by(http, docker, monkeypatch):
    monkeypatch.setattr(spawner, "NETWORK", "labb-apps")
    app = http.post("/apps", json={"owner": "u1", "name": "A"}).json()
    run = docker.runs[0]
    assert run["network"] == "labb-apps"
    assert run["networking_config"] == {"labb-apps": {"Aliases": [f"labb-app-{spawner.FIRST_PORT}"]}}
    # Published too, and the server checks it on the network
    assert run["ports"] == {"8080/tcp": (spawner.BIND, spawner.FIRST_PORT)}
    assert app["upstream"] == f"http://labb-app-{app['id']}:8080"
    # The network is made if it isn't there, once
    assert docker.networks.made == ["labb-apps"]
    http.post("/apps", json={"owner": "u1", "name": "B"})
    assert docker.networks.made == ["labb-apps"]


def test_with_https_caddy_has_the_ports_so_nothing_is_published(http, docker, monkeypatch):
    monkeypatch.setattr(spawner, "NETWORK", "labb-apps")
    monkeypatch.setattr(spawner, "PUBLISH", False)
    app = http.post("/apps", json={"owner": "u1", "name": "A"}).json()
    assert app["port"] == spawner.FIRST_PORT and docker.runs[0]["ports"] is None


def test_a_token_is_needed(docker):
    bare = TestClient(spawner.app)
    assert bare.get("/apps", params={"owner": "u1"}).status_code == 401
    assert bare.get("/health").json() == {"ok": True, "docker": True}


def made(http, owner="u1", name="A", starter="python"):
    """An app with a server, by default (most of what's tested here is the server's)"""
    return http.post("/apps", json={"owner": owner, "name": name, "starter": starter}).json()


def test_stop_start_restart(http, docker):
    app = made(http)
    assert app["detail"] == "" and app["restarts"] == 0 and app["memory_limit"] == spawner.MEMORY
    stopped = http.post(f"/apps/{app['id']}/stop", params={"owner": "u1"}).json()
    assert stopped["status"] == "exited" and stopped["detail"] == "Stopped"
    assert stopped["port"] == app["port"]
    started = http.post(f"/apps/{app['id']}/start", params={"owner": "u1"}).json()
    assert started["status"] == "running" and started["detail"] == ""
    assert http.post(f"/apps/{app['id']}/restart", params={"owner": "u1"}).json()["status"] == "running"
    # Only the owner
    for action in ("stop", "start", "restart"):
        assert http.post(f"/apps/{app['id']}/{action}", params={"owner": "u2"}).status_code == 404


def test_why_an_app_isnt_running(http, docker):
    app = made(http)
    # While its front runs, its server says how it is
    container = server_of(docker, app)
    container.status, container.attrs["State"] = "exited", {"ExitCode": 1, "OOMKilled": False}
    assert http.get(f"/apps/{app['id']}", params={"owner": "u1"}).json()["detail"].startswith("Crashed (exit code 1)")
    container.attrs["State"] = {"ExitCode": 137, "OOMKilled": True}
    assert http.get(f"/apps/{app['id']}", params={"owner": "u1"}).json()["detail"] == f"Ran out of memory ({spawner.MEMORY})"
    container.status, container.attrs["State"] = "restarting", {"ExitCode": 2, "OOMKilled": False}
    assert "Keeps crashing" in http.get(f"/apps/{app['id']}", params={"owner": "u1"}).json()["detail"]
    container.status, container.attrs["RestartCount"] = "running", 3
    running = http.get(f"/apps/{app['id']}", params={"owner": "u1"}).json()
    assert running["detail"] == "Crashed and restarted 3 times" and running["restarts"] == 3
    # Its memory use, for its page: the front's and the server's
    assert running["memory_used"] == 60_000_000
    # Its front stopped: the app is
    front = docker.containers.get(f"labb-app-{app['id']}")
    front.status, front.attrs["State"] = "exited", {"ExitCode": 143, "OOMKilled": False}
    assert http.get(f"/apps/{app['id']}", params={"owner": "u1"}).json()["status"] == "exited"


def test_a_port_taken_while_stopped_is_said(http, docker):
    app = made(http)
    http.post(f"/apps/{app['id']}/stop", params={"owner": "u1"})
    docker.running[0].port_taken = True
    refused = http.post(f"/apps/{app['id']}/start", params={"owner": "u1"})
    assert refused.status_code == 409 and "took this app's port" in refused.json()["detail"]


def test_logs(http, docker):
    app = made(http)
    assert http.get(f"/apps/{app['id']}/logs", params={"owner": "u1"}).json() == {"lines": "2026-11-08T12:00:00Z serving on 8080\n"}
    http.get(f"/apps/{app['id']}/logs", params={"owner": "u1", "tail": 999_999})
    assert [a for a in docker.log_asks if a] == [300, spawner.MAX_LOG_LINES]
    assert http.get(f"/apps/{app['id']}/logs", params={"owner": "u2"}).status_code == 404


def test_idle_apps_are_stopped_and_say_so(http, docker, monkeypatch):
    monkeypatch.setattr(spawner, "IDLE_MINUTES", 10)
    quiet, busy = made(http, name="Quiet"), made(http, name="Busy")
    quiet_c, busy_c = (docker.containers.get(f"labb-app-{a['id']}") for a in (quiet, busy))
    assert spawner.idle_sweep(0) == []
    busy_c.bytes += 500
    assert spawner.idle_sweep(9 * 60) == []
    busy_c.bytes += 500
    # Ten minutes without a byte: stopped; the other's traffic just changed, so it isn't
    assert spawner.idle_sweep(10 * 60) == [quiet["id"]]
    assert quiet_c.status == "exited" and busy_c.status == "running"
    # Its server with it
    assert server_of(docker, quiet).status == "exited" and server_of(docker, busy).status == "running"
    assert http.get(f"/apps/{quiet['id']}", params={"owner": "u1"}).json()["detail"].startswith("Stopped after 10 minutes")
    # Started again, it's counted from then; the busy one went quiet at 10 minutes
    http.post(f"/apps/{quiet['id']}/start", params={"owner": "u1"})
    assert spawner.idle_sweep(20 * 60) == [busy["id"]]
    assert spawner.idle_sweep(25 * 60) == []


def test_a_limit_for_the_whole_server(http, monkeypatch):
    monkeypatch.setattr(spawner, "MAX_TOTAL", 2)
    made(http, "u1"), made(http, "u2")
    refused = http.post("/apps", json={"owner": "u3", "name": "C"})
    assert refused.status_code == 409 and "most apps (2)" in refused.json()["detail"]


def test_starters(http):
    starters = http.get("/starters").json()
    assert [s["name"] for s in starters] == ["react", "python", "node"]
    assert all(s["title"] and s["description"] for s in starters)
    assert http.post("/apps", json={"owner": "u1", "name": "A", "starter": "cobol"}).status_code == 400
    # Each has the React frontend, and its three questions
    for starter in starters:
        assert [c["name"] for c in starter["choices"]] == ["color", "style", "font"]
        assert [len(c["options"]) for c in starter["choices"]] == [5, 3, 5]
    python = starter_files("python")
    assert {"backend/main.py", "backend/Dockerfile", "db/migrations/0001_items.sql", "frontend/src/App.jsx",
            "frontend/src/lib/api.js", "frontend/src/components/ui/card.jsx"} <= python and "Dockerfile" not in python
    assert {"backend/server.js", "backend/Dockerfile", "db/migrations/0001_items.sql"} <= starter_files("node")


def html_of(http, app) -> str:
    return http.get(f"/apps/{app['id']}/files/frontend/index.html", params={"owner": "u1"}).json()["content"]


def test_the_choices_go_on_the_page(http):
    picked = {"color": "violet", "style": "roomy", "font": "lora"}
    app = http.post("/apps", json={"owner": "u1", "name": "A", "starter": "python", "choices": picked}).json()
    assert '<html lang="en" data-color="violet" data-style="roomy" data-font="lora">' in html_of(http, app)
    # The python starter's own page, over the shared one
    page = http.get(f"/apps/{app['id']}/files/frontend/src/App.jsx", params={"owner": "u1"}).json()["content"]
    assert "/api/items" in page
    # What isn't picked is the default (style), else the first option
    app = http.post("/apps", json={"owner": "u1", "name": "B", "choices": {"font": "mono"}}).json()
    assert 'data-color="neutral" data-style="balanced" data-font="mono"' in html_of(http, app)


def test_choices_must_be_ones_offered(http):
    for choices in ({"color": "plaid"}, {"colour": "blue"}, {"font": "../x"}):
        refused = http.post("/apps", json={"owner": "u1", "name": "A", "choices": choices})
        assert refused.status_code == 400, choices
    assert http.get("/apps", params={"owner": "u1"}).json() == []


def test_the_name_goes_in_the_html_escaped(http):
    app = http.post("/apps", json={"owner": "u1", "name": "<b>Mine</b>", "starter": "node"}).json()
    page = http.get(f"/apps/{app['id']}/files/frontend/index.html", params={"owner": "u1"}).json()["content"]
    assert "<title>&lt;b&gt;Mine&lt;/b&gt;</title>" in page and "{{APP_NAME}}" not in page


def test_files_can_be_read_written_and_deleted_only_inside_the_app(http):
    app = made(http)
    owner = {"owner": "u1"}
    base = f"/apps/{app['id']}/files"
    assert http.put(f"{base}/frontend/about.html", params=owner, json={"content": "<p>Hi</p>"}).json() == {"path": "frontend/about.html", "size": 9}
    assert http.get(f"{base}/frontend/about.html", params=owner).json() == {"path": "frontend/about.html", "content": "<p>Hi</p>"}
    assert http.delete(f"{base}/frontend/about.html", params=owner).status_code == 204
    assert http.get(f"{base}/frontend/about.html", params=owner).status_code == 404
    # Nowhere else
    for bad in ("../escape.txt", "frontend/../../x", "/etc/passwd", "%2e%2e/escape.txt"):
        assert http.put(f"{base}/{bad}", params=owner, json={"content": "x"}).status_code in (400, 404), bad
    assert not (spawner.sources.DATA / "apps" / "escape.txt").exists()
    # What an HTTP client would tidy up before sending, refused here too
    for bad in ("./x", "a//b", "a/./b", "..", "", "a\\..\\..\\x"):
        with pytest.raises(HTTPException):
            spawner.sources.inside(app["id"], bad)
    # Only the owner's
    assert http.get(base, params={"owner": "u2"}).status_code == 404
    assert http.get(f"{base}/frontend/index.html", params={"owner": "u2"}).status_code == 404


def test_searching_the_files(http):
    app = made(http)
    owner = {"owner": "u1"}
    base = f"/apps/{app['id']}"
    http.put(f"{base}/files/frontend/notes.txt", params=owner, json={"content": "one Apple\ntwo\napple pie, apple\n"})
    found = http.get(f"{base}/search", params={**owner, "q": "apple"}).json()
    mine = [m for m in found["matches"] if m["path"] == "frontend/notes.txt"]
    assert [(m["line"], m["ranges"]) for m in mine] == [(1, [[4, 9]]), (3, [[0, 5], [11, 16]])]
    assert found["files"] == [] and found["more"] is False
    # Case, whole words, regular expressions, and file names
    cased = http.get(f"{base}/search", params={**owner, "q": "Apple", "case": True}).json()["matches"]
    assert [m["line"] for m in cased if m["path"] == "frontend/notes.txt"] == [1]
    assert http.get(f"{base}/search", params={**owner, "q": "appl", "word": True}).json()["matches"] == []
    assert [m["line"] for m in http.get(f"{base}/search", params={**owner, "q": "^t.o$", "regex": True}).json()["matches"]] == [2]
    assert http.get(f"{base}/search", params={**owner, "q": "(", "regex": True}).status_code == 400
    assert "frontend/notes.txt" in http.get(f"{base}/search", params={**owner, "q": "notes"}).json()["files"]
    assert http.get(f"{base}/search", params={"owner": "u2", "q": "apple"}).status_code == 404


def test_a_symlink_out_of_the_app_is_refused(http):
    app = made(http)
    (spawner.sources.source(app["id"]) / "out").symlink_to("/etc")
    assert http.get(f"/apps/{app['id']}/files/out/hostname", params={"owner": "u1"}).status_code == 400


def test_file_limits(http, monkeypatch):
    app = made(http)
    base, owner = f"/apps/{app['id']}/files", {"owner": "u1"}
    monkeypatch.setattr(spawner.sources, "MAX_FILE", 10)
    assert http.put(f"{base}/big.txt", params=owner, json={"content": "x" * 11}).status_code == 413
    monkeypatch.setattr(spawner.sources, "MAX_FILE", 1_000_000)
    monkeypatch.setattr(spawner.sources, "MAX_SOURCE", spawner.sources.size(app["id"]) + 5)
    assert http.put(f"{base}/a.txt", params=owner, json={"content": "12345"}).status_code == 200
    assert http.put(f"{base}/b.txt", params=owner, json={"content": "1"}).status_code == 413
    # Changing a file counts its new size, not both
    assert http.put(f"{base}/a.txt", params=owner, json={"content": "54321"}).status_code == 200


def test_a_rebuild_runs_the_new_build_on_the_same_port(http, docker):
    app = made(http)
    owner = {"owner": "u1"}
    http.put(f"/apps/{app['id']}/files/frontend/src/App.jsx", params=owner, json={"content": "<h1>Two</h1>"})
    built = http.post(f"/apps/{app['id']}/build", params=owner).json()
    assert built["ok"] is True and built["error"] is None and "Successfully built" in built["log"]
    # Progress lines of pulls aren't kept, their status is
    assert "Downloading" not in built["log"] and "latest Pulling from library/busybox" in built["log"]
    assert built["app"]["build"] == 2 and built["app"]["port"] == app["port"]
    assert servers(docker)[-1]["image"] == f"labb-app-{app['id']}:2" and len(docker.running) == 2
    # The page changed, so it was built first, and is served
    assert built["page"]["ok"] is True and built["page"]["build"] == 2 and built["server"] == {"ok": True, "error": None}
    assert (spawner.pages.site(app["id"]) / "current" / "index.html").read_text() == "<h1>Two</h1>"
    last = http.get(f"/apps/{app['id']}/build", params=owner).json()
    assert last["build"] == 2 and last["ok"] is True and last["building"] is False and "Successfully" in last["log"]
    # Two builds kept (this one and the one before); older ones go
    http.post(f"/apps/{app['id']}/build", params=owner)
    assert {t for t in docker.tags if t.startswith(f"labb-app-{app['id']}:")} == {f"labb-app-{app['id']}:2", f"labb-app-{app['id']}:3"}


def test_a_failed_build_leaves_the_last_good_one_running(http, docker):
    app = made(http)
    owner = {"owner": "u1"}
    http.put(f"/apps/{app['id']}/files/backend/Dockerfile", params=owner, json={"content": "FROM busybox\nRUN FAIL\n"})
    built = http.post(f"/apps/{app['id']}/build", params=owner).json()
    assert built["ok"] is False and "non-zero code" in built["error"] and "The build failed" in built["log"]
    assert built["app"]["build"] == 1 and built["app"]["status"] == "running"
    assert [r["image"] for r in servers(docker)] == [f"labb-app-{app['id']}:1"] and len(docker.runs) == 2
    assert http.get(f"/apps/{app['id']}/build", params=owner).json()["ok"] is False


def test_a_build_that_wont_start_goes_back_to_the_last_one(http, docker):
    app = made(http)
    docker.broken.add(f"labb-app-{app['id']}:2")
    refused = http.post(f"/apps/{app['id']}/build", params={"owner": "u1"})
    assert refused.status_code == 502 and "the last one runs again" in refused.json()["detail"]
    assert servers(docker)[-1]["image"] == f"labb-app-{app['id']}:1" and len(docker.running) == 2
    assert fronts(docker)[-1]["labels"]["labb.app.build"] == "1"


def test_a_build_that_crashes_as_it_starts_fails_and_the_last_one_runs_again(http, docker):
    app = made(http)
    owner = {"owner": "u1"}
    error = "/app/server.js:133\n})\n^\n\nSyntaxError: missing ) after argument list\n" + "    at wrapSafe (node:internal)\n" * 60
    docker.crashing[f"labb-app-{app['id']}:2"] = error.encode()
    http.put(f"/apps/{app['id']}/files/notes.txt", params=owner, json={"content": "changed"})
    built = http.post(f"/apps/{app['id']}/build", params=owner).json()
    assert built["ok"] is False and built["error"].startswith("It built, but crashed as it started (exit code 1)")
    # The error itself, from the start of what it wrote, not only the stack after it
    assert "SyntaxError: missing ) after argument list" in built["error"] and "more lines" in built["error"]
    # And its end, where Python says what went wrong
    assert built["error"].endswith("    at wrapSafe (node:internal)") and built["error"].count("wrapSafe") == 40 - 5
    assert built["app"]["build"] == 1 and built["app"]["status"] == "running" and len(docker.running) == 2
    assert built["before_crashing"] is False
    assert [r["image"] for r in servers(docker)] == [f"labb-app-{app['id']}:{n}" for n in (1, 2, 1)]
    last = http.get(f"/apps/{app['id']}/build", params=owner).json()
    assert last["ok"] is False and "SyntaxError" in last["error"]
    assert spawner.versions.history(app["id"])["versions"][0]["message"] == "Build 2 (crashed on start)"
    # When the build that goes back in was crashing too, the answer says so
    server_of(docker, app).status = "restarting"
    again = http.post(f"/apps/{app['id']}/build", params=owner).json()
    assert again["ok"] is False and again["before_crashing"] is True
    # Fixed, the next build is build 2 again, and runs
    del docker.crashing[f"labb-app-{app['id']}:2"]
    again = http.post(f"/apps/{app['id']}/build", params=owner).json()
    assert again["ok"] is True and again["app"]["build"] == 2


def test_a_build_whose_program_just_ends_is_told_it_must_keep_running(http, docker):
    app = made(http)
    docker.crashing[f"labb-app-{app['id']}:2"] = b"Database module not available\n"
    docker.crash_codes[f"labb-app-{app['id']}:2"] = 0
    built = http.post(f"/apps/{app['id']}/build", params={"owner": "u1"}).json()
    assert built["ok"] is False and "ended as it started, without an error (exit code 0)" in built["error"]
    assert "listening on port 8080" in built["error"] and "Database module not available" in built["error"]


def test_a_starter_that_doesnt_build_leaves_nothing(http, docker, monkeypatch, tmp_path):
    starters = tmp_path / "starters"
    (starters / "bad").mkdir(parents=True)
    (starters / "bad" / "starter.json").write_text('{"title": "Bad"}')
    (starters / "bad" / "Dockerfile").write_text("FROM busybox\nRUN FAIL\n")
    monkeypatch.setattr(spawner.sources, "STARTERS", starters)
    refused = http.post("/apps", json={"owner": "u1", "name": "A", "starter": "bad"})
    assert refused.status_code == 502 and "didn't build" in refused.json()["detail"]
    assert docker.running == [] and not list((spawner.sources.DATA / "apps").iterdir())


def test_removing_an_app_removes_its_files_and_images(http, docker):
    app = made(http)
    http.delete(f"/apps/{app['id']}", params={"owner": "u1"})
    assert not spawner.sources.folder(app["id"]).exists() and not any(t.startswith(f"labb-app-{app['id']}") for t in docker.tags)
    # Its front and its server
    assert docker.running == []


def test_apps_from_before_sources_are_legacy(http, docker):
    app = made(http)
    container = docker.running[0]
    del container.labels["labb.app.build"]
    spawner.sources.remove(app["id"])
    legacy = http.get(f"/apps/{app['id']}", params={"owner": "u1"}).json()
    assert legacy["legacy"] is True and legacy["build"] == 0
    assert http.get(f"/apps/{app['id']}/files", params={"owner": "u1"}).json() == []
    assert http.post(f"/apps/{app['id']}/build", params={"owner": "u1"}).status_code == 409


def settings_of(http, app, owner="u1"):
    return http.get(f"/apps/{app['id']}/settings", params={"owner": owner})


def put_settings(http, app, variables, owner="u1"):
    return http.put(f"/apps/{app['id']}/settings", params={"owner": owner}, json={"variables": variables})


def test_settings_reach_the_container_and_secrets_never_come_back(http, docker):
    app = made(http)
    saved = put_settings(http, app, [{"name": "GREETING", "value": "hi"}, {"name": "API_KEY", "value": "s3cret", "secret": True}])
    assert saved.status_code == 200
    body = saved.json()
    assert body["variables"] == [{"name": "GREETING", "secret": False, "value": "hi"}, {"name": "API_KEY", "secret": True, "value": None}]
    # The app was started again with them, labb's own last
    assert docker.runs[-1]["environment"] == {"GREETING": "hi", "API_KEY": "s3cret", "APP_NAME": "A", "PORT": "8080"}
    assert body["app"]["build"] == 1 and body["app"]["status"] == "running" and len(docker.running) == 2
    # Only its server: the front has none of them
    assert len(fronts(docker)) == 1
    # Encrypted where they're kept
    stored = (spawner.sources.folder(app["id"]) / "settings.json").read_text()
    assert "s3cret" not in stored and "hi" in stored
    assert settings_of(http, app).json()["variables"][1] == {"name": "API_KEY", "secret": True, "value": None}
    # A secret sent without a value keeps it
    put_settings(http, app, [{"name": "API_KEY", "secret": True}, {"name": "MORE", "value": "x"}])
    assert docker.runs[-1]["environment"]["API_KEY"] == "s3cret" and "GREETING" not in docker.runs[-1]["environment"]


def test_settings_that_cant_be(http):
    app = made(http)
    for variables, says in [
        ([{"name": "1BAD", "value": "x"}], "can't be a variable's name"),
        ([{"name": "PORT", "value": "9000"}], "labb's to set"),
        ([{"name": "A", "value": "1"}, {"name": "A", "value": "2"}], "twice"),
        ([{"name": "NEW_SECRET", "secret": True}], "needs a value"),
        ([{"name": "BIG", "value": "x" * 9000}], "over"),
    ]:
        refused = put_settings(http, app, variables)
        assert refused.status_code == 400 and says in refused.json()["detail"], variables
    assert put_settings(http, app, [], owner="u2").status_code == 404


def test_a_stopped_app_stays_stopped_with_new_settings(http, docker):
    app = made(http)
    http.post(f"/apps/{app['id']}/stop", params={"owner": "u1"})
    after = put_settings(http, app, [{"name": "X", "value": "1"}]).json()["app"]
    assert after["status"] == "exited" and docker.runs[-1]["environment"]["X"] == "1"


def test_data_size_limit_download_and_removal(http, docker, monkeypatch):
    import io
    import tarfile
    app = made(http)
    data = spawner.appdata.data_dir(app["id"])
    (data / "notes.db").write_bytes(b"x" * 5000)
    (data / "sub").mkdir()
    (data / "sub" / "a.txt").write_text("hello")
    # A link the app made is measured and packed as a link, never followed
    (data / "escape").symlink_to("/etc")
    shown = settings_of(http, app).json()["data"]
    assert shown["path"] == "/data" and 5005 <= shown["size"] < 20000 and shown["limit"] == spawner.appdata.DATA_LIMIT

    tar = tarfile.open(fileobj=io.BytesIO(http.get(f"/apps/{app['id']}/data/download", params={"owner": "u1"}).content))
    names = {m.name: m for m in tar.getmembers()}
    assert {"data/notes.db", "data/sub/a.txt", "data/escape"} <= set(names) and names["data/escape"].issym()
    assert not any(n.startswith("data/escape/") for n in names)
    assert http.get(f"/apps/{app['id']}/data/download", params={"owner": "u2"}).status_code == 404

    monkeypatch.setattr(spawner.appdata, "DATA_LIMIT", 1000)
    assert spawner.data_sweep() == [app["id"]] and docker.running[0].status == "exited"
    assert "its data is over" in http.get(f"/apps/{app['id']}", params={"owner": "u1"}).json()["detail"]

    http.delete(f"/apps/{app['id']}", params={"owner": "u1"})
    assert not data.exists()


def test_in_docker_the_data_is_mounted_from_the_spawners_volume(http, docker, monkeypatch):
    class Me:
        name = "me"
        labels = {}
        attrs = {"Mounts": [{"Destination": str(spawner.sources.DATA), "Source": "/var/lib/docker/volumes/labb_spawner-data/_data"}]}

    monkeypatch.setattr(spawner.socket, "gethostname", lambda: "me")
    docker.running.append(Me())
    docker.mounted = ("/var/lib/docker/volumes/labb_spawner-data/_data", str(spawner.sources.DATA))
    app = made(http)
    assert list(docker.runs[-1]["volumes"]) == [f"/var/lib/docker/volumes/labb_spawner-data/_data/apps/{app['id']}/data"]


def test_labbs_variables_for_an_app_that_uses_it(http, docker):
    app = made(http)
    put_settings(http, app, [{"name": "CITY", "value": "Prishtina"}])
    env = {"LABB_URL": "http://labb-web:8000", "LABB_API_KEY": "lba_secret"}
    assert http.put(f"/apps/{app['id']}/labb", params={"owner": "u1"}, json={"env": env}).status_code == 200
    # In its environment after the user's, never given back, encrypted where they're kept
    assert docker.runs[-1]["environment"] == {"CITY": "Prishtina", **env, "APP_NAME": "A", "PORT": "8080"}
    shown = settings_of(http, app).json()
    assert shown["uses_labb"] is True and [v["name"] for v in shown["variables"]] == ["CITY"]
    assert "lba_secret" not in (spawner.sources.folder(app["id"]) / "settings.json").read_text()
    # Changing the user's variables keeps them
    put_settings(http, app, [{"name": "CITY", "value": "Tirana"}])
    assert docker.runs[-1]["environment"]["LABB_API_KEY"] == "lba_secret"
    # The user can't set or override them, and the server sets only them
    assert put_settings(http, app, [{"name": "LABB_API_KEY", "value": "mine"}]).status_code == 400
    assert http.put(f"/apps/{app['id']}/labb", params={"owner": "u1"}, json={"env": {"OTHER": "x"}}).status_code == 400
    assert http.put(f"/apps/{app['id']}/labb", params={"owner": "u2"}, json={"env": env}).status_code == 404
    # None: gone
    http.put(f"/apps/{app['id']}/labb", params={"owner": "u1"}, json={"env": None})
    assert "LABB_API_KEY" not in docker.runs[-1]["environment"] and settings_of(http, app).json()["uses_labb"] is False


def test_labbs_helper_goes_into_the_app(http):
    app = made(http, starter="react")
    written = http.post(f"/apps/{app['id']}/labb-helper", params={"owner": "u1", "language": "python"}).json()
    # Beside the server, for an app with one; in the top folder for one from before backend/
    assert written["path"] == "labb.py"
    content = http.get(f"/apps/{app['id']}/files/labb.py", params={"owner": "u1"}).json()["content"]
    assert "def ask(" in content and "/app-api/ask" in content
    assert http.post(f"/apps/{app['id']}/labb-helper", params={"owner": "u1", "language": "node"}).json()["path"] == "labb.js"
    python = http.post("/apps", json={"owner": "u1", "name": "Py", "starter": "python"}).json()
    assert http.post(f"/apps/{python['id']}/labb-helper", params={"owner": "u1", "language": "python"}).json()["path"] == "backend/labb.py"
    assert http.post(f"/apps/{app['id']}/labb-helper", params={"owner": "u1", "language": "cobol"}).status_code == 400
    assert http.post(f"/apps/{app['id']}/labb-helper", params={"owner": "u2", "language": "python"}).status_code == 404


def test_a_starters_leftovers_arent_copied(http, monkeypatch, tmp_path):
    import shutil
    starters = tmp_path / "starters"
    shutil.copytree(spawner.sources.STARTERS / "python", starters / "python")
    shutil.copytree(spawner.sources.STARTERS / "_frontend", starters / "_frontend")
    (starters / "python" / "__pycache__").mkdir()
    (starters / "python" / "__pycache__" / "app.cpython-314.pyc").write_bytes(b"x")
    (starters / "_frontend" / "node_modules" / "react").mkdir(parents=True)
    (starters / "_frontend" / "node_modules" / "react" / "index.js").write_text("")
    (starters / "_frontend" / "dist").mkdir()
    (starters / "_frontend" / "dist" / "index.html").write_text("")
    monkeypatch.setattr(spawner.sources, "STARTERS", starters)
    app = http.post("/apps", json={"owner": "u1", "name": "A", "starter": "python"}).json()
    paths = {f["path"] for f in http.get(f"/apps/{app['id']}/files", params={"owner": "u1"}).json()}
    assert "backend/main.py" in paths and not any("__pycache__" in p or p.endswith(".pyc") for p in paths)
    assert not any("node_modules" in p or "dist/" in p for p in paths)


def versions_of(http, app, owner="u1"):
    return http.get(f"/apps/{app['id']}/versions", params={"owner": owner})


def test_downloading_the_files_with_their_history(http, tmp_path):
    import io
    import zipfile

    from dulwich import porcelain
    from dulwich.repo import Repo

    app = made(http, name="My Board!")
    owner = {"owner": "u1"}
    # A change not built yet, and a new file
    http.put(f"/apps/{app['id']}/files/frontend/index.html", params=owner, json={"content": "<h1>Changed</h1>\n"})
    http.put(f"/apps/{app['id']}/files/notes.txt", params=owner, json={"content": "new\n"})
    response = http.get(f"/apps/{app['id']}/source/download", params={**owner, "name": "My Board!"})
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    zipfile.ZipFile(io.BytesIO(response.content)).extractall(tmp_path)
    folder = tmp_path / "My-Board"
    assert (folder / "frontend" / "index.html").read_text() == "<h1>Changed</h1>\n" and (folder / ".git" / "HEAD").is_file()
    repo = Repo(str(folder))
    assert not repo.bare and repo[repo.head()].message.decode().strip() == versions_of(http, app).json()["versions"][0]["message"]
    status = porcelain.status(str(folder))
    assert status.unstaged == [b"frontend/index.html"] and status.untracked == [b"notes.txt"]
    assert not any(status.staged.values())
    # Only the owner's
    assert http.get(f"/apps/{app['id']}/source/download", params={"owner": "u2"}).status_code == 404


def test_a_version_at_making_and_at_each_build(http):
    app = made(http, starter="react")
    owner = {"owner": "u1"}
    first = versions_of(http, app).json()
    assert [v["message"] for v in first["versions"]] == ["Made from the react starter"] and first["unsaved"] == []
    assert "frontend/index.html" in first["versions"][0]["files"] and len(first["versions"][0]["id"]) == 40
    http.put(f"/apps/{app['id']}/files/frontend/index.html", params=owner, json={"content": "<h1>Two</h1>\n"})
    assert versions_of(http, app).json()["unsaved"] == ["frontend/index.html"]
    unsaved = http.get(f"/apps/{app['id']}/versions/unsaved/diff", params=owner).json()["diff"]
    assert "+<h1>Two</h1>" in unsaved and "--- a/frontend/index.html" in unsaved
    http.post(f"/apps/{app['id']}/build", params={**owner, "note": "from the chat"})
    after = versions_of(http, app).json()
    assert [v["message"] for v in after["versions"]] == ["Build 2, from the chat", "Made from the react starter"]
    assert after["versions"][0]["files"] == ["frontend/index.html"] and after["unsaved"] == []
    diff = http.get(f"/apps/{app['id']}/versions/{after['versions'][0]['id']}/diff", params=owner).json()["diff"]
    assert "+<h1>Two</h1>" in diff
    # Nothing changed: no new version
    http.post(f"/apps/{app['id']}/build", params=owner)
    assert len(versions_of(http, app).json()["versions"]) == 2


def test_a_failed_build_is_a_version_too(http):
    app = made(http)
    http.put(f"/apps/{app['id']}/files/backend/Dockerfile", params={"owner": "u1"}, json={"content": "FROM busybox\nRUN FAIL\n"})
    http.post(f"/apps/{app['id']}/build", params={"owner": "u1"})
    assert versions_of(http, app).json()["versions"][0]["message"] == "Build 2 (didn't build)"


def test_restoring_a_version_keeps_what_wasnt_saved(http):
    app = made(http, starter="react")
    owner = {"owner": "u1"}
    original = versions_of(http, app).json()["versions"][0]["id"]
    http.put(f"/apps/{app['id']}/files/frontend/index.html", params=owner, json={"content": "<h1>Built</h1>\n"})
    http.put(f"/apps/{app['id']}/files/frontend/extra/new.js", params=owner, json={"content": "1"})
    http.post(f"/apps/{app['id']}/build", params=owner)
    # A change not built yet
    http.put(f"/apps/{app['id']}/files/frontend/src/App.jsx", params=owner, json={"content": "// mine\n"})
    restored = http.post(f"/apps/{app['id']}/versions/{original}/restore", params=owner).json()
    messages = [v["message"] for v in restored["versions"]]
    assert messages[0].startswith(f"Back to {original[:8]} (Made from the react starter)")
    assert messages[1] == "Changes not built yet, kept before going back" and restored["unsaved"] == []
    # The files are as they were: the new one gone (and its folder), the changed ones back
    paths = {f["path"] for f in http.get(f"/apps/{app['id']}/files", params=owner).json()}
    assert paths == REACT_FILES
    assert not (spawner.sources.source(app["id"]) / "frontend" / "extra").exists()
    assert "{{APP_NAME}}" not in http.get(f"/apps/{app['id']}/files/frontend/index.html", params=owner).json()["content"]
    # And the change not built is in its version, to go back to
    kept = restored["versions"][1]["id"]
    assert "+// mine" in http.get(f"/apps/{app['id']}/versions/{kept}/diff", params=owner).json()["diff"]


def test_versions_are_the_owners_and_must_be_real(http):
    app = made(http)
    assert versions_of(http, app, owner="u2").status_code == 404
    assert http.get(f"/apps/{app['id']}/versions/{'0' * 40}/diff", params={"owner": "u1"}).status_code == 404
    assert http.get(f"/apps/{app['id']}/versions/HEAD/diff", params={"owner": "u1"}).status_code == 404
    assert http.post(f"/apps/{app['id']}/versions/{'0' * 40}/restore", params={"owner": "u1"}).status_code == 404


def test_an_app_from_before_versions_starts_with_its_files(http):
    app = made(http)
    import shutil
    shutil.rmtree(spawner.sources.folder(app["id"]) / "git")
    assert [v["message"] for v in versions_of(http, app).json()["versions"]] == ["The files as they were before versions"]


def test_a_new_api_is_waited_for_until_it_answers(monkeypatch):
    from types import SimpleNamespace
    from labb_spawner import app as spawner
    asked = []

    class Held:
        name = "labb-app-x-api"

        def exec_run(self, cmd, user):
            asked.append((cmd[:2], user))
            return SimpleNamespace(exit_code=0 if len(asked) >= 3 else 1)

    monkeypatch.setattr(spawner, "API_WAIT_SECONDS", 5)
    monkeypatch.setattr(spawner.time, "sleep", lambda s: None)
    spawner.wait_for_api(Held())
    assert len(asked) == 3 and asked[0] == (["python", "-I"], "65534:65534")
    # Never answering: given up on after the wait
    monkeypatch.setattr(spawner, "API_WAIT_SECONDS", 0.05)
    Held.exec_run = lambda self, cmd, user: SimpleNamespace(exit_code=1)
    spawner.wait_for_api(Held())


# Putting apps and their APIs right after a restart (heal)

@pytest.fixture
def on_network(docker, monkeypatch):
    """Apps on the labb-apps network (as with compose), and an API for any app that asks"""
    monkeypatch.setattr(spawner, "NETWORK", "labb-apps")
    monkeypatch.setattr(spawner, "PUBLISH", False)
    with_api = set()
    monkeypatch.setattr(spawner.appdata, "api_secrets",
                        lambda app_id: {"password": "p", "jwt_secret": "s" * 48, "auth_password": "a"} if app_id in with_api else None)
    return with_api


def with_its_api(http, on_network, owner="u1"):
    app = made(http, owner=owner)
    on_network.add(app["id"])
    spawner.run_api(app["id"], app["port"])
    return app


def test_the_apps_network_made_again_puts_the_app_and_its_api_on_the_new_one(http, docker, on_network):
    app = with_its_api(http, on_network)
    docker.networks.remake("labb-apps")
    old_api = spawner.api_container(app["id"])
    assert not spawner.on_network(old_api)
    spawner.heal_all()
    container, api = spawner.find(app["id"], "u1"), spawner.api_container(app["id"])
    assert spawner.on_network(container) and spawner.on_network(api) and api is not old_api
    assert api.status == "running"
    # Caddy finds it by the same name
    assert docker.runs[-1]["networking_config"] == {"labb-apps": {"Aliases": [f"labb-app-api-{app['port'] + 100}"]}}
    # Right now: nothing more to do
    runs = len(docker.runs)
    spawner.heal_all()
    assert len(docker.runs) == runs


def test_restarting_an_app_starts_its_stopped_api(http, docker, on_network):
    app = with_its_api(http, on_network)
    api = spawner.api_container(app["id"])
    api.stop()
    assert http.post(f"/apps/{app['id']}/restart", params={"owner": "u1"}).status_code == 200
    assert api.status == "running"


def test_a_stopped_app_starts_on_the_network_made_again_meanwhile(http, docker, on_network):
    app = with_its_api(http, on_network)
    http.post(f"/apps/{app['id']}/stop", params={"owner": "u1"})
    docker.networks.remake("labb-apps")
    started = http.post(f"/apps/{app['id']}/start", params={"owner": "u1"})
    assert started.status_code == 200 and started.json()["status"] == "running"
    assert spawner.on_network(spawner.find(app["id"], "u1"))
    api = spawner.api_container(app["id"])
    assert spawner.on_network(api) and api.status == "running"


def test_an_api_that_went_is_made_again_and_one_turned_off_isnt(http, docker, on_network):
    app = with_its_api(http, on_network)
    spawner.api_container(app["id"]).remove(force=True)
    spawner.heal_all()
    assert spawner.api_container(app["id"]).status == "running"
    # Turned off (no secrets kept): nothing made
    on_network.discard(app["id"])
    spawner.api_container(app["id"]).remove(force=True)
    spawner.heal_all()
    assert spawner.api_container(app["id"]) is None


def test_a_stopped_apps_missing_api_is_made_stopped_and_a_building_app_is_left_alone(http, docker, on_network):
    app = with_its_api(http, on_network)
    http.post(f"/apps/{app['id']}/stop", params={"owner": "u1"})
    spawner.api_container(app["id"]).remove(force=True)
    spawner._building.add(app["id"])
    try:
        spawner.heal_all()
        assert spawner.api_container(app["id"]) is None
    finally:
        spawner._building.discard(app["id"])
    spawner.heal_all()
    assert spawner.api_container(app["id"]).status == "exited"


def test_an_app_with_only_a_page_builds_and_runs_without_a_server(http, docker):
    app = made(http, starter="react")
    owner = {"owner": "u1"}
    http.put(f"/apps/{app['id']}/files/frontend/src/App.jsx", params=owner, json={"content": "<h1>Mine</h1>"})
    built = http.post(f"/apps/{app['id']}/build", params=owner).json()
    assert built["ok"] is True and "its page only" in built["log"] and built["app"]["server"] is False
    assert built["page"]["ok"] is True and servers(docker) == [] and len(docker.running) == 1
    assert (spawner.pages.site(app["id"]) / "current" / "index.html").read_text() == "<h1>Mine</h1>"
    # Its front's logs are its logs, and stopping and starting it is its front's
    assert http.get(f"/apps/{app['id']}/logs", params=owner).json()["lines"]
    assert http.post(f"/apps/{app['id']}/stop", params=owner).json()["status"] == "exited"
    assert http.post(f"/apps/{app['id']}/start", params=owner).json()["status"] == "running"


def test_a_page_change_is_built_and_served_by_itself(http, docker, monkeypatch):
    import time
    app = made(http)
    owner = {"owner": "u1"}
    runs = len(docker.runs)
    # A few files written together build once, after the last. Without timing them: the first ones are
    # written while the wait is an hour (conftest's), so none can build early however slow the machine
    for n in range(2):
        http.put(f"/apps/{app['id']}/files/frontend/src/App.jsx", params=owner, json={"content": f"<h1>{n}</h1>"})
    assert http.get(f"/apps/{app['id']}/page", params=owner).json()["pending"] is True
    assert docker.page_builds == [app["id"]]
    # The last one starts the wait again, short now
    monkeypatch.setattr(spawner.pages, "DEBOUNCE_SECONDS", 0.05)
    http.put(f"/apps/{app['id']}/files/frontend/src/App.jsx", params=owner, json={"content": "<h1>2</h1>"})
    deadline = time.monotonic() + 5
    while spawner.pages.last(app["id"])["build"] < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)
    page = http.get(f"/apps/{app['id']}/page", params=owner).json()
    assert page["build"] == 2 and page["ok"] is True and page["pending"] is False and page["changed"] is False
    assert docker.page_builds == [app["id"]] * 2
    assert (spawner.pages.site(app["id"]) / "current" / "index.html").read_text() == "<h1>2</h1>"
    # Nothing was started again for it, and only this build and the one before are kept
    assert len(docker.runs) == runs
    assert sorted(p.name for p in spawner.pages.site(app["id"]).iterdir()) == ["1", "2", "current"]
    # A change outside frontend/ isn't the page's
    http.put(f"/apps/{app['id']}/files/backend/main.py", params=owner, json={"content": "x"})
    time.sleep(0.2)
    assert docker.page_builds == [app["id"]] * 2


def test_a_page_that_doesnt_build_leaves_the_last_one_served(http, docker):
    app = made(http)
    owner = {"owner": "u1"}
    http.put(f"/apps/{app['id']}/files/frontend/src/App.jsx", params=owner, json={"content": "export default function App( { FAIL"})
    page = http.get(f"/apps/{app['id']}/page", params={**owner, "wait": True}).json()
    assert page["ok"] is False and page["build"] == 2 and page["served"] == 1
    # Vite's error, without its stack, with its paths in frontend/
    assert page["error"].startswith("`vite build` failed (paths are in frontend/)") and "Expected `}`" in page["error"]
    assert "aggregateBindingErrors" not in page["error"] and "frontend/node_modules" in page["log"]
    assert (spawner.pages.site(app["id"]) / "current").readlink().name == "1"
    # The same files again fail the same way, without building; a build of the app tries once more
    assert http.get(f"/apps/{app['id']}/page", params={**owner, "wait": True}).json()["build"] == 2
    built = http.post(f"/apps/{app['id']}/build", params=owner).json()
    assert built["ok"] is False and built["error"].startswith("The page didn't build") and built["server"]["ok"] is True
    assert built["log"].startswith("The page (frontend/) didn't build") and built["page"]["build"] == 3
    # The server's new build runs all the same
    assert built["app"]["build"] == 2 and servers(docker)[-1]["image"] == f"labb-app-{app['id']}:2"
    assert http.get(f"/apps/{app['id']}/build", params=owner).json()["ok"] is False
    assert http.get(f"/apps/{app['id']}/page", params={"owner": "u2"}).status_code == 404


def test_its_server_follows_its_front(http, docker):
    app = made(http)
    owner = {"owner": "u1"}
    http.post(f"/apps/{app['id']}/stop", params=owner)
    assert server_of(docker, app).status == "exited"
    assert http.post(f"/apps/{app['id']}/start", params=owner).json()["status"] == "running"
    assert server_of(docker, app).status == "running"
    # A server that went is made again, stopped if its app is
    server_of(docker, app).remove(force=True)
    front = docker.containers.get(f"labb-app-{app['id']}")
    assert spawner.heal(app["id"], front) == ["made its missing server"] and server_of(docker, app).status == "running"
    server_of(docker, app).stop()
    assert spawner.heal(app["id"], front) == ["started its server"]
    assert spawner.heal(app["id"], front) == []


def test_an_app_from_before_fronts_becomes_a_front_and_a_server(http, docker):
    app = made(http)
    front = docker.containers.get(f"labb-app-{app['id']}")
    server_of(docker, app).remove(force=True)
    # As it was: one container, published on its port, with no front's labels
    for key in ("labb.app.front", "labb.app.server"):
        del front.labels[key]
    runs = len(docker.runs)
    assert spawner.heal(app["id"], front) == ["made it a front and a server"]
    made_now = docker.runs[runs:]
    assert [r["image"] for r in made_now][0] == f"labb-app-{app['id']}:1" and made_now[1]["labels"]["labb.app.front"] == "1"
    assert made_now[1]["labels"]["labb.app.port"] == str(app["port"]) and made_now[1]["ports"] == {"8080/tcp": (spawner.BIND, app["port"])}
    assert http.get(f"/apps/{app['id']}", params={"owner": "u1"}).json()["server"] is True
    # Once is enough
    assert spawner.heal(app["id"], docker.containers.get(f"labb-app-{app['id']}")) == []


def test_restoring_a_version_builds_its_page(http, docker):
    app = made(http)
    owner = {"owner": "u1"}
    original = versions_of(http, app).json()["versions"][0]["id"]
    http.put(f"/apps/{app['id']}/files/frontend/src/App.jsx", params=owner, json={"content": "<h1>New</h1>"})
    http.post(f"/apps/{app['id']}/build", params=owner)
    http.post(f"/apps/{app['id']}/versions/{original}/restore", params=owner)
    page = http.get(f"/apps/{app['id']}/page", params={**owner, "wait": True}).json()
    assert page["build"] == 3 and "/api/items" in (spawner.pages.site(app["id"]) / "current" / "index.html").read_text()


def test_the_system_report_has_labbs_services_and_apps_added_up(http, docker, monkeypatch):
    from conftest import FakeContainer
    monkeypatch.setattr(spawner.system, "SAMPLE_SECONDS", 0)
    monkeypatch.setattr(spawner.system.socket, "gethostname", lambda: "spawner-1")
    project = {"com.docker.compose.project": "labb"}
    me = FakeContainer(docker, "spawner-1", {**project, "com.docker.compose.service": "spawner"})
    web = FakeContainer(docker, "labb-web-1", {**project, "com.docker.compose.service": "labb-web"})
    web.attrs["State"]["StartedAt"] = "2026-11-12T08:00:00Z"
    stopped = FakeContainer(docker, "labb-crawler-1", {**project, "com.docker.compose.service": "crawler"})
    stopped.status = "exited"
    docker.running += [me, web, stopped]
    # Each sample has used 1% more of 2 processors' time: 2 x 1% = 2% of one
    counters = {}

    def stats(self, stream, one_shot):
        work = counters[self.name] = counters.get(self.name, 0) + 1_000
        return {"memory_stats": {"usage": self.memory + 5_000, "limit": 512_000_000, "stats": {"inactive_file": 5_000}},
                "cpu_stats": {"cpu_usage": {"total_usage": work}, "system_cpu_usage": work * 100, "online_cpus": 2}}

    monkeypatch.setattr(FakeContainer, "stats", stats)
    app = http.post("/apps", json={"owner": "u1", "name": "Shop"}).json()

    found = http.get("/system").json()
    assert found["project"] == "labb"
    services = {s["name"]: s for s in found["services"]}
    assert set(services) == {"spawner", "labb-web", "crawler"}
    assert services["labb-web"] == {"name": "labb-web", "container": "labb-web-1", "status": "running",
                                    "since": "2026-11-12T08:00:00Z", "restarts": 0, "memory": 30_000_000,
                                    "memory_limit": 512_000_000, "cpu": 2.0}
    # A stopped service has no usage
    assert "cpu" not in services["crawler"] and services["crawler"]["status"] == "exited"
    top = found["apps"]["top"][0]
    assert found["apps"]["running"] == 1 and top["id"] == app["id"] and top["name"] == "Shop" and top["owner"] == "u1"
    assert top["memory"] == found["apps"]["memory"] == 30_000_000 * top["containers"]
    assert TestClient(spawner.app).get("/system").status_code == 401
