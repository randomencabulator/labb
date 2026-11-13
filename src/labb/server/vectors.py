"""Searching by meaning: the chunks of documents (documents.py) and the
server's memories (memory_index.py), embedded into a `vectors` table of the server's database.

- On Postgres with pgvector (the Docker image), each embedding is a `vector` and Postgres finds
  the closest (cosine) itself. It also keeps each text's words (`tsv`, Postgres full-text
  search), so documents are searched by meaning and by keywords at once (`hybrid`).
- Elsewhere (SQLite, or a Postgres without the extension) each embedding is float32 bytes and
  the search runs here, over the asking user's rows: quick enough for a person's documents.

One row per (kind, model, id): vectors of different models can't be compared, so a new
embedding model (embedding.py) gets rows of its own and the old ones are left out of searches. Every search
filters on the user first.
"""

import json
import logging
import math
import re
from array import array
from dataclasses import dataclass, field

from langchain_core.embeddings import Embeddings

from labb import db
from labb.chats import conn, lock

log = logging.getLogger(__name__)

# Candidates each search (meaning, keywords) brings to the fusion, at least
CANDIDATES = 20
# Reciprocal rank fusion's constant: how much the top of each list counts over the rest
RRF_K = 60
WORD = re.compile(r"\w+")


def _setup() -> bool:
    """The table; True when it's pgvector's (the search runs in Postgres)"""

    if db.POSTGRES:
        try:
            with lock:
                conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception as e:
            log.warning("No pgvector in this Postgres (%s); searching by meaning in Python instead", e)
    with lock, conn:
        pgvector = db.POSTGRES and conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone() is not None
        embedding = "vector" if pgvector else ("BYTEA" if db.POSTGRES else "BLOB")
        words = ", tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED" if pgvector else ""
        conn.execute(
            "CREATE TABLE IF NOT EXISTS vectors ("
            "kind TEXT NOT NULL, model TEXT NOT NULL, id TEXT NOT NULL, user_id TEXT NOT NULL, doc_id TEXT, "
            f"text TEXT NOT NULL, meta TEXT NOT NULL, embedding {embedding} NOT NULL{words}, "
            "PRIMARY KEY (kind, model, id))"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS vectors_by_user ON vectors (kind, model, user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS vectors_by_document ON vectors (doc_id)")
        if not db.POSTGRES:
            return False
        # A table made before pgvector was there stays as it was
        udt = conn.execute(
            "SELECT udt_name FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = 'vectors' AND column_name = 'embedding'"
        ).fetchone()[0]
        if udt == "vector":
            conn.execute("CREATE INDEX IF NOT EXISTS vectors_words ON vectors USING gin (tsv)")
        return udt == "vector"


PGVECTOR = _setup()


@dataclass
class Item:
    id: str
    user_id: str
    text: str
    doc_id: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class Hit:
    id: str
    text: str
    meta: dict
    # Cosine similarity: 1 the same direction, 0 unrelated
    score: float


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(math.sumprod(vector, vector)) or 1.0
    return [v / norm for v in vector]


def _encode(vector: list[float]):
    """An embedding as the column takes it: pgvector's text form, or float32 bytes"""

    vector = _unit(vector)
    if PGVECTOR:
        return "[" + ",".join(repr(v) for v in vector) + "]"
    return array("f", vector).tobytes()


def words(text: str) -> set[str]:
    return set(WORD.findall(text.lower()))


def fuse(*ranked: list[Hit], k: int) -> list[Hit]:
    """Reciprocal rank fusion: what's high in any of the lists comes first, and what's high
    in both before that"""

    scores: dict[str, float] = {}
    hits: dict[str, Hit] = {}
    for hits_in_order in ranked:
        for rank, hit in enumerate(hits_in_order, start=1):
            scores[hit.id] = scores.get(hit.id, 0.0) + 1 / (RRF_K + rank)
            hits.setdefault(hit.id, hit)
    return [hits[i] for i in sorted(scores, key=lambda i: -scores[i])[:k]]


class Store:
    """The vectors of one kind ("documents", "memories"), embedded with `embeddings` and kept
    under `model`'s name"""

    def __init__(self, kind: str, embeddings: Embeddings, model: str):
        self.kind, self.embeddings, self.model = kind, embeddings, model

    # --- Writing ---------------------------------------------------------------------

    def replace(self, items: list[Item], *, doc_id: str | None = None, batch: int = 64):
        """Embed `items` and save them, in place of the same ids (and of `doc_id`'s other
        chunks), all at once: a search sees the old ones or the new ones"""

        vectors = []
        for i in range(0, len(items), batch):
            vectors += self.embeddings.embed_documents([item.text for item in items[i:i + batch]])
        rows = [(self.kind, self.model, item.id, item.user_id, item.doc_id, item.text,
                 json.dumps(item.meta), _encode(vector)) for item, vector in zip(items, vectors)]
        with lock, conn:
            if doc_id is not None:
                conn.execute("DELETE FROM vectors WHERE kind = ? AND model = ? AND doc_id = ?", (self.kind, self.model, doc_id))
            conn.executemany(
                "INSERT INTO vectors (kind, model, id, user_id, doc_id, text, meta, embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (kind, model, id) DO UPDATE SET user_id = excluded.user_id, doc_id = excluded.doc_id, "
                "text = excluded.text, meta = excluded.meta, embedding = excluded.embedding",
                rows,
            )

    def delete(self, ids: list[str] | None = None, *, doc_id: str | None = None):
        """Forget these ids, or a document's chunks, under every model"""

        with lock, conn:
            if doc_id is not None:
                conn.execute("DELETE FROM vectors WHERE kind = ? AND doc_id = ?", (self.kind, doc_id))
            if ids:
                conn.executemany("DELETE FROM vectors WHERE kind = ? AND id = ?", [(self.kind, i) for i in ids])

    # --- Reading ---------------------------------------------------------------------

    def text(self, item_id: str) -> str | None:
        """What's saved for an id under this model (None: nothing)"""

        with lock:
            row = conn.execute("SELECT text FROM vectors WHERE kind = ? AND model = ? AND id = ?",
                               (self.kind, self.model, item_id)).fetchone()
        return row[0] if row else None

    def ids(self, doc_id: str) -> list[str]:
        with lock:
            rows = conn.execute("SELECT id FROM vectors WHERE kind = ? AND model = ? AND doc_id = ? ORDER BY id",
                                (self.kind, self.model, doc_id)).fetchall()
        return [row[0] for row in rows]

    def search(self, user_id: str, query: str, k: int, *, doc_ids: list[str] | None = None,
               hybrid: bool = False) -> list[Hit]:
        """The `k` items of `user_id` (in `doc_ids`, if given) closest to `query` in meaning,
        best first; with `hybrid`, those that share its words count too"""

        if not query.strip() or doc_ids == []:
            return []
        vector = self.embeddings.embed_query(query)
        n = max(CANDIDATES, 4 * k) if hybrid else k
        where = "kind = ? AND model = ? AND user_id = ?"
        params = [self.kind, self.model, user_id]
        if doc_ids is not None:
            where += f" AND doc_id IN ({', '.join('?' for _ in doc_ids)})"
            params += doc_ids
        search = self._search_postgres if PGVECTOR else self._search_here
        meaning, keywords = search(where, params, vector, query, n, hybrid)
        return fuse(meaning, keywords, k=k) if hybrid else meaning[:k]

    def _search_postgres(self, where: str, params: list, vector: list[float], query: str, n: int,
                         hybrid: bool) -> tuple[list[Hit], list[Hit]]:
        encoded = _encode(vector)
        columns = "id, text, meta, 1 - (embedding <=> CAST(? AS vector))"
        with lock:
            meaning = conn.execute(
                f"SELECT {columns} FROM vectors WHERE {where} ORDER BY embedding <=> CAST(? AS vector) LIMIT ?",
                [encoded, *params, encoded, n],
            ).fetchall()
            keywords = []
            if hybrid:
                # Any of the query's words, not all of them
                any_word = "CAST(replace(CAST(plainto_tsquery('simple', ?) AS text), '&', '|') AS tsquery)"
                keywords = conn.execute(
                    f"SELECT {columns} FROM vectors WHERE {where} AND tsv @@ {any_word} "
                    f"ORDER BY ts_rank_cd(tsv, {any_word}) DESC LIMIT ?",
                    [encoded, *params, query, query, n],
                ).fetchall()
        hit = lambda row: Hit(row[0], row[1], json.loads(row[2]), max(0.0, float(row[3])))
        return [hit(row) for row in meaning], [hit(row) for row in keywords]

    def _search_here(self, where: str, params: list, vector: list[float], query: str, n: int,
                     hybrid: bool) -> tuple[list[Hit], list[Hit]]:
        with lock:
            rows = conn.execute(f"SELECT id, text, meta, embedding FROM vectors WHERE {where}", params).fetchall()
        target = _unit(vector)
        hits = [Hit(item_id, text, json.loads(meta), max(0.0, math.sumprod(target, array("f", bytes(embedding)))))
                for item_id, text, meta, embedding in rows]
        meaning = sorted(hits, key=lambda hit: -hit.score)[:n]
        keywords = []
        if hybrid and (wanted := words(query)):
            matched = [(len(wanted & words(hit.text)), hit) for hit in hits]
            keywords = [hit for count, hit in sorted(matched, key=lambda pair: -pair[0]) if count][:n]
        return meaning, keywords
