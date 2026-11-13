"""The memories endpoints (see memories.py): yours to see and change in Settings → Memories,
and what labb on other PCs reads, writes and recalls them with. Nobody else sees them,
admins included."""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from labb.server.auth_routes import current_user
from labb import memories

router = APIRouter()


class NewMemory(BaseModel):
    text: str
    pinned: bool = False
    # False for what the agent saves: it waits for the user to keep it
    reviewed: bool = True


class MemoryChange(BaseModel):
    text: str | None = None
    pinned: bool | None = None
    reviewed: bool | None = None


class RecallRequest(BaseModel):
    query: str
    k: int = Field(memories.RECALL, ge=1, le=20)
    min_score: float = Field(memories.MIN_SCORE, ge=0, le=1)
    # Also the pinned ones (labb checks for similar memories before saving one)
    pinned: bool = False


@router.get("/memories")
def list_memories(user: dict = Depends(current_user)):
    """Your memories, pinned first, then newest first"""
    return memories.list_for(user["id"])


@router.post("/memories", status_code=201)
def add_memory(memory: NewMemory, user: dict = Depends(current_user)):
    """Save a memory; one that's saved already comes back with `existed`"""
    try:
        return memories.add_for(user["id"], memory.text, memory.pinned, memory.reviewed)
    except memories.MemoryError as e:
        raise HTTPException(400, str(e))


@router.patch("/memories/{memory_id}")
def update_memory(memory_id: str, change: MemoryChange, user: dict = Depends(current_user)):
    try:
        memory = memories.update_for(user["id"], memory_id, change.text, change.pinned, change.reviewed)
    except memories.MemoryError as e:
        raise HTTPException(400, str(e))
    if memory is None:
        raise HTTPException(404, "No such memory")
    return memory


@router.delete("/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: str, user: dict = Depends(current_user)):
    if not memories.delete_for(user["id"], memory_id):
        raise HTTPException(404, "No such memory")
    return Response(status_code=204)


@router.delete("/memories")
def delete_all_memories(user: dict = Depends(current_user)):
    """Forget everything"""
    return {"forgotten": memories.delete_for(user["id"])}


@router.post("/memories/recall")
def recall(request: RecallRequest, user: dict = Depends(current_user)):
    """Your memories closest to `query`, best first, with their `score`"""
    return memories.recall_for(user["id"], request.query, request.k, request.min_score, request.pinned)
