"""The database: a SQLite file by default, or Postgres when LABB_DATABASE_URL is set.

labb on a PC always keeps its own SQLite file. The server can use either: SQLite for
trying it out and for tests, Postgres for a server several people use every day.

Queries are written once, SQLite style (`?` placeholders), using only what both databases
understand: `INSERT ... ON CONFLICT`, `COALESCE`, text times that compare as strings. The
Postgres connection turns `?` into psycopg's `%s`. The few things that differ (whether a
table is there, local days and hours, a JSON object's entries, the database's size) are helpers here.

psycopg is only in the server's dependencies, so it's imported only for Postgres.
"""

import atexit
import os
import sqlite3

DATABASE_URL = os.getenv("LABB_DATABASE_URL", "").strip()
POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))


def where() -> str:
    """Where the data is, for messages: the file, or Postgres's host and database (no password)"""

    if not POSTGRES:
        return os.getenv("CHAT_DB", "chats.sqlite")
    from psycopg.conninfo import conninfo_to_dict

    info = conninfo_to_dict(DATABASE_URL)
    return f"postgres://{info.get('host', 'localhost')}:{info.get('port', 5432)}/{info.get('dbname', '')}"


def placeholders(sql: str) -> str:
    """SQLite's `?` as psycopg's `%s`, and a literal `%` (as in LIKE 'cli-%') doubled"""

    out, quoted = [], False
    for char in sql:
        if char == "'":
            quoted = not quoted
        if char == "%":
            out.append("%%")
        elif char == "?" and not quoted:
            out.append("%s")
        else:
            out.append(char)
    return "".join(out)


class PostgresConnection:
    """A psycopg connection used like sqlite3's: `execute` and `executemany` with `?`, and
    `with conn:` as one transaction (outside one, each statement commits by itself).

    Parameters are bound by psycopg on the client (`ClientCursor`), as SQLite does, so
    `? IS NULL` and the like work without types. Like sqlite3's connection, it's shared by
    the threads of the process behind the callers' lock. A connection lost between
    transactions (Postgres restarted) is opened again."""

    def __init__(self, url: str):
        self.url = url
        self._conn = None
        self._transactions = []

    def _connection(self):
        import psycopg

        if self._conn is None or (self._conn.closed and not self._transactions):
            self._conn = psycopg.connect(self.url, autocommit=True, cursor_factory=psycopg.ClientCursor)
        return self._conn

    def execute(self, sql: str, params=()):
        try:
            return self._connection().execute(placeholders(sql) if params else sql, params or None)
        except Exception:
            self._drop_if_lost()
            raise

    def executemany(self, sql: str, rows):
        cursor = self._connection().cursor()
        try:
            cursor.executemany(placeholders(sql), list(rows))
        except Exception:
            self._drop_if_lost()
            raise
        return cursor

    def _drop_if_lost(self):
        if self._conn is not None and self._conn.broken and not self._transactions:
            self._conn.close()

    def __enter__(self):
        transaction = self._connection().transaction()
        transaction.__enter__()
        self._transactions.append(transaction)
        return self

    def __exit__(self, *exc):
        transaction = self._transactions.pop()
        try:
            return transaction.__exit__(*exc)
        finally:
            self._drop_if_lost()

    def close(self):
        if self._conn is not None:
            self._conn.close()


def connect(path: str):
    """The app's connection: Postgres's with LABB_DATABASE_URL, else the SQLite file's"""

    if POSTGRES:
        return PostgresConnection(DATABASE_URL)
    return sqlite3.connect(path, check_same_thread=False)


def checkpointer(path: str):
    """LangGraph's checkpointer for the same database, its tables created"""

    if not POSTGRES:
        from langgraph.checkpoint.sqlite import SqliteSaver

        return SqliteSaver(sqlite3.connect(path, check_same_thread=False))

    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    # The pool replaces connections Postgres dropped; the saver takes one at a time anyway
    pool = ConnectionPool(
        DATABASE_URL, min_size=1, max_size=2, open=True, check=ConnectionPool.check_connection,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    atexit.register(pool.close)
    saver = PostgresSaver(pool)
    saver.setup()
    return saver


# --- What the two databases say differently ------------------------------------------

def has_table(conn, table: str) -> bool:
    if POSTGRES:
        sql = "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ?"
    else:
        sql = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"
    return conn.execute(sql, (table,)).fetchone() is not None


def add_column(conn, table: str, column: str, declaration: str):
    """Add a column to a table made before it existed; nothing if it's there"""

    if POSTGRES:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {declaration}")
        return
    if column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _local(column: str, minutes: int) -> str:
    return f"(CAST({column} AS timestamptz) AT TIME ZONE 'UTC' + INTERVAL '{int(minutes)} minutes')"


def local_day(column: str, minutes: int) -> str:
    """SQL for the day (YYYY-MM-DD) of a UTC text time, `minutes` away from UTC"""

    if POSTGRES:
        return f"to_char({_local(column, minutes)}, 'YYYY-MM-DD')"
    return f"date({column}, '{int(minutes):+d} minutes')"


def local_hour(column: str, minutes: int) -> str:
    """SQL for the hour (0 to 23, an integer) of a UTC text time, `minutes` away from UTC"""

    if POSTGRES:
        return f"CAST(EXTRACT(HOUR FROM {_local(column, minutes)}) AS INTEGER)"
    return f"CAST(strftime('%H', {column}, '{int(minutes):+d} minutes') AS INTEGER)"


def json_entries(column: str, alias: str) -> str:
    """A FROM item with the `key` and `value` (as text) of the JSON object in a text column"""

    if POSTGRES:
        return f"jsonb_each_text(CAST({column} AS jsonb)) AS {alias}"
    return f"json_each({column}) AS {alias}"


def group_concat(expression: str) -> str:
    """The distinct values of a group, joined with commas"""

    if POSTGRES:
        return f"string_agg(DISTINCT {expression}, ',')"
    return f"GROUP_CONCAT(DISTINCT {expression})"


def size(conn, path: str) -> int:
    """How many bytes the database takes"""

    if POSTGRES:
        return conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
    return sum(os.path.getsize(p) for p in (path, path + "-wal") if os.path.exists(p))


# Postgres's tables of LangGraph's checkpointer are called differently
WRITES_TABLE = "checkpoint_writes" if POSTGRES else "writes"
