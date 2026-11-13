"""What the default engine does, recorded before the core became configurable: the prompts, the
graph, the tools, the model's input and compaction must stay exactly as they are here.

UPDATE_GOLDEN=1 uv run python -m pytest tests/test_engine_golden.py rewrites the record; do
that only for a change to the default that's meant, and read the diff.
"""

import datetime
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from labb import context, llm, memories, model, runner, skills, tools
from labb.agent import agent
from labb.tools import ToolRules, documents, gmail, google_calendar, notes, todo
from labb.tools.loop import Loop

GOLDEN = Path(__file__).parent / "golden" / "engine_default.json"


class FrozenDate(datetime.date):
    @classmethod
    def today(cls):
        return cls(2026, 1, 15)


class SummaryModel:
    """Remembers what it was asked to summarize"""

    def __init__(self):
        self.seen = []

    def invoke(self, messages):
        self.seen.append([[m.type, m.text] for m in messages])
        return AIMessage("The summary.")


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """A project in a throwaway home, with everything that depends on what ran before held still"""

    home = tmp_path / "home"
    project = home / "project"
    project.mkdir(parents=True)
    (project / "AGENTS.md").write_text("Use tabs.")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(model, "date", FrozenDate)
    monkeypatch.setattr(documents, "enabled", lambda: True)
    monkeypatch.setattr(gmail, "enabled", lambda: False)
    monkeypatch.setattr(google_calendar, "enabled", lambda: False)
    monkeypatch.setattr(tools, "extra_tools", [])
    monkeypatch.setattr(tools, "server_tools", [])
    monkeypatch.setattr(skills, "available", lambda config: [{"name": "demo", "description": "A demo skill."}])
    monkeypatch.setattr(memories, "pinned", lambda config: [{"id": "m1", "text": "Likes tea."}])
    return str(project)


AGENT = {"name": "Reader", "description": "Reads things.", "instructions": "Be kind.", "toolsets": ["web"],
         "documents": "none", "skills": "none", "memories": False}


def scenarios(workdir: str) -> dict[str, dict]:
    terminal = {"workdir": workdir, "thread_id": "golden", "git_context": "On branch main."}
    return {
        "web": {},
        "web_off": {"web_off": True, "tools": ToolRules.of(deny=("web",))},
        "web_fast": {"answer_mode": "fast"},
        "web_thinking": {"answer_mode": "thinking"},
        "task": {"task": {"thread_id": "golden"}},
        "agent": {"agent": AGENT},
        "terminal": terminal,
        "terminal_plan": {**terminal, "mode": "plan"},
        "terminal_rules": {**terminal, "tools": ToolRules.of(deny=("run_command",))},
        "terminal_loop": {**terminal, "loop": Loop("check it", every=60)},
        "terminal_self_paced": {**terminal, "loop": Loop("check it")},
    }


def conversation() -> dict:
    """A conversation with results to clear, memories to recall and turns to summarize"""

    messages = []
    for i in range(6):
        messages += [
            HumanMessage(f"question {i} " + "x" * 400, id=f"h{i}"),
            AIMessage("", id=f"c{i}", tool_calls=[{"name": "read_file", "args": {"path": f"{i}.py"}, "id": f"t{i}"}]),
            ToolMessage("r" * 1500, tool_call_id=f"t{i}", name="read_file", id=f"r{i}"),
            AIMessage(f"answer {i} " + "y" * 400, id=f"a{i}"),
        ]
    messages.append(HumanMessage("the current question", id="now"))
    return {"messages": messages, "recalled": [{"id": "m2", "text": "Prefers short answers."}]}


def dumped(messages: list) -> list:
    return [[m.type, m.text] for m in messages]


def schemas(has_workdir: bool, config: dict) -> list:
    return [convert_to_openai_tool(tool) for tool in
            tools.tools_for(has_workdir, tools.offered_for(config), tools.rules_for(config))]


def record(workdir: str, monkeypatch) -> dict:
    out = {"graph": agent.get_graph(xray=True).draw_mermaid(), "prompts": {}, "tools": {}, "input": {}, "compact": {}, "summary_prompts": {}}
    for name, configurable in scenarios(workdir).items():
        config = {"configurable": configurable}
        out["prompts"][name] = model.system_prompt_parts(config)
        out["tools"][name] = schemas(bool(configurable.get("workdir")), config)
        out["summary_prompts"][name] = model.summary_prompt(config)

    todo.save(workdir, "golden", [{"task": "First step", "status": "done"}, {"task": "Second step", "status": "pending"}])
    (notes.notes_file(workdir)).write_text("## setup\nRun it.\n")
    for name in ("web", "terminal"):
        config = {"configurable": scenarios(workdir)[name]}
        out["input"][name] = dumped(model.model_input(conversation(), config))
        out["input"][f"{name}_summarized"] = dumped(model.model_input({**conversation(), "summary": "Earlier.", "summarized": 8}, config))

        # Fits, then full: what the node decides, and what the summary model is asked
        for window in (1_000_000, 3_000):
            summarizer = SummaryModel()
            monkeypatch.setattr(llm, "for_config", lambda config, w=window, s=summarizer: replace(llm.default, context_window=w, summary_model=s))
            update = model.compact_context(conversation(), config)
            out["compact"][f"{name}_{window}"] = {"update": update, "asked": summarizer.seen}
    out["constants"] = {
        "context": {key: getattr(context, key) for key in ("KEEP_TOOL_RESULTS", "MIN_CLEARED_CHARS", "COMPACT_AT", "KEEP_RECENT", "MAX_SCALE")},
        "title": runner.TITLE_PROMPT,
        "recall_query_chars": model.RECALL_QUERY_CHARS,
        "instruction_files": list(model.INSTRUCTION_FILES),
        "max_instruction_chars": model.MAX_INSTRUCTION_CHARS,
    }
    # Through JSON, so tuples and lists compare, without the throwaway paths
    return json.loads(json.dumps(out, default=str).replace(str(Path(workdir).parent), "<HOME>"))


def test_the_default_engine_does_what_was_recorded(workdir, monkeypatch):
    now = record(workdir, monkeypatch)
    if os.getenv("UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(now, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    golden = json.loads(GOLDEN.read_text())
    for part in golden:
        assert now[part] == golden[part], f"the default engine's {part} changed"
    assert set(now) == set(golden)
