"""Running workflows.

A step starts when every step before it has ended. A connection is taken when the step it
leaves ended through that way out (a condition's branch, an approval's answer); a step that
none of its incoming connections were taken to is skipped, and so on down the line. So an
if/else that meets again waits only for the branch that ran. Steps that don't depend on each
other run at the same time.

Each step is saved as it ends, which is what lets a run pause: an approval waits for an answer
in the web chat, a wait for its time, both also across restarts. A run that a restart cut off
mid-step is marked failed; `rerun` starts it again from any step, with the outputs before it.
"""

import logging
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone

from labb import chats
from labb.server import inbox, limits
from labb.server.studio import nodes, steps, store
from labb.server.studio.steps import Pause, Step, StepError, Stopped

log = logging.getLogger(__name__)

WORKERS = max(1, int(os.getenv("WORKFLOW_WORKERS", "2")))
# A run's time, steps, parallel steps and retries, and each person's runs at once: Settings → Limits
# How long a run that started on its own waits for people's chats to finish, at most
BUSY_WAIT_SECONDS = 10 * 60
RESTARTED = "The server restarted during this step; re-run the workflow from here"

_pool = ThreadPoolExecutor(WORKERS, thread_name_prefix="workflow")
_stops: dict[str, threading.Event] = {}
_stops_lock = threading.Lock()

# Set by server/app.py: whether someone's web reply is being written
busy = lambda: False


class RunError(ValueError):
    """A run that can't start, said for whoever started it"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Starting ------------------------------------------------------------------------

def coerce_inputs(definition: dict, given: dict) -> dict:
    """The Start step's inputs from what was given: defaults filled in, types checked"""

    trigger = nodes.trigger_of(definition) or {}
    values = {}
    for spec in (trigger.get("config") or {}).get("inputs") or []:
        name, kind = spec.get("name"), spec.get("type", "text")
        value = given.get(name)
        if value in (None, ""):
            value = spec.get("default")
        if value in (None, ""):
            if spec.get("required"):
                raise RunError(f"The input {spec.get('label') or name} is needed")
            value = False if kind == "boolean" else ""
        elif kind == "number":
            try:
                value = float(value) if "." in str(value) else int(value)
            except ValueError:
                raise RunError(f"{spec.get('label') or name} is a number")
        elif kind == "boolean":
            value = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
        else:
            value = str(value)
        values[name] = value
    return values


def lookups(user_id: str) -> nodes.Lookups:
    """What this user's steps can use: their agents, skills and actions"""

    from labb import skills
    from labb.server import agents
    from labb.server import speech
    from labb.server.studio import sandbox

    return nodes.Lookups(
        agents={a["id"] for a in agents.list_for(user_id)},
        skills={s["name"] for s in skills.available({"configurable": {"user_id": user_id}})},
        actions={a["id"] for a in store.list_actions(user_id)},
        sandbox=sandbox.available(),
        speech=speech.available(),
    )


def definition_for(workflow: dict, draft: bool) -> tuple[dict, int | None]:
    if draft:
        return workflow["draft"], None
    if not workflow["published_version"]:
        raise RunError(f"{workflow['name']} isn't published yet; publish it, or run the draft from Studio")
    return store.get_version(workflow["id"], workflow["published_version"]), workflow["published_version"]


def start(workflow: dict, user_id: str, *, trigger: str, inputs: dict | None = None, draft: bool = False,
          extra: dict | None = None, quiet: bool = False) -> str:
    """Queue a run of a workflow for `user_id`; returns its id. Raises RunError."""

    definition, version = definition_for(workflow, draft)
    problems = nodes.check(definition, lookups(user_id))
    if problems:
        raise RunError(f"{workflow['name']} can't run yet: {problems[0]['message']}")
    if store.active_runs(user_id) >= (most := limits.get("workflow_active")):
        raise RunError(f"You have {most} runs going already; wait for one to end")
    values = {**coerce_inputs(definition, inputs or {}), **(extra or {})}
    run_id = store.insert_run(workflow["id"], user_id, version, definition, trigger, values)
    submit(run_id, quiet=quiet)
    return run_id


def submit(run_id: str, quiet: bool = False):
    _pool.submit(_execute_logged, run_id, quiet)


def _execute_logged(run_id: str, quiet: bool):
    try:
        if quiet:
            waited = 0.0
            while busy() and waited < BUSY_WAIT_SECONDS:
                time.sleep(2)
                waited += 2
        execute(run_id)
    except Exception:
        log.exception("Workflow run %s failed", run_id)
        store.update_run(run_id, status="failed", error="Something went wrong running it; see the server's log",
                         finished_at=utcnow())
        run = store.get_run(run_id)
        if run is not None:
            workflow = store.get_workflow(run["workflow_id"])
            _tell(run, workflow, workflow["name"] if workflow else "Workflow", {})


# --- Running -------------------------------------------------------------------------

def _descendants(definition: dict, start_id: str) -> set[str]:
    _, _, outgoing = nodes.index(definition)
    found, todo = {start_id}, [start_id]
    while todo:
        for edge in outgoing[todo.pop()]:
            if edge["target"] not in found:
                found.add(edge["target"])
                todo.append(edge["target"])
    return found


def execute(run_id: str) -> dict | None:
    """Run (or go on with) a queued run until it ends or waits; the run as it is then"""

    if not store.claim_run(run_id, ("queued",)):
        return store.get_run(run_id)
    run = store.get_run(run_id)
    definition = run["definition"]
    by_id, incoming, _ = nodes.index(definition)
    order = [n["id"] for n in definition.get("nodes", [])]
    workflow = store.get_workflow(run["workflow_id"])
    name = workflow["name"] if workflow else "Workflow"

    saved = {s["node_id"]: s for s in store.steps(run_id)}
    status = {nid: saved[nid]["status"] if nid in saved else "pending" for nid in by_id}
    outputs = {nid: saved[nid]["output"] for nid in saved}
    handles = {nid: saved[nid]["handle"] for nid in saved}
    trigger = nodes.trigger_of(definition)
    if status[trigger["id"]] != "done":
        at = utcnow()
        store.save_step(run_id, trigger["id"], trigger["key"], status="done", output=run["inputs"], started_at=at, finished_at=at)
        status[trigger["id"]], outputs[trigger["id"]] = "done", run["inputs"]

    stop = threading.Event()
    with _stops_lock:
        _stops[run_id] = stop
    minutes, most_steps = limits.get("workflow_minutes"), limits.get("workflow_steps")
    deadline = time.monotonic() + minutes * 60
    count = sum(1 for s in saved.values() if s["status"] == "done")
    failure: str | None = None
    timed_out = False
    base = {"run": {"id": run_id, "workflow": name, "started_at": run["started_at"]}}

    def continues(nid: str) -> bool:
        return by_id[nid].get("on_error") == "continue"

    def resolved(nid: str) -> bool:
        return status[nid] in ("done", "skipped", "failed") and (status[nid] != "failed" or continues(nid))

    def taken(edge: dict) -> bool:
        source = edge["source"]
        if status[source] == "failed":
            return continues(source) and edge.get("handle") is None
        return status[source] == "done" and handles.get(source) == edge.get("handle")

    def context_for(nid: str) -> dict:
        context = {**base, "now": utcnow(), "trigger": outputs.get(trigger["id"])}
        for other, state in status.items():
            # Steps on a branch not taken (or failed and passed over) are there, empty
            if state in ("done", "skipped", "failed") and other != trigger["id"]:
                context[by_id[other]["key"]] = outputs.get(other) if state == "done" else None
        context["_merged"] = {by_id[e["source"]]["key"]: outputs.get(e["source"]) for e in incoming[nid] if taken(e)}
        return context

    def run_node(nid: str, context: dict, attempt_from: int):
        node = by_id[nid]
        retries = max(0, min(int(node.get("retries") or 0), limits.get("workflow_retries")))
        step = Step(run_id, run["user_id"], name, node["key"], stop)
        for attempt in range(attempt_from, retries + 1):
            try:
                return steps.run(node, context, step), attempt
            except StepError:
                if attempt >= retries or stop.is_set():
                    raise
                time.sleep(min(2 ** attempt, 10))

    running: dict = {}
    with ThreadPoolExecutor(limits.get("workflow_parallel"), thread_name_prefix=f"run-{run_id[:6]}") as pool:
        while True:
            changed = True
            while changed:
                changed = False
                for nid in order:
                    if status[nid] != "pending" or not all(resolved(e["source"]) for e in incoming[nid]):
                        continue
                    if not any(taken(e) for e in incoming[nid]):
                        status[nid] = "skipped"
                        store.save_step(run_id, nid, by_id[nid]["key"], status="skipped")
                        changed = True
                        continue
                    if failure or stop.is_set():
                        continue
                    if count >= most_steps:
                        failure = f"Stopped after {most_steps} steps"
                        continue
                    status[nid] = "running"
                    count += 1
                    attempt = (saved.get(nid) or {}).get("attempt") or 0
                    store.save_step(run_id, nid, by_id[nid]["key"], status="running", started_at=utcnow(),
                                    finished_at=None, error=None, attempt=attempt)
                    running[pool.submit(run_node, nid, context_for(nid), 0)] = nid
            if not running:
                break
            done, _ = wait(list(running), timeout=1, return_when=FIRST_COMPLETED)
            if time.monotonic() > deadline and not timed_out:
                timed_out = True
                failure = failure or f"Took longer than {minutes} minutes"
                stop.set()
            for future in done:
                nid = running.pop(future)
                node = by_id[nid]
                try:
                    result, attempt = future.result()
                except Stopped:
                    status[nid] = "stopped"
                    store.save_step(run_id, nid, node["key"], status="stopped", finished_at=utcnow())
                    continue
                except Exception as e:
                    message = str(e) if isinstance(e, StepError) else f"{type(e).__name__}: {e}"
                    if not isinstance(e, StepError):
                        log.exception("Step %s of run %s failed", node["key"], run_id)
                    status[nid] = "failed"
                    store.save_step(run_id, nid, node["key"], status="failed", error=message, finished_at=utcnow())
                    if not continues(nid):
                        failure = failure or f"{node.get('label') or node['key']}: {message}"
                    continue
                if isinstance(result, Pause):
                    status[nid] = "waiting"
                    store.save_step(run_id, nid, node["key"], status="waiting", attempt=attempt,
                                    input={**(result.input or {}), "until": result.resume_at} if result.resume_at else result.input)
                    continue
                status[nid], outputs[nid], handles[nid] = "done", result.output, result.handle
                count += result.steps - 1
                store.save_step(run_id, nid, node["key"], status="done", input=result.input, output=result.output,
                                handle=result.handle, trace=result.trace, attempt=attempt, finished_at=utcnow(),
                                input_tokens=result.input_tokens, output_tokens=result.output_tokens)
                if result.input_tokens or result.output_tokens:
                    store.add_run_tokens(run_id, result.input_tokens, result.output_tokens)

    with _stops_lock:
        _stops.pop(run_id, None)
    finished = utcnow()
    waiting = [nid for nid, s in status.items() if s == "waiting"]
    if stop.is_set() and not timed_out:
        store.update_run(run_id, status="stopped", finished_at=finished)
    elif failure:
        store.update_run(run_id, status="failed", error=failure, finished_at=finished)
    elif waiting:
        waits = [s["input"].get("until") for s in store.steps(run_id)
                 if s["node_id"] in waiting and isinstance(s["input"], dict) and s["input"].get("until")]
        store.update_run(run_id, status="waiting", resume_at=min(waits) if waits else None)
    else:
        store.update_run(run_id, status="done", result=_result(definition, status, outputs, run_id), finished_at=finished)
    _tidy(run["workflow_id"])
    ended = store.get_run(run_id)
    if ended is not None:
        _tell(ended, workflow, name, by_id)
    return ended


# Runs that start on their own, which nobody is watching: their results go to the inbox
UNWATCHED = ("schedule", "webhook", "event")


def approval_key(run_id: str, node_id: str) -> str:
    return f"approval:{run_id}:{node_id}"


def _tell(run: dict, workflow: dict | None, name: str, by_id: dict):
    """The inbox's word on a run that ended or waits (inbox.py): an approval waiting for its
    person and the workflow's owner, a failure (not of a test run, which someone is watching),
    and the result of a run that started on its own"""

    if run["workflow_id"].startswith("try-"):
        # The drafting room's Try it: its person is watching
        return
    link = f"/studio/workflows/{run['workflow_id']}/runs/{run['id']}"
    if run["status"] == "waiting":
        for step in store.steps(run["id"]):
            node = by_id.get(step["node_id"]) or {}
            if step["status"] != "waiting" or node.get("type") != "approval":
                continue
            label = node.get("label") or step["key"]
            message = (step["input"] or {}).get("message", "") if isinstance(step["input"], dict) else ""
            for person in {run["user_id"], *([workflow["owner_id"]] if workflow else [])}:
                inbox.add(person, "studio", f"Approval needed: {name}", f"{label}: {message}" if message else label,
                          link=link, key=approval_key(run["id"], step["node_id"]), level="warning")
    elif run["status"] == "failed" and run["trigger"] != "test":
        inbox.add(run["user_id"], "studio", f"Workflow failed: {name}", run.get("error") or "", link=link,
                  key=f"workflow:{run['workflow_id']}:failed", level="error")
    elif run["status"] == "done" and run["trigger"] in UNWATCHED:
        inbox.add(run["user_id"], "studio", name, "Workflow finished: see its result.", link=link,
                  key=f"workflow:{run['workflow_id']}:done", level="success")


def _result(definition: dict, status: dict, outputs: dict, run_id: str):
    """A Result step's output, or else the output of the last step of the line that ran (one no
    step that ran comes after), the latest to end if there are several"""

    responds = [n["id"] for n in definition.get("nodes", []) if n["type"] == "respond" and status.get(n["id"]) == "done"]
    if responds:
        return outputs[responds[-1]]
    _, _, outgoing = nodes.index(definition)
    last = {nid for nid, state in status.items()
            if state == "done" and not any(status.get(e["target"]) == "done" for e in outgoing[nid])}
    ended = [s for s in store.steps(run_id) if s["node_id"] in last and s["key"] != nodes.TRIGGER_KEY]
    ended.sort(key=lambda s: s["finished_at"] or "")
    return ended[-1]["output"] if ended else None


def _tidy(workflow_id: str):
    """Delete the runs beyond the latest ones, with the conversations of their agent steps"""

    old = store.old_runs(workflow_id)
    for run_id in old:
        for thread_id in chats.checkpointed_threads(f"wf-{run_id}-"):
            chats.delete_chat(thread_id)
    if old:
        store.delete_runs(old)


# --- Pausing, stopping, again --------------------------------------------------------

def decide(run_id: str, node_id: str, approved: bool, by: str, comment: str = "") -> dict:
    """Answer a run's approval and let it go on; raises RunError"""

    run = store.get_run(run_id)
    if run is None or run["status"] != "waiting":
        raise RunError("That run isn't waiting for an answer")
    step = next((s for s in store.steps(run_id) if s["node_id"] == node_id), None)
    node = nodes.index(run["definition"])[0].get(node_id)
    if step is None or node is None or node["type"] != "approval" or step["status"] != "waiting":
        raise RunError("That step isn't waiting for an approval")
    handle = "approved" if approved else "rejected"
    store.save_step(run_id, node_id, node["key"], status="done", handle=handle, finished_at=utcnow(),
                    output={"approved": approved, "comment": comment, "by": by})
    store.update_run(run_id, status="queued", resume_at=None)
    # Answered: what the inbox asked of everyone about it is done
    inbox.mark_read_about(key=approval_key(run_id, node_id))
    submit(run_id)
    return store.get_run(run_id)


def resume_waits(now: str | None = None) -> int:
    """Let the runs whose waits are over go on (the scheduler's tick); how many"""

    now = now or utcnow()
    resumed = 0
    for run in store.runs_in(("waiting",)):
        if not run["resume_at"] or run["resume_at"] > now:
            continue
        by_id = nodes.index(run["definition"])[0]
        for step in store.steps(run["id"]):
            until = step["input"].get("until") if isinstance(step["input"], dict) else None
            if step["status"] == "waiting" and until and until <= now and by_id.get(step["node_id"], {}).get("type") == "wait":
                store.save_step(run["id"], step["node_id"], step["key"], status="done", finished_at=utcnow())
        store.update_run(run["id"], status="queued", resume_at=None)
        submit(run["id"])
        resumed += 1
    return resumed


def stop(run_id: str) -> bool:
    with _stops_lock:
        flag = _stops.get(run_id)
    if flag:
        flag.set()
        return True
    run = store.get_run(run_id)
    if run and run["status"] in ("queued", "waiting"):
        store.update_run(run_id, status="stopped", finished_at=utcnow())
        inbox.mark_read_about(key_prefix=f"approval:{run_id}:")
        return True
    return False


def rerun(run_id: str, from_node: str, user_id: str) -> str:
    """A new run of the same version and inputs, keeping the outputs of the steps that don't come
    after `from_node`; returns its id"""

    run = store.get_run(run_id)
    if run is None:
        raise RunError("There's no such run")
    by_id = nodes.index(run["definition"])[0]
    if from_node not in by_id or by_id[from_node]["type"] == "trigger":
        raise RunError("Pick a step to run again from")
    if store.active_runs(user_id) >= (most := limits.get("workflow_active")):
        raise RunError(f"You have {most} runs going already; wait for one to end")
    again = _descendants(run["definition"], from_node)
    new_id = store.insert_run(run["workflow_id"], user_id, run["version"], run["definition"], "rerun", run["inputs"])
    for step in store.steps(run_id):
        if step["node_id"] not in again and step["status"] in ("done", "skipped"):
            store.save_step(new_id, step["node_id"], step["key"], status=step["status"], input=step["input"],
                            output=step["output"], handle=step["handle"], trace=step["trace"],
                            started_at=step["started_at"], finished_at=step["finished_at"])
    submit(new_id)
    return new_id


def recover() -> int:
    """At start: runs a restart cut off fail (their running steps say so); queued ones go on"""

    cut = store.runs_in(("running",))
    for run in cut:
        for step in store.steps(run["id"]):
            if step["status"] == "running":
                store.save_step(run["id"], step["node_id"], step["key"], status="failed", error=RESTARTED, finished_at=utcnow())
        store.update_run(run["id"], status="failed", error=RESTARTED, finished_at=utcnow())
    for run in store.runs_in(("queued",)):
        submit(run["id"], quiet=True)
    return len(cut)


def view(run: dict) -> dict:
    """A run with its steps, for the API"""
    return {**run, "steps": store.steps(run["id"])}

