"""Notes on the server (see tools/notes.py): each person's own, which the web chat, tasks and
workflows keep as they work, and the Notes page shows; and each app's own, kept by its chat
(app_notes, by app, with its owner; apps_routes serves them and drops them with the app). The page
also shows and changes the notes of a connected PC's folders, which stay on the PC
(pcs.ask_notes). Nobody else sees someone's notes, admins included."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from labb.chats import conn, lock
from labb.server import pcs
from labb.server.auth_routes import current_user
from labb.server.pcs_routes import person
from labb.tools import notes

with lock, conn:
    conn.execute("CREATE TABLE IF NOT EXISTS notes (user_id TEXT PRIMARY KEY, text TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS app_notes (app_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, text TEXT NOT NULL, "
                 "updated_at TEXT NOT NULL)")


class Store:
    """The notes tools' `store`: a person's notes, or with `app_id` one of their apps', one Markdown text"""

    def read(self, user_id: str, app_id: str | None = None) -> str:
        with lock:
            if app_id:
                row = conn.execute("SELECT text FROM app_notes WHERE app_id = ? AND user_id = ?", (app_id, user_id)).fetchone()
            else:
                row = conn.execute("SELECT text FROM notes WHERE user_id = ?", (user_id,)).fetchone()
        return row[0] if row else ""

    def change(self, user_id: str, action: str, text: str = "", topic: str = "", base: str | None = None,
               app_id: str | None = None) -> str:
        """Change them in one transaction, so the agent and the page don't write over each other"""
        with lock, conn:
            if app_id:
                row = conn.execute("SELECT text, user_id FROM app_notes WHERE app_id = ?", (app_id,)).fetchone()
                # An app is one person's: never someone else's notes, even with its id
                if row and row[1] != user_id:
                    raise notes.NotesError("No such app.")
                new = notes.changed(row[0] if row else "", action, text, topic, base)
                conn.execute("INSERT INTO app_notes (app_id, user_id, text, updated_at) VALUES (?, ?, ?, ?) "
                             "ON CONFLICT (app_id) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at",
                             (app_id, user_id, new, pcs.stamp()))
                return new
            row = conn.execute("SELECT text FROM notes WHERE user_id = ?", (user_id,)).fetchone()
            new = notes.changed(row[0] if row else "", action, text, topic, base)
            conn.execute("INSERT INTO notes (user_id, text, updated_at) VALUES (?, ?, ?) "
                         "ON CONFLICT (user_id) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at",
                         (user_id, new, pcs.stamp()))
        return new

    def forget_app(self, app_id: str):
        """An app that was removed: its notes go with it"""
        with lock, conn:
            conn.execute("DELETE FROM app_notes WHERE app_id = ?", (app_id,))


store = Store()
router = APIRouter()


class Change(BaseModel):
    # "add" an entry (text under topic), or "replace" them all with text
    action: str
    text: str = ""
    topic: str = ""
    # The digest they had when read: a change over someone else's is refused (409)
    base: str | None = None


class PcChange(Change):
    folder: str


def shown(text: str) -> dict:
    return {"text": text, "digest": notes.digest(text), "topics": notes.headings(text) if text.strip() else []}


def changing(do):
    try:
        return shown(do())
    except notes.Conflict as e:
        raise HTTPException(409, str(e))
    except notes.NotesError as e:
        raise HTTPException(400, str(e))


@router.get("/notes")
def get_notes(user: dict = Depends(current_user)):
    """Your notes, with the digest to change them with"""
    return shown(store.read(user["id"]))


@router.post("/notes")
def change_notes(change: Change, user: dict = Depends(current_user)):
    return changing(lambda: store.change(user["id"], change.action, change.text, change.topic, change.base))


def from_pc(do):
    try:
        return changing(do)
    except LookupError:
        raise HTTPException(404, "No such PC")
    except pcs.PcConflict as e:
        raise HTTPException(409, str(e))
    except pcs.PcError as e:
        raise HTTPException(502, str(e))


@router.get("/pcs/{pc_id}/notes")
def get_pc_notes(pc_id: str, folder: str, user: dict = Depends(person)):
    """A folder's notes (.labb/notes.md), asked of the PC while it's connected"""
    return from_pc(lambda: pcs.ask_notes(user["id"], pc_id, folder))


@router.post("/pcs/{pc_id}/notes")
def change_pc_notes(pc_id: str, change: PcChange, user: dict = Depends(person)):
    return from_pc(lambda: pcs.ask_notes(user["id"], pc_id, change.folder, action=change.action, text=change.text,
                                         topic=change.topic, base=change.base))
