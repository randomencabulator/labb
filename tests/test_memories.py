"""Memories: saving, duplicates, pinned ones in the system prompt, recall by keywords and by
meaning, the graph's recall step (the model sees them, the saved chat doesn't), the tools,
one set per account on the server, and labb on a PC using the server's."""

import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import Field

from labb import memories, model, remote, runner
from labb.agent import chat_agent
from labb.server import app as server
from labb.server import auth, vectors
from labb.server.memory_index import MemoryIndex

from conftest import FakeModel, WordEmbeddings, ai


class SeeingModel(FakeModel):
    """A FakeModel that keeps what it was sent"""

    seen: list = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.fixture
def local(monkeypatch):
    """labb without a server: its own memories, recalled by keywords"""

    monkeypatch.setattr(memories, "on_server", False)
    monkeypatch.setattr(memories, "index", None)
    monkeypatch.setattr(remote, "server", lambda: None)
    memories.delete_for(memories.LOCAL_USER)
    yield
    memories.delete_for(memories.LOCAL_USER)


@pytest.fixture
def index(tmp_path, monkeypatch):
    """The server's memories, embedded with word counts"""

    memory_index = MemoryIndex()
    memory_index._store = vectors.Store("memories", WordEmbeddings(), "test")
    monkeypatch.setattr(memories, "on_server", True)
    monkeypatch.setattr(memories, "index", memory_index)
    return memory_index


@pytest.fixture
def person(accounts):
    """A new member with a key: (user, client)"""

    user = auth.create_user(f"u{uuid4().hex[:10]}", "a-password")
    key, _ = auth.create_key(user["id"], "laptop")
    return user, TestClient(server.app, headers={"Authorization": f"Bearer {key}"})


def seeing(monkeypatch, *replies) -> SeeingModel:
    fake = SeeingModel(messages=iter(list(replies)))
    monkeypatch.setattr(model, "model_with_tools", lambda *args: fake)
    return fake


def test_memories_are_saved_once_and_changed_in_place(local):
    first = memories.remember(None, "  Prefers   metric units ")
    assert first["text"] == "Prefers metric units" and not first["pinned"]
    again = memories.remember(None, "prefers metric units", pinned=True)
    assert again["existed"] and again["id"] == first["id"] and again["pinned"]

    memories.update(None, first["id"], text="Prefers metric units and 24-hour times")
    assert [m["text"] for m in memories.all_memories()] == ["Prefers metric units and 24-hour times"]
    memories.forget(None, first["id"])
    assert memories.all_memories() == []

    for text in ("", "x" * (memories.MAX_CHARS + 1)):
        with pytest.raises(memories.MemoryError):
            memories.remember(None, text)
    with pytest.raises(memories.MemoryError):
        memories.forget(None, "nope")


def test_pinned_memories_are_in_the_prompt_and_the_others_recalled_by_keywords(local):
    name = memories.remember(None, "The user's name is Sam", pinned=True)
    dog = memories.remember(None, "Has a dog called Rex who is allergic to chicken")
    memories.remember(None, "Works as a nurse on night shifts")

    prompt = model.system_prompt()
    assert f"[{name['id']}] The user's name is Sam" in prompt
    assert dog["id"] not in prompt

    found = memories.recall(None, "What food is good for my dog?")
    assert [m["id"] for m in found] == [dog["id"]]
    # Pinned ones are in the prompt already
    assert memories.recall(None, "what is my name, Sam?") == []
    assert memories.recall(None, "quantum chromodynamics") == []


def test_the_model_sees_recalled_memories_but_the_chat_keeps_the_message(local, monkeypatch):
    memories.remember(None, "Prefers answers in German", pinned=True)
    dog = memories.remember(None, "Has a dog called Rex who is allergic to chicken")
    fake = seeing(monkeypatch, ai("Try fish."), ai("Gern."))

    config = {"configurable": {"thread_id": f"t-{uuid4()}"}}
    chat_agent.invoke({"messages": [HumanMessage("What should I feed my dog?")]}, config)
    system, *_, asked = fake.seen[0]
    assert isinstance(system, SystemMessage) and "Prefers answers in German" in system.content
    assert "<memories>" in asked.content and f"[{dog['id']}] Has a dog called Rex" in asked.content

    saved = chat_agent.get_state(config).values["messages"]
    assert saved[0].content == "What should I feed my dog?"

    # The next message isn't about the dog: nothing recalled, and the earlier message goes as it was
    chat_agent.invoke({"messages": [HumanMessage("Thanks!")]}, config)
    second = fake.seen[1]
    assert all("<memories>" not in str(m.content) for m in second if m.type == "human")


def test_the_model_remembers_and_forgets_with_its_tools(local, monkeypatch, config):
    old = memories.remember(None, "Uses vim")
    seeing(
        monkeypatch,
        ai("", [("remember", {"text": "Uses Neovim with LazyVim", "replace_id": old["id"]}),
                ("remember", {"text": "Is called Robin", "always": True})]),
        ai("Noted."),
        ai("", [("forget", {"memory_id": f"[{old['id']}]"})]),
        ai("Forgotten."),
    )
    chat_agent.invoke({"messages": [HumanMessage("I switched to Neovim. Also, I'm Robin.")]},
                      {"configurable": {**config["configurable"], "thread_id": f"t-{uuid4()}"}})
    assert {(m["text"], m["pinned"]) for m in memories.all_memories()} == {("Uses Neovim with LazyVim", False), ("Is called Robin", True)}

    chat_agent.invoke({"messages": [HumanMessage("Forget the editor thing.")]},
                      {"configurable": {**config["configurable"], "thread_id": f"t-{uuid4()}"}})
    assert [m["text"] for m in memories.all_memories()] == ["Is called Robin"]


def test_on_the_server_memories_are_per_account_and_recalled_by_meaning(person, accounts, index):
    user, client = person
    added = client.post("/memories", json={"text": "Grows tomatoes and peppers in a greenhouse"}).json()
    client.post("/memories", json={"text": "Lives in Vienna", "pinned": True})
    assert index.store().text(added["id"]) == "Grows tomatoes and peppers in a greenhouse"

    found = client.post("/memories/recall", json={"query": "tomatoes in my greenhouse"}).json()
    assert [m["id"] for m in found] == [added["id"]] and found[0]["score"] >= memories.MIN_SCORE
    assert client.post("/memories/recall", json={"query": "Vienna weather", "pinned": True}).json()[0]["text"] == "Lives in Vienna"

    bob = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert bob.get("/memories").json() == []
    assert bob.post("/memories/recall", json={"query": "tomatoes greenhouse"}).json() == []
    assert bob.delete(f"/memories/{added['id']}").status_code == 404
    assert bob.patch(f"/memories/{added['id']}", json={"pinned": True}).status_code == 404

    assert client.patch(f"/memories/{added['id']}", json={"text": "Grows chillies"}).json()["text"] == "Grows chillies"
    assert index.store().text(added["id"]) == "Grows chillies"
    assert client.delete("/memories").json() == {"forgotten": 2}
    assert index.store().text(added["id"]) is None


def test_the_web_chat_remembers_for_whoever_is_chatting(person, index, monkeypatch):
    user, client = person
    monkeypatch.setattr(runner, "name_chat", lambda thread_id, config: None)
    seeing(monkeypatch, ai("", [("remember", {"text": "Is vegetarian"})]), ai("Got it."))
    client.post("/chat", json={"message": "I'm vegetarian, remember that."})
    assert [m["text"] for m in client.get("/memories").json()] == ["Is vegetarian"]
    assert memories.list_for(memories.LOCAL_USER) == []


def test_recall_falls_back_to_keywords_when_embedding_fails(person, index, monkeypatch):
    user, client = person
    client.post("/memories", json={"text": "Plays the cello in an orchestra"})
    monkeypatch.setattr(index, "search", lambda *args: (_ for _ in ()).throw(ConnectionError("no model")))
    assert client.post("/memories/recall", json={"query": "cello lessons"}).json()[0]["text"] == "Plays the cello in an orchestra"


def test_memories_that_werent_embedded_are_at_the_next_start(person, index):
    user, client = person
    memory = client.post("/memories", json={"text": "Speaks Albanian and English"}).json()
    index.remove([memory["id"]])
    conn, lock = memories.db()
    with lock, conn:
        conn.execute("UPDATE memories SET embed_model = NULL WHERE id = ?", (memory["id"],))

    assert index.reindex_stale() >= 1
    for _ in range(100):
        if index.store().text(memory["id"]):
            break
        time.sleep(0.05)
    assert index.store().text(memory["id"]) == "Speaks Albanian and English"


def test_labb_on_a_pc_uses_the_servers_memories(person, index, monkeypatch):
    user, client = person
    monkeypatch.setattr(memories, "on_server", False)
    monkeypatch.setattr(remote, "server", lambda: "http://server")
    monkeypatch.setattr(memories.httpx, "request", lambda method, url, **kwargs: client.request(
        method, url.removeprefix("http://server"), json=kwargs.get("json")))
    memories.forget_cached()
    # As the server's process sees it, so the server keeps indexing them
    monkeypatch.setattr(memories, "index", index)

    memory = memories.remember(None, "Runs Arch Linux on a ThinkPad", pinned=True)
    assert [m["id"] for m in client.get("/memories").json()] == [memory["id"]]
    assert "Runs Arch Linux on a ThinkPad" in (memories.prompt_section(None) or "")

    other = memories.remember(None, "Drinks oat milk in coffee")
    assert memories.recall(None, "oat milk or coffee?")[0]["id"] == other["id"]
    memories.forget(None, other["id"])
    assert [m["id"] for m in memories.all_memories()] == [memory["id"]]
    assert memories.list_for(memories.LOCAL_USER) == []


def test_what_the_agent_saves_waits_for_review_before_its_pinned(local):
    from labb.tools.memory import remember as remember_tool
    config = {"configurable": {}}
    remember_tool.invoke({"text": "Name is Ada", "always": True}, config)
    saved = memories.all_memories(config)[0]
    assert saved["pinned"] and not saved["reviewed"]
    # Not in every conversation yet, but recalled when it's relevant
    assert memories.prompt_section(config) is None
    assert [m["text"] for m in memories.recall(config, "what is my name")] == ["Name is Ada"]

    memories.update(config, saved["id"], reviewed=True)
    assert "Name is Ada" in memories.prompt_section(config)
    # Changed by the agent: reviewed again
    remember_tool.invoke({"text": "Name is Ada Lovelace", "replace_id": saved["id"]}, config)
    assert not memories.all_memories(config)[0]["reviewed"] and memories.prompt_section(config) is None


def test_what_people_add_themselves_needs_no_review(local, accounts):
    from fastapi.testclient import TestClient
    from labb.server import app as server
    config = {"configurable": {}}
    assert memories.remember(config, "Likes tea")["reviewed"] is True
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    added = member.post("/memories", json={"text": "Works nights"}).json()
    assert added["reviewed"] is True
    waiting = member.post("/memories", json={"text": "Has a cat", "reviewed": False}).json()
    assert member.patch(f"/memories/{waiting['id']}", json={"reviewed": True}).json()["reviewed"] is True
    member.delete("/memories")
