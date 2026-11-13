"""Snapshots of labb's working directory, so /undo and /rewind can put files back.

Before each message, the files are saved in a separate git repository under
~/.labb/snapshots (one per directory), never in the
project's own .git. A snapshot is a git tree: unchanged files cost nothing. The
project's .gitignore files apply, and folders like node_modules and .venv are skipped.
"""

import hashlib
import subprocess
from pathlib import Path

from labb.tools.files import SKIPPED_DIRS

SNAPSHOTS_DIR = Path.home() / ".labb" / "snapshots"
# Saving a huge directory would hold up every message; give up on snapshots there
TIMEOUT_SECONDS = 15


class SnapshotError(Exception):
    pass


def git_dir(workdir: str) -> Path:
    return SNAPSHOTS_DIR / hashlib.sha1(workdir.encode()).hexdigest()[:16]


def git(workdir: str, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", f"--git-dir={git_dir(workdir)}", f"--work-tree={workdir}", *args],
            cwd=workdir, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise SnapshotError(f"saving the files took over {TIMEOUT_SECONDS}s")
    except OSError as e:
        raise SnapshotError(f"git isn't available: {e}")
    if result.returncode != 0:
        raise SnapshotError(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout


def can_snapshot(workdir: str) -> bool:
    """Not for the home folder or the root, which are too big to save before every message"""
    return Path(workdir) not in (Path.home(), Path("/"))


def take(workdir: str) -> str:
    """Save the working directory's files; returns the snapshot's id (a git tree)"""

    if not git_dir(workdir).exists():
        git_dir(workdir).parent.mkdir(parents=True, exist_ok=True)
        git(workdir, "init", "--quiet")
        (git_dir(workdir) / "info").mkdir(exist_ok=True)
        (git_dir(workdir) / "info" / "exclude").write_text("".join(f"{name}/\n" for name in sorted(SKIPPED_DIRS)))
        (git_dir(workdir) / "workdir").write_text(workdir + "\n")
    git(workdir, "add", "--all", ".")
    return git(workdir, "write-tree").strip()


def changes_since(workdir: str, snapshot: str) -> list[tuple[str, str]]:
    """The files changed since the snapshot, as (status, path): "A" added, "M" changed, "D" deleted"""

    now = take(workdir)
    changes = []
    for line in git(workdir, "diff-tree", "-r", "--no-renames", "--name-status", snapshot, now).splitlines():
        status, _, path = line.partition("\t")
        changes.append((status[:1], path))
    return changes


def restore(workdir: str, snapshot: str, changes: list[tuple[str, str]]):
    """Put the changed files back as they were in the snapshot"""

    root = Path(workdir)
    for status, path in changes:
        if status == "A":
            # Added since: remove it, and the folders it leaves empty
            (root / path).unlink(missing_ok=True)
            folder = (root / path).parent
            while folder != root and folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
                folder = folder.parent
    restored = [path for status, path in changes if status != "A"]
    if restored:
        git(workdir, "checkout", snapshot, "--", *restored)
