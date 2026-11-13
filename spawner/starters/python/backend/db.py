"""The app's database, for the rest of backend/, in three calls:

    rows = db.query("SELECT * FROM items WHERE done = ? ORDER BY created_at", False)  # a list of dicts
    row = db.one("SELECT * FROM items WHERE id = ?", item_id)                           # a dict, or None
    count = db.execute("DELETE FROM items WHERE id = ?", item_id)                       # the rows it changed

Values always go in as `?`, never into the SQL's text. It's Postgres when the app has a database
(DATABASE_URL, which its owner turns on on its page), else SQLite in /data/app.db (kept across builds
and restarts), so write SQL both understand: TEXT ids made in Python (uuid4().hex), BOOLEAN,
TIMESTAMP, and `?` for values.

The tables come from db/migrations/*.sql, each run once, in name order: for Postgres by labb at each
build, for SQLite by migrate() as the server starts. To change a table, write the next migration
(0002_….sql): one that ran is never run again.
"""

import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

URL = os.environ.get("DATABASE_URL", "")
SQLITE = Path(os.environ.get("DATA_DIR", "/data")) / "app.db"
MIGRATIONS = Path(__file__).resolve().parent / "db" / "migrations"

# SQLite keeps booleans and times as numbers and text: given back as Postgres gives them
sqlite3.register_converter("BOOLEAN", lambda value: value not in (b"0", b""))
sqlite3.register_converter("TIMESTAMP", lambda value: datetime.fromisoformat(value.decode()))


def _sqlite() -> sqlite3.Connection:
    connection = sqlite3.connect(SQLITE, detect_types=sqlite3.PARSE_DECLTYPES, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _run(sql: str, args: tuple):
    """(the rows, or None for a statement that gives none; how many rows it changed)"""
    if URL:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(URL, row_factory=dict_row) as connection:
            cursor = connection.execute(sql.replace("%", "%%").replace("?", "%s"), args)
            return (cursor.fetchall() if cursor.description else None), cursor.rowcount
    with closing(_sqlite()) as connection, connection:
        cursor = connection.execute(sql, args)
        return ([dict(row) for row in cursor.fetchall()] if cursor.description else None), cursor.rowcount


def query(sql: str, *args) -> list[dict]:
    return _run(sql, args)[0] or []


def one(sql: str, *args) -> dict | None:
    rows = query(sql, *args)
    return rows[0] if rows else None


def execute(sql: str, *args) -> int:
    return _run(sql, args)[1]


def migrate():
    """SQLite's tables: the migrations it hasn't had yet (Postgres has had them, from labb)"""
    if URL:
        return
    SQLITE.parent.mkdir(parents=True, exist_ok=True)
    with closing(_sqlite()) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS labb_migrations (name TEXT PRIMARY KEY, "
                           "applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        done = {row["name"] for row in connection.execute("SELECT name FROM labb_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            try:
                # All of it or none of it
                connection.executescript(f"BEGIN;\n{path.read_text()}\n;\nINSERT INTO labb_migrations (name) "
                                         f"VALUES ('{path.name.replace("'", "''")}');\nCOMMIT;")
            except sqlite3.Error as e:
                connection.rollback()
                raise RuntimeError(f"The migration {path.name} failed: {e}") from e
