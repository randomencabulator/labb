"""The tools for longer work in the terminal: edits, the todo list, notes, and turning tools off."""

import threading
from datetime import date
from uuid import uuid4

from labb import model, permissions, runner, settings
from labb.agent import chat_agent
from labb.cli.app import tool_rules
from labb.context import CODING_SUMMARY_PROMPT, SUMMARY_PROMPT
from labb.tools import ToolRules, prompts_for, tools_for
from labb.tools import todo
from labb.tools.edits import edit_file, write_file
from labb.tools.notes import read_notes, write_notes
from labb.tools.todo import update_todos
from conftest import ai


def run(tool, config, **args):
    return tool.invoke(args, config)


def approving(config, answer="yes"):
    """Approve every change with `answer`, keeping what was asked"""
    asked = []
    config["configurable"]["approve"] = lambda shown, needed, always_ask, title="Run this command?": asked.append((title, shown)) or answer
    return asked


def test_edit_file_replaces_exact_text_after_approval(tmp_path, config):
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a - b\n")
    asked = approving(config)
    result = run(edit_file, config, path="app.py", old_string="return a - b", new_string="return a + b")
    assert result == "Edited app.py: +1 -1 lines"
    assert (tmp_path / "app.py").read_text() == "def add(a, b):\n    return a + b\n"
    title, shown = asked[0]
    assert title == "Edit app.py?" and "-    return a - b" in shown and "+    return a + b" in shown


def test_edit_file_needs_text_that_is_there_once(tmp_path, config):
    (tmp_path / "a.txt").write_text("x = 1\nx = 1\n")
    approving(config)
    assert "isn't in a.txt" in run(edit_file, config, path="a.txt", old_string="y = 2", new_string="y = 3")
    assert "2 times" in run(edit_file, config, path="a.txt", old_string="x = 1", new_string="x = 2")
    assert "2 places" in run(edit_file, config, path="a.txt", old_string="x = 1", new_string="x = 2", replace_all=True)
    assert (tmp_path / "a.txt").read_text() == "x = 2\nx = 2\n"
    assert "use write_file" in run(edit_file, config, path="new.txt", old_string="a", new_string="b")


def test_edit_file_keeps_windows_line_endings(tmp_path, config):
    (tmp_path / "w.txt").write_bytes(b"one\r\ntwo\r\n")
    approving(config)
    run(edit_file, config, path="w.txt", old_string="one\ntwo", new_string="one\n2")
    assert (tmp_path / "w.txt").read_bytes() == b"one\r\n2\r\n"


def test_write_file_creates_and_overwrites(tmp_path, config):
    asked = approving(config)
    assert run(write_file, config, path="src/new.py", content="print(1)\n") == "Created src/new.py: 1 lines"
    assert run(write_file, config, path="src/new.py", content="print(2)\n").startswith("Wrote src/new.py")
    assert [title for title, _ in asked] == ["Create src/new.py?", "Overwrite src/new.py?"]
    assert "outside the working directory" in run(write_file, config, path="../x.py", content="")


def test_changes_that_arent_allowed_are_not_made(tmp_path, config):
    (tmp_path / "a.txt").write_text("old")
    approving(config, ("no", "use b.txt"))
    assert "didn't allow" in run(edit_file, config, path="a.txt", old_string="old", new_string="new")
    assert "use b.txt" in run(write_file, config, path="a.txt", content="new")
    del config["configurable"]["approve"]
    assert "isn't possible here" in run(edit_file, config, path="a.txt", old_string="old", new_string="new")
    config["configurable"]["mode"] = "plan"
    assert "plan mode" in run(write_file, config, path="b.txt", content="x")
    assert (tmp_path / "a.txt").read_text() == "old" and not (tmp_path / "b.txt").exists()


def test_always_allows_edits_in_the_project(tmp_path, config):
    asked = approving(config, "always")
    run(write_file, config, path="a.txt", content="1")
    run(write_file, config, path="a.txt", content="2")
    assert len(asked) == 1 and "edits" in permissions.load_allowed(str(tmp_path))
    assert (tmp_path / "a.txt").read_text() == "2"


def test_accept_edits_mode_edits_without_asking_but_commands_still_ask(tmp_path, config):
    from labb.tools.shell import ask_permission
    asked = approving(config, "no")
    config["configurable"]["mode"] = "acceptEdits"
    (tmp_path / "app.py").write_text("x = 1\n")
    assert run(edit_file, config, path="app.py", old_string="x = 1", new_string="x = 2").startswith("Edited")
    assert run(write_file, config, path="new.txt", content="hi").startswith(("Wrote", "Created"))
    assert not asked and "edits" not in permissions.load_allowed(str(tmp_path))
    assert ask_permission("rm app.py", str(tmp_path), config["configurable"]) is not None and asked
    # Still inside the working directory only
    assert "outside" in run(write_file, config, path="../elsewhere.txt", content="no")


def test_accept_edits_can_be_the_default_mode(tmp_path, monkeypatch):
    import json
    from labb.cli.app import MODES
    assert MODES == ("default", "acceptEdits", "plan")
    monkeypatch.setattr(settings, "user_file", lambda: tmp_path / "none.json")
    (tmp_path / ".labb").mkdir()
    (tmp_path / ".labb" / "settings.json").write_text(json.dumps({"defaultMode": "acceptEdits"}))
    # A project's own file asks for it: it counts once agreed to
    assert "defaultMode" not in settings.load(str(tmp_path))[0]
    settings.trust_all(str(tmp_path), settings.untrusted(str(tmp_path)))
    merged, problems = settings.load(str(tmp_path))
    assert merged["defaultMode"] == "acceptEdits" and problems == []


def test_the_todo_list_is_kept_per_conversation(tmp_path, config):
    config["configurable"]["thread_id"] = "cli-1"
    result = run(update_todos, config, todos=[
        {"task": "Read the code", "status": "done"}, {"task": "Fix it", "status": "in_progress"}, {"task": "Test it"}])
    assert result.splitlines() == ["Todo list (1 of 3 done):", "[x] 1. Read the code", "[>] 2. Fix it", "[ ] 3. Test it"]
    assert [item["status"] for item in todo.load(str(tmp_path), "cli-1")] == ["done", "in_progress", "pending"]
    assert todo.load(str(tmp_path), "cli-2") == []
    assert (tmp_path / ".labb" / "todos" / ".gitignore").read_text() == "*\n"
    two = run(update_todos, config, todos=[{"task": "A", "status": "in_progress"}, {"task": "B", "status": "in_progress"}])
    assert "more than one step" in two


def test_notes_are_added_to_and_rewritten(tmp_path, config):
    assert run(read_notes, config) == "No notes yet."
    run(write_notes, config, text="Tests: uv run pytest")
    run(write_notes, config, text="Auth lives in server/auth.py")
    notes = run(read_notes, config)
    assert notes.startswith("Tests: uv run pytest") and notes.endswith("Auth lives in server/auth.py")
    run(write_notes, config, text="Only this", replace=True)
    assert (tmp_path / ".labb" / "notes.md").read_text() == "Only this\n"
    assert "over" in run(write_notes, config, text="x" * 30_000)


def test_notes_have_topics_the_model_is_told_about(tmp_path, config):
    from labb.tools import notes

    assert notes.topics(tmp_path) == []
    run(write_notes, config, text="Tests: uv run pytest")
    assert notes.topics(tmp_path) == ["notes without topics"]
    run(write_notes, config, text="Sessions are checked in server/auth.py", topic="Auth")
    assert notes.topics(tmp_path) == [f"Auth ({date.today():%Y-%m-%d})"]
    assert "Your notes have: Auth" in model.work_reminder(config)


def test_the_open_todo_list_rides_along_with_each_call(tmp_path, config):
    from langchain_core.messages import HumanMessage, ToolMessage

    config["configurable"]["thread_id"] = "cli-1"
    assert model.work_reminder(config) == ""
    run(update_todos, config, todos=[{"task": "Fix it", "status": "in_progress"}, {"task": "Test it"}])
    reminder = model.work_reminder(config)
    assert "[>] 1. Fix it" in reminder and "update_todos" in reminder
    sent = model.with_reminder([HumanMessage("go"), ToolMessage("ok", tool_call_id="1")], reminder)
    # After a tool's result, as a message of its own, so it isn't taken for what the tool gave back
    assert sent[0].content == "go" and sent[1].content == "ok" and sent[2].type == "human"
    assert sent[2].content.startswith("[labb, for you only:") and "Fix it" in sent[2].content
    # On the user's message, added to it
    asked = model.with_reminder([HumanMessage("go")], reminder)
    assert len(asked) == 1 and asked[0].content.startswith("go\n\n[labb, for you only:")
    # Done lists, turned-off todos and another conversation (the same id in the web chat's store) get none
    run(update_todos, config, todos=[{"task": "Fix it", "status": "done"}, {"task": "Test it", "status": "done"}])
    assert model.work_reminder(config) == ""
    run(update_todos, config, todos=[{"task": "Fix it"}])
    off = {"configurable": {**config["configurable"], "tools": ToolRules.of(deny=["update_todos"])}}
    assert model.work_reminder(off) == ""
    assert model.work_reminder({"configurable": {"thread_id": "cli-1"}}) == ""


def test_rules_turn_tools_off_by_name_toolset_or_wildcard():
    rules = ToolRules.of(deny=["edits", "read_*"])
    names = [tool.name for tool in tools_for(True, None, rules)]
    assert "edit_file" not in names and "write_file" not in names and "read_file" not in names and "read_notes" not in names
    assert "run_command" in names and "grep" in names
    only = ToolRules.of(allow=["files", "update_todos"], deny=["grep"])
    assert {tool.name for tool in tools_for(True, None, only)} == {"list_files", "read_file", "glob", "update_todos"}
    assert ToolRules.of() is None and ToolRules.of(allow=None, deny=[" "]) is None
    # A toolset that's all off has its instructions left out too
    assert not any("`edit_file`" in p for p in prompts_for(True, None, rules))
    assert any("`edit_file`" in p for p in prompts_for(True))


def test_flags_and_settings_make_the_rules(tmp_path):
    assert tool_rules({}, None, None) is None
    assert tool_rules({"deny": ["shell"]}, None, "edits, mcp__*") == ToolRules(None, ("shell", "edits", "mcp__*"))
    assert tool_rules({"allow": ["files"]}, "files,todo", None).allow == ("files", "todo")
    assert tool_rules({}, "", None) == ToolRules((), ())
    (tmp_path / ".labb").mkdir()
    (tmp_path / ".labb" / "settings.json").write_text('{"tools": {"deny": ["write_file"], "allow": "files"}}')
    loaded, problems = settings.load(str(tmp_path))
    assert "write_file" in loaded["tools"]["deny"] and "allow" not in loaded["tools"]
    assert any("tools.allow" in problem for problem in problems)


def test_a_tool_that_is_off_isnt_run(fake_model, config, tmp_path):
    (tmp_path / "a.txt").write_text("hi")
    fake_model(ai(tool_calls=[("read_file", {"path": "a.txt"})]), ai("Can't read it."))
    config["configurable"]["tools"] = ToolRules.of(deny=["read_file"])
    thread = f"cli-{uuid4()}"
    runner.run_reply(thread, "Read a.txt", threading.Event(), lambda event: None, configurable=config["configurable"], name=False)
    tool_message = chat_agent.get_state({"configurable": {"thread_id": thread}}).values["messages"][2]
    assert tool_message.status == "error" and "turned off" in tool_message.content
    assert "Some tools are turned off" in model.system_prompt(config)


def test_terminal_summaries_keep_the_work_going(config):
    assert model.summary_prompt(config) == CODING_SUMMARY_PROMPT and "Next" in CODING_SUMMARY_PROMPT
    assert model.summary_prompt({"configurable": {}}) == SUMMARY_PROMPT


def test_the_terminal_shows_every_step_of_the_list():
    from labb.cli.render import result_lines
    lines = [line.plain for line in result_lines("update_todos", {"preview": ["[x] Read", "[>] Fix", "[ ] Test"], "first_line": ""})]
    assert lines == ["  ⎿ ✔ Read", "    ◼ Fix", "    ◻ Test"]
    numbered = [line.plain for line in result_lines("update_todos", {"preview": ["[!] 1. Deploy (no key)", "[-] 2. Docs (not asked)"], "first_line": ""})]
    assert numbered == ["  ⎿ ✗ Deploy (no key)", "    – Docs (not asked)"]


def test_steps_cant_vanish_and_need_a_reason_to_be_dropped(tmp_path, config):
    config["configurable"]["thread_id"] = "cli-steps"
    run(update_todos, config, todos=[{"task": "Read"}, {"task": "Fix"}, {"task": "Test"}])
    # Left out: kept at the end, with a note
    result = run(update_todos, config, todos=[{"task": "Read", "status": "done"}, {"task": "Fix", "status": "in_progress"}])
    assert "left out 1 open step (Test)" in result and [i["task"] for i in todo.load(str(tmp_path), "cli-steps")] == ["Read", "Fix", "Test"]
    # Skipped needs a reason, and then it's resolved
    assert "say why" in run(update_todos, config, todos=[{"task": "Read", "status": "done"}, {"task": "Fix"}, {"task": "Test", "status": "skipped"}])
    run(update_todos, config, todos=[{"task": "Read", "status": "done"}, {"id": 2, "task": "Fix the bug", "status": "in_progress"},
                                     {"task": "Test", "status": "skipped", "note": "no tests in this repo"}])
    items = todo.load(str(tmp_path), "cli-steps")
    # Reworded by its number: the same step, not a dropped one
    assert [(i["id"], i["task"], i["status"]) for i in items] == [(1, "Read", "done"), (2, "Fix the bug", "in_progress"), (3, "Test", "skipped")]
    assert [i["task"] for i in todo.open_items(items)] == ["Fix the bug"]


def test_a_long_list_is_shown_short_in_the_reminder():
    items = [{"id": i, "task": f"Step {i}", "status": "done" if i < 4 else "pending"} for i in range(1, 13)]
    items[3]["status"] = "in_progress"
    short = todo.short_show(items)
    assert short.startswith("Todo list (8 pending, 1 in progress, 3 done)") and "[>] 4. Step 4" in short
    assert "Step 5" in short and "Step 7" in short and "Step 8" not in short and "Step 1" not in short.split("\n", 1)[1]


def test_the_web_chat_keeps_a_todo_list_too():
    web = {"configurable": {"thread_id": "web-todos", "user_id": "u1"}}
    from labb.tools import offered_for
    assert "todo" in offered_for(web)
    assert "Todo list (0 of 1 done)" in update_todos.invoke({"todos": [{"task": "Find flights"}]}, web)
    assert todo.items_for(web)[0]["task"] == "Find flights"
    assert "Find flights" in model.work_reminder(web)
    # Without a conversation there's nowhere to keep one
    assert "todo" not in offered_for({"configurable": {}})


def helper_replies(monkeypatch, *replies):
    """The task tool's helper answers with these, in order; returns what it was sent each time"""
    from types import SimpleNamespace
    from conftest import FakeModel
    from labb import llm
    seen = []

    class Recording(FakeModel):
        def invoke(self, messages, config=None, **kwargs):
            seen.append(list(messages))
            return super().invoke(messages, config, **kwargs)

    fake = Recording(messages=iter(list(replies)))
    monkeypatch.setattr(llm, "for_config", lambda config: SimpleNamespace(model=fake))
    return seen


def test_task_runs_a_helper_with_read_only_tools_and_returns_its_conclusion(tmp_path, config, monkeypatch):
    from labb.tools.task import task
    (tmp_path / "auth.py").write_text("def login(user):\n    return check(user)\n")
    seen = helper_replies(monkeypatch,
                          ai(tool_calls=[("grep", {"pattern": "def login"})]),
                          ai(tool_calls=[("run_command", {"command": "rm auth.py"})]),
                          ai("login is in auth.py:1."))
    asked = approving(config)
    answer = run(task, config, description="Find login", prompt="Where is login defined?")
    assert answer == "login is in auth.py:1."
    # It searched, and its change was refused without asking: the helper is read-only
    tool_results = [m.content for m in seen[-1] if m.type == "tool"]
    assert "auth.py:1: def login(user):" in tool_results[0]
    assert "plan mode" in tool_results[1] and (tmp_path / "auth.py").exists() and not asked
    # It starts fresh: its own prompt and the job, nothing of the conversation
    assert [m.type for m in seen[0]] == ["system", "human"] and "Where is login defined?" in seen[0][1].content


def test_task_answers_with_what_it_has_after_its_steps(tmp_path, config, monkeypatch):
    from labb.tools import task as task_module
    monkeypatch.setattr(task_module, "MAX_STEPS", 2)
    helper_replies(monkeypatch, ai(tool_calls=[("list_files", {})]), ai(tool_calls=[("list_files", {})]), ai("Partly done."))
    answer = run(task_module.task, config, description="Look around", prompt="Look around")
    assert answer.startswith("(The helper stopped after 2 steps.)") and "Partly done." in answer


def test_task_is_only_for_the_terminal():
    from labb.tools import task as task_module
    assert task_module.NEEDS_WORKDIR and task_module.task not in task_module.helper_tools({"configurable": {}})
    assert task_module.task.name not in [t.name for t in tools_for(False)]


def test_background_commands_run_on_and_can_be_read_and_stopped(tmp_path, config):
    from labb.tools import shell
    config["configurable"]["allow_all"] = True
    started = run(shell.run_command, config, command="echo ready; sleep 30", background=True)
    job_id = started.split(" as ")[1].split(".")[0]
    assert job_id in shell.jobs
    first = run(shell.read_output, config, job_id=job_id, wait=5)
    assert "running" in first and "ready" in first
    # Only what's new
    assert "(no new output)" in run(shell.read_output, config, job_id=job_id)
    process = shell.jobs[job_id].process
    assert run(shell.stop_command, config, job_id=job_id).startswith(f"Stopped {job_id}")
    assert process.poll() is not None and job_id not in shell.jobs
    assert "no background command" in run(shell.read_output, config, job_id=job_id)


def test_background_commands_need_the_same_permission_and_end_with_labb(tmp_path, config):
    from labb.tools import shell
    config["configurable"]["mode"] = "plan"
    assert "plan mode" in run(shell.run_command, config, command="touch x", background=True)
    del config["configurable"]["mode"]
    config["configurable"]["allow_all"] = True
    run(shell.run_command, config, command="sleep 30", background=True)
    processes = [job.process for job in shell.jobs.values()]
    shell.stop_all()
    assert not shell.jobs and all(p.poll() is not None for p in processes)


def test_the_helper_clears_its_old_results_counts_its_calls_and_can_look_back(tmp_path, config, monkeypatch):
    from labb.tools.task import task
    for i in range(5):
        (tmp_path / f"{i}.py").write_text(f"# file {i}\n" + "x = 1\n" * 400)
    reads = [ai(tool_calls=[("read_file", {"path": f"{i}.py"})]) for i in range(5)]
    seen = helper_replies(monkeypatch, *reads, ai(tool_calls=[("look_back", {"number": 3})]), ai("Read them all."))
    counted = []
    answer = run(task, {"configurable": {**config["configurable"], "count_usage": counted.append}}, description="Read", prompt="Read 0-4.py")
    assert answer == "Read them all." and len(counted) == 7
    # By the last call, the oldest results were cleared, and look_back read the first one again
    last = [m.content for m in seen[-1] if m.type == "tool"]
    assert last[0].startswith("[Cleared") and "# file 0" in last[-1]


def test_a_file_still_in_view_isnt_sent_again(tmp_path, config):
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from labb.tools.files import read_file
    (tmp_path / "big.py").write_text("y = 2\n" * 400)
    first = run(read_file, config, path="big.py")
    history = [HumanMessage("read it"), AIMessage("", tool_calls=[{"name": "read_file", "args": {"path": "big.py"}, "id": "r1"}]),
               ToolMessage(first, tool_call_id="r1", name="read_file")]
    seen = {"configurable": {**config["configurable"], "history": history, "summarized": 0, "cleared_before": 0}}
    assert run(read_file, seen, path="big.py").startswith("Unchanged since you read it at #2")
    # Changed, cleared from view, or a short file: read again in full
    assert not run(read_file, {"configurable": {**seen["configurable"], "cleared_before": 3}}, path="big.py").startswith("Unchanged")
    (tmp_path / "big.py").write_text("y = 3\n" * 400)
    assert run(read_file, seen, path="big.py").startswith("     1\ty = 3")


def test_tools_wait_to_be_found_when_they_would_fill_the_window(monkeypatch, config):
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.tools import StructuredTool
    from labb import tools as toolsets
    from labb.tools import tool_search

    def mcp(i: int) -> StructuredTool:
        return StructuredTool.from_function(lambda text="": "ok", name=f"mcp__issues__tool_{i}",
                                            description=f"(From the MCP server issues.) Does issue thing number {i}. " + "Detail. " * 30)

    many = [mcp(i) for i in range(40)]
    always = StructuredTool.from_function(lambda text="": "ok", name="mcp__core__status", description="Status.", metadata={"always_load": True})
    monkeypatch.setattr(toolsets, "extra_tools", [*many, always])
    run_config = {"configurable": {**config["configurable"], "thread_id": "cli-defer"}}
    names = {t.name for t in model.tools_of(run_config)}
    assert "find_tools" in names and "mcp__issues__tool_3" not in names and "mcp__core__status" in names and "read_file" in names
    assert "mcp__issues__tool_3: (From the MCP server issues.)" in model.system_prompt(run_config)
    found = tool_search.find_tools.invoke({"query": "issue thing number 3"}, run_config)
    assert "mcp__issues__tool_3" in found and found.splitlines()[-1].startswith("Loaded: ")
    state = {"messages": [HumanMessage("x"), AIMessage("", tool_calls=[{"name": "find_tools", "args": {"query": "3"}, "id": "f"}]),
                          ToolMessage(found, tool_call_id="f", name="find_tools")]}
    assert "mcp__issues__tool_3" in {t.name for t in model.tools_of(run_config, state)}
    assert "mcp__issues__tool_3" not in model.hidden_tools(run_config, state=state)
    # A big window takes them all
    from dataclasses import replace
    from labb import llm
    monkeypatch.setattr(llm, "for_config", lambda c: replace(llm.default, context_window=1_000_000))
    assert "find_tools" not in {t.name for t in model.tools_of(run_config)}


def test_a_cut_instruction_file_says_where_the_rest_is(tmp_path):
    from labb import engine
    text = "# Top\nIntro.\n" + "word " * 2000 + "\n## Deploying\nSteps.\n## Testing\nRun it."
    (tmp_path / "AGENTS.md").write_text(text)
    [(path, kept)] = model.project_instructions(str(tmp_path), engine.DEFAULT, window=2_000)
    assert len(kept) < 1_500 and f"the rest is in {tmp_path / 'AGENTS.md'}" in kept and "Its headings: Deploying; Testing" in kept


def test_the_context_gauge_and_the_save_before_a_summary(monkeypatch, config):
    from dataclasses import replace
    from langchain_core.messages import AIMessage, HumanMessage
    from labb import context, engine, llm
    from conftest import FakeModel
    monkeypatch.setattr(model, "system_prompt", lambda c: "You help.")
    state = {"messages": [m for i in range(8) for m in (HumanMessage("q" * 2000, id=f"h{i}"), AIMessage("a" * 2000, id=f"a{i}"))]
             + [HumanMessage("now", id="now")], "cleared_before": 0}
    tokens = context.input_tokens(state, "You help.", model.tools_of(config, state), 1, 1.0)
    # 60% full: the model is told, two thirds of the way to 75%
    monkeypatch.setattr(llm, "for_config", lambda c: replace(llm.default, context_window=int(tokens / 0.6)))
    sent = model.model_input(state, config)
    assert "Context: 60% full; older turns get summarized at 75%." in sent[-1].text
    # Over 75%: first a round to save notes, then the summary
    monkeypatch.setattr(llm, "for_config", lambda c: replace(llm.default, context_window=int(tokens / 0.8),
                                                             summary_model=FakeModel(messages=iter([AIMessage("S")] * 5))))
    first = model.compact_context(state, config)
    assert first["flushed"] and first["notes"] == [engine.CONTEXT_FLUSH_PROMPT] and "summary" not in first
    second = model.compact_context({**state, **first}, config)
    assert second["summary"].startswith("S") and second["flushed"] is False
