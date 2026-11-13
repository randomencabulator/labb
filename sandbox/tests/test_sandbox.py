"""The sandbox: results, printed output, errors with their traceback, and the limits."""

import pytest
from fastapi.testclient import TestClient

from labb_sandbox import app as service

client = TestClient(service.app)


def run(code: str, inputs: dict | None = None, timeout: int = 10) -> dict:
    response = client.post("/run", json={"code": code, "inputs": inputs or {}, "timeout": timeout})
    assert response.status_code == 200, response.text
    return response.json()


def test_the_code_gets_inputs_and_gives_a_result():
    done = run("print('working')\nresult = {'total': sum(inputs['numbers'])}", {"numbers": [1, 2, 3]})
    assert done["ok"] and done["result"] == {"total": 6} and done["stdout"] == "working\n"


def test_errors_come_back_with_the_traceback():
    failed = run("def f():\n    return 1 / 0\nresult = f()")
    assert not failed["ok"] and "ZeroDivisionError" in failed["error"] and 'step.py", line 2' in failed["error"]
    assert "wrapper.py" not in failed["error"]


def test_results_that_arent_json_are_text_or_refused():
    assert run("import datetime\nresult = datetime.date(2026, 10, 25)")["result"] == "2026-10-25"
    assert run("result = None")["result"] is None


def test_time_and_memory_limits():
    slow = run("while True: pass", timeout=1)
    assert not slow["ok"] and ("longer than 1 seconds" in slow["error"] or "ran out" in slow["error"])
    big = run("x = bytearray(1024 * 1024 * 1024)\nresult = len(x)")
    assert not big["ok"] and "MemoryError" in big["error"] + big.get("stdout", "")


def test_a_run_has_its_own_folder_and_nothing_else():
    done = run("import os\nresult = sorted(os.listdir('.'))")
    assert done["result"] == ["code.py", "inputs.json"]
    env = run("import os\nresult = sorted(os.environ)")["result"]
    assert "SANDBOX_TOKEN" not in env and "PATH" in env


def test_the_token(monkeypatch):
    monkeypatch.setattr(service, "TOKEN", "secret")
    assert client.post("/run", json={"code": "result = 1"}).status_code == 401
    ok = client.post("/run", json={"code": "result = 1"}, headers={"Authorization": "Bearer secret"})
    assert ok.json()["result"] == 1


@pytest.mark.parametrize("code", ["x" * (service.MAX_CODE + 1)])
def test_too_much_code(code):
    assert client.post("/run", json={"code": code}).status_code == 400


def test_the_token_is_made_once_and_kept(tmp_path, monkeypatch):
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)
    monkeypatch.setattr(service, "TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setattr(service, "ISOLATED", False)
    made = service.load_token()
    assert len(made) > 30 and (tmp_path / "token").stat().st_mode & 0o777 == 0o600
    assert service.load_token() == made
    monkeypatch.setenv("SANDBOX_TOKEN", "set")
    assert service.load_token() == "set"


def test_a_token_is_needed_when_there_is_one(monkeypatch):
    monkeypatch.setattr(service, "TOKEN", "secret")
    assert client.post("/run", json={"code": "result = 1"}).status_code == 401
    ok = client.post("/run", json={"code": "result = 1"}, headers={"Authorization": "Bearer secret"})
    assert ok.json()["result"] == 1


def test_labb_changes_the_memory_a_step_may_use(monkeypatch):
    from labb_sandbox import settings
    monkeypatch.setattr(service, "TOKEN", "secret")
    monkeypatch.delenv("SANDBOX_TOKEN_FILE", raising=False)
    monkeypatch.setattr(settings, "_defaults", {"memory_mb": 256})
    monkeypatch.setattr(settings, "_saved_here", {})
    monkeypatch.setattr(service, "MEMORY_MB", 256)
    headers = {"Authorization": "Bearer secret"}
    assert client.put("/settings", json={"memory_mb": 512}, headers=headers).json()[0]["value"] == 512
    assert service.MEMORY_MB == 512
    assert client.put("/settings", json={"memory_mb": 1}, headers=headers).status_code == 400
    client.put("/settings", json={"memory_mb": None}, headers=headers)
    assert service.MEMORY_MB == 256
