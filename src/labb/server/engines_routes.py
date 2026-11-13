"""The engines endpoints (see engines.py): the page's list and editor, what the editor offers
and previews, the picker's choices, and what the terminal fetches."""

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from labb import engine, llm, model
from labb.server import engines
from labb.server.auth_routes import admin_user, current_user
from labb.server.studio import sandbox
from labb.tools import TOOLSETS, name_of

router = APIRouter()

# The system prompt's sections for the editor: which prompts each one's text comes from. The rest
# are written by people or found at run time, so an engine can move or turn them off but not edit them.
SECTIONS = [
    {"id": "base", "label": "Base", "prompts": ["base", "terminal"], "about": "Who the agent is. The second text is added in the terminal."},
    {"id": "agent", "label": "Agent", "prompts": [], "about": "The instructions of the agent you're chatting with."},
    {"id": "task", "label": "Task", "prompts": ["task"], "about": "When it runs on its own on a schedule."},
    {"id": "loop", "label": "Loop", "prompts": [], "about": "When the terminal's /loop runs a prompt again and again."},
    {"id": "web_off", "label": "Web off", "prompts": ["web_off"], "about": "When the message box's Web toggle is off."},
    {"id": "answer_mode", "label": "Answer mode", "prompts": ["mode_fast", "mode_thinking"], "about": "For the Fast and Thinking answer modes."},
    {"id": "deferred_tools", "label": "Tools to load", "prompts": [], "about": "When the tools would take too much of the window: the ones that wait for find_tools."},
    {"id": "skills", "label": "Skills", "prompts": [], "about": "The list of skills it can load."},
    {"id": "memories", "label": "Pinned memories", "prompts": [], "about": "What it remembers about you, always."},
    {"id": "how_to_work", "label": "How to work", "prompts": ["coding"], "about": "In the terminal: how it goes about a request."},
    {"id": "git", "label": "Git", "prompts": [], "about": "In the terminal: the branch and status when the session started."},
    {"id": "plan_mode", "label": "Plan mode", "prompts": ["plan_mode"], "about": "In the terminal's plan mode. Commands that change something are refused either way."},
    {"id": "tool_rules", "label": "Tool rules", "prompts": ["tool_rules"], "about": "In the terminal, when some tools are off."},
    {"id": "instructions", "label": "Project instructions", "prompts": [], "about": "In the terminal: AGENTS.md and CLAUDE.md files."},
]
OTHER_PROMPTS = [
    {"key": "summary", "label": "Summary", "about": "How the web chat's older turns get summarized."},
    {"key": "work_summary", "label": "Summary with a job in hand", "about": "The same, while the chat has open todos or a goal."},
    {"key": "coding_summary", "label": "Summary in the terminal", "about": "The same for coding sessions."},
    {"key": "title", "label": "Chat title", "about": "How a new chat gets its name."},
    {"key": "continue_truncated", "label": "Going on: cut off", "about": "Told to the model when its reply was cut off for being too long."},
    {"key": "continue_truncated_tool", "label": "Going on: cut-off tool call", "about": "The same, when a tool call was cut off and dropped."},
    {"key": "continue_empty", "label": "Going on: empty reply", "about": "Told to the model when it replied with nothing."},
    {"key": "continue_announced", "label": "Going on: said but not done", "about": "When it said what it'd do next without doing it."},
    {"key": "continue_open_steps", "label": "Going on: open steps", "about": "When it ended with steps of its todo list still open. {{steps}} lists them."},
    {"key": "continue_loop", "label": "Going on: loop not scheduled", "about": "When a self-paced loop's run said neither when to run again nor to stop."},
    {"key": "continue_goal", "label": "Going on: goal not done", "about": "Each round of a goal. {{objective}} is the goal, {{usage}} its budget used."},
    {"key": "continue_goal_budget", "label": "Goal out of budget", "about": "When a goal used its budget: wrap up. {{usage}} is what it used."},
    {"key": "continue_audit", "label": "Check before done", "about": "Before the final report, when Check before done is on."},
    {"key": "context_flush", "label": "Before a summary", "about": "Once before older turns are summarized, to save notes and update the todos first."},
]
# How many times per reply the model is told to go on (engine.CONTINUE)
CONTINUE_FIELDS = [
    {"key": "truncated", "label": "Cut off", "about": "Its reply hit the model's length limit."},
    {"key": "empty", "label": "Empty", "about": "It replied with nothing, not even a tool call."},
    {"key": "announced", "label": "Said but not done", "about": "It said what it'd do next and stopped."},
    {"key": "open_steps", "label": "Open steps", "about": "It ended with todo steps still open (a task gets 4)."},
    {"key": "loop_unscheduled", "label": "Loop not scheduled", "about": "A self-paced loop's run said neither when to run again nor to stop."},
    {"key": "goal", "label": "Goal not done", "about": "Rounds per reply while a goal is active (a task gets 50)."},
    {"key": "goal_budget", "label": "Goal out of budget", "about": "A round to wrap up when a goal used its budget."},
    {"key": "hook", "label": "Stop hook said no", "about": "Terminal: a Stop hook said the work isn't done."},
    {"key": "audit", "label": "Check before done", "about": "A round to check the evidence before the final report. Off by default."},
]
CONTEXT_FIELDS = [
    {"key": "compact_at", "label": "Summarize at", "about": "How full the window gets before older turns are summarized.", "unit": "share"},
    {"key": "keep_recent", "label": "Keep after a summary", "about": "How much of the window the recent turns may keep. Up to Summarize at.", "unit": "share"},
    {"key": "keep_tool_results", "label": "Large tool results kept", "about": "Older ones are replaced with a note the agent can read back."},
    {"key": "clear_at", "label": "Clear old results at", "about": "Old results are cleared in one go once those in full take this much of the window, so the prompt cache holds in between.", "unit": "share"},
    {"key": "max_result_share", "label": "Largest tool result", "about": "No one result takes more of the window; the agent reads on with look_back.", "unit": "share"},
    {"key": "user_words", "label": "Your words after a summary", "about": "Your messages from the summarized part, kept word for word, up to this much of the window.", "unit": "share"},
    {"key": "summary_share", "label": "Summary length", "about": "How long a summary may be (Ollama models).", "unit": "share"},
    {"key": "tool_share", "label": "Tools loaded up front", "about": "Over this much of the window, tools that aren't core wait to be found with find_tools.", "unit": "share"},
    {"key": "min_cleared_chars", "label": "Large means over", "about": "Shorter tool results are never cleared.", "unit": "characters"},
    {"key": "max_scale", "label": "Most token correction", "about": "How far the real token count may scale up the estimate."},
    {"key": "recall_query_chars", "label": "Searched for memories", "about": "How much of your message memories are looked up with.", "unit": "characters"},
    {"key": "max_instruction_chars", "label": "Project instructions cut at", "about": "Per AGENTS.md or CLAUDE.md file.", "unit": "characters"},
    {"key": "max_instruction_share", "label": "Project instructions share", "about": "Per file, at most this much of the window; the agent is told where the rest is.", "unit": "share"},
]
SWITCHES = [
    {"section": "input", "key": "recalled", "label": "Recalled memories", "about": "Memories related to your message are added to it, for the model only."},
    {"section": "input", "key": "work_reminder", "label": "Work reminder", "about": "The goal, the open todo list, the approved plan and the notes' topics at the end of each call."},
    {"section": "input", "key": "context_note", "label": "Context gauge", "about": "Tells the agent how full its context is, once it's two thirds of the way to a summary."},
    {"section": "input", "key": "flush", "label": "Save before a summary", "about": "Gives the agent one round to save notes and update its todos before older turns are summarized."},
    {"section": "repair", "key": "fix", "label": "Fix tool calls", "about": "Repairs wrong tool names and arguments. Off shows how a model does by itself."},
    {"section": "repair", "key": "constrained", "label": "Rewrite broken calls", "about": "Asks again with constrained decoding, where the provider can (Ollama)."},
    {"section": "repair", "key": "strict", "label": "Strict tools", "about": "Uses the provider's strict mode, where there's one."},
]


def surface_configurable(user_id: str, surface: str) -> dict:
    """A run of this kind, for a preview. The server has no project, so the terminal's get a made-up one."""

    configurable = {"user_id": user_id, "thread_id": "preview", "preview": True}
    if surface in ("terminal", "plan"):
        configurable |= {"workdir": "/your/project", "git_context": "(the branch and status of your repository)"}
    if surface == "plan":
        configurable["mode"] = "plan"
    if surface == "task":
        configurable["task"] = {"thread_id": "preview"}
    return configurable


def tools_of_toolset(toolset) -> list[dict]:
    found = []
    for tool in toolset.TOOLS:
        schema = tool.args_schema.model_json_schema() if hasattr(tool.args_schema, "model_json_schema") else {}
        params = [name for name in schema.get("properties", {}) if name != "config"]
        found.append({"name": tool.name, "description": tool.description, "params": params})
    return found


class Fields(BaseModel):
    name: str | None = None
    description: str | None = None
    shared: bool | None = None
    overlay: dict | None = None
    note: str | None = None


class FromOverlay(BaseModel):
    overlay: dict


class Preview(BaseModel):
    overlay: dict
    surface: str = "web"
    model: str | None = None


class Suggest(BaseModel):
    overlay: dict
    ask: str
    model: str | None = None


class Choice(BaseModel):
    engine_id: str | None = None


class PromptSet(BaseModel):
    name: str
    prompts: list[str]


def _or_400(action):
    try:
        return action()
    except engines.EngineError as e:
        raise HTTPException(400, str(e))


@router.get("/engines")
def list_engines(user: dict = Depends(current_user)):
    return {"engines": engines.list_for(user["id"]), "my_default": engines.my_default(user["id"]), "server_default": engines.server_default()}


@router.get("/engines/catalog")
def catalog(user: dict = Depends(current_user)):
    """All the editor needs to draw itself: what can be changed, and what the defaults are"""

    return {
        "prompts": engine.PROMPTS, "context": {**engine.CONTEXT, "instruction_files": list(engine.INSTRUCTION_FILES)},
        "ranges": {key: {"min": low, "max": high, "whole": whole} for key, (low, high, whole) in engine.RANGES.items()},
        "input": engine.INPUT, "repair": engine.REPAIR,
        "continue": engine.CONTINUE, "continue_fields": CONTINUE_FIELDS, "max_continue": engine.MAX_CONTINUE, "sections": SECTIONS, "other_prompts": OTHER_PROMPTS,
        "context_fields": CONTEXT_FIELDS, "switches": SWITCHES, "when": list(engine.WHEN),
        "toolsets": [{"name": name_of(t), "label": getattr(t, "LABEL", name_of(t)), "prompt": getattr(t, "PROMPT", "") or "",
                      "terminal_only": bool(getattr(t, "NEEDS_WORKDIR", False)), "tools": tools_of_toolset(t)} for t in TOOLSETS],
        "steps": {kind: {key: info.get(key) for key in ("label", "ways", "about", "fixed", "required", "terminal_only", "runs_python")}
                  for kind, info in engine.STEPS.items()},
        "route_tests": engine.ROUTE_TESTS, "default_loop": engine.DEFAULT_LOOP, "presets": engine.PRESETS,
        "max_model_calls": engine.MAX_MODEL_CALLS, "tool_kinds": engine.TOOL_KINDS, "param_types": list(engine.PARAM_TYPES),
        "hook_events": list(engine.HOOK_EVENTS), "sandbox": sandbox.available(),
    }


@router.get("/engines/for-terminal")
def for_terminal(user: dict = Depends(current_user)):
    return engines.for_terminal(user["id"])


@router.get("/engines/chat/{thread_id}")
def of_chat(thread_id: str, user: dict = Depends(current_user)):
    """The engine kept for a chat of yours"""
    from labb import chats

    exists, owner = chats.owner(thread_id)
    if not exists or owner != user["id"]:
        raise HTTPException(404, "No such chat")
    return {"engine_id": engines.of_chat(thread_id)}


@router.post("/engines/check")
def check(body: FromOverlay, user: dict = Depends(current_user)):
    """What's wrong with a draft, and what it changes; on every edit"""

    problems = engine.check(body.overlay)
    return {"problems": problems, "web_problems": [] if problems else engines.web_problems(body.overlay),
            "changes": [] if problems else [{"where": w, "before": a, "after": b} for w, a, b in engine.changes(body.overlay)]}


@router.post("/engines/preview")
def preview(body: Preview, user: dict = Depends(current_user)):
    """What the model would get with this draft, part by part with estimated tokens"""

    if body.surface not in ("web", "terminal", "plan", "task"):
        raise HTTPException(400, "Preview web, terminal, plan or task")
    if problems := engine.check(body.overlay):
        return {"problems": problems, "seen": None}
    configurable = surface_configurable(user["id"], body.surface) | {"engine": engine.resolve(body.overlay, "draft")}
    if body.model:
        configurable["model"] = body.model
    return {"problems": [], "seen": model.inspect_context({}, {"configurable": configurable})}


SUGGEST_PROMPT = """\
You help change an engine: a JSON overlay on an AI agent's defaults, holding only what differs. Its sections, all optional:
prompts (texts by key: {prompts}); tool_prompts (toolset name to text); sections (off: section ids to leave out, order: ids to \
come first, add: [{{id, label, text, when}}], when being any of {when}; ids: {sections}, tools:<toolset>); context ({context}); \
input (recalled, work_reminder: booleans); tools (allow, deny: tool or toolset names; descriptions: tool name to text); \
repair (fix, constrained, strict: booleans); continue (truncated, empty, announced, loop_unscheduled: how many \
times per reply the model is told to go on, 0 to {max_continue}); model (model, summary_model, answer_mode: fast, auto or thinking); \
loop ({{nodes, edges, max_model_calls}}: keep it as it is unless asked).
Change only what's asked and keep the rest. Reply with the whole new overlay as JSON and nothing else.

The current overlay:
{overlay}

The change wanted:
{ask}"""


@router.post("/engines/suggest")
def suggest(body: Suggest, user: dict = Depends(current_user)):
    """A change in words, as a new overlay to preview. Nothing is saved."""

    ask = body.ask.strip()
    if not ask:
        raise HTTPException(400, "Say what to change")
    prompt = SUGGEST_PROMPT.format(prompts=", ".join(engine.PROMPTS), when=", ".join(engine.WHEN), sections=", ".join(engine.SECTIONS),
                                   context=", ".join(engine.CONTEXT), max_continue=engine.MAX_CONTINUE, overlay=json.dumps(body.overlay, ensure_ascii=False), ask=ask[:2000])
    configurable = {"user_id": user["id"], **({"model": body.model} if body.model else {})}
    try:
        text = llm.for_config({"configurable": configurable}).model.invoke(prompt).text
    except Exception as e:
        raise HTTPException(502, f"The model couldn't answer: {e}")
    found = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        overlay = json.loads(found.group(0)) if found else None
    except ValueError:
        overlay = None
    if not isinstance(overlay, dict):
        raise HTTPException(422, "The model didn't answer with an engine. Try saying it differently.")
    problems = engine.check(overlay)
    return {"overlay": overlay, "problems": problems,
            "changes": [] if problems else [{"where": w, "before": a, "after": b} for w, a, b in engine.changes(overlay, body.overlay)]}


@router.put("/engines/my-default")
def set_my_default(body: Choice, user: dict = Depends(current_user)):
    _or_400(lambda: engines.set_my_default(user["id"], body.engine_id))
    return {"my_default": body.engine_id}


@router.put("/admin/engine-default")
def set_server_default(body: Choice, user: dict = Depends(admin_user)):
    _or_400(lambda: engines.set_server_default(user["id"], body.engine_id))
    return {"server_default": body.engine_id}


@router.get("/engines/prompt-sets")
def list_prompt_sets(user: dict = Depends(current_user)):
    return engines.prompt_sets(user["id"])


@router.post("/engines/prompt-sets", status_code=201)
def create_prompt_set(body: PromptSet, user: dict = Depends(current_user)):
    return _or_400(lambda: engines.save_prompt_set(user["id"], None, body.name, body.prompts))


@router.put("/engines/prompt-sets/{set_id}")
def update_prompt_set(set_id: str, body: PromptSet, user: dict = Depends(current_user)):
    return _or_400(lambda: engines.save_prompt_set(user["id"], set_id, body.name, body.prompts))


@router.delete("/engines/prompt-sets/{set_id}", status_code=204)
def delete_prompt_set(set_id: str, user: dict = Depends(current_user)):
    if not engines.delete_prompt_set(user["id"], set_id):
        raise HTTPException(404, "No such set")
    return Response(status_code=204)


@router.post("/engines", status_code=201)
def create(body: Fields, user: dict = Depends(current_user)):
    return _or_400(lambda: engines.create(user["id"], body.name, body.description or "", body.overlay or {}))


@router.get("/engines/{engine_id}")
def get(engine_id: str, user: dict = Depends(current_user)):
    if (found := engines.visible(engine_id, user["id"])) is None:
        raise HTTPException(404, "No such engine")
    return found


@router.patch("/engines/{engine_id}")
def update(engine_id: str, body: Fields, user: dict = Depends(current_user)):
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    found = _or_400(lambda: engines.update(user["id"], engine_id, fields))
    if found is None:
        raise HTTPException(404, "No engine of yours here")
    return found


@router.delete("/engines/{engine_id}", status_code=204)
def delete(engine_id: str, user: dict = Depends(current_user)):
    if not engines.delete(user["id"], engine_id):
        raise HTTPException(404, "No engine of yours here")
    return Response(status_code=204)


@router.post("/engines/{engine_id}/duplicate", status_code=201)
def duplicate(engine_id: str, user: dict = Depends(current_user)):
    found = _or_400(lambda: engines.duplicate(user["id"], engine_id))
    if found is None:
        raise HTTPException(404, "No such engine")
    return found


@router.get("/engines/{engine_id}/versions")
def versions(engine_id: str, user: dict = Depends(current_user)):
    found = engines.versions(user["id"], engine_id)
    if found is None:
        raise HTTPException(404, "No such engine")
    return found


@router.post("/engines/{engine_id}/versions/{version}/restore")
def restore(engine_id: str, version: int, user: dict = Depends(current_user)):
    found = _or_400(lambda: engines.restore(user["id"], engine_id, version))
    if found is None:
        raise HTTPException(404, "No engine of yours here")
    return found
