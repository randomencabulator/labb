"""The accounts' skills on the server (see skills.py for what a skill is): written in the web
chat's editor or uploaded as a SKILL.md or a zipped folder, private or shared with everyone.

- `skills` has one row per skill (its name and description come from its SKILL.md), and
  `skill_files` its text files, SKILL.md among them. Binary files aren't kept: a skill on the
  server is instructions and references, since the web chat can't run scripts.
- Names are unique per account; saving one with a name you have replaces it. When others
  shared a skill with the same name, yours wins, then the newest.
- labb on a PC lists and reads them through `/skills` with its API key.
"""

import io
import uuid
import zipfile
from pathlib import PurePosixPath

from labb import skills
from labb.chats import conn, lock, now
from labb.server import limits


with lock, conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS skills ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL, "
        "shared INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (user_id, name))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS skill_files ("
        "skill_id TEXT NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL, PRIMARY KEY (skill_id, path))"
    )

COLUMNS = "s.id, s.user_id, u.username, s.name, s.description, s.shared, s.created_at, s.updated_at"
FROM = "skills s LEFT JOIN users u ON u.id = s.user_id"


def _skill(row, viewer: str | None) -> dict:
    id, user_id, username, name, description, shared, created_at, updated_at = row
    return {"id": id, "owner_id": user_id, "owner": username, "mine": viewer is None or viewer == user_id,
            "name": name, "description": description, "shared": bool(shared),
            "created_at": created_at, "updated_at": updated_at}


def _paths(skill_id: str) -> list[str]:
    with lock:
        rows = conn.execute("SELECT path FROM skill_files WHERE skill_id = ? ORDER BY path != ?, path",
                            (skill_id, skills.SKILL_FILE)).fetchall()
    return [row[0] for row in rows]


def list_all(user_id: str) -> list[dict]:
    """Your skills, then the ones others shared, each newest first, with their files"""

    with lock:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM {FROM} WHERE s.user_id = ? OR s.shared = 1 ORDER BY s.user_id != ?, s.updated_at DESC",
            (user_id, user_id),
        ).fetchall()
    return [{**_skill(row, user_id), "files": _paths(row[0])} for row in rows]


def list_for(user_id: str) -> list[dict]:
    """The skills a run of `user_id` can use: one per name, yours first"""

    found, seen = [], set()
    for skill in list_all(user_id):
        if skill["name"] not in seen:
            seen.add(skill["name"])
            found.append(skill)
    return found


def get(user_id: str, skill_id: str) -> dict | None:
    """A skill you can see, with its files and its SKILL.md"""

    with lock:
        row = conn.execute(f"SELECT {COLUMNS} FROM {FROM} WHERE s.id = ?", (skill_id,)).fetchone()
    if row is None:
        return None
    skill = _skill(row, user_id)
    if not (skill["mine"] or skill["shared"]):
        return None
    return {**skill, "files": _paths(skill_id), "skill_md": _content(skill_id, skills.SKILL_FILE) or ""}


def _content(skill_id: str, path: str) -> str | None:
    with lock:
        row = conn.execute("SELECT content FROM skill_files WHERE skill_id = ? AND path = ?", (skill_id, path)).fetchone()
    return row[0] if row else None


def _by_name(user_id: str, name: str) -> dict | None:
    return next((s for s in list_for(user_id) if s["name"] == name), None)


def files(user_id: str, name: str) -> list[str] | None:
    skill = _by_name(user_id, name)
    return skill["files"] if skill else None


def read(user_id: str, name: str, path: str) -> str | None:
    skill = _by_name(user_id, name)
    return _content(skill["id"], str(PurePosixPath(path))) if skill else None


def _clean_path(path: str) -> str | None:
    """A file's path in the skill, or None for one to leave out (hidden, outside it)"""

    parts = [p for p in PurePosixPath(path.replace("\\", "/")).parts if p not in ("", ".")]
    if not parts or ".." in parts or any(p.startswith(".") or p == "__MACOSX" for p in parts):
        return None
    return "/".join(parts)


def from_zip(data: bytes) -> dict[str, str]:
    """A zipped skill folder's text files, by their path in the skill (SKILL.md at its top,
    also when the zip has the folder itself at its top); raises SkillError"""

    if len(data) > (most := limits.get("skill_zip_mb")) * 1_000_000:
        raise skills.SkillError(f"The zip is over {most} MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise skills.SkillError("That isn't a zip file")
    found = {}
    with archive:
        for info in archive.infolist():
            path = _clean_path(info.filename)
            if info.is_dir() or path is None or info.file_size > limits.get("skill_file_chars") * 4:
                continue
            raw = archive.read(info)
            if b"\0" in raw[:8000]:
                continue
            found[path] = raw.decode(errors="replace").replace("\0", "")
    tops = [p for p in found if PurePosixPath(p).name == skills.SKILL_FILE]
    if not tops:
        raise skills.SkillError("The zip has no SKILL.md")
    root = str(PurePosixPath(min(tops, key=lambda p: p.count("/"))).parent)
    if root == ".":
        return found
    prefix = root + "/"
    return {p[len(prefix):]: text for p, text in found.items() if p.startswith(prefix)}


def save(user_id: str, contents: dict[str, str], shared: bool | None = None, replace: bool = True) -> dict:
    """Add a skill from its files, or replace yours with the same name (unless not `replace`);
    raises SkillError"""

    contents = {path: text for p, text in contents.items() if (path := _clean_path(p))}
    if skills.SKILL_FILE not in contents:
        raise skills.SkillError("A skill needs a SKILL.md")
    meta, _ = skills.parse(contents[skills.SKILL_FILE])
    if len(contents) > (most := limits.get("skill_files")):
        raise skills.SkillError(f"A skill has at most {most} files")
    if any(len(text) > (most := limits.get("skill_file_chars")) for text in contents.values()):
        raise skills.SkillError(f"A file is over {most:,} characters")
    if sum(len(text) for text in contents.values()) > (most := limits.get("skill_chars")):
        raise skills.SkillError(f"The skill is over {most:,} characters")
    at = now()
    with lock, conn:
        row = conn.execute("SELECT id, shared FROM skills WHERE user_id = ? AND name = ?", (user_id, meta["name"])).fetchone()
        if row and not replace:
            raise skills.SkillError(f"You have a skill called {meta['name']} already")
        if row:
            skill_id = row[0]
            conn.execute("UPDATE skills SET description = ?, shared = ?, updated_at = ? WHERE id = ?",
                         (meta["description"], int(row[1] if shared is None else shared), at, skill_id))
            conn.execute("DELETE FROM skill_files WHERE skill_id = ?", (skill_id,))
        else:
            if conn.execute("SELECT COUNT(*) FROM skills WHERE user_id = ?", (user_id,)).fetchone()[0] >= (most := limits.get("skills_per_person")):
                raise skills.SkillError(f"You have {most} skills already; delete one first")
            skill_id = uuid.uuid4().hex[:12]
            conn.execute("INSERT INTO skills (id, user_id, name, description, shared, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (skill_id, user_id, meta["name"], meta["description"], int(bool(shared)), at, at))
        conn.executemany("INSERT INTO skill_files (skill_id, path, content) VALUES (?, ?, ?)",
                         [(skill_id, path, text) for path, text in contents.items()])
    skills.forget_cached()
    return get(user_id, skill_id)


def update(user_id: str, skill_id: str, skill_md: str | None = None, shared: bool | None = None) -> dict | None:
    """Change your skill's SKILL.md (its name too) or sharing; None if you have no such skill"""

    skill = get(user_id, skill_id)
    if skill is None or not skill["mine"]:
        return None
    if skill_md is not None:
        meta, _ = skills.parse(skill_md)
        with lock:
            taken = conn.execute("SELECT 1 FROM skills WHERE user_id = ? AND name = ? AND id != ?",
                                 (user_id, meta["name"], skill_id)).fetchone()
        if taken:
            raise skills.SkillError(f"You have another skill called {meta['name']}")
        with lock, conn:
            conn.execute("UPDATE skills SET name = ?, description = ?, updated_at = ? WHERE id = ?",
                         (meta["name"], meta["description"], now(), skill_id))
            conn.execute("INSERT INTO skill_files (skill_id, path, content) VALUES (?, ?, ?) "
                         "ON CONFLICT (skill_id, path) DO UPDATE SET content = excluded.content",
                         (skill_id, skills.SKILL_FILE, skill_md))
    if shared is not None:
        with lock, conn:
            conn.execute("UPDATE skills SET shared = ?, updated_at = ? WHERE id = ?", (int(shared), now(), skill_id))
    skills.forget_cached()
    return get(user_id, skill_id)


def delete(user_id: str, skill_id: str) -> bool:
    with lock, conn:
        deleted = conn.execute("DELETE FROM skills WHERE id = ? AND user_id = ?", (skill_id, user_id)).rowcount
        if deleted:
            conn.execute("DELETE FROM skill_files WHERE skill_id = ?", (skill_id,))
    skills.forget_cached()
    return bool(deleted)


def zip_of(user_id: str, skill_id: str) -> tuple[str, bytes] | None:
    """A skill you can see as a zip of its folder, to download"""

    skill = get(user_id, skill_id)
    if skill is None:
        return None
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in skill["files"]:
            archive.writestr(f"{skill['name']}/{path}", _content(skill_id, path) or "")
    return skill["name"], out.getvalue()


class ServerStore:
    """skills.store on the server"""

    list_for = staticmethod(list_for)
    save = staticmethod(save)
    files = staticmethod(files)
    read = staticmethod(read)
