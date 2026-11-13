"""The agents and skills every account starts with: given once, deletable, and put back by
restore without touching the ones still there."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from labb import skills as skill_format
from labb.server import app as server
from labb.server import agents, auth, defaults, skills


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(defaults, "enabled", True)


def person() -> tuple[dict, TestClient]:
    user = auth.create_user(f"u{uuid4().hex[:10]}", "a-password")
    key, _ = auth.create_key(user["id"], "laptop")
    return user, TestClient(server.app, headers={"Authorization": f"Bearer {key}"})


def test_the_default_skills_are_valid_and_the_agents_name_them():
    files = defaults.skill_files()
    assert len(files) >= 5
    for name, contents in files.items():
        meta, body = skill_format.parse(contents["SKILL.md"])
        assert meta["name"] == name and body.strip()
    keys = [a["key"] for a in defaults.AGENTS]
    assert len(keys) == len(set(keys))
    for spec in defaults.AGENTS:
        assert set(spec["skills"] if isinstance(spec["skills"], list) else []) <= set(files)
        agents.clean("nobody", {k: v for k, v in spec.items() if k not in ("key", "toolsets", "skills", "model")})


def test_a_new_account_gets_them_once(on):
    user, client = person()
    names = {s["name"] for s in client.get("/skills").json() if s["mine"]}
    assert names == set(defaults.skill_files())
    mine = [a for a in client.get("/agents").json() if a["mine"]]
    assert {a["name"] for a in mine} == {a["name"] for a in defaults.AGENTS}
    news = next(a for a in mine if a["name"] == "News Desk")
    assert "news-briefing" in news["skills"] and "web" in news["toolsets"] and not news["shared"]
    assert client.get("/defaults").json()["agents"] == []

    # Deleted stays deleted, also after the server starts again
    client.delete(f"/agents/{news['id']}")
    skill = next(s for s in client.get("/skills").json() if s["name"] == "fact-check" and s["mine"])
    client.delete(f"/skills/{skill['id']}")
    defaults.ensure(user["id"])
    defaults.ensure_all()
    assert client.get("/defaults").json() | {"total": None} == {"agents": ["news-desk"], "skills": ["fact-check"], "total": None}


def test_restore_puts_back_only_whats_missing(on):
    user, client = person()
    mine = {a["name"]: a for a in client.get("/agents").json() if a["mine"]}
    client.patch(f"/agents/{mine['Researcher']['id']}", json={"instructions": "My own way"})
    client.delete(f"/agents/{mine['Trip Planner']['id']}")
    for skill in client.get("/skills").json():
        if skill["mine"] and skill["name"] == "trip-planner":
            client.delete(f"/skills/{skill['id']}")

    added = client.post("/defaults/restore").json()
    assert added == {"agents": ["trip-planner"], "skills": ["trip-planner"]}
    after = [a for a in client.get("/agents").json() if a["mine"]]
    assert len(after) == len(defaults.AGENTS)
    assert next(a for a in after if a["name"] == "Researcher")["instructions"] == "My own way"
    assert next(a for a in after if a["name"] == "Trip Planner")["skills"] == ["trip-planner", "page-summary"]
    assert client.post("/defaults/restore").json() == {"agents": [], "skills": []}


def test_a_copy_of_a_default_agent_isnt_one(on):
    user, client = person()
    news = next(a for a in client.get("/agents").json() if a["mine"] and a["name"] == "News Desk")
    client.post(f"/agents/{news['id']}/duplicate")
    client.delete(f"/agents/{news['id']}")
    assert client.get("/defaults").json()["agents"] == ["news-desk"]


def test_without_defaults_on_accounts_start_empty():
    user, client = person()
    assert client.get("/agents").json() == [] or all(not a["mine"] for a in client.get("/agents").json())
    assert skills.list_all(user["id"]) == [] or all(not s["mine"] for s in skills.list_all(user["id"]))
