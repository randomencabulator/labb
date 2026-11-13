"""Web pages the model builds (tools/artifacts.py, server/artifact_tools.py): offered where the server
turns them on, only the user's own, built by a stand-in spawner and published to a stand-in
labb-sites with its token."""

import json

import httpx
import pytest

from labb.server import app_tools, apps, artifact_tools
from labb.tools import artifacts as artifact_toolset
from labb.tools import offered_for

ME, SOMEONE = "user-1", "user-2"
NAME = "sales-k3f9qa"


@pytest.fixture
def services(monkeypatch):
    """A spawner with one artifact of ME's, and labb-sites: what each was asked"""
    files = {"AGENTS.md": "# This page\n", "app/page.tsx": '"use client";\nexport default function Page() {\n  return <h1>Hi</h1>;\n}\n',
             "package.json": "{}", "examples/dashboard.tsx": "x"}
    state = {"asked": [], "published": {}, "build": None, "made": []}

    def handle(request: httpx.Request) -> httpx.Response:
        state["asked"].append(request)
        if request.url.host == "sites-deploy":
            assert request.headers["authorization"] == "Bearer sites-secret"
            name = request.url.path.rsplit("/", 1)[-1]
            if request.method == "PUT":
                state["published"][name] = request.content
                return httpx.Response(200, json={"name": name})
            if request.method == "DELETE":
                return httpx.Response(200 if state["published"].pop(name, None) else 404, json={"deleted": name})
        assert request.headers["authorization"] == "Bearer spawner-secret"
        if request.url.params.get("owner") != ME:
            return httpx.Response(404, json={"detail": "No artifact"})
        parts = request.url.path.strip("/").split("/")
        if parts == ["artifacts"]:
            if request.method == "POST":
                body = json.loads(request.content)
                state["made"].append(body)
                return httpx.Response(201, json={"name": NAME, "owner": body["owner"], "thread": body["thread"], "title": body["title"]})
            thread = request.url.params.get("thread")
            listed = [{"name": NAME, "title": "Sales", "thread": "t1", "build": state["build"]}]
            return httpx.Response(200, json=[a for a in listed if thread in (None, a["thread"])])
        if parts[1] != NAME:
            return httpx.Response(404, json={"detail": "No artifact"})
        if len(parts) == 2 and request.method == "DELETE":
            state["deleted"] = True
            return httpx.Response(204)
        if parts[2] == "files":
            path = "/".join(parts[3:])
            if not path:
                return httpx.Response(200, json=[{"path": p, "size": len(c)} for p, c in sorted(files.items())])
            if request.method == "PUT":
                created = path not in files
                files[path] = json.loads(request.content)["content"]
                return httpx.Response(200, json={"path": path, "size": len(files[path]), "created": created})
            if request.method == "DELETE":
                del files[path]
                return httpx.Response(204)
            if path not in files:
                return httpx.Response(404, json={"detail": f"No file {path!r}"})
            return httpx.Response(200, json={"path": path, "content": files[path]})
        if parts[2] == "build":
            ok = "mnth" not in files["app/page.tsx"]
            state["build"] = {"ok": ok}
            error = None if ok else ("The type check failed:\napp/page.tsx(3,30): error TS2820: Type '\"mnth\"' is not "
                                     "assignable to type '\"month\"'. Did you mean '\"month\"'?")
            return httpx.Response(200, json={"ok": ok, "error": error, "log": "", "files": 3 if ok else 0})
        if parts[2] == "site":
            return httpx.Response(200, content=b"tar.gz of the site")
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(apps, "SPAWNER_URL", "http://spawner:4100")
    monkeypatch.setattr(apps, "SPAWNER_TOKEN", "spawner-secret")
    monkeypatch.setattr(artifact_tools, "SITES_URL", "http://sites-deploy:4200")
    monkeypatch.setattr(artifact_tools, "SITES_TOKEN", "sites-secret")
    monkeypatch.setattr(app_tools.httpx, "request", client.request)
    monkeypatch.setattr(artifact_toolset, "backend", artifact_tools.Backend())
    return {"files": files, "state": state}


def config(user=ME, thread="t1", **more):
    return {"configurable": {"user_id": user, "thread_id": thread, "artifacts": True,
                             "sites_base": "http://labb.lan:8300", **more}}


def use(tool, cfg=None, **args):
    return tool.invoke(args, cfg if cfg is not None else config())


def test_offered_only_where_the_server_turned_them_on():
    assert "artifacts" in offered_for(config())
    elsewhere = offered_for({"configurable": {"user_id": ME}})
    assert elsewhere is None or "artifacts" not in elsewhere
    # An agent has them only if it was given them
    assert "artifacts" not in offered_for(config(agent={"toolsets": ["web"]}))
    assert "artifacts" in offered_for(config(agent={"toolsets": ["web", "artifacts"]}))


def test_refused_where_theyre_off(services):
    said = artifact_toolset.list_artifacts.invoke({}, {"configurable": {"user_id": ME}})
    assert said == "Error: web pages can't be built here."


def test_making_one_names_it_and_says_what_to_do_next(services):
    said = use(artifact_toolset.new_artifact, title="Sales")
    assert f"'{NAME}'" in said and "AGENTS.md" in said and "app/page.tsx" in said and "publish_artifact" in said
    assert "examples/dashboard.tsx" not in said
    assert services["state"]["made"] == [{"owner": ME, "thread": "t1", "title": "Sales"}]


def test_reading_writing_and_editing(services):
    read = use(artifact_toolset.read_artifact_file, name=NAME, path="app/page.tsx")
    assert read.startswith("app/page.tsx (4 lines):") and "3|   return <h1>Hi</h1>;" in read
    assert use(artifact_toolset.write_artifact_file, name=NAME, path="data/sales.ts", content="1| export const a = 1").startswith("Made data/sales.ts")
    assert services["files"]["data/sales.ts"] == "export const a = 1"
    edited = use(artifact_toolset.edit_artifact_file, name=NAME, path="app/page.tsx", old_text="<h1>Hi</h1>", new_text="<h1>Sales</h1>")
    assert edited.startswith("Wrote app/page.tsx") and "<h1>Sales</h1>" in services["files"]["app/page.tsx"]
    missing = use(artifact_toolset.edit_artifact_file, name=NAME, path="app/page.tsx", old_text="<h2>Sales</h2>", new_text="x")
    assert "isn't in app/page.tsx" in missing and "closest part" in missing
    broken = use(artifact_toolset.write_artifact_file, name=NAME, path="data/rows.json", content="{nope")
    assert "syntax error" in broken and "not valid JSON" in broken
    assert use(artifact_toolset.delete_artifact_file, name=NAME, path="data/sales.ts") == "Deleted data/sales.ts."


def test_names_are_checked_before_asking(services):
    said = use(artifact_toolset.read_artifact_file, name="../apps", path="x")
    assert said.startswith("Error:") and "isn't an artifact's name" in said
    assert not services["state"]["asked"]


def test_publishing_builds_then_uploads_with_the_sites_token(services):
    said = use(artifact_toolset.publish_artifact, name=NAME)
    assert said.startswith(f"Published at http://labb.lan:8300/artifacts/{NAME}/")
    assert services["state"]["published"] == {NAME: b"tar.gz of the site"}
    listed = use(artifact_toolset.list_artifacts)
    assert listed == f"{NAME}: Sales (published at http://labb.lan:8300/artifacts/{NAME}/)"


def test_a_failed_build_shows_the_lines_and_publishes_nothing(services):
    services["files"]["app/page.tsx"] = '"use client";\nconst rows = [];\nexport default () => <Bar x="mnth" />;\n'
    said = use(artifact_toolset.publish_artifact, name=NAME)
    assert said.startswith("Error: it didn't build") and "error TS2820" in said
    assert "app/page.tsx around line 3:" in said and '3| export default () => <Bar x="mnth" />;' in said
    assert services["state"]["published"] == {}
    assert "its last build failed" in use(artifact_toolset.list_artifacts)


def test_someone_elses_are_out_of_reach(services):
    said = use(artifact_toolset.read_artifact_file, config(user=SOMEONE), name=NAME, path="app/page.tsx")
    assert said.startswith("Error:")
    assert use(artifact_toolset.list_artifacts, config(thread="other")) == "No web pages made in this chat yet: new_artifact makes one."


def test_a_deleted_chat_takes_its_pages_along(services):
    use(artifact_toolset.publish_artifact, name=NAME)
    artifact_tools.remove_all(ME, "t1")
    assert services["state"].get("deleted") and services["state"]["published"] == {}


def test_where_pages_open():
    assert artifact_tools.public_base("http", "labb.lan:8000") == "http://labb.lan:8300"
    assert artifact_tools.public_base("https", "labb.lan") == "https://labb.lan:8301"
    assert artifact_tools.public_base("http", "[::1]:8000") == "http://[::1]:8300"
    assert artifact_tools.public_base("http", "192.168.1.5:5173") == "http://192.168.1.5:8300"


def test_build_errors_name_their_place():
    found = artifact_tools.PLACE.search("./app/page.tsx:2:48\nError: Unexpected token")
    assert found and found.group(1) == "app/page.tsx" and found.group(3) == "2"
    found = artifact_tools.PLACE.search("components/charts/index.tsx(12,3): error TS2322")
    assert found and found.group(1) == "components/charts/index.tsx" and found.group(2) == "12"


def test_the_web_chat_turns_them_on_with_where_pages_open(client, fake_model, monkeypatch):
    from labb import model
    fake = fake_model(ai_reply("Hi."))
    asked = []
    monkeypatch.setattr(model, "model_with_tools", lambda *args, **kwargs: asked.append(args) or fake)
    monkeypatch.setattr(artifact_tools, "enabled", lambda: True)
    response = client.post("/chat", json={"message": "hello"}, headers={"host": "labb.lan:8000"})
    assert response.status_code == 200, response.text
    offered = next(arg for arg in asked[0] if isinstance(arg, frozenset))
    assert "artifacts" in offered
    # Off where they can't be built
    monkeypatch.setattr(artifact_tools, "enabled", lambda: False)
    asked.clear()
    client.post("/chat", json={"message": "hello"})
    assert not any(isinstance(arg, frozenset) and "artifacts" in arg for arg in asked[0])


def ai_reply(text):
    from langchain_core.messages import AIMessage
    return AIMessage(content=text)
