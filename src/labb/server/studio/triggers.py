"""Starting workflows on their own: on a schedule, from a webhook, on an event. They always run
the published version, as its owner.

- Schedules are the tasks' (server/tasks.py): every N minutes, daily at 8:00 in the owner's time
  zone, and so on. `next_run` is worked out when a version is published and after each run.
- A webhook is a secret URL (`/hooks/<token>`); only the token's SHA-256 is kept. Its JSON body
  fills the Start step's inputs by name, and the whole body is `{{trigger.body}}`.
- Events: a document added (not by a workflow, so a workflow can't start itself in a loop).
"""

import hashlib
import logging
import secrets as tokens
import threading
import time
from collections import deque
from datetime import datetime

from labb.server import tasks
from labb.server.studio import engine, nodes, steps, store
from labb.server import limits

log = logging.getLogger(__name__)

TICK_SECONDS = 30
WEBHOOK_PREFIX = "wh_"
# Webhook calls per workflow and minute, at most

_tick = threading.Event()
_started = False
_calls: dict[str, deque] = {}
_calls_lock = threading.Lock()


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- Schedules -----------------------------------------------------------------------

def schedule_of(definition: dict) -> tuple[dict | None, str]:
    config = (nodes.trigger_of(definition) or {}).get("config") or {}
    return config.get("schedule") or None, config.get("tz") or "UTC"


def clean_schedule(definition: dict, now: datetime) -> dict:
    """The definition with its schedule checked (raises tasks.TaskError)"""

    schedule, tz = schedule_of(definition)
    if not schedule:
        return definition
    trigger = nodes.trigger_of(definition)
    trigger["config"]["schedule"] = tasks.clean_schedule(schedule, now, tz)
    return definition


def plan(workflow_id: str, now: datetime | None = None):
    """Work out when a workflow runs next (none: not published, off, or no schedule)"""

    workflow = store.get_workflow(workflow_id)
    next_run = None
    if workflow and workflow["enabled"] and workflow["published_version"]:
        schedule, tz = schedule_of(store.get_version(workflow_id, workflow["published_version"]) or {})
        if schedule:
            following = tasks.next_after(schedule, tz, now or tasks.utcnow())
            next_run = tasks.iso(following) if following else None
    store.set_workflow_state(workflow_id, next_run=next_run)


def due(now: datetime) -> list[str]:
    """Start the scheduled workflows whose time has come; their run ids"""

    started = []
    for workflow in store.scheduled_due(tasks.iso(now)):
        try:
            started.append(engine.start(workflow, workflow["owner_id"], trigger="schedule", quiet=True))
        except engine.RunError as e:
            log.warning("Scheduled run of %s didn't start: %s", workflow["name"], e)
        plan(workflow["id"], now)
    return started


def tick(now: datetime | None = None):
    due(now or tasks.utcnow())
    engine.resume_waits()


def _loop():
    while True:
        try:
            tick()
        except Exception:
            log.exception("Studio's scheduler tick failed")
        _tick.wait(TICK_SECONDS)
        _tick.clear()


def start():
    """Start the scheduler (server/app.py, once the server is up); runs a restart cut off are marked"""

    global _started
    if _started:
        return
    _started = True
    engine.recover()
    threading.Thread(target=_loop, daemon=True, name="studio-scheduler").start()


# --- Webhooks ------------------------------------------------------------------------

def set_webhook(workflow_id: str, on: bool) -> str | None:
    """Turn a workflow's webhook on (a new token, returned once) or off"""

    if not on:
        store.set_workflow_state(workflow_id, webhook_hash=None)
        return None
    token = WEBHOOK_PREFIX + tokens.token_urlsafe(24)
    store.set_workflow_state(workflow_id, webhook_hash=digest(token))
    return token


def _allowed(workflow_id: str) -> bool:
    now = time.monotonic()
    with _calls_lock:
        calls = _calls.setdefault(workflow_id, deque())
        while calls and now - calls[0] > 60:
            calls.popleft()
        if len(calls) >= limits.get("webhook_per_minute"):
            return False
        calls.append(now)
        return True


def fire_webhook(token: str, body) -> str:
    """Start a run from a webhook call; its id. Raises LookupError for an unknown token, and
    engine.RunError for one that can't run."""

    workflow = store.workflow_by_webhook(digest(token)) if token.startswith(WEBHOOK_PREFIX) else None
    if workflow is None or not workflow["published_version"]:
        raise LookupError("There's no webhook at that address")
    published = store.get_version(workflow["id"], workflow["published_version"]) or {}
    if not ((nodes.trigger_of(published) or {}).get("config") or {}).get("webhook"):
        raise LookupError("There's no webhook at that address")
    if not workflow["enabled"]:
        raise engine.RunError(f"{workflow['name']} is turned off")
    if not _allowed(workflow["id"]):
        raise engine.RunError(f"Too many calls; at most {limits.get('webhook_per_minute')} a minute")
    inputs = body if isinstance(body, dict) else {}
    return engine.start(workflow, workflow["owner_id"], trigger="webhook", inputs=inputs, extra={"body": body}, quiet=True)


# --- Events --------------------------------------------------------------------------

def on_event(user_id: str, event: str, payload: dict) -> list[str]:
    # What a workflow does itself doesn't start workflows
    if getattr(steps.in_workflow, "on", False):
        return []
    started = []
    for workflow in store.published_by_owner(user_id):
        definition = store.get_version(workflow["id"], workflow["published_version"]) or {}
        events = ((nodes.trigger_of(definition) or {}).get("config") or {}).get("events") or []
        if event not in events:
            continue
        try:
            started.append(engine.start(workflow, user_id, trigger="event", extra={"event": {"type": event, **payload}},
                                        quiet=True))
        except engine.RunError as e:
            log.warning("%s didn't start on %s: %s", workflow["name"], event, e)
    return started


def document_added(user_id: str, document: dict):
    on_event(user_id, "document.added", {"document": {"id": document["id"], "name": document["name"]}})

