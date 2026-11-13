"""Agents working with others (tools/delegate.py): an agent asks the agents it was given, and
uses the actions it was given. Both run as whoever is chatting, with the agent's own owner's
choices: the agents and actions it lists are the ones its owner could see when they chose them.

An agent asked can ask others in turn, agent_ask_depth deep at most (Settings → Limits), so two agents can't keep asking
each other. Its conversation is thrown away after; its usage is counted (source "agent").
"""

import json
import threading
import uuid

from labb import chats
from labb.agent import chat_agent
from labb.runner import run_reply
from labb.server import agents
from labb.server.studio import actions
from labb.server.studio.steps import Step, StepError, ask_model
from labb.server import limits

MAX_REPLY = 20_000


def _me(config) -> dict:
    return ((config or {}).get("configurable") or {}).get("agent") or {}


def _pick(listed: list[dict], name: str, kind: str) -> dict | str:
    """The listed one with this name, or what to tell the model"""

    wanted = name.strip().lower()
    found = next((x for x in listed if x["name"].lower() == wanted), None)
    if found:
        return found
    names = ", ".join(x["name"] for x in listed) or "none"
    return f"There's no {kind} called {name!r} for you; you have: {names}."


def ask(config, name: str, message: str) -> str:
    configurable = (config or {}).get("configurable") or {}
    me = _me(config)
    depth = configurable.get("depth", 0)
    if depth >= limits.get("agent_ask_depth"):
        return "Agents asked by other agents can't ask further; answer with what you have."
    picked = _pick(me.get("delegates") or [], name, "agent")
    if isinstance(picked, str):
        return picked
    agent = agents.visible(picked["id"], me.get("owner_id", ""))
    if agent is None:
        return f"{picked['name']} isn't there any more, or isn't shared."
    thread_id = f"ask-{uuid.uuid4()}"
    run = {"user_id": configurable.get("user_id"), "agent": agents.for_run(agent), "depth": depth + 1,
           "task": {"id": thread_id, "name": me.get("name", "an agent")}, "workflow": configurable.get("workflow")}
    if agent["model"]:
        run["model"] = agent["model"]
    stop = configurable.get("stop_requested") or threading.Event()
    events = []
    try:
        run_reply(thread_id, message, stop, events.append, configurable=run, name=False,
                  recursion_limit=limits.recursion(limits.get("agent_step_model_calls")), source="agent")
        error = next((e["message"] for e in events if e["type"] == "error"), None)
        if error:
            return f"{agent['name']} couldn't answer: {error}"
        messages = chat_agent.get_state({"configurable": {"thread_id": thread_id}}).values.get("messages", [])
        reply = messages[-1].text if messages and messages[-1].type == "ai" else ""
        return (reply or f"{agent['name']} didn't answer.")[:MAX_REPLY]
    finally:
        chats.delete_chat(thread_id)


def act(config, name: str, inputs: dict) -> str:
    me = _me(config)
    picked = _pick(me.get("actions") or [], name, "action")
    if isinstance(picked, str):
        return picked
    action = actions.visible(picked["id"], me.get("owner_id", ""))
    if action is None:
        return f"{picked['name']} isn't there any more."
    return _run_action(action, inputs, config, me.get("owner_id", ""))


def engine_action(config, action_id: str, inputs: dict) -> str:
    """An engine's action tool: only an action whoever is chatting can use themselves"""

    user_id = ((config or {}).get("configurable") or {}).get("user_id") or ""
    action = actions.visible(action_id, user_id)
    if action is None:
        return "Error: that action isn't there, or isn't yours to use."
    return _run_action(action, inputs, config, user_id)


def _run_action(action: dict, inputs: dict, config, owner_id: str) -> str:
    configurable = (config or {}).get("configurable") or {}
    try:
        if action["kind"] == "http":
            output, _ = actions.run_http(action, inputs if isinstance(inputs, dict) else {})
            return json.dumps(output, ensure_ascii=False, default=str)[:MAX_REPLY]
        prompt = actions.render_prompt_config(action, inputs if isinstance(inputs, dict) else {})
        step = Step(f"agent-{uuid.uuid4().hex[:8]}", configurable.get("user_id") or owner_id, action["name"],
                    "action", configurable.get("stop_requested") or threading.Event())
        result = ask_model(prompt["prompt"], step, system=prompt["system"], model=prompt["model"],
                           fields=prompt["fields"] if prompt["format"] == "json" else None)
        output = result.output
        return (output if isinstance(output, str) else json.dumps(output, ensure_ascii=False))[:MAX_REPLY]
    except (actions.ActionError, StepError) as e:
        return f"{action['name']} failed: {e}"
