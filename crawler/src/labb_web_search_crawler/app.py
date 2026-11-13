"""The HTTP API: /read turns a page into Markdown, /search searches the web and can read the
top results too. Made for a model to call (through labb's tools), not for people.

    GET /read?url=…             {title, markdown, next_start, …}; format=markdown for the text alone
    GET /search?q=…&read=2      [{title, url, snippet, page: {…}}, …]
    GET /health

Long pages come in chunks of `max_chars`, from `start`; `next_start` says where the next one
begins. Set CRAWLER_TOKEN to require `Authorization: Bearer <token>`.
"""

import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from labb_web_search_crawler import extract, fetch, reader, search as searching, settings, urls

def load_token() -> str:
    """CRAWLER_TOKEN, or the one in CRAWLER_TOKEN_FILE, made there on the first start (on a volume
    the server reads it from, see compose.yaml); "" without either: anything that reaches it may use it"""

    if token := os.getenv("CRAWLER_TOKEN", ""):
        return token
    if not (file := os.getenv("CRAWLER_TOKEN_FILE", "")):
        return ""
    path = Path(file)
    try:
        if token := path.read_text().strip():
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(token + "\n")
    os.replace(partial, path)
    return token


TOKEN = load_token()
# What an admin changed in labb's Settings → Services
settings.apply()
MAX_CHARS = 100_000
# Reading search results: at most this many, each this long, each within this time
MAX_READ = 5
READ_SECONDS = 40


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await fetch.browser.close()


app = FastAPI(title="labb-web-search-crawler", lifespan=lifespan)


def authorized(request: Request):
    if TOKEN and not secrets.compare_digest(request.headers.get("authorization", ""), f"Bearer {TOKEN}"):
        raise HTTPException(401, "A token is needed")


def problem(e: Exception) -> HTTPException:
    """What went wrong, said for the model that asked"""
    if isinstance(e, urls.BadUrl):
        return HTTPException(400, str(e))
    if isinstance(e, (urls.Blocked, reader.NotAllowed)):
        return HTTPException(403, str(e))
    if isinstance(e, extract.Unsupported):
        return HTTPException(415, str(e))
    if isinstance(e, fetch.FetchError):
        return HTTPException(502, str(e))
    return HTTPException(500, f"Couldn't read the page ({e})")


def chunk(result: reader.Read, start: int, max_chars: int) -> dict:
    text = result.page.markdown
    start = max(0, min(start, len(text)))
    end = min(len(text), start + max_chars)
    # End at a paragraph or line when one is near, not mid-word
    if end < len(text):
        for mark in ("\n\n", "\n", ". ", " "):
            cut = text.rfind(mark, start + max_chars // 2, end)
            if cut > start:
                end = cut + len(mark)
                break
    page = result.page
    return {
        "url": result.url,
        "final_url": result.final_url,
        "title": page.title,
        "description": page.description,
        "author": page.author,
        "published": page.published,
        "site": page.site,
        "kind": page.kind,
        **page.extra,
        "markdown": text[start:end],
        "start": start,
        "end": end,
        "total_chars": len(text),
        "next_start": end if end < len(text) else None,
        "rendered": result.rendered,
        "cached": result.cached,
        "fetched_at": datetime.fromtimestamp(result.fetched_at, timezone.utc).isoformat(timespec="seconds"),
    }


def as_markdown(data: dict) -> str:
    """The text with a short header: what it is, where it's from, and how to read on"""

    # The page's own heading, when it starts with one, is the title
    starts_with_title = data["markdown"].lstrip().startswith("# ")
    lines = [f"# {data['title']}" if data["title"] and not starts_with_title else "", f"Source: {data['final_url']}"]
    for label, key in (("By", "author"), ("Published", "published"), ("Site", "site")):
        if data.get(key):
            lines.append(f"{label}: {data[key]}")
    if data["start"]:
        lines.append(f"(from character {data['start']:,} of {data['total_chars']:,})")
    body = "\n".join(line for line in lines if line) + "\n\n" + data["markdown"].rstrip()
    if data["next_start"] is not None:
        left = data["total_chars"] - data["next_start"]
        body += f"\n\n[… {left:,} more characters: read on with start={data['next_start']}]"
    return body


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/settings", dependencies=[Depends(authorized)])
def get_settings():
    """For labb's Settings → Services (settings.py)"""
    return settings.described()


@app.put("/settings", dependencies=[Depends(authorized)])
def save_settings(values: dict[str, bool | int | float | None]):
    """Change some; null puts one back to its default"""
    return settings.save(values)


@app.get("/read", dependencies=[Depends(authorized)])
async def read(
    url: str,
    start: int = Query(0, ge=0),
    max_chars: int = Query(20_000, ge=500, le=MAX_CHARS),
    full: bool = False,
    images: bool = False,
    render: Literal["auto", "always", "never"] = "auto",
    fresh: bool = False,
    ignore_robots: bool = False,
    format: Literal["json", "markdown"] = "json",
):
    """A page as Markdown. `full` keeps the whole page instead of its main content, `images`
    keeps the images, `render` decides when the browser is used, and `fresh` reads it again
    instead of using the stored copy."""

    try:
        result = await reader.read(url, full=full, images=images, render=render, fresh=fresh, ignore_robots=ignore_robots)
    except Exception as e:
        raise problem(e)
    data = chunk(result, start, max_chars)
    if format == "markdown":
        return PlainTextResponse(as_markdown(data), media_type="text/markdown; charset=utf-8")
    return data


@app.get("/search", dependencies=[Depends(authorized)])
async def search(
    q: str = Query(..., min_length=1),
    k: int = Query(5, ge=1, le=searching.MAX_RESULTS),
    read: int = Query(0, ge=0, le=MAX_READ),
    max_chars: int = Query(4000, ge=500, le=MAX_CHARS),
    region: str | None = None,
):
    """The top `k` results, and the first `read` of them read as Markdown (up to `max_chars`
    each), so one call can answer a question"""

    try:
        results = await searching.search(q, k, region)
    except searching.SearchError as e:
        raise HTTPException(502, str(e))

    async def read_one(result: dict):
        try:
            got = await asyncio.wait_for(reader.read(result["url"]), READ_SECONDS)
            data = chunk(got, 0, max_chars)
            result["page"] = {key: data[key] for key in ("title", "markdown", "total_chars", "next_start", "published")}
        except asyncio.TimeoutError:
            result["page"] = {"error": "It took too long to read"}
        except Exception as e:
            result["page"] = {"error": problem(e).detail}

    await asyncio.gather(*(read_one(r) for r in results[:read]))
    return results


def main():
    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", 3000)))


if __name__ == "__main__":
    main()
