"""Web search through DuckDuckGo (no key needed), and reading the top results right away"""

import asyncio

from ddgs import DDGS
from ddgs.exceptions import DDGSException

MAX_RESULTS = 10


class SearchError(Exception):
    pass


def _search(query: str, k: int, region: str | None) -> list[dict]:
    try:
        results = DDGS().text(query, max_results=k, **({"region": region} if region else {}))
    except DDGSException as e:
        raise SearchError(f"The search failed ({e})")
    return [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")} for r in results or []]


async def search(query: str, k: int = 5, region: str | None = None) -> list[dict]:
    """The top `k` results: title, url and snippet"""
    return await asyncio.to_thread(_search, query, max(1, min(k, MAX_RESULTS)), region)
