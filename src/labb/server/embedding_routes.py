"""Settings → Models → Search (admins): which Ollama model embeds documents and memories (see
embedding.py), with the models to pick from and how far embedding everything again has got."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from labb.chats import conn, lock
from labb.server import embedding
from labb.server.auth_routes import admin_user

router = APIRouter()


class EmbeddingChange(BaseModel):
    model: str


def progress() -> dict:
    """How many documents and memories are in the current model's index, of how many"""

    index = embedding.index_id()
    with lock:
        documents = conn.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE embed_model = ? AND status <> 'indexing') "
                                 "FROM documents", (index,)).fetchone()
        memories = conn.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE embed_model = ?) FROM memories",
                                (index,)).fetchone()
    return {"documents": {"total": documents[0], "done": documents[1]}, "memories": {"total": memories[0], "done": memories[1]}}


@router.get("/admin/embedding")
def get_settings(user: dict = Depends(admin_user)):
    return {**embedding.settings(), "progress": progress()}


@router.put("/admin/embedding")
def save(body: EmbeddingChange, user: dict = Depends(admin_user)):
    """Use another model: checked on Ollama first, then documents and memories are embedded again"""
    try:
        embedding.save(body.model)
    except embedding.EmbeddingError as e:
        raise HTTPException(400, str(e))
    return get_settings(user)
