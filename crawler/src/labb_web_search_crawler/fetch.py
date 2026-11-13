"""Getting a page: a plain HTTP request first, which is fast and enough for most pages, and
headless Chromium (Playwright) for pages that build their content with JavaScript.

Every hop is checked against private addresses (urls.check): each redirect of a plain
request, and every request the browser makes. Images, media, fonts and trackers aren't
loaded: only the text is wanted.

A rendered page is read once its text has settled, not when the network goes quiet: pages that
wait before loading their content, or that never stop making requests, are read correctly
and without waiting longer than needed. Text in iframes is read too.
"""

import asyncio
import os
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from labb_web_search_crawler import urls

USER_AGENT = "Mozilla/5.0 (compatible; labb-web-search-crawler/0.1; +https://github.com/randomencabulator/labb)"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
MAX_BYTES = int(os.getenv("MAX_BYTES", 15_000_000))
MAX_REDIRECTS = 5
TIMEOUT = float(os.getenv("FETCH_TIMEOUT", 20))
RENDER_TIMEOUT = float(os.getenv("RENDER_TIMEOUT", 30))
# Pages rendered at once; each is a browser tab
RENDERS = int(os.getenv("RENDERS", 2))
# How long a page waits for a free tab
TAB_WAIT = float(os.getenv("TAB_WAIT", 30))
# The longest a rendered page is given to settle
SETTLE_SECONDS = float(os.getenv("SETTLE_SECONDS", 12))
# The site may answer next time: a dropped connection, or a gateway that's briefly down
RETRY_STATUSES = {502, 503, 504}
RETRY_AFTER = 1.0
# Iframes read into a rendered page, and the least text one needs to be worth it (not an ad)
MAX_FRAMES = 5
MIN_FRAME_CHARS = 200
BLOCKED_TYPES = {"image", "media", "font"}
BLOCKED_HOSTS = ("google-analytics.com", "googletagmanager.com", "doubleclick.net", "facebook.net",
                 "hotjar.com", "segment.io", "scorecardresearch.com")


class FetchError(Exception):
    """The page couldn't be loaded; `status` is the site's, if it answered"""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Fetched:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    rendered: bool = False


class _Retry(FetchError):
    """A failure that may not happen again"""


async def plain(url: str, client: httpx.AsyncClient | None = None) -> Fetched:
    """A GET that follows redirects itself, checking each one, and tries again once when the
    connection drops or a gateway is briefly down"""

    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS)
    try:
        try:
            return await _plain(url, client)
        except _Retry:
            await asyncio.sleep(RETRY_AFTER)
        try:
            return await _plain(url, client)
        except _Retry as e:
            raise FetchError(str(e), e.status)
    finally:
        if own:
            await client.aclose()


async def _plain(url: str, client: httpx.AsyncClient) -> Fetched:
    current = url
    try:
        # TIMEOUT is for each step; this is for the whole download, so a slow trickle ends too
        async with asyncio.timeout(TIMEOUT * 3):
            for _ in range(MAX_REDIRECTS + 1):
                target, pinned, extensions = await asyncio.to_thread(urls.pin, current)
                async with client.stream("GET", target, headers=pinned, extensions=extensions, follow_redirects=False) as response:
                    if response.is_redirect and "location" in response.headers:
                        current = urls.normalize(urljoin(current, response.headers["location"]))
                        continue
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise FetchError(f"The page is over {MAX_BYTES // 1_000_000} MB")
                    if response.status_code >= 400:
                        error = _Retry if response.status_code in RETRY_STATUSES else FetchError
                        raise error(f"The site answered {response.status_code}", response.status_code)
                    return Fetched(url, current, response.status_code, response.headers.get("content-type", ""), bytes(body))
    except (httpx.TimeoutException, TimeoutError):
        raise FetchError("The site took too long to answer")
    except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
        raise _Retry(f"Couldn't load the page ({e})")
    except httpx.HTTPError as e:
        raise FetchError(f"Couldn't load the page ({e})")
    raise FetchError("Too many redirects")


class Browser:
    """One headless Chromium for the whole service, started on first use, a tab per page"""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._loop = None

    def _for_this_loop(self):
        """Playwright belongs to the event loop it started in: a new loop starts afresh"""
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop = loop
            self._playwright = self._browser = None
            self._lock = asyncio.Lock()
            self._tabs = asyncio.Semaphore(RENDERS)

    async def _get(self):
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                from playwright.async_api import async_playwright
                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=True)
            return self._browser

    async def close(self):
        if self._loop is not asyncio.get_running_loop():
            return
        async with self._lock:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            self._browser = self._playwright = None

    async def render(self, url: str) -> Fetched:
        await asyncio.to_thread(urls.check, url)
        self._for_this_loop()
        try:
            async with asyncio.timeout(TAB_WAIT):
                await self._tabs.acquire()
        except TimeoutError:
            raise FetchError("The crawler is busy rendering other pages; try again shortly")
        try:
            return await self._render(url)
        except (FetchError, urls.Blocked, urls.BadUrl):
            raise
        except Exception as e:
            # Chromium that won't start or crashed: a page read plainly can still be used (reader.py)
            raise FetchError(f"The page couldn't be rendered ({e})")
        finally:
            self._tabs.release()

    async def _render(self, url: str) -> Fetched:
        browser = await self._get()
        context = await browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
        try:
            await context.route("**/*", _route)
            page = await context.new_page()
            timeout = RENDER_TIMEOUT * 1000
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            except Exception as e:
                raise FetchError(f"Couldn't load the page in the browser ({str(e).splitlines()[0]})")
            if response and response.status >= 400:
                raise FetchError(f"The site answered {response.status}", response.status)
            await _settle(page, SETTLE_SECONDS)
            await _scroll(page)
            await _settle(page, 3)
            await _inline_frames(page)
            final = page.url
            await asyncio.to_thread(urls.check, final)
            html = await page.content()
            return Fetched(url, final, response.status if response else 200, "text/html; charset=utf-8",
                           html.encode(), rendered=True)
        finally:
            # A browser that crashed can't close it, and that mustn't hide what went wrong
            try:
                await context.close()
            except Exception:
                pass


async def _route(route):
    request = route.request
    host = urlsplit(request.url).hostname or ""
    if request.resource_type in BLOCKED_TYPES or any(host == h or host.endswith("." + h) for h in BLOCKED_HOSTS):
        await route.abort()
        return
    if request.url.startswith(("http://", "https://")):
        try:
            await asyncio.to_thread(urls.check, request.url)
        except ValueError:
            await route.abort("blockedbyclient")
            return
    await route.continue_()


# Waits until the page's text stops changing. A page with plenty of text is done after a
# second without change; one with little text, or that says it's loading, is given longer,
# as its content may still be on the way.
SETTLE = """async ({maxMs, minChars}) => {
    const loading = /loading|please wait|just a moment|one moment|checking your browser/i;
    const start = performance.now();
    let last = -1, since = start;
    while (performance.now() - start < maxMs) {
        const text = (document.body ? document.body.innerText : "").trim();
        const now = performance.now();
        if (text.length !== last) {
            last = text.length;
            since = now;
        } else {
            const quiet = text.length >= minChars ? 1000 : loading.test(text.slice(0, 500)) ? maxMs : 3000;
            if (now - since >= quiet) return;
        }
        await new Promise(r => setTimeout(r, 250));
    }
}"""


async def _settle(page, seconds: float):
    # Twice, when the page sends itself elsewhere while it's waited for
    for _ in range(2):
        try:
            await page.evaluate(SETTLE, {"maxMs": seconds * 1000, "minChars": 400})
            return
        except Exception:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=seconds * 1000)
            except Exception:
                return


async def _scroll(page):
    """Down the page in steps, so lazy content loads"""
    try:
        await page.evaluate("""async () => {
            for (let y = 0, i = 0; y < document.body.scrollHeight && i < 20; y += window.innerHeight, i++) {
                window.scrollTo(0, y);
                await new Promise(r => setTimeout(r, 100));
            }
            window.scrollTo(0, 0);
        }""")
    except Exception:
        pass


async def _inline_frames(page):
    """The text of the page's iframes (embedded documents, comments) put where they are, as
    the page's HTML doesn't include it. Ones with little text, like ads, are left out."""
    try:
        frames = await page.query_selector_all("iframe")
    except Exception:
        return
    inlined = 0
    for element in frames:
        if inlined >= MAX_FRAMES:
            break
        try:
            frame = await element.content_frame()
            if frame is None or not frame.url.startswith(("http://", "https://")):
                continue
            html, chars = await frame.evaluate(
                "() => document.body ? [document.body.innerHTML, document.body.innerText.trim().length] : ['', 0]")
            if chars < MIN_FRAME_CHARS or len(html) > MAX_BYTES:
                continue
            await element.evaluate("""(frame, html) => {
                const div = document.createElement("div");
                div.innerHTML = html;
                frame.replaceWith(div);
            }""", html)
            inlined += 1
        except Exception:
            continue


browser = Browser()
