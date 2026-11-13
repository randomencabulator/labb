"""Agents working with others: asking the agents they were given and using the actions they were
given (tools/delegate.py, server/studio/team.py)."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from labb import chats, memories, model, tools
from labb.agent import chat_agent
from labb.server import agents, auth
from labb.server import app as server
from labb.server.studio import steps, team
from labb.server import limits

from conftest import ai


@pytest.fixture
def owner(accounts, monkeypatch):
    monkeypatch.setattr(memories, "index", None)
    user = auth.create_user(f"u{uuid4().hex[:10]}", "a-password")
    key, _ = auth.create_key(user["id"], "laptop")
    return user, TestClient(server.app, headers={"Authorization": f"Bearer {key}"})


def config_for(agent: dict, user_id: str) -> dict:
    return {"configurable": {"agent": agents.for_run(agent), "user_id": user_id, "thread_id": f"t-{uuid4()}"}}


def test_an_agent_is_checked_and_lists_its_team(owner, monkeypatch):
    user, client = owner
    bee = agents.create(user["id"], {"name": "Bee", "description": "Knows about bees"})
    action = client.post("/studio/actions", json={"kind": "prompt", "name": "Shorten", "description": "Makes text short",
                                                  "config": {"prompt": "Shorten {{text}}", "inputs": [{"name": "text"}]}}).json()
    boss = agents.create(user["id"], {"name": "Boss", "delegates": [bee["id"]], "actions": [action["id"]]})
    assert boss["delegates"] == [bee["id"]] and boss["actions"] == [action["id"]]
    with pytest.raises(agents.AgentError, match="No such agent"):
        agents.create(user["id"], {"name": "X", "delegates": ["nope"]})
    with pytest.raises(agents.AgentError, match="itself"):
        agents.update(user["id"], boss["id"], {"delegates": [boss["id"]]})

    config = config_for(boss, user["id"])
    prompt = model.system_prompt(config)
    assert "- Bee: Knows about bees" in prompt and "- Shorten: Makes text short (inputs: text)" in prompt
    names = {t.name for t in tools.tools_for(False, tools.offered_for(config))}
    assert {"ask_agent", "call_action"} <= names
    plain = {t.name for t in tools.tools_for(False, tools.offered_for(config_for(bee, user["id"])))}
    assert "ask_agent" not in plain
    assert "ask_agent" not in {t.name for t in tools.tools_for(False, tools.offered_for({"configurable": {}}))}


def test_an_agent_asks_another_and_uses_an_action(owner, fake_model, monkeypatch):
    user, client = owner
    bee = agents.create(user["id"], {"name": "Bee", "instructions": "Answer about bees."})
    action = client.post("/studio/actions", json={"kind": "prompt", "name": "Shorten",
                                                  "config": {"prompt": "Shorten {{text}}", "inputs": [{"name": "text"}]}}).json()
    boss = agents.create(user["id"], {"name": "Boss", "delegates": [bee["id"]], "actions": [action["id"]]})
    fake_model(ai("", [("ask_agent", {"agent": "bee", "message": "Do bees sleep?"})]),
               ai("Yes, in short naps."),
               ai("", [("call_action", {"action": "Shorten", "inputs": {"text": "Yes, in short naps."}})]),
               ai("Bee says they nap."))
    monkeypatch.setattr(steps, "chat_model", lambda spec: GenericFakeChatModel(messages=iter([AIMessage("Naps.")])))
    before = set(chats.checkpointed_threads("ask-"))
    config = config_for(boss, user["id"])
    result = chat_agent.invoke({"messages": [HumanMessage("Ask Bee if bees sleep, then shorten it.")]}, config)
    results = [m.content for m in result["messages"] if m.type == "tool"]
    assert results == ["Yes, in short naps.", "Naps."]
    assert result["messages"][-1].text == "Bee says they nap."
    # The asked agent's conversation was thrown away
    assert set(chats.checkpointed_threads("ask-")) == before


def test_wrong_names_depth_and_copies(owner, accounts):
    user, client = owner
    bee = agents.create(user["id"], {"name": "Bee"})
    boss = agents.create(user["id"], {"name": "Boss", "delegates": [bee["id"]]})
    config = config_for(boss, user["id"])
    assert "you have: Bee" in team.ask(config, "Wasp", "hi")
    deep = {"configurable": {**config["configurable"], "depth": limits.get("agent_ask_depth")}}
    assert "can't ask further" in team.ask(deep, "Bee", "hi")
    assert "you have: none" in team.act(config, "Anything", {})

    agents.update(user["id"], boss["id"], {"shared": True})
    other = accounts["member"][0]
    copy = agents.duplicate(other["id"], boss["id"])
    assert copy["delegates"] == []  # Bee is private to its owner
