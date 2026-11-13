"""File tools for the terminal (labb). They only work inside its working directory."""

import fnmatch
import os
import re
from pathlib import Path

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig

from labb import permissions

# Only offered to the model when there's a working directory (see tools/__init__.py)
NEEDS_WORKDIR = True

MAX_ENTRIES = 500
# read_file: lines per call, and characters, so a few long lines can't fill the context
MAX_LINES = 2000
MAX_READ_CHARS = 50_000
MAX_LINE_CHARS = 2000
# grep: matches per call, characters shown of each, and files too big to search
MAX_MATCHES = 200
MAX_MATCH_CHARS = 300
MAX_GREP_BYTES = 1_000_000
# Skipped when listing recursively: big, generated, rarely what you're looking for
SKIPPED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".cache",
}


def resolve(config: RunnableConfig, path: str) -> tuple[Path, Path]:
    """The working directory and `path` inside it. Raises ValueError for paths outside it,
    including through symlinks."""

    workdir = (config.get("configurable") or {}).get("workdir")
    if not workdir:
        raise ValueError("file tools only work in the terminal (labb), which has a working directory")
    root = Path(workdir).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"{path} is outside the working directory")
    return root, target


def ask_to_read_secret(path: str, target: Path, config: RunnableConfig) -> str | None:
    """None if `target` may be read, otherwise why not (for the model). A file that may hold
    secrets (permissions.is_secret) is read only with the user's yes, asked every time."""

    configurable = config.get("configurable") or {}
    if not permissions.is_secret(target) or configurable.get("allow_all"):
        return None
    approve = configurable.get("approve")
    if approve is None:
        return f"Error: {path} may hold secrets, and reading it needs the user's approval, which isn't possible here"
    answer = approve(f"read_file {path}", [], f"{permissions.READS}a file that may hold secrets ({path})", "Read this file?")
    if answer in ("yes", "always"):
        return None
    reason = answer[1] if isinstance(answer, tuple) and len(answer) > 1 and answer[1] else None
    said = f" They said: {reason.rstrip('.')}." if reason else ""
    return f"Not read: the user didn't allow reading {path}, which may hold secrets.{said}"


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


@tool
def list_files(path: str = ".", recursive: bool = False, config: RunnableConfig = None) -> str:
    """List the files and folders in a folder of the working directory.

    Folders end with `/`; files show their size. Paths are relative to the working directory.

    Args:
        path: Folder to list, relative to the working directory (default: the working directory itself)
        recursive: Also list everything in subfolders, skipping folders like .git, node_modules and .venv
    """
    try:
        root, target = resolve(config, path)
    except ValueError as e:
        return f"Error: {e}"
    if not target.exists():
        return f"Error: {path} doesn't exist"
    if not target.is_dir():
        return f"Error: {path} is a file, not a folder"

    lines = []
    skipped = []

    def add(entry: Path):
        relative = entry.relative_to(root).as_posix()
        if entry.is_symlink():
            # Don't follow it: it can point outside the working directory
            lines.append(f"{relative}  (link)")
        elif entry.is_dir():
            lines.append(f"{relative}/")
        else:
            try:
                lines.append(f"{relative}  ({human_size(entry.stat().st_size)})")
            except OSError:
                lines.append(relative)

    def sorted_entries(folder: Path):
        # Folders first, then files, both alphabetically
        return sorted(folder.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))

    try:
        if not recursive:
            for entry in sorted_entries(target):
                add(entry)
        else:
            for folder, dirs, _ in os.walk(target):
                folder = Path(folder)
                skipped += [(folder / d).relative_to(root).as_posix() + "/" for d in dirs if d in SKIPPED_DIRS]
                dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS)
                for entry in sorted_entries(folder):
                    if not (entry.is_dir() and entry.name in SKIPPED_DIRS):
                        add(entry)
                if len(lines) > MAX_ENTRIES:
                    break
    except PermissionError as e:
        return f"Error: no permission to read {e.filename}"

    if not lines:
        return f"{path} is empty"
    result = lines[:MAX_ENTRIES]
    if len(lines) > MAX_ENTRIES:
        result.append(f"... more than {MAX_ENTRIES} entries; list a subfolder to see the rest")
    if skipped:
        result.append(f"(not listed: {', '.join(skipped)})")
    return "\n".join(result)


def is_binary(path: Path) -> bool:
    """Files with a NUL byte near the start, like images and compiled files"""
    with open(path, "rb") as f:
        return b"\0" in f.read(8192)


def walk_files(root: Path, target: Path):
    """The files under `target` (or `target` itself), skipping folders like .git and
    node_modules and not following symlinks, with their paths relative to `root`"""

    if target.is_file():
        yield target, target.relative_to(root).as_posix()
        return
    for folder, dirs, files in os.walk(target):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS)
        for name in sorted(files):
            entry = Path(folder) / name
            if not entry.is_symlink():
                yield entry, entry.relative_to(root).as_posix()


@tool
def read_file(path: str, offset: int = 1, limit: int = MAX_LINES, config: RunnableConfig = None) -> str:
    """Read a text file of the working directory, with line numbers.

    Long files are read in parts: the result says which line to continue from.

    Args:
        path: File to read, relative to the working directory
        offset: Line number to start from (the first line is 1)
        limit: How many lines to read, at most 2000
    """
    try:
        _, target = resolve(config, path)
    except ValueError as e:
        return f"Error: {e}"
    if not target.exists():
        return f"Error: {path} doesn't exist"
    if target.is_dir():
        return f"Error: {path} is a folder; use list_files to see what's in it"
    if refused := ask_to_read_secret(path, target, config):
        return refused

    try:
        if is_binary(target):
            return f"Error: {path} is a binary file"
        lines = target.read_text(errors="replace").splitlines()
    except PermissionError:
        return f"Error: no permission to read {path}"
    if not lines:
        return f"{path} is empty"

    start = max(offset, 1)
    if start > len(lines):
        return f"Error: {path} has only {len(lines)} lines"
    end = min(start + max(min(limit, MAX_LINES), 1), len(lines) + 1)

    result = []
    chars = 0
    for number in range(start, end):
        line = lines[number - 1]
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + " … (line cut)"
        chars += len(line)
        if chars > MAX_READ_CHARS and result:
            end = number
            break
        result.append(f"{number:>6}\t{line}")
    if end <= len(lines):
        result.append(f"… {len(lines) - end + 1} more lines; read again with offset={end} to see them")
    text = "\n".join(result)
    if (number := still_seen(config, path, text)) is not None:
        return f"Unchanged since you read it at #{number}, which is still in your context above: use that."
    return text


# A read is answered "unchanged" only when it's worth it, and when the earlier one wasn't cut to fit
SAME_READ_MIN_CHARS = 1_000
SAME_READ_MAX_CHARS = 20_000


def still_seen(config: RunnableConfig | None, path: str, text: str) -> int | None:
    """The number of an earlier read_file result of this path with exactly this text that the model
    still gets in full (not summarized or cleared, see context.py), if there is one"""

    configurable = (config or {}).get("configurable") or {}
    history, cleared_before = configurable.get("history"), configurable.get("cleared_before")
    if not history or cleared_before is None or not SAME_READ_MIN_CHARS <= len(text) <= SAME_READ_MAX_CHARS:
        return None
    paths = {call["id"]: (call.get("args") or {}).get("path") for message in history if message.type == "ai"
             for call in message.tool_calls if call["name"] == "read_file"}
    for i in range(len(history) - 1, max(configurable.get("summarized") or 0, cleared_before) - 1, -1):
        message = history[i]
        if message.type == "tool" and message.name == "read_file" and message.content == text \
                and paths.get(message.tool_call_id) == path:
            return i
    return None


@tool
def glob(pattern: str, path: str = ".", config: RunnableConfig = None) -> str:
    """Find files in the working directory by name, for example `**/*.py` or `src/**/test_*.ts`.

    Skips folders like .git, node_modules and .venv.

    Args:
        pattern: Glob pattern, relative to `path`; `**` matches any number of folders
        path: Folder to search in, relative to the working directory (default: the working directory)
    """
    try:
        root, target = resolve(config, path)
    except ValueError as e:
        return f"Error: {e}"
    if not target.is_dir():
        return f"Error: {path} isn't a folder"

    # `**` also matches no folder at all, so `**/*.py` finds top-level files too
    matches = [
        relative for entry, relative in walk_files(root, target)
        if entry.relative_to(target).full_match(pattern)
    ]

    if not matches:
        return f"No files match {pattern}"
    result = matches[:MAX_ENTRIES]
    if len(matches) > MAX_ENTRIES:
        result.append(f"... {len(matches) - MAX_ENTRIES} more files; use a narrower pattern")
    return "\n".join(result)


@tool
def grep(pattern: str, path: str = ".", glob: str | None = None, ignore_case: bool = False, config: RunnableConfig = None) -> str:
    """Search the contents of the files in the working directory with a regular expression.

    Returns the matching lines as `file:line: text`. Skips binary files and folders like
    .git, node_modules and .venv.

    Args:
        pattern: Python regular expression, for example `def \\w+_file` or `TODO`
        path: File or folder to search, relative to the working directory (default: the working directory)
        glob: Only search files whose name matches this, for example `*.py`
        ignore_case: Match upper and lower case alike
    """
    try:
        root, target = resolve(config, path)
    except ValueError as e:
        return f"Error: {e}"
    if not target.exists():
        return f"Error: {path} doesn't exist"
    try:
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        return f"Error: invalid regular expression: {e}"

    matches = []
    secrets = []
    for entry, relative in walk_files(root, target):
        if glob and not (fnmatch.fnmatch(entry.name, glob) or fnmatch.fnmatch(relative, glob)):
            continue
        if permissions.is_secret(entry.resolve()):
            secrets.append(relative)
            continue
        try:
            if entry.stat().st_size > MAX_GREP_BYTES or is_binary(entry):
                continue
            text = entry.read_text(errors="replace")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                line = line.strip()
                if len(line) > MAX_MATCH_CHARS:
                    line = line[:MAX_MATCH_CHARS] + " …"
                matches.append(f"{relative}:{number}: {line}")
        if len(matches) > MAX_MATCHES:
            break

    skipped = f"\n(Not searched, since they may hold secrets: {', '.join(secrets)}; read_file asks the user first.)" if secrets else ""
    if not matches:
        return f"No matches for {pattern}{skipped}"
    result = matches[:MAX_MATCHES]
    if len(matches) > MAX_MATCHES:
        result.append(f"... more than {MAX_MATCHES} matches; narrow the pattern, path or glob")
    return "\n".join(result) + skipped


RUN_TOGETHER = ("list_files", "read_file", "glob", "grep")
TOOLS = [list_files, read_file, glob, grep]

PROMPT = (
    "You can look at the files in the working directory: `list_files` lists a folder, `glob` "
    "finds files by name, `grep` searches their contents and `read_file` reads one. Paths are "
    "relative to the working directory, and you can't access anything outside it. Look at the "
    "files instead of guessing what's there.\n\n"
    "When you work on code: find the relevant files with `glob` and `grep`, and read them before "
    "you change anything. Keep changes small, and check your work by running the project's tests "
    "or the program when there's a way to."
)
