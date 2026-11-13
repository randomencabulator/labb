# The app's server

This file is for coding agents changing the server. It's FastAPI, in Python, serving the app's JSON
API under `/api/` on port 8080. It doesn't serve the page: `frontend/` is built and served apart, by
itself, and the page's `fetch("/api/…")` calls come here. Never add the page's files or a static
file server here.

## Files

| | |
|---|---|
| `main.py` | the app: includes each part of the API, and `/api/health` (keep it: labb asks it after each build) |
| `items.py` | the items API: **the example to copy** for each thing the app keeps |
| `db.py` | the database: `db.query`, `db.one`, `db.execute` (read its top) |
| `../db/migrations/*.sql` | the tables, made by migrations |
| `labb.py` | calls labb from the server, if the app may use it |
| `requirements.txt` | packages, with exact versions |
| `Dockerfile` | how the server is built: leave it as it is |

## Adding to the API

To keep something new (notes, say):
1. **A table**: `db/migrations/0002_notes.sql`, like `0001_items.sql`. Never change a migration
   that ran; write the next one. SQL that works in SQLite and Postgres: `id TEXT PRIMARY KEY` (made
   with `uuid4().hex`), `TEXT`, `INTEGER`, `BOOLEAN`, `TIMESTAMP … DEFAULT CURRENT_TIMESTAMP`.
2. **Its routes**: `notes.py`, a copy of `items.py` with the names changed: an
   `APIRouter(prefix="/api/notes")`, Pydantic models for what's sent, `db` calls with `?` for values.
3. **Include it** in `main.py`: `import notes` and `app.include_router(notes.router)`.
4. **The page** calls it with `api("/api/notes")` from `@/lib/api` (see `frontend/AGENTS.md`).

Then build the app, and check it with `look_at_app("/api/notes")`.

## Rules

- Every route's path starts with `/api/`: nothing else reaches the server.
- Say what's wrong with `raise HTTPException(400, "An item needs a title")`: the page shows `detail`.
- Values in SQL only as `?`: `db.one("SELECT * FROM notes WHERE id = ?", note_id)`.
- Files the app keeps go in `/data` (the only folder kept; the rest is read-only, but `/tmp`).
- Settings and secrets come from the environment (`os.environ["NAME"]`), set on the app's page.
- Packages: add them to `requirements.txt` with an exact version (`httpx==0.28.1`).
