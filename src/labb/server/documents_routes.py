"""The documents endpoints (see documents.py): add, list and delete your documents, and search
them, which is what labb's `search_documents` tool calls from other PCs."""

import base64
import binascii

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from labb.server.auth_routes import current_user
from labb.server import agents, documents

router = APIRouter()


class Upload(BaseModel):
    name: str
    # The file, as base64
    data: str
    # When you have a document of that name: add another, replace its text, or refuse (400)
    existing: Literal["add", "replace", "refuse"] = "add"


class SearchRequest(BaseModel):
    query: str
    k: int = Field(5, ge=1, le=20)


@router.get("/documents")
def list_documents(user: dict = Depends(current_user)):
    """Your documents, newest first, with their status: indexing, ready or error"""
    return documents.list_documents(user["id"])


@router.post("/documents", status_code=201)
def add_document(upload: Upload, user: dict = Depends(current_user)):
    """Add a PDF, Word document or text file; it's searchable once its status is `ready`.
    The answer's `replaced` says whether it replaced one of the same name (`existing`)."""

    try:
        data = base64.b64decode(upload.data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, f"{upload.name} isn't valid base64")
    try:
        return documents.save(user["id"], upload.name, data, upload.existing)
    except documents.DocumentError as e:
        raise HTTPException(400, str(e))


@router.get("/documents/{doc_id}/text")
def document_text(doc_id: str, user: dict = Depends(current_user)):
    """One of your documents' text, page by page, to read (the Documents page opens a passage the chat cited)"""

    found = documents.pages_of(user["id"], doc_id)
    if found is None:
        raise HTTPException(404, "No such document")
    return found


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(doc_id: str, user: dict = Depends(current_user)):
    if not documents.delete(user["id"], doc_id):
        raise HTTPException(404, "No such document")
    agents.remove_document(user["id"], doc_id)
    return Response(status_code=204)


@router.post("/documents/search")
def search_documents(request: SearchRequest, user: dict = Depends(current_user)):
    """The passages of your documents closest to `query`, best first"""
    return documents.search(user["id"], request.query, request.k)
