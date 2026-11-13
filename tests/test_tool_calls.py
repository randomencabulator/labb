"""Fixing the model's tool calls (tool_calls.py), strict schemas, and the tool-call eval's scoring."""

import json
import threading
from uuid import uuid4

from langchain_core.messages import AIMessage

from labb import llm, model, runner, tool_calls
from labb.agent import chat_agent
from labb.cli.evals import score
from labb.tools import tools_for
from conftest import ai


def tools(*names):
    every = {tool.name: tool for tool in tools_for(True)}
    return [every[name] for name in names]


def reply(*calls, invalid=()):
    message = ai(tool_calls=list(calls))
    return message.model_copy(update={"invalid_tool_calls": [
        {"name": name, "args": args, "id": f"bad-{i}", "error": None, "type": "invalid_tool_call"} for i, (name, args) in enumerate(invalid)]})


def test_valid_calls_are_left_alone():
    message = reply(("read_file", {"path": "a.py"}))
    assert tool_calls.fix(message, tools("read_file")) is message


def test_names_from_other_engines_are_mapped():
    offered = tools("web_search", "run_command", "read_file", "update_todos")
    fixed = tool_calls.fix(reply(("search_web", {"query": "x"}), ("functions.Bash", {"command": "ls"}),
                                 ("ReadFile", {"path": "a"}), ("TodoWrite", {"todos": [{"task": "a"}]})), offered)
    assert [c["name"] for c in fixed.tool_calls] == ["web_search", "run_command", "read_file", "update_todos"]
    fixes = fixed.response_metadata["tool_fixes"]
    assert fixes["repaired"] == 4 and fixes["failed"] == 0
    assert fixes["notes"]["call-0-search_web"] == ["search_web → web_search"]


def test_argument_names_and_types_are_repaired():
    offered = tools("read_file", "grep", "update_todos", "edit_file")
    fixed = tool_calls.fix(reply(
        ("read_file", {"file_path": "a.py", "limit": None}),
        ("grep", {"patern": "def", "glob": None}),
        ("update_todos", {"todos": '[{"task": "Fix it", "status": "in_progress"}]'}),
        ("update_todos", {"todos": ["Read", "Fix"]}),
        ("edit_file", {"path": "a", "old_str": "x", "replacement": "y"}),
    ), offered)
    args = [c["args"] for c in fixed.tool_calls]
    assert args[0] == {"path": "a.py"}
    assert args[1] == {"pattern": "def"}
    assert args[2] == {"todos": [{"task": "Fix it", "status": "in_progress"}]}
    assert args[3] == {"todos": [{"task": "Read"}, {"task": "Fix"}]}
    # One unknown key for the one missing required key
    assert args[4] == {"path": "a", "old_string": "x", "new_string": "y"}
    assert fixed.response_metadata["tool_fixes"]["failed"] == 0


def test_broken_json_is_parsed_and_the_call_comes_back():
    fixed = tool_calls.fix(reply(invalid=[("read_file", '```json\n{"path": "a.py",}\n```'),
                                          ("grep", "{'pattern': 'x', 'ignore_case': True}")]), tools("read_file", "grep"))
    assert [(c["name"], c["args"]) for c in fixed.tool_calls] == [("read_file", {"path": "a.py"}),
                                                                   ("grep", {"pattern": "x", "ignore_case": True})]
    assert fixed.invalid_tool_calls == []


def test_what_cant_be_fixed_is_counted_and_left_for_tool_node():
    fixed = tool_calls.fix(reply(("teleport", {}), ("read_file", {"offset": 3})), tools("read_file"))
    assert fixed.response_metadata["tool_fixes"]["failed"] == 2
    assert [c["name"] for c in fixed.tool_calls] == ["teleport", "read_file"]


def test_a_constrained_retry_fixes_the_rest():
    asked = []

    def constrain(what, *args):
        asked.append(what)
        return "read_file" if what == "name" else {"path": "a.py"}

    fixed = tool_calls.fix(reply(("open_the_file_please", {"x": 1})), tools("read_file", "grep"), constrain)
    assert asked == ["name", "args"]
    assert fixed.tool_calls[0]["name"] == "read_file" and fixed.tool_calls[0]["args"] == {"path": "a.py"}
    fixes = fixed.response_metadata["tool_fixes"]
    assert fixes["constrained"] == 2 and fixes["failed"] == 0


def test_strict_schemas_close_objects_and_make_optional_arguments_nullable():
    grep = tool_calls.strict_schema(tool_calls.schema_of(tools("grep")[0]))
    assert grep["additionalProperties"] is False
    assert set(grep["required"]) == {"pattern", "path", "glob", "ignore_case"}
    assert grep["properties"]["pattern"] == {"type": "string"}
    assert grep["properties"]["path"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert grep["properties"]["glob"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    todos = tool_calls.strict_schema(tool_calls.schema_of(tools("update_todos")[0]))
    item = todos["properties"]["todos"]["items"]
    assert item["additionalProperties"] is False and set(item["required"]) == {"task", "status", "note", "id"}
    assert tool_calls.strict_schema({"type": "object", "properties": {}, "additionalProperties": True}) is None


def test_strict_tools_take_each_providers_shape():
    openai = tool_calls.strict_tools(tools("read_file"), "openai")[0]
    assert openai["type"] == "function" and openai["function"]["strict"] is True
    anthropic = tool_calls.strict_tools(tools("read_file"), "anthropic")[0]
    assert anthropic["strict"] is True and anthropic["input_schema"]["additionalProperties"] is False


def test_strict_calls_by_provider_and_setting(monkeypatch):
    monkeypatch.setattr(llm, "STRICT_TOOLS", "auto")
    assert llm.uses_strict("openai", None) and llm.uses_strict("anthropic", None)
    assert not llm.uses_strict("openai_compatible", None) and not llm.uses_strict("ollama", None)
    assert llm.uses_strict("openai_compatible", llm.Provider("vllm", "openai_compatible", strict_tools=True))
    monkeypatch.setattr(llm, "STRICT_TOOLS", "off")
    assert not llm.uses_strict("openai", None)


def test_the_graph_runs_the_repaired_call_and_counts_it(fake_model, config, tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    fake_model(ai(tool_calls=[("cat", {"file_path": "a.txt"})]), ai("It says hello."))
    events = []
    thread = f"cli-{uuid4()}"
    runner.run_reply(thread, "Read a.txt", threading.Event(), events.append, configurable=config["configurable"], name=False)
    messages = chat_agent.get_state({"configurable": {"thread_id": thread}}).values["messages"]
    assert messages[1].tool_calls[0]["name"] == "read_file" and "hello" in messages[2].content
    call = next(e for e in events if e["type"] == "tool_call")
    assert call["fixed"] == ["cat → read_file", "file_path → path"]
    done = events[-1]
    assert done["stats"]["tool_repaired"] == 1 and done["stats"]["tool_failed"] == 0


def test_a_provider_that_refuses_strict_mode_is_asked_again_without(monkeypatch, config):
    calls = []

    class Bound:
        def __init__(self, strict):
            self.strict = strict

        def invoke(self, messages):
            calls.append(self.strict)
            if self.strict:
                raise ValueError("Error code: 400 - 'strict' is not supported for this model")
            return AIMessage(content="ok")

    spec = "openai:old-model"
    models = llm.Models(spec, "openai", "old-model", None, None, 1000, "openai", True, None)
    monkeypatch.setattr(model, "model_with_tools", type("Cached", (), {
        "__call__": staticmethod(lambda s, *a: Bound(s not in model.strict_refused)), "cache_clear": staticmethod(lambda: None)})())
    assert model.call_model(models, [], True).content == "ok"
    assert calls == [True, False] and spec in model.strict_refused
    model.strict_refused.discard(spec)


def test_the_eval_scores_raw_repaired_and_retried_calls():
    case = {"prompt": "Read app.py", "tool": "read_file", "args": {"path": "app.py"}}
    offered = tools("read_file", "grep")
    assert score(case, reply(("read_file", {"path": "app.py"})), offered) == {
        "called": True, "valid": True, "right_tool": True, "right_args": True, "repaired": True, "constrained": True}
    fixed = score(case, reply(("cat", {"file_path": "app.py"})), offered)
    assert fixed["called"] and not fixed["valid"] and fixed["repaired"]
    retried = score(case, reply(("read_file", {"wrong": 1, "other": 2})), offered, lambda what, *a: {"path": "app.py"})
    assert not retried["repaired"] and retried["constrained"]
    assert score(case, ai("I can't."), offered)["called"] is False


def test_usage_keeps_the_tool_call_counts(client):
    from labb import usage
    usage.record(f"t-{uuid4()}", "m1", {"model": "test:model", "tool_calls": 10, "tool_repaired": 2, "tool_constrained": 1,
                                        "tool_failed": 1}, user_id=None, source="web")
    row = next(m for m in usage.summary(None)["by_model"] if m["model"] == "test:model")
    assert (row["tool_calls"], row["tool_repaired"], row["tool_constrained"], row["tool_failed"]) == (10, 2, 1, 1)


def test_providers_save_strict_tool_calls(client):
    listed = client.put("/admin/providers/openai", json={"strict_tools": False}).json()
    openai = next(p for p in listed if p["name"] == "openai")
    assert openai["can_be_strict"] and openai["strict_tools"] is False
    assert llm.providers["openai"].strict_tools is False
    listed = client.put("/admin/providers/openai", json={"strict_tools": None}).json()
    assert next(p for p in listed if p["name"] == "openai")["strict_tools"] is None
    assert client.put("/admin/providers/ollama", json={"strict_tools": True}).status_code == 400
    json.dumps(listed)


def test_the_constrained_retry_asks_ollama_with_the_schema_and_no_stream():
    seen = []

    class Constrained:
        def invoke(self, messages, config=None, **kwargs):
            seen.append((messages[-1].content, config, kwargs))
            return AIMessage(content='{"path": "a.py"}')

    models = llm.Models("ollama:x", "ollama", "x", None, None, 1000, "ollama", False, Constrained())
    constrain = model.constrainer(models, [], reply(("read_file", {"wrong": 1})))
    schema = tool_calls.schema_of(tools("read_file")[0])
    assert constrain("args", "read_file", schema) == {"path": "a.py"}
    prompt, config, kwargs = seen[0]
    assert kwargs["format"] == schema and config["callbacks"] == [] and "read_file" in prompt
    assert model.constrainer(llm.Models("openai:x", "openai", "x", None, None, 1000, "openai"), [], reply()) is None


def test_older_databases_get_the_new_usage_columns(tmp_path, monkeypatch):
    import sqlite3
    from labb import db
    # A SQLite file, also when the suite runs on Postgres (TEST_DATABASE_URL)
    monkeypatch.setattr(db, "POSTGRES", False)
    conn = sqlite3.connect(tmp_path / "old.sqlite")
    conn.execute("CREATE TABLE usage (thread_id TEXT, tool_calls INTEGER)")
    for _ in range(2):
        db.add_column(conn, "usage", "tool_failed", "INTEGER NOT NULL DEFAULT 0")
    assert [row[1] for row in conn.execute("PRAGMA table_info(usage)")] == ["thread_id", "tool_calls", "tool_failed"]
