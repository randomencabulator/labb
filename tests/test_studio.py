"""Studio: templates, checks, the engine (branches, merging, parallel steps, pauses, retries, running
again), each kind of step, actions and secrets, triggers (schedule, webhook, events) and who can
see and change what. Models are fakes; the sandbox is replaced, and HTTP actions call a local server."""

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from labb import chats, memories, usage
from labb.server import app as server
from labb.server import auth, documents
from labb.server.studio import describe, engine, nodes, sandbox, steps, store, triggers
from labb.server.studio.template import TemplateError, render, render_text

from conftest import WordEmbeddings, ai


# --- Helpers -------------------------------------------------------------------------

def node(key: str, kind: str, **config) -> dict:
    return {"id": key, "key": key, "type": kind, "label": key.title(), "position": {"x": 0, "y": 0},
            "config": {**nodes.defaults(kind), **config}}


def flow(*items, inputs=None, trigger_config=None) -> dict:
    """A definition: nodes, then edges as (source, target) or (source, target, handle)"""

    start = node("trigger", "trigger", inputs=inputs or [], **(trigger_config or {}))
    found = [start] + [i for i in items if isinstance(i, dict)]
    edges = [{"id": f"e{n}", "source": e[0], "target": e[1], "handle": e[2] if len(e) > 2 else None}
             for n, e in enumerate(i for i in items if isinstance(i, tuple))]
    return {"nodes": found, "edges": edges}


def person() -> tuple[dict, TestClient]:
    user = auth.create_user(f"u{uuid4().hex[:10]}", "a-password")
    key, _ = auth.create_key(user["id"], "laptop")
    return user, TestClient(server.app, headers={"Authorization": f"Bearer {key}"})


@pytest.fixture
def alice(accounts):
    return person()


@pytest.fixture
def bob(accounts):
    return person()


def replies(monkeypatch, *texts):
    """Prompt steps answer with these, in order"""
    fake = GenericFakeChatModel(messages=iter([AIMessage(t, usage_metadata={"input_tokens": 10, "output_tokens": 5,
                                                                             "total_tokens": 15}) for t in texts]))
    monkeypatch.setattr(steps, "chat_model", lambda spec: fake)
    return fake


def make(client: TestClient, definition: dict, name: str = "Test flow", publish: bool = False) -> dict:
    workflow = client.post("/studio/workflows", json={"name": name, "draft": definition}).json()
    if publish:
        published = client.post(f"/studio/workflows/{workflow['id']}/publish")
        assert published.status_code == 200, published.text
        workflow = published.json()
    return workflow


def finished(run_id: str, timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = store.get_run(run_id)
        if run["status"] not in ("queued", "running"):
            return engine.view(run)
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} still {store.get_run(run_id)['status']}")


def run(client: TestClient, workflow: dict, inputs: dict | None = None, draft: bool = True) -> dict:
    started = client.post(f"/studio/workflows/{workflow['id']}/runs", json={"inputs": inputs or {}, "draft": draft})
    assert started.status_code == 202, started.text
    return finished(started.json()["id"])


def step_of(run: dict, key: str) -> dict:
    return next(s for s in run["steps"] if s["key"] == key)


# --- Templates and checks ------------------------------------------------------------

def test_templates_keep_types_and_fill_text():
    context = {"trigger": {"topic": "bees", "n": 3}, "list": ["a", "b"], "obj": '{"title": "Hi"}'}
    assert render("{{list}}", context) == ["a", "b"]
    assert render("{{ trigger.n }}", context) == 3
    assert render_text("About {{trigger.topic}}: {{list | json}}", context) == 'About bees: ["a", "b"]'
    assert render("{{obj.title}}", context) == "Hi"
    assert render("{{list.1 | upper}}", context) == "B"
    assert render({"q": ["{{trigger.topic}}"]}, context) == {"q": ["bees"]}
    with pytest.raises(TemplateError, match="no step called nope"):
        render("{{nope}}", context)
    with pytest.raises(TemplateError, match="trigger has no color"):
        render("{{trigger.color}}", context)


def test_checks_say_whats_wrong():
    good = flow(node("a", "transform", template="{{trigger.x}}"), ("trigger", "a"), inputs=[{"name": "x"}])
    assert nodes.check(good) == []
    messages = lambda d: [p["message"] for p in nodes.check(d)]
    assert any("called y" in m for m in messages(flow(node("a", "transform", template="{{trigger.y}}"), ("trigger", "a"))))
    assert any("comes after" in m for m in messages(flow(
        node("a", "transform", template="{{b}}"), node("b", "transform", template="x"), ("trigger", "a"), ("a", "b"))))
    assert any("circle" in m for m in messages(flow(
        node("a", "transform", template="x"), node("b", "transform", template="x"), ("trigger", "a"), ("a", "b"), ("b", "a"))))
    assert any("Nothing leads" in m for m in messages(flow(node("a", "transform", template="x"))))
    assert any("no way out called maybe" in m for m in messages(flow(
        node("c", "condition"), node("a", "transform", template="x"), ("trigger", "c"), ("c", "a", "maybe"))))
    assert any("Two steps" in m for m in messages({"nodes": [node("trigger", "trigger"), node("a", "merge"),
                                                             {**node("a", "merge"), "id": "a2"}], "edges": []}))
    assert any("Secrets are only" in m for m in messages(flow(node("a", "transform", template="{{secrets.X}}"), ("trigger", "a"))))
    assert any("Message is missing" in m for m in messages(flow(node("a", "agent", prompt=""), ("trigger", "a"))))


# --- The engine ----------------------------------------------------------------------

def test_a_run_goes_step_by_step_and_keeps_tokens(alice, monkeypatch):
    user, client = alice
    replies(monkeypatch, "Bees pollinate flowers.", '{"title": "Bees", "words": 3}')
    workflow = make(client, flow(
        node("facts", "prompt", prompt="Tell me about {{trigger.topic}}"),
        node("card", "prompt", prompt="Title for: {{facts}}", format="json", fields=[{"name": "title"}, {"name": "words"}]),
        node("result", "respond", value="{{card.title}}: {{facts}}"),
        ("trigger", "facts"), ("facts", "card"), ("card", "result"),
        inputs=[{"name": "topic", "required": True}]))
    done = run(client, workflow, {"topic": "bees"})
    assert done["status"] == "done", done
    assert done["result"] == "Bees: Bees pollinate flowers."
    assert step_of(done, "card")["output"] == {"title": "Bees", "words": 3}
    assert step_of(done, "facts")["input"] == "Tell me about bees"
    assert done["input_tokens"] == 20 and done["output_tokens"] == 10
    summary = usage.summary(user["id"])
    assert any(s["source"] == "workflow" for s in summary["by_source"])
    missing = client.post(f"/studio/workflows/{workflow['id']}/runs", json={"inputs": {}, "draft": True})
    assert missing.status_code == 400 and "needed" in missing.json()["detail"]


def test_branches_not_taken_are_skipped_and_merges_wait_only_for_the_one_that_ran(alice):
    _, client = alice
    workflow = make(client, flow(
        node("check", "condition", rules=[{"left": "{{trigger.n}}", "op": "greater", "right": "5"}]),
        node("big", "transform", template="big"), node("small", "transform", template="small"),
        node("after", "transform", template="{{big}}{{small}} done"),
        ("trigger", "check"), ("check", "big", "true"), ("check", "small", "false"), ("big", "after"), ("small", "after"),
        inputs=[{"name": "n", "type": "number"}]))
    done = run(client, workflow, {"n": "9"})
    assert done["status"] == "done" and done["result"] == "big done"
    assert step_of(done, "small")["status"] == "skipped" and step_of(done, "check")["handle"] == "true"
    done = run(client, workflow, {"n": 2})
    assert done["result"] == "small done" and step_of(done, "big")["status"] == "skipped"


def test_independent_steps_run_together_and_merge(alice, monkeypatch):
    _, client = alice
    started = []

    def slow(code, inputs, timeout=30):
        started.append(time.monotonic())
        time.sleep(0.4)
        return {"result": code, "stdout": ""}

    monkeypatch.setattr(sandbox, "run", slow)
    monkeypatch.setattr(sandbox, "available", lambda: True)
    workflow = make(client, flow(
        node("one", "python", code="1"), node("two", "python", code="2"), node("both", "merge"),
        ("trigger", "one"), ("trigger", "two"), ("one", "both"), ("two", "both")))
    done = run(client, workflow)
    assert done["status"] == "done" and done["result"] == {"one": "1", "two": "2"}
    assert abs(started[0] - started[1]) < 0.3


def test_an_approval_pauses_the_run_until_it_is_answered(alice):
    user, client = alice
    workflow = make(client, flow(
        node("ask", "approval", message="Send {{trigger.what}}?"),
        node("yes", "transform", template="sent"), node("no", "transform", template="kept"),
        ("trigger", "ask"), ("ask", "yes", "approved"), ("ask", "no", "rejected"), inputs=[{"name": "what"}]))
    waiting = run(client, workflow, {"what": "the report"})
    assert waiting["status"] == "waiting" and step_of(waiting, "ask")["input"]["message"] == "Send the report?"
    pending = client.get("/studio/waiting").json()
    assert [p["message"] for p in pending if p["run_id"] == waiting["id"]] == ["Send the report?"]
    answered = client.post(f"/studio/runs/{waiting['id']}/approve", json={"node_id": "ask", "approved": False, "comment": "not yet"})
    assert answered.status_code == 200
    done = finished(waiting["id"])
    assert done["status"] == "done" and done["result"] == "kept"
    assert step_of(done, "ask")["output"] == {"approved": False, "comment": "not yet", "by": user["username"]}
    assert client.post(f"/studio/runs/{waiting['id']}/approve", json={"node_id": "ask", "approved": True}).status_code == 400


def test_a_wait_goes_on_when_its_time_comes(alice):
    _, client = alice
    workflow = make(client, flow(node("pause", "wait", minutes=5), node("after", "transform", template="later"),
                                 ("trigger", "pause"), ("pause", "after")))
    waiting = run(client, workflow)
    assert waiting["status"] == "waiting" and waiting["resume_at"]
    assert engine.resume_waits(datetime.now(timezone.utc).isoformat()) == 0
    later = (datetime.now(timezone.utc) + timedelta(minutes=6)).isoformat()
    assert engine.resume_waits(later) >= 1
    done = finished(waiting["id"])
    assert done["result"] == "later", (done["status"], done["error"], [(s["key"], s["status"], s["output"]) for s in done["steps"]])


def test_retries_and_going_on_after_an_error(alice, monkeypatch):
    _, client = alice
    calls = {"n": 0}

    def flaky(code, inputs, timeout=30):
        calls["n"] += 1
        if code == "always" or calls["n"] < 2:
            raise sandbox.SandboxError("boom")
        return {"result": "ok", "stdout": ""}

    monkeypatch.setattr(sandbox, "run", flaky)
    monkeypatch.setattr(sandbox, "available", lambda: True)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    retried = {**node("flaky", "python", code="x"), "retries": 2}
    ignored = {**node("broken", "python", code="always"), "on_error": "continue"}
    workflow = make(client, flow(retried, ignored, node("end", "transform", template="{{flaky}} {{broken}}"),
                                 ("trigger", "flaky"), ("flaky", "broken"), ("broken", "end")))
    done = run(client, workflow)
    assert done["status"] == "done" and done["result"] == "ok "
    assert step_of(done, "flaky")["attempt"] == 1 and step_of(done, "broken")["status"] == "failed"

    failing = make(client, flow(node("broken", "python", code="always"), node("end", "transform", template="x"),
                                ("trigger", "broken"), ("broken", "end")))
    failed = run(client, failing)
    assert failed["status"] == "failed" and "boom" in failed["error"]
    assert step_of(failed, "end")["status"] == "skipped" if any(s["key"] == "end" for s in failed["steps"]) else True


def test_running_again_from_a_step_keeps_what_came_before(alice, monkeypatch):
    _, client = alice
    replies(monkeypatch, "first draft", "second draft")
    workflow = make(client, flow(node("draft", "prompt", prompt="Write"), node("upper", "transform", template="{{draft | upper}}"),
                                 ("trigger", "draft"), ("draft", "upper")))
    first = run(client, workflow)
    again = client.post(f"/studio/runs/{first['id']}/rerun", json={"node_id": "upper"}).json()
    done = finished(again["id"])
    assert done["result"] == "FIRST DRAFT" and done["trigger"] == "rerun"
    again = client.post(f"/studio/runs/{first['id']}/rerun", json={"node_id": "draft"}).json()
    assert finished(again["id"])["result"] == "SECOND DRAFT"


def test_a_restart_fails_the_running_step_so_it_can_be_run_again(alice):
    _, client = alice
    workflow = make(client, flow(node("a", "transform", template="x"), ("trigger", "a")))
    run_id = store.insert_run(workflow["id"], alice[0]["id"], None, workflow["draft"], "manual", {})
    store.update_run(run_id, status="running")
    store.save_step(run_id, "a", "a", status="running", started_at=store.now())
    engine.recover()
    cut = engine.view(store.get_run(run_id))
    assert cut["status"] == "failed" and step_of(cut, "a")["error"] == engine.RESTARTED


def test_stopping_a_run(alice):
    _, client = alice
    workflow = make(client, flow(node("pause", "wait", minutes=60), ("trigger", "pause")))
    waiting = run(client, workflow)
    assert client.post(f"/studio/runs/{waiting['id']}/stop").json() == {"stopped": True}
    assert store.get_run(waiting["id"])["status"] == "stopped"


# --- Kinds of steps ------------------------------------------------------------------

def test_agent_and_skill_steps_reply_like_a_chat(alice, fake_model):
    _, client = alice
    fake_model(ai("Hello from the agent", tokens=(30, 7)))
    workflow = make(client, flow(node("hi", "agent", prompt="Say hi to {{trigger.name}}"), ("trigger", "hi"),
                                 inputs=[{"name": "name"}]))
    done = run(client, workflow, {"name": "Robin"})
    assert done["status"] == "done", done
    assert done["result"] == "Hello from the agent" and step_of(done, "hi")["input_tokens"] == 30
    gone = make(client, flow(node("hi", "agent", prompt="x", agent_id="nope"), ("trigger", "hi")))
    assert client.post(f"/studio/workflows/{gone['id']}/runs", json={"draft": True}).status_code == 400


def test_condition_by_model_and_foreach(alice, monkeypatch):
    _, client = alice
    replies(monkeypatch, "Positive.", "A", "B", "C")
    workflow = make(client, flow(
        node("mood", "condition", mode="model", question="Is '{{trigger.text}}' positive?", choices=["positive", "negative"]),
        node("each", "foreach", items="{{trigger.list}}", step_type="prompt", step={"prompt": "Letter for {{item}} at {{index}}"}),
        ("trigger", "mood"), ("mood", "each", "positive"), inputs=[{"name": "text"}, {"name": "list"}]))
    done = run(client, workflow, {"text": "great", "list": "x\ny\nz"})
    assert done["status"] == "done", done
    assert step_of(done, "mood")["handle"] == "positive" and done["result"] == ["A", "B", "C"]


def test_tool_python_transform_chat_and_document_steps(alice, monkeypatch):
    user, client = alice
    monkeypatch.setattr(memories, "on_server", True)
    monkeypatch.setattr(memories, "index", None)
    monkeypatch.setattr(sandbox, "available", lambda: True)
    monkeypatch.setattr(sandbox, "run", lambda code, inputs, timeout=30: {"result": inputs["trigger"]["fact"].upper(), "stdout": "hi\n"})
    monkeypatch.setattr(documents, "_store", documents.vectors.Store("documents", WordEmbeddings(), "test"))
    workflow = make(client, flow(
        node("save", "tool", tool="remember", args={"text": "{{trigger.fact}}"}),
        node("shout", "python", code="result = ..."),
        node("json", "transform", template='{"fact": {{shout | json}}}', format="json"),
        node("post", "chat", title="Fact", message="{{json.fact}}"),
        node("file", "document", name="fact.md", content="# {{json.fact}}"),
        ("trigger", "save"), ("save", "shout"), ("shout", "json"), ("json", "post"), ("json", "file"),
        inputs=[{"name": "fact"}]))
    done = run(client, workflow, {"fact": "Likes tea"})
    assert done["status"] == "done", done
    assert [m["text"] for m in memories.list_for(user["id"])] == ["Likes tea"]
    assert step_of(done, "json")["output"] == {"fact": "LIKES TEA"} and step_of(done, "shout")["trace"] == [{"stdout": "hi\n"}]
    thread = step_of(done, "post")["output"]["thread_id"]
    listed = next(c for c in chats.list_chats(user["id"]) if c["thread_id"] == thread)
    assert listed["unread"] and listed["title"] == "Fact"
    assert [d["name"] for d in documents.list_documents(user["id"])] == ["fact.md"]


def test_a_tool_step_saves_a_document_without_starting_workflows(alice, monkeypatch):
    user, client = alice
    monkeypatch.setattr(documents, "_store", documents.vectors.Store("documents", WordEmbeddings(), "test"))
    added = []
    monkeypatch.setattr(documents, "on_added", [lambda user_id, doc: added.append(doc["name"])])
    assert "save_document" in [t["name"] for t in client.get("/studio/catalog").json()["tools"]]
    workflow = make(client, flow(
        node("keep", "tool", tool="save_document", args={"name": "Note {{trigger.topic}}", "content": "About {{trigger.topic}}"}),
        ("trigger", "keep"), inputs=[{"name": "topic"}]))
    done = run(client, workflow, {"topic": "tea"})
    assert done["status"] == "done", done
    assert step_of(done, "keep")["output"].startswith("Saved [Note tea.md](/documents/")
    assert "Note tea.md" in [d["name"] for d in documents.list_documents(user["id"])] and added == []


def test_python_without_a_sandbox_says_so(alice, monkeypatch):
    _, client = alice
    monkeypatch.setattr(sandbox, "SANDBOX_URL", "")
    workflow = make(client, flow(node("code", "python", code="result = 1"), ("trigger", "code")))
    full = client.get(f"/studio/workflows/{workflow['id']}").json()
    assert any("sandbox" in p["message"] for p in full["problems"])


# --- Actions and secrets -------------------------------------------------------------

class Echo(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        Echo.seen.append({"auth": self.headers.get("Authorization"), "body": body})
        payload = json.dumps({"ok": True, "got": json.loads(body)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def echo_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    Echo.seen.clear()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def http_action(url: str, allow_private: bool = True) -> dict:
    return {"kind": "http", "name": "Create item", "description": "Adds an item",
            "config": {"method": "POST", "url": url + "/items", "headers": {"Authorization": "Bearer {{secrets.ITEM_TOKEN}}"},
                       "body": '{"title": "{{title}}"}', "allow_private": allow_private,
                       "inputs": [{"name": "title", "required": True}]}}


def test_http_actions_send_requests_with_secrets_masked(client, alice, echo_server):
    _, member = alice
    assert client.put("/studio/secrets/ITEM_TOKEN", json={"value": "s3cret-token"}).status_code == 200
    assert client.get("/studio/secrets").json()[0]["name"] == "ITEM_TOKEN" and "value" not in client.get("/studio/secrets").json()[0]
    assert member.get("/studio/secrets").status_code == 403
    assert member.post("/studio/actions", json=http_action(echo_server)).status_code == 400
    action = client.post("/studio/actions", json=http_action(echo_server)).json()
    assert "url" not in [a for a in member.get("/studio/actions").json() if a["id"] == action["id"]][0]["config"]

    workflow = make(member, flow(node("add", "action", action_id=action["id"], args={"title": "{{trigger.title}}"}),
                                 ("trigger", "add"), inputs=[{"name": "title"}]))
    done = run(member, workflow, {"title": "Milk"})
    assert done["status"] == "done", done
    assert Echo.seen[-1] == {"auth": "Bearer s3cret-token", "body": '{"title": "Milk"}'}
    assert done["result"] == {"status": 200, "body": {"ok": True, "got": {"title": "Milk"}}}
    recorded = json.dumps(step_of(done, "add")["input"], ensure_ascii=False)
    assert "s3cret-token" not in recorded and "••••••" in recorded

    private = client.post("/studio/actions", json=http_action(echo_server, allow_private=False)).json()
    tried = client.post(f"/studio/actions/{private['id']}/test", json={"args": {"title": "x"}}).json()
    assert not tried["ok"] and "private or local" in tried["error"]
    assert client.post(f"/studio/actions/{action['id']}/test", json={"args": {}}).json()["error"] == "Create item needs title"
    store.delete_secret("ITEM_TOKEN")


def test_prompt_actions_are_anyones(alice, bob, monkeypatch):
    _, client = alice
    replies(monkeypatch, "Short.")
    action = client.post("/studio/actions", json={"kind": "prompt", "name": "Shorten", "config": {
        "prompt": "Shorten: {{text}}", "inputs": [{"name": "text", "required": True}]}}).json()
    workflow = make(client, flow(node("short", "action", action_id=action["id"], args={"text": "a long text"}), ("trigger", "short")))
    assert run(client, workflow)["result"] == "Short."
    assert bob[1].patch(f"/studio/actions/{action['id']}", json={"name": "Mine"}).status_code == 404
    assert all(a["id"] != action["id"] for a in bob[1].get("/studio/actions").json())


# --- Triggers ------------------------------------------------------------------------

def test_webhooks_start_the_published_version(alice):
    _, client = alice
    definition = flow(node("echo", "respond", value="Got {{trigger.text}}"), ("trigger", "echo"),
                      inputs=[{"name": "text"}], trigger_config={"webhook": True})
    workflow = make(client, definition)
    path = client.post(f"/studio/workflows/{workflow['id']}/webhook").json()["path"]
    outside = TestClient(server.app)
    assert outside.post(path, json={"text": "hi"}).status_code == 404  # not published yet
    client.post(f"/studio/workflows/{workflow['id']}/publish")
    answered = outside.post(path + "?wait=10", json={"text": "hi"})
    assert answered.status_code == 200 and answered.json()["result"] == "Got hi"
    assert outside.post("/hooks/wh_nope", json={}).status_code == 404
    client.post(f"/studio/workflows/{workflow['id']}/webhook?on=false")
    assert outside.post(path, json={"text": "hi"}).status_code == 404


def test_schedules_and_events_start_runs(alice, monkeypatch):
    user, client = alice
    monkeypatch.setattr(documents, "_store", documents.vectors.Store("documents", WordEmbeddings(), "test"))
    scheduled = make(client, flow(node("tick", "transform", template="tick"), ("trigger", "tick"),
                                  trigger_config={"schedule": {"kind": "minutes", "every": 30}}), publish=True)
    assert scheduled["next_run"]
    later = datetime.fromisoformat(scheduled["next_run"]) + timedelta(minutes=1)
    started = triggers.due(later)
    assert len(started) == 1 and finished(started[0])["result"] == "tick"
    assert store.get_workflow(scheduled["id"])["next_run"] > scheduled["next_run"]

    on_add = make(client, flow(node("seen", "document", name="copy of {{trigger.event.document.name}}", content="x"),
                               ("trigger", "seen"), trigger_config={"events": ["document.added"]}), publish=True)
    documents.add(user["id"], "notes.md", b"# Notes")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not any(r["status"] == "done" for r in store.list_runs(on_add["id"])):
        time.sleep(0.05)
    names = sorted(d["name"] for d in documents.list_documents(user["id"]))
    assert names == ["copy of notes.md", "notes.md"]
    # The workflow's own document didn't start it again
    assert len(store.list_runs(on_add["id"])) == 1


# --- Who sees what -------------------------------------------------------------------

def test_workflows_are_private_unless_shared(alice, bob):
    _, mine = alice
    _, theirs = bob
    workflow = make(mine, flow(node("a", "transform", template="x"), ("trigger", "a")), publish=True)
    assert theirs.get(f"/studio/workflows/{workflow['id']}").status_code == 404
    mine.patch(f"/studio/workflows/{workflow['id']}", json={"shared": True})
    assert theirs.get(f"/studio/workflows/{workflow['id']}").json()["mine"] is False
    assert theirs.patch(f"/studio/workflows/{workflow['id']}", json={"name": "Taken"}).status_code == 404
    started = theirs.post(f"/studio/workflows/{workflow['id']}/runs", json={}).json()
    assert finished(started["id"])["status"] == "done"
    assert theirs.post(f"/studio/workflows/{workflow['id']}/runs", json={"draft": True}).status_code == 404
    copy = theirs.post(f"/studio/workflows/{workflow['id']}/duplicate").json()
    assert copy["mine"] and copy["name"] == "Test flow (copy)"
    mine_run = run(mine, workflow, draft=False)
    assert theirs.get(f"/studio/runs/{mine_run['id']}").status_code == 404
    assert mine.get(f"/studio/runs/{started['id']}").status_code == 200


def test_publishing_checks_and_versions(alice):
    _, client = alice
    workflow = make(client, flow(node("a", "transform", template="{{missing}}"), ("trigger", "a")))
    assert client.post(f"/studio/workflows/{workflow['id']}/publish").status_code == 400
    assert client.post(f"/studio/workflows/{workflow['id']}/runs", json={}).status_code == 400
    client.patch(f"/studio/workflows/{workflow['id']}", json={"draft": flow(node("a", "transform", template="v1"), ("trigger", "a"))})
    assert client.post(f"/studio/workflows/{workflow['id']}/publish").json()["published_version"] == 1
    client.patch(f"/studio/workflows/{workflow['id']}", json={"draft": flow(node("a", "transform", template="v2"), ("trigger", "a"))})
    assert run(client, workflow, draft=False)["result"] == "v1"
    assert run(client, workflow, draft=True)["result"] == "v2"


def test_templates_and_export(alice):
    _, client = alice
    for template in client.get("/studio/templates").json():
        made = client.post("/studio/workflows", json={"template": template["id"]}).json()
        assert [p for p in made["problems"] if "sandbox" not in p["message"]] == [], (template["id"], made["problems"])
    exported = client.get(f"/studio/workflows/{made['id']}/export").json()
    imported = client.post("/studio/import", json=exported).json()
    assert imported["draft"] == made["draft"] and imported["name"] == made["name"]


def test_describe_turns_a_models_answer_into_a_workflow(alice, monkeypatch):
    user, client = alice
    answer = {"name": "Digest", "emoji": "📰", "nodes": [
        {"key": "Start", "type": "trigger", "config": {"inputs": [{"name": "topic"}]}},
        {"key": "find", "type": "tool", "label": "Find", "config": {"tool": "web_search", "args": {"query": "{{trigger.topic}}"}}},
        {"key": "sum", "type": "prompt", "config": {"prompt": "Summarize {{find}}"}},
        {"key": "x", "type": "nonsense"}],
        "edges": [{"source": "Start", "target": "find"}, {"source": "find", "target": "sum"}, {"source": "sum", "target": "ghost"}]}
    replies(monkeypatch, json.dumps(answer))
    drafted = client.post("/studio/describe", json={"description": "A digest of a topic"}).json()
    assert drafted["name"] == "Digest" and drafted["problems"] == []
    definition = drafted["definition"]
    assert [n["key"] for n in definition["nodes"]] == ["trigger", "find", "sum"]
    assert [n["position"]["x"] for n in definition["nodes"]] == [0, 280, 560]
    assert describe.normalize({"nodes": []})["nodes"][0]["type"] == "trigger"


def test_describe_cleans_the_settings_models_make_up(alice, monkeypatch):
    user, client = alice
    researcher = client.post("/agents", json={"name": "Researcher"}).json()
    choices = describe.Choices(agents=[{"id": researcher["id"], "name": "Researcher", "description": ""}],
                               models=["ollama:beam:cloud", "openai:gpt-4o"], tools=describe.tool_args(), tz="Europe/Berlin")
    answer = {"nodes": [
        {"type": "trigger", "key": "trigger", "config": {"inputs": [{"name": "urls", "type": "list"}, "topic"],
                                                         "schedule": "30 7 * * 1-5", "events": ["document_added"]}},
        {"type": "agent", "key": "plain", "config": {"agent_id": "labb", "prompt": "On {{trigger.topic}}", "model": "default"}},
        {"type": "agent", "key": "named", "config": {"agent_id": "researcher", "prompt": "Hi", "model": "gpt-4o"}},
        {"type": "condition", "key": "ask", "config": {"mode": "ai", "question": "Good?", "choices": "yes, no", "model": "gpt-9"}},
        {"type": "condition", "key": "rule", "config": {"rules": [{"left": "{{named}}", "op": "==", "right": "x"}], "match": "OR"}},
        {"type": "prompt", "key": "extract", "config": {"prompt": {"text": 1}, "fields": [{"name": "total", "type": "string"}], "bogus": 1}},
        {"type": "foreach", "key": "each", "config": {"items": "{{trigger.urls}}", "step_type": "tool", "parallel": "true", "max": "100",
                                                      "step": {"type": "tool", "config": {"tool": "fetch_page", "args": {"url": "{{item}}", "junk": 1}}}}},
        {"type": "wait", "key": "later", "config": {"minutes": "1440"}}]}
    config = {n["key"]: n["config"] for n in describe.normalize(answer, choices)["nodes"]}
    assert config["trigger"] == {"webhook": False, "events": ["document.added"], "tz": "Europe/Berlin",
                                 "schedule": {"kind": "weekdays", "time": "07:30"},
                                 "inputs": [{"name": "urls", "label": "Urls", "type": "long_text", "required": False},
                                            {"name": "topic", "label": "Topic", "type": "text", "required": False}]}
    # The default agent and model are no setting at all
    assert config["plain"] == {"prompt": "On {{trigger.topic}}"}
    assert config["named"] == {"agent_id": researcher["id"], "prompt": "Hi", "model": "openai:gpt-4o"}
    assert config["ask"]["mode"] == "model" and config["ask"]["choices"] == ["yes", "no"] and "model" not in config["ask"]
    assert config["rule"]["rules"] == [{"left": "{{named}}", "op": "equals", "right": "x"}] and config["rule"]["match"] == "any"
    assert config["extract"] == {"format": "json", "prompt": '{"text": 1}', "fields": [{"name": "total", "description": "string"}]}
    assert config["each"] == {"items": "{{trigger.urls}}", "step_type": "tool", "parallel": True, "max": 50,
                              "step": {"tool": "fetch_page", "args": {"url": "{{item}}"}}}
    assert config["later"] == {"minutes": 1440}

    # What the model is told: each setting's form, and what the person has
    prompt = describe.system_prompt(choices)
    assert f'id "{researcher["id"]}": Researcher' in prompt and '"ollama:beam:cloud"' in prompt
    assert '"rules" (Rules)' in prompt and "save_document(name, content, replace)" in prompt and "{{trigger.event.document.name}}" in prompt


def test_the_sandbox_token_comes_from_its_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "SANDBOX_TOKEN", "")
    monkeypatch.setattr(sandbox, "SANDBOX_TOKEN_FILE", str(tmp_path / "token"))
    # The sandbox hasn't made it yet
    assert sandbox.token() == ""
    (tmp_path / "token").write_text("made-by-the-sandbox\n")
    assert sandbox.token() == "made-by-the-sandbox"
    monkeypatch.setattr(sandbox, "SANDBOX_TOKEN", "from-env")
    assert sandbox.token() == "from-env"


class FakeSpawner:
    """The spawner's artifacts and labb-sites, where the server calls them (app_tools.call, artifact_tools.sites)"""

    def __init__(self, fail_build=False):
        self.made: dict[str, dict] = {}
        self.published: list[str] = []
        self.fail_build = fail_build

    def call(self, method, path, user_id, timeout=60, **kwargs):
        from types import SimpleNamespace
        answer = lambda body=None, content=b"": SimpleNamespace(json=lambda: body, content=content)
        params = kwargs.get("params") or {}
        if path == "/artifacts" and method == "POST":
            body = kwargs["json"]
            name = f"{body['title'].lower().replace(' ', '-')}-a1b2c3"
            self.made[name] = {"name": name, "owner": user_id, "thread": body["thread"], "title": body["title"], "site": False,
                               "build": None, "created": f"2026-11-12T10:0{len(self.made)}:00+00:00"}
            return answer({"name": name})
        if path == "/artifacts":
            found = [a for a in self.made.values() if a["owner"] == user_id and params.get("thread") in (None, a["thread"])]
            return answer(sorted(found, key=lambda a: a["created"], reverse=True))
        name = path.split("/")[2]
        if name not in self.made or self.made[name]["owner"] != user_id:
            raise ValueError(f"No artifact {name!r}")
        if path.endswith("/build"):
            ok = not self.fail_build
            self.made[name]["build"] = {"ok": ok, "error": None if ok else "Type error in app/page.tsx", "at": "now"}
            self.made[name]["site"] = self.made[name]["site"] or ok
            return answer({"ok": ok, "error": self.made[name]["build"]["error"], "log": "", "files": 3})
        if path.endswith("/site"):
            return answer(content=b"tar")
        if "/files" in path:
            return answer([{"path": "app/page.tsx", "size": 10}] if path.endswith("/files") else {"size": 10, "created": False})
        return answer(self.made[name])


@pytest.fixture
def spawner(monkeypatch):
    from labb.server import app_tools, artifact_tools
    from labb.tools import artifacts as artifact_toolset
    fake = FakeSpawner()
    monkeypatch.setattr(artifact_tools, "enabled", lambda: True)
    monkeypatch.setattr(artifact_toolset, "backend", artifact_tools.Backend())
    monkeypatch.setattr(app_tools, "call", fake.call)
    monkeypatch.setattr(artifact_tools, "sites", lambda method, path, **kwargs: fake.published.append(path))
    monkeypatch.setattr(artifact_tools, "SITES_PUBLIC_URL", "https://pages.example.com")
    return fake


def builds_a_page(title: str, name: str) -> list:
    return [ai("", [("new_artifact", {"title": title})]),
            ai("", [("write_artifact_file", {"name": name, "path": "app/page.tsx", "content": "export default () => null"})]),
            ai("", [("publish_artifact", {"name": name})]),
            ai(f"Published at https://pages.example.com/artifacts/{name}/")]


def test_a_web_page_step_builds_and_publishes_a_page_for_the_next_steps(alice, fake_model, spawner):
    _, client = alice
    fake_model(*builds_a_page("Sales", "sales-a1b2c3"))
    definition = flow(node("page", "page", prompt="A bar chart of {{trigger.numbers}}", title="Sales"),
                      node("tell", "chat", title="Your page", message="It's at {{page.url}}"),
                      ("trigger", "page"), ("page", "tell"), inputs=[{"name": "numbers"}])
    workflow = make(client, definition)
    done = run(client, workflow, {"numbers": "3, 5, 8"})
    assert done["status"] == "done", done
    page = step_of(done, "page")["output"]
    assert page == {"name": "sales-a1b2c3", "title": "Sales", "url": "https://pages.example.com/artifacts/sales-a1b2c3/",
                    "updated": False}
    assert spawner.published == ["/artifacts/sales-a1b2c3"]
    assert step_of(done, "page")["input"] == "A bar chart of 3, 5, 8"
    # The Artifacts page says a workflow made it
    listed = client.get("/artifacts").json()["artifacts"]
    assert listed[0]["workflow"] == "Test flow"


def test_keeping_one_page_updates_it_each_run(alice, fake_model, spawner):
    _, client = alice
    workflow = make(client, flow(node("page", "page", prompt="Today's numbers", title="Daily", same_page=True), ("trigger", "page")))
    fake_model(*builds_a_page("Daily", "daily-a1b2c3"))
    assert run(client, workflow)["status"] == "done"
    # The second run changes the page it made: no new one, the same link
    fake_model(ai("", [("write_artifact_file", {"name": "daily-a1b2c3", "path": "app/page.tsx", "content": "v2"})]),
                      ai("", [("publish_artifact", {"name": "daily-a1b2c3"})]), ai("Published."))
    second = run(client, workflow)
    assert second["status"] == "done", second
    assert step_of(second, "page")["output"]["updated"] is True and list(spawner.made) == ["daily-a1b2c3"]
    asked = steps.chat_agent.get_state({"configurable": {"thread_id": f"wf-{second['id']}-page"}}).values["messages"][0].content
    assert asked.startswith("Change the web page 'daily-a1b2c3'") and "Today's numbers" in asked


def test_a_page_that_doesnt_build_fails_the_step_with_why(alice, fake_model, spawner):
    _, client = alice
    spawner.fail_build = True
    fake_model(*builds_a_page("Broken", "broken-a1b2c3")[:3], ai("It didn't build."))
    workflow = make(client, flow(node("page", "page", prompt="Something"), ("trigger", "page")))
    failed = run(client, workflow)
    assert failed["status"] == "failed"
    assert "didn't publish" in step_of(failed, "page")["error"] and "Type error" in step_of(failed, "page")["error"]
