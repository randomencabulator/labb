"""The app's server: its JSON API, under /api/. The page (frontend/) isn't served here: it's built
and served apart, and its calls to /api/… come here. See AGENTS.md.

Each part of the API is a module like items.py (an APIRouter), included below."""

import os

from fastapi import FastAPI

import db
import items

# The tables, before anything asks for them (db/migrations/)
db.migrate()

app = FastAPI(title=os.environ.get("APP_NAME", "App"), docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
app.include_router(items.router)


@app.get("/api/health")
def health():
    """Whether the server answers: labb asks after each build"""
    return {"ok": True}
