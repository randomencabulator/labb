"""Searching the user's documents (the knowledge base on the server, see server/documents.py),
and saving new ones.

In the web chat the server uses its own store: server/app.py sets `local_search` and
`local_save`. labb on a PC asks the server, with its API key, so it uses the documents of
whoever logged in. Without either there are no documents, and the tools aren't offered.

An agent given some documents (see server/agents.py) searches only those, which are its
owner's, also when someone else chats with a shared agent. What it saves is the chatter's own,
never the owner's."""

import base64
import json
from pathlib import Path
from typing import Callable

import httpx
from langchain.tools import tool
from langchain_core.runnables import RunnableConfig

from labb import remote

# (user_id, query, k, document ids or None for all) -> results; set by the server
local_search: Callable[[str, str, int, list[str] | None], list[dict]] | None = None
# (user_id, name, data, existing, announce=...) -> document, raising ValueError; set by the server
local_save: Callable[..., dict] | None = None

# What save_document writes is text: a name without one of these suffixes gets .md
SAVED_SUFFIXES = {".md", ".markdown", ".txt", ".csv", ".json", ".yaml", ".yml", ".html", ".htm", ".xml", ".rst", ".log"}


def enabled() -> bool:
    return local_search is not None or remote.server() is not None


@tool
def search_documents(query: str, max_results: int = 5, config: RunnableConfig = None) -> str:
    """Search the user's own documents (PDFs, notes, docs they added) by meaning.

    Returns a JSON list of the closest passages, best first, each with `document`, `page`
    (for PDFs), `score` (0 to 1), `text` and `link`: cite a passage you use as a Markdown
    link to it, e.g. [report.pdf, page 3](link).

    Args:
        query: What to look for, as a question or a few descriptive words
        max_results: How many passages to return (1-10)
    """
    k = max(1, min(max_results, 10))
    try:
        if local_search is not None:
            configurable = (config or {}).get("configurable", {})
            user_id, only = configurable.get("user_id"), None
            agent = configurable.get("agent") or {}
            if isinstance(agent.get("documents"), list):
                user_id, only = agent["owner_id"], agent["documents"]
            if not user_id:
                return "Error: documents can only be searched in someone's chat"
            results = local_search(user_id, query, k, only)
        else:
            response = httpx.post(f"{remote.server()}/documents/search", json={"query": query, "k": k},
                                  headers=remote.headers(), timeout=60)
            if response.status_code == 401:
                return "Error: the server didn't accept this PC's key; run labb --login"
            response.raise_for_status()
            # The links are the server's pages
            results = [{**r, "link": remote.server() + r["link"]} if r.get("link", "").startswith("/") else r
                       for r in response.json()]
    except Exception as e:
        return f"Error: the search failed ({e})"
    if not results:
        return "No passages found: the user has no documents, or none that match."
    return json.dumps(results, ensure_ascii=False)


def document_name(name: str) -> str:
    """A file name for saved text: no folders, and .md unless it names a text format"""

    name = Path(name.replace("\\", "/")).name.strip() or "Document"
    return name if Path(name).suffix.lower() in SAVED_SUFFIXES else name + ".md"


@tool
def save_document(name: str, content: str, replace: bool = False, config: RunnableConfig = None) -> str:
    """Save text as one of the user's documents, which `search_documents` then finds and the
    user can read and delete on the Documents page. Only when the user asks to save or keep
    something as a document (a summary, report, notes, a plan).

    Args:
        name: The document's file name, e.g. "Meeting notes 2026-11-01.md" (Markdown unless
            it ends in .txt, .csv, .json, .html…)
        content: The document's full text, Markdown for .md
        replace: True to replace the text of the user's document with this name (when they
            ask to update it); otherwise saving a name they have already is refused
    """
    name = document_name(name)
    if not content.strip():
        return "Error: the document has no content"
    data = content.encode()
    existing = "replace" if replace else "refuse"
    try:
        if local_save is not None:
            configurable = (config or {}).get("configurable", {})
            if not (user_id := configurable.get("user_id")):
                return "Error: documents can only be saved in someone's chat"
            # What a workflow saves doesn't start the workflows waiting for new documents
            doc = local_save(user_id, name, data, existing, announce=not configurable.get("workflow"))
            link = f"/documents/{doc['id']}"
        else:
            response = httpx.post(f"{remote.server()}/documents",
                                  json={"name": name, "data": base64.b64encode(data).decode(), "existing": existing},
                                  headers=remote.headers(), timeout=60)
            if response.status_code == 401:
                return "Error: the server didn't accept this PC's key; run labb --login"
            if response.status_code == 400:
                return f"Error: {response.json().get('detail', response.text)}"
            response.raise_for_status()
            doc = response.json()
            link = f"{remote.server()}/documents/{doc['id']}"
    except ValueError as e:
        # DocumentError: a name they have, too many documents, too big
        hint = "; pass replace=true to update it, or choose another name" if "called" in str(e) and not replace else ""
        return f"Error: {e}{hint}"
    except Exception as e:
        return f"Error: saving failed ({e})"
    action = "Replaced the text of" if doc.get("replaced") else "Saved"
    return f"{action} [{doc['name']}]({link}) in the user's documents; it's searchable once it's indexed."


# For the web chat's agent editor
LABEL = "Documents"
DESCRIPTION = "Search documents by meaning, and save new ones"

RUN_TOGETHER = ("search_documents",)
TOOLS = [search_documents, save_document]

PROMPT = (
    "The user can add their own documents to the server, and `search_documents` searches them. "
    "Use it when a question may be answered by those documents (their notes, papers, manuals, "
    "or anything they mention having added), not for general knowledge. Answer from the passages "
    "you find, cite each one you used as a Markdown link to it (its `link`, named by the document "
    "and page), and say so if they don't contain the answer. When the user asks you to save or keep "
    "something as a document, write it with `save_document` and give them its link."
)
