"""Engines on the server: saving, versions, who can use what, and what a chat runs with."""

import json

import pytest
from fastapi.testclient import TestClient

from labb import engine
from labb.server import app as server
from labb.server.studio import sandbox
from labb.tools import custom
from conftest import ai


@pytest.fixture
def member(accounts):
    return TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})


PROMPT = {"prompts": {"base": "You are terse."}}
TERMINAL_ONLY = {"hooks": {"PreModel": [{"command": "x"}]}}
PYTHON_TOOL = {"tools": {"custom": [{"name": "p", "kind": "python", "description": "d", "code": "result = 1"}]}}


def make(client, overlay=PROMPT, name="Terse", **fields):
    response = client.post("/engines", json={"name": name, "overlay": overlay, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def chat(client, **body) -> list[dict]:
    response = client.post("/chat", json={"message": "hi", **body})
    assert response.status_code == 200, response.text
    return [json.loads(line) for line in response.text.splitlines()]


def system_seen(monkeypatch):
    from labb import model
    seen = []
    original = model.model_input

    def spying(state, config):
        sent = original(state, config)
        seen.append(sent[0].content)
        return sent

    monkeypatch.setattr(model, "model_input", spying)
    return seen


def test_the_list_starts_with_the_built_in(client):
    listed = client.get("/engines").json()["engines"]
    assert listed[0]["id"] == "default" and listed[0]["built_in"] and listed[0]["overlay"] == {}


def test_the_built_in_cant_be_changed_or_deleted(client):
    assert client.patch("/engines/default", json={"overlay": PROMPT}).status_code == 404
    assert client.delete("/engines/default").status_code == 404


def test_saving_makes_versions_and_restoring_adds_one(client):
    made = make(client)
    assert made["version"] == 1 and made["changes"] == ["prompts.base"]
    # The same overlay again is no new version; a name isn't either
    assert client.patch(f"/engines/{made['id']}", json={"overlay": PROMPT, "name": "New name"}).json()["version"] == 1
    assert client.patch(f"/engines/{made['id']}", json={"overlay": {}, "note": "Back to basics"}).json()["version"] == 2

    versions = client.get(f"/engines/{made['id']}/versions").json()
    assert [v["version"] for v in versions] == [2, 1] and versions[0]["note"] == "Back to basics" and versions[0]["labb_version"]

    restored = client.post(f"/engines/{made['id']}/versions/1/restore").json()
    assert restored["version"] == 3 and restored["overlay"] == PROMPT
    assert client.post(f"/engines/{made['id']}/versions/99/restore").status_code == 400


def test_what_cant_be_used_isnt_saved(client):
    assert "compact_at" in client.post("/engines", json={"name": "x", "overlay": {"context": {"compact_at": 9}}}).json()["detail"]
    assert client.post("/engines", json={"name": " ", "overlay": {}}).status_code == 400
    made = make(client)
    assert client.patch(f"/engines/{made['id']}", json={"overlay": {"nope": {}}}).status_code == 400
    assert client.get(f"/engines/{made['id']}").json()["overlay"] == PROMPT


def test_only_the_owner_changes_a_engine_and_only_shared_ones_show(client, member):
    made = make(client)
    assert member.get(f"/engines/{made['id']}").status_code == 404
    assert made["id"] not in [h["id"] for h in member.get("/engines").json()["engines"]]
    assert member.get(f"/engines/{made['id']}/versions").status_code == 404

    client.patch(f"/engines/{made['id']}", json={"shared": True})
    seen = member.get(f"/engines/{made['id']}").json()
    assert not seen["mine"] and seen["overlay"] == PROMPT
    assert member.patch(f"/engines/{made['id']}", json={"overlay": {}}).status_code == 404
    assert member.delete(f"/engines/{made['id']}").status_code == 404
    assert member.post(f"/engines/{made['id']}/versions/1/restore").status_code == 404

    copy = member.post(f"/engines/{made['id']}/duplicate").json()
    assert copy["mine"] and copy["overlay"] == PROMPT and not copy["shared"]


def test_the_built_in_can_be_duplicated(client):
    copy = client.post("/engines/default/duplicate").json()
    assert copy["overlay"] == {} and copy["mine"]


def test_a_chat_uses_the_engine_picked_and_keeps_it(client, fake_model, monkeypatch):
    seen = system_seen(monkeypatch)
    made = make(client)
    fake_model(ai("one"), ai("two"), ai("three"))
    first = chat(client, engine_id=made["id"])
    thread = first[0]["thread_id"]
    assert first[-1]["stats"]["engine"].startswith("terse@")
    chat(client, thread_id=thread)
    assert ["You are terse." in text for text in seen] == [True, True]
    assert client.get(f"/engines/chat/{thread}").json()["engine_id"] == made["id"]

    # Going back to the built-in is a choice like any other, and is kept
    chat(client, thread_id=thread, engine_id="default")
    assert "You are terse." not in seen[-1]
    assert client.get(f"/engines/chat/{thread}").json()["engine_id"] == "default"


def test_defaults_come_in_order(client, member, fake_model, monkeypatch):
    seen = system_seen(monkeypatch)
    mine = make(client, name="Mine")
    assert client.put("/engines/my-default", json={"engine_id": mine["id"]}).status_code == 200
    fake_model(ai("a"), ai("b"), ai("c"), ai("d"))
    chat(client)
    assert "You are terse." in seen[-1]
    chat(member)
    assert "You are terse." not in seen[-1]

    # An admin's choice has to be one everyone can see
    assert client.put("/admin/engine-default", json={"engine_id": mine["id"]}).status_code == 400
    assert member.put("/admin/engine-default", json={"engine_id": None}).status_code == 403
    shared = make(client, {"prompts": {"base": "Server-wide."}}, name="Everyone", shared=False)
    client.patch(f"/engines/{shared['id']}", json={"shared": True})
    assert client.put("/admin/engine-default", json={"engine_id": shared["id"]}).status_code == 200
    try:
        chat(member)
        assert "Server-wide." in seen[-1]
        # Yours comes first
        chat(client)
        assert "You are terse." in seen[-1]
    finally:
        client.put("/admin/engine-default", json={"engine_id": None})
        client.put("/engines/my-default", json={"engine_id": None})


def test_you_cant_use_someone_elses_private_engine(client, member, fake_model):
    made = make(client)
    fake_model(ai("x"))
    assert member.post("/chat", json={"message": "hi", "engine_id": made["id"]}).status_code == 400
    assert member.put("/engines/my-default", json={"engine_id": made["id"]}).status_code == 400


def test_nothing_runs_commands_on_the_server(client, fake_model):
    made = make(client, TERMINAL_ONLY, name="Hooky")
    assert made["web_problems"] and made["runs_code"]
    fake_model(ai("x"))
    assert client.post("/chat", json={"message": "hi", "engine_id": made["id"]}).status_code == 400
    assert client.post("/chat", json={"message": "hi", "engine_draft": TERMINAL_ONLY}).status_code == 400
    assert client.put("/engines/my-default", json={"engine_id": made["id"]}).status_code == 400
    # It stays good for the terminal
    assert "hooky" in [item["slug"] for item in client.get("/engines/for-terminal").json()]


def test_python_needs_the_sandbox(client, fake_model, monkeypatch):
    monkeypatch.setattr(sandbox, "SANDBOX_URL", "")
    made = make(client, PYTHON_TOOL, name="Py")
    assert any("sandbox" in problem for problem in made["web_problems"])
    assert client.post("/chat", json={"message": "hi", "engine_id": made["id"]}).status_code == 400
    monkeypatch.setattr(sandbox, "SANDBOX_URL", "http://sandbox")
    fake_model(ai("x"))
    assert chat(client, engine_id=made["id"])[-1]["type"] == "done"


def test_the_server_sends_python_to_the_sandbox():
    # Set when the server starts; nothing on the server runs it another way
    assert custom.sandbox is sandbox.run


def test_a_engine_gone_or_unshared_falls_back(client, member, fake_model, monkeypatch):
    seen = system_seen(monkeypatch)
    made = make(client)
    client.patch(f"/engines/{made['id']}", json={"shared": True})
    fake_model(ai("a"), ai("b"), ai("c"))
    thread = chat(member, engine_id=made["id"])[0]["thread_id"]
    assert "You are terse." in seen[-1]
    client.patch(f"/engines/{made['id']}", json={"shared": False})
    assert chat(member, thread_id=thread)[-1]["type"] == "done"
    assert "You are terse." not in seen[-1]
    client.delete(f"/engines/{made['id']}")
    assert chat(member, thread_id=thread)[-1]["type"] == "done"


def test_an_agents_engine_is_used_and_the_chats_pick_comes_first(client, fake_model, monkeypatch):
    seen = system_seen(monkeypatch)
    of_agent = make(client, {"prompts": {"base": "Agent's engine."}}, name="For agent")
    other = make(client, name="Other")
    agent = client.post("/agents", json={"name": "A", "engine_id": of_agent["id"]}).json()
    assert agent["engine_id"] == of_agent["id"]
    fake_model(ai("a"), ai("b"))
    thread = chat(client, agent_id=agent["id"])[0]["thread_id"]
    assert "Agent's engine." in seen[-1]
    chat(client, thread_id=thread, engine_id=other["id"])
    assert "You are terse." in seen[-1]
    # Not someone else's or terminal-only
    hooky = make(client, TERMINAL_ONLY, name="Hooky2")
    assert client.post("/agents", json={"name": "B", "engine_id": hooky["id"]}).status_code == 400


def test_a_shared_agent_uses_its_owners_engine(client, member, fake_model, monkeypatch):
    seen = system_seen(monkeypatch)
    made = make(client, {"prompts": {"base": "Owner's."}}, name="Owner's")
    agent = client.post("/agents", json={"name": "Shared", "shared": True, "engine_id": made["id"]}).json()
    fake_model(ai("a"))
    chat(member, agent_id=agent["id"])
    assert "Owner's." in seen[-1]


def test_a_tasks_agent_brings_its_engine(client):
    from labb.server import agents
    made = make(client, name="For a task")
    agent = client.post("/agents", json={"name": "T", "engine_id": made["id"]}).json()
    assert agents.for_run(agents.get(agent["id"]))["engine"].prompt("base") == "You are terse."


def test_trying_a_draft_keeps_nothing(client, fake_model, monkeypatch):
    seen = system_seen(monkeypatch)
    fake_model(ai("x"))
    events = chat(client, engine_draft={"prompts": {"base": "Draft."}})
    assert "Draft." in seen[-1] and events[0]["thread_id"].startswith("try-")
    thread = events[0]["thread_id"]
    assert thread not in [t["thread_id"] for t in client.get("/threads").json()]
    assert client.delete(f"/agents/try/{thread}").status_code == 204
    assert client.post("/chat", json={"message": "x", "engine_draft": {"nope": {}}}).status_code == 400


def test_comparing_saved_engines_keeps_nothing_either(client, fake_model, monkeypatch):
    seen = system_seen(monkeypatch)
    made = make(client)
    fake_model(ai("a"), ai("b"))
    with_it = chat(client, trial=True, engine_id=made["id"])
    without = chat(client, trial=True, engine_id="default")
    assert "You are terse." in seen[0] and "You are terse." not in seen[1]
    assert with_it[-1]["stats"]["engine"].startswith("terse@") and "engine" not in without[-1]["stats"]
    assert with_it[0]["thread_id"].startswith("try-")
    assert with_it[0]["thread_id"] not in [t["thread_id"] for t in client.get("/threads").json()]


def test_a_try_doesnt_change_the_chat_or_default(client, fake_model):
    made = make(client)
    fake_model(ai("a"))
    chat(client, trial=True, engine_id=made["id"])
    assert client.get("/engines").json()["my_default"] is None


def test_a_model_of_the_engine_needs_access(client, fake_model, monkeypatch):
    from labb.server import providers
    monkeypatch.setattr(providers, "why_not", lambda spec, role: "Not for you" if spec == "ollama:admins" else None)
    made = make(client, {"model": {"summary_model": "ollama:admins"}}, name="Admins")
    assert client.post("/chat", json={"message": "x", "engine_id": made["id"]}).status_code == 403


def test_the_catalog_draws_the_editor(client):
    catalog = client.get("/engines/catalog").json()
    assert catalog["prompts"]["coding"] == engine.CODING_PROMPT and catalog["context"]["compact_at"] == 0.75
    assert set(catalog["steps"]) == set(engine.STEPS) and "self-review" in catalog["presets"]
    assert any(t["name"] == "shell" and t["terminal_only"] for t in catalog["toolsets"])
    # Every prompt has a place on the page, and every section is known
    placed = {p for s in catalog["sections"] for p in s["prompts"]} | {p["key"] for p in catalog["other_prompts"]}
    assert placed == set(engine.PROMPTS)
    assert {s["id"] for s in catalog["sections"]} == set(engine.SECTIONS)
    assert {f["key"] for f in catalog["context_fields"]} == set(engine.RANGES)


@pytest.mark.parametrize("surface, label", [("web", "Base"), ("terminal", "How to work"), ("plan", "Plan mode"), ("task", "Task")])
def test_the_preview_shows_each_kind_of_run(client, surface, label):
    seen = client.post("/engines/preview", json={"overlay": {}, "surface": surface}).json()["seen"]
    assert label in [s["label"] for s in seen["system"]] and seen["used"] > 0


def test_the_preview_shows_the_draft(client):
    overlay = {"prompts": {"coding": "Short."}, "tools": {"deny": ["run_command"]}, "context": {"compact_at": 0.5, "keep_recent": 0.2}}
    seen = client.post("/engines/preview", json={"overlay": overlay, "surface": "terminal"}).json()["seen"]
    assert next(s for s in seen["system"] if s["label"] == "How to work")["text"] == "Short."
    assert "run_command" not in [t["name"] for t in seen["tools"]]
    assert seen["compact_at"] == round(0.5 * seen["window"])


def test_a_preview_doesnt_read_the_servers_files(client, monkeypatch, tmp_path):
    from labb import model
    monkeypatch.setattr(model, "project_instructions", lambda *args: pytest.fail("read the server's files"))
    assert client.post("/engines/preview", json={"overlay": {}, "surface": "terminal"}).json()["seen"]


def test_a_broken_draft_gets_its_problems(client):
    assert client.post("/engines/preview", json={"overlay": {"nope": {}}}).json()["problems"]
    checked = client.post("/engines/check", json={"overlay": PROMPT}).json()
    assert checked["problems"] == [] and checked["changes"][0]["where"] == "prompts.base"
    assert client.post("/engines/check", json={"overlay": TERMINAL_ONLY}).json()["web_problems"]


def test_a_change_in_words_is_a_preview(client, monkeypatch):
    from dataclasses import replace
    from labb import llm
    from langchain_core.messages import AIMessage

    class Answers:
        def __init__(self, text):
            self.text = text

        def invoke(self, prompt):
            return AIMessage(self.text)

    use = lambda text: monkeypatch.setattr(llm, "for_config", lambda c: replace(llm.default, model=Answers(text)))
    made = make(client)
    use('Here you go: {"prompts": {"base": "Shorter."}}')
    said = client.post("/engines/suggest", json={"overlay": PROMPT, "ask": "make it shorter"}).json()
    assert said["overlay"] == {"prompts": {"base": "Shorter."}} and said["changes"][0]["after"] == "Shorter."
    # Nothing was saved
    assert client.get(f"/engines/{made['id']}").json()["overlay"] == PROMPT
    use('{"nope": {}}')
    assert client.post("/engines/suggest", json={"overlay": {}, "ask": "x"}).json()["problems"]
    use("no idea")
    assert client.post("/engines/suggest", json={"overlay": {}, "ask": "x"}).status_code == 422
    assert client.post("/engines/suggest", json={"overlay": {}, "ask": " "}).status_code == 400


def test_prompt_sets_are_yours(client, member):
    made = client.post("/engines/prompt-sets", json={"name": "Basics", "prompts": ["one", " ", "two"]}).json()
    assert made["prompts"] == ["one", "two"]
    assert member.get("/engines/prompt-sets").json() == []
    assert member.delete(f"/engines/prompt-sets/{made['id']}").status_code == 404
    assert member.put(f"/engines/prompt-sets/{made['id']}", json={"name": "x", "prompts": ["y"]}).status_code == 400
    assert client.put(f"/engines/prompt-sets/{made['id']}", json={"name": "New", "prompts": ["three"]}).json()["name"] == "New"
    assert client.post("/engines/prompt-sets", json={"name": "Empty", "prompts": []}).status_code == 400
    assert client.delete(f"/engines/prompt-sets/{made['id']}").status_code == 204


def test_usage_says_which_engine(client, fake_model):
    made = make(client, name="Counted")
    fake_model(ai("a", tokens=(11, 3)))
    chat(client, engine_id=made["id"])
    names = {row["engine"]: row for row in client.get("/usage").json()["by_engine"]}
    assert any(name and name.startswith("counted@") for name in names)


def test_the_page_address_loads_the_app_and_the_api_still_answers(client):
    from labb.server.app import PAGE_ROOTS
    assert "engines" in PAGE_ROOTS
    assert isinstance(client.get("/engines").json()["engines"], list)


def test_the_terminal_can_fetch_them(client, monkeypatch, accounts):
    make(client, name="Fetch Me Please")
    fetched = client.get("/engines/for-terminal").json()
    entry = next(item for item in fetched if item["slug"] == "fetch-me-please")

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return fetched

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Response())
    monkeypatch.setenv("LABB_SERVER", "http://server")
    monkeypatch.setattr(engine, "_server_cache", [-engine.SERVER_CACHE_SECONDS, []])
    found = engine.find("fetch-me-please")
    assert found.scope == "server" and found.engine.overlay == entry["overlay"]


def test_a_engine_from_the_server_that_runs_code_asks_first(client):
    entry = engine.Entry("x", "server", None, engine.resolve(TERMINAL_ONLY, "x"), [])
    assert engine.needs_trust(entry, True)
    plain = engine.Entry("y", "server", None, engine.resolve(PROMPT, "y"), [])
    assert not engine.needs_trust(plain, True)


# --- labb local, the built-in for small models ------------------------------------------
# Its own short prompt for the web tools, to tell it apart in what the model was sent
LOCAL_MARK = "read=1 also reads the top one"


def test_labb_local_comes_next_to_the_default_which_stays_the_default(client):
    listed = client.get("/engines").json()
    assert [h["id"] for h in listed["engines"][:2]] == ["default", "labb-local"]
    local = listed["engines"][1]
    assert local["built_in"] and not local["mine"] and local["web_problems"] == [] and not local["runs_code"]
    assert listed["engines"][0]["overlay"] == {} and engine.check(local["overlay"]) == []
    assert local["overlay"] == engine.LOCAL_OVERLAY
    # labb web is gone
    assert client.get("/engines/labb-web").status_code == 404


def test_labb_local_cant_be_changed_or_deleted(client):
    before = client.get("/engines/labb-local").json()
    assert client.patch("/engines/labb-local", json={"overlay": {}}).status_code == 404
    assert client.delete("/engines/labb-local").status_code == 404
    assert client.post("/engines/labb-local/versions/1/restore").status_code == 404
    assert client.get("/engines/labb-local/versions").status_code == 404
    assert client.get("/engines/labb-local").json() == before


def test_a_chat_can_pick_labb_local_and_a_new_chat_doesnt_get_it(client, member, fake_model, monkeypatch):
    seen = system_seen(monkeypatch)
    fake_model(ai("a"), ai("b"), ai("c"))
    events = chat(member, engine_id="labb-local")
    assert LOCAL_MARK in seen[-1] and events[-1]["stats"]["engine"].startswith("labb-local@")
    assert member.get(f"/engines/chat/{events[0]['thread_id']}").json()["engine_id"] == "labb-local"
    chat(member, thread_id=events[0]["thread_id"])
    assert LOCAL_MARK in seen[-1]
    chat(member)
    assert LOCAL_MARK not in seen[-1]


def test_labb_local_can_be_a_default(client, member, fake_model, monkeypatch):
    seen = system_seen(monkeypatch)
    fake_model(ai("a"), ai("b"))
    assert member.put("/engines/my-default", json={"engine_id": "labb-local"}).status_code == 200
    try:
        chat(member)
        assert LOCAL_MARK in seen[-1]
    finally:
        member.put("/engines/my-default", json={"engine_id": None})
    assert client.put("/admin/engine-default", json={"engine_id": "labb-local"}).status_code == 200
    try:
        chat(member)
        assert LOCAL_MARK in seen[-1]
    finally:
        client.put("/admin/engine-default", json={"engine_id": None})


def test_a_pick_of_labb_web_from_before_falls_back_to_the_default(client, member, accounts, fake_model, monkeypatch):
    from labb.chats import conn, lock
    seen = system_seen(monkeypatch)
    fake_model(ai("a"), ai("b"))
    events = chat(member)
    me = accounts["member"][0]["id"]
    with lock, conn:
        conn.execute("UPDATE chats SET engine_id = 'labb-web' WHERE thread_id = ?", (events[0]["thread_id"],))
        conn.execute("INSERT INTO engine_defaults (user_id, engine_id) VALUES (?, 'labb-web')", (me,))
    try:
        again = chat(member, thread_id=events[0]["thread_id"])
        assert "engine" not in again[-1]["stats"] or not again[-1]["stats"]["engine"]
        assert LOCAL_MARK not in seen[-1] and "Start with the answer" not in seen[-1]
    finally:
        with lock, conn:
            conn.execute("DELETE FROM engine_defaults WHERE user_id = ?", (me,))


def test_labb_local_offers_a_small_model_less(client):
    from labb import model
    local = engine.LOCAL_ENGINE
    terminal = lambda h: {"configurable": {"workdir": "/nowhere", "preview": True, "engine": h}}
    names = lambda h: {tool.name for tool in model.tools_of(terminal(h), {}, everything=True)}
    assert {"task", "save_skill", "write_notes", "read_output", "stop_command", "forget"} <= names(engine.DEFAULT)
    assert not {"task", "save_skill", "write_notes", "read_output", "stop_command", "forget"} & names(local)
    assert {"read_file", "edit_file", "write_file", "grep", "glob", "run_command", "web_search", "look_back"} <= names(local)
    labels = [label for label, _ in model.system_prompt_parts(terminal(local))]
    assert "Example" in labels and "Tool rules" not in labels
    # In the web chat, the example (about code) isn't there
    assert "Example" not in [label for label, _ in model.system_prompt_parts({"configurable": {"engine": local}})]
    size = lambda h: sum(len(text) for _, text in model.system_prompt_parts(terminal(h)))
    assert size(local) < size(engine.DEFAULT) / 2


def test_the_terminal_has_labb_local_built_in(client):
    found = engine.find("labb-local")
    assert found.scope == "built-in" and found.engine is engine.LOCAL_ENGINE
    assert "labb-local" not in [item["slug"] for item in client.get("/engines/for-terminal").json()]
    with pytest.raises(ValueError):
        engine.fork("labb-local", found)


def test_a_fork_of_labb_local_keeps_what_it_has(client):
    copy = client.post("/engines/labb-local/duplicate").json()
    assert copy["mine"] and copy["name"] == "labb local (copy)" and copy["overlay"] == engine.LOCAL_OVERLAY
    assert copy["slug"] in [item["slug"] for item in client.get("/engines/for-terminal").json()]


def test_an_agent_can_use_labb_local(client):
    from labb.server import agents
    agent = client.post("/agents", json={"name": "Small helper", "engine_id": "labb-local"}).json()
    assert agents.for_run(agents.get(agent["id"]))["engine"].name == "labb-local"
