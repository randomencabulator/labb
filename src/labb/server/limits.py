"""Settings → Limits, for admins: how far replies, tasks and workflows may go, and how many of
everything people may have. Each limit has a default (what labb used before it could be changed)
and a range; what admins save is kept in `server_settings` and read when it's used, so a change
applies to the next reply, run or upload.

The apps' and artifacts' limits are the spawner's, and the published pages' are labb-sites': each
service keeps its own (`GET|PUT /limits`, with its token), and this page shows and saves them
alongside these (`remote`), when the service is set up and answers."""

import json
import threading
from dataclasses import asdict, dataclass

import httpx

from labb.chats import conn, lock

KEY = "limits"


@dataclass(frozen=True)
class Limit:
    key: str
    group: str
    label: str
    description: str
    default: int
    min: int
    max: int
    unit: str = ""


LIMITS = [
    Limit("chat_model_calls", "Chat", "Model calls per reply",
          "How many times one reply may call the model; each round of tool calls is one. 0 for no limit.", 0, 0, 1000),
    Limit("tool_parallel", "Chat", "Tool calls at once",
          "How many of a reply's tool calls that only read (fetching pages, searching) may run at the same time. 1 for one at a time.", 5, 1, 32),
    Limit("approval_minutes", "Chat", "Waiting for a tool's approval",
          "How long a tool that asks in the chat (an MCP server's) waits for Allow before it's refused.", 10, 1, 1440, "minutes"),
    Limit("task_model_calls", "Tasks", "Model calls per run",
          "How many times a scheduled task's run may call the model before it's stopped.", 25, 1, 1000),
    Limit("task_minutes", "Tasks", "Time per run", "How long a task's run may take before it's stopped.", 15, 1, 1440, "minutes"),
    Limit("tasks_per_person", "Tasks", "Tasks per person", "How many tasks each person may have.", 20, 1, 1000),
    Limit("task_failures_to_pause", "Tasks", "Failures before a task is paused",
          "A task that fails this many times in a row is turned off.", 3, 1, 100),
    Limit("task_runs_kept", "Tasks", "Runs kept per task", "Older runs are deleted, with their chats.", 50, 1, 1000),
    Limit("heartbeat_min_minutes", "Tasks", "Shortest heartbeat",
          "How often someone's heartbeat may look at their to-dos, at most.", 5, 1, 1440, "minutes"),
    Limit("heartbeat_todos_per_person", "Tasks", "Open to-dos per person", "How many to-dos may wait for someone's heartbeat.",
          100, 1, 10_000),
    Limit("workflow_minutes", "Workflows", "Time per run", "How long a workflow's run may take before it's stopped.",
          30, 1, 1440, "minutes"),
    Limit("workflow_steps", "Workflows", "Steps per run",
          "How many steps a run may take, loops included, before it's stopped.", 200, 1, 10000),
    Limit("workflow_parallel", "Workflows", "Steps at once", "How many of a run's steps may run at the same time.", 4, 1, 32),
    Limit("workflow_active", "Workflows", "Runs at once per person",
          "How many runs each person may have going at the same time.", 3, 1, 100),
    Limit("workflow_retries", "Workflows", "Retries per step", "The most retries a step can be set to.", 3, 0, 20),
    Limit("agent_step_model_calls", "Workflows", "Model calls per agent step",
          "How many times an agent or skill step may call the model.", 25, 1, 1000),
    Limit("loop_parallel", "Workflows", "Loop items at once", "How many items of a loop may run at the same time.", 4, 1, 32),
    Limit("webhook_per_minute", "Workflows", "Webhook calls per minute",
          "How often each workflow's webhook may be called.", 30, 1, 10000),
    Limit("workflow_runs_kept", "Workflows", "Runs kept per workflow", "Older runs are deleted.", 50, 1, 1000),
    Limit("python_seconds", "Workflows", "Time per Python step",
          "The most a Python step may run; the sandbox stops any at 120 seconds.", 120, 1, 120, "seconds"),
    Limit("http_seconds", "Workflows", "HTTP action timeout",
          "How long an HTTP action waits for an answer when it doesn't set its own.", 20, 1, 120, "seconds"),
    Limit("agent_ask_depth", "Agents", "How deep agents ask each other",
          "An agent asked by another may ask others in turn, this deep at most.", 2, 1, 5),
    Limit("attachments_per_message", "Chat", "Attachments per message", "How many files one message may have.", 10, 1, 100),
    Limit("attachment_image_mb", "Chat", "Image size", "The largest image a message may have.", 5, 1, 100, "MB"),
    Limit("attachment_text_chars", "Chat", "Text file size", "The longest text file a message may have.",
          200_000, 1_000, 10_000_000, "characters"),
    Limit("memories_per_person", "Memory", "Memories per person", "How many memories each person may have.", 500, 1, 100_000),
    Limit("memory_chars", "Memory", "Memory length", "The longest one memory may be; each is meant to be one fact.",
          500, 50, 10_000, "characters"),
    Limit("documents_per_person", "Documents", "Documents per person", "How many documents each person may upload.",
          200, 1, 100_000),
    Limit("document_mb", "Documents", "Document size", "The largest document that can be uploaded.", 20, 1, 1_000, "MB"),
    Limit("recording_mb", "Voice", "Recording size",
          "The longest recording that can be turned into text. OpenAI takes 25 MB at most.", 25, 1, 500, "MB"),
    Limit("speak_chars", "Voice", "Read aloud at once", "How much text can be read aloud at once.",
          20_000, 100, 1_000_000, "characters"),
    Limit("clips_kept", "Voice", "Recordings kept per person", "Older recordings and read-aloud clips are deleted.",
          200, 1, 100_000),
    Limit("agents_per_person", "Agents", "Agents per person", "How many agents each person may make.", 100, 1, 10_000),
    Limit("agent_instructions_chars", "Agents", "Instructions length", "The longest an agent's instructions may be.",
          8_000, 100, 200_000, "characters"),
    Limit("agent_team", "Agents", "Agents and actions per agent",
          "How many other agents an agent may ask, and how many actions it may use (each).", 12, 1, 100),
    Limit("skills_per_person", "Skills", "Skills per person", "How many skills each person may add.", 50, 1, 10_000),
    Limit("skill_files", "Skills", "Files per skill", "How many files one skill may have.", 100, 1, 10_000),
    Limit("skill_file_chars", "Skills", "File length", "The longest one of a skill's files may be.",
          200_000, 1_000, 10_000_000, "characters"),
    Limit("skill_chars", "Skills", "Skill size", "How long a skill's files may be in all.", 1_000_000, 1_000, 100_000_000, "characters"),
    Limit("skill_zip_mb", "Skills", "Zip size", "The largest zip a skill can be uploaded as.", 5, 1, 1_000, "MB"),
    Limit("task_prompt_chars", "Tasks", "Prompt length", "The longest a task's prompt may be.", 8_000, 100, 200_000, "characters"),
    Limit("workflows_per_person", "Workflows", "Workflows per person", "How many workflows each person may have.", 100, 1, 10_000),
    Limit("workflow_nodes", "Workflows", "Steps per workflow", "How many steps one workflow may have.", 60, 2, 1_000),
    Limit("actions_total", "Workflows", "Actions on the server", "How many actions there may be, for everyone.", 200, 1, 10_000),
    Limit("engines_per_person", "Engines", "Engines per person", "How many engines each person may make.", 100, 1, 10_000),
    Limit("engine_versions_kept", "Engines", "Versions kept per engine",
          "Older versions are deleted; the current one always stays.", 200, 1, 10_000),
    Limit("mcp_servers", "Server", "MCP servers", "How many MCP servers Settings → MCP servers may have.", 20, 1, 1_000),
    Limit("providers", "Server", "Custom providers", "How many model providers may be added besides the built-in ones.",
          20, 1, 1_000),
    Limit("chosen_models", "Server", "Models in the list", "How many models may be chosen for people to pick from.",
          100, 1, 10_000),
    Limit("session_days", "Accounts", "Staying signed in",
          "How long a sign-in lasts before signing in again; for new sign-ins.", 30, 1, 3_650, "days"),
    Limit("invite_days", "Accounts", "Invites last", "How long an invite link works; for new invites.", 7, 1, 365, "days"),
    Limit("sign_in_tries", "Accounts", "Wrong passwords before waiting",
          "After this many wrong passwords for a username, each try waits longer (up to 15 minutes).", 5, 1, 100),
    Limit("inbox_kept", "Inbox", "Notifications kept per person", "Older ones are deleted, read or not.", 200, 10, 10_000),
]
BY_KEY = {limit.key: limit for limit in LIMITS}
# The ones the pages check before sending (GET /limits, for anyone signed in)
SHOWN = ("attachments_per_message", "attachment_image_mb", "attachment_text_chars", "memory_chars", "document_mb",
         "agent_instructions_chars", "task_prompt_chars")

_saved: dict[str, int] | None = None
_lock = threading.Lock()


class LimitError(ValueError):
    """A value that can't be saved, said for the admin"""


def saved() -> dict[str, int]:
    """What admins changed (kept in memory once read)"""

    global _saved
    with _lock:
        if _saved is None:
            # Made the server_settings table this uses (imported here: it imports what uses limits)
            from labb.server import providers  # noqa: F401
            with lock:
                row = conn.execute("SELECT value FROM server_settings WHERE key = ?", (KEY,)).fetchone()
            try:
                values = json.loads(row[0]) if row else {}
            except ValueError:
                values = {}
            _saved = {key: value for key, value in values.items()
                      if key in BY_KEY and isinstance(value, int) and BY_KEY[key].min <= value <= BY_KEY[key].max}
        return dict(_saved)


def get(key: str) -> int:
    """A limit's value: what an admin saved, or its default"""
    return saved().get(key, BY_KEY[key].default)


def recursion(model_calls: int) -> int | None:
    """The graph's step limit for this many model calls (each is up to 3 steps: model, tools,
    compact); None for no limit"""
    return 1 + 3 * model_calls if model_calls else None


def save(values: dict) -> dict[str, int]:
    """Save changed limits (a value of None goes back to the default), the spawner's and labb-sites'
    too; all the values after. Nothing is saved if one can't be."""

    global _saved
    current = saved()
    unreachable: list[str] = []
    known_remote = {limit["key"]: name for name, found in _remote_described(unreachable) for limit in found}
    remote = {name: {} for name in SERVICES}
    for key, value in values.items():
        if key in known_remote:
            remote[known_remote[key]][key] = value
            continue
        if key not in BY_KEY:
            raise LimitError(unreachable[0] if unreachable else f"There's no limit {key}")
        limit = BY_KEY[key]
        if value is None or value == limit.default:
            current.pop(key, None)
            continue
        if not isinstance(value, int) or isinstance(value, bool) or not limit.min <= value <= limit.max:
            raise LimitError(f"{limit.group}: {limit.label} is a whole number from {limit.min} to {limit.max}")
        current[key] = value
    for name, changed in remote.items():
        if changed:
            _call(name, "PUT", changed)
    with lock, conn:
        conn.execute("INSERT INTO server_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (KEY, json.dumps(current)))
    with _lock:
        _saved = current
    return values_now()


def values_now() -> dict[str, int]:
    return {limit.key: get(limit.key) for limit in LIMITS}


def described(problems: list[str] | None = None) -> list[dict]:
    """Every limit with its value, for the settings page, the services' after the server's; why a
    service's aren't there goes in `problems`"""

    now = values_now()
    found = [{**asdict(limit), "value": now[limit.key]} for limit in LIMITS]
    for _, limits in _remote_described(problems):
        found += limits
    return found


# The services with limits of their own: (what the page calls it, its URL, its token)
def _services() -> dict:
    from labb.server import apps, artifact_tools
    return {"spawner": ("the app spawner", apps.SPAWNER_URL, apps.token),
            "sites": ("labb-sites", artifact_tools.SITES_URL, artifact_tools.token)}


SERVICES = ("spawner", "sites")


def _call(name: str, method: str, values: dict | None = None) -> list[dict]:
    """A service's limits (after saving `values`, with PUT); what it refuses as LimitError"""

    label, url, token = _services()[name]
    headers = {"Authorization": f"Bearer {key}"} if (key := token()) else {}
    try:
        response = httpx.request(method, f"{url}/limits", headers=headers, json=values, timeout=10)
    except httpx.HTTPError as e:
        raise LimitError(f"Couldn't reach {label}: {e}")
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise LimitError(f"{label} refused it: {str(detail)[:300]}" if response.status_code != 400 else str(detail)[:300])
    found = response.json()
    if not isinstance(found, list):
        raise LimitError(f"{label} answered with something that isn't its limits")
    return found


def _remote_described(problems: list[str] | None = None) -> list[tuple[str, list[dict]]]:
    """(service, its limits) of each service that's set up and answers"""

    found = []
    for name in SERVICES:
        label, url, _ = _services()[name]
        if not url:
            continue
        try:
            found.append((name, _call(name, "GET")))
        except LimitError as e:
            if problems is not None:
                problems.append(f"{e}, so its limits aren't here")
    return found
