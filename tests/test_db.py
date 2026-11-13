"""The database layer (db.py): the same queries on SQLite and Postgres, and pruning checkpoints.

The Postgres tests run when the suite does (TEST_DATABASE_URL=postgresql://… uv run pytest)."""

from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from labb import chats, db
from labb.agent import chat_agent

postgres_only = pytest.mark.skipif(not db.POSTGRES, reason="needs TEST_DATABASE_URL")


def test_placeholders_become_psycopgs():
    sql = "SELECT a FROM t WHERE b = ? AND c LIKE 'cli-%' AND d = '?' AND e IN (?, ?)"
    assert db.placeholders(sql) == "SELECT a FROM t WHERE b = %s AND c LIKE 'cli-%%' AND d = '?' AND e IN (%s, %s)"


def test_tables():
    with chats.lock:
        assert db.has_table(chats.conn, "usage")
        assert not db.has_table(chats.conn, "no_such_table")


def test_local_days_and_hours():
    with chats.lock:
        day, hour = chats.conn.execute(
            f"SELECT {db.local_day('t', 120)}, {db.local_hour('t', -60)} FROM (SELECT '2026-10-24T23:30:00Z' AS t) AS x"
        ).fetchone()
    assert (day, hour) == ("2026-10-25", 22)


def test_a_transaction_is_all_or_nothing():
    thread_id = f"web-{uuid4()}"
    with pytest.raises(RuntimeError):
        with chats.lock, chats.conn:
            chats.conn.execute("INSERT INTO chats (thread_id, title, updated_at) VALUES (?, ?, ?)", (thread_id, "t", chats.now()))
            raise RuntimeError("halfway")
    assert not chats.owner(thread_id)[0]


def test_prune_keeps_the_conversation():
    thread_id = f"web-{uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}
    for n in range(3):
        chat_agent.update_state(config, {"messages": [HumanMessage(f"question {n}"), AIMessage(f"answer {n}")]})

    assert chats.prune(thread_id) == 2
    assert [m.text for m in chat_agent.get_state(config).values["messages"]][-2:] == ["question 2", "answer 2"]
    assert len(chat_agent.get_state(config).values["messages"]) == 6
    with chats.lock:
        left = chats.conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (thread_id,)).fetchone()[0]
    assert left == 1


@postgres_only
def test_prune_drops_the_values_no_checkpoint_uses():
    thread_id = f"web-{uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}
    for n in range(3):
        chat_agent.update_state(config, {"messages": [HumanMessage(f"question {n}")]})
    count = lambda: chats.conn.execute(
        "SELECT COUNT(*) FROM checkpoint_blobs WHERE thread_id = ? AND channel = 'messages'", (thread_id,)).fetchone()[0]
    with chats.lock:
        assert count() == 3
    chats.prune(thread_id)
    with chats.lock:
        assert count() == 1

    # A reply being saved writes its values before its checkpoint: a newer version stays
    with chats.lock, chats.conn:
        chats.conn.execute(
            "INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob) "
            "VALUES (?, '', 'messages', ?, 'empty', NULL)", (thread_id, "9" * 32 + ".0"))
    chats.prune(thread_id)
    with chats.lock:
        assert count() == 2
