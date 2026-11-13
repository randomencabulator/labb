"""Notes: a project's file in the terminal, each person's own on the server, and a PC's folders'
from the web page (tools/notes.py, server/notes.py, pcs.ask_notes, cli/connect.notes_answer)."""

import threading

import pytest
from fastapi.testclient import TestClient

from labb import model
from labb.tools import notes, offered_for


@pytest.fixture
def server():
    from labb.server import app as server
    return server


def test_your_notes_on_the_server(client):
    assert client.get("/notes").json() == {"text": "", "digest": notes.digest(""), "topics": []}
    added = client.post("/notes", json={"action": "add", "topic": "Trip", "text": "Train at 9."}).json()
    assert added["text"].startswith("## Trip (") and added["text"].endswith("\nTrain at 9.")
    assert added["topics"][0].startswith("Trip")
    # A change made over an older read is refused
    stale = client.post("/notes", json={"action": "replace", "text": "Mine", "base": notes.digest("")})
    assert stale.status_code == 409
    replaced = client.post("/notes", json={"action": "replace", "text": "## Plan\nPack.", "base": added["digest"]}).json()
    assert replaced["text"] == "## Plan\nPack." and replaced["topics"] == ["Plan"]
    assert client.post("/notes", json={"action": "add", "text": "  "}).status_code == 400
    assert client.post("/notes", json={"action": "replace", "text": "x" * (notes.MAX_NOTES_CHARS + 1)}).status_code == 400


def test_nobody_else_sees_them(client, server, accounts):
    client.post("/notes", json={"action": "replace", "text": "Admin's"})
    bob = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert "Admin's" not in bob.get("/notes").json()["text"]


def test_the_web_chats_agent_keeps_your_notes(server, accounts):
    user_id = accounts["member"][0]["id"]
    config = {"configurable": {"user_id": user_id}}
    notes.store.change(user_id, "replace", "")
    assert "notes" in (offered_for(config) or {"notes"})
    assert notes.write_notes.invoke({"text": "Uses Postgres.", "topic": "Stack"}, config).startswith("Saved")
    assert "Uses Postgres." in notes.read_notes.invoke({}, config)
    # The model is told the topics, and gets the web's prompt
    assert "Stack" in model.work_reminder(config)
    assert "Notes page" in model.system_prompt(config)


def test_without_a_project_or_a_person_there_are_no_notes(server):
    assert "notes" not in offered_for({"configurable": {}})


def test_the_terminal_keeps_the_projects_file(config):
    assert notes.write_notes.invoke({"text": "Run make test.", "topic": "Tests"}, config).startswith("Saved")
    text = notes.notes_file(config["configurable"]["workdir"]).read_text()
    assert "## Tests" in text and "Run make test." in text


class AnsweringPc:
    """A connected PC's line that answers notes questions like labb connect does"""

    def __init__(self, pcs, user_id: str, folders: list[str]):
        self.pcs, self.user_id, self.folders = pcs, user_id, folders
        self.queue = type("Empty", (), {"empty": lambda self: True})()

    def send(self, message):
        from labb.cli.connect import notes_answer

        if message["type"] == "notes":
            answer = notes_answer(self.folders, message)
            threading.Thread(target=self.pcs.answered, args=(self.user_id, message["id"], answer)).start()


@pytest.fixture
def pc(server, accounts, tmp_path):
    from labb.server import pcs

    user_id = accounts["admin"][0]["id"]
    folder = str(tmp_path)
    pc_id = pcs.register(user_id, "notes-laptop", [folder], "1", False, [])
    pcs.connected(pc_id, AnsweringPc(pcs, user_id, [folder]), [])
    yield pc_id, folder
    pcs._lines.pop(pc_id, None)


def logged_in(server, accounts):
    client = TestClient(server.app, headers={"X-Labb": "1"})
    assert client.post("/auth/login", json={"username": "admin", "password": "admin-password"}, headers={"X-Labb": "1"}).status_code == 200
    return client


def test_a_pc_folders_notes_from_the_web(server, accounts, pc):
    pc_id, folder = pc
    page = logged_in(server, accounts)
    assert page.get(f"/pcs/{pc_id}/notes", params={"folder": folder}).json()["text"] == ""
    added = page.post(f"/pcs/{pc_id}/notes", json={"folder": folder, "action": "add", "topic": "Build", "text": "uv run pytest"}).json()
    assert "uv run pytest" in notes.notes_file(folder).read_text() and added["topics"][0].startswith("Build")
    stale = page.post(f"/pcs/{pc_id}/notes", json={"folder": folder, "action": "replace", "text": "x", "base": notes.digest("")})
    assert stale.status_code == 409
    # Only the folders it serves, and only from the page (a key can't)
    assert page.get(f"/pcs/{pc_id}/notes", params={"folder": "/etc"}).status_code == 502
    keyed = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['admin'][1]}"})
    assert keyed.get(f"/pcs/{pc_id}/notes", params={"folder": folder}).status_code == 403


def test_the_pc_checks_the_folder_itself(tmp_path):
    from labb.cli.connect import notes_answer

    assert "error" in notes_answer([str(tmp_path)], {"folder": "/etc", "action": "read"})
    assert notes_answer([str(tmp_path)], {"folder": str(tmp_path), "action": "read"}) == {"text": ""}


def test_an_offline_pc_says_so(server, accounts):
    from labb.server import pcs

    user_id = accounts["admin"][0]["id"]
    pc_id = pcs.register(user_id, "asleep", ["/code"], "1", False, [])
    page = logged_in(server, accounts)
    response = page.get(f"/pcs/{pc_id}/notes", params={"folder": "/code"})
    assert response.status_code == 502 and "isn't connected" in response.json()["detail"]


def test_the_pcs_answer_comes_back_with_its_key(server, accounts, monkeypatch):
    from labb.server import pcs

    user_id = accounts["admin"][0]["id"]
    waiting = {"user_id": user_id, "done": threading.Event(), "answer": None}
    monkeypatch.setitem(pcs._asks, "ask-1", waiting)
    keyed = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['admin'][1]}"})
    assert keyed.post("/pcs/answers/ask-1", json={"text": "hi"}).status_code == 204
    assert waiting["answer"]["text"] == "hi" and waiting["done"].is_set()
    bob = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert bob.post("/pcs/answers/ask-1", json={"text": "no"}).status_code == 404


def test_an_apps_chat_keeps_the_apps_notes_not_yours(server, accounts):
    user_id = accounts["member"][0]["id"]
    notes.store.change(user_id, "replace", "## Holidays\nFlights on Friday.")
    todo_app = {"configurable": {"user_id": user_id, "app": {"id": "0123456789ab", "name": "Todo"}}}
    shop_app = {"configurable": {"user_id": user_id, "app": {"id": "ba9876543210", "name": "Shop"}}}
    assert "notes" in (offered_for(todo_app) or {"notes"})
    assert notes.write_notes.invoke({"text": "Items are in /data/app.db.", "topic": "Storage"}, todo_app).startswith("Saved")
    # The app's chat reads only the app's notes, and is told only their topics
    read = notes.read_notes.invoke({}, todo_app)
    assert "Items are in /data/app.db." in read and "Flights" not in read
    reminder = model.work_reminder(todo_app)
    assert "Storage" in reminder and "Holidays" not in reminder
    # Your notes and another app's don't get them
    assert "app.db" not in notes.store.read(user_id)
    assert "app.db" not in notes.read_notes.invoke({}, {"configurable": {"user_id": user_id}})
    assert notes.read_notes.invoke({}, shop_app) == "No notes yet."
