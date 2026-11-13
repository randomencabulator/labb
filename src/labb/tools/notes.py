"""labb's notes: memory that outlasts its context, where old tool results are cleared and older
turns summarized, so it keeps what it learns and decides (findings, decisions, progress on longer
jobs) and a later session can pick the work up. Entries have a `## topic` heading; the model is
told the topics each call (model.work_reminder).

Where they are depends on the run. In the terminal, the project's <workdir>/.labb/notes.md:
labb's own file, so writing it needs no approval, also in plan mode. Elsewhere (the web chat,
tasks and workflows) the person's own notes, kept by the server (`store`, set by server/app.py,
per `configurable["user_id"]`); an app's chat (`configurable["app"]`) keeps that app's own
instead, and never sees the person's other notes. People see and change both on the web's Notes page, a PC's
folders through `labb connect` (cli/connect.py).
"""

import hashlib
from datetime import date
from pathlib import Path

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig

from labb.tools.files import resolve

LABEL = "Notes"
DESCRIPTION = "Keeps notes of what it finds out and decides, which you see on the Notes page."

MAX_NOTES_CHARS = 20_000
# Topics listed to the model each call: the latest ones
MAX_TOPICS = 15

# The server's notes: read(user_id, app_id) -> str, change(user_id, action, text, topic, base, app_id) -> str,
# app_id None for the person's own. None outside the server (where notes are a project's file).
store = None


class NotesError(ValueError):
    """A change that can't be made, said for the person or the model"""


class Conflict(NotesError):
    """The notes changed since they were read"""


class TooLong(NotesError):
    pass


def notes_file(workdir: str | Path) -> Path:
    return Path(workdir) / ".labb" / "notes.md"


def digest(text: str) -> str:
    """What the notes were when read, to refuse a change made over someone else's"""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def headings(text: str) -> list[str]:
    """The notes' headings, the latest last; ["notes without topics"] for notes that have none"""
    found = [line.lstrip("#").strip() for line in text.splitlines() if line.startswith("#") and line.lstrip("#").strip()]
    if not found:
        return ["notes without topics"] if text.strip() else []
    return found[-MAX_TOPICS:]


def appended(old: str, text: str, topic: str = "") -> str:
    """The notes with an entry added: one with a topic gets it as a heading; one without is dated,
    unless it's the first"""
    text, topic = text.strip(), topic.strip()
    if topic or old.strip():
        heading = f"## {topic} ({date.today():%Y-%m-%d})" if topic else f"_{date.today():%Y-%m-%d}_"
        text = f"{heading}\n{text}"
    return f"{old.strip()}\n\n{text}".strip() if old.strip() else text


def changed(old: str, action: str, text: str = "", topic: str = "", base: str | None = None) -> str:
    """The notes after a change: "add" an entry, or "replace" them all. With `base` (the digest
    they had when read), refused if they changed since."""

    if base is not None and base != digest(old):
        raise Conflict("The notes changed since they were read: load them again first.")
    if action == "add":
        if not text.strip():
            raise NotesError("Write something to add.")
        new = appended(old, text, topic)
    elif action == "replace":
        new = text.strip()
    else:
        raise NotesError(f"Can't {action} notes: add or replace them.")
    if len(new) > MAX_NOTES_CHARS:
        raise TooLong(f"The notes would be over {MAX_NOTES_CHARS:,} characters.")
    return new


def read_file(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except FileNotFoundError:
        return ""


def change_file(path: Path, action: str, text: str = "", topic: str = "", base: str | None = None) -> str:
    """Change a project's notes file; the new notes"""
    new = changed(read_file(path), action, text, topic, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new + "\n" if new else "")
    return new


def topics(workdir: str | Path) -> list[str]:
    """A project's topics (see headings)"""
    try:
        return headings(read_file(notes_file(workdir)))
    except OSError:
        return []


def _user(config: RunnableConfig | None) -> str | None:
    """Whose server notes a run without a project uses, if it can"""
    configurable = (config or {}).get("configurable") or {}
    return configurable.get("user_id") if store is not None and not configurable.get("workdir") else None


def _app(config: RunnableConfig | None) -> str | None:
    """The app whose chat this run is, whose notes it keeps instead of the person's"""
    return (((config or {}).get("configurable") or {}).get("app") or {}).get("id")


def usable(config: RunnableConfig | None) -> bool:
    """Whether the run has notes: a project's, or the person's on the server"""
    configurable = (config or {}).get("configurable") or {}
    return bool(configurable.get("workdir")) or _user(config) is not None


def topics_for(config: RunnableConfig | None) -> list[str]:
    configurable = (config or {}).get("configurable") or {}
    if configurable.get("workdir"):
        return topics(configurable["workdir"])
    if (user := _user(config)) is not None:
        try:
            return headings(store.read(user, _app(config)))
        except Exception:
            return []
    return []


def _read(config: RunnableConfig | None) -> str:
    if (user := _user(config)) is not None:
        return store.read(user, _app(config))
    root, _ = resolve(config, ".")
    return read_file(notes_file(root))


def _change(config: RunnableConfig | None, action: str, text: str, topic: str) -> str:
    if (user := _user(config)) is not None:
        return store.change(user, action, text, topic, None, _app(config))
    root, _ = resolve(config, ".")
    return change_file(notes_file(root), action, text, topic)


@tool
def read_notes(config: RunnableConfig = None) -> str:
    """Read your notes: what earlier sessions found, decided and left to do."""
    try:
        text = _read(config)
    except ValueError as e:
        return f"Error: {e}"
    except OSError as e:
        return f"Error: can't read the notes: {e}"
    return text or "No notes yet."


@tool
def write_notes(text: str, topic: str = "", replace: bool = False, config: RunnableConfig = None) -> str:
    """Add to your notes, or rewrite them with `replace`.

    Args:
        text: Markdown to add, e.g. "Sessions are checked in server/auth.py; tests need
            TEST_DATABASE_URL." With `replace`, the whole new notes (keep their `## ` headings).
        topic: What it's about, a few words, e.g. "Auth" or "Fixing the login bug": the entry's heading
        replace: Replace all the notes instead of adding (to tidy them up or remove what's
            no longer true)
    """
    try:
        new = _change(config, "replace" if replace else "add", text, topic)
    except TooLong:
        return (f"Error: the notes would be over {MAX_NOTES_CHARS:,} characters. Read them and rewrite "
                "them shorter with replace=True, keeping what's still useful.")
    except NotesError as e:
        return "Error: text is empty" if not text.strip() and not replace else f"Error: {e}"
    except ValueError as e:
        return f"Error: {e}"
    except OSError as e:
        return f"Error: can't save the notes: {e}"
    return f"Saved the notes ({len(new.splitlines())} lines)."


RUN_TOGETHER = ("read_notes",)
TOOLS = [read_notes, write_notes]

PROMPT = (
    "Your notes (.labb/notes.md) are memory that outlasts your context: old tool results get "
    "cleared and older turns summarized, but the notes stay. As soon as something took effort to "
    "find out (how a part of the code works, a command that builds or tests, a decision and why, "
    "where an unfinished job stands), save it with `write_notes` under a short `topic` instead of "
    "counting on remembering it. You're told the notes' topics: `read_notes` before reading files "
    "again or picking up earlier work. Keep them short and true; don't copy the code or AGENTS.md."
)
# Without a project: the person's own notes, which they see on the Notes page
WEB_PROMPT = (
    "Your notes are memory that outlasts this conversation, and the user can read and edit them "
    "on the Notes page. When a job takes several steps or will go on later, save what took effort "
    "to find out, decisions and where the job stands with `write_notes` under a short `topic`. "
    "You're told the notes' topics: `read_notes` when one matters. Facts about the user go in "
    "memories, not notes. Keep them short and true."
)
