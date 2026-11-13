"""labb run / labb workflows / /run: Studio's workflows from the terminal, against the
test server."""

import io
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from rich.console import Console

from labb import remote
from labb.cli import workflows
from labb.server import app as server
from labb.server import auth
from labb.server.studio import nodes


class Client(TestClient):
    """The test server, without starting it (`with` would run its startup: the schedulers)"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def node(key: str, kind: str, **config) -> dict:
    return {"id": key, "key": key, "type": kind, "label": key.title(), "position": {"x": 0, "y": 0},
            "config": {**nodes.defaults(kind), **config}}


def edge(a: str, b: str, handle=None) -> dict:
    return {"id": f"{a}-{b}", "source": a, "target": b, "handle": handle}


@pytest.fixture
def terminal(accounts, monkeypatch):
    """A person with a key; the terminal's requests go to the test server; returns (client, output)"""

    user = auth.create_user(f"u{uuid4().hex[:10]}", "a-password")
    key, _ = auth.create_key(user["id"], "laptop")
    client = TestClient(server.app, headers={"Authorization": f"Bearer {key}"})
    monkeypatch.setattr(workflows, "_client", lambda: Client(server.app, headers={"Authorization": f"Bearer {key}"}))
    monkeypatch.setattr(workflows, "POLL_SECONDS", 0.05)
    output = io.StringIO()
    monkeypatch.setattr(workflows, "console", Console(file=output, width=100, force_terminal=False))
    return client, output


def make(client: TestClient, name: str, nodes_: list, edges: list, publish: bool = True) -> dict:
    workflow = client.post("/studio/workflows", json={"name": name, "draft": {"nodes": nodes_, "edges": edges}}).json()
    if publish:
        assert client.post(f"/studio/workflows/{workflow['id']}/publish").status_code == 200
    return workflow


def test_running_a_workflow_shows_its_steps_and_result(terminal, capsys):
    client, output = terminal
    make(client, "Shout it", [node("trigger", "trigger", inputs=[{"name": "text", "required": True}]),
                              node("loud", "transform", template="{{trigger.text | upper}}!")],
         [edge("trigger", "loud")])
    assert workflows.run_workflow("shout", {"text": "hello"}) == 0
    shown = output.getvalue()
    assert "Running" in shown and "✓ Loud" in shown and "HELLO!" in shown

    assert workflows.run_workflow("Shout it", {"text": "x"}, output="json") == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "done" and printed["result"] == "X!"
    with pytest.raises(workflows.WorkflowError, match="needed"):
        workflows.run_workflow("shout", {})


def test_failures_approvals_and_names(terminal, monkeypatch):
    client, output = terminal
    make(client, "Ask first", [node("trigger", "trigger"), node("ask", "approval", message="Go?"),
                               node("done", "transform", template="went")],
         [edge("trigger", "ask"), edge("ask", "done", "approved")])
    monkeypatch.setattr(workflows.sys.stdin, "isatty", lambda: False)
    assert workflows.run_workflow("Ask first", {}) == 3
    assert "Go?" in output.getvalue() and "waiting" in output.getvalue()

    make(client, "Ask again", [node("trigger", "trigger")], [])
    with pytest.raises(workflows.WorkflowError, match="More than one"):
        workflows.find(workflows._client(), "ask")
    with pytest.raises(workflows.WorkflowError, match="no workflow called"):
        workflows.find(workflows._client(), "nothing like it")
    draft_only = make(client, "Not yet", [node("trigger", "trigger")], [], publish=False)
    assert workflows.main(["run", draft_only["id"]]) == 1 and "isn't published" in output.getvalue()


def test_inputs_and_the_chat_commands(terminal):
    client, output = terminal
    assert workflows.parse_inputs(["n=3", "flag=true", "text=hi there", "list=[1, 2]"]) == \
        {"n": 3, "flag": True, "text": "hi there", "list": [1, 2]}
    with pytest.raises(workflows.WorkflowError):
        workflows.parse_inputs(["oops"])
    make(client, "Daily note", [node("trigger", "trigger", inputs=[{"name": "topic"}]),
                                node("note", "transform", template="About {{trigger.topic}}")], [edge("trigger", "note")])
    workflows.command("/workflows")
    assert "Daily note" in output.getvalue() and "v1" in output.getvalue()
    workflows.command("/run Daily note topic=bees")
    assert "About bees" in output.getvalue()


def test_without_a_server(monkeypatch):
    monkeypatch.setattr(remote, "server", lambda: None)
    out = io.StringIO()
    monkeypatch.setattr(workflows, "console", Console(file=out))
    assert workflows.main(["workflows"]) == 1 and "LABB_SERVER" in out.getvalue()


def test_what_is_piped_into_p_goes_with_the_question(monkeypatch):
    from labb.cli import app
    assert app.with_stdin("review this", "") == "review this"
    assert app.with_stdin("review this", "-a\n+b\n") == "review this\n\n<stdin>\n-a\n+b\n</stdin>"
    monkeypatch.setattr(app, "MAX_STDIN_CHARS", 5)
    assert app.with_stdin("q", "0123456789").endswith("01234\n… (cut)\n</stdin>")


def test_p_reads_a_pipe_but_doesnt_wait_on_an_empty_one():
    import subprocess, sys
    check = "import sys; from labb.cli.app import stdin_has_data; print(stdin_has_data(0.3))"
    piped = subprocess.run([sys.executable, "-c", check], input="diff", capture_output=True, text=True)
    assert piped.stdout.strip() == "True"
    # Open but silent: the parent keeps the pipe open and writes nothing
    silent = subprocess.Popen([sys.executable, "-c", check], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    out = silent.stdout.readline()
    silent.stdin.close()
    silent.wait()
    assert out.strip() == "False"
