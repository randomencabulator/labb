# The app's server

This file is for coding agents changing the server. It's [Express](https://expressjs.com) 5, in
Node.js (CommonJS: `require`), serving the app's JSON API under `/api/` on port 8080. It doesn't serve
the page: `frontend/` is built and served apart, by itself, and the page's `fetch("/api/…")` calls
come here. Never add the page's files or a static file server here.

## Files

| | |
|---|---|
| `server.js` | the app: uses each part of the API, and `/api/health` (keep it: labb asks it after each build) |
| `items.js` | the items API: **the example to copy** for each thing the app keeps |
| `db.js` | the database: `db.query`, `db.one`, `db.execute` (read its top) |
| `../db/migrations/*.sql` | the tables, made by migrations |
| `labb.js` | calls labb from the server, if the app may use it |
| `package.json` | packages, with exact versions |
| `Dockerfile` | how the server is built: leave it as it is |

## Adding to the API

To keep something new (notes, say):
1. **A table**: `db/migrations/0002_notes.sql`, like `0001_items.sql`. Never change a migration
   that ran; write the next one. SQL that works in SQLite and Postgres: `id TEXT PRIMARY KEY` (made
   with `crypto.randomUUID()`), `TEXT`, `INTEGER`, `BOOLEAN`, `TIMESTAMP … DEFAULT CURRENT_TIMESTAMP`.
2. **Its routes**: `notes.js`, a copy of `items.js` with the names changed: an `express.Router()`
   with `async` handlers, `db` calls with `?` for values.
3. **Use it** in `server.js`: `const notes = require("./notes");` and `app.use("/api/notes", notes);`,
   next to items (before the `/api` 404).
4. **The page** calls it with `api("/api/notes")` from `@/lib/api` (see `frontend/AGENTS.md`).

Then build the app, and check it with `look_at_app("/api/notes")`.

## Rules

- Every route is under `/api/`: nothing else reaches the server.
- Say what's wrong with `res.status(400).json({ error: "An item needs a title" })`: the page shows `error`.
- Values in SQL only as `?`, in an array: `db.one("SELECT * FROM notes WHERE id = ?", [id])`.
- Files the app keeps go in `/data` (the only folder kept; the rest is read-only, but `/tmp`).
- Settings and secrets come from the environment (`process.env.NAME`), set on the app's page.
- Packages: add them to `package.json`'s `dependencies` with an exact version (`"zod": "4.1.12"`).
