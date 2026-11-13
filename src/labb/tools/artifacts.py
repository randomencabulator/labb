"""Artifacts: web pages the model builds in a chat and publishes as a link, for a dashboard, a chart,
a diagram, a small tool or a mock-up. Each starts from a template (labb-spawner's
spawner/artifacts/template: Next.js, Base UI components, ten typed charts, typed diagrams, with an
AGENTS.md saying how), is built by the spawner with no network, and is served by labb-sites at
/artifacts/<name>/.

Offered where the server set `configurable["artifacts"]` (the web chat, when the spawner and
labb-sites are both there), and every call goes as the chat's user, so it only reaches their own
artifacts. This module stays free of the server's code: server/artifact_tools.py sets `backend`."""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from labb.tools.apps import closest, cut, loose_spans, numbered, syntax_problem, unnumbered, with_lines

LABEL = "Web pages"
DESCRIPTION = "Build web pages (dashboards, charts, diagrams, small tools) and share them as a link"
# Not in "all tools": the server turns it on where it can be built and served; an agent can be given it
ONLY_IF_ALLOWED = True
CHOOSABLE = True
# Its schemas wait for find_tools when the model's tools don't fit
DEFERRABLE = True
RUN_TOGETHER = ("list_artifacts", "list_artifact_files", "read_artifact_file")

# Set by the server: has `make`, `listing`, `files`, `read_text`, `write`, `delete` and `publish`,
# each `(config, ...)`, raising ValueError with what went wrong
backend = None

PROMPT = (
    "You can build web pages and give the user a link to them: a dashboard, charts, a diagram, a report, a small tool or "
    "a mock-up. Do it when they ask for one, or when a page shows something much better than text. Each page is an "
    "artifact: `new_artifact(title)` makes one from a template (Next.js, React, TypeScript, Tailwind, ready components, "
    "ten chart components and diagrams), and gives its name. Then read its AGENTS.md (read_artifact_file) once, which "
    "says what's there and how to use it; write the page in app/page.tsx with write_artifact_file or "
    "edit_artifact_file; then `publish_artifact(name)`, which builds it and gives the link, or the error to fix "
    "(the build checks the types, so it names the file and line). Publish again after each change. To change a page "
    "made earlier in this chat, `list_artifacts` names them. Put the data in the page itself; it has no server. "
    "Give the user the link when it's published."
)


def enabled() -> bool:
    return backend is not None


def _refusal(config: RunnableConfig | None) -> str | None:
    if backend is None or not ((config or {}).get("configurable") or {}).get("artifacts"):
        return "Error: web pages can't be built here."
    return None


def _run(config: RunnableConfig | None, action: str, *args) -> str:
    if refused := _refusal(config):
        return refused
    try:
        return getattr(backend, action)(config, *args)
    except ValueError as e:
        return f"Error: {e}"


def _checked(path: str, content: str, written: str) -> str:
    if problem := syntax_problem(path, content):
        return f"{written}\nBut it has a syntax error now, so the build would fail: fix it.\n" + with_lines(problem, content)
    return written


@tool
def new_artifact(title: str, config: RunnableConfig = None) -> str:
    """Make a new web page from the template, and get its name, which the other artifact tools take.

    Args:
        title: what the page is, in a few words, e.g. "Sales dashboard Q3"
    """
    return _run(config, "make", title)


@tool
def list_artifacts(config: RunnableConfig = None) -> str:
    """The web pages made in this chat: each one's name, title, and its link if it's published."""
    return _run(config, "listing")


@tool
def list_artifact_files(name: str, config: RunnableConfig = None) -> str:
    """List a page's files with their sizes.

    Args:
        name: the artifact's name, as new_artifact gave it
    """
    return _run(config, "files", name)


@tool
def read_artifact_file(name: str, path: str, start_line: int | None = None, end_line: int | None = None,
                       config: RunnableConfig = None) -> str:
    """Read one of a page's files, each line after its number (`12| `), which isn't part of the file.

    Args:
        name: the artifact's name
        path: the file's path in it, e.g. "AGENTS.md" or "app/page.tsx"
        start_line: the first line to show (from 1); leave out for the start
        end_line: the last line to show; leave out for the end
    """
    if refused := _refusal(config):
        return refused
    try:
        content = backend.read_text(config, name, path)
    except ValueError as e:
        return f"Error: {e}"
    if not content:
        return f"{path} is empty."
    total = len(content.split("\n")) - (1 if content.endswith("\n") else 0)
    start, end = start_line or 1, end_line or total
    if start > total:
        return f"Error: {path} has {total} lines, so there's no line {start}."
    head = f"{path}, lines {start}-{min(end, total)} of {total}:" if (start_line or end_line) else f"{path} ({total} lines):"
    return cut(f"{head}\n{numbered(content, start, end)}")


@tool
def write_artifact_file(name: str, path: str, content: str, config: RunnableConfig = None) -> str:
    """Write a whole file of a page: a new one, or one to rewrite completely (like app/page.tsx).

    Args:
        name: the artifact's name
        path: the file's path in it, e.g. "app/page.tsx" or "data/sales.ts"; folders are made as needed
        content: everything the file should contain
    """
    content = unnumbered(content)
    written = _run(config, "write", name, path, content)
    return written if written.startswith("Error:") else _checked(path, content, written)


@tool
def edit_artifact_file(name: str, path: str, old_text: str, new_text: str, replace_all: bool = False,
                       config: RunnableConfig = None) -> str:
    """Change part of one of a page's files: `old_text`, which must be in it exactly once (or every time,
    with replace_all), becomes `new_text`.

    Args:
        name: the artifact's name
        path: the file's path in it
        old_text: the exact text to replace, with enough around it to be found only once
        new_text: what goes in its place
        replace_all: replace every time old_text is in the file
    """
    if refused := _refusal(config):
        return refused
    if not old_text:
        return "Error: old_text can't be empty: use write_artifact_file for a whole file"
    old_text, new_text = unnumbered(old_text), unnumbered(new_text)
    if old_text == new_text:
        return "Error: new_text is the same as old_text, so nothing would change"
    try:
        current = backend.read_text(config, name, path)
        count, note = current.count(old_text), ""
        if count:
            changed = current.replace(old_text, new_text)
        else:
            spans = loose_spans(current, old_text)
            count = len(spans)
            if not count:
                return f"Error: that text isn't in {path}. Copy old_text exactly from the file, or read it again." + closest(current, old_text)
            changed = current
            for begin, end in reversed(spans if replace_all else spans[:1]):
                changed = changed[:begin] + new_text + changed[end:]
            note = " (old_text matched once spaces at the ends of lines were left out)"
        if count > 1 and not replace_all:
            return f"Error: that text is in {path} {count} times: give more around it, or replace_all"
        return _checked(path, changed, backend.write(config, name, path, changed) + note)
    except ValueError as e:
        return f"Error: {e}"


@tool
def delete_artifact_file(name: str, path: str, config: RunnableConfig = None) -> str:
    """Delete one of a page's files.

    Args:
        name: the artifact's name
        path: the file's path in it
    """
    return _run(config, "delete", name, path)


@tool
def publish_artifact(name: str, config: RunnableConfig = None) -> str:
    """Build the page and publish it: gives its link, or what failed (with the file's lines) to fix first. Takes
    10 to 30 seconds. Publish again after each change; the link stays the same.

    Args:
        name: the artifact's name
    """
    return _run(config, "publish", name)


TOOLS = [new_artifact, list_artifacts, list_artifact_files, read_artifact_file, write_artifact_file, edit_artifact_file,
         delete_artifact_file, publish_artifact]
