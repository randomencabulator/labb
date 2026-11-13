"""The documents' search (RAG): uploading, indexing, searching only your own, deleting, and the
search_documents tool in the web chat and on a PC. The vectors are real (in the test database);
the embeddings are a bag of words, so passages that share words with the query come first."""

import base64
import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from labb import remote
from labb.server import app as server
from labb import chats
from labb.server import auth, documents, embedding, vectors
from labb.tools import documents as documents_tool

from conftest import WordEmbeddings

@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    """No documents, and vectors of the word embeddings, for each test"""

    with chats.lock, chats.conn:
        chats.conn.execute("DELETE FROM documents")
        chats.conn.execute("DELETE FROM vectors WHERE kind = 'documents'")
    store = vectors.Store("documents", WordEmbeddings(), "test")
    monkeypatch.setattr(documents, "_store", store)
    return store


@pytest.fixture
def person(accounts):
    """A new member with a key: (user, client)"""

    user = auth.create_user(f"u{uuid4().hex[:10]}", "a-password")
    key, _ = auth.create_key(user["id"], "laptop")
    return user, TestClient(server.app, headers={"Authorization": f"Bearer {key}"})


def upload(client: TestClient, name: str, data: bytes | str) -> dict:
    raw = data.encode() if isinstance(data, str) else data
    response = client.post("/documents", json={"name": name, "data": base64.b64encode(raw).decode()})
    assert response.status_code == 201, response.text
    return response.json()


def wait_until_indexed(client: TestClient, doc_id: str) -> dict:
    for _ in range(100):
        doc = next(d for d in client.get("/documents").json() if d["id"] == doc_id)
        if doc["status"] != "indexing":
            return doc
        time.sleep(0.05)
    raise AssertionError("still indexing")


def pdf(*pages: str) -> bytes:
    """A small PDF with one line of text on each page"""

    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objects)} 0 R "
                       "/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_an_upload_is_indexed_and_found_by_its_words(person):
    _, client = person
    garden = upload(client, "garden.md", "# Tomatoes\n\nWater the tomatoes every morning in summer.")
    upload(client, "car.txt", "Change the engine oil of the car every ten thousand kilometres.")
    assert garden["status"] == "indexing"

    doc = wait_until_indexed(client, garden["id"])
    assert (doc["status"], doc["name"], doc["chunks"]) == ("ready", "garden.md", 1)

    results = client.post("/documents/search", json={"query": "when to water tomatoes", "k": 2}).json()
    assert results[0]["document"] == "garden.md"
    assert results[0]["score"] > results[-1]["score"]
    # The heading added for the embedding isn't part of the passage
    assert results[0]["text"].startswith("# Tomatoes")


def test_pdfs_are_searched_by_page(person):
    _, client = person
    doc = upload(client, "manual.pdf", pdf("Unpacking the printer", "Replacing the toner cartridge"))
    doc = wait_until_indexed(client, doc["id"])
    assert (doc["status"], doc["pages"], doc["chunks"]) == ("ready", 2, 2)

    best = client.post("/documents/search", json={"query": "replacing toner"}).json()[0]
    assert (best["document"], best["page"], best["text"]) == ("manual.pdf", 2, "Replacing the toner cartridge")


def test_everyone_finds_and_deletes_only_their_own(person, accounts):
    _, client = person
    doc = upload(client, "secret.txt", "The vault code is in the blue notebook.")
    wait_until_indexed(client, doc["id"])

    bob = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert doc["id"] not in [d["id"] for d in bob.get("/documents").json()]
    assert bob.post("/documents/search", json={"query": "vault code"}).json() == []
    assert bob.delete(f"/documents/{doc['id']}").status_code == 404

    assert client.post("/documents/search", json={"query": "vault code"}).json()[0]["document"] == "secret.txt"


def test_deleting_takes_the_chunks_out_too(person, store):
    _, client = person
    doc = upload(client, "notes.txt", "Some notes about the meeting on Tuesday.")
    wait_until_indexed(client, doc["id"])
    assert store.ids(doc["id"]) == [f"{doc['id']}:0"]

    assert client.delete(f"/documents/{doc['id']}").status_code == 204
    assert store.ids(doc["id"]) == []
    assert client.get("/documents").json() == []
    assert client.post("/documents/search", json={"query": "meeting"}).json() == []


def test_long_documents_are_split_into_overlapping_chunks():
    text = "\n\n".join(f"Paragraph {n}. " + "word " * 60 for n in range(40))
    chunks = documents.chunks("d1", "u1", "long.txt", [(None, text)])
    assert len(chunks) > 10
    assert all(len(c.text) <= documents.CHUNK_CHARS + len("long.txt\n\n") for c in chunks)
    assert all(c.text.startswith("long.txt\n\n") for c in chunks)
    assert [c.meta["start"] for c in chunks] == sorted(c.meta["start"] for c in chunks)
    assert {c.user_id for c in chunks} == {"u1"} and {c.doc_id for c in chunks} == {"d1"}


@pytest.mark.parametrize("name, data, error", [
    ("photo.png", b"\x89PNG\r\n\x1a\n\0\0\0", "only PDFs, Word documents"),
    ("empty.txt", b"  \n", "is empty"),
    ("broken.pdf", b"%PDF-1.4 not really", "isn't a PDF that can be read"),
])
def test_files_that_cant_be_read_are_refused(person, name, data, error):
    _, client = person
    response = client.post("/documents", json={"name": name, "data": base64.b64encode(data).decode()})
    assert response.status_code == 400 and error in response.json()["detail"]
    assert client.post("/documents", json={"name": "x.txt", "data": "not base64!"}).status_code == 400


def test_documents_embedded_with_another_model_are_indexed_again(person):
    user, client = person
    doc = upload(client, "a.txt", "Alpha beta gamma.")
    wait_until_indexed(client, doc["id"])
    with documents.lock, documents.conn:
        documents.conn.execute("UPDATE documents SET embed_model = 'old-model' WHERE id = ?", (doc["id"],))

    assert documents.reindex_stale() == 1
    wait_until_indexed(client, doc["id"])
    with documents.lock:
        model = documents.conn.execute("SELECT embed_model FROM documents WHERE id = ?", (doc["id"],)).fetchone()[0]
    assert model == embedding.index_id()
    assert documents.search(user["id"], "beta")[0]["document"] == "a.txt"


def test_the_tool_in_the_web_chat_searches_the_users_documents(person):
    user, client = person
    doc = upload(client, "recipes.txt", "Bake the bread at 220 degrees for 35 minutes.")
    wait_until_indexed(client, doc["id"])

    # server/app.py sets local_search when it starts
    assert documents_tool.local_search is documents.search and documents_tool.enabled()
    found = json.loads(documents_tool.search_documents.invoke(
        {"query": "bread baking temperature"}, config={"configurable": {"user_id": user["id"]}}))
    assert found[0]["document"] == "recipes.txt"
    assert documents_tool.search_documents.invoke({"query": "bread"}).startswith("Error:")


def test_the_tool_on_a_pc_asks_the_server(person, monkeypatch):
    _, client = person
    doc = upload(client, "pc.txt", "The router password is on the sticker under the router.")
    wait_until_indexed(client, doc["id"])

    monkeypatch.setattr(documents_tool, "local_search", None)
    monkeypatch.setattr(remote, "server", lambda: "http://server")
    monkeypatch.setattr(documents_tool.httpx, "post", lambda url, **kwargs: client.post(url.removeprefix("http://server"), json=kwargs["json"]))
    found = json.loads(documents_tool.search_documents.invoke({"query": "router password"}))
    assert found[0]["document"] == "pc.txt"

    # Without a server to ask, there's nothing to search
    monkeypatch.setattr(remote, "server", lambda: None)
    assert not documents_tool.enabled()


def test_the_tool_saves_a_document_in_the_web_chat(person):
    user, client = person
    config = {"configurable": {"user_id": user["id"]}}
    assert documents_tool.local_save is documents.save
    saved = documents_tool.save_document.invoke(
        {"name": "notes/Bread plan", "content": "# Bread\n\nBake the rye at 230 degrees."}, config=config)
    assert saved.startswith("Saved [Bread plan.md](/documents/"), saved
    doc = next(d for d in client.get("/documents").json() if d["name"] == "Bread plan.md")
    assert wait_until_indexed(client, doc["id"])["status"] == "ready"
    assert documents.search(user["id"], "rye degrees")[0]["document"] == "Bread plan.md"

    # A name they have already: refused, unless it replaces that document's text (same id)
    again = documents_tool.save_document.invoke({"name": "Bread plan.md", "content": "Other"}, config=config)
    assert again.startswith("Error:") and "replace=true" in again
    replaced = documents_tool.save_document.invoke(
        {"name": "Bread plan.md", "content": "Bake the spelt at 200 degrees.", "replace": True}, config=config)
    assert replaced.startswith("Replaced the text of [Bread plan.md]")
    assert [d["id"] for d in client.get("/documents").json()] == [doc["id"]]
    wait_until_indexed(client, doc["id"])
    assert "spelt" in documents.pages_of(user["id"], doc["id"])["pages"][0]
    assert "rye" not in documents.search(user["id"], "spelt")[0]["text"]

    assert documents_tool.save_document.invoke({"name": "x", "content": "  "}, config=config).startswith("Error:")
    assert documents_tool.save_document.invoke({"name": "x", "content": "Hi"}).startswith("Error:")
    assert documents_tool.document_name("data.csv") == "data.csv" and documents_tool.document_name("a.pdf") == "a.pdf.md"


def test_a_document_saved_in_a_workflow_starts_no_workflows(person, monkeypatch):
    user, _ = person
    added = []
    monkeypatch.setattr(documents, "on_added", [lambda user_id, doc: added.append(doc["name"])])
    documents_tool.save_document.invoke({"name": "a", "content": "A"}, config={"configurable": {"user_id": user["id"]}})
    documents_tool.save_document.invoke({"name": "b", "content": "B"},
                                        config={"configurable": {"user_id": user["id"], "workflow": True}})
    assert added == ["a.md"]


def test_the_tool_on_a_pc_saves_through_the_server(person, monkeypatch):
    _, client = person
    monkeypatch.setattr(documents_tool, "local_save", None)
    monkeypatch.setattr(remote, "server", lambda: "http://server")
    monkeypatch.setattr(documents_tool.httpx, "post", lambda url, **kwargs: client.post(url.removeprefix("http://server"), json=kwargs["json"]))
    saved = documents_tool.save_document.invoke({"name": "Summary.txt", "content": "The meeting moved to Friday."})
    assert saved.startswith("Saved [Summary.txt](http://server/documents/")
    assert documents_tool.save_document.invoke({"name": "Summary.txt", "content": "x"}).startswith("Error: You have a document called")
    assert [d["name"] for d in client.get("/documents").json()] == ["Summary.txt"]

    # Uploads from the Documents page still add another of the same name
    upload(client, "Summary.txt", "Second")
    assert [d["name"] for d in client.get("/documents").json()] == ["Summary.txt", "Summary.txt"]


def make_docx(paragraphs: list[tuple[str | None, str]], table: list[list[str]] | None = None) -> bytes:
    """A minimal Word document: (style, text) paragraphs, and a table after them"""
    import io, zipfile
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(
        "<w:p>" + (f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else "") + f"<w:r><w:t>{text}</w:t></w:r></w:p>"
        for style, text in paragraphs)
    if table:
        body += "<w:tbl>" + "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row)
                                    + "</w:tr>" for row in table) + "</w:tbl>"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", f"<w:document {ns}><w:body>{body}</w:body></w:document>")
    return out.getvalue()


def test_word_documents_web_pages_and_markdown_images_are_read_as_text():
    word = documents.extract("plan.docx", make_docx([("Heading1", "Garden plan"), (None, "Plant beans in May.")],
                                                    [["Crop", "Month"], ["Peas", "March"]]))[0][1]
    assert word.startswith("# Garden plan") and "Plant beans in May." in word and "| Peas | March |" in word
    page = documents.extract("page.html", b"<html><head><title>x</title><style>p{}</style></head><body><nav>Menu</nav>"
                             b"<h2>Soil</h2><p>Add compost &amp; mulch.</p><script>evil()</script>"
                             b"<ul><li>One</li></ul><img alt='A raised bed'></body></html>")[0][1]
    assert page == "## Soil\n\nAdd compost & mulch.\n\n- One\n\n[image: A raised bed]"
    notes = documents.extract("notes.md", b"See ![the bed](data:image/png;base64,iVBORw0KGgo=) here.")[0][1]
    assert notes == "See [image: the bed] here."
    with pytest.raises(documents.DocumentError):
        documents.extract("broken.docx", b"PK not really")


def test_search_results_link_to_their_passage(person):
    _, client = person
    doc = upload(client, "plan.docx", make_docx([("Heading1", "Beans"), (None, "Sow the climbing beans in May.")]))
    wait_until_indexed(client, doc["id"])
    hit = client.post("/documents/search", json={"query": "when to sow beans", "k": 1}).json()[0]
    assert hit["link"] == f"/documents/{doc['id']}?at=0"
    text = client.get(f"/documents/{doc['id']}/text").json()
    assert text["name"] == "plan.docx" and text["pages"][0].startswith("# Beans") and text["paged"] is False
