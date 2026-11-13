"""The embedding model (embedding.py): the words each model wants in front of searches and
passages, Settings → Models → Search picking another, and everything embedded again with it."""

import time
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_ollama import OllamaEmbeddings

from labb import chats, llm
from labb.server import app as server
from labb.server import documents, embedding

from conftest import WordEmbeddings
from test_documents import person, upload, wait_until_indexed  # noqa: F401 (person is a fixture)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """The default model, and no admin's choice saved, before and after each test"""

    def forget():
        with chats.lock, chats.conn:
            chats.conn.execute("DELETE FROM server_settings WHERE key = 'embedding'")

    forget()
    monkeypatch.setattr(embedding, "_current", embedding.DEFAULT)
    monkeypatch.setattr(embedding, "on_change", list(embedding.on_change))
    yield
    forget()


def test_the_default_is_embeddinggemma_but_a_server_keeps_the_model_it_has(monkeypatch):
    assert embedding.DEFAULT == "embeddinggemma" and embedding.current() == "embeddinggemma"
    with chats.lock, chats.conn:
        chats.conn.execute("DELETE FROM documents")
        chats.conn.execute("DELETE FROM memories")
    assert embedding._in_use() is None
    with chats.lock, chats.conn:
        chats.conn.execute("INSERT INTO documents (id, user_id, name, bytes, chars, status, embed_model, created_at, text) "
                           "VALUES ('d-old', ?, 'old.txt', 1, 1, 'ready', 'nomic-embed-text+passage-prompt', '2026-01-01', 'x')", (f"u-{uuid4().hex[:8]}",))
    assert embedding._in_use() == "nomic-embed-text"
    with chats.lock, chats.conn:
        chats.conn.execute("UPDATE documents SET embed_model = 'all-minilm' WHERE id = 'd-old'")
    assert embedding._in_use() == "all-minilm"
    with chats.lock, chats.conn:
        chats.conn.execute("DELETE FROM documents WHERE id = 'd-old'")


@pytest.mark.parametrize("model, query, passage", [
    ("embeddinggemma", "task: search result | query: ", "title: none | text: "),
    ("embeddinggemma:300m", "task: search result | query: ", "title: none | text: "),
    ("qwen3-embedding:0.6b", "Instruct: Find notes\nQuery: ", ""),
    ("nomic-embed-text", "search_query: ", "search_document: "),
    ("hf.co/someone/nomic-embed-text-v1.5-GGUF:Q8_0", "search_query: ", "search_document: "),
    ("all-minilm", "", ""),
])
def test_each_model_gets_the_words_it_wants(model, query, passage):
    assert embedding.prompts(model, "Find notes") == (query, passage)
    # Passages with words in front are an index of their own
    assert embedding.index_id(model) == (f"{model}+passage-prompt" if passage else model)


def test_searches_and_passages_get_their_words(monkeypatch):
    seen = []
    monkeypatch.setattr(OllamaEmbeddings, "embed_query", lambda self, text: seen.append(text) or [1.0])
    monkeypatch.setattr(OllamaEmbeddings, "embed_documents", lambda self, texts: seen.extend(texts) or [[1.0]] * len(texts))
    monkeypatch.setattr(embedding, "_current", "embeddinggemma")
    made = documents.embeddings()
    made.embed_query("tomatoes?")
    made.embed_documents(["Tomatoes like it warm."])
    assert seen == ["task: search result | query: tomatoes?", "title: none | text: Tomatoes like it warm."]

    seen.clear()
    monkeypatch.setattr(embedding, "_current", "qwen3-embedding:0.6b")
    made = documents.embeddings("Find facts")
    made.embed_query("tomatoes?")
    made.embed_documents(["Tomatoes like it warm."])
    assert seen == ["Instruct: Find facts\nQuery: tomatoes?", "Tomatoes like it warm."]


def ollama_embed(status: int = 200, body: dict | None = None):
    def post(url, json, timeout):
        assert url.endswith("/api/embed") and json["input"]
        return httpx.Response(status, json=body if body is not None else {"embeddings": [[0.1, 0.2]]},
                              request=httpx.Request("POST", url))
    return post


def test_admins_see_the_models_to_pick_from(client, accounts, monkeypatch):
    monkeypatch.setattr(llm, "ollama_models", lambda: ["embeddinggemma:latest", "mxbai-embed-large:latest", "gemma4:e4b"])
    found = client.get("/admin/embedding").json()
    assert found["model"] == "embeddinggemma" and found["ollama"]
    options = {o["model"]: o for o in found["options"]}
    assert options["embeddinggemma"]["pulled"] and not options["qwen3-embedding:0.6b"]["pulled"]
    # Pulled embedding models are offered too, chat models aren't
    assert options["mxbai-embed-large"]["pulled"] and "gemma4:e4b" not in options
    assert set(found["progress"]) == {"documents", "memories"}

    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert member.get("/admin/embedding").status_code == 403
    assert member.put("/admin/embedding", json={"model": "all-minilm"}).status_code == 403


def test_a_model_ollama_cant_use_is_refused(client, monkeypatch):
    monkeypatch.setattr(embedding.httpx, "post", ollama_embed(404, {"error": 'model "bge-m3" not found, try pulling it first'}))
    refused = client.put("/admin/embedding", json={"model": "bge-m3"})
    assert refused.status_code == 400 and "ollama pull bge-m3" in refused.json()["detail"]
    monkeypatch.setattr(embedding.httpx, "post", ollama_embed(400, {"error": "this model does not support embeddings"}))
    assert "Is it an embedding model?" in client.put("/admin/embedding", json={"model": "gemma4:e4b"}).json()["detail"]
    assert client.put("/admin/embedding", json={"model": " "}).status_code == 400
    assert embedding.current() == "embeddinggemma"


def test_changing_the_model_embeds_everything_again(client, person, monkeypatch):  # noqa: F811
    user, member = person
    # Word counts stand in for Ollama, whichever model is asked for
    monkeypatch.setattr(documents, "embeddings", lambda task=documents.SEARCH_TASK: WordEmbeddings())
    monkeypatch.setattr(documents, "_store", None)
    doc = upload(member, "notes.md", "# Greenhouse\n\nTomatoes like it warm.")
    wait_until_indexed(member, doc["id"])
    with chats.lock:
        before = {row[0] for row in chats.conn.execute("SELECT DISTINCT model FROM vectors WHERE doc_id = ?", (doc["id"],))}
    assert before == {"embeddinggemma+passage-prompt"}

    monkeypatch.setattr(embedding.httpx, "post", ollama_embed())
    saved = client.put("/admin/embedding", json={"model": "all-minilm"}).json()
    assert saved["model"] == embedding.current() == "all-minilm"
    for _ in range(100):
        if client.get("/admin/embedding").json()["progress"]["documents"]["done"] == saved["progress"]["documents"]["total"]:
            break
        time.sleep(0.05)
    with chats.lock:
        models = {row[0] for row in chats.conn.execute("SELECT DISTINCT model FROM vectors")}
        kept = chats.conn.execute("SELECT embed_model FROM documents WHERE id = ?", (doc["id"],)).fetchone()[0]
    # The old model's vectors are gone, and the document is in the new index
    assert models <= {"all-minilm"} and kept == "all-minilm"
    assert documents.search(user["id"], "warm tomatoes")[0]["document"] == "notes.md"
    # It's kept for the next start
    assert embedding._saved() == "all-minilm"
