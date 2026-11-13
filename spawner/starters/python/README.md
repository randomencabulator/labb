# React and Python

Two parts, each built by itself:

- `frontend/`: the page, React with [shadcn/ui](https://ui.shadcn.com) on Tailwind CSS, built with
  Vite. A change to its files is built and served by itself a moment later: there's nothing to deploy.
  `frontend/AGENTS.md` says how it's set up.
- `backend/`: the server, [FastAPI](https://fastapi.tiangolo.com) on port 8080, for the JSON API
  under `/api/` that the page calls (`fetch("/api/items")`). It doesn't serve the page. Building the
  app builds it and runs the new one. `backend/AGENTS.md` says how to add to it.

`backend/items.py` is an example to start from: a list of items, kept in the app's database (SQLite
in `/data`, or Postgres once the app has a database of its own: Settings → Database on its page),
with its table made by `db/migrations/0001_items.sql`. The page in `frontend/src/App.jsx` shows it.

`backend/labb.py` calls labb from the app's server, once its owner lets it (Settings → Use labb on its
page): `labb.ask("…")`, `labb.search_documents("…")`, `labb.recall("…")` and more. Never from its
pages: the key is the app's secret.
