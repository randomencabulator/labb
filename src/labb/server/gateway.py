"""The server as the gateway to Ollama and labb-web-search-crawler: labb on
the PCs sends its model calls, searches and page reads here, with its API key, and the server
forwards them. Ollama and the crawler then only need to be reachable from the server.

Only what labb uses is forwarded: chatting with a model and asking about models, not
pulling, copying or deleting them. Chats and page reads are counted for the usage stats
(usage.py), with the tokens from the last line of each chat's reply. A chat is held to the
models the person may use (Settings → Models, providers.why_not), as in the web chat, and the
list of models shows only those.
"""

import json
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from labb.server import providers
from labb.server.auth_routes import current_user
from labb.tools.web import CRAWLER_URL, crawler_token
from labb import llm
from labb import usage

OLLAMA_PATHS = {"api/chat", "api/show", "api/tags", "api/version", "api/ps"}

# Model replies stream for as long as the model writes; only connecting has a time limit
client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10))
# Reading a page in a headless browser, or a search that reads its results, can take a while
pages = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10))
CRAWLER_PATHS = {"read", "search", "health"}

router = APIRouter()


class ChatMeter:
    """Watches an Ollama chat reply stream past and counts it when it ends. Ollama's last line
    (`"done": true`, or the whole answer without streaming) has the model and the tokens."""

    def __init__(self, user: dict, key_id: str | None, model: str):
        self.user, self.key_id, self.model = user, key_id, model
        self.buffer = b""
        self.final: dict | None = None

    def feed(self, chunk: bytes):
        lines = (self.buffer + chunk).split(b"\n")
        self.buffer = lines.pop()
        for line in lines:
            self.read(line)

    def read(self, line: bytes):
        if b'"done"' not in line:
            return
        try:
            data = json.loads(line)
        except ValueError:
            return
        if isinstance(data, dict) and data.get("done"):
            self.final = data

    def finish(self, status: int):
        self.read(self.buffer)
        final = self.final or {}
        usage.record_gateway(
            self.user["id"], self.key_id, str(final.get("model") or self.model or "unknown"),
            failed=status >= 400 or self.final is None,
            input_tokens=int(final.get("prompt_eval_count") or 0),
            output_tokens=int(final.get("eval_count") or 0),
            eval_ms=round((final.get("eval_duration") or 0) / 1e6),
        )


def why_not(model: str, role: str) -> str | None:
    """Why this person may not use an Ollama model (None: they may), "x" and "x:latest" alike"""

    names = {model, model.removesuffix(":latest")} if model else set()
    if ":" not in model:
        names.add(f"{model}:latest")
    reasons = [providers.why_not(f"ollama:{name}", role) for name in names]
    return None if not reasons or None in reasons else reasons[0]


def requested_model(body: bytes) -> str:
    try:
        return str(json.loads(body).get("model") or "")
    except (ValueError, AttributeError):
        return ""


@router.api_route("/ollama/{path:path}", methods=["GET", "POST"])
async def ollama(path: str, request: Request, user: dict = Depends(current_user)):
    """Ollama's API, streamed through: OLLAMA_HOST=http://<server>:8000/ollama"""

    if path not in OLLAMA_PATHS:
        raise HTTPException(404, f"Only these Ollama endpoints go through the server: {', '.join(sorted(OLLAMA_PATHS))}")
    body = await request.body()
    if path == "api/chat" and (reason := why_not(requested_model(body), user["role"])):
        raise HTTPException(403, reason)
    if path == "api/tags" and user["role"] != "admin":
        return await allowed_tags(user)
    meter = ChatMeter(user, getattr(request.state, "key_id", None), requested_model(body)) if path == "api/chat" else None
    upstream = client.build_request(
        request.method, f"{llm.ollama_host()}/{path}", content=body,
        headers={"content-type": request.headers.get("content-type", "application/json")},
    )
    try:
        response = await client.send(upstream, stream=True)
    except httpx.HTTPError as e:
        if meter:
            meter.finish(502)
        raise HTTPException(502, f"Couldn't reach Ollama: {e}")

    async def chunks():
        # Decoded bytes (aiter_bytes), since Ollama's content-encoding isn't passed on
        try:
            async for chunk in response.aiter_bytes():
                if meter:
                    meter.feed(chunk)
                yield chunk
        finally:
            # Also when labb goes away mid-reply (a stopped reply): counted without its tokens
            if meter:
                meter.finish(response.status_code)

    return StreamingResponse(
        chunks(), status_code=response.status_code,
        media_type=response.headers.get("content-type"), background=BackgroundTask(response.aclose),
    )


async def allowed_tags(user: dict) -> Response:
    """Ollama's list of models, with only those this person may use"""
    try:
        response = await client.get(f"{llm.ollama_host()}/api/tags")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Couldn't reach Ollama: {e}")
    if response.status_code != 200:
        return Response(response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))
    found = response.json()
    found["models"] = [m for m in found.get("models", []) if not why_not(str(m.get("name") or m.get("model") or ""), user["role"])]
    return Response(json.dumps(found), media_type="application/json")


@router.get("/crawler/{path}")
async def crawler(path: str, request: Request, user: dict = Depends(current_user)):
    """labb-web-search-crawler, read through: CRAWLER_URL=http://<server>:8000/crawler on the PCs.
    The PC's key stays here; the crawler gets its own token, if it has one."""

    if path not in CRAWLER_PATHS:
        raise HTTPException(404, "Not something the crawler does")
    if not CRAWLER_URL:
        raise HTTPException(503, "This server has no crawler (CRAWLER_URL isn't set)")
    query = urlencode(list(request.query_params.multi_items()))
    key_id = getattr(request.state, "key_id", None)
    headers = {"Authorization": f"Bearer {token}"} if (token := crawler_token()) else {}
    try:
        response = await pages.get(f"{CRAWLER_URL}/{path}" + (f"?{query}" if query else ""), headers=headers)
    except httpx.HTTPError as e:
        usage.record_gateway(user["id"], key_id, "", page=True, failed=True)
        raise HTTPException(502, f"Couldn't reach the crawler: {e}")
    usage.record_gateway(user["id"], key_id, "", page=True, failed=response.status_code >= 400)
    return Response(response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))
