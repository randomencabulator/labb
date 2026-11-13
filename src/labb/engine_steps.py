"""An engine's own loop: its nodes and edges (checked by engine.check_loop) as a graph, and what
its steps do. An engine without a loop uses agent.py's graph.

Every step keeps to what makes a chat portable: the state's channels are the same for every
engine, llm_call and tool_node keep their names, and only one step runs at a time. Model calls
are counted before they're made, so `model_call_cap` is a real limit however the loop goes round.
"""

import json
import threading
from datetime import date

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from labb import engine, llm, model
from labb.agent import chat_agent, checkpointer
from labb.state import MessagesState
from labb.tool_node import tool_node
from labb.tools.custom import EngineRunError, fill, run_process, run_python

# The key in `visits` that counts this reply's model calls (not an id a step can have)
MODEL_CALLS = "model calls"
MAX_COMPILED = 64

_compiled: dict[str, object] = {}
_compiled_lock = threading.Lock()


def values_of(state: dict, config: RunnableConfig) -> dict:
    """What templates and Python can use"""

    messages = state.get("messages") or []
    return {
        "vars": state.get("vars") or {},
        "user_message": next((m.text for m in reversed(messages) if m.type == "human"), ""),
        "last_reply": next((m.text for m in reversed(messages) if m.type == "ai" and m.text), ""),
        "today": f"{date.today():%A, %B %-d, %Y}",
        "workdir": (config.get("configurable") or {}).get("workdir") or "",
    }


def _cap_model_call(state: dict, config: RunnableConfig):
    cap = (config.get("configurable") or {}).get("model_call_cap")
    if cap and (state.get("visits") or {}).get(MODEL_CALLS, 0) >= cap:
        raise GraphRecursionError(f"The reply made {cap} model calls, as many as it may")


def model_step(step: dict):
    settings = step.get("config") or {}

    def run(state: dict, config: RunnableConfig):
        _cap_model_call(state, config)
        configurable = config.get("configurable") or {}
        spec = settings.get("model")
        # Checked for this person like any model of the run
        models = llm.for_config({"configurable": {**configurable, **({"model": spec} if spec else {})}})
        reply = models.model.invoke([HumanMessage(fill(settings["prompt"], values_of(state, config)))], config)
        if count := configurable.get("count_usage"):
            count(reply)
        update = {"vars": {settings["save_as"]: reply.text}, "step": {"text": reply.text, "model_call": True}}
        if settings.get("as_note"):
            update["notes"] = [*(state.get("notes") or []), f"{step.get('label') or settings['save_as']}:\n{reply.text}"]
        return update

    return run


def note_step(step: dict):
    def run(state: dict, config: RunnableConfig):
        return {"notes": [*(state.get("notes") or []), fill(step["config"]["text"], values_of(state, config))]}

    return run


def route_step(step: dict):
    settings = step.get("config") or {}

    def run(state: dict, config: RunnableConfig):
        values, test, visits = state.get("vars") or {}, settings.get("test"), state.get("visits") or {}
        wanted = settings.get("value")
        if test == "reply_contains":
            yes = str(wanted).lower() in values_of(state, config)["last_reply"].lower()
        elif test == "var_contains":
            yes = str(wanted).lower() in str(values.get(settings.get("var"), "")).lower()
        elif test == "var_set":
            yes = settings.get("var") in values
        elif test == "visits_over":
            yes = visits.get(settings.get("step") or step["id"], 0) > wanted
        elif test == "model_calls_over":
            yes = visits.get(MODEL_CALLS, 0) > wanted
        else:
            yes = bool(values.get("last_check_failed"))
        return {"route": "yes" if yes else "no"}

    return run


def check_step(step: dict):
    settings = step.get("config") or {}

    def run(state: dict, config: RunnableConfig):
        configurable = config.get("configurable") or {}
        workdir = configurable.get("workdir")
        if not workdir:
            # check() keeps these off the web; a run that gets here anyway doesn't run anything
            raise EngineRunError(f"{step.get('label') or step['id']} runs a command, which only works in the terminal")
        # Plan mode changes nothing, and a command might
        if configurable.get("mode") == "plan":
            return {"route": "passed", "step": {"text": "Skipped in plan mode"}}
        ok, output = run_process(["bash", "-c", settings["command"]], workdir, settings.get("timeout", 120), configurable.get("stop_requested"))
        update = {"route": "passed" if ok else "failed", "vars": {"last_check_failed": not ok},
                  "step": {"text": output or ("Passed" if ok else "Failed"), "error": not ok}}
        if not ok:
            update["notes"] = [*(state.get("notes") or []), f"`{settings['command']}` failed:\n{output}\nFix what it reports."]
        return update

    return run


def python_step(step: dict):
    settings = step.get("config") or {}

    def run(state: dict, config: RunnableConfig):
        values = values_of(state, config)
        values["messages"] = [{"role": m.type, "text": m.text} for m in (state.get("messages") or [])[-20:]]
        result = run_python(settings["code"], values, config, settings.get("timeout", 30))
        update = {"step": {"text": json.dumps(result, ensure_ascii=False, default=str)[:1000]}}
        if isinstance(result, dict):
            if isinstance(result.get("vars"), dict):
                update["vars"] = result["vars"]
            if isinstance(result.get("note"), str) and result["note"]:
                update["notes"] = [*(state.get("notes") or []), result["note"]]
        return update

    return run


def _llm_call(state: dict, config: RunnableConfig):
    _cap_model_call(state, config)
    return model.llm_call(state, config)


BUILDERS = {"model_step": model_step, "note": note_step, "route": route_step, "check": check_step, "python": python_step}
FIXED = {"recall": model.recall_memories, "compact": model.compact_context, "llm_call": _llm_call, "tool_node": tool_node}


def counted(step: dict, function):
    """A step that keeps count of itself and of the reply's model calls, and says what it did"""

    step_id, kind = step["id"], step["type"]

    def run(state: dict, config: RunnableConfig):
        visits = dict(state.get("visits") or {})
        update = dict(function(state, config) or {})
        visits[step_id] = visits.get(step_id, 0) + 1
        said = update.get("step")
        if kind == "llm_call" or (said or {}).get("model_call"):
            visits[MODEL_CALLS] = visits.get(MODEL_CALLS, 0) + 1
        update["visits"] = visits
        if said is not None:
            update["step"] = {"id": step_id, "type": kind, "label": step.get("label") or engine.STEPS[kind]["label"], **said}
        return update

    return run


def compile_loop(loop: dict):
    """The graph of a checked loop"""

    builder = StateGraph(MessagesState)
    for step in loop["nodes"]:
        function = FIXED.get(step["type"]) or BUILDERS[step["type"]](step)
        builder.add_node(step["id"], counted(step, function))
    ways: dict[str, dict] = {}
    for edge in loop["edges"]:
        target = END if edge["target"] == engine.END else edge["target"]
        if edge["source"] == engine.START:
            builder.add_edge(START, target)
        else:
            ways.setdefault(edge["source"], {})[edge.get("handle")] = target
    for source, targets in ways.items():
        if list(targets) == [None]:
            builder.add_edge(source, targets[None])
        elif source == "llm_call":
            # A reply that ended too early goes round again the way tool results do: check_loop
            # makes that lead back to the model
            again = next((e["target"] for e in loop["edges"] if e["source"] == "tool_node"), "llm_call")

            # As end_logic: the list can be empty after the terminal takes back the only message
            def by_reply(state: dict, targets=targets, again=again):
                last = state["messages"][-1] if state["messages"] else None
                if getattr(last, "tool_calls", None):
                    return targets["tools"]
                return again if state.get("continuing") else targets["done"]
            builder.add_conditional_edges(source, by_reply, list(dict.fromkeys([*targets.values(), again])))
        else:
            def by_choice(state: dict, targets=targets):
                return targets.get(state.get("route")) or next(iter(targets.values()))
            builder.add_conditional_edges(source, by_choice, list(dict.fromkeys(targets.values())))
    return builder.compile(checkpointer=checkpointer)


def graph_for(config: dict | None):
    """The graph a run goes through: its engine's loop, or the usual one"""

    h = engine.of(config)
    if not h.loop:
        return chat_agent
    key = json.dumps({"nodes": h.loop["nodes"], "edges": h.loop["edges"]}, sort_keys=True)
    with _compiled_lock:
        if key not in _compiled:
            if len(_compiled) >= MAX_COMPILED:
                _compiled.pop(next(iter(_compiled)))
            _compiled[key] = compile_loop(h.loop)
        return _compiled[key]


def limits_for(config: dict, recursion_limit: int | None) -> tuple[int | None, int | None]:
    """A run's (graph step limit, model call cap). `recursion_limit` assumes 3 steps per model call,
    as the usual loop has; another loop has its own number of steps, and a cap of its own."""

    h = engine.of(config)
    if not h.loop:
        return recursion_limit, None
    cap = h.loop.get("max_model_calls", engine.MAX_MODEL_CALLS)
    if recursion_limit:
        cap = min(cap, max(1, (recursion_limit - 1) // 3))
    return 1 + (len(h.loop["nodes"]) + 1) * (cap + 1), cap
