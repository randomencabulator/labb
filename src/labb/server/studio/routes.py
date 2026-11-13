"""Studio's endpoints: workflows (draft, publish, run), runs (watch, stop, approve, run again),
actions, secrets (admins), the step catalog and templates, and `/hooks/<token>` for webhooks.

Your workflows are yours; shared ones others can run and duplicate, not change. A run is seen by
whoever started it and the workflow's owner. HTTP actions and secrets are for admins to make.
"""

import asyncio
import json
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from labb.server import tasks
from labb.server.auth_routes import admin_user, current_user
from labb.server.studio import actions, assist, describe, engine, nodes, sandbox, secrets, store, templates, triggers
from labb.server.studio.steps import Step, StepError, ask_model
from labb.server import limits

router = APIRouter()

MAX_NAME = 80
MAX_DESCRIPTION = 300
MAX_DEFINITION = 500_000
MAX_WAIT_SECONDS = 30


class WorkflowFields(BaseModel):
    name: str | None = None
    emoji: str | None = None
    description: str | None = None
    draft: dict | None = None
    shared: bool | None = None
    enabled: bool | None = None
    # On create: start from one of the templates
    template: str | None = None


class RunRequest(BaseModel):
    inputs: dict = {}
    # Run the draft being edited instead of the published version
    draft: bool = False


class Decision(BaseModel):
    node_id: str
    approved: bool
    comment: str = ""


class Rerun(BaseModel):
    node_id: str


class ActionFields(BaseModel):
    kind: str | None = None
    name: str | None = None
    emoji: str | None = None
    description: str | None = None
    config: dict | None = None
    shared: bool | None = None


class ActionTest(BaseModel):
    args: dict = {}


class SecretValue(BaseModel):
    value: str


class DraftAnswer(BaseModel):
    question: str = ""
    answer: str = ""


class DraftRequest(BaseModel):
    """One turn of the drafting room: the description and answers so far, or the blueprint and a
    change to it. Nothing is saved."""

    description: str = ""
    answers: list[DraftAnswer] = []
    # Ask questions first (only before there are answers or a blueprint)
    ask: bool = True
    current: dict | None = None
    request: str | None = None
    model: str | None = None
    tz: str | None = None


class Suggest(BaseModel):
    prompt: str = ""
    # The draft as the builder has it (it may not be saved yet)
    definition: dict
    selected: list[str] = []
    # A failed run to fix
    run_id: str | None = None
    model: str | None = None
    tz: str | None = None


class Explain(BaseModel):
    definition: dict
    model: str | None = None


class TryRequest(BaseModel):
    name: str = ""
    definition: dict
    inputs: dict = {}


class Describe(BaseModel):
    description: str
    model: str | None = None
    # The browser's time zone, for a schedule the draft has
    tz: str | None = None


def _clean_fields(body: WorkflowFields) -> dict:
    sent = body.model_dump(exclude_unset=True)
    fields = {}
    if "name" in sent:
        name = (sent["name"] or "").strip()
        if not name or len(name) > MAX_NAME:
            raise HTTPException(400, f"A workflow needs a name of at most {MAX_NAME} characters")
        fields["name"] = name
    if "emoji" in sent:
        fields["emoji"] = (sent["emoji"] or "⚙️").strip()[:16] or "⚙️"
    if "description" in sent:
        fields["description"] = (sent["description"] or "").strip()[:MAX_DESCRIPTION]
    if "draft" in sent and sent["draft"] is not None:
        draft = sent["draft"]
        if len(json.dumps(draft)) > MAX_DEFINITION:
            raise HTTPException(400, "This workflow is too big")
        if not isinstance(draft.get("nodes", []), list) or not isinstance(draft.get("edges", []), list):
            raise HTTPException(400, "A workflow is a list of nodes and a list of edges")
        fields["draft"] = {"nodes": draft.get("nodes", []), "edges": draft.get("edges", [])}
    for flag in ("shared", "enabled"):
        if sent.get(flag) is not None:
            fields[flag] = sent[flag]
    return fields


def _full(workflow: dict, user_id: str) -> dict:
    """A workflow with what the builder shows: what's wrong with the draft, versions, schedule"""

    schedule, tz = triggers.schedule_of(workflow["draft"])
    when = None
    if schedule:
        try:
            when = tasks.describe(schedule)
        except Exception:
            when = None
    return {**workflow, "problems": nodes.check(workflow["draft"], engine.lookups(user_id)),
            "versions": store.list_versions(workflow["id"]), "schedule_text": when, "tz": tz}


def _summary(workflow: dict) -> dict:
    return {k: v for k, v in workflow.items() if k != "draft"} | {"steps": len(workflow["draft"].get("nodes", []))}


def _mine(workflow_id: str, user: dict) -> dict:
    workflow = store.get_workflow(workflow_id, user["id"])
    if workflow is None or not workflow["mine"]:
        raise HTTPException(404, "There's no such workflow of yours")
    return workflow


def _visible(workflow_id: str, user: dict) -> dict:
    workflow = store.visible_workflow(workflow_id, user["id"])
    if workflow is None:
        raise HTTPException(404, "There's no such workflow")
    return workflow


def _run_for(run_id: str, user: dict) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(404, "There's no such run")
    workflow = store.get_workflow(run["workflow_id"])
    if run["user_id"] != user["id"] and (workflow is None or workflow["owner_id"] != user["id"]):
        raise HTTPException(404, "There's no such run")
    return run


# --- Catalog and templates -----------------------------------------------------------

@router.get("/studio/catalog")
def catalog(user: dict = Depends(current_user)):
    """The kinds of steps and their settings, for the builder's palette and forms"""

    from labb.tools import tools_by_name

    return {
        "nodes": nodes.CATALOG, "categories": nodes.CATEGORIES,
        "tools": [{"name": n, "description": (tools_by_name[n].description or "").split("\n")[0],
                   "args": [a for a in tools_by_name[n].args if a != "config"]} for n in nodes.TOOLS],
        "events": [{"name": k, "label": v} for k, v in nodes.EVENTS.items()],
        "operators": [{"name": k, "label": v} for k, v in nodes.OPERATORS.items()],
        "input_types": nodes.INPUT_TYPES, "schedule_kinds": tasks.KINDS, "sandbox": sandbox.available(),
        "is_admin": user["role"] == "admin",
    }


@router.get("/studio/templates")
def list_templates(user: dict = Depends(current_user)):
    return templates.listing()


@router.post("/studio/describe")
def describe_workflow(body: Describe, user: dict = Depends(current_user)):
    """A model drafts a workflow from a description, to adjust in the builder"""

    try:
        return describe.draft(body.description, user, body.model, body.tz)
    except describe.DescribeError as e:
        raise HTTPException(400, str(e))


def _definition(raw: dict) -> dict:
    if len(json.dumps(raw)) > MAX_DEFINITION:
        raise HTTPException(400, "This workflow is too big")
    if not isinstance(raw.get("nodes", []), list) or not isinstance(raw.get("edges", []), list):
        raise HTTPException(400, "A workflow is a list of nodes and a list of edges")
    return {"nodes": raw.get("nodes", []), "edges": raw.get("edges", [])}


@router.post("/studio/drafts")
def draft_turn(body: DraftRequest, user: dict = Depends(current_user)):
    """The drafting room: questions about a description, a blueprint, or the blueprint changed as asked"""

    try:
        if body.current is not None:
            changed = assist.suggest(_definition(body.current), body.request or "", user, model=body.model, tz=body.tz)
            return {"kind": "blueprint", **changed}
        if body.ask and not body.answers:
            description = body.description.strip()
            if not description:
                raise HTTPException(400, "Say what the workflow should do")
            if found := assist.questions(description[:describe.MAX_DESCRIPTION], user, body.model, body.tz):
                return {"kind": "questions", "questions": found}
        drafted = describe.draft(assist.with_answers(body.description.strip(), [a.model_dump() for a in body.answers]),
                                 user, body.model, body.tz)
        return {"kind": "blueprint", **drafted, "changes": [], "reply": ""}
    except (describe.DescribeError, assist.AssistError) as e:
        raise HTTPException(400, str(e))


@router.post("/studio/explain")
def explain_workflow(body: Explain, user: dict = Depends(current_user)):
    try:
        return {"text": assist.explain(_definition(body.definition), user, body.model)}
    except assist.AssistError as e:
        raise HTTPException(400, str(e))


TRY_PREFIX = "try-"


@router.post("/studio/try", status_code=202)
def try_draft(body: TryRequest, user: dict = Depends(current_user)):
    """Run a workflow that isn't saved yet, once (the drafting room's Try it). Its runs aren't in the
    Runs list; the next try deletes the ones before that have ended."""

    workflow_id = TRY_PREFIX + user["id"]
    ended = [r["id"] for r in store.list_runs(workflow_id) if r["status"] in ("done", "failed", "stopped")]
    if ended:
        store.delete_runs(ended)
    workflow = {"id": workflow_id, "name": body.name.strip()[:MAX_NAME] or "The draft",
                "draft": _definition(body.definition), "published_version": None}
    try:
        run_id = engine.start(workflow, user["id"], trigger="test", inputs=body.inputs, draft=True)
    except engine.RunError as e:
        raise HTTPException(400, str(e))
    return engine.view(store.get_run(run_id))


# --- Workflows -----------------------------------------------------------------------

@router.get("/studio/workflows")
def list_workflows(user: dict = Depends(current_user)):
    return [_summary(w) for w in store.list_workflows(user["id"])]


@router.post("/studio/workflows", status_code=201)
def create_workflow(body: WorkflowFields, user: dict = Depends(current_user)):
    if store.count_workflows(user["id"]) >= (most := limits.get("workflows_per_person")):
        raise HTTPException(400, f"You have {most} workflows already; delete one first")
    template = templates.get(body.template) if body.template else None
    if body.template and template is None:
        raise HTTPException(400, "There's no such template")
    fields = _clean_fields(body)
    draft = fields.get("draft") or (template["definition"] if template else templates.blank())
    workflow_id = store.insert_workflow(
        user["id"], fields.get("name") or (template["name"] if template else "New workflow"),
        fields.get("emoji") or (template["emoji"] if template else "⚙️"),
        fields.get("description", template["description"] if template else ""), draft, bool(fields.get("shared")))
    return _full(store.get_workflow(workflow_id, user["id"]), user["id"])


@router.get("/studio/workflows/{workflow_id}")
def get_workflow(workflow_id: str, user: dict = Depends(current_user)):
    return _full(_visible(workflow_id, user), user["id"])


@router.patch("/studio/workflows/{workflow_id}")
def update_workflow(workflow_id: str, body: WorkflowFields, user: dict = Depends(current_user)):
    _mine(workflow_id, user)
    fields = _clean_fields(body)
    if fields:
        store.update_workflow(workflow_id, **fields)
    if "enabled" in fields:
        triggers.plan(workflow_id)
    return _full(store.get_workflow(workflow_id, user["id"]), user["id"])


@router.delete("/studio/workflows/{workflow_id}", status_code=204)
def delete_workflow(workflow_id: str, user: dict = Depends(current_user)):
    _mine(workflow_id, user)
    for run_id in store.delete_workflow(workflow_id):
        engine.stop(run_id)
    return Response(status_code=204)


@router.post("/studio/workflows/{workflow_id}/duplicate", status_code=201)
def duplicate_workflow(workflow_id: str, user: dict = Depends(current_user)):
    workflow = _visible(workflow_id, user)
    if store.count_workflows(user["id"]) >= (most := limits.get("workflows_per_person")):
        raise HTTPException(400, f"You have {most} workflows already; delete one first")
    new_id = store.insert_workflow(user["id"], f"{workflow['name']} (copy)"[:MAX_NAME], workflow["emoji"],
                                   workflow["description"], workflow["draft"], False)
    return _full(store.get_workflow(new_id, user["id"]), user["id"])


@router.post("/studio/workflows/{workflow_id}/publish")
def publish(workflow_id: str, user: dict = Depends(current_user)):
    """Make the draft the version that schedules, webhooks, events and the terminal run"""

    workflow = _mine(workflow_id, user)
    problems = nodes.check(workflow["draft"], engine.lookups(user["id"]))
    if problems:
        raise HTTPException(400, f"Fix this first: {problems[0]['message']}")
    try:
        definition = triggers.clean_schedule(workflow["draft"], tasks.utcnow())
    except tasks.TaskError as e:
        raise HTTPException(400, f"The schedule: {e}")
    store.add_version(workflow_id, definition)
    store.update_workflow(workflow_id, draft=definition)
    triggers.plan(workflow_id)
    return _full(store.get_workflow(workflow_id, user["id"]), user["id"])


@router.post("/studio/workflows/{workflow_id}/webhook")
def webhook(workflow_id: str, on: bool = True, user: dict = Depends(current_user)):
    """Turn the webhook on (a new secret URL, shown once) or off"""

    _mine(workflow_id, user)
    token = triggers.set_webhook(workflow_id, on)
    return {"on": on, "path": f"/hooks/{token}" if token else None}


@router.get("/studio/workflows/{workflow_id}/export")
def export_workflow(workflow_id: str, user: dict = Depends(current_user)):
    workflow = _visible(workflow_id, user)
    data = {"labb_workflow": 1, "name": workflow["name"], "emoji": workflow["emoji"],
            "description": workflow["description"], "definition": workflow["draft"]}
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{workflow["name"]}.json"'})


@router.post("/studio/import", status_code=201)
def import_workflow(data: dict, user: dict = Depends(current_user)):
    if not isinstance(data.get("definition"), dict):
        raise HTTPException(400, "That isn't a labb workflow (it has no definition)")
    return create_workflow(WorkflowFields(name=str(data.get("name") or "Imported workflow")[:MAX_NAME],
                                          emoji=data.get("emoji"), description=data.get("description"),
                                          draft=data["definition"]), user)


# --- Runs ----------------------------------------------------------------------------

@router.post("/studio/workflows/{workflow_id}/runs", status_code=202)
def start_run(workflow_id: str, body: RunRequest, user: dict = Depends(current_user)):
    workflow = _mine(workflow_id, user) if body.draft else _visible(workflow_id, user)
    try:
        run_id = engine.start(workflow, user["id"], trigger="test" if body.draft else "manual",
                              inputs=body.inputs, draft=body.draft)
    except engine.RunError as e:
        raise HTTPException(400, str(e))
    return engine.view(store.get_run(run_id))


@router.post("/studio/workflows/{workflow_id}/suggest")
def suggest_change(workflow_id: str, body: Suggest, user: dict = Depends(current_user)):
    """The draft changed as asked, or fixed for a failed run, to preview in the builder (not saved)"""

    _mine(workflow_id, user)
    failure = None
    if body.run_id:
        run = _run_for(body.run_id, user)
        if run["workflow_id"] != workflow_id or run["status"] != "failed":
            raise HTTPException(400, "Only a failed run of this workflow can be fixed")
        failure = assist.failure_of(run, store.steps(run["id"]))
    try:
        return assist.suggest(_definition(body.definition), body.prompt, user, model=body.model, tz=body.tz,
                              selected=body.selected, failure=failure)
    except assist.AssistError as e:
        raise HTTPException(400, str(e))


@router.get("/studio/workflows/{workflow_id}/runs")
def workflow_runs(workflow_id: str, user: dict = Depends(current_user)):
    workflow = _visible(workflow_id, user)
    mine = None if workflow["mine"] else user["id"]
    return [{k: v for k, v in run.items() if k != "definition"} for run in store.list_runs(workflow_id, mine)]


@router.get("/studio/runs")
def my_runs(user: dict = Depends(current_user)):
    names = {w["id"]: w for w in store.list_workflows(user["id"])}
    return [{**{k: v for k, v in run.items() if k != "definition"},
             "workflow": names.get(run["workflow_id"], {}).get("name"), "emoji": names.get(run["workflow_id"], {}).get("emoji")}
            for run in store.list_runs(user_id=user["id"]) if not run["workflow_id"].startswith(TRY_PREFIX)]


@router.get("/studio/runs/{run_id}")
def get_run(run_id: str, user: dict = Depends(current_user)):
    return engine.view(_run_for(run_id, user))


@router.post("/studio/runs/{run_id}/stop")
def stop_run(run_id: str, user: dict = Depends(current_user)):
    _run_for(run_id, user)
    return {"stopped": engine.stop(run_id)}


@router.post("/studio/runs/{run_id}/approve")
def approve(run_id: str, body: Decision, user: dict = Depends(current_user)):
    _run_for(run_id, user)
    try:
        return engine.view(engine.decide(run_id, body.node_id, body.approved, user["username"], body.comment.strip()[:1000]))
    except engine.RunError as e:
        raise HTTPException(400, str(e))


@router.post("/studio/runs/{run_id}/rerun", status_code=202)
def rerun(run_id: str, body: Rerun, user: dict = Depends(current_user)):
    _run_for(run_id, user)
    try:
        return engine.view(store.get_run(engine.rerun(run_id, body.node_id, user["id"])))
    except engine.RunError as e:
        raise HTTPException(400, str(e))


@router.get("/studio/waiting")
def waiting(user: dict = Depends(current_user)):
    """Approvals waiting for you: in your runs, or runs of your workflows"""

    found = []
    for run in store.runs_in(("waiting",)):
        if run["workflow_id"].startswith(TRY_PREFIX):
            continue
        workflow = store.get_workflow(run["workflow_id"])
        if run["user_id"] != user["id"] and (workflow is None or workflow["owner_id"] != user["id"]):
            continue
        by_id = nodes.index(run["definition"])[0]
        for step in store.steps(run["id"]):
            node = by_id.get(step["node_id"]) or {}
            if step["status"] == "waiting" and node.get("type") == "approval":
                found.append({"run_id": run["id"], "workflow_id": run["workflow_id"],
                              "workflow": workflow["name"] if workflow else "", "node_id": step["node_id"],
                              "label": node.get("label") or step["key"], "message": (step["input"] or {}).get("message", ""),
                              "since": step["started_at"]})
    return found


# --- Actions and secrets -------------------------------------------------------------

@router.get("/studio/actions")
def list_actions(user: dict = Depends(current_user)):
    admin = user["role"] == "admin"
    return [actions.for_listing(a, admin) for a in store.list_actions(user["id"])]


@router.post("/studio/actions", status_code=201)
def create_action(body: ActionFields, user: dict = Depends(current_user)):
    if store.count_actions() >= (most := limits.get("actions_total")):
        raise HTTPException(400, f"The server has {most} actions already")
    try:
        fields = actions.clean(body.model_dump(), user["role"] == "admin")
    except actions.ActionError as e:
        raise HTTPException(400, str(e))
    action_id = store.insert_action(user["id"], fields["kind"], fields["name"], fields["emoji"], fields["description"],
                                    fields["config"], fields["shared"])
    return store.get_action(action_id, user["id"])


def _editable_action(action_id: str, user: dict) -> dict:
    action = store.get_action(action_id, user["id"])
    admin = user["role"] == "admin"
    if action is None or (action["kind"] == "http" and not admin) or (action["kind"] == "prompt" and not action["mine"]):
        raise HTTPException(404, "There's no such action you can change")
    return action


@router.patch("/studio/actions/{action_id}")
def update_action(action_id: str, body: ActionFields, user: dict = Depends(current_user)):
    action = _editable_action(action_id, user)
    merged = {**{k: action[k] for k in ("kind", "name", "emoji", "description", "config", "shared")},
              **body.model_dump(exclude_unset=True), "kind": action["kind"]}
    try:
        fields = actions.clean(merged, user["role"] == "admin")
    except actions.ActionError as e:
        raise HTTPException(400, str(e))
    store.update_action(action_id, **{k: fields[k] for k in ("name", "emoji", "description", "config", "shared")})
    return store.get_action(action_id, user["id"])


@router.delete("/studio/actions/{action_id}", status_code=204)
def delete_action(action_id: str, user: dict = Depends(current_user)):
    _editable_action(action_id, user)
    store.delete_action(action_id)
    return Response(status_code=204)


@router.post("/studio/actions/{action_id}/test")
def test_action(action_id: str, body: ActionTest, user: dict = Depends(current_user)):
    """Run an action once with these inputs, to try it"""

    action = _editable_action(action_id, user)
    try:
        if action["kind"] == "http":
            output, request = actions.run_http(action, body.args)
            return {"ok": True, "output": output, "request": request}
        config = actions.render_prompt_config(action, body.args)
        step = Step("test", user["id"], action["name"], "test", threading.Event())
        result = ask_model(config["prompt"], step, system=config["system"], model=config["model"],
                           fields=config["fields"] if config["format"] == "json" else None)
        return {"ok": True, "output": result.output}
    except (actions.ActionError, StepError) as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.get("/studio/secrets")
def list_secrets(user: dict = Depends(admin_user)):
    """The secrets' names; values are never shown"""
    return store.list_secret_names()


@router.put("/studio/secrets/{name}")
def put_secret(name: str, body: SecretValue, user: dict = Depends(admin_user)):
    try:
        secrets.put(name, body.value, user["id"])
    except secrets.SecretError as e:
        raise HTTPException(400, str(e))
    return store.list_secret_names()


@router.delete("/studio/secrets/{name}", status_code=204)
def delete_secret(name: str, user: dict = Depends(admin_user)):
    if not store.delete_secret(name):
        raise HTTPException(404, "There's no such secret")
    return Response(status_code=204)


# --- Webhooks ------------------------------------------------------------------------

@router.post("/hooks/{token}")
async def hook(token: str, request: Request, wait: int = 0):
    """Start a workflow from outside. With ?wait=N (at most 30 seconds), answer with its result
    if it ends by then. No login: the secret URL is the key."""

    raw = await request.body()
    try:
        body = json.loads(raw) if raw.strip() else {}
    except ValueError:
        body = {"text": raw.decode(errors="replace")}
    try:
        run_id = await asyncio.to_thread(triggers.fire_webhook, token, body)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except engine.RunError as e:
        raise HTTPException(400, str(e))
    deadline = time.monotonic() + max(0, min(wait, MAX_WAIT_SECONDS))
    run = store.get_run(run_id)
    while run["status"] in ("queued", "running") and time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        run = store.get_run(run_id)
    ended = run["status"] in ("done", "failed", "stopped")
    return JSONResponse({"run_id": run_id, "status": run["status"], "result": run["result"], "error": run["error"]},
                        status_code=200 if ended else 202)
