"""Usage stats: a ledger of every reply and hourly totals of the
gateway's traffic, and the numbers the web chat shows from them.

- The `usage` ledger has one row per reply: web replies as they end (runner.py) and
  labb's when a PC syncs its session. Rows are only ever added: deleting a chat,
  regenerating or taking back a reply leaves them, so totals don't shrink.
- `gateway_usage` adds up the Ollama calls and page reads that go through the server, per
  hour, user, key and model. It's the load on Ollama as it happens, including replies that
  never reach the ledger (`labb -p`, sessions not synced yet), so the two are never added
  together.
- `clients` remembers which labb version and machine each API key was last seen with.

A reply's `cost` is worked out as it's recorded, from `prices()` (the server's: Settings →
Models → Prices; costs.py), and kept: changing a price later doesn't change what was spent before
it. NULL is a reply whose model had no price. `reprice` fills in the ones without a cost.

Admins see counts, models and machines, never what anyone asked: the titles of chats are
only shown to their owner.
"""

import csv
import io
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from labb import costs, db
from labb.agent import CHAT_DB
from labb.chats import conn, lock

# How often a key's client version is written at most
SEEN_EVERY_SECONDS = 60
# Rows in the top lists
TOP = 10
# The header labb sends with every request: "<version> (<machine>)"
CLIENT_HEADER = "x-labb-client"

# The prices replies are costed at, as costs.py takes them; the server sets it (server/prices.py)
prices = lambda: {}

_seen: dict[str, float] = {}
_seen_lock = threading.Lock()


def stamp(moment: datetime | str | None = None) -> str:
    """A time as the ledger stores it: UTC, YYYY-MM-DDTHH:MM:SSZ (now if none is given)"""

    if moment is None:
        moment = datetime.now(timezone.utc)
    elif isinstance(moment, str):
        moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Recording -----------------------------------------------------------------------

def _row(thread_id: str, message_id: str, stats: dict, user_id: str | None, source: str,
         machine: str | None, fallback_time: str | None) -> tuple:
    at = stats.get("at") or fallback_time
    try:
        created_at = stamp(at)
    except (TypeError, ValueError):
        created_at = stamp()
    tools = stats.get("tools") if isinstance(stats.get("tools"), dict) else {}
    number = lambda key: int(stats.get(key) or 0)
    optional = lambda key: int(stats[key]) if isinstance(stats.get(key), (int, float)) else None
    model = str(stats.get("model") or "unknown")
    return (
        thread_id, message_id, user_id, source, machine, model, created_at,
        number("duration_ms"), optional("first_token_ms"), number("input_tokens"), number("output_tokens"),
        optional("generation_ms"), number("llm_calls"), number("tool_calls"), json.dumps(tools),
        int(bool(stats.get("stopped"))), int(bool(stats.get("error"))),
        number("tool_repaired"), number("tool_constrained"), number("tool_failed"),
        stats.get("engine") if isinstance(stats.get("engine"), str) else None,
        number("cache_read_tokens"), number("cache_write_tokens"),
        cost_of(model, number("input_tokens"), number("output_tokens"), number("cache_read_tokens"),
                number("cache_write_tokens"), prices()),
    )


def cost_of(model: str, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int, price_list: dict) -> float | None:
    """What a reply cost at these prices; None if its model has none"""
    price = costs.price_of(model, price_list)
    return None if price is None else costs.cost_at(price, input_tokens, output_tokens, cache_read, cache_write)


INSERT = (
    "INSERT INTO usage (thread_id, message_id, user_id, source, machine, model, created_at, duration_ms, "
    "first_token_ms, input_tokens, output_tokens, generation_ms, llm_calls, tool_calls, tools, stopped, error, "
    "tool_repaired, tool_constrained, tool_failed, engine, cache_read_tokens, cache_write_tokens, cost) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (thread_id, message_id) DO NOTHING"
)


def record(thread_id: str, message_id: str, stats: dict, *, user_id: str | None, source: str,
           machine: str | None = None, fallback_time: str | None = None):
    """Add one reply to the ledger; a reply that's in it already stays as it is"""

    # Made before taking the lock: costing it can read the prices from the database
    row = _row(thread_id, message_id, stats, user_id, source, machine, fallback_time)
    with lock, conn:
        conn.execute(INSERT, row)


def record_session(thread_id: str, stats: dict[str, dict], *, user_id: str | None, machine: str, updated_at: str):
    """Add the replies of a session a PC synced. Replies from before labb noted when they
    ended get the session's last update as their time."""

    rows = [_row(thread_id, message_id, data, user_id, "labb", machine, updated_at)
            for message_id, data in stats.items() if isinstance(data, dict)]
    with lock, conn:
        conn.executemany(INSERT, rows)


def record_gateway(user_id: str, key_id: str | None, model: str, *, failed: bool = False, input_tokens: int = 0,
                   output_tokens: int = 0, eval_ms: int = 0, page: bool = False):
    """Count one Ollama call (or page read, with `page`) that went through the gateway"""

    hour = stamp()[:13] + ":00:00Z"
    with lock, conn:
        conn.execute(
            "INSERT INTO gateway_usage (hour, user_id, key_id, model, requests, failures, input_tokens, output_tokens, eval_ms, pages) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (hour, user_id, key_id, model) DO UPDATE SET "
            "requests = gateway_usage.requests + excluded.requests, failures = gateway_usage.failures + excluded.failures, "
            "input_tokens = gateway_usage.input_tokens + excluded.input_tokens, "
            "output_tokens = gateway_usage.output_tokens + excluded.output_tokens, "
            "eval_ms = gateway_usage.eval_ms + excluded.eval_ms, pages = gateway_usage.pages + excluded.pages",
            (hour, user_id, key_id or "", model, 0 if page else 1, int(failed), input_tokens, output_tokens, eval_ms, int(page)),
        )


def reprice(price_list: dict, everything: bool = False) -> int:
    """Cost the replies that have none at these prices (all of them, with `everything`); how many
    got a cost. A reply whose model still has no price stays without one."""

    where = "" if everything else " WHERE cost IS NULL"
    rows = _query("SELECT thread_id, message_id, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens "
                  f"FROM usage{where}", [])
    updates = [(cost, thread_id, message_id) for thread_id, message_id, model, i, o, read, write in rows
               if (cost := cost_of(model, i, o, read, write, price_list)) is not None or everything]
    with lock, conn:
        conn.executemany("UPDATE usage SET cost = ? WHERE thread_id = ? AND message_id = ?", updates)
    return sum(1 for cost, _, _ in updates if cost is not None)


def parse_client(header: str) -> tuple[str, str] | None:
    """The version and machine in an X-Labb-Client header, "0.1.1 (laptop)" """

    version, _, rest = header.strip().partition(" ")
    machine = rest.strip().removeprefix("(").removesuffix(")").strip()
    if not version or len(version) > 40 or len(machine) > 200:
        return None
    return version, machine or "unknown"


def seen(key_id: str, header: str | None):
    """Note the labb version a key was used with (once a minute at most)"""

    if not header or not (client := parse_client(header)):
        return
    with _seen_lock:
        if time.monotonic() - _seen.get(key_id, -SEEN_EVERY_SECONDS) < SEEN_EVERY_SECONDS:
            return
        _seen[key_id] = time.monotonic()
    with lock, conn:
        conn.execute(
            "INSERT INTO clients (key_id, version, machine, last_seen) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (key_id) DO UPDATE SET version = excluded.version, machine = excluded.machine, last_seen = excluded.last_seen",
            (key_id, *client, stamp()),
        )


# --- Reading -----------------------------------------------------------------------

def _where(user_id: str | None, since: str | None, until: str | None, column: str = "created_at",
           table: str = "") -> tuple[str, list]:
    """A WHERE clause for a user and a time range; `table` prefixes the columns ("u.")"""

    clauses, params = [], []
    column = table + column
    if user_id is not None:
        clauses.append(f"{table}user_id = ?")
        params.append(user_id)
    if since:
        clauses.append(f"{column} >= ?")
        params.append(stamp(since))
    if until:
        clauses.append(f"{column} < ?")
        params.append(stamp(until))
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _query(sql: str, params: list) -> list[tuple]:
    with lock:
        return conn.execute(sql, params).fetchall()


def _source(source: str, machine: str | None) -> str:
    return source if source in ("web", "task", "workflow", "agent") else (machine or "labb")


def summary(user_id: str | None, since: str | None = None, until: str | None = None, tz_minutes: int = 0,
            titles: bool = False) -> dict:
    """The numbers of one user's usage page (everyone's for None): totals, and series by
    day, source, model, tool and hour. `tz_minutes` is the viewer's offset from UTC, for days
    and hours. With `titles`, also the chats and sessions that used the most tokens."""

    where, params = _where(user_id, since, until)
    totals = _query(
        "SELECT COUNT(*), COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), COALESCE(SUM(duration_ms), 0), "
        "COALESCE(SUM(tool_calls), 0), COALESCE(SUM(stopped), 0), COALESCE(SUM(error), 0), COUNT(DISTINCT user_id), "
        f"COUNT(DISTINCT thread_id), MIN(created_at), MAX(created_at), SUM(cost), COUNT(cost), "
        f"COALESCE(SUM(cache_read_tokens), 0), COALESCE(SUM(cache_write_tokens), 0) FROM usage{where}", params,
    )[0]
    result = {
        "totals": {
            "replies": totals[0], "input_tokens": totals[1], "output_tokens": totals[2], "duration_ms": totals[3],
            "tool_calls": totals[4], "stopped": totals[5], "errors": totals[6], "users": totals[7],
            "conversations": totals[8], "first": totals[9], "last": totals[10],
            # Dollars, from the replies that had a price (null: none had); how many had one
            "cost": totals[11], "priced_replies": totals[12],
            "cache_read_tokens": totals[13], "cache_write_tokens": totals[14],
        },
        "by_day": [
            {"day": day, "source": _source(source, machine), "replies": replies, "input_tokens": i, "output_tokens": o, "cost": cost}
            for day, source, machine, replies, i, o, cost in _query(
                f"SELECT {db.local_day('created_at', tz_minutes)} AS day, source, machine, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost) "
                f"FROM usage{where} GROUP BY day, source, machine ORDER BY day", params,
            )
        ],
        "by_model": [
            {"model": model, "replies": replies, "input_tokens": i, "output_tokens": o,
             "avg_first_token_ms": round(first) if first is not None else None,
             "max_first_token_ms": slowest,
             "tokens_per_second": round(generated / (gen_ms / 1000), 1) if gen_ms else None,
             # How the model's tool calls came out (tool_calls.py): fixed by labb, rewritten, or still broken
             "tool_calls": calls, "tool_repaired": repaired, "tool_constrained": constrained, "tool_failed": failed,
             # Dollars (null: no reply of it had a price), and the input read from the provider's cache
             "cost": cost, "priced_replies": priced, "cache_read_tokens": read, "cache_write_tokens": written}
            for model, replies, i, o, first, slowest, generated, gen_ms, calls, repaired, constrained, failed, cost, priced, read, written in _query(
                "SELECT model, COUNT(*), SUM(input_tokens), SUM(output_tokens), AVG(first_token_ms), MAX(first_token_ms), "
                "SUM(CASE WHEN generation_ms > 0 THEN output_tokens ELSE 0 END), SUM(CASE WHEN generation_ms > 0 THEN generation_ms ELSE 0 END), "
                "SUM(tool_calls), SUM(tool_repaired), SUM(tool_constrained), SUM(tool_failed), SUM(cost), COUNT(cost), "
                "SUM(cache_read_tokens), SUM(cache_write_tokens) "
                f"FROM usage{where} GROUP BY model ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC", params,
            )
        ],
        "by_engine": [
            {"engine": name, "replies": replies, "input_tokens": i, "output_tokens": o, "avg_duration_ms": round(duration or 0),
             "llm_calls": calls, "tool_calls": tools, "errors": errors}
            for name, replies, i, o, duration, calls, tools, errors in _query(
                "SELECT engine, COUNT(*), SUM(input_tokens), SUM(output_tokens), AVG(duration_ms), SUM(llm_calls), SUM(tool_calls), SUM(error) "
                f"FROM usage{where} GROUP BY engine ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC", params,
            )
        ],
        "by_source": [
            {"source": _source(source, machine), "replies": replies, "input_tokens": i, "output_tokens": o, "last": last, "cost": cost}
            for source, machine, replies, i, o, last, cost in _query(
                "SELECT source, machine, COUNT(*), SUM(input_tokens), SUM(output_tokens), MAX(created_at), SUM(cost) "
                f"FROM usage{where} GROUP BY source, machine ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC", params,
            )
        ],
        "tools": [
            {"name": name, "calls": calls}
            for name, calls in _query(
                f"SELECT j.key, SUM(CAST(j.value AS INTEGER)) FROM usage, {db.json_entries('usage.tools', 'j')}{where} "
                f"GROUP BY j.key ORDER BY 2 DESC LIMIT {TOP}", params,
            )
        ],
        "by_hour": [
            {"hour": int(hour), "replies": replies, "avg_first_token_ms": round(first) if first is not None else None}
            for hour, replies, first in _query(
                f"SELECT {db.local_hour('created_at', tz_minutes)} AS hour, COUNT(*), AVG(first_token_ms) "
                f"FROM usage{where} GROUP BY hour ORDER BY hour", params,
            )
        ],
    }
    if titles:
        where, params = _where(user_id, since, until, table="u.")
        result["top_conversations"] = [
            {"thread_id": thread_id, "title": title or "(deleted)", "source": _source(source, machine),
             "replies": replies, "tokens": tokens, "exists": title is not None, "cost": cost}
            for thread_id, source, machine, replies, tokens, title, cost in _query(
                "SELECT u.thread_id, MAX(u.source), MAX(u.machine), COUNT(*), SUM(u.input_tokens + u.output_tokens), "
                "MAX(COALESCE(c.title, s.title)), SUM(u.cost) FROM usage u LEFT JOIN chats c ON c.thread_id = u.thread_id "
                f"LEFT JOIN synced_sessions s ON s.thread_id = u.thread_id{where} "
                f"GROUP BY u.thread_id ORDER BY 5 DESC LIMIT {TOP}", params,
            )
        ]
    return result


def active_users() -> dict:
    """How many people used the server today, in the last 7 days and in the last 30 days"""

    now = datetime.now(timezone.utc)
    counts = {}
    for name, days in (("day", 1), ("week", 7), ("month", 30)):
        since = stamp(now - timedelta(days=days))
        counts[name] = _query("SELECT COUNT(DISTINCT user_id) FROM usage WHERE created_at >= ?", [since])[0][0]
    return counts


def users(since: str | None = None, until: str | None = None) -> list[dict]:
    """One row per account: replies, tokens, last active, and the models and machines used"""

    where, params = _where(None, since, until)
    rows = _query(
        "SELECT u.id, u.username, u.role, u.disabled, COUNT(g.message_id), COALESCE(SUM(g.input_tokens), 0), "
        f"COALESCE(SUM(g.output_tokens), 0), MAX(g.created_at), SUM(g.cost), {db.group_concat('g.model')}, "
        f"{db.group_concat("CASE WHEN g.source = 'web' THEN 'web' ELSE g.machine END")} "
        f"FROM users u LEFT JOIN (SELECT * FROM usage{where}) g ON g.user_id = u.id "
        "GROUP BY u.id ORDER BY COALESCE(SUM(g.input_tokens + g.output_tokens), 0) DESC, u.username", params,
    )
    split = lambda text: sorted(text.split(",")) if text else []
    return [
        {"id": user_id, "username": username, "role": role, "disabled": bool(disabled), "replies": replies,
         "input_tokens": i, "output_tokens": o, "last_active": last, "cost": cost, "models": split(models), "sources": split(sources)}
        for user_id, username, role, disabled, replies, i, o, last, cost, models, sources in rows
    ]


def gateway(since: str | None = None, until: str | None = None, tz_minutes: int = 0) -> dict:
    """The gateway's traffic: per model, per hour of the day, and in total"""

    where, params = _where(None, since, until, column="hour")
    by_model = [
        {"model": model, "requests": requests, "failures": failures, "input_tokens": i, "output_tokens": o,
         "tokens_per_second": round(o / (eval_ms / 1000), 1) if eval_ms else None}
        for model, requests, failures, i, o, eval_ms in _query(
            "SELECT model, SUM(requests), SUM(failures), SUM(input_tokens), SUM(output_tokens), SUM(eval_ms) "
            f"FROM gateway_usage{where}{' AND' if where else ' WHERE'} model != '' GROUP BY model ORDER BY SUM(requests) DESC", params,
        )
    ]
    by_hour = [
        {"hour": int(hour), "requests": requests, "failures": failures}
        for hour, requests, failures in _query(
            f"SELECT {db.local_hour('hour', tz_minutes)} AS h, SUM(requests), SUM(failures) "
            f"FROM gateway_usage{where} GROUP BY h ORDER BY h", params,
        )
    ]
    totals = _query(
        "SELECT COALESCE(SUM(requests), 0), COALESCE(SUM(failures), 0), COALESCE(SUM(input_tokens), 0), "
        f"COALESCE(SUM(output_tokens), 0), COALESCE(SUM(pages), 0) FROM gateway_usage{where}", params,
    )[0]
    return {
        "totals": dict(zip(("requests", "failures", "input_tokens", "output_tokens", "pages"), totals)),
        "by_model": by_model,
        "by_hour": by_hour,
    }


def machines(server_version: str, since: str | None = None, until: str | None = None) -> list[dict]:
    """Each API key that isn't revoked: its owner, the machine and labb version it was last
    seen with (`outdated` when that's not the server's), and its gateway traffic"""

    where, params = _where(None, since, until, column="hour")
    rows = _query(
        "SELECT k.id, k.name, u.username, k.last_used_at, c.version, c.machine, c.last_seen, "
        "COALESCE(g.requests, 0), COALESCE(g.tokens, 0), COALESCE(g.pages, 0) FROM api_keys k "
        "JOIN users u ON u.id = k.user_id LEFT JOIN clients c ON c.key_id = k.id "
        "LEFT JOIN (SELECT key_id, SUM(requests) AS requests, SUM(input_tokens + output_tokens) AS tokens, SUM(pages) AS pages "
        f"FROM gateway_usage{where} GROUP BY key_id) g ON g.key_id = k.id "
        "WHERE k.revoked_at IS NULL ORDER BY COALESCE(c.last_seen, k.last_used_at, '') DESC", params,
    )
    return [
        {"key_id": key_id, "name": name, "username": username, "last_used_at": last_used, "version": version,
         "machine": machine, "last_seen": last_seen, "outdated": version is not None and version != server_version,
         "requests": requests, "tokens": tokens, "pages": pages}
        for key_id, name, username, last_used, version, machine, last_seen, requests, tokens, pages in rows
    ]


def storage() -> dict:
    """How big the database is, and what it holds"""

    with lock:
        size = db.size(conn, CHAT_DB)
    counts = {}
    for table in ("chats", "synced_sessions", "cli_sessions", "snapshots", "usage"):
        counts[table] = _query(f"SELECT COUNT(*) FROM {table}", [])[0][0]
    return {"bytes": size, **counts}


LEDGER_COLUMNS = ("created_at", "user_id", "source", "machine", "thread_id", "model", "duration_ms", "first_token_ms",
                  "input_tokens", "output_tokens", "generation_ms", "llm_calls", "tool_calls", "tool_repaired",
                  "tool_constrained", "tool_failed", "stopped", "error", "engine", "cache_read_tokens", "cache_write_tokens", "cost")


def ledger(user_id: str | None, since: str | None = None, until: str | None = None) -> list[dict]:
    """The ledger's rows, oldest first, for a CSV download"""

    where, params = _where(user_id, since, until)
    rows = _query(f"SELECT {', '.join(LEDGER_COLUMNS)} FROM usage{where} ORDER BY created_at", params)
    return [dict(zip(LEDGER_COLUMNS, row)) for row in rows]


def to_csv(rows: list[dict]) -> str:
    """Rows as CSV; lists are joined with spaces, and nested values left out"""

    if not rows:
        return ""
    columns = [key for key, value in rows[0].items() if not isinstance(value, dict)]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([" ".join(row[c]) if isinstance(row[c], list) else row[c] for c in columns])
    return out.getvalue()
