"""Tools that change files in the terminal's working directory.

Each change is shown as a diff and needs the user's approval through the `approve`
function in the run's config, like commands; "always" allows edits in the project
from then on (the rule `edits` in .labb/permissions.json or the settings).
/undo puts the files back from the snapshot taken before the message.
"""

import difflib

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig

from labb import permissions
from labb.tools.files import resolve

NEEDS_WORKDIR = True

# The permission rule that allows every edit without asking
RULE = "edits"
MAX_FILE_CHARS = 1_000_000
DIFF_CONTEXT = 3


def read_text(path) -> str:
    """The file's text exactly as it is (line endings too). Raises ValueError for binary files."""
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        raise ValueError("it's a binary file")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("it isn't UTF-8 text") from None


def diff(path: str, old: str, new: str) -> str:
    lines = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                 f"a/{path}", f"b/{path}", n=DIFF_CONTEXT)
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines).rstrip("\n")


def counts(old: str, new: str) -> str:
    """"+3 -1" lines"""
    added = removed = 0
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        added += line.startswith("+ ")
        removed += line.startswith("- ")
    return f"+{added} -{removed}"


def ask_to_change(path: str, old: str, new: str, workdir: str, configurable: dict, title: str) -> str | None:
    """None if the change may be made, otherwise why not (for the model)"""

    if configurable.get("mode") == "plan":
        return "Not changed: you're in plan mode. Don't change anything yet; finish your plan first."
    # Accept-edits mode (Shift-Tab): edits go through; they're inside the working directory (resolve)
    if configurable.get("allow_all") or configurable.get("mode") == "acceptEdits" or RULE in permissions.allowed(workdir):
        return None
    approve = configurable.get("approve")
    if approve is None:
        return (
            "Not changed: editing files needs the user's approval, which isn't possible here. "
            f"Run labb with --allow-all, or add \"{RULE}\" to permissions.allow in the settings."
        )
    answer = approve(diff(path, old, new) or f"(no changes to {path})", [RULE], None, title)
    if answer == "always":
        permissions.allow(workdir, [RULE])
    if answer in ("yes", "always"):
        return None
    reason = answer[1] if isinstance(answer, tuple) and len(answer) > 1 and answer[1] else None
    said = f" They said: {reason.rstrip('.')}." if reason else ""
    return f"Not changed: the user didn't allow this edit.{said}"


@tool
def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False, config: RunnableConfig = None) -> str:
    """Change a file by replacing exact text in it. Read the file first, and copy `old_string`
    exactly from it (indentation included, without read_file's line numbers).

    Args:
        path: File to change, relative to the working directory
        old_string: The exact text to replace; include enough lines around it to be unique
        new_string: The text to put in its place (empty to delete it)
        replace_all: Replace every occurrence instead of exactly one
    """
    try:
        root, target = resolve(config, path)
    except ValueError as e:
        return f"Error: {e}"
    if not target.is_file():
        return f"Error: {path} doesn't exist; use write_file to create it"
    if not old_string:
        return "Error: old_string is empty; to write a whole file, use write_file"
    if old_string == new_string:
        return "Error: old_string and new_string are the same"
    try:
        old = read_text(target)
    except (OSError, ValueError) as e:
        return f"Error: can't edit {path}: {e}"

    # Files with Windows line endings: match and replace with them
    crlf = "\r\n" in old and "\r\n" not in old_string
    if crlf:
        old_string, new_string = old_string.replace("\n", "\r\n"), new_string.replace("\n", "\r\n")
    found = old.count(old_string)
    if found == 0:
        return (f"Error: old_string isn't in {path}. Read the file again and copy the text exactly, "
                "with its indentation and without the line numbers.")
    if found > 1 and not replace_all:
        return (f"Error: old_string is in {path} {found} times. Include more lines around it so it's "
                "unique, or set replace_all to change every one.")
    new = old.replace(old_string, new_string) if replace_all else old.replace(old_string, new_string, 1)

    configurable = config.get("configurable") or {}
    if refusal := ask_to_change(path, old, new, str(root), configurable, f"Edit {path}?"):
        return refusal
    target.write_bytes(new.encode("utf-8"))
    times = f" ({found} places)" if replace_all and found > 1 else ""
    return f"Edited {path}: {counts(old, new)} lines{times}"


@tool
def write_file(path: str, content: str, config: RunnableConfig = None) -> str:
    """Create a file, or replace all of one, with `content`. For changes to part of an existing
    file, use edit_file instead. Folders on the way are created.

    Args:
        path: File to write, relative to the working directory
        content: The whole new content of the file
    """
    try:
        root, target = resolve(config, path)
    except ValueError as e:
        return f"Error: {e}"
    if target.is_dir():
        return f"Error: {path} is a folder"
    if len(content) > MAX_FILE_CHARS:
        return f"Error: content is over {MAX_FILE_CHARS:,} characters"
    exists = target.exists()
    try:
        old = read_text(target) if exists else ""
    except (OSError, ValueError) as e:
        return f"Error: can't replace {path}: {e}"

    configurable = config.get("configurable") or {}
    title = f"Overwrite {path}?" if exists else f"Create {path}?"
    if refusal := ask_to_change(path, old, content, str(root), configurable, title):
        return refusal
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content.encode("utf-8"))
    if exists:
        return f"Wrote {path}: {counts(old, content)} lines"
    return f"Created {path}: {len(content.splitlines())} lines"


TOOLS = [edit_file, write_file]

PROMPT = (
    "Change files with `edit_file` (replace exact text; read the file first and copy the text "
    "exactly) and create new ones with `write_file`, not with `run_command`. Prefer several small "
    "edits to rewriting a whole file. Each change is shown to the user as a diff to approve; if "
    "they say no, don't make it another way."
)
