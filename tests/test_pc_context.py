"""The context panel for a session on a PC: the server asks the PC, whose session process answers
from what its replies run with (cli/app.answer_context), as the server can't rebuild that."""

import threading

import pytest
from fastapi.testclient import TestClient

from labb import runner
from conftest import ai


class PcLine:
    """A connected PC's line whose session answers the context panel's questions, like labb connect does"""

    def __init__(self, pcs, user_id: str, options: dict | None = None):
        self.pcs, self.user_id, self.options, self.asked = pcs, user_id, options or {}, []
        self.queue = type("Empty", (), {"empty": lambda self: True})()

    def send(self, message):
        from labb.cli import app as cli_app

        if message["type"] == "context":
            self.asked.append(message)
            answer = cli_app.answer_context(message["thread_id"], message["folder"], message, self.options)
            threading.Thread(target=self.pcs.answered, args=(self.user_id, message["id"], answer)).start()


@pytest.fixture
def on_pc(accounts, tmp_path, fake_model):
    from labb.server import app as server, pcs  # noqa: F401 (the server, with its routes)

    user_id = accounts["admin"][0]["id"]
    folder = str(tmp_path)
    pc_id = pcs.register(user_id, "context-laptop", [folder], "1", False, [])
    line = PcLine(pcs, user_id)
    pcs.connected(pc_id, line, [])
    session = pcs.new_session(user_id, pc_id, folder, "Fix it")
    # The PC's reply, in the database the server reads too (here they're one)
    fake_model(ai("Looked at it."))
    runner.run_reply(session["thread_id"], "Fix the build", threading.Event(), lambda e: None, configurable={"workdir": folder}, name=False)
    yield session, line, pc_id
    pcs._lines.pop(pc_id, None)


def test_the_pc_answers_what_the_panel_shows(client, on_pc):
    session, line, _ = on_pc
    thread = session["thread_id"]
    seen = client.get("/context", params={"thread_id": thread}).json()
    # Its terminal prompt: what the server alone couldn't rebuild
    assert "How to work" in str(seen) and seen["total_messages"] == 2
    assert line.asked[-1]["what"] == "seen" and line.asked[-1]["folder"] == session["directory"]
    raw = client.get("/context/raw", params={"thread_id": thread}).json()
    assert "coding agent" in raw["messages"][0]["content"] and any(t["function"]["name"] == "read_file" for t in raw["tools"])
    assert client.get("/context/message", params={"thread_id": thread, "number": 0}).json()["text"] == "Fix the build"
    assert client.get("/context/message", params={"thread_id": thread, "number": 9}).status_code == 404
    assert isinstance(client.get("/context/traces", params={"thread_id": thread}).json(), list)


def test_what_if_asks_the_pc_with_the_choice(client, on_pc):
    session, line, _ = on_pc
    client.get("/context", params={"thread_id": session["thread_id"], "model": "", "answer_mode": "fast"})
    assert line.asked[-1]["answer_mode"] == "fast" and line.asked[-1]["model"] is None


def test_summarize_now_on_the_pc(client, on_pc, monkeypatch):
    from labb import context

    session, _, _ = on_pc
    monkeypatch.setattr(context, "summarize", lambda *args, **kwargs: "The summary.")
    response = client.post("/context/summarize", json={"thread_id": session["thread_id"]})
    assert response.status_code == 200 and response.json()["summarized"] > 0


def test_an_offline_pc_says_so(client, on_pc):
    from labb.server import pcs

    session, _, pc_id = on_pc
    pcs._lines.pop(pc_id, None)
    response = client.get("/context", params={"thread_id": session["thread_id"]})
    assert response.status_code == 502 and "isn't connected" in response.json()["detail"]


def test_nobody_elses(on_pc, accounts):
    from labb.server import app as server

    session, _, _ = on_pc
    bob = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert bob.get("/context", params={"thread_id": session["thread_id"]}).status_code == 404


def test_the_session_answers_only_about_itself(tmp_path):
    from labb.cli import app as cli_app

    assert "error" in cli_app.answer_context("cli-a", str(tmp_path), {"thread_id": "cli-b", "what": "seen"}, {})


def test_the_work_of_a_session_comes_from_its_project_on_the_pc(client, on_pc):
    from labb import work

    session, line, _ = on_pc
    config = {"configurable": {"workdir": session["directory"], "thread_id": session["thread_id"]}}
    work.write_json(config, "todos", [{"id": 1, "task": "Fix the build", "status": "in_progress"}])
    work.write(config, "plan", "# Approved plan\n\n1. Fix the build\n")
    found = client.get("/context/work", params={"thread_id": session["thread_id"]}).json()
    assert line.asked[-1]["what"] == "work"
    assert found["todos"][0]["task"] == "Fix the build" and found["open"] == 1
    assert found["plan"].startswith("# Approved plan") and found["plan_file"] == f".labb/plans/{session['thread_id']}.md"
    assert found["goal"] is None
