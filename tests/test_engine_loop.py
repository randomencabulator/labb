"""An engine's own loop, tools and hooks: what they do, and the limits they keep to."""

import json
import threading
from dataclasses import replace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from labb import engine, engine_steps, hooks, llm, model, runner
from labb.agent import agent, chat_agent
from labb.engine import DEFAULT_LOOP, PRESETS, check_loop, resolve
from labb.tool_node import tool_node
from labb.tools import ToolRules, custom
from conftest import FakeModel, ai


@pytest.fixture(autouse=True)
def not_on_the_server(monkeypatch):
    """The server's tests set its sandbox in this process; these are the terminal's"""
    monkeypatch.setattr(custom, "sandbox", None)


def edge(source, target, handle=None):
    return {"source": source, "target": target, "handle": handle}


def base(*extra_nodes, edges=()):
    return {
        "nodes": [{"id": "llm_call", "type": "llm_call"}, {"id": "tool_node", "type": "tool_node"}, *extra_nodes],
        "edges": [edge("start", "llm_call"), edge("llm_call", "tool_node", "tools"), edge("tool_node", "llm_call"), *edges],
    }


@pytest.fixture
def side_model(monkeypatch):
    """What an engine's side calls (a step's, a prompt tool's) get: side_model("text", ...); `.seen` has what they were asked"""

    class Recording(FakeModel):
        def invoke(self, messages, *args, **kwargs):
            seen.append(messages[-1].text)
            return super().invoke(messages, *args, **kwargs)

    seen = []

    def use(*replies, tokens=(10, 5)):
        usage = {"input_tokens": tokens[0], "output_tokens": tokens[1], "total_tokens": sum(tokens)}
        fake = Recording(messages=iter([AIMessage(text, usage_metadata=usage) for text in replies]))
        monkeypatch.setattr(llm, "for_config", lambda config: replace(llm.default, model=fake))
        return seen

    return use


def reply(thread, text, overlay, config=None, stop=None, **kwargs):
    events = []
    configurable = {**(config or {}).get("configurable", {}), "engine": resolve(overlay, "test")}
    runner.run_reply(thread, text, stop or threading.Event(), events.append, configurable=configurable, name=False, **kwargs)
    return events


def saved(thread):
    return chat_agent.get_state({"configurable": {"thread_id": thread}}).values["messages"]


def spy_inputs(monkeypatch):
    seen = []
    original = model.model_input

    def spying(state, config):
        sent = original(state, config)
        seen.append(sent)
        return sent

    monkeypatch.setattr(model, "model_input", spying)
    return seen


# --- What's refused -----------------------------------------------------------------------

def test_the_presets_and_the_default_loop_are_good():
    for preset in PRESETS.values():
        assert check_loop(preset["loop"]) == []
    assert check_loop(DEFAULT_LOOP) == []


def test_the_default_as_data_is_the_same_graph():
    built = engine_steps.compile_loop(DEFAULT_LOOP).get_graph()
    usual = agent.get_graph()
    assert {n for n in built.nodes} == {n for n in usual.nodes}
    pairs = lambda graph: {(e.source, e.target, e.conditional) for e in graph.edges}
    assert pairs(built) == pairs(usual)


NOTE = {"id": "a", "type": "note", "config": {"text": "x"}}


@pytest.mark.parametrize("loop, said", [
    ({"nodes": [], "edges": []}, "needs the Model"),
    ({"nodes": [{"id": "llm_call", "type": "llm_call"}], "edges": []}, "needs the Run tools"),
    ({"nodes": [{"id": "x", "type": "nope"}], "edges": []}, "unknown type"),
    ({"nodes": [{"id": "mine", "type": "llm_call"}], "edges": []}, "has to be llm_call"),
    ({"nodes": [{"id": "llm_call", "type": "note", "config": {"text": "x"}}], "edges": []}, "is taken"),
    ({**base(), "extra": 1}, "unknown setting"),
    ({**base(), "max_model_calls": 0}, "max_model_calls"),
    (base(), "needs an edge out for done"),
    (base(edges=[edge("llm_call", "end", "done"), edge("llm_call", "end", "done")]), "two edges"),
    (base(edges=[edge("llm_call", "end", "done"), edge("tool_node", "end")]), "two edges"),
    (base(edges=[edge("llm_call", "end", "done"), edge("llm_call", "end", "nope")]), "no way out called nope"),
    (base(edges=[edge("llm_call", "end", "done"), edge("end", "llm_call")]), "out of the end"),
    (base(edges=[edge("llm_call", "ghost", "done")]), "no such step"),
    # Tool calls without results break the next model call
    ({"nodes": base()["nodes"], "edges": [edge("start", "llm_call"), edge("llm_call", "end", "tools"), edge("llm_call", "tool_node", "done"), edge("tool_node", "end")]}, "straight to Run tools"),
    # Run tools with no calls to run
    ({"nodes": base()["nodes"], "edges": [edge("start", "tool_node"), edge("llm_call", "tool_node", "tools"), edge("llm_call", "end", "done"), edge("tool_node", "llm_call")]}, "only the Model's tools way"),
    (base(NOTE, edges=[edge("llm_call", "end", "done"), edge("a", "end")]), "nothing leads to"),
    # A loop with no way out
    (base(edges=[edge("llm_call", "llm_call", "done")]), "no way to the end"),
    # Going round for free: nothing caps it
    (base(NOTE, {"id": "b", "type": "route", "config": {"test": "var_set", "var": "x"}},
          edges=[edge("llm_call", "a", "done"), edge("a", "b"), edge("b", "a", "yes"), edge("b", "end", "no")]), "without the Model"),
])
def test_loops_that_cant_run_are_refused(loop, said):
    assert any(said in problem for problem in check_loop(loop)), check_loop(loop)


@pytest.mark.parametrize("step, said", [
    ({"id": "a", "type": "model_step", "config": {"save_as": "x"}}, "what to ask"),
    ({"id": "a", "type": "model_step", "config": {"prompt": "p", "save_as": "not ok"}}, "give its answer a name"),
    ({"id": "a", "type": "note", "config": {}}, "write the note"),
    ({"id": "a", "type": "check", "config": {}}, "which command"),
    ({"id": "a", "type": "check", "config": {"command": "x", "timeout": 0}}, "timeout"),
    ({"id": "a", "type": "python", "config": {}}, "write the code"),
    ({"id": "a", "type": "route", "config": {"test": "nope"}}, "what to test"),
    ({"id": "a", "type": "route", "config": {"test": "visits_over", "value": "many"}}, "whole number"),
    ({"id": "a", "type": "route", "config": {"test": "var_set"}}, "which saved value"),
])
def test_steps_are_checked(step, said):
    assert any(said in problem for problem in check_loop(base(step)))


# --- Running ------------------------------------------------------------------------------

def test_a_loop_goes_through_its_steps_and_nothing_else(fake_model, side_model, config, tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("hi")
    fake_model(ai(tool_calls=[("list_files", {"path": "."})]), ai("There's a.txt."))
    asked = side_model("1. Look around.")
    sent = spy_inputs(monkeypatch)
    thread = f"cli-{uuid4()}"
    events = reply(thread, "what's here?", {"loop": PRESETS["plan-first"]["loop"]}, config)

    assert events[-1]["type"] == "done"
    assert asked == ["Write a short numbered plan for this request. Only the plan.\n\nRequest: what's here?"]
    # The plan reached the model's first call only, and only the model
    assert "1. Look around." in sent[0][-1].text and "1. Look around." not in sent[1][-1].text
    assert [m.type for m in saved(thread)] == ["human", "ai", "tool", "ai"]
    assert not any("Look around" in m.text for m in saved(thread))
    assert any(e["type"] == "tool_call" and e["name"] == "step: Plan" for e in events)


def test_a_review_can_send_the_reply_back(fake_model, side_model, monkeypatch):
    fake_model(ai("draft"), ai("better"))
    asked = side_model("The date is wrong.", "OK")
    sent = spy_inputs(monkeypatch)
    thread = f"web-{uuid4()}"
    events = reply(thread, "when?", {"loop": PRESETS["self-review"]["loop"]})

    assert events[-1]["type"] == "done"
    assert len(asked) == 2 and "The reply was:\nbetter" in asked[1]
    # After its own reply, the model gets the review as a message of its own, not saved
    assert sent[1][-1].type == "human" and "The date is wrong." in sent[1][-1].text
    assert [m.text for m in saved(thread)] == ["when?", "draft", "better"]


def test_side_calls_count_as_usage(fake_model, side_model):
    fake_model(ai("draft", tokens=(100, 20)), ai("better", tokens=(100, 20)))
    side_model("Fix it.", "OK", tokens=(7, 3))
    stats = reply(f"web-{uuid4()}", "q", {"loop": PRESETS["self-review"]["loop"]})[-1]["stats"]
    assert stats["llm_calls"] == 4
    assert stats["input_tokens"] == 2 * 100 + 2 * 7 and stats["output_tokens"] == 2 * 20 + 2 * 3


def test_side_calls_reach_the_budget(fake_model, side_model):
    from labb import costs

    fake_model(ai("draft"))
    side_model("OK", tokens=(1_000_000, 0))
    stop = threading.Event()
    budget = costs.Budget(0.5, 0, "m", {"m": [1, 1]}, stop)
    thread = f"web-{uuid4()}"
    reply(thread, "q", {"loop": PRESETS["self-review"]["loop"]}, {"configurable": {"budget": budget}}, stop)
    assert budget.spent == 1.0 and budget.reached


ROUND_FOREVER = {
    "nodes": [{"id": "llm_call", "type": "llm_call"}, {"id": "tool_node", "type": "tool_node"},
              {"id": "again", "type": "route", "config": {"test": "var_set", "var": "never"}},
              {"id": "note", "type": "note", "config": {"text": "Go on."}}],
    "edges": [edge("start", "llm_call"), edge("llm_call", "tool_node", "tools"), edge("tool_node", "llm_call"),
              edge("llm_call", "again", "done"), edge("again", "end", "yes"), edge("again", "note", "no"), edge("note", "llm_call")],
}


def test_the_model_call_cap_is_a_real_limit(fake_model):
    fake = fake_model(*[ai(f"reply {i}") for i in range(20)])
    thread = f"web-{uuid4()}"
    events = reply(thread, "q", {"loop": {**ROUND_FOREVER, "max_model_calls": 3}})
    assert any(e["type"] == "error" and "as many model calls" in e["message"] for e in events)
    assert sum(m.type == "ai" for m in saved(thread)) == 3
    # The ones that weren't used are still there
    assert next(fake.messages).text == "reply 3"


def test_a_loop_of_many_steps_gets_its_model_calls(fake_model):
    # 6 steps a round: the usual "3 steps per call" would cut it short
    long = {"nodes": [{"id": "llm_call", "type": "llm_call"}, {"id": "tool_node", "type": "tool_node"},
                      *[{"id": f"n{i}", "type": "note", "config": {"text": "x"}} for i in range(5)],
                      {"id": "stop", "type": "route", "config": {"test": "model_calls_over", "value": 2}}],
            "edges": [edge("start", "llm_call"), edge("llm_call", "tool_node", "tools"), edge("tool_node", "llm_call"), edge("llm_call", "n0", "done"),
                      *[edge(f"n{i}", f"n{i + 1}") for i in range(4)], edge("n4", "stop"), edge("stop", "end", "yes"), edge("stop", "llm_call", "no")]}
    assert check_loop(long) == []
    fake_model(*[ai(f"r{i}") for i in range(3)])
    thread = f"web-{uuid4()}"
    # What an admin's limit of 5 model calls comes to
    events = reply(thread, "q", {"loop": long}, recursion_limit=16)
    assert events[-1]["type"] == "done" and sum(m.type == "ai" for m in saved(thread)) == 3


def test_an_admins_limit_beats_the_engines(fake_model):
    fake_model(*[ai(f"r{i}") for i in range(20)])
    thread = f"web-{uuid4()}"
    reply(thread, "q", {"loop": {**ROUND_FOREVER, "max_model_calls": 15}}, recursion_limit=1 + 3 * 2)
    assert sum(m.type == "ai" for m in saved(thread)) == 2


def test_each_reply_counts_afresh(fake_model):
    fake_model(*[ai(f"r{i}") for i in range(20)])
    thread = f"web-{uuid4()}"
    loop = {**ROUND_FOREVER, "max_model_calls": 2}
    reply(thread, "one", {"loop": loop})
    reply(thread, "two", {"loop": loop})
    assert sum(m.type == "ai" for m in saved(thread)) == 4


def test_stopping_works_inside_a_step(fake_model, monkeypatch):
    fake_model(ai("first"), ai("after"))
    stop = threading.Event()

    class Stops:
        def invoke(self, messages, config=None):
            stop.set()
            return AIMessage("late")

    monkeypatch.setattr(llm, "for_config", lambda c: replace(llm.default, model=Stops()))
    thread = f"web-{uuid4()}"
    events = reply(thread, "q", {"loop": PRESETS["self-review"]["loop"]}, stop=stop)
    assert events[-1]["type"] == "stopped"
    assert [m.type for m in saved(thread)][:2] == ["human", "ai"]
    assert reply(thread, "next", {})[-1]["type"] == "done"


def test_stopping_during_tools_leaves_every_call_answered(fake_model, config):
    fake_model(ai(tool_calls=[("list_files", {"path": "."})]), ai("after"))
    thread = f"cli-{uuid4()}"
    stop = threading.Event()
    events = []

    def stop_at_the_call(event):
        events.append(event)
        if event["type"] == "tool_call":
            stop.set()

    configurable = {**config["configurable"], "engine": resolve({"loop": PRESETS["minimal"]["loop"]}, "test")}
    runner.run_reply(thread, "q", stop, stop_at_the_call, configurable=configurable, name=False)
    assert events[-1]["type"] == "stopped"
    calls = {c["id"] for m in saved(thread) if m.type == "ai" for c in m.tool_calls}
    assert calls == {m.tool_call_id for m in saved(thread) if m.type == "tool"}
    assert reply(thread, "next", {}, config)[-1]["type"] == "done"


def test_compact_is_only_announced_when_it_summarized(fake_model):
    fake_model(ai("hi"))
    events = reply(f"web-{uuid4()}", "q", {"loop": DEFAULT_LOOP})
    assert not any(e["type"] == "compact" for e in events)


def test_a_chat_keeps_going_across_loops(fake_model, side_model):
    fake_model(ai("a"), ai("b"), ai("c"))
    side_model("plan", "plan")
    thread = f"web-{uuid4()}"
    for overlay in ({"loop": PRESETS["plan-first"]["loop"]}, {}, {"loop": PRESETS["minimal"]["loop"]}):
        assert reply(thread, "q", overlay)[-1]["type"] == "done"
    assert [m.type for m in saved(thread)] == ["human", "ai"] * 3


def test_a_engine_of_an_agent_is_used_where_agents_run():
    custom_engine = resolve({"prompts": {"base": "Mine."}}, "of-agent")
    agent_config = {"configurable": {"agent": {"name": "A", "engine": custom_engine}}}
    assert engine.of(agent_config) is custom_engine
    # One chosen for the run comes first
    chosen = resolve({}, "chosen")
    assert engine.of({"configurable": {"agent": {"engine": custom_engine}, "engine": chosen}}) is chosen


# --- Commands -----------------------------------------------------------------------------

def checking(command: str, tries: int = 3) -> dict:
    loop = json.loads(json.dumps(PRESETS["test-until-green"]["loop"]))
    next(n for n in loop["nodes"] if n["id"] == "tests")["config"]["command"] = command
    next(n for n in loop["nodes"] if n["id"] == "enough")["config"]["value"] = tries
    return {"loop": loop}


def test_failures_go_back_until_it_passes(fake_model, config, tmp_path, monkeypatch):
    fake_model(ai("one"), ai("two"))
    sent = spy_inputs(monkeypatch)
    overlay = checking("test -f done || { echo not done yet; touch done; exit 1; }")
    events = reply(f"cli-{uuid4()}", "q", overlay, config)
    assert events[-1]["type"] == "done" and len(sent) == 2
    assert "not done yet" in sent[1][-1].text and "failed" in sent[1][-1].text
    assert [e["error"] for e in events if e["type"] == "tool_result"] == [True, False]


def test_it_gives_up_after_its_tries(fake_model, config):
    fake_model(*[ai(f"r{i}") for i in range(10)])
    thread = f"cli-{uuid4()}"
    assert reply(thread, "q", checking("exit 1", tries=1), config)[-1]["type"] == "done"
    assert sum(m.type == "ai" for m in saved(thread)) == 2


def test_commands_dont_run_in_plan_mode(fake_model, config, tmp_path):
    fake_model(ai("the plan"))
    plan = {"configurable": {**config["configurable"], "mode": "plan"}}
    assert reply(f"cli-{uuid4()}", "q", checking("touch made"), plan)[-1]["type"] == "done"
    assert not (tmp_path / "made").exists()


def test_commands_dont_run_without_a_terminal(fake_model):
    fake_model(ai("hi"))
    events = reply(f"web-{uuid4()}", "q", checking("touch /tmp/never-made-by-labb"))
    assert any(e["type"] == "error" and "only works in the terminal" in e["message"] for e in events)


def test_stopping_kills_a_steps_command(fake_model, config, tmp_path):
    fake_model(ai("hi"))
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    events = reply(f"cli-{uuid4()}", "q", checking("sleep 30; touch finished"), config, stop)
    assert events[-1]["type"] == "stopped" and not (tmp_path / "finished").exists()


def test_what_only_works_in_the_terminal_is_said():
    assert engine.terminal_only({}) == [] and not engine.runs_code({})
    assert engine.terminal_only(checking("x")) and engine.runs_code(checking("x"))
    assert engine.terminal_only({"hooks": {"PreModel": [{"command": "x"}]}})
    assert engine.terminal_only({"mcp": {"a": {"command": "x"}}})
    assert engine.terminal_only({"tools": {"custom": [{"name": "a", "kind": "command"}]}})
    assert not engine.terminal_only({"tools": {"custom": [{"name": "a", "kind": "prompt"}]}})
    python = {"tools": {"custom": [{"name": "a", "kind": "python"}]}}
    assert engine.runs_python(python) and not engine.terminal_only(python) and engine.runs_code(python)


# --- Custom tools -------------------------------------------------------------------------

COMMAND_TOOL = {"name": "shout", "kind": "command", "description": "Says it.", "command": "echo {{what}}",
                "params": [{"name": "what", "description": "What to say"}]}


def call_tool(name, args, config):
    return tool_node({"messages": [AIMessage("", tool_calls=[{"name": name, "args": args, "id": "c"}])]}, config)["messages"][0]


def with_tools(*specs, **configurable):
    return {"configurable": {**configurable, "engine": resolve({"tools": {"custom": list(specs)}}, "test")}}


@pytest.mark.parametrize("spec, said", [
    ({"name": "Bad Name", "kind": "prompt", "description": "d", "prompt": "p"}, "lowercase"),
    ({"name": "a", "kind": "prompt", "prompt": "p"}, "describe"),
    ({"name": "a", "kind": "nope", "description": "d"}, "kind should be"),
    ({"name": "a", "kind": "command", "description": "d"}, "needs its command"),
    ({"name": "a", "kind": "prompt", "description": "d", "prompt": "p", "params": [{"name": "not ok"}]}, "params"),
    ({"name": "a", "kind": "prompt", "description": "d", "prompt": "p", "params": [{"name": "x", "type": "date"}]}, "type should be"),
])
def test_custom_tools_are_checked(spec, said):
    assert any(said in problem for problem in engine.check({"tools": {"custom": [spec]}}))


def test_they_cant_stand_in_for_labbs_tools(config):
    run = with_tools({"name": "run_command", "kind": "prompt", "description": "Fake.", "prompt": "p"}, **config["configurable"])
    names = [tool.name for tool in model.tools_of(run)]
    assert "custom__run_command" in names and names.count("run_command") == 1
    assert next(t for t in model.tools_of(run) if t.name == "run_command").description != "Fake."


def test_a_command_tool_asks_and_its_arguments_stay_arguments(config, tmp_path):
    asked = []

    def approve(command, *args, **kwargs):
        asked.append(command)
        return "yes"

    run = with_tools(COMMAND_TOOL, **config["configurable"], approve=approve)
    result = call_tool("custom__shout", {"what": "hi; touch hacked"}, run)
    assert "hi; touch hacked" in result.content
    assert not (tmp_path / "hacked").exists()


def test_a_command_tool_that_changes_things_needs_the_yes(config, tmp_path):
    spec = {**COMMAND_TOOL, "command": "touch {{what}}"}
    asked = []

    def refuse(command, *args, **kwargs):
        asked.append(command)
        return "no"

    result = call_tool("custom__shout", {"what": "made"}, with_tools(spec, **config["configurable"], approve=refuse))
    assert asked and not (tmp_path / "made").exists() and result.tool_call_id == "c"


def test_a_command_tool_keeps_to_plan_mode_and_the_rules(config, tmp_path):
    spec = {**COMMAND_TOOL, "command": "touch {{what}}"}
    call_tool("custom__shout", {"what": "made"}, with_tools(spec, **config["configurable"], mode="plan"))
    assert not (tmp_path / "made").exists()
    off = call_tool("custom__shout", {"what": "made"}, with_tools(spec, **config["configurable"], tools=ToolRules.of(deny=("run_command",))))
    assert "turned off" in off.content and not (tmp_path / "made").exists()


def test_custom_tools_can_be_turned_off_like_others(config):
    run = with_tools(COMMAND_TOOL, **config["configurable"], tools=ToolRules.of(deny=("custom",)))
    assert "custom__shout" not in [t.name for t in model.tools_of(run)]
    assert "turned off" in call_tool("custom__shout", {"what": "x"}, run).content


def test_agents_dont_get_custom_tools():
    agent_run = with_tools(COMMAND_TOOL, agent={"name": "A", "toolsets": ["web"], "documents": "none", "skills": "none", "memories": False})
    assert "custom__shout" not in [t.name for t in model.tools_of(agent_run)]
    assert "isn't one of your tools" in call_tool("custom__shout", {"what": "x"}, agent_run).content


def test_a_prompt_tool_asks_a_model_and_counts(side_model):
    asked = side_model("Bonjour", tokens=(9, 1))
    counted = []
    run = with_tools({"name": "translate", "kind": "prompt", "description": "Translates.", "prompt": "French for: {{text}}",
                      "params": [{"name": "text"}]}, count_usage=counted.append)
    assert call_tool("custom__translate", {"text": "hello"}, run).content == "Bonjour"
    assert asked == ["French for: hello"] and counted[0].usage_metadata["input_tokens"] == 9


def test_a_prompt_tools_model_needs_access(monkeypatch):
    def refuse(spec, configurable):
        raise PermissionError("not for you")

    monkeypatch.setattr(llm, "check_access", refuse)
    run = with_tools({"name": "t", "kind": "prompt", "description": "d", "prompt": "p", "model": "ollama:forbidden"})
    result = call_tool("custom__t", {}, run)
    assert result.status == "error" and "not for you" in result.content


def test_an_action_tool_runs_as_whoever_is_chatting(monkeypatch):
    seen = []
    monkeypatch.setattr(custom, "action", lambda config, action, args: seen.append((config["configurable"]["user_id"], action, args)) or "done")
    run = with_tools({"name": "ticket", "kind": "action", "description": "d", "action": "a1", "params": [{"name": "title"}]}, user_id="u1")
    assert call_tool("custom__ticket", {"title": "x"}, run).content == "done"
    assert seen == [("u1", "a1", {"title": "x"})]


def test_optional_parameters_can_be_left_out(side_model):
    side_model("ok")
    run = with_tools({"name": "t", "kind": "prompt", "description": "d", "prompt": "{{a}}|{{b}}",
                      "params": [{"name": "a"}, {"name": "b", "required": False}]})
    assert call_tool("custom__t", {"a": "1"}, run).content == "ok"
    assert "Error" in call_tool("custom__t", {}, run).content


def test_they_reach_the_model(monkeypatch):
    bound = []

    class Recording:
        def bind_tools(self, tools, **kwargs):
            bound.append([tool.name for tool in tools])
            return self

    monkeypatch.setattr(llm, "load", lambda spec: replace(llm.default, model=Recording(), strict=False))
    model.model_with_tools.cache_clear()
    try:
        h = resolve({"tools": {"custom": [{"name": "t", "kind": "prompt", "description": "d", "prompt": "p"}]}})
        model.model_with_tools("spec", False, None, None, h.tool_shape())
        model.model_with_tools("spec", False, frozenset({"web"}), None, h.tool_shape())
    finally:
        model.model_with_tools.cache_clear()
    assert "custom__t" in bound[0] and "custom__t" not in bound[1]


# --- Python -------------------------------------------------------------------------------

def python_loop(code: str) -> dict:
    return {"loop": {"nodes": [{"id": "llm_call", "type": "llm_call"}, {"id": "tool_node", "type": "tool_node"},
                               {"id": "py", "type": "python", "label": "Count", "config": {"code": code}}],
                     "edges": [edge("start", "py"), edge("py", "llm_call"), edge("llm_call", "tool_node", "tools"),
                               edge("tool_node", "llm_call"), edge("llm_call", "end", "done")]}}


def test_python_sees_the_run_and_sets_vars_and_notes(fake_model, config, monkeypatch):
    fake_model(ai("hi"))
    sent = spy_inputs(monkeypatch)
    thread = f"cli-{uuid4()}"
    code = "result = {'vars': {'words': len(inputs['user_message'].split())}, 'note': 'Words: ' + str(len(inputs['user_message'].split()))}"
    assert reply(thread, "three words here", python_loop(code), config)[-1]["type"] == "done"
    assert "Words: 3" in sent[0][-1].text
    assert chat_agent.get_state({"configurable": {"thread_id": thread}}).values["vars"]["words"] == 3


def test_python_errors_say_what_failed(fake_model, config):
    fake_model(ai("hi"))
    events = reply(f"cli-{uuid4()}", "q", python_loop("raise ValueError('nope here')"), config)
    assert any(e["type"] == "error" and "nope here" in e["message"] for e in events)


def test_python_runs_in_its_own_process_with_a_timeout(config):
    assert custom.run_python("import os; result = os.getpid()", {}, config) != __import__("os").getpid()
    with pytest.raises(custom.EngineRunError, match="timed out"):
        custom.run_python("import time; time.sleep(30)", {}, config, timeout=1)


def test_python_is_stopped_with_the_reply(config, tmp_path):
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    run = {"configurable": {**config["configurable"], "stop_requested": stop}}
    with pytest.raises(custom.EngineRunError, match="stopped"):
        custom.run_python(f"import time; time.sleep(30); open({str(tmp_path / 'late')!r}, 'w')", {}, run)
    assert not (tmp_path / "late").exists()


def test_without_a_terminal_or_a_sandbox_python_doesnt_run():
    with pytest.raises(custom.EngineRunError, match="sandbox"):
        custom.run_python("result = 1", {}, {"configurable": {}})


def test_a_servers_python_only_runs_in_its_sandbox(monkeypatch, config):
    ran = []
    monkeypatch.setattr(custom, "sandbox", lambda code, inputs, timeout: ran.append(code) or {"result": 42, "stdout": ""})
    # Even with a working directory, nothing runs here
    assert custom.run_python("result = __import__('os').getpid()", {}, config) == 42 and len(ran) == 1


# --- Hooks --------------------------------------------------------------------------------

def hook(event, command):
    return {"hooks": {event: [{"command": command}]}}


def test_the_new_events_run_and_can_leave_notes(fake_model, config, tmp_path, monkeypatch):
    fake_model(ai("hi"))
    sent = spy_inputs(monkeypatch)
    overlay = {"hooks": {
        "RunStart": [{"command": "echo start >> log; echo from-start >&2; exit 2"}],
        "PreModel": [{"command": "echo pre >> log; echo from-pre >&2; exit 2"}],
        "PostModel": [{"command": "echo post >> log"}],
        "RunEnd": [{"command": "echo end >> log"}],
    }}
    assert reply(f"cli-{uuid4()}", "q", overlay, config)[-1]["type"] == "done"
    assert (tmp_path / "log").read_text().split() == ["start", "pre", "post", "end"]
    assert "from-start" in sent[0][-1].text and "from-pre" in sent[0][-1].text


def test_a_engines_tool_hooks_add_to_the_settings(config, tmp_path):
    settings_hooks = {"PreToolUse": [{"command": "echo settings >> log"}]}
    run = {"configurable": {**config["configurable"], "hooks": settings_hooks,
                            "engine": resolve(hook("PreToolUse", "echo engine >> log; echo no way >&2; exit 2"), "t")}}
    result = call_tool("list_files", {"path": "."}, run)
    assert "no way" in result.content and (tmp_path / "log").read_text().split() == ["settings", "engine"]


def test_pre_compact_runs_only_before_a_summary(config, tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "for_config", lambda c, w=[1_000_000]: replace(llm.default, context_window=w[0], summary_model=FakeModel(messages=iter([AIMessage("S")]))))
    monkeypatch.setattr(model, "system_prompt", lambda c: "x")
    state = {"messages": [m for i in range(9) for m in (HumanMessage("q" * 3000, id=f"h{i}"), AIMessage("a" * 3000, id=f"a{i}"))] + [HumanMessage("now", id="now")]}
    run = {"configurable": {**config["configurable"], "engine": resolve(hook("PreCompact", "echo ran >> log"), "t")}}
    assert model.compact_context(state, run) == {"cleared_before": 0}
    assert not (tmp_path / "log").exists()
    # Too big for the summary model at once: summarized in parts, one hook run for the summary
    monkeypatch.setattr(llm, "for_config", lambda c: replace(llm.default, context_window=2_000, summary_model=FakeModel(messages=iter([AIMessage("S")] * 20))))
    assert model.compact_context(state, run)["summary"] == "S"
    assert (tmp_path / "log").read_text().split() == ["ran"]


def test_hooks_dont_run_without_a_terminal(fake_model, tmp_path):
    fake_model(ai("hi"))
    marker = tmp_path / "ran"
    overlay = {"hooks": {"RunStart": [{"command": f"touch {marker}"}], "PreModel": [{"command": f"touch {marker}"}]}}
    assert reply(f"web-{uuid4()}", "q", overlay)[-1]["type"] == "done"
    assert not marker.exists() and hooks.around("PreModel", {"configurable": {}}) == []


@pytest.mark.parametrize("overlay, said", [
    ({"hooks": {"Nope": []}}, "unknown event"),
    ({"hooks": {"PreModel": "x"}}, "should be a list"),
    ({"hooks": {"PreModel": [{}]}}, "has a command"),
    ({"hooks": {"PreModel": [{"command": "x", "timeout": 0}]}}, "timeout"),
    ({"mcp": {"a": {}}}, "command or a url"),
])
def test_hooks_and_servers_are_checked(overlay, said):
    assert any(said in problem for problem in engine.check(overlay))
