"""Each user's documents, searchable by meaning and by keywords (RAG): uploads from the web
chat, split into chunks, embedded with Ollama and kept in the server's database (vectors.py).

- The `documents` table keeps each upload's text and status, so the index can always be
  rebuilt from it. The `vectors` table keeps the chunks and their embeddings, with the user,
  document, name and page of each.
- A document is indexed in a background thread: `indexing`, then `ready` or `error`.
- Vectors of different models can't be compared, so each model has rows of its own. When the
  embedding model changes (embedding.py: Settings → Models), every document is embedded again in
  the background; one whose chunks aren't there is embedded again at start.
- Searches only ever see the asking user's chunks.
"""

import html
import io
import logging
import re
import threading
import uuid
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

from labb import llm
from labb.chats import conn, lock
from labb.server import embedding, speech, vectors
from labb.usage import stamp
from labb.server import limits

log = logging.getLogger(__name__)


# Characters per chunk: big enough to keep a paragraph's meaning, small enough to be precise
CHUNK_CHARS = 1000
CHUNK_OVERLAP = 150
# Chunks embedded per call to Ollama
BATCH = 64
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".yaml", ".yml", ".html", ".htm", ".xml", ".log", ".docx"}

# What a search of documents is for, for the embedding models that want to know (embedding.prompts)
SEARCH_TASK = "Given a question, retrieve passages of the user's documents that answer it"

with lock, conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS documents ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, bytes INTEGER NOT NULL, "
        "chars INTEGER NOT NULL, pages INTEGER, chunks INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, "
        "error TEXT, embed_model TEXT, created_at TEXT NOT NULL, text TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS documents_by_user ON documents (user_id)")

COLUMNS = "id, name, bytes, chars, pages, chunks, status, error, created_at"


class DocumentError(ValueError):
    """An upload that can't be indexed, said for the person who sent it"""


class Embeddings(OllamaEmbeddings):
    """Ollama embeddings, with the words each model wants in front of searches and passages
    (embedding.prompts)"""

    query_prefix: str = ""
    document_prefix: str = ""

    def embed_query(self, text: str) -> list[float]:
        return super().embed_query(self.query_prefix + text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return super().embed_documents([self.document_prefix + text for text in texts] if self.document_prefix else texts)


_store: vectors.Store | None = None
# Called with (user_id, document) after a document is added: Studio's "document added" event
on_added: list = []


def embeddings(task: str = SEARCH_TASK) -> Embeddings:
    """The embedding model on Ollama (embedding.py), for searches that are for `task`"""
    model = embedding.current()
    query, passage = embedding.prompts(model, task)
    return Embeddings(model=model, base_url=llm.ollama_host(), query_prefix=query, document_prefix=passage)


def store() -> vectors.Store:
    """The documents' vectors"""

    global _store
    if _store is None:
        _store = vectors.Store("documents", embeddings(), embedding.index_id())
    return _store


def reset_store():
    """The embedding model changed: the next search or index uses the new one"""
    global _store
    _store = None


# --- Reading and splitting -----------------------------------------------------------

# Word's XML namespace, and the paragraph styles that are headings ("Heading1", "Title")
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
WORD_HEADING = re.compile(r"^(?:Heading|heading )?(\d)$|^Title$")
# Markdown images whose picture is in the file (data: URLs): kept as their description
INLINE_IMAGE = re.compile(r"!\[([^\]]*)\]\(data:[^)]*\)")


def docx_text(name: str, data: bytes) -> str:
    """A Word document's text, its headings as Markdown headings and tables as rows"""

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        raise DocumentError(f"{name} isn't a Word document that can be read ({e})")
    lines = []
    body = root.find(f"{WORD}body")
    for block in body if body is not None else []:
        if block.tag == f"{WORD}tbl":
            for row in block.iter(f"{WORD}tr"):
                cells = ["".join(t.text or "" for t in cell.iter(f"{WORD}t")).strip() for cell in row.iter(f"{WORD}tc")]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
            continue
        if block.tag != f"{WORD}p":
            continue
        text = "".join(t.text or "" if t.tag == f"{WORD}t" else "\t" if t.tag == f"{WORD}tab" else "\n"
                       for t in block.iter() if t.tag in (f"{WORD}t", f"{WORD}tab", f"{WORD}br")).strip()
        style = block.find(f"{WORD}pPr/{WORD}pStyle")
        level = WORD_HEADING.match(style.get(f"{WORD}val", "")) if style is not None else None
        if text and level:
            lines.append("#" * min(int(level.group(1) or 1), 6) + " " + text)
        elif text:
            lines.append(text)
        lines.append("")
    return "\n".join(lines).strip()


class HtmlText(HTMLParser):
    """A web page's text: headings as Markdown headings, paragraphs and list items on lines of
    their own, without scripts, styles or navigation"""

    BLOCKS = {"p", "div", "section", "article", "br", "tr", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "table"}
    SKIPPED = {"script", "style", "noscript", "template", "svg", "nav", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIPPED:
            self.skipping += 1
        elif tag in self.BLOCKS:
            self.out.append("\n\n" + ("#" * int(tag[1]) + " " if re.fullmatch(r"h[1-6]", tag) else "- " if tag == "li" else ""))
        elif tag == "img" and (alt := dict(attrs).get("alt")):
            self.out.append(f"[image: {alt}]")

    def handle_endtag(self, tag):
        if tag in self.SKIPPED:
            self.skipping = max(0, self.skipping - 1)
        elif tag in self.BLOCKS:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skipping:
            self.out.append(re.sub(r"\s+", " ", data))

    def text(self) -> str:
        lines = (line.strip() for line in "".join(self.out).splitlines())
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_text(text: str) -> str:
    parser = HtmlText()
    parser.feed(text)
    parser.close()
    return html.unescape(parser.text())


def extract(name: str, data: bytes) -> list[tuple[int | None, str]]:
    """A file's text as (page, text) pairs: one per page of a PDF, one for anything else.
    Word documents and web pages become Markdown-like text; Markdown keeps its images'
    descriptions, not the pictures; a recording is what's said in it (speech.py)."""

    suffix = Path(name).suffix.lower()
    if speech.is_audio(name):
        try:
            text = speech.transcribe(data, name)
        except speech.SpeechError as e:
            raise DocumentError(f"{name}: {e}")
        if not text:
            raise DocumentError(f"{name}: nothing is said in it")
        return [(None, text)]
    if suffix == ".docx":
        if not (text := docx_text(name, data).replace("\0", "")):
            raise DocumentError(f"{name} has no text")
        return [(None, text)]
    if suffix == ".pdf" or data.startswith(b"%PDF"):
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        try:
            reader = PdfReader(io.BytesIO(data))
            # Postgres can't keep NUL characters in text, and they mean nothing to a reader
            pages = [(number, (page.extract_text() or "").replace("\0", "")) for number, page in enumerate(reader.pages, start=1)]
        except (PdfReadError, ValueError, KeyError) as e:
            raise DocumentError(f"{name} isn't a PDF that can be read ({e})")
        if not any(text.strip() for _, text in pages):
            raise DocumentError(f"{name} has no text to read (a scanned PDF needs OCR first)")
        return pages
    if b"\0" in data[:8192]:
        raise DocumentError(f"{name}: only PDFs, Word documents (.docx), text files (Markdown, web pages, code, CSV…) "
                            "and recordings (MP3, M4A, WAV, WebM, OGG, FLAC) can be added")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    text = text.replace("\0", "")
    if suffix in (".html", ".htm"):
        text = html_text(text)
    elif suffix in (".md", ".markdown"):
        text = INLINE_IMAGE.sub(lambda image: f"[image: {image.group(1) or 'no description'}]", text)
    if not text.strip():
        raise DocumentError(f"{name} is empty")
    return [(None, text)]


def splitter(name: str) -> RecursiveCharacterTextSplitter:
    """Markdown splits at headings first; everything else at paragraphs, lines, then words"""

    # Word documents and web pages are Markdown-like once extracted
    if Path(name).suffix.lower() in {".md", ".markdown", ".docx", ".html", ".htm"}:
        return RecursiveCharacterTextSplitter.from_language(
            Language.MARKDOWN, chunk_size=CHUNK_CHARS, chunk_overlap=CHUNK_OVERLAP, add_start_index=True,
        )
    return RecursiveCharacterTextSplitter(chunk_size=CHUNK_CHARS, chunk_overlap=CHUNK_OVERLAP, add_start_index=True)


def chunks(doc_id: str, user_id: str, name: str, pages: list[tuple[int | None, str]]) -> list[vectors.Item]:
    """The chunks to embed. Each starts with the document's name (and page), so a chunk that
    doesn't say what it's about is still found by it."""

    split = splitter(name)
    out = []
    for page, text in pages:
        heading = f"{name}, page {page}" if page else name
        for piece in split.create_documents([text]):
            meta = {"name": name, "start": piece.metadata["start_index"]}
            if page:
                meta["page"] = page
            out.append(vectors.Item(f"{doc_id}:{len(out)}", user_id, f"{heading}\n\n{piece.page_content}", doc_id, meta))
    return out


# --- Documents -------------------------------------------------------------------------

PAGE_BREAK = "\f"


def _read(name: str, data: bytes) -> tuple[str, list[tuple[int | None, str]], str]:
    """An upload's clean name, pages and text to keep. Raises DocumentError if it can't be read."""

    name = Path(name).name.strip() or "document"
    if len(data) > (most := limits.get("document_mb")) * 1_000_000:
        raise DocumentError(f"{name} is over {most} MB")
    pages = extract(name, data)
    # Pages are kept apart by form feeds, so a PDF is indexed with its page numbers again
    return name, pages, PAGE_BREAK.join(text.replace(PAGE_BREAK, "\n") for _, text in pages)


def add(user_id: str, name: str, data: bytes, announce: bool = True) -> dict:
    """Save an upload and start indexing it. Raises DocumentError if it can't be. Without
    `announce`, it doesn't start the workflows waiting for a new document (one saved by a workflow)."""

    with lock:
        count = conn.execute("SELECT COUNT(*) FROM documents WHERE user_id = ?", (user_id,)).fetchone()[0]
    if count >= (most := limits.get("documents_per_person")):
        raise DocumentError(f"You have {most} documents already; delete some first")
    name, pages, text = _read(name, data)
    doc_id = uuid.uuid4().hex
    with lock, conn:
        conn.execute(
            "INSERT INTO documents (id, user_id, name, bytes, chars, pages, status, created_at, text) "
            "VALUES (?, ?, ?, ?, ?, ?, 'indexing', ?, ?)",
            (doc_id, user_id, name, len(data), len(text), len(pages) if pages[0][0] else None, stamp(), text),
        )
    # Read before indexing starts, so the answer always says "indexing"
    doc = get(user_id, doc_id)
    start_indexing(doc_id)
    for hook in on_added if announce else ():
        try:
            hook(user_id, doc)
        except Exception:
            log.exception("A hook for a new document failed")
    return doc


def save(user_id: str, name: str, data: bytes, existing: str = "add", announce: bool = True) -> dict:
    """Save a document, given what to do when one of yours has that name already: "add" another,
    "replace" its text (it keeps its id, so agents given it still have it), or "refuse".
    The document says whether it `replaced` one. Raises DocumentError."""

    clean = Path(name).name.strip() or "document"
    with lock:
        row = conn.execute("SELECT id FROM documents WHERE user_id = ? AND name = ? ORDER BY created_at DESC",
                           (user_id, clean)).fetchone()
    if row is None or existing == "add":
        return {**add(user_id, name, data, announce=announce), "replaced": False}
    if existing == "refuse":
        raise DocumentError(f"You have a document called {clean} already")
    clean, pages, text = _read(name, data)
    with lock, conn:
        conn.execute(
            "UPDATE documents SET bytes = ?, chars = ?, pages = ?, chunks = 0, status = 'indexing', error = NULL, "
            "created_at = ?, text = ? WHERE id = ?",
            (len(data), len(text), len(pages) if pages[0][0] else None, stamp(), text, row[0]),
        )
    doc = get(user_id, row[0])
    start_indexing(row[0])
    return {**doc, "replaced": True}


def index(doc_id: str, tell: bool = False):
    """Embed one document's chunks with the current model, replacing any it had there; with `tell`,
    a failure goes to its owner's inbox (not for the ones indexed again at a start, said before)"""

    with lock:
        row = conn.execute("SELECT user_id, name, pages, text FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        return
    user_id, name, page_count, text = row
    pieces = text.split(PAGE_BREAK)
    pages = list(enumerate(pieces, start=1)) if page_count else [(None, text)]
    try:
        docs = chunks(doc_id, user_id, name, pages)
        store().replace(docs, doc_id=doc_id, batch=BATCH)
        status, error = "ready", None
    except Exception as e:
        hint = (f" (on the Ollama server: ollama pull {embedding.current()}, or pick another model in Settings → Models)"
                if "not found" in str(e) else "")
        status, error, docs = "error", f"Couldn't index it: {e}{hint}", []
    with lock, conn:
        conn.execute(
            "UPDATE documents SET status = ?, error = ?, chunks = ?, embed_model = ? WHERE id = ?",
            (status, error, len(docs), embedding.index_id(), doc_id),
        )
    # Deleted while it was being indexed: take its chunks out again
    if get_owner(doc_id) is None and status == "ready":
        store().delete(doc_id=doc_id)
    elif tell and status == "error":
        from labb.server import inbox
        inbox.add(user_id, "document", f"Couldn't index {name}", error, link=f"/documents/{doc_id}",
                  key=f"document:{doc_id}:error", level="error")


def start_indexing(doc_id: str):
    threading.Thread(target=index, args=(doc_id, True), daemon=True, name=f"index-{doc_id[:8]}").start()


def reindex_stale() -> int:
    """At start: index the documents embedded with another model, whose indexing the last
    shutdown interrupted, that failed (e.g. before the model was pulled), or whose chunks
    aren't in the vectors. Returns how many there are."""

    with lock, conn:
        ids = [row[0] for row in conn.execute(
            "SELECT id FROM documents d WHERE status IN ('indexing', 'error') OR embed_model IS DISTINCT FROM ? "
            "OR (chunks > 0 AND NOT EXISTS (SELECT 1 FROM vectors v WHERE v.kind = 'documents' AND v.model = ? "
            "AND v.doc_id = d.id))",
            (embedding.index_id(), store().model),
        )]
        conn.executemany("UPDATE documents SET status = 'indexing' WHERE id = ?", [(i,) for i in ids])

    def run():
        for doc_id in ids:
            index(doc_id)

    if ids:
        threading.Thread(target=run, daemon=True, name="reindex").start()
    return len(ids)


def as_dict(row: tuple) -> dict:
    return dict(zip([c.strip() for c in COLUMNS.split(",")], row))


def list_documents(user_id: str) -> list[dict]:
    """Your documents, newest first"""
    with lock:
        rows = conn.execute(f"SELECT {COLUMNS} FROM documents WHERE user_id = ? ORDER BY created_at DESC, name", (user_id,))
        return [as_dict(row) for row in rows]


def get(user_id: str, doc_id: str) -> dict | None:
    with lock:
        row = conn.execute(f"SELECT {COLUMNS} FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id)).fetchone()
    return as_dict(row) if row else None


def get_owner(doc_id: str) -> str | None:
    with lock:
        row = conn.execute("SELECT user_id FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return row[0] if row else None


def delete(user_id: str, doc_id: str) -> bool:
    """Delete one of your documents and its chunks. False if you have no such document."""

    with lock, conn:
        deleted = conn.execute("DELETE FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id)).rowcount
        if deleted:
            conn.execute("DELETE FROM vectors WHERE kind = 'documents' AND doc_id = ?", (doc_id,))
    return bool(deleted)


def search(user_id: str, query: str, k: int = 5, only: list[str] | None = None) -> list[dict]:
    """The `k` chunks of your documents (or of the ones in `only`) that best match `query`,
    by meaning and by its words, best first, each with its document's name, page and how
    close it is in meaning (0 to 1)"""

    if not query.strip() or only == []:
        return []
    with lock:
        ready = conn.execute("SELECT id FROM documents WHERE user_id = ? AND status = 'ready'", (user_id,)).fetchall()
    ids = {row[0] for row in ready} & (set(only) if only is not None else {row[0] for row in ready})
    if not ids:
        return []
    hits = store().search(user_id, query, max(1, min(k, 20)), doc_ids=sorted(ids), hybrid=True)
    return [
        {
            "document": hit.meta["name"],
            **({"page": hit.meta["page"]} if "page" in hit.meta else {}),
            "score": round(hit.score, 3),
            # Without the heading `chunks` added for the embedding
            "text": hit.text.split("\n\n", 1)[-1],
            # The web chat's Documents page opens there, with the passage marked
            "link": passage_link(hit.id.rsplit(":", 1)[0], hit.meta.get("page"), hit.meta.get("start", 0)),
        }
        for hit in hits
    ]


def passage_link(doc_id: str, page: int | None, start: int) -> str:
    return f"/documents/{doc_id}?" + (f"page={page}&" if page else "") + f"at={start}"


def pages_of(user_id: str, doc_id: str) -> dict | None:
    """One of your documents' text, page by page, to read in the web chat; None if you have no such document"""

    with lock:
        row = conn.execute("SELECT name, pages, text FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id)).fetchone()
    if row is None:
        return None
    name, page_count, text = row
    return {"id": doc_id, "name": name, "pages": text.split(PAGE_BREAK) if page_count else [text], "paged": bool(page_count)}
