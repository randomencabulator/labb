"""web_search and fetch_page: through labb-web-search-crawler (a fake one here), and without it,
searching DuckDuckGo and reading pages plainly"""

import json

import httpx
import pytest

from labb.tools import web


@pytest.fixture
def crawler(monkeypatch):
    """A fake crawler; returns the requests it got"""

    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        params = dict(request.url.params)
        if request.url.path == "/search":
            results = [{"title": "Magnets", "url": "https://notes.example/magnets", "snippet": "How magnets work"}]
            if params["read"] != "0":
                results[0]["page"] = {"title": "Magnets", "markdown": "# Magnets\n\nThey attract iron."}
            return httpx.Response(200, json=results)
        if params["url"] == "https://blocked.example/":
            return httpx.Response(403, json={"detail": "The site's robots.txt asks crawlers not to read https://blocked.example/"})
        if params["url"] == "https://down.example/":
            return httpx.Response(502, json={"detail": "The site answered 500"})
        return httpx.Response(200, text=f"# Magnets\nSource: {params['url']}\n\nThey attract iron.",
                              headers={"content-type": "text/markdown"})

    monkeypatch.setattr(web, "CRAWLER_URL", "http://crawler:3000")
    monkeypatch.setattr(web, "crawler", lambda: httpx.Client(base_url="http://crawler:3000", transport=httpx.MockTransport(handle)))
    return seen


def test_searching_and_reading_through_the_crawler(crawler):
    results = json.loads(web.web_search.invoke({"query": "magnets", "max_results": 20, "read": 9}))
    assert results[0]["page"]["markdown"].startswith("# Magnets")
    assert dict(crawler[-1].url.params) == {"q": "magnets", "k": "10", "read": "3", "max_chars": "4000"}

    page = web.fetch_page.invoke({"url": "https://notes.example/magnets", "start": 1200})
    assert page.startswith("# Magnets\nSource: https://notes.example/magnets")
    assert dict(crawler[-1].url.params) == {"url": "https://notes.example/magnets", "start": "1200", "max_chars": "20000", "format": "markdown"}

    assert web.fetch_page.invoke({"url": "https://blocked.example/"}) == \
        "Error: The site's robots.txt asks crawlers not to read https://blocked.example/. Try another result."
    assert web.fetch_page.invoke({"url": "https://down.example/"}) == "Error: couldn't read the page (The site answered 500)"


def test_without_a_crawler_private_addresses_arent_read(monkeypatch):
    monkeypatch.setattr(web, "CRAWLER_URL", None)
    for url in ("http://127.0.0.1:11434/api/tags", "http://169.254.169.254/latest/meta-data", "http://localhost/"):
        assert "private or local" in web.fetch_page.invoke({"url": url}), url


def test_without_a_crawler_pages_are_read_plainly(monkeypatch):
    monkeypatch.setattr(web, "CRAWLER_URL", None)
    html = ("<html><head><title>T</title><script>var x = 1;</script></head><body><nav><a href='/'>Home</a></nav>"
            "<h1>Magnets</h1><p>They attract <a href='/iron'>iron</a>.</p><p>" + "More text. " * 3000 + "</p></body></html>")
    monkeypatch.setattr(web.safe_http, "get", lambda url, **kwargs: (httpx.Response(
        200, text=html, headers={"content-type": "text/html"}, request=httpx.Request("GET", url)), url))

    page = web.fetch_page.invoke({"url": "https://notes.example/a/magnets"})
    assert page.startswith("# Magnets\n\nThey attract [iron](https://notes.example/iron).")
    assert "var x" not in page and "Home" not in page
    assert "read on with start=20000" in page
    rest = web.fetch_page.invoke({"url": "https://notes.example/a/magnets", "start": 20000})
    assert "More text" in rest and "read on" not in rest

    monkeypatch.setattr(web, "DDGS", lambda: type("Fake", (), {"text": lambda self, q, max_results: [
        {"title": "Magnets", "href": "https://notes.example/a/magnets", "body": "How magnets work"}]})())
    results = json.loads(web.web_search.invoke({"query": "magnets", "read": 1}))
    assert results[0]["url"] == "https://notes.example/a/magnets" and results[0]["page"]["markdown"].startswith("# Magnets")
