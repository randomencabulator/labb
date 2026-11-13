"""The embedding model: the Ollama model that turns documents' passages, memories and searches into
vectors (vectors.py), so they're found by meaning. Admins pick it in Settings → Models → Search;
until they do, it's EMBED_MODEL from the environment, else the model the server's documents and
memories are already embedded with, else `embeddinggemma` (Google's EmbeddingGemma, 308M
parameters, multilingual, made for search).

- Each model family wants its own words in front of what it embeds (`prompts`): EmbeddingGemma a
  task in front of searches and "title: none | text: " in front of passages, Qwen3 an instruction
  in front of searches, Nomic "search_query: " and "search_document: ". Other models get the
  text as it is.
- What's in the index is named by `index_id`: the model, and a mark when passages get words in
  front of them, so changing those words is a new index too. Documents and memories remember
  which index they're in (`embed_model`), and a search only compares vectors of the same one.
- Changing the model (`save`) checks Ollama can embed with it, deletes the old vectors (searches
  can't use them), and embeds every document and memory again in the background. Until a
  memory is embedded again, it's found by keywords; documents say "indexing".
"""

import json
import logging
import os
import re

import httpx

from labb import db, llm
from labb.chats import conn, lock


log = logging.getLogger(__name__)

ENV_MODEL = os.getenv("EMBED_MODEL", "").strip() or None
DEFAULT = ENV_MODEL or "embeddinggemma"
PASSAGE_MARK = "+passage-prompt"
KEY = "embedding"
# Offered in the picker whether they're pulled or not (the default first, then by size), with what each is like
SUGGESTED = {
    "embeddinggemma": "308M, Google, multilingual, made for search",
    "all-minilm": "23M, English only: the fastest, and the least precise",
    "nomic-embed-text": "137M, mostly English",
    "granite-embedding:278m": "278M, IBM, multilingual",
    "bge-m3": "567M, multilingual",
    "qwen3-embedding:0.6b": "600M, multilingual",
}
# Names of Ollama models that embed, for the picker's list of what's pulled
EMBEDDING_NAMES = re.compile(r"embed|minilm|bge|e5-|gte|arctic", re.I)
CHECK_SECONDS = 60


def _family(model: str) -> str:
    """"embeddinggemma:300m" → "embeddinggemma"; "hf.co/x/qwen3-embedding-gguf" → "qwen3-embedding-gguf\""""
    return model.rsplit("/", 1)[-1].split(":", 1)[0].lower()


def prompts(model: str, task: str) -> tuple[str, str]:
    """What goes in front of a search for `task`, and in front of each passage, for this model"""

    family = _family(model)
    if family.startswith("embeddinggemma"):
        return "task: search result | query: ", "title: none | text: "
    if family.startswith("qwen3-embedding"):
        return f"Instruct: {task}\nQuery: ", ""
    if family.startswith("nomic-embed-text"):
        return "search_query: ", "search_document: "
    if family.startswith("mxbai-embed-large"):
        return "Represent this sentence for searching relevant passages: ", ""
    return "", ""


def index_id(model: str | None = None) -> str:
    """The index's name for vectors made by `model` (the current one by default): the model, and
    a mark when its passages get words in front of them"""

    model = model or current()
    return f"{model}{PASSAGE_MARK}" if prompts(model, "")[1] else model


# --- The setting ---------------------------------------------------------------------------

def _saved() -> str | None:
    with lock, conn:
        # The server's settings (providers.py makes it too)
        conn.execute("CREATE TABLE IF NOT EXISTS server_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        row = conn.execute("SELECT value FROM server_settings WHERE key = ?", (KEY,)).fetchone()
    return json.loads(row[0]).get("model") if row else None


def _in_use() -> str | None:
    """The model the documents or memories are embedded with already, if any are: a server keeps
    the index it has until an admin picks another model"""

    with lock:
        for table in ("documents", "memories"):
            if db.has_table(conn, table):
                row = conn.execute(f"SELECT embed_model FROM {table} WHERE embed_model IS NOT NULL LIMIT 1").fetchone()
                if row:
                    return row[0].removesuffix(PASSAGE_MARK)
    return None


# Read once, here: current() is called with the database's lock held (it isn't reentrant)
_current = _saved() or ENV_MODEL or _in_use() or DEFAULT


def current() -> str:
    """The model in use: the admins' choice, else EMBED_MODEL, else the one in use, else embeddinggemma"""
    return _current


class EmbeddingError(ValueError):
    """A model that can't be used, said for the admin who picked it"""


def check(model: str):
    """Embed a word with it on Ollama; raises EmbeddingError with what to do when that fails"""

    try:
        response = httpx.post(f"{llm.ollama_host()}/api/embed", json={"model": model, "input": ["labb"]}, timeout=CHECK_SECONDS)
    except httpx.HTTPError as e:
        raise EmbeddingError(f"Couldn't reach Ollama at {llm.ollama_host()}: {e}")
    if response.status_code == 404 or "not found" in response.text:
        raise EmbeddingError(f"{model} isn't pulled on the Ollama server: ollama pull {model}")
    if response.status_code >= 400:
        detail = response.json().get("error") if response.headers.get("content-type", "").startswith("application/json") else response.text
        raise EmbeddingError(f"Ollama couldn't embed with {model}: {detail or response.status_code}. Is it an embedding model?")
    if not (response.json().get("embeddings") or [[]])[0]:
        raise EmbeddingError(f"{model} gave no vector. Is it an embedding model?")


# Called after the model changed, to embed everything again (server/app.py sets them)
on_change: list = []


def save(model: str) -> dict:
    """Use another embedding model: checked first, then everything is embedded again with it"""

    model = (model or "").strip()
    if not model:
        raise EmbeddingError("Pick a model")
    if len(model) > 200:
        raise EmbeddingError("That's not a model's name")
    before = current()
    if model == before:
        return settings()
    check(model)
    with lock, conn:
        conn.execute(f"INSERT INTO server_settings (key, value) VALUES ('{KEY}', ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (json.dumps({"model": model}),))
        # Vectors of another model can't be compared with the new one's: searches would never use them
        conn.execute("DELETE FROM vectors WHERE model <> ?", (index_id(model),))
    global _current
    _current = model
    log.info("The embedding model is now %s (was %s): embedding everything again", model, before)
    for hook in on_change:
        try:
            hook()
        except Exception:
            log.exception("Couldn't start embedding everything again")
    return settings()


# --- For the picker ------------------------------------------------------------------------

def settings() -> dict:
    """The model in use, the default, and the models to pick from: the suggested ones and the
    embedding models pulled on Ollama, each saying whether it's pulled"""

    pulled = set(llm.ollama_models())
    reached = bool(pulled)
    pulled_bare = {name.removesuffix(":latest") for name in pulled}
    names = list(SUGGESTED) + sorted(n for n in pulled_bare if EMBEDDING_NAMES.search(n) and n not in SUGGESTED)
    model = current()
    if model not in names:
        names.append(model)
    return {
        "model": model,
        "default": DEFAULT,
        # Nothing pulled, or Ollama not reached: the picker can't say what's pulled
        "ollama": reached,
        "options": [{"model": name, "about": SUGGESTED.get(name, ""), "pulled": name in pulled_bare} for name in names],
    }
