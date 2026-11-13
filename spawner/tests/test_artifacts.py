"""Artifacts: made from the template, only their owner's, their files kept inside, and built with
no network into a site labb can publish."""

import io
import json
import tarfile

import pytest
from fastapi import HTTPException

from labb_spawner import app as spawner
from labb_spawner import artifacts


@pytest.fixture
def made(http):
    answer = http.post("/artifacts", json={"owner": "ada", "thread": "t1", "title": "Sales chart, Q3!"})
    assert answer.status_code == 201, answer.text
    return answer.json()


def page(http, name, text, owner="ada"):
    return http.put(f"/artifacts/{name}/files/app/page.tsx", params={"owner": owner}, json={"content": text})


def test_made_from_the_template_with_a_name_that_cant_be_guessed(http, made, docker):
    assert made["name"].startswith("sales-chart-q3-") and len(made["name"]) == len("sales-chart-q3-") + 6
    assert artifacts.NAME.match(made["name"])
    assert made["owner"] == "ada" and made["thread"] == "t1" and made["title"] == "Sales chart, Q3!"
    listed = [f["path"] for f in http.get(f"/artifacts/{made['name']}/files", params={"owner": "ada"}).json()]
    assert {"AGENTS.md", "app/page.tsx", "package.json", "components/charts/index.tsx", "components/diagram/index.tsx"} <= set(listed)
    assert not any(path.startswith(("node_modules/", ".next/", "out/")) for path in listed)
    assert http.get("/artifacts", params={"owner": "ada"}).json()[0]["name"] == made["name"]
    assert http.get("/artifacts", params={"owner": "ada", "thread": "other"}).json() == []


def test_only_the_owner_reaches_it(http, made):
    name = made["name"]
    for answer in (
        http.get(f"/artifacts/{name}", params={"owner": "eve"}),
        http.get(f"/artifacts/{name}/files", params={"owner": "eve"}),
        http.get(f"/artifacts/{name}/files/app/page.tsx", params={"owner": "eve"}),
        page(http, name, "x", owner="eve"),
        http.get(f"/artifacts/{name}/search", params={"owner": "eve", "q": "x"}),
        http.post(f"/artifacts/{name}/build", params={"owner": "eve"}),
        http.get(f"/artifacts/{name}/site", params={"owner": "eve"}),
        http.delete(f"/artifacts/{name}", params={"owner": "eve"}),
    ):
        assert answer.status_code == 404, answer.text
    assert http.get("/artifacts", params={"owner": "eve"}).json() == []


def test_its_files_are_searched_like_an_apps(http, made):
    name = made["name"]
    assert page(http, name, "export default function Page() {\n  return <h1>Quarterly sales</h1>;\n}\n").status_code == 200
    found = http.get(f"/artifacts/{name}/search", params={"owner": "ada", "q": "quarterly"}).json()
    assert [(m["path"], m["line"]) for m in found["matches"]] == [("app/page.tsx", 2)]
    assert http.get(f"/artifacts/{name}/search", params={"owner": "ada", "q": "Quarterly", "case": True, "word": True}).json()["matches"]
    assert http.get(f"/artifacts/{name}/search", params={"owner": "ada", "q": "page", "word": True}).json()["files"] == ["app/page.tsx"]
    assert http.get(f"/artifacts/{name}/search", params={"owner": "ada", "q": "(", "regex": True}).status_code == 400


def test_files_stay_inside(http, made):
    name = made["name"]
    for path in ("node_modules/x.js", "out/index.html", ".next/x", "a%5Cb"):
        answer = http.put(f"/artifacts/{name}/files/{path}", params={"owner": "ada"}, json={"content": "x"})
        assert answer.status_code == 400, (path, answer.status_code)
    # What an HTTP client would tidy away before it's sent
    for path in ("../artifact.json", "a//b", "./x", "a/../../x", "/etc/passwd"):
        with pytest.raises(HTTPException):
            artifacts.inside(name, path)
    assert page(http, name, "export default 1").json() == {"path": "app/page.tsx", "size": 16, "created": False}
    assert http.put(f"/artifacts/{name}/files/data/sales.ts", params={"owner": "ada"}, json={"content": "x"}).json()["created"]
    assert http.get(f"/artifacts/{name}/files/app/page.tsx", params={"owner": "ada"}).json()["content"] == "export default 1"
    assert http.delete(f"/artifacts/{name}/files/data/sales.ts", params={"owner": "ada"}).status_code == 204
    assert not (artifacts.source(name) / "data").exists()


def test_a_build_makes_the_site_with_no_network(http, made, docker):
    name = made["name"]
    page(http, name, "export default function Page() { return <h1>Hi</h1> }")
    built = http.post(f"/artifacts/{name}/build", params={"owner": "ada"}).json()
    assert built["ok"] and built["error"] is None and built["files"] == 2
    run = docker.artifact_builds[-1]
    assert run["network_mode"] == "none" and run["cap_drop"] == ["ALL"]
    assert run["environment"]["ARTIFACT_BASE_PATH"] == f"/artifacts/{name}"
    assert [mount["mode"] for mount in run["volumes"].values()] == ["ro", "rw"]
    assert run["image"].startswith("labb-artifact-builder:")
    site = http.get(f"/artifacts/{name}/site", params={"owner": "ada"})
    with tarfile.open(fileobj=io.BytesIO(site.content)) as archive:
        assert sorted(archive.getnames()) == ["_next/page.js", "index.html"]
        assert b"<h1>Hi</h1>" in archive.extractfile("index.html").read()
    assert http.get(f"/artifacts/{name}", params={"owner": "ada"}).json()["build"]["ok"] is True


def test_a_failed_build_says_why_and_keeps_the_last_good_site(http, made):
    name = made["name"]
    page(http, name, "good")
    http.post(f"/artifacts/{name}/build", params={"owner": "ada"})
    page(http, name, "FAIL")
    failed = http.post(f"/artifacts/{name}/build", params={"owner": "ada"}).json()
    assert not failed["ok"] and "The type check failed" in failed["error"] and "app/page.tsx(3,30)" in failed["error"]
    site = http.get(f"/artifacts/{name}/site", params={"owner": "ada"})
    with tarfile.open(fileobj=io.BytesIO(site.content)) as archive:
        assert b"good" in archive.extractfile("index.html").read()
    # Its last build failed, but it still has a site: the good one before
    shown = http.get(f"/artifacts/{name}", params={"owner": "ada"}).json()
    assert shown["build"]["ok"] is False and shown["site"] is True


def test_no_site_before_a_build(http, made):
    assert http.get(f"/artifacts/{made['name']}/site", params={"owner": "ada"}).status_code == 409
    assert http.get("/artifacts", params={"owner": "ada"}).json()[0]["site"] is False


def test_other_packages_are_refused_before_building(http, made, docker):
    name = made["name"]
    package = json.loads(http.get(f"/artifacts/{name}/files/package.json", params={"owner": "ada"}).json()["content"])
    package["dependencies"]["left-pad"] = "1.3.0"
    http.put(f"/artifacts/{name}/files/package.json", params={"owner": "ada"}, json={"content": json.dumps(package)})
    failed = http.post(f"/artifacts/{name}/build", params={"owner": "ada"}).json()
    assert not failed["ok"] and "only what's installed" in failed["error"].lower() and "recharts" in failed["error"]
    assert docker.artifact_builds == []
    # A missing package.json is put back
    http.delete(f"/artifacts/{name}/files/package.json", params={"owner": "ada"})
    assert http.post(f"/artifacts/{name}/build", params={"owner": "ada"}).json()["ok"]


def test_removing_it(http, made):
    name = made["name"]
    assert http.delete(f"/artifacts/{name}", params={"owner": "ada"}).status_code == 204
    assert not artifacts.folder(name).exists()
    assert http.get(f"/artifacts/{name}", params={"owner": "ada"}).status_code == 404


def test_a_limit_per_owner(http, monkeypatch):
    monkeypatch.setattr(artifacts, "MAX_PER_OWNER", 2)
    for _ in range(2):
        assert http.post("/artifacts", json={"owner": "ada", "title": "x"}).status_code == 201
    assert http.post("/artifacts", json={"owner": "ada", "title": "x"}).status_code == 409
    assert http.post("/artifacts", json={"owner": "bob", "title": "x"}).status_code == 201


def test_next_errors_are_read():
    typed = ("▲ Next.js 16.4.6 (Turbopack)\n  Creating an optimized production build ...\n✓ Compiled successfully in 2.3s\n"
             "  Running TypeScript ...\napp/page.tsx(4,64): error TS2820: Type '\"mnth\"' is not assignable to type "
             "'\"online\" | \"month\"'. Did you mean '\"month\"'?\nFailed to type check.\n")
    said = artifacts.next_error(typed, 1)
    assert said == ("The type check failed:\napp/page.tsx(4,64): error TS2820: Type '\"mnth\"' is not assignable to type "
                    "'\"online\" | \"month\"'. Did you mean '\"month\"'?")
    syntax = ("  Creating an optimized production build ...\n\n> Build error occurred\nError: Turbopack build failed with 1 error:\n"
              "./app/page.tsx:2:48\nError: Unexpected token.\n> 2 | export default function Page() { return <div>; }\n"
              "    |                                                ^\n\nParsing ecmascript source code failed\n\n\n    at ignore-listed frames\n")
    said = artifacts.next_error(syntax, 1)
    assert said.startswith("The build failed:\nError: Turbopack build failed") and "./app/page.tsx:2:48" in said
    assert "> 2 | export default" in said and "ignore-listed" not in said
    assert "exit code 2" in artifacts.next_error("something\nelse\n", 2)


def test_the_builders_tag_follows_the_template(docker, tmp_path, monkeypatch):
    folder = tmp_path / "artifacts"
    (folder / "template" / "node_modules").mkdir(parents=True)
    (folder / "Dockerfile").write_text("FROM node")
    (folder / "template" / "page.tsx").write_text("a")
    (folder / "template" / "node_modules" / "x.js").write_text("ignored")
    monkeypatch.setattr(spawner, "ARTIFACT_DIR", folder)
    first = spawner.artifact_image()
    (folder / "template" / "node_modules" / "x.js").write_text("still ignored")
    assert spawner.artifact_image() == first and len(docker.builds) == 1
    (folder / "template" / "page.tsx").write_text("b")
    assert spawner.artifact_image() != first
