"""Pages already read, so reading one again (or the next chunk of a long one) doesn't fetch it
again. Each is kept for CACHE_HOURS.

They're in Postgres with CRAWLER_DATABASE_URL (in Docker, the same database as labb's
server, in a table of their own), else in a SQLite file at DB_PATH. It's only a cache: if the
database can't be reached, pages are fetched again and the error is logged."""

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path

from labb_web_search_crawler.extract import Page

DB_PATH = os.getenv("DB_PATH", "data/pages.sqlite")
DATABASE_URL = os.getenv("CRAWLER_DATABASE_URL", "").strip()
CACHE_HOURS = float(os.getenv("CACHE_HOURS", 24))

log = logging.getLogger(__name__)

_conn = None
_lock = threading.Lock()


def postgres() -> bool:
    return DATABASE_URL.startswith(("postgres://", "postgresql://"))


def _sql(query: str) -> str:
    return query.replace("?", "%s") if postgres() else query


def db():
    """The connection, opened (again, after Postgres dropped it) when needed"""

    global _conn
    with _lock:
        if _conn is None or getattr(_conn, "closed", False):
            if postgres():
                import psycopg

                _conn = psycopg.connect(DATABASE_URL, autocommit=True)
                seconds = "DOUBLE PRECISION"
            else:
                Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
                _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
                seconds = "REAL"
            _conn.execute(
                "CREATE TABLE IF NOT EXISTS crawler_pages (key TEXT PRIMARY KEY, url TEXT NOT NULL, final_url TEXT NOT NULL, "
                f"page TEXT NOT NULL, rendered INTEGER NOT NULL, fetched_at {seconds} NOT NULL)"
            )
            if not postgres():
                _conn.commit()
        return _conn


def _lost(error: Exception):
    """Log a database error, and drop a Postgres connection so the next call opens a new one"""

    log.warning("The page store failed (%s); fetching instead", error)
    with _lock:
        if postgres() and _conn is not None and _conn.broken:
            _conn.close()


def key(url: str, full: bool, images: bool) -> str:
    return f"{url}|{int(full)}|{int(images)}"


def get(cache_key: str) -> tuple[str, Page, bool, float] | None:
    """(final URL, page, rendered, fetched at) if it's stored and fresh"""

    try:
        conn = db()
        with _lock:
            row = conn.execute(_sql("SELECT final_url, page, rendered, fetched_at FROM crawler_pages WHERE key = ?"),
                               (cache_key,)).fetchone()
    except Exception as e:
        _lost(e)
        return None
    if row is None or time.time() - row[3] > CACHE_HOURS * 3600:
        return None
    return row[0], Page(**json.loads(row[1])), bool(row[2]), row[3]


def put(cache_key: str, url: str, final_url: str, page: Page, rendered: bool) -> float:
    at = time.time()
    # JSON writes NUL characters as \u0000, which Postgres's text takes
    data = json.dumps(asdict(page), ensure_ascii=False)
    try:
        conn = db()
        with _lock:
            conn.execute(
                _sql("INSERT INTO crawler_pages (key, url, final_url, page, rendered, fetched_at) VALUES (?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT (key) DO UPDATE SET url = excluded.url, final_url = excluded.final_url, "
                     "page = excluded.page, rendered = excluded.rendered, fetched_at = excluded.fetched_at"),
                (cache_key, url, final_url, data, int(rendered), at),
            )
            # Forget what's expired now and then
            conn.execute(_sql("DELETE FROM crawler_pages WHERE fetched_at < ?"), (at - CACHE_HOURS * 3600,))
            if not postgres():
                conn.commit()
    except Exception as e:
        _lost(e)
    return at
