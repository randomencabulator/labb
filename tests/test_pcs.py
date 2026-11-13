"""Sessions on PCs connected with labb connect: whose they are, who can do what, how a reply goes, and
what the PC decides itself. The real thing, process and all, needs a model; here the PC's side is faked
at its edges."""

import io
import json
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

PATIENCE = 5


class FakeLine:
    """A PC's open request, without one"""

    def __init__(self):
        self.sent = []
        self.queue = type("Empty", (), {"empty": lambda self: True})()

    def send(self, message):
        self.sent.append(message)

    def jobs(self):
        return [m for m in self.sent if m["type"] == "job"]


@pytest.fixture
def server():
    from labb.server import app as server
    return server


@pytest.fixture
def pcs():
    from labb.server import pcs
    pcs._lines.clear()
    pcs._undelivered.clear()
    yield pcs
    pcs._lines.clear()
    pcs._undelivered.clear()


def browser(server, username, password):
    """Someone logged in to the web page: a session cookie, no key"""
    client = TestClient(server.app, headers={"X-Labb": "1"})
    assert client.post("/auth/login", json={"username": username, "password": password}, headers={"X-Labb": "1"}).status_code == 200
    return client


def with_key(server, key):
    return TestClient(server.app, headers={"Authorization": f"Bearer {key}"})


def connect_pc(pcs, user_id, name="laptop", folders=("/code",), active=(), models=("ollama:small",)):
    pc_id = pcs.register(user_id, name, list(folders), "1", False, list(models))
    line = FakeLine()
    pcs.connected(pc_id, line, list(active))
    return pc_id, line


def new_reply(pcs, user_id, pc_id, message="hi", **options):
    session = pcs.new_session(user_id, pc_id, "/code", message)
    return session, pcs.start(user_id, session["thread_id"], message, **options)


def finish(pcs, run, user_id, text="Done", seq=1):
    pcs.add_events(run["id"], user_id, [{"seq": seq, "event": {"type": "token", "content": text}},
                                        {"seq": seq + 1, "event": {"type": "done", "stats": {"model": "m"}}}])


def answering(pcs, line, user_id, *replies):
    """A PC that answers each message it gets with this text, in the background"""

    def work():
        for i, text in enumerate(replies):
            deadline = time.monotonic() + PATIENCE
            while len(line.jobs()) <= i and time.monotonic() < deadline:
                time.sleep(0.01)
            finish(pcs, pcs.get_run(line.jobs()[i]["run_id"], user_id), user_id, text)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


def events_of(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


# A chat on a PC

def test_a_person_starts_a_chat_on_their_pc_and_goes_on_with_it(server, pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id)
    bob = browser(server, "bob", "bob-password")

    answering(pcs, line, user_id, "First answer", "Second answer")
    first = bob.post("/chat", json={"message": "fix it", "pc_id": pc_id, "folder": "/code"})
    assert first.status_code == 200
    events = events_of(first)
    thread_id = events[0]["thread_id"]
    assert thread_id.startswith("cli-")
    assert [e["type"] for e in events] == ["thread", "token", "done"]
    assert events[1]["content"] == "First answer"

    # The same session, not a new one
    second = bob.post("/chat", json={"message": "and now?", "thread_id": thread_id})
    assert [e["type"] for e in events_of(second)] == ["token", "done"]
    jobs = line.jobs()
    assert [job["thread_id"] for job in jobs] == [thread_id, thread_id]
    assert [job["content"] for job in jobs] == ["fix it", "and now?"]


def test_a_job_says_only_this(pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id, "shape")
    new_reply(pcs, user_id, pc_id)
    # No tools or permissions: those are the PC's
    assert set(line.jobs()[0]) == {"type", "run_id", "thread_id", "folder", "content", "title", "questions", "model", "answer_mode",
                                   "replace_from", "keep_attachments"}


def test_the_message_boxs_choices_go_along(server, pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id, "choices")
    bob = browser(server, "bob", "bob-password")
    answering(pcs, line, user_id, "ok")
    response = bob.post("/chat", json={"message": "hi", "pc_id": pc_id, "folder": "/code", "model": "ollama:small", "answer_mode": "fast",
                                       "attachments": [{"name": "a.txt", "kind": "text", "data": "the file"}], "web": False, "browser": True})
    assert response.status_code == 200
    job = line.jobs()[0]
    assert (job["model"], job["answer_mode"]) == ("ollama:small", "fast")
    assert "the file" in job["content"] and job["title"] == "hi"


def test_only_models_the_pc_has(server, pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id, "models", models=["ollama:small"])
    bob = browser(server, "bob", "bob-password")
    response = bob.post("/chat", json={"message": "hi", "pc_id": pc_id, "folder": "/code", "model": "openai:gpt-other"})
    assert response.status_code == 409 and "doesn't have the model" in response.json()["detail"]
    assert not line.jobs()
    # And nothing's left behind of a chat that couldn't start
    assert not pcs.sessions_for(user_id) or all(s["pc_id"] != pc_id for s in pcs.sessions_for(user_id).values())


def test_a_server_skill_isnt_added_to_a_message_on_a_pc(server, pcs, accounts, monkeypatch):
    from labb import skills

    monkeypatch.setattr(skills, "find", lambda *a: pytest.fail("a PC has its own skills"))
    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id, "skills")
    answering(pcs, line, user_id, "ok")
    assert browser(server, "bob", "bob-password").post("/chat", json={"message": "/review this", "pc_id": pc_id, "folder": "/code"}).status_code == 200
    assert line.jobs()[0]["content"] == "/review this"


def test_one_reply_at_a_time_in_a_session(pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, _ = connect_pc(pcs, user_id, "busy")
    session, run = new_reply(pcs, user_id, pc_id)
    with pytest.raises(pcs.PcError):
        pcs.start(user_id, session["thread_id"], "and another")
    finish(pcs, run, user_id)
    assert pcs.start(user_id, session["thread_id"], "now it can")["status"] == "queued"


def test_the_session_is_listed_from_the_start_and_says_its_pc(server, pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, _ = connect_pc(pcs, user_id, "listed")
    session, _ = new_reply(pcs, user_id, pc_id, "my title")
    bob = browser(server, "bob", "bob-password")
    found = next(s for s in bob.get("/cli_sessions").json() if s["thread_id"] == session["thread_id"])
    assert (found["pc_id"], found["machine"], found["directory"], found["title"], found["synced"]) == (pc_id, "listed", "/code", "my title", False)


def test_once_synced_it_still_says_its_pc(server, pcs, accounts):
    user_id, key = accounts["member"]
    user_id = user_id["id"]
    pc_id, _ = connect_pc(pcs, user_id, "synced-one")
    session, run = new_reply(pcs, user_id, pc_id)
    finish(pcs, run, user_id)
    thread_id = session["thread_id"]
    sent = with_key(server, key).post(f"/sync/{thread_id}", json={"machine": "synced-one", "directory": "/code", "title": "hi", "updated_at": "2026-01-01T00:00:00",
                                                                  "messages": [], "stats": {}})
    assert sent.status_code == 204
    bob = browser(server, "bob", "bob-password")
    found = [s for s in bob.get("/cli_sessions").json() if s["thread_id"] == thread_id]
    assert len(found) == 1 and found[0]["pc_id"] == pc_id and found[0]["synced"] is True
    # A terminal's own session has none
    with_key(server, key).post("/sync/cli-from-a-terminal", json={"machine": "m", "directory": "/d", "title": "t", "updated_at": "2026-01-01T00:00:00", "messages": [], "stats": {}})
    own = next(s for s in bob.get("/cli_sessions").json() if s["thread_id"] == "cli-from-a-terminal")
    assert own["pc_id"] is None
    # Deleting it forgets the session here
    assert bob.delete(f"/sync/{thread_id}").status_code == 204
    assert pcs.session(thread_id, user_id) is None


def test_deleting_waits_for_the_reply(server, pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, _ = connect_pc(pcs, user_id, "deleting")
    session, run = new_reply(pcs, user_id, pc_id)
    bob = browser(server, "bob", "bob-password")
    assert bob.delete(f"/sync/{session['thread_id']}").status_code == 409
    finish(pcs, run, user_id)
    assert bob.delete(f"/sync/{session['thread_id']}").status_code == 204


def test_a_page_can_join_a_reply_being_written(server, pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id, "joined")
    session, run = new_reply(pcs, user_id, pc_id, "the question")
    bob = browser(server, "bob", "bob-password")
    pcs.add_events(run["id"], user_id, [{"seq": 1, "event": {"type": "token", "content": "So far"}}])
    threading.Timer(0.2, lambda: pcs.add_events(run["id"], user_id, [{"seq": 2, "event": {"type": "done", "stats": None}}])).start()
    events = events_of(bob.get("/pcs/live", params={"thread_id": session["thread_id"]}))
    assert events[0] == {"type": "user", "content": "the question", "turn": 0}
    assert [e["type"] for e in events[1:]] == ["token", "done"]
    # What the chat needs, also when the PC had none to give
    assert events[-1]["stats"]["duration_ms"] is not None
    # Nothing to join any more
    assert bob.get("/pcs/live", params={"thread_id": session["thread_id"]}).status_code == 204


def test_a_reply_that_was_lost_says_so(server, pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id, "losing")
    session, run = new_reply(pcs, user_id, pc_id)
    pcs.add_events(run["id"], user_id, [{"seq": 1, "event": {"type": "token", "content": "Half"}}])
    bob = browser(server, "bob", "bob-password")
    # It comes back saying it isn't doing that any more
    threading.Timer(0.2, lambda: pcs.connected(pc_id, FakeLine(), [])).start()
    events = events_of(bob.get("/pcs/live", params={"thread_id": session["thread_id"]}))
    assert [e["type"] for e in events] == ["user", "token", "error", "stopped"]
    assert "stopped this run" in events[2]["message"]


def test_stop_and_answers_go_through_the_chats_endpoints(server, pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id, "chatting")
    session, run = new_reply(pcs, user_id, pc_id)
    pcs.add_events(run["id"], user_id, [{"seq": 1, "event": {"type": "approval", "id": "q1", "title": "t", "detail": "d"}},
                                        {"seq": 2, "event": {"type": "question", "id": "q2", "question": "Which?", "options": ["a", "b"]}}])
    bob = browser(server, "bob", "bob-password")
    assert bob.post("/chat/approve", json={"id": "q1", "allow": False, "reason": "no"}).status_code == 204
    assert bob.post("/chat/answer", json={"id": "q2", "text": "a"}).status_code == 204
    answers = [m for m in line.sent if m["type"] == "answer"]
    assert (answers[0]["id"], answers[0]["allow"], answers[0]["reason"]) == ("q1", False, "no")
    assert (answers[1]["id"], answers[1]["text"]) == ("q2", "a")
    assert bob.post("/stop", json={"thread_id": session["thread_id"]}).status_code == 204
    assert line.sent[-1] == {"type": "stop", "run_id": run["id"]}


def test_stopping_while_the_pc_is_away_doesnt_leave_the_chat_waiting(pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id, "away")
    session, run = new_reply(pcs, user_id, pc_id)
    pcs.add_events(run["id"], user_id, [{"seq": 1, "event": {"type": "token", "content": "x"}}])
    pcs.disconnected(pc_id, line, user_id)
    pcs.stop(run["id"], user_id)
    assert pcs.get_run(run["id"], user_id)["status"] == "stopped"
    assert pcs.active_run(session["thread_id"], user_id) is None
    # It still hears about it when it's back
    again = FakeLine()
    pcs.connected(pc_id, again, [run["id"]])
    assert {"type": "stop", "run_id": run["id"]} in again.sent


# Who can do what

def test_an_api_key_cant_send_or_answer(server, pcs, accounts):
    user, key = accounts["member"]
    pc_id, line = connect_pc(pcs, user["id"], "keys")
    client = with_key(server, key)
    assert client.post("/chat", json={"message": "rm -rf", "pc_id": pc_id, "folder": "/code"}).status_code == 403
    session, run = new_reply(pcs, user["id"], pc_id)
    pcs.add_events(run["id"], user["id"], [{"seq": 1, "event": {"type": "approval", "id": "q1", "title": "t", "detail": "d"}},
                                           {"seq": 2, "event": {"type": "question", "id": "q2", "question": "q", "options": ["a", "b"]}}])
    sent = len(line.sent)
    assert client.post("/chat", json={"message": "rm -rf", "thread_id": session["thread_id"]}).status_code == 403
    assert client.post("/chat/approve", json={"id": "q1", "allow": True}).status_code == 404
    assert client.post("/chat/answer", json={"id": "q2", "text": "a"}).status_code == 404
    assert client.post("/stop", json={"thread_id": session["thread_id"]}).status_code == 403
    assert client.delete(f"/pcs/{pc_id}").status_code == 403
    assert len(line.sent) == sent


def test_a_session_cant_pose_as_a_pc(server, pcs):
    bob = browser(server, "bob", "bob-password")
    assert bob.post("/pcs/connect", json={"machine": "x", "folders": ["/"]}).status_code == 403
    assert bob.post("/pcs/events", json={"run_id": "x", "events": []}).status_code == 403


def test_someone_elses_pc_and_sessions_dont_exist(server, pcs, accounts):
    admin_id = accounts["admin"][0]["id"]
    pc_id, line = connect_pc(pcs, admin_id, "admins-pc")
    session, run = new_reply(pcs, admin_id, pc_id, "secret work")
    pcs.add_events(run["id"], admin_id, [{"seq": 1, "event": {"type": "approval", "id": "admins-q1", "title": "t", "detail": "d"}}])
    sent = len(line.sent)
    thread_id = session["thread_id"]

    bob = browser(server, "bob", "bob-password")
    assert all(pc["id"] != pc_id for pc in bob.get("/pcs").json())
    assert all(s["thread_id"] != thread_id for s in bob.get("/cli_sessions").json())
    assert bob.post("/chat", json={"message": "hi", "pc_id": pc_id, "folder": "/code"}).status_code == 404
    assert bob.post("/chat", json={"message": "hi", "thread_id": thread_id}).status_code == 404
    assert bob.get("/pcs/live", params={"thread_id": thread_id}).status_code == 204
    assert bob.get("/history", params={"thread_id": thread_id}).json() == []
    assert bob.post("/chat/approve", json={"id": "admins-q1", "allow": True}).status_code == 404
    assert bob.post("/chat/answer", json={"id": "admins-q1", "text": "x"}).status_code == 404
    assert bob.post("/stop", json={"thread_id": thread_id}).status_code == 404
    assert bob.delete(f"/pcs/{pc_id}").status_code == 404
    assert bob.delete(f"/sync/{thread_id}").status_code == 404
    # Nor can their PC report on it
    key_client = with_key(server, accounts["member"][1])
    assert key_client.post("/pcs/events", json={"run_id": run["id"], "events": [{"seq": 2, "event": {"type": "done"}}]}).status_code == 404
    assert len(line.sent) == sent
    assert pcs.get_run(run["id"], admin_id)["status"] == "waiting"
    assert pcs.session(thread_id, admin_id)


def test_same_name_for_two_people_is_two_pcs(pcs, accounts):
    a = pcs.register(accounts["admin"][0]["id"], "same", ["/a"], "1", False)
    b = pcs.register(accounts["member"][0]["id"], "same", ["/b"], "1", False)
    assert a != b


def test_only_folders_it_serves_and_only_when_connected(pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, _ = connect_pc(pcs, user_id, "narrow", ["/code"])
    with pytest.raises(pcs.PcError):
        pcs.new_session(user_id, pc_id, "/etc", "hi")
    session = pcs.new_session(user_id, pc_id, "/code", "hi")
    with pytest.raises(pcs.PcError):
        pcs.start(user_id, session["thread_id"], "   ")
    # It stops serving that folder
    pcs.register(user_id, "narrow", ["/other"], "1", False)
    with pytest.raises(pcs.PcError):
        pcs.start(user_id, session["thread_id"], "hi")
    pcs.register(user_id, "narrow", ["/code"], "1", False)
    pcs._lines.clear()
    with pytest.raises(pcs.PcError):
        pcs.start(user_id, session["thread_id"], "hi")


# A reply's life

def test_the_status_follows_the_events_and_sending_twice_is_harmless(pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id, "flow")
    _, run = new_reply(pcs, user_id, pc_id)
    run_id = run["id"]
    assert run["status"] == "queued"

    batch = [{"seq": 1, "event": {"type": "token", "content": "Hel"}}, {"seq": 2, "event": {"type": "approval", "id": "q", "title": "t", "detail": "d"}}]
    pcs.add_events(run_id, user_id, batch)
    pcs.add_events(run_id, user_id, batch)
    run = pcs.get_run(run_id, user_id)
    assert (run["status"], run["waiting"], run["result"]) == ("waiting", ["q"], "Hel")

    # Not answered until the PC says so: the answer may have been lost, so it can be sent again
    pcs.answer(run_id, user_id, "q", allow=True)
    pcs.answer(run_id, user_id, "q", allow=True)
    assert [m["allow"] for m in line.sent if m["type"] == "answer"] == [True, True]
    pcs.add_events(run_id, user_id, [{"seq": 3, "event": {"type": "answered", "id": "q"}}])
    assert pcs.get_run(run_id, user_id)["status"] == "running"
    with pytest.raises(pcs.PcError):
        pcs.answer(run_id, user_id, "q", allow=True)

    pcs.add_events(run_id, user_id, [{"seq": 4, "event": {"type": "token", "content": "lo"}}, {"seq": 5, "event": {"type": "done", "stats": {}}}])
    run = pcs.get_run(run_id, user_id)
    assert (run["status"], run["result"]) == ("done", "Hello")
    assert run["finished_at"]
    assert [e["seq"] for e in pcs.events_after(run_id, 3)] == [4, 5]


def test_an_error_makes_it_failed(pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, _ = connect_pc(pcs, user_id, "failing")
    _, run = new_reply(pcs, user_id, pc_id)
    pcs.add_events(run["id"], user_id, [{"seq": 1, "event": {"type": "error", "message": "no model"}}, {"seq": 2, "event": {"type": "done", "stats": {}}}])
    run = pcs.get_run(run["id"], user_id)
    assert (run["status"], run["error"]) == ("failed", "no model")


def test_reconnecting_sorts_out_what_happened(pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id, "flaky")
    _, alive = new_reply(pcs, user_id, pc_id, "alive")
    _, dead = new_reply(pcs, user_id, pc_id, "dead")
    _, queued = new_reply(pcs, user_id, pc_id, "queued")
    for run in (alive, dead):
        pcs.add_events(run["id"], user_id, [{"seq": 1, "event": {"type": "token", "content": "x"}}])
    pcs.disconnected(pc_id, line, user_id)
    assert not pcs.get(pc_id, user_id)["online"]
    # A stop while it's away reaches it when it's back; asking again after the mark doesn't matter
    pcs._undelivered.setdefault(pc_id, []).append({"type": "stop", "run_id": alive["id"]})

    again = FakeLine()
    pcs.connected(pc_id, again, [alive["id"]])
    assert pcs.get_run(alive["id"], user_id)["status"] == "running"
    assert pcs.get_run(dead["id"], user_id)["status"] == "lost"
    # What it missed: the stop, and the job it never started, whole
    assert {"type": "stop", "run_id": alive["id"]} in again.sent
    jobs = again.jobs()
    assert [m["run_id"] for m in jobs] == [queued["id"]] and jobs[0]["content"] == "queued"


def test_an_old_job_isnt_started_later(pcs, accounts):
    from labb.chats import conn, lock

    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id, "sleepy")
    _, run = new_reply(pcs, user_id, pc_id)
    pcs.disconnected(pc_id, line, user_id)
    with lock, conn:
        conn.execute("UPDATE pc_runs SET created_at = '2020-01-01T00:00:00Z' WHERE id = ?", (run["id"],))
    again = FakeLine()
    pcs.connected(pc_id, again, [])
    assert pcs.get_run(run["id"], user_id)["status"] == "failed"
    assert not again.jobs()


def test_a_new_connection_replaces_the_old(pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, old = connect_pc(pcs, user_id, "twice")
    new = FakeLine()
    pcs.connected(pc_id, new, [])
    assert old.sent[-1] == {"type": "replaced"}
    # The old request ending doesn't take the new one down
    pcs.disconnected(pc_id, old, user_id)
    assert pcs.get(pc_id, user_id)["online"]


def test_a_connected_pc_cant_be_forgotten(server, pcs, accounts):
    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id, "forgetme")
    session, run = new_reply(pcs, user_id, pc_id)
    bob = browser(server, "bob", "bob-password")
    assert bob.delete(f"/pcs/{pc_id}").status_code == 409
    pcs.disconnected(pc_id, line, user_id)
    assert bob.delete(f"/pcs/{pc_id}").status_code == 204
    assert pcs.get(pc_id, user_id) is None
    assert pcs.get_run(run["id"], user_id) is None
    assert pcs.session(session["thread_id"], user_id) is None


# The session's process on the PC

def channel(*lines):
    from labb.cli.channel import Channel

    return Channel(io.StringIO("\n".join([json.dumps({"thread_id": "cli-1"}), *map(json.dumps, lines)]) + "\n"))


def message_line(run_id="r1", **more):
    return {"type": "message", "run_id": run_id, "content": "hi", "title": "hi", **more}


def open_channel():
    from labb.cli.channel import Channel

    read_fd, write_fd = os.pipe()
    reader, writer = os.fdopen(read_fd), os.fdopen(write_fd, "w")
    writer.write(json.dumps({"thread_id": "cli-1"}) + "\n")
    writer.flush()
    return Channel(reader), writer


def answering_channel(with_answer: dict, ask, **message):
    """Ask through a channel whose other end answers as soon as it's asked"""

    from labb.cli.channel import Message

    ch, writer = open_channel()
    events = []

    def emit(event):
        events.append(event)
        if event["type"] in ("approval", "question"):
            writer.write(json.dumps({"type": "answer", "id": event["id"], **with_answer}) + "\n")
            writer.flush()

    configurable = {}
    stop = threading.Event()
    ch.attach(Message.of(message_line(**message)), configurable, emit, stop)
    result = ask(configurable)
    writer.close()
    return result, events, stop


def test_messages_come_one_after_another_and_a_closed_channel_ends_it():
    ch = channel(message_line("r1"), message_line("r2", model="m", answer_mode="fast", replace_from=3, keep_attachments=True, questions=False))
    first, second = ch.next_message(), ch.next_message()
    assert first.run_id == "r1" and first.questions is True and first.model is None
    assert (second.run_id, second.model, second.answer_mode, second.replace_from, second.keep_attachments, second.questions) == ("r2", "m", "fast", 3, True, False)
    assert ch.next_message() is None


def test_the_channel_says_yes_only_to_a_real_yes():
    approve = lambda c: c["approve"]("rm x", ["rm"], None)
    assert answering_channel({"allow": True}, approve)[0] == "yes"
    assert answering_channel({"allow": False, "reason": "no way"}, approve)[0] == ("no", "no way")
    # Only for this action: "always" isn't something it can say, whatever it's sent
    assert answering_channel({"allow": "always"}, approve)[0] == ("no", None)
    assert answering_channel({"answer": "always"}, approve)[0] == ("no", None)


def test_the_channel_passes_on_questions():
    answer, events, _ = answering_channel({"text": "SQLite"}, lambda c: c["ask_user"]("Where?", ["a", "b"]))
    assert answer == "SQLite"
    assert [e["type"] for e in events] == ["question", "answered"]


def test_without_someone_to_ask_questions_arent_offered():
    from labb.cli.channel import Message

    ch, writer = open_channel()
    configurable = {}
    ch.attach(Message.of(message_line(questions=False)), configurable, lambda e: None, threading.Event())
    writer.close()
    assert "approve" in configurable and "ask_user" not in configurable


def test_a_closed_channel_stops_the_reply():
    from labb.cli.channel import Message

    ch, writer = open_channel()
    stop = threading.Event()
    ch.attach(Message.of(message_line()), {}, lambda e: None, stop)
    writer.close()
    assert stop.wait(PATIENCE)


def test_a_stop_reaches_only_its_own_reply_even_if_it_came_first():
    from labb.cli.channel import Message

    ch, writer = open_channel()
    stop = threading.Event()
    ch.attach(Message.of(message_line("r1")), {}, lambda e: None, stop)
    writer.write(json.dumps({"type": "stop", "run_id": "r2"}) + "\n")
    writer.flush()
    time.sleep(0.1)
    assert not stop.is_set()
    # The stop for r2 came before r2 began
    second = threading.Event()
    ch.attach(Message.of(message_line("r2")), {}, lambda e: None, second)
    assert second.is_set()
    writer.close()


def test_a_stop_refuses_whats_waiting():
    from labb.cli.channel import Message

    ch, writer = open_channel()
    stop = threading.Event()
    stop.set()
    configurable = {}
    ch.attach(Message.of(message_line()), configurable, lambda e: None, stop)
    assert configurable["approve"]("rm x", [], None) == ("no", None)
    writer.close()


def test_the_session_is_with_the_server_before_the_reply_ends(capsys):
    import queue

    ch, writer = open_channel()
    events = queue.Queue()
    order = []
    events.put({"type": "token", "content": "x"})
    events.put({"type": "done", "stats": {}})
    events.put(None)
    final = ch.relay(events, lambda: order.append(capsys.readouterr().out))
    printed = capsys.readouterr().out
    assert '"done"' in printed and '"done"' not in order[0] and '"token"' in order[0]
    assert final["type"] == "done" and not final["failed"]
    writer.close()


def test_an_edited_message_keeps_what_only_the_pc_has(monkeypatch):
    from langchain_core.messages import HumanMessage

    from labb.cli import app
    from labb.cli.channel import Message

    saved = HumanMessage("Before\n\nAttached:\n\n<file path=\"a.txt\">\nthe file\n</file>")
    monkeypatch.setattr(app, "user_message", lambda thread, turn: saved)
    edited = Message.of(message_line(replace_from=0, keep_attachments=True) | {"content": "After"})
    assert app.channel_content("cli-1", edited).startswith("After\n\nAttached:")
    assert "the file" in app.channel_content("cli-1", edited)
    assert app.channel_content("cli-1", Message.of(message_line() | {"content": "New"})) == "New"
    # Replacing a turn that isn't there is for run_reply to say
    monkeypatch.setattr(app, "user_message", lambda thread, turn: [][turn])
    assert app.channel_content("cli-1", edited) == "After"


def test_a_session_stays_in_its_folder(monkeypatch):
    from labb import chats
    from labb.cli import app
    from labb.cli.channel import Message

    monkeypatch.setattr(chats, "list_all_cli", lambda: [{"thread_id": "cli-1", "directory": "/code"}])
    message = Message.of(message_line())
    assert app.channel_problem("cli-1", "/code", message, {}) is None
    assert "belongs to /code" in app.channel_problem("cli-1", "/other", message, {})


def test_the_cost_limit_holds_for_a_session_from_the_web(monkeypatch):
    from labb import chats, costs
    from labb.cli import app
    from labb.cli.channel import Message

    monkeypatch.setattr(chats, "list_all_cli", lambda: [])
    monkeypatch.setattr(chats, "get_stats", lambda thread: {"m": {}})
    monkeypatch.setattr(costs, "of_stats", lambda stats, prices: (3.0, []))
    assert "its limit" in app.channel_problem("cli-1", "/code", Message.of(message_line()), {"maxCostPerSession": 2})


def test_only_one_process_writes_in_a_session():
    import subprocess
    import sys

    from labb.cli import session_lock

    holder = (
        "import sys; from labb.cli import session_lock\n"
        "with session_lock.held(sys.argv[1]) as free: print(free)\n"
    )
    thread = f"cli-lock-test-{os.getpid()}"
    with session_lock.held(thread) as free:
        assert free is True
        other = subprocess.run([sys.executable, "-c", holder, thread], capture_output=True, text=True, env={**os.environ, "CHAT_DB": os.environ["CHAT_DB"]})
        assert other.stdout.strip() == "False", other.stderr
    other = subprocess.run([sys.executable, "-c", holder, thread], capture_output=True, text=True)
    assert other.stdout.strip() == "True", other.stderr


JOB = {"type": "job", "run_id": "00000000-0000-0000-0000-000000000001", "thread_id": "cli-00000000-0000-0000-0000-00000000000a",
       "folder": "/code", "content": "hi", "title": "hi"}


class FakeSession:
    made = []

    def __init__(self, thread_id, folder, allow_all, new_outbox, on_reply_end, on_exit):
        self.thread_id, self.folder, self.allow_all = thread_id, folder, allow_all
        self.reply = None
        self.last_used = time.monotonic()
        self.sent = []
        self.written = []
        self.closed = False
        self.is_alive = True
        FakeSession.made.append(self)

    def alive(self):
        return self.is_alive

    def send(self, job):
        if self.reply is not None:
            return False
        self.reply = type("Reply", (), {"run_id": job["run_id"]})()
        self.sent.append(job)
        return True

    def stop(self, run_id=None):
        self.written.append({"type": "stop", "run_id": run_id})

    def write(self, message):
        self.written.append(message)

    def close(self):
        self.closed = True


@pytest.fixture
def pc(monkeypatch):
    from labb.cli import connect

    FakeSession.made = []
    monkeypatch.setattr(connect, "Session", FakeSession)
    connection = connect.Connection(["/code"], False)
    connection.refused = []
    monkeypatch.setattr(connection, "refuse", lambda run_id, why: connection.refused.append(why))
    return connection


def job(n=1, thread="a", **more):
    return {**JOB, "run_id": f"00000000-0000-0000-0000-{n:012d}", "thread_id": f"cli-00000000-0000-0000-0000-00000000000{thread}", **more}


def test_messages_in_a_session_share_a_process(pc):
    pc.handle(job(1))
    FakeSession.made[0].reply = None
    pc.handle(job(2))
    assert len(FakeSession.made) == 1
    assert [j["run_id"] for j in FakeSession.made[0].sent] == [job(1)["run_id"], job(2)["run_id"]]
    # Another session has its own
    pc.handle(job(3, "b"))
    assert len(FakeSession.made) == 2


def test_a_session_whose_process_ended_gets_a_new_one(pc):
    pc.handle(job(1))
    FakeSession.made[0].reply = None
    FakeSession.made[0].is_alive = False
    pc.handle(job(2))
    assert len(FakeSession.made) == 2 and FakeSession.made[1].thread_id == job(2)["thread_id"]


def test_the_pc_refuses_what_it_wasnt_given(pc):
    for bad in (job(1, folder="/etc"), job(2, folder="/code/../etc"), job(3, thread_id="../../x"), job(4, thread_id="cli-terminal"),
                job(5, model="openai:other"), job(6, answer_mode="wild"), job(7, content="")):
        pc.handle(bad)
    assert len(pc.refused) == 7 and not FakeSession.made
    # A run id that could be anything isn't even answered
    pc.handle(job(1) | {"run_id": "../../x"})
    assert len(pc.refused) == 7 and not FakeSession.made


def test_only_models_it_listed(pc):
    pc.models = ["ollama:small"]
    pc.handle(job(1, model="ollama:small"))
    assert FakeSession.made and not pc.refused


def test_a_session_stays_in_the_folder_it_started_in(monkeypatch):
    from labb.cli import connect

    FakeSession.made = []
    monkeypatch.setattr(connect, "Session", FakeSession)
    connection = connect.Connection(["/code", "/other"], False)
    refused = []
    monkeypatch.setattr(connection, "refuse", lambda run_id, why: refused.append(why))
    connection.handle(job(1))
    FakeSession.made[0].reply = None
    connection.handle(job(2, folder="/other"))
    assert refused and len(FakeSession.made) == 1


def test_a_message_starts_once_whatever_it_says(pc):
    pc.handle(job(1, allow_all=True))
    pc.handle(job(1, allow_all=True))
    assert len(FakeSession.made[0].sent) == 1
    # Only the PC's own flag turns the checks off
    assert FakeSession.made[0].allow_all is False


def test_a_second_message_while_one_is_answered_is_refused(pc):
    pc.handle(job(1))
    pc.handle(job(2))
    assert len(pc.refused) == 1 and "still answering" in pc.refused[0]


def test_the_limit_is_on_replies_not_on_idle_sessions(pc, monkeypatch):
    from labb.cli import connect

    for n in range(connect.MAX_REPLIES + 2):
        pc.handle(job(n + 1, thread="abcdef"[n]))
        if n < 2:
            # These finished: they don't count
            FakeSession.made[n].reply = None
    assert len(FakeSession.made) == connect.MAX_REPLIES + 2 and not pc.refused
    pc.handle(job(9, thread="9"))
    assert len(pc.refused) == 1 and "already writing" in pc.refused[0]


def test_idle_sessions_close_and_busy_ones_dont(pc):
    from labb.cli import connect

    pc.handle(job(1, "a"))
    pc.handle(job(2, "b"))
    idle, busy = FakeSession.made
    idle.reply = None
    now = time.monotonic()
    pc.close_idle(now + 60)
    assert not idle.closed
    pc.close_idle(now + connect.IDLE_MINUTES * 60 + 1)
    assert idle.closed and not busy.closed
    assert idle.thread_id not in pc.sessions and busy.thread_id in pc.sessions


def test_too_many_processes_close_the_longest_idle(pc, monkeypatch):
    from labb.cli import connect

    monkeypatch.setattr(connect, "MAX_SESSIONS", 2)
    pc.handle(job(1, "a"))
    pc.handle(job(2, "b"))
    first, second = FakeSession.made
    first.reply = second.reply = None
    first.last_used, second.last_used = 1, 2
    pc.handle(job(3, "c"))
    assert first.closed and not second.closed


def test_answers_and_stops_reach_the_right_session(pc):
    pc.handle(job(1, "a"))
    pc.handle(job(2, "b"))
    first, second = FakeSession.made
    pc.handle({"type": "answer", "run_id": job(2)["run_id"], "id": "q", "allow": True, "reason": None, "text": None, "extra": "dropped"})
    pc.handle({"type": "stop", "run_id": job(1)["run_id"]})
    assert second.written == [{"type": "answer", "id": "q", "allow": True, "reason": None, "text": None}]
    assert first.written == [{"type": "stop", "run_id": job(1)["run_id"]}]


def test_the_outbox_joins_text_and_cuts_whats_long(monkeypatch):
    from labb.cli import connect

    posted = []

    class Ok:
        status_code = 204
        is_success = True

    monkeypatch.setattr(connect.remote, "server", lambda: "http://server")
    monkeypatch.setattr(connect.httpx, "post", lambda url, json, headers, timeout: (posted.append(json), Ok())[1])

    outbox = connect.Outbox.__new__(connect.Outbox)
    outbox.run_id, outbox.on_refused, outbox.pending, outbox.sent, outbox.closed = "r", lambda: None, [], 0, False
    outbox.lock = threading.Lock()
    outbox.add({"type": "token", "content": "a"})
    outbox.add({"type": "token", "content": "b"})
    outbox.add({"type": "approval", "id": "q", "detail": "x" * (connect.MAX_FIELD_CHARS + 5)})
    outbox.add({"type": "token", "content": "c"})
    outbox.closed = True
    outbox._send_all()
    events = [item["event"] for item in posted[0]["events"]]
    assert [e["type"] for e in events] == ["token", "approval", "token"]
    assert events[0]["content"] == "ab"
    assert "5 more characters not shown" in events[1]["detail"]
    assert [item["seq"] for item in posted[0]["events"]] == [1, 2, 3]


# The web chat's agent

def tool_config(user_id, answers=("yes",), **more):
    answers = iter(answers)
    asked = []

    def approve(detail, needed, always_ask, title="?", **kwargs):
        asked.append((title, detail))
        return next(answers)

    return {"configurable": {"user_id": user_id, "pcs": True, "approve": approve, "thread_id": "t", **more}}, asked


def test_the_tools_only_work_with_someone_there(server, pcs, accounts):
    from labb.tools import pcs as tool

    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id, "tooled")
    # As a task, a workflow or an API key's chat would: nothing sets `pcs`
    config = {"configurable": {"user_id": user_id}}
    args = {"pc": "tooled", "folder": "/code", "request": "hi"}
    assert tool.run_on_pc.invoke(args, config=config).startswith("Error")
    assert tool.list_pcs.invoke({}, config=config).startswith("Error")
    assert not line.jobs()


def test_saying_no_starts_nothing(server, pcs, accounts):
    from labb.tools import pcs as tool

    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id, "refused")
    config, asked = tool_config(user_id, [("no", "not now")])
    before = len(pcs.sessions_for(user_id))
    result = tool.run_on_pc.invoke({"pc": "refused", "folder": "/code", "request": "wipe it"}, config=config)
    assert "the user said no" in result and "not now" in result
    assert "wipe it" in asked[0][1]
    assert not line.sent and len(pcs.sessions_for(user_id)) == before


def test_work_from_the_chat_asks_there_and_is_a_session_to_go_on_with(server, pcs, accounts, monkeypatch):
    from labb.server import pc_team
    from labb.tools import pcs as tool

    monkeypatch.setattr(pc_team, "POLL_SECONDS", 0.01)
    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id, "chatty")

    # The PC: once the job comes, it asks one thing; once that's answered, it finishes
    def pc():
        while not (jobs := line.jobs()):
            time.sleep(0.01)
        run_id = jobs[0]["run_id"]
        assert jobs[0]["questions"] is False
        pcs.add_events(run_id, user_id, [{"seq": 1, "event": {"type": "approval", "id": "q", "title": "Run this command?", "detail": "make"}}])
        while not (answers := [m for m in line.sent if m["type"] == "answer"]):
            time.sleep(0.01)
        assert answers[0]["allow"] is True
        pcs.add_events(run_id, user_id, [{"seq": 2, "event": {"type": "answered", "id": "q"}}, {"seq": 3, "event": {"type": "token", "content": "Built."}},
                                         {"seq": 4, "event": {"type": "done", "stats": {}}}])

    thread = threading.Thread(target=pc)
    thread.start()
    config, asked = tool_config(user_id, ["yes", "yes"])
    result = tool.run_on_pc.invoke({"pc": "chatty", "folder": "/code", "request": "build"}, config=config)
    thread.join(PATIENCE)
    assert "Finished on chatty" in result and "Built." in result
    assert [title for title, _ in asked] == ["Start this on your PC?", "Run this command?"]
    assert "make" in asked[1][1]
    thread_id = line.jobs()[0]["thread_id"]
    assert f"/chat/{thread_id}" in result
    assert pcs.session(thread_id, user_id)["source"] == "agent"
    # The person can go on with it
    assert pcs.start(user_id, thread_id, "and now the tests")["status"] == "queued"


def test_stopping_the_chat_stops_the_work(server, pcs, accounts, monkeypatch):
    from labb.server import pc_team
    from labb.tools import pcs as tool

    monkeypatch.setattr(pc_team, "POLL_SECONDS", 0.01)
    user_id = accounts["member"][0]["id"]
    pc_id, line = connect_pc(pcs, user_id, "stopped")
    stop = threading.Event()
    stop.set()
    config, _ = tool_config(user_id, ["yes"], stop_requested=stop)
    assert "Stopping" in tool.run_on_pc.invoke({"pc": "stopped", "folder": "/code", "request": "x"}, config=config)
    assert line.sent[-1]["type"] == "stop"


def test_the_tools_arent_offered_unless_asked_for():
    from labb import tools

    assert "pcs" not in {tools.name_of(t) for t in tools.available(False)}
    assert "pcs" in (tools.offered_for({"configurable": {"pcs": True}}) or ())
    # An agent has only what it was given, connected or not
    agent = {"toolsets": ["web"], "documents": "none", "skills": "none", "memories": False}
    assert "pcs" not in tools.offered_for({"configurable": {"pcs": True, "agent": agent}})
    assert "pcs" in tools.offered_for({"configurable": {"agent": {**agent, "toolsets": ["pcs"]}}})
