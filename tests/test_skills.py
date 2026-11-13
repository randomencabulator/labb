"""Skills: reading SKILL.md, finding them in the terminal's folders, the tools loading them,
the system prompt listing them (and no skill tools without skills), the server's per account
and shared, uploading a zipped folder, /name in the web chat, and labb using the server's."""

import base64
import io
import json
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from pydantic import Field

from labb import model, remote, runner, skills
from labb.agent import chat_agent
from labb.server import app as server
from labb.server import auth
from labb.tools import skills as skill_tools
from labb.tools.skill_writing import save_skill as skill_tools_save

from conftest import FakeModel, ai

CHART = """---
name: chart-maker
description: Turns a table of numbers into a chart description. Use when someone asks for a chart.
---
# Chart maker

1. Read `style.md` for the colors.
2. Run `scripts/plot.py` with the numbers.
"""


def write(folder: Path, name: str, skill_md: str, **files: str) -> Path:
    path = folder / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(skill_md)
    for file, text in files.items():
        (path / file).parent.mkdir(parents=True, exist_ok=True)
        (path / file).write_text(text)
    return path


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A home folder of its own, and no caches between tests"""

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    skills.forget_cached()
    yield tmp_path / "home"
    skills.forget_cached()


@pytest.fixture
def local(monkeypatch):
    """labb without a server"""
    monkeypatch.setattr(skills, "on_server", False)
    monkeypatch.setattr(remote, "server", lambda: None)


class SeeingModel(FakeModel):
    seen: list = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def replies(monkeypatch, *answers) -> SeeingModel:
    fake = SeeingModel(messages=iter(answers))
    fake.__dict__["offered"] = []
    monkeypatch.setattr(model, "model_with_tools", lambda spec, workdir, offered=None, rules=None: (fake.__dict__["offered"].append(offered), fake)[1])
    return fake


def person() -> tuple[dict, TestClient]:
    user = auth.create_user(f"u{uuid4().hex[:10]}", "a-password")
    key, _ = auth.create_key(user["id"], "laptop")
    return user, TestClient(server.app, headers={"Authorization": f"Bearer {key}"})


def zipped(files: dict[str, str]) -> str:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return base64.b64encode(out.getvalue()).decode()


def test_a_skill_md_needs_a_name_and_a_description():
    meta, body = skills.parse(CHART)
    assert meta["name"] == "chart-maker" and meta["description"].startswith("Turns a table") and body.startswith("# Chart maker")
    assert skills.parse("---\ndescription: From the folder\n---\nDo it.", "from-folder")[0]["name"] == "from-folder"
    for bad in ["no front matter", "---\nname: x\n", "---\nname: Bad Name\ndescription: d\n---\n",
                "---\nname: ok\n---\nno description", "---\n- a list\n---\n"]:
        with pytest.raises(skills.SkillError):
            skills.parse(bad)


def test_the_terminal_finds_skills_in_its_folders(tmp_path, home, local):
    work = tmp_path / "project"
    write(work / ".labb" / "skills", "chart-maker", CHART, **{"style.md": "Orange first.", "scripts/plot.py": "print('plot')"})
    write(home / ".labb" / "skills", "chart-maker", CHART.replace("Turns a table", "My own version"))
    write(home / ".claude" / "skills", "release-notes", "---\nname: release-notes\ndescription: Writes release notes from git log\n---\nUse git log.")
    write(home / ".labb" / "skills", "broken", "---\nname: Not OK\ndescription: x\n---\n")

    found = skills.available({"configurable": {"workdir": str(work)}})
    assert [(s["name"], s["source"]) for s in found] == [("chart-maker", "project"), ("release-notes", "personal")]
    assert found[0]["description"].startswith("Turns a table")
    assert any("broken" in p for p in skills.problems(str(work)))
    # The web chat has no folders
    assert skills.available({"configurable": {}}) == []


def test_the_tools_load_a_skill_and_its_files(tmp_path, local):
    work = tmp_path / "project"
    folder = write(work / ".labb" / "skills", "chart-maker", CHART, **{"style.md": "Orange first.", "scripts/plot.py": "print('plot')"})
    (tmp_path / "secret.txt").write_text("not yours")
    config = {"configurable": {"workdir": str(work)}}

    loaded = skill_tools.use_skill.invoke({"name": "chart-maker"}, config)
    assert "# Chart maker" in loaded and "name: chart-maker" not in loaded
    assert "scripts/plot.py" in loaded and "style.md" in loaded and str(folder) in loaded
    assert skill_tools.read_skill_file.invoke({"name": "chart-maker", "path": "style.md"}, config) == "Orange first."
    assert skill_tools.read_skill_file.invoke({"name": "chart-maker", "path": "../../../secret.txt"}, config).startswith("Error:")
    assert skill_tools.use_skill.invoke({"name": "nope"}, config).startswith("Error: no skill called `nope`")


def test_the_model_sees_the_skills_and_no_skill_tools_without_them(tmp_path, local, monkeypatch):
    work = tmp_path / "project"
    work.mkdir()
    config = {"configurable": {"workdir": str(work), "thread_id": f"t-{uuid4()}"}}
    fake = replies(monkeypatch, ai("Hi."), ai("Hi again."))
    chat_agent.invoke({"messages": [HumanMessage("hello")]}, config)
    assert "skills" not in fake.offered[0] and "Skills you can use" not in fake.seen[0][0].content

    write(work / ".labb" / "skills", "chart-maker", CHART)
    skills.forget_cached()
    chat_agent.invoke({"messages": [HumanMessage("make a chart")]}, config)
    assert fake.offered[1] is None  # all of them, the skill tools too
    assert "- chart-maker: Turns a table of numbers" in fake.seen[1][0].content


def test_skills_on_the_server_are_per_account_and_can_be_shared(accounts):
    alice, alices = person()
    _, bobs = person()
    added = alices.post("/skills", json={"skill_md": CHART}).json()
    assert added["name"] == "chart-maker" and added["files"] == ["SKILL.md"] and not added["shared"]
    assert all(s["name"] != "chart-maker" for s in bobs.get("/skills").json())
    assert bobs.get(f"/skills/{added['id']}").status_code == 404
    assert bobs.get("/skills/by-name/chart-maker/files").status_code == 404

    # A zipped folder with the same name replaces it, with its files
    upload = zipped({"chart-maker/SKILL.md": CHART, "chart-maker/style.md": "Orange first.", "chart-maker/.hidden": "x",
                     "__MACOSX/chart-maker/._style.md": "junk", "chart-maker/logo.png": "\0binary"})
    replaced = alices.post("/skills", json={"zip": upload, "shared": True}).json()
    assert replaced["id"] == added["id"] and replaced["files"] == ["SKILL.md", "style.md"] and replaced["shared"]

    shared = next(s for s in bobs.get("/skills").json() if s["id"] == added["id"])
    assert not shared["mine"] and shared["owner"] == alice["username"]
    assert bobs.get("/skills/by-name/chart-maker/file", params={"path": "style.md"}).json()["content"] == "Orange first."
    assert bobs.patch(f"/skills/{added['id']}", json={"shared": False}).status_code == 404
    assert bobs.delete(f"/skills/{added['id']}").status_code == 404
    with zipfile.ZipFile(io.BytesIO(bobs.get(f"/skills/{added['id']}/download").content)) as archive:
        assert sorted(archive.namelist()) == ["chart-maker/SKILL.md", "chart-maker/style.md"]

    for bad in [{"skill_md": "no front matter"}, {"zip": zipped({"readme.md": "no skill"})}, {"zip": "not base64!"}, {}]:
        assert alices.post("/skills", json=bad).status_code == 400, bad
    renamed = alices.patch(f"/skills/{added['id']}", json={"skill_md": CHART.replace("chart-maker", "charts")}).json()
    assert renamed["name"] == "charts"
    assert alices.delete(f"/skills/{added['id']}").status_code == 204


def test_slash_name_in_the_web_chat_uses_the_skill(accounts, monkeypatch):
    _, client = person()
    client.post("/skills", json={"skill_md": CHART})
    monkeypatch.setattr(runner, "name_chat", lambda thread_id, config: None)
    fake = replies(monkeypatch, ai("Here's the chart."))
    events = [json.loads(line) for line in client.post("/chat", json={"message": "/chart-maker sales 3, 5, 8"}).text.splitlines()]
    asked = fake.seen[0][-1].content
    assert asked.startswith("/chart-maker sales 3, 5, 8") and "# Chart maker" in asked
    assert "- chart-maker: Turns a table" in fake.seen[0][0].content
    thread = next(e["thread_id"] for e in events if e["type"] == "thread")
    user = next(e for e in client.get("/history", params={"thread_id": thread}).json() if e["type"] == "user")
    assert user["content"] == "/chart-maker sales 3, 5, 8" and user["files"] == ["skills/chart-maker/SKILL.md"]


def test_labb_on_a_pc_uses_the_servers_skills(accounts, tmp_path, monkeypatch):
    _, client = person()
    client.post("/skills", json={"zip": zipped({"SKILL.md": CHART, "style.md": "Orange first."})})
    monkeypatch.setattr(skills, "on_server", False)
    monkeypatch.setattr(remote, "server", lambda: "http://server")
    monkeypatch.setattr(skills.httpx, "get", lambda url, params=None, **kwargs: client.get(url.removeprefix("http://server"), params=params))
    work = tmp_path / "project"
    work.mkdir()
    config = {"configurable": {"workdir": str(work)}}

    assert [(s["name"], s["source"]) for s in skills.available(config)] == [("chart-maker", "server")]
    assert "style.md" in skill_tools.use_skill.invoke({"name": "chart-maker"}, config)
    assert skill_tools.read_skill_file.invoke({"name": "chart-maker", "path": "style.md"}, config) == "Orange first."
    # The project's own skill with that name wins
    write(work / ".labb" / "skills", "chart-maker", CHART.replace("Turns a table", "The project's"))
    skills.forget_cached()
    assert skills.available(config)[0]["source"] == "project"


def test_a_pc_that_just_started_still_asks_the_server(accounts, tmp_path, monkeypatch):
    """time.monotonic() counts from the machine's start: on one up for less than the caches' seconds
    (a fresh CI runner), nothing fetched yet mustn't pass for a fresh, empty list"""

    _, client = person()
    client.post("/skills", json={"skill_md": CHART})
    monkeypatch.setattr(skills, "on_server", False)
    monkeypatch.setattr(remote, "server", lambda: "http://server")
    monkeypatch.setattr(skills.httpx, "get", lambda url, params=None, **kwargs: client.get(url.removeprefix("http://server"), params=params))
    monkeypatch.setattr(skills.time, "monotonic", lambda: 30.0)
    skills.forget_cached()
    (tmp_path / "project").mkdir()
    config = {"configurable": {"workdir": str(tmp_path / "project")}}
    assert [s["name"] for s in skills.available(config)] == ["chart-maker"]
    skills.forget_cached()


def test_an_agent_loads_the_skills_it_was_given(accounts, monkeypatch):
    owner, alices = person()
    _, bobs = person()
    alices.post("/skills", json={"skill_md": CHART})
    alices.post("/skills", json={"skill_md": "---\nname: private-notes\ndescription: Alice's own way of taking notes\n---\nBe terse."})
    bobs.post("/skills", json={"skill_md": "---\nname: bobs-skill\ndescription: Bob's own skill\n---\nDo it Bob's way."})
    monkeypatch.setattr(runner, "name_chat", lambda thread_id, config: None)
    assert alices.post("/agents", json={"name": "x", "skills": ["no-such-skill"]}).status_code == 400

    # Shared, with one of the owner's skills: Bob gets that one (read as the owner), not his own
    charts = alices.post("/agents", json={"name": "Charts", "skills": ["chart-maker"], "shared": True}).json()
    fake = replies(monkeypatch, ai("", [("use_skill", {"name": "chart-maker"})]), ai("Done."))
    events = [json.loads(line) for line in bobs.post("/chat", json={"message": "a chart please", "agent_id": charts["id"]}).text.splitlines()]
    prompt = fake.seen[0][0].content
    assert "- chart-maker:" in prompt and "private-notes" not in prompt and "bobs-skill" not in prompt
    assert "skill_writing" not in fake.offered[0]
    assert not next(e for e in events if e["type"] == "tool_result")["error"]
    assert "# Chart maker" in fake.seen[1][-1].content

    # None: no skills and no skill tools, though Bob has some
    plain = bobs.post("/agents", json={"name": "Plain", "skills": "none"}).json()
    fake = replies(monkeypatch, ai("Ok."))
    bobs.post("/chat", json={"message": "hi", "agent_id": plain["id"]})
    assert not {"skills", "skill_writing"} & fake.offered[0] and "Skills you can use" not in fake.seen[0][0].content

    # A copy keeps the skills its new owner can use
    assert bobs.post(f"/agents/{charts['id']}/duplicate").json()["skills"] == []


def test_the_model_saves_a_skill_when_asked(tmp_path, home, local, accounts, monkeypatch):
    work = tmp_path / "project"
    work.mkdir()
    config = {"configurable": {"workdir": str(work)}}
    saved = skill_tools_save.invoke({"name": "release-notes", "description": "Writes release notes. Use for a changelog.",
                                     "instructions": "Group into New, Better, Fixed."}, config)
    path = home / ".labb" / "skills" / "release-notes" / "SKILL.md"
    assert str(path) in saved and skills.parse(path.read_text())[1] == "Group into New, Better, Fixed."
    assert skills.find(config, "release-notes")["source"] == "personal"
    again = {"name": "release-notes", "description": "Better notes", "instructions": "x"}
    assert skill_tools_save.invoke(again, config).startswith("Error: There's a skill called release-notes already")
    assert "Saved" in skill_tools_save.invoke({**again, "replace": True}, config)
    assert "Saved" in skill_tools_save.invoke({**again, "name": "project-notes", "project": True}, config)
    assert (work / ".labb" / "skills" / "project-notes" / "SKILL.md").exists()
    assert skill_tools_save.invoke({**again, "name": "Bad Name"}, config).startswith("Error:")

    # In the web chat, to the account's skills
    monkeypatch.setattr(skills, "on_server", True)
    _, client = person()
    monkeypatch.setattr(runner, "name_chat", lambda thread_id, config: None)
    replies(monkeypatch, ai("", [("save_skill", {"name": "standup", "description": "Writes a standup update. Use on weekday mornings.",
                                                  "instructions": "Yesterday, today, blockers."})]), ai("Saved."))
    client.post("/chat", json={"message": "save how we write standups as a skill"})
    assert [s["name"] for s in client.get("/skills").json() if s["mine"]] == ["standup"]


def test_a_skill_is_written_from_a_description(accounts, monkeypatch):
    from types import SimpleNamespace

    from langchain_core.messages import AIMessage

    from labb.server import skill_drafts

    _, alices = person()
    alices.post("/skills", json={"skill_md": CHART})
    asked = []

    class Model:
        def __init__(self, answers):
            self.answers = list(answers)

        def invoke(self, messages):
            asked.append(messages)
            return AIMessage(self.answers.pop(0), usage_metadata={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30})

    def use(*answers):
        monkeypatch.setattr(skill_drafts, "models_for", lambda spec, user_id: SimpleNamespace(model=Model(answers), spec="fake:model"))

    # In a code block with words around it, and the names already taken are said
    use("Here it is:\n```markdown\n" + CHART.replace("chart-maker", "release-notes") + "```\nEnjoy!")
    drafted = alices.post("/skills/draft", json={"ask": "Write release notes from a changelog"}).json()
    assert drafted["name"] == "release-notes" and drafted["skill_md"].startswith("---\nname: release-notes\n")
    assert "chart-maker" in asked[-1][1].content
    # Nothing is saved
    assert [s["name"] for s in alices.get("/skills").json() if s["mine"]] == ["chart-maker"]

    # Not a valid skill: asked again once with what's wrong
    use("no front matter at all", CHART)
    assert alices.post("/skills/draft", json={"ask": "charts", "current": CHART}).json()["name"] == "chart-maker"
    assert "isn't a valid SKILL.md" in asked[-1][-1].content and "Change it: charts" in asked[-1][1].content
    use("nope", "still nope")
    assert "didn't write a valid skill" in alices.post("/skills/draft", json={"ask": "charts"}).json()["detail"]
    assert alices.post("/skills/draft", json={"ask": "  "}).status_code == 400
