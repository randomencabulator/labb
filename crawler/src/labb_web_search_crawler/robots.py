"""robots.txt: sites that ask crawlers to stay away aren't read (unless a request says to).
A robots.txt that can't be fetched allows everything; each one is kept for an hour."""

import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from labb_web_search_crawler import urls
from labb_web_search_crawler.fetch import USER_AGENT

AGENT = "labb-web-search-crawler"
KEEP_SECONDS = 3600

_cache: dict[str, tuple[float, RobotFileParser | None]] = {}


async def allowed(url: str, client: httpx.AsyncClient | None = None) -> bool:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    at, parser = _cache.get(origin, (None, None))
    # Not "at 0": the clock counts from the machine's start, so that would be fresh for its first hour
    if at is None or time.monotonic() - at > KEEP_SECONDS:
        parser = None
        try:
            urls.check(origin)
            own = client is None
            client = client or httpx.AsyncClient(timeout=5, headers={"User-Agent": USER_AGENT})
            try:
                response = await client.get(f"{origin}/robots.txt", follow_redirects=True)
            finally:
                if own:
                    await client.aclose()
            if response.status_code == 200:
                parser = RobotFileParser()
                parser.parse(response.text.splitlines())
        except (httpx.HTTPError, ValueError):
            parser = None
        _cache[origin] = (time.monotonic(), parser)
    return parser is None or parser.can_fetch(AGENT, url)
