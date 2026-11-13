"""The vector store (server/vectors.py): searching by meaning per user, hybrid search with
keywords, and documents whose vectors are missing."""

import time
from uuid import uuid4

import pytest
from langchain_core.embeddings import Embeddings

from labb import chats, db
from labb.server import documents, vectors

from conftest import WordEmbeddings


class SameEmbeddings(Embeddings):
    """Every text points the same way: meaning can't tell them apart, only keywords can"""

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]


@pytest.fixture
def store():
    return vectors.Store(f"test-{uuid4().hex[:8]}", WordEmbeddings(), "test")


def test_postgres_searches_with_pgvector_when_it_has_it():
    if not db.POSTGRES:
        assert not vectors.PGVECTOR
        return
    with chats.lock:
        available = chats.conn.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'").fetchone()
    assert vectors.PGVECTOR == bool(available)


def test_the_closest_in_meaning_come_first_and_only_the_users_own(store):
    store.replace([
        vectors.Item("a", "u1", "tomatoes grow in the greenhouse"),
        vectors.Item("b", "u1", "the train to Vienna leaves at eight"),
        vectors.Item("c", "u2", "tomatoes and peppers in the greenhouse"),
    ])
    hits = store.search("u1", "greenhouse tomatoes", 2)
    assert [hit.id for hit in hits] == ["a", "b"]
    assert hits[0].score > 0.5 > hits[1].score
    assert [hit.id for hit in store.search("u2", "greenhouse tomatoes", 5)] == ["c"]


def test_replacing_and_deleting(store):
    store.replace([vectors.Item("d1:0", "u1", "first", "d1"), vectors.Item("d1:1", "u1", "second", "d1")], doc_id="d1")
    store.replace([vectors.Item("d1:0", "u1", "again", "d1")], doc_id="d1")
    assert store.ids("d1") == ["d1:0"] and store.text("d1:0") == "again"
    store.delete(doc_id="d1")
    assert store.ids("d1") == []


def test_hybrid_search_finds_the_exact_words_meaning_misses():
    store = vectors.Store(f"test-{uuid4().hex[:8]}", SameEmbeddings(), "test")
    store.replace([vectors.Item(f"n{n}", "u1", f"note number {n} about nothing much", "d1") for n in range(40)]
                  + [vectors.Item("err", "u1", "the printer shows E4471 when the tray jams", "d2")])
    assert "err" in [hit.id for hit in store.search("u1", "what does E4471 mean", 3, hybrid=True)]
    assert "err" not in [hit.id for hit in store.search("u1", "E4471", 3, doc_ids=["d1"], hybrid=True)]


def test_fusion_puts_what_both_found_first():
    hit = lambda i: vectors.Hit(i, i, {}, 0.5)
    assert [h.id for h in vectors.fuse([hit("a"), hit("b"), hit("c")], [hit("c"), hit("d")], k=2)] == ["c", "a"]


def test_documents_without_vectors_are_indexed_again(monkeypatch):
    """Ready documents without vectors (e.g. after the vectors were cleared) are embedded at start"""

    monkeypatch.setattr(documents, "_store", vectors.Store("documents", WordEmbeddings(), "test"))
    user_id = f"u-{uuid4().hex[:8]}"
    doc = documents.add(user_id, "notes.md", b"# Greenhouse\n\nTomatoes like it warm.")
    for _ in range(100):
        if documents.get(user_id, doc["id"])["status"] != "indexing":
            break
        time.sleep(0.05)
    with chats.lock, chats.conn:
        chats.conn.execute("DELETE FROM vectors WHERE doc_id = ?", (doc["id"],))

    assert documents.reindex_stale() >= 1
    for _ in range(100):
        if documents.get(user_id, doc["id"])["status"] == "ready" and documents.store().ids(doc["id"]):
            break
        time.sleep(0.05)
    assert documents.search(user_id, "warm tomatoes")[0]["document"] == "notes.md"
