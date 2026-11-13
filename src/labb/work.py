"""What a conversation is working on, kept outside its messages so summaries can't lose it: its
todo list (tools/todo.py), its goal (tools/goal.py) and the plan the user approved (cli/app.py).

Where they are depends on the run, as with notes: in the terminal, files in the project's
.labb folder (git-ignored: working state, not something to commit); elsewhere (the web chat,
agents, tasks, workflows) the `work` table of the chats database, by thread.
"""

import json
from pathlib import Path

from langchain_core.runnables import RunnableConfig

# Each kind's folder under <workdir>/.labb and its files' extension
KINDS = {"todos": ("todos", ".json"), "goal": ("goals", ".json"), "plan": ("plans", ".md")}


def file_of(workdir: str | Path, thread_id: str | None, kind: str) -> Path:
    folder, extension = KINDS[kind]
    return Path(workdir) / ".labb" / folder / f"{thread_id or 'default'}{extension}"


def _where(config: RunnableConfig | None) -> tuple[str | None, str | None]:
    configurable = (config or {}).get("configurable") or {}
    return configurable.get("workdir"), configurable.get("thread_id")


def usable(config: RunnableConfig | None) -> bool:
    """Whether the run has somewhere to keep its work: a project, or a conversation on record"""
    workdir, thread_id = _where(config)
    return bool(workdir or thread_id)


def read(config: RunnableConfig | None, kind: str) -> str | None:
    workdir, thread_id = _where(config)
    if workdir:
        try:
            return file_of(workdir, thread_id, kind).read_text()
        except OSError:
            return None
    if not thread_id:
        return None
    from labb import chats

    try:
        return chats.get_work(thread_id, kind)
    except Exception:
        return None


def write(config: RunnableConfig | None, kind: str, text: str | None):
    """Keep it, or forget it with None"""

    workdir, thread_id = _where(config)
    if workdir:
        path = file_of(workdir, thread_id, kind)
        if text is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        ignore = path.parent / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n")
        path.write_text(text)
        return
    if not thread_id:
        raise ValueError("this conversation has nowhere to keep its work")
    from labb import chats

    chats.set_work(thread_id, kind, text)


def read_json(config: RunnableConfig | None, kind: str, default=None):
    """What was kept, or `default` if nothing was or it's broken"""
    try:
        text = read(config, kind)
        return json.loads(text) if text else default
    except (ValueError, TypeError):
        return default


def write_json(config: RunnableConfig | None, kind: str, value):
    write(config, kind, None if value is None else json.dumps(value, indent=2, ensure_ascii=False) + "\n")
