"""The drafting room, changes in words, fixes for failed runs, explanations and trying a draft"""

import json

import pytest

from labb.server.studio import assist, describe, nodes, store

from test_studio import finished, flow, make, node, person, replies


@pytest.fixture
def alice(accounts):
    return person()


def digest() -> dict:
    definition = flow(node("find", "transform", template="news"), node("sum", "transform", template="sum of {{find}}"),
                      node("post", "transform", template="post {{sum}}"),
                      ("trigger", "find"), ("find", "sum"), ("sum", "post"),
                      trigger_config={"schedule": {"kind": "weekdays", "time": "08:00"}})
    for i, n in enumerate(definition["nodes"]):
        n["position"] = {"x": i * describe.X_STEP, "y": 0}
    return definition


def edges_of(definition: dict) -> set[tuple]:
    keys = {n["id"]: n["key"] for n in definition["nodes"]}
    return {(keys[e["source"]], keys[e["target"]], e["handle"]) for e in definition["edges"]}


CHOICES = describe.Choices(tools=describe.tool_args())


def test_edits_change_only_what_they_name():
    before = digest()
    changed, changes = assist.apply(before, [
        {"op": "update", "key": "trigger", "config": {"schedule": {"kind": "weekdays", "time": "07:00"}}, "why": "Runs at 7"},
        {"op": "add", "step": {"key": "ok", "type": "approval", "label": "Ask first", "config": {"message": "Send it?"}}, "after": "sum"},
        {"op": "remove", "key": "find"},
        {"op": "remove", "key": "trigger"},
        {"op": "update", "key": "ghost", "config": {"template": "x"}},
        "nonsense",
    ], CHOICES)
    assert [n["key"] for n in changed["nodes"]] == ["trigger", "sum", "post", "ok"]
    assert edges_of(changed) == {("trigger", "sum", None), ("sum", "ok", None), ("ok", "post", "approved")}
    assert [(c["kind"], c["label"]) for c in changes] == [("change", "Trigger"), ("add", "Ask first"), ("remove", "Find")]
    assert changes[0]["text"] == "Runs at 7" and changes[0]["details"][0]["after"].count("07:00") == 1
    # The new step takes the place after "sum", and the steps after it move along
    positions = {n["key"]: n["position"]["x"] for n in changed["nodes"]}
    assert positions["ok"] == positions["sum"] + describe.X_STEP and positions["post"] == 4 * describe.X_STEP
    # Steps nobody named are exactly as they were, and the original isn't touched
    unmoved = lambda d: {k: v for k, v in next(n for n in d["nodes"] if n["key"] == "post").items() if k != "position"}
    assert unmoved(changed) == unmoved(before)
    assert len(before["nodes"]) == 4
    assert [p["message"] for p in nodes.check(changed)] == ["{{find}}: there's no step called find"]


def test_connections_never_go_round():
    changed, changes = assist.apply(digest(), [{"op": "connect", "source": "post", "target": "find"},
                                               {"op": "disconnect", "source": "sum", "target": "post"},
                                               {"op": "connect", "source": "find", "target": "post"}], CHOICES)
    assert edges_of(changed) == {("trigger", "find", None), ("find", "sum", None), ("find", "post", None)}
    assert [c["text"] for c in changes] == ["Disconnected Sum → Post", "Connected Find → Post"]


def test_the_drafting_room_asks_then_drafts(alice, monkeypatch):
    _, client = alice
    replies(monkeypatch,
            json.dumps({"questions": [{"question": "What time?", "why": "Morning could be any time",
                                       "options": ["7:00", "8:00", "9:00", "10:00", "11:00"]}, {"question": ""}]}),
            json.dumps({"name": "Digest", "emoji": "📰", "nodes": [
                {"key": "trigger", "type": "trigger", "config": {"schedule": {"kind": "weekdays", "time": "08:00"}}},
                {"key": "sum", "type": "transform", "config": {"template": "hello"}}],
                "edges": [{"source": "trigger", "target": "sum"}]}))
    asked = client.post("/studio/drafts", json={"description": "Every morning, a digest"}).json()
    assert asked == {"kind": "questions", "questions": [
        {"question": "What time?", "why": "Morning could be any time", "options": ["7:00", "8:00", "9:00", "10:00"]}]}
    drafted = client.post("/studio/drafts", json={"description": "Every morning, a digest",
                                                  "answers": [{"question": "What time?", "answer": "8:00"}]}).json()
    assert drafted["kind"] == "blueprint" and drafted["name"] == "Digest" and drafted["problems"] == []
    assert [n["key"] for n in drafted["definition"]["nodes"]] == ["trigger", "sum"]
    assert [w for w in store.list_workflows(alice[0]["id"]) if w["mine"]] == []
    assert client.post("/studio/drafts", json={"description": " "}).status_code == 400


def test_the_drafting_room_changes_a_blueprint(alice, monkeypatch):
    _, client = alice
    replies(monkeypatch, json.dumps({"reply": "It now asks you first.", "edits": [
        {"op": "add", "step": {"key": "ok", "type": "approval", "label": "Ask first", "config": {"message": "Go?"}}, "after": "post"}]}))
    changed = client.post("/studio/drafts", json={"current": digest(), "request": "Ask me first"}).json()
    assert changed["kind"] == "blueprint" and changed["reply"] == "It now asks you first."
    assert [c["kind"] for c in changed["changes"]] == ["add"] and changed["definition"]["nodes"][-1]["key"] == "ok"


def test_suggest_in_the_builder(alice, monkeypatch):
    _, client = alice
    workflow = make(client, flow(node("boom", "transform", template="x"), ("trigger", "boom")))
    tried = finished(client.post("/studio/try", json={"definition": workflow["draft"]}).json()["id"])
    replies(monkeypatch, json.dumps({"reply": "Shorter", "edits": [{"op": "update", "key": "boom", "label": "Calm", "why": "Renamed"}]}))
    url = f"/studio/workflows/{workflow['id']}/suggest"
    suggested = client.post(url, json={"prompt": "Rename it", "definition": workflow["draft"], "selected": ["boom"]}).json()
    assert suggested["reply"] == "Shorter" and suggested["changes"][0]["text"] == "Renamed"
    assert suggested["changes"][0]["details"] == [{"field": "Name", "before": "Boom", "after": "Calm"}]
    # Nothing is saved until the builder keeps it
    assert client.get(f"/studio/workflows/{workflow['id']}").json()["draft"] == workflow["draft"]
    assert client.post(url, json={"prompt": "", "definition": workflow["draft"]}).status_code == 400
    # Only a failed run of this workflow can be fixed
    assert client.post(url, json={"definition": workflow["draft"], "run_id": tried["id"]}).status_code == 400


def test_a_fix_is_told_what_failed(alice, monkeypatch):
    user, client = alice
    from labb.server.studio import sandbox

    def broken(*args, **kwargs):
        raise sandbox.SandboxError("boom")

    monkeypatch.setattr(sandbox, "run", broken)
    monkeypatch.setattr(sandbox, "available", lambda: True)
    workflow = make(client, flow(node("make", "python", code="x"), ("trigger", "make")))
    started = client.post(f"/studio/workflows/{workflow['id']}/runs", json={"inputs": {}, "draft": True}).json()
    run = finished(started["id"])
    assert run["status"] == "failed"
    seen = []
    from labb.server.studio import steps

    def fake_ask(prompt, step, **kwargs):
        seen.append(prompt)
        return steps.Result({"reply": "Fixed", "edits": [{"op": "update", "key": "make", "config": {"code": "fine"}}]})

    monkeypatch.setattr(assist, "ask_model", fake_ask)
    fixed = client.post(f"/studio/workflows/{workflow['id']}/suggest",
                        json={"definition": workflow["draft"], "run_id": run["id"]}).json()
    assert "Its last run failed" in seen[0] and '"key": "make"' in seen[0] and "boom" in seen[0]
    assert fixed["definition"]["nodes"][1]["config"]["code"] == "fine" and fixed["problems"] == []


def test_explain(alice, monkeypatch):
    _, client = alice
    replies(monkeypatch, "It   runs every weekday\nat 8 and posts a summary.")
    assert client.post("/studio/explain", json={"definition": digest()}).json() == {
        "text": "It runs every weekday at 8 and posts a summary."}


def test_trying_a_draft_saves_no_workflow(alice):
    user, client = alice
    definition = flow(node("say", "transform", template="hi there"), ("trigger", "say"))
    first = client.post("/studio/try", json={"name": "Hello", "definition": definition})
    assert first.status_code == 202, first.text
    assert finished(first.json()["id"])["result"] == "hi there"
    second = finished(client.post("/studio/try", json={"definition": definition}).json()["id"])
    assert store.get_run(first.json()["id"]) is None and second["status"] == "done"
    assert [w for w in store.list_workflows(user["id"]) if w["mine"]] == [] and client.get("/studio/runs").json() == []
    assert client.get(f"/studio/runs/{second['id']}").status_code == 200
    broken = client.post("/studio/try", json={"definition": {"nodes": [], "edges": []}})
    assert broken.status_code == 400
