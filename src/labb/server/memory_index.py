"""The server's memories, searchable by meaning: each memory embedded into the `vectors` table
(vectors.py, kind "memories"), so recall finds "likes hiking" for a message about a weekend
trip. memories.py keeps the memories themselves and falls back to keywords whenever this can't
answer (e.g. the embedding model isn't pulled yet)."""

import threading

from labb import memories
from labb.server import documents, embedding, vectors

# What a search of memories is for, for the embedding models that want to know (embedding.prompts)
TASK = "Given a user's message, retrieve facts about the user that help answer it"


class MemoryIndex:
    def __init__(self):
        self._store: vectors.Store | None = None
        self._lock = threading.Lock()

    def store(self) -> vectors.Store:
        with self._lock:
            if self._store is None:
                self._store = vectors.Store("memories", documents.embeddings(TASK), embedding.index_id())
            return self._store

    def reset(self):
        """The embedding model changed: the next search or embedding uses the new one"""
        with self._lock:
            self._store = None

    def add(self, memory: dict, user_id: str):
        """Embed a new or changed memory, replacing what it was before"""

        self.store().replace([vectors.Item(memory["id"], user_id, memory["text"])])
        conn, lock = memories.db()
        with lock, conn:
            conn.execute("UPDATE memories SET embed_model = ? WHERE id = ?", (embedding.index_id(), memory["id"]))

    def remove(self, ids: list[str]):
        self.store().delete(ids)

    def search(self, user_id: str, query: str, k: int) -> list[tuple[str, float]]:
        """(memory id, score from 0 to 1), closest first"""
        return [(hit.id, hit.score) for hit in self.store().search(user_id, query, k)]

    def reindex_stale(self) -> int:
        """At start, and when the embedding model changed, in a thread: embed the memories that
        aren't embedded with the current model (new model, the embedding failed, or no vector
        saved). Returns how many there are."""

        conn, lock = memories.db()
        with lock:
            rows = conn.execute(
                "SELECT id, text, user_id FROM memories m WHERE embed_model IS DISTINCT FROM ? OR NOT EXISTS ("
                "SELECT 1 FROM vectors v WHERE v.kind = 'memories' AND v.model = ? AND v.id = m.id)",
                (embedding.index_id(), self.store().model),
            ).fetchall()

        def run():
            for memory_id, text, user_id in rows:
                try:
                    self.add({"id": memory_id, "text": text}, user_id)
                except Exception:
                    # Most likely the model isn't there: the others would fail the same way
                    return

        if rows:
            threading.Thread(target=run, daemon=True, name="reindex-memories").start()
        return len(rows)
