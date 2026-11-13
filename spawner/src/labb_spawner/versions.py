"""Each app's files under version control: a git repository of their own (Dulwich: git in Python,
no git program needed), kept next to them (SPAWNER_DATA/apps/<id>/git, bare), so a build never
sees it. A version is saved at each build ("Build 3"), when the app is made, and when one is
restored, and only when the files changed. The history is a plain git one: it can be cloned out
of the folder with any git, and `pack_source` gives the files with it as a .git, as a zip.

Restoring a version puts its files back as they were (files it hadn't go) and saves that as a
new version, so a restore can be undone too. Files are only ever written inside the app's folder.
"""

import io
import shutil
import stat
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from dulwich.index import build_index_from_tree
from dulwich.objects import Blob, Commit, Tree
from dulwich.patch import write_tree_diff
from dulwich.repo import Repo

from labb_spawner import sources

AUTHOR = b"labb <labb@labb.local>"
BRANCH = b"refs/heads/main"
MAX_DIFF = 200_000
MAX_VERSIONS = 200

_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _lock(app_id: str) -> threading.Lock:
    with _locks_lock:
        return _locks.setdefault(app_id, threading.Lock())


def _path(app_id: str) -> Path:
    return sources.folder(app_id) / "git"


def _repo(app_id: str) -> Repo:
    path = _path(app_id)
    if not (path / "HEAD").exists():
        path.mkdir(parents=True, exist_ok=True)
        repo = Repo.init_bare(str(path))
        repo.refs.set_symbolic_ref(b"HEAD", BRANCH)
        return repo
    return Repo(str(path))


def _head(repo: Repo) -> bytes | None:
    try:
        return repo.refs[BRANCH]
    except KeyError:
        return None


def _tree_of(repo: Repo, folder: Path) -> bytes:
    """The folder as a git tree (its files' contents stored), links and empty folders left out"""
    tree = Tree()
    for entry in sorted(folder.iterdir()):
        if entry.is_symlink():
            continue
        if entry.is_dir():
            sub = _tree_of(repo, entry)
            if repo[sub].items():
                tree.add(entry.name.encode(), stat.S_IFDIR, sub)
        elif entry.is_file():
            blob = Blob.from_string(entry.read_bytes())
            repo.object_store.add_object(blob)
            mode = 0o100755 if entry.stat().st_mode & 0o111 else 0o100644
            tree.add(entry.name.encode(), mode, blob.id)
    repo.object_store.add_object(tree)
    return tree.id


def _work_tree(app_id: str, repo: Repo) -> bytes:
    root = sources.source(app_id)
    return _tree_of(repo, root) if root.is_dir() else Tree().id


def save(app_id: str, message: str) -> str | None:
    """A version of the app's files as they are now; None if they're as the last one"""
    if not sources.source(app_id).is_dir():
        return None
    with _lock(app_id):
        repo = _repo(app_id)
        tree = _work_tree(app_id, repo)
        head = _head(repo)
        # Nothing changed; or nothing at all, for a first version
        if (head is not None and repo[head].tree == tree) or (head is None and not repo[tree].items()):
            return None
        commit = Commit()
        commit.tree = tree
        commit.parents = [head] if head is not None else []
        commit.author = commit.committer = AUTHOR
        commit.author_time = commit.commit_time = int(time.time())
        commit.author_timezone = commit.commit_timezone = 0
        commit.message = message.encode()
        repo.object_store.add_object(commit)
        repo.refs[BRANCH] = commit.id
        return commit.id.decode()


def _changed(repo: Repo, old_tree: bytes | None, new_tree: bytes) -> list[str]:
    from dulwich.diff_tree import tree_changes
    # A deleted file has no new side, an added one no old side
    side = lambda change: change.new if change.new is not None and change.new.path else change.old
    return sorted({side(c).path.decode() for c in tree_changes(repo.object_store, old_tree, new_tree)})


def history(app_id: str) -> dict:
    """The versions, newest first (`files`: which it changed), and whether the files changed since the last"""
    with _lock(app_id):
        repo = _repo(app_id)
        head = _head(repo)
        found = []
        current = head
        while current is not None and len(found) < MAX_VERSIONS:
            commit = repo[current]
            parent = commit.parents[0] if commit.parents else None
            found.append({
                "id": current.decode(), "message": commit.message.decode("utf-8", "replace").strip(),
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(commit.commit_time)),
                "files": _changed(repo, repo[parent].tree if parent else None, commit.tree),
            })
            current = parent
        unsaved = []
        if head is not None:
            unsaved = _changed(repo, repo[head].tree, _work_tree(app_id, repo))
        return {"versions": found, "unsaved": unsaved}


def _commit(repo: Repo, version: str):
    try:
        found = repo[version.encode()]
    except (KeyError, ValueError):
        found = None
    if not isinstance(found, Commit):
        raise KeyError(version)
    return found


def _diff(repo: Repo, old_tree: bytes | None, new_tree: bytes) -> str:
    out = io.BytesIO()
    write_tree_diff(out, repo.object_store, old_tree, new_tree)
    text = out.getvalue().decode("utf-8", "replace")
    return text if len(text) <= MAX_DIFF else text[:MAX_DIFF] + f"\n… ({len(text) - MAX_DIFF:,} more characters not shown)\n"


def diff(app_id: str, version: str) -> str:
    """What a version changed, from the one before it"""
    with _lock(app_id):
        repo = _repo(app_id)
        commit = _commit(repo, version)
        parent = commit.parents[0] if commit.parents else None
        return _diff(repo, repo[parent].tree if parent else None, commit.tree)


def unsaved_diff(app_id: str) -> str:
    """What changed in the files since the last version (not built since)"""
    with _lock(app_id):
        repo = _repo(app_id)
        head = _head(repo)
        return _diff(repo, repo[head].tree if head else None, _work_tree(app_id, repo))


def _files_of(repo: Repo, tree_id: bytes, prefix: str = "") -> dict[str, bytes]:
    found = {}
    for entry in repo[tree_id].items():
        name = prefix + entry.path.decode()
        if stat.S_ISDIR(entry.mode):
            found.update(_files_of(repo, entry.sha, name + "/"))
        else:
            found[name] = repo[entry.sha].data
    return found


def restore(app_id: str, version: str) -> str | None:
    """The files as they were in `version`, saved as a new version; the new version's id. Changes
    not saved in a version yet are saved first, so going back never loses them."""
    save(app_id, "Changes not built yet, kept before going back")
    with _lock(app_id):
        repo = _repo(app_id)
        commit = _commit(repo, version)
        wanted = _files_of(repo, commit.tree)
        root = sources.source(app_id)
        for f in sources.files(app_id):
            if f["path"] not in wanted:
                sources.inside(app_id, f["path"]).unlink()
        for path, data in wanted.items():
            target = sources.inside(app_id, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        # Folders left empty go
        for folder in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            if not any(folder.iterdir()):
                folder.rmdir()
        message = commit.message.decode("utf-8", "replace").strip().splitlines()[0] if commit.message else ""
    return save(app_id, f"Back to {version[:8]} ({message})")


def pack_source(app_id: str, out, name: str):
    """The app's files as they are now, in a folder `name` with their history as its .git (a
    working copy, not bare: changes not built yet show in `git status`), as a zip written to `out`"""
    with tempfile.TemporaryDirectory(prefix="labb-source-") as folder:
        work = Path(folder) / "work"
        with _lock(app_id):
            head = _head(_repo(app_id))
            shutil.copytree(_path(app_id), work / ".git")
            root = sources.source(app_id)
            listed = sources.files(app_id)
            # The files from the last version first, for the index, then the ones there are now
            repo = Repo(str(work))
            config = repo.get_config()
            config.set((b"core",), b"bare", False)
            config.write_to_path()
            if head is not None:
                build_index_from_tree(str(work), repo.index_path(), repo.object_store, repo[head].tree)
                for entry in work.iterdir():
                    if entry.name != ".git":
                        shutil.rmtree(entry) if entry.is_dir() and not entry.is_symlink() else entry.unlink()
            for f in listed:
                target = work / f["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / f["path"], target)
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            # Folders too: git needs its empty ones (refs/tags, …)
            for path in sorted(work.rglob("*")):
                if not path.is_symlink():
                    archive.write(path, f"{name}/{path.relative_to(work).as_posix()}")
