"""Call labb from this app's server, as the app's owner, for what they let it use (on the app's
page: Settings → Use labb). labb gives the app LABB_URL and LABB_API_KEY in its environment then.

Only ever from the server: the key is the app's secret. A page in the browser asks this app's own
server, which asks labb.

    import labb
    answer = labb.ask("Write a haiku about autumn")      # {"text", "conversation", "model", ...}
    more = labb.ask("Another", conversation=answer["conversation"])
    labb.search_documents("invoice from March")          # the owner's documents
    labb.recall("favourite food"); labb.remember("Likes pears")
    labb.agents(); labb.ask_agent(agent_id, "What's new?")
"""

import json
import os
import urllib.error
import urllib.request

URL = os.environ.get("LABB_URL", "").rstrip("/")
KEY = os.environ.get("LABB_API_KEY", "")
# A model's answer can take a while (labb waits up to 5 minutes)
TIMEOUT = 330


class LabbError(Exception):
    pass


def available() -> bool:
    return bool(URL and KEY)


def call(method: str, path: str, body: dict | None = None):
    if not available():
        raise LabbError("This app can't use labb yet: its owner turns that on, on its page (Settings → Use labb)")
    request = urllib.request.Request(
        URL + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail")
        except ValueError:
            detail = e.reason
        raise LabbError(f"labb said {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise LabbError(f"Couldn't reach labb at {URL}: {e.reason}") from None


def ask(message: str, conversation: str | None = None, model: str | None = None) -> dict:
    """A model's answer (no tools): {"text", "conversation", "model", "input_tokens", "output_tokens"}"""
    return call("POST", "/app-api/ask", {"message": message, "conversation": conversation, "model": model})


def agents() -> list[dict]:
    return call("GET", "/app-api/agents")


def ask_agent(agent_id: str, message: str, conversation: str | None = None) -> dict:
    """One of the owner's agents answers, with its own tools"""
    return call("POST", f"/app-api/agents/{agent_id}/ask", {"message": message, "conversation": conversation})


def search_documents(query: str, k: int = 5) -> list[dict]:
    return call("POST", "/app-api/documents/search", {"query": query, "k": k})


def memories() -> list[dict]:
    return call("GET", "/app-api/memories")


def recall(query: str, k: int = 5) -> list[dict]:
    return call("POST", "/app-api/memories/recall", {"query": query, "k": k})


def remember(text: str) -> dict:
    return call("POST", "/app-api/memories", {"text": text})
