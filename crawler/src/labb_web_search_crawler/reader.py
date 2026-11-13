"""Reading a URL as Markdown: from the store if it was read lately, else fetched (plainly, or
in the browser when the page needs it), turned into Markdown and stored. Requests for the
same page at the same time share one read."""

import asyncio
import re
from dataclasses import dataclass

from labb_web_search_crawler import extract, fetch, robots, store, urls
from labb_web_search_crawler.extract import Page

# A plain fetch that gives less text than this is read again in the browser
RENDER_BELOW_CHARS = 400
JS_NEEDED = re.compile(r"enable javascript|javascript (is )?(required|disabled)|requires javascript", re.I)
# The site turned a plain request away, but may let a browser in
RETRY_IN_BROWSER = {401, 403, 406, 429, 503}


class NotAllowed(Exception):
    """robots.txt asks crawlers not to read this page"""


@dataclass
class Read:
    url: str
    final_url: str
    page: Page
    rendered: bool
    cached: bool
    fetched_at: float


_inflight: dict[str, asyncio.Future] = {}


def _needs_browser(fetched: fetch.Fetched, page: Page) -> bool:
    if page.kind not in ("article", "full"):
        return False
    return len(page.markdown) < RENDER_BELOW_CHARS or bool(JS_NEEDED.search(page.markdown[:2000]))


async def _fetch_and_extract(url: str, full: bool, images: bool, render: str) -> tuple[fetch.Fetched, Page]:
    fetched = page = None
    if render != "always":
        try:
            fetched = await fetch.plain(url)
        except fetch.FetchError as e:
            if render == "never" or e.status not in RETRY_IN_BROWSER:
                raise
        if fetched:
            page = await asyncio.to_thread(extract.from_bytes, fetched.body, fetched.content_type, fetched.final_url, full, images)
            if render == "never" or not _needs_browser(fetched, page):
                return fetched, page
    try:
        rendered = await fetch.browser.render(url)
    except fetch.FetchError:
        if page is not None:
            return fetched, page
        raise
    rendered_page = await asyncio.to_thread(extract.from_html, rendered.body.decode(), rendered.final_url, full, images)
    # The browser's version, unless the plain one had more to say
    if page is not None and len(page.markdown) > len(rendered_page.markdown):
        return fetched, page
    return rendered, rendered_page


async def read(raw_url: str, full: bool = False, images: bool = False, render: str = "auto",
               fresh: bool = False, ignore_robots: bool = False) -> Read:
    """The page at `raw_url` as Markdown. Raises urls.BadUrl, urls.Blocked, NotAllowed,
    fetch.FetchError or extract.Unsupported."""

    url = urls.normalize(raw_url)
    await asyncio.to_thread(urls.check, url)
    cache_key = store.key(url, full, images)
    if not fresh and (hit := await asyncio.to_thread(store.get, cache_key)):
        final_url, page, rendered, at = hit
        return Read(url, final_url, page, rendered, True, at)
    if not ignore_robots and not await robots.allowed(url):
        raise NotAllowed(f"The site's robots.txt asks crawlers not to read {url}")

    # One read per page at a time; the others wait for it
    flight_key = f"{cache_key}|{render}"
    if flight_key in _inflight:
        return await asyncio.shield(_inflight[flight_key])
    future = asyncio.get_running_loop().create_future()
    _inflight[flight_key] = future
    try:
        fetched, page = await _fetch_and_extract(url, full, images, render)
        at = await asyncio.to_thread(store.put, cache_key, url, fetched.final_url, page, fetched.rendered)
        result = Read(url, fetched.final_url, page, fetched.rendered, False, at)
        future.set_result(result)
        return result
    except asyncio.CancelledError:
        future.cancel()
        raise
    except Exception as e:
        future.set_exception(e)
        # Nobody else may be waiting for it
        future.exception()
        raise
    finally:
        del _inflight[flight_key]
