"""The tasks endpoints (see tasks.py): your tasks, their runs, running one now and stopping it.
Nobody else sees them, admins included."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from labb.server.auth_routes import current_user
from labb.server import heartbeat, tasks

router = APIRouter()


class TaskFields(BaseModel):
    """What the editor sends; on a change, only the fields it sends change"""

    name: str | None = None
    prompt: str | None = None
    # Empty for labb
    agent_id: str | None = None
    # See tasks.clean_schedule
    schedule: dict | None = None
    # The browser's time zone, e.g. "Europe/Vienna"
    tz: str | None = None
    # Each run in a "new" chat, or always the "same" one
    mode: Literal["new", "same"] | None = None
    enabled: bool | None = None
    # Keep going until done: each run is a goal (tools/goal.py)
    goal: bool | None = None


class PreviewRequest(BaseModel):
    schedule: dict
    tz: str = "UTC"


def fields(body: TaskFields) -> dict:
    sent = body.model_dump(exclude_unset=True)
    return {k: v for k, v in sent.items() if v is not None or k == "agent_id"}


def bad(e: tasks.TaskError):
    return HTTPException(400, str(e))


@router.get("/tasks")
def list_tasks(user: dict = Depends(current_user)):
    """Your tasks, each with its schedule in words (`when`), next run and last run"""
    return tasks.list_for(user["id"])


@router.post("/tasks/preview")
def preview(request: PreviewRequest, user: dict = Depends(current_user)):
    """A schedule in words and its next 3 times, for the editor"""
    try:
        return tasks.preview(request.schedule, request.tz)
    except tasks.TaskError as e:
        raise bad(e)


@router.post("/tasks", status_code=201)
def create_task(body: TaskFields, user: dict = Depends(current_user)):
    try:
        return tasks.create(user["id"], fields(body))
    except tasks.TaskError as e:
        raise bad(e)


# The heartbeat (heartbeat.py): before /tasks/{task_id}, so "heartbeat" isn't taken for a task's id

class HeartbeatFields(BaseModel):
    enabled: bool | None = None
    every_minutes: int | None = None
    # Empty for labb
    agent_id: str | None = None
    per_beat: int | None = None


class TodoFields(BaseModel):
    text: str | None = None
    # Put a done or failed one back on the list
    reopen: bool = False


def _heartbeat(work):
    try:
        return work()
    except heartbeat.HeartbeatError as e:
        raise HTTPException(400, str(e))
    except tasks.TaskError as e:
        raise bad(e)


@router.get("/tasks/heartbeat")
def get_heartbeat(user: dict = Depends(current_user)):
    """Your heartbeat's settings, how many to-dos are open, and when it next beats"""
    return heartbeat.settings(user["id"])


@router.put("/tasks/heartbeat")
def save_heartbeat(body: HeartbeatFields, user: dict = Depends(current_user)):
    sent = body.model_dump(exclude_unset=True)
    return _heartbeat(lambda: heartbeat.save(user["id"], {k: v for k, v in sent.items() if v is not None or k == "agent_id"}))


@router.post("/tasks/heartbeat/beat")
def beat_now(user: dict = Depends(current_user)):
    """Work through the to-dos now, without waiting for the next beat"""
    return _heartbeat(lambda: heartbeat.beat_now(user["id"]))


@router.get("/tasks/heartbeat/todos")
def list_todos(user: dict = Depends(current_user)):
    return heartbeat.todos(user["id"])


@router.post("/tasks/heartbeat/todos", status_code=201)
def add_todo(body: TodoFields, user: dict = Depends(current_user)):
    return _heartbeat(lambda: heartbeat.add(user["id"], body.text or ""))


@router.patch("/tasks/heartbeat/todos/{todo_id}")
def change_todo(todo_id: str, body: TodoFields, user: dict = Depends(current_user)):
    found = _heartbeat(lambda: heartbeat.change(user["id"], todo_id, body.text, body.reopen))
    if found is None:
        raise HTTPException(404, "No such to-do")
    return found


@router.delete("/tasks/heartbeat/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: str, user: dict = Depends(current_user)):
    if not heartbeat.delete(user["id"], todo_id):
        raise HTTPException(404, "No such to-do")
    return Response(status_code=204)


@router.delete("/tasks/heartbeat/done", status_code=204)
def clear_done(user: dict = Depends(current_user)):
    heartbeat.clear_done(user["id"])
    return Response(status_code=204)


@router.get("/tasks/{task_id}")
def get_task(task_id: str, user: dict = Depends(current_user)):
    if (task := tasks.get(user["id"], task_id)) is None:
        raise HTTPException(404, "No such task")
    return task


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, body: TaskFields, user: dict = Depends(current_user)):
    try:
        task = tasks.update(user["id"], task_id, fields(body))
    except tasks.TaskError as e:
        raise bad(e)
    if task is None:
        raise HTTPException(404, "No such task")
    return task


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str, user: dict = Depends(current_user)):
    """Delete a task and its runs; the chats it wrote stay"""
    if not tasks.delete(user["id"], task_id):
        raise HTTPException(404, "No such task")
    return Response(status_code=204)


@router.post("/tasks/{task_id}/run", status_code=201)
def run_task(task_id: str, user: dict = Depends(current_user)):
    """Run it now, whatever its schedule"""
    try:
        run = tasks.run_now(user["id"], task_id)
    except tasks.TaskError as e:
        raise HTTPException(409, str(e))
    if run is None:
        raise HTTPException(404, "No such task")
    return run


@router.get("/tasks/{task_id}/runs")
def task_runs(task_id: str, user: dict = Depends(current_user)):
    """Its latest runs, newest first"""
    if tasks.get(user["id"], task_id) is None:
        raise HTTPException(404, "No such task")
    return tasks.runs_for(user["id"], task_id)


@router.post("/tasks/runs/{run_id}/stop", status_code=204)
def stop_run(run_id: str, user: dict = Depends(current_user)):
    run = tasks.get_run(run_id)
    if run is None or run["user_id"] != user["id"]:
        raise HTTPException(404, "No such run")
    if not tasks.stop_run(run_id):
        raise HTTPException(409, "It isn't running")
    return Response(status_code=204)
