"""Agents: setting them up, what reaches the model (instructions, tools, documents, memories),
the agent staying with its chat, sharing, deleting, and trying one out in the editor."""

import base64
import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import SystemMessage
from pydantic import Field

from labb import memories, model, runner
from labb.server import app as server
from labb.server import auth, documents, vectors

from conftest import FakeModel, WordEmbeddings, ai


class SeeingModel(FakeModel):
    """A FakeModel that keeps what it was sent"""

    seen: list = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    """Vectors of word embeddings for the documents, keyword recall for memories, and no
    titling (it would use up the fake model's replies)"""

    monkeypatch.setattr(documents, "_store", vectors.Store("documents", WordEmbeddings(), "test"))
    monkeypatch.setattr(memories, "index", None)
    monkeypatch.setattr(runner, "name_chat", lambda thread_id, config: None)


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


@pytest.fixture
def replies(monkeypatch):
    """replies(ai(...), ...): the model's replies; .seen has what it was sent, .allowed the toolsets it got"""

    def use(*answers):
        fake = SeeingModel(messages=iter(list(answers)))
        fake.__dict__["allowed"] = []
        fake.__dict__["rules"] = []

        def with_tools(spec, has_workdir, allowed=None, rules=None):
            fake.__dict__["allowed"].append(allowed)
            fake.__dict__["rules"].append(rules)
            return fake

        monkeypatch.setattr(model, "model_with_tools", with_tools)
        return fake

    return use


def chat(client: TestClient, message: str, **fields) -> tuple[str | None, list[dict]]:
    response = client.post("/chat", json={"message": message, **fields})
    assert response.status_code == 200, response.text
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    thread = next((e["thread_id"] for e in events if e["type"] == "thread"), fields.get("thread_id"))
    return thread, events


def upload(client: TestClient, name: str, text: str) -> dict:
    doc = client.post("/documents", json={"name": name, "data": base64.b64encode(text.encode()).decode()}).json()
    for _ in range(100):
        if next(d for d in client.get("/documents").json() if d["id"] == doc["id"])["status"] != "indexing":
            return doc
        time.sleep(0.05)
    raise AssertionError("still indexing")


def system_of(fake: SeeingModel, call: int = 0) -> str:
    system = fake.seen[call][0]
    assert isinstance(system, SystemMessage)
    return system.content


def tool_results(events: list[dict]) -> list[dict]:
    return [e for e in events if e["type"] == "tool_result"]


def test_agents_are_checked_and_changed(alice):
    user, client = alice
    doc = upload(client, "notes.txt", "Some notes")
    agent = client.post("/agents", json={"name": "  Chef  ", "instructions": "Suggest recipes.", "toolsets": ["web"],
                                         "documents": [doc["id"]], "starters": ["What's for dinner?", " "]}).json()
    assert agent["name"] == "Chef" and agent["emoji"] == "🤖" and agent["starters"] == ["What's for dinner?"]
    assert agent["mine"] and not agent["shared"] and agent["memories"] and agent["documents"] == [doc["id"]]

    for bad in [{"name": ""}, {"name": "x", "toolsets": ["shell"]}, {"name": "x", "documents": ["nope"]},
                {"name": "x", "documents": "everyone's"}, {"name": "x", "starters": ["a", "b", "c", "d", "e"]},
                {"name": "x", "instructions": "x" * 8001}]:
        assert client.post("/agents", json=bad).status_code == 400, bad

    changed = client.patch(f"/agents/{agent['id']}", json={"emoji": "🍳", "toolsets": None, "model": ""}).json()
    assert changed["emoji"] == "🍳" and changed["toolsets"] is None and changed["instructions"] == "Suggest recipes."
    assert client.patch(f"/agents/{agent['id']}", json={"name": " "}).status_code == 400
    assert [a["id"] for a in client.get("/agents").json()] == [agent["id"]]
    assert {t["name"] for t in client.get("/agents/tools").json()} >= {"web"}
    assert not {t["name"] for t in client.get("/agents/tools").json()} & {"files", "shell", "documents", "memory"}


def test_the_model_gets_the_agents_instructions_tools_and_documents(alice, replies):
    user, client = alice
    memories.add_for(user["id"], "Is called Alice", pinned=True)
    recipe = upload(client, "bread.txt", "Bake the bread at 220 degrees for 35 minutes.")
    upload(client, "taxes.txt", "Bake the taxes at 220 degrees before April.")
    agent = client.post("/agents", json={"name": "Baker", "description": "Helps with bread", "instructions": "Answer in rhyme.",
                                         "toolsets": ["web"], "documents": [recipe["id"]], "memories": False}).json()

    fake = replies(ai("", [("search_documents", {"query": "bake at 220 degrees"}), ("remember", {"text": "Likes bread"})]), ai("Done."))
    thread, events = chat(client, "How long do I bake bread?", agent_id=agent["id"])
    prompt = system_of(fake)
    assert "You are Baker: Helps with bread." in prompt and "Answer in rhyme." in prompt and "bread.txt" in prompt
    assert "Is called Alice" not in prompt
    assert fake.allowed[0] == {"web", "documents", "skill_writing", "history", "todo"}

    searched, remembered = tool_results(events)
    assert not searched["error"] and remembered["error"]
    found = json.loads(server.agent.get_state({"configurable": {"thread_id": thread}}).values["messages"][-3].content)
    assert {f["document"] for f in found} == {"bread.txt"}
    assert memories.list_for(user["id"])[0]["text"] == "Is called Alice" and len(memories.list_for(user["id"])) == 1

    # The chat keeps its agent, whatever the next message asks for
    other = client.post("/agents", json={"name": "Other"}).json()
    fake = replies(ai("Still rhyming."))
    chat(client, "And rye?", thread_id=thread, agent_id=other["id"])
    assert "You are Baker" in system_of(fake)
    assert next(c for c in client.get("/threads").json() if c["thread_id"] == thread)["agent_id"] == agent["id"]


def test_an_agents_model_is_the_default_for_its_chats(alice, replies, monkeypatch):
    user, client = alice
    agent = client.post("/agents", json={"name": "Big", "model": "ollama:big-model"}).json()
    assert agent["model"] == "ollama:big-model"
    used = []
    monkeypatch.setattr(server, "run_reply", lambda thread_id, content, stop, emit, configurable=None, **kw:
                        (used.append(configurable.get("model")), emit({"type": "done", "stats": {}})))
    chat(client, "hi", agent_id=agent["id"])
    chat(client, "hi", agent_id=agent["id"], model="ollama:small-model")
    chat(client, "hi")
    assert used == ["ollama:big-model", "ollama:small-model", None]


def test_shared_agents_are_for_everyone_but_only_their_owner_changes_them(alice, bob, replies):
    owner, alices = alice
    _, bobs = bob
    memories.add_for(owner["id"], "Owner's secret preference", pinned=True)
    manual = upload(alices, "manual.txt", "The boiler resets with the red button.")
    private = alices.post("/agents", json={"name": "Private"}).json()
    shared = alices.post("/agents", json={"name": "Helpdesk", "documents": [manual["id"]], "shared": True}).json()

    listed = bobs.get("/agents").json()
    assert [a["id"] for a in listed if a["id"] in (private["id"], shared["id"])] == [shared["id"]]
    seen = next(a for a in listed if a["id"] == shared["id"])
    assert not seen["mine"] and seen["owner"] == owner["username"]
    assert bobs.get(f"/agents/{private['id']}").status_code == 404
    assert bobs.patch(f"/agents/{shared['id']}", json={"name": "Mine now"}).status_code == 404
    assert bobs.delete(f"/agents/{shared['id']}").status_code == 404
    assert bobs.post("/chat", json={"message": "hi", "agent_id": private["id"]}).status_code == 404

    # Bob's own memories, and the owner's document the agent was given
    fake = replies(ai("", [("search_documents", {"query": "boiler red button"})]), ai("Press the red button."))
    thread, events = chat(bobs, "How do I reset the boiler?", agent_id=shared["id"])
    assert "Owner's secret preference" not in system_of(fake)
    assert not tool_results(events)[0]["error"]
    found = json.loads(server.agent.get_state({"configurable": {"thread_id": thread}}).values["messages"][-2].content)
    assert found[0]["document"] == "manual.txt"
    assert all(c["thread_id"] != thread for c in alices.get("/threads").json())

    copy = bobs.post(f"/agents/{shared['id']}/duplicate").json()
    assert copy["mine"] and copy["name"] == "Helpdesk (copy)" and copy["documents"] == "none" and not copy["shared"]


def test_deleting_an_agent_or_its_document(alice, replies):
    user, client = alice
    doc = upload(client, "a.txt", "Alpha")
    agent = client.post("/agents", json={"name": "Gone soon", "instructions": "Be brief.", "documents": [doc["id"]]}).json()
    replies(ai("Hi."))
    thread, _ = chat(client, "hello", agent_id=agent["id"])

    assert client.delete(f"/documents/{doc['id']}").status_code == 204
    assert client.get(f"/agents/{agent['id']}").json()["documents"] == []

    assert client.delete(f"/agents/{agent['id']}").status_code == 204
    assert client.delete(f"/agents/{agent['id']}").status_code == 404
    fake = replies(ai("Hello again."))
    chat(client, "still there?", thread_id=thread)
    assert "Gone soon" not in system_of(fake) and {"web", "documents", "memory"} <= fake.allowed[0]
    assert client.post("/chat", json={"message": "hi", "agent_id": agent["id"]}).status_code == 404


def test_trying_an_agent_out_isnt_kept(alice, bob, replies):
    _, client = alice
    _, bobs = bob
    fake = replies(ai("Ahoy!"), ai("Arr."))
    thread, events = chat(client, "hello", draft={"name": "Pirate", "instructions": "Talk like a pirate."})
    assert thread.startswith("try-") and "Talk like a pirate." in system_of(fake)
    assert events[-1]["type"] == "done"
    chat(client, "again", thread_id=thread, draft={"name": "Pirate", "instructions": "Talk like a pirate, briefly."})
    assert "briefly" in system_of(fake, 1)
    assert all(c["thread_id"] != thread for c in client.get("/threads").json())

    assert bobs.get("/history", params={"thread_id": thread}).status_code == 404
    assert bobs.delete(f"/agents/try/{thread}").status_code == 404
    assert client.post("/chat", json={"message": "x", "thread_id": thread}).status_code == 400
    assert client.post("/chat", json={"message": "x", "draft": {"name": ""}}).status_code == 400

    assert client.delete(f"/agents/try/{thread}").status_code == 204
    assert server.agent.get_state({"configurable": {"thread_id": thread}}).values == {}


def test_agents_with_a_removed_toolset_keep_their_others(alice):
    from labb.server import agents

    user, client = alice
    agent = client.post("/agents", json={"name": "Old", "toolsets": ["web"]}).json()
    with agents.lock, agents.conn:
        agents.conn.execute("UPDATE agents SET toolsets = ? WHERE id = ?", ('["removed_toolset", "web"]', agent["id"]))
    loaded = client.get(f"/agents/{agent['id']}").json()
    assert loaded["toolsets"] == ["web"]
    # The editor sends back what it got, and that saves
    assert client.patch(f"/agents/{agent['id']}", json={"toolsets": loaded["toolsets"], "name": "Renamed"}).status_code == 200


def test_the_web_toggle_leaves_out_searching_and_reading_pages(alice, replies):
    _, client = alice
    fake = replies(ai("", [("web_search", {"query": "today's news"})]), ai("I can't look that up."))
    thread, events = chat(client, "What's in the news?", web=False)
    rules = fake.rules[0]
    assert rules is not None and not rules.is_on("web_search") and not rules.is_on("fetch_page") and rules.is_on("remember")
    assert "turning on Web in the message box" in system_of(fake)
    # A model that calls it anyway is refused
    assert tool_results(events)[0]["error"]

    fake = replies(ai("Here's the news."))
    chat(client, "And now?", thread_id=thread)
    assert fake.rules[0] is None and "turning on Web" not in system_of(fake)
