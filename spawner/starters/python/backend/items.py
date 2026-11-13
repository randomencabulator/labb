"""The items API, on the items table (db/migrations/0001_items.sql): an example to copy for each
thing the app keeps, or to change into the first one.

    GET    /api/items         every item, oldest first
    POST   /api/items         {"title"} → the new item (201)
    PATCH  /api/items/{id}    {"title"?, "done"?} → the item
    DELETE /api/items/{id}    (204)
"""

from uuid import uuid4

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

import db

router = APIRouter(prefix="/api/items")


class NewItem(BaseModel):
    title: str


class ItemChange(BaseModel):
    title: str | None = None
    done: bool | None = None


def found(item_id: str) -> dict:
    item = db.one("SELECT * FROM items WHERE id = ?", item_id)
    if item is None:
        raise HTTPException(404, "No such item")
    return item


@router.get("")
def list_items():
    return db.query("SELECT * FROM items ORDER BY created_at, id")


@router.post("", status_code=201)
def add_item(new: NewItem):
    title = new.title.strip()
    if not title:
        raise HTTPException(400, "An item needs a title")
    item_id = uuid4().hex
    db.execute("INSERT INTO items (id, title) VALUES (?, ?)", item_id, title)
    return found(item_id)


@router.patch("/{item_id}")
def change_item(item_id: str, change: ItemChange):
    item = found(item_id)
    title = item["title"] if change.title is None else change.title.strip()
    if not title:
        raise HTTPException(400, "An item needs a title")
    done = item["done"] if change.done is None else change.done
    db.execute("UPDATE items SET title = ?, done = ? WHERE id = ?", title, done, item_id)
    return found(item_id)


@router.delete("/{item_id}", status_code=204)
def remove_item(item_id: str):
    if not db.execute("DELETE FROM items WHERE id = ?", item_id):
        raise HTTPException(404, "No such item")
    return Response(status_code=204)
