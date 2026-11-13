"""The crawler: URLs and private addresses, HTML/feeds/PDF/text to Markdown, encodings, reading
through the API (chunks, the store, robots.txt, errors and retries, the browser for JavaScript
pages, late content and iframes) and search."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from labb_web_search_crawler import app as service
from labb_web_search_crawler import extract, fetch, search, store, urls

from conftest import ARTICLE, ATOM, LATIN, RSS, Site, pdf

client = TestClient(service.app)


def chromium_installed() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


def test_urls_are_normalized_and_private_addresses_refused(monkeypatch):
    assert urls.normalize("example.com/a#top") == "https://example.com/a"
    assert urls.normalize(" HTTP://Example.com ") == "http://example.com/"
    for bad in ["", "ftp://example.com/x", "javascript:alert(1)", "https://"]:
        with pytest.raises(urls.BadUrl):
            urls.normalize(bad)

    monkeypatch.setattr(urls, "ALLOW_PRIVATE", False)
    for blocked in ["http://127.0.0.1/", "http://10.0.0.5/", "http://192.168.1.1/admin", "http://169.254.169.254/latest",
                    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://0.0.0.0/"]:
        with pytest.raises(urls.Blocked):
            urls.check(blocked)
    urls.check("http://93.184.216.34/")
    # Names are checked by what they resolve to
    monkeypatch.setattr(urls, "resolve", lambda host: {"localhost": ("127.0.0.1",), "router.home": ("192.168.1.1",),
                                                       "example.com": ("93.184.216.34",)}[host])
    for blocked in ["http://localhost:8000/", "https://router.home/"]:
        with pytest.raises(urls.Blocked):
            urls.check(blocked)
    urls.check("https://example.com/")


def test_html_becomes_markdown_with_its_details():
    page = extract.from_html(ARTICLE, "https://notes.example/physics/magnets")
    assert page.kind == "article" and page.title == "How magnets work"
    assert page.author == "Ana Berisha" and page.published == "2026-03-14" and page.site == "Physics Notes"
    md = page.markdown
    assert md.startswith("# How magnets work") and "## Domains" in md and "- Iron, nickel and cobalt" in md
    assert "[magnetic domains](https://notes.example/domains)" in md

    # Relative to the page itself, and without heading anchors
    docs = extract.from_html('<html><body><article><h1>Tasks<a href="#tasks">¶</a></h1><p>' + "Words about tasks. " * 20 +
                             '<a href="glossary.html#coroutine">coroutine</a> and <a href="#gather">gather</a>.</p></article></body></html>',
                             "https://docs.example/3/library/tasks.html")
    assert docs.markdown.startswith("# Tasks\n") and "¶" not in docs.markdown
    assert "[coroutine](https://docs.example/3/library/glossary.html#coroutine)" in docs.markdown
    assert "[gather](https://docs.example/3/library/tasks.html#gather)" in docs.markdown
    assert "| MRI | 3 |" in md
    # The navigation and footer aren't the article
    assert "Contact" not in md and "Privacy" not in md

    full = extract.from_html(ARTICLE, "https://notes.example/physics/magnets", full=True)
    assert full.kind == "full" and "[Contact](https://notes.example/contact)" in full.markdown

    # A page with too little article falls back to the whole page
    short = extract.from_html("<html><body><a href='/a'>A</a> <a href='/b'>B</a></body></html>", "https://x.example/")
    assert short.kind == "full" and "[A](https://x.example/a)" in short.markdown


def test_rss_and_atom_feeds_become_markdown_entry_by_entry():
    feed = extract.from_bytes(RSS.encode(), "application/rss+xml", "https://physics.example/feed.xml")
    assert feed.kind == "feed" and feed.extra == {"entries": 2}
    assert feed.title == "Physics Notes" and feed.description == "Notes on magnets and more"
    assert "## [How magnets work](https://physics.example/article)" in feed.markdown
    assert "[magnetic domains](https://physics.example/domains)" in feed.markdown
    assert "Ana Berisha" in feed.markdown and "Sat, 14 Mar 2026" in feed.markdown
    assert "## [Superconductors](https://physics.example/cold)\n\nMagnets without resistance." in feed.markdown
    # Atom, served as plain XML
    atom = extract.from_bytes(ATOM.encode(), "application/xml", "https://lab.example/atom")
    assert atom.kind == "feed" and atom.title == "Lab log" and atom.published == "2026-04-01T10:00:00Z"
    assert "## [Measured the fridge magnet](https://lab.example/fridge)" in atom.markdown and "It was 5 mT." in atom.markdown
    # XML that isn't a feed comes as it is
    other = extract.from_bytes(b'<?xml version="1.0"?><urlset><url>a</url></urlset>', "text/xml", "https://x.example/s.xml")
    assert other.kind == "text" and other.markdown.startswith("```xml\n<?xml")


def test_text_is_read_in_the_encoding_the_site_gave():
    page = extract.from_bytes(LATIN, "text/html", "https://x.example/")
    assert page.title == "Café" and "Ein schöner Café-Besuch – naïve Grüße." in page.markdown
    assert extract.decode("Grüße".encode("cp1252"), "text/plain; charset=windows-1252") == "Grüße"
    # The header wins over the page; an unknown name, or none, means UTF-8, else a guess
    assert extract.decode("<meta charset='latin-1'>é".encode(), "text/html; charset=utf-8").endswith("é")
    assert extract.decode("é".encode(), "text/plain; charset=nonsense") == "é"
    assert extract.decode(b"\xef\xbb\xbfhello") == "hello"
    # Undeclared and not UTF-8: guessed, so text rather than "�" (a close encoding may be picked)
    guessed = extract.decode(("A naïve café, déjà vu. " * 20).encode("cp1252"))
    assert "café" in guessed and "\ufffd" not in guessed


def test_pdfs_text_and_json_are_read_and_other_files_refused():
    document = extract.from_bytes(pdf("Page one about magnets", "Page two about iron"), "application/pdf", "https://x.example/a.pdf")
    assert document.kind == "pdf" and document.extra == {"pages": 2}
    assert "## Page 1\n\nPage one about magnets" in document.markdown and "## Page 2" in document.markdown
    assert extract.from_bytes(b"hello\nworld", "text/plain; charset=utf-8", "https://x.example/a.txt").markdown == "hello\nworld"
    assert extract.from_bytes(b'{"a": 1}', "application/json", "https://x.example/").markdown == '```json\n{\n  "a": 1\n}\n```'
    with pytest.raises(extract.Unsupported):
        extract.from_bytes(b"\x89PNG", "image/png", "https://x.example/cat.png")


def test_reading_a_page_in_chunks_from_the_store(site):
    first = client.get("/read", params={"url": f"{site}/old", "max_chars": 1000}).json()
    assert first["final_url"] == f"{site}/article" and first["title"] == "How magnets work"
    assert first["start"] == 0 and first["next_start"] == first["end"] and first["end"] <= 1000
    assert first["markdown"].startswith("# How magnets work") and not first["cached"] and not first["rendered"]

    following = client.get("/read", params={"url": f"{site}/old", "max_chars": 1000, "start": first["next_start"]}).json()
    assert following["cached"] and following["start"] == first["end"]
    assert Site.hits["/article"] == 1

    whole = client.get("/read", params={"url": f"{site}/old", "format": "markdown", "max_chars": 1000})
    assert whole.headers["content-type"].startswith("text/markdown")
    text = whole.text
    assert text.startswith(f"Source: {site}/article\nBy: Ana Berisha") and "\n\n# How magnets work\n" in text
    assert f"read on with start={first['next_start']}" in text

    again = client.get("/read", params={"url": f"{site}/old", "fresh": True}).json()
    assert not again["cached"] and Site.hits["/article"] == 2


def test_other_kinds_of_files_and_errors(site, monkeypatch):
    assert client.get("/read", params={"url": f"{site}/notes.txt"}).json()["markdown"] == "Plain text notes.\nSecond line."
    read_pdf = client.get("/read", params={"url": f"{site}/pdf"}).json()
    assert read_pdf["kind"] == "pdf" and read_pdf["pages"] == 2
    rss = client.get("/read", params={"url": f"{site}/feed.xml"}).json()
    assert rss["kind"] == "feed" and rss["entries"] == 2 and not rss["rendered"]
    assert f"## [How magnets work]({site}/article)" in rss["markdown"]
    atom = client.get("/read", params={"url": f"{site}/atom", "format": "markdown"}).text
    assert atom.startswith("# Lab log\n") and "Measured the fridge magnet" in atom
    assert client.get("/read", params={"url": f"{site}/cat.png"}).status_code == 415
    missing = client.get("/read", params={"url": f"{site}/nope", "render": "never"})
    assert missing.status_code == 502 and "404" in missing.json()["detail"] and Site.hits["/nope"] == 1
    # A gateway that's briefly down is tried once more
    assert client.get("/read", params={"url": f"{site}/flaky", "render": "never"}).json()["markdown"].startswith("Plain text")
    down = client.get("/read", params={"url": f"{site}/down", "render": "never"})
    assert down.status_code == 502 and "503" in down.json()["detail"] and Site.hits["/down"] == 2
    assert client.get("/read", params={"url": "ftp://x"}).status_code == 400

    robots = client.get("/read", params={"url": f"{site}/private/secret"})
    assert robots.status_code == 403 and "robots.txt" in robots.json()["detail"]
    assert client.get("/read", params={"url": f"{site}/private/secret", "ignore_robots": True}).status_code == 200

    # Private addresses, also when a public page redirects to one
    monkeypatch.setattr(urls, "ALLOW_PRIVATE", False)
    assert client.get("/read", params={"url": f"{site}/article", "fresh": True}).status_code == 403
    monkeypatch.setattr(urls, "_private", lambda address: address != "127.0.0.1")
    port = site.rsplit(":", 1)[1]
    redirected = client.get("/read", params={"url": f"{site}/to/127.0.0.2:{port}/article", "render": "never"})
    assert redirected.status_code == 403 and "127.0.0.2" in redirected.json()["detail"]


def test_a_token_can_be_required(site, monkeypatch):
    monkeypatch.setattr(service, "TOKEN", "s3cret")
    assert client.get("/read", params={"url": f"{site}/notes.txt"}).status_code == 401
    assert client.get("/read", params={"url": f"{site}/notes.txt"}, headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200


def test_reads_of_the_same_page_at_once_share_one_fetch(site):
    async def both():
        from labb_web_search_crawler import reader
        return await asyncio.gather(reader.read(f"{site}/article"), reader.read(f"{site}/article"))

    first, second = asyncio.run(both())
    assert first.page.markdown == second.page.markdown and Site.hits["/article"] == 1


@pytest.mark.skipif(not chromium_installed(), reason="Playwright's Chromium isn't installed")
def test_pages_built_with_javascript_are_read_in_the_browser(site):
    # One event loop for both requests, like the real server (the browser stays open)
    with TestClient(service.app) as one_loop:
        read = one_loop.get("/read", params={"url": f"{site}/js"}).json()
        again = one_loop.get("/read", params={"url": f"{site}/js", "render": "always", "fresh": True}).json()
    assert read["rendered"] and read["title"] == "Built in the browser" and again["rendered"]
    assert "This text only exists after the script runs" in read["markdown"]
    plain = client.get("/read", params={"url": f"{site}/js", "render": "never", "fresh": True}).json()
    assert not plain["rendered"] and "only exists after" not in plain["markdown"]


@pytest.mark.skipif(not chromium_installed(), reason="Playwright's Chromium isn't installed")
def test_the_browser_waits_for_late_content_and_reads_iframes(site):
    with TestClient(service.app) as one_loop:
        late = one_loop.get("/read", params={"url": f"{site}/late", "render": "always"}).json()
        framed = one_loop.get("/read", params={"url": f"{site}/framed", "render": "always", "full": True}).json()
    assert late["title"] == "Arrived late" and "two seconds after the page loads" in late["markdown"]
    assert "A page with an embedded article" in framed["markdown"] and "Every magnet has a north" in framed["markdown"]


def test_a_render_waits_only_so_long_for_a_free_tab(site, monkeypatch):
    monkeypatch.setattr(fetch, "TAB_WAIT", 0.1)

    async def busy():
        fetch.browser._for_this_loop()
        for _ in range(fetch.RENDERS):
            await fetch.browser._tabs.acquire()
        with pytest.raises(fetch.FetchError, match="busy"):
            await fetch.browser.render(f"{site}/js")

    asyncio.run(busy())


def test_search_reads_the_top_results(site, monkeypatch):
    monkeypatch.setattr(search, "_search", lambda query, k, region: [
        {"title": "Magnets", "url": f"{site}/article", "snippet": "How magnets work"},
        {"title": "Gone", "url": f"{site}/nope", "snippet": "A dead link"},
        {"title": "Notes", "url": f"{site}/notes.txt", "snippet": "Plain notes"},
    ][:k])
    results = client.get("/search", params={"q": "magnets", "k": 3, "read": 2, "max_chars": 600}).json()
    assert [r["title"] for r in results] == ["Magnets", "Gone", "Notes"]
    assert results[0]["page"]["markdown"].startswith("# How magnets work") and len(results[0]["page"]["markdown"]) <= 600
    assert "error" in results[1]["page"] and "page" not in results[2]
    assert client.get("/search", params={"q": ""}).status_code == 422


def test_reading_works_without_the_store(site, monkeypatch):
    """The store is only a cache: a database that can't be reached means fetching every time"""

    monkeypatch.setattr(store, "DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none?connect_timeout=1")
    monkeypatch.setattr(store, "_conn", None)
    for _ in range(2):
        page = client.get("/read", params={"url": f"{site}/article"}).json()
        assert page["title"] == "How magnets work" and not page["cached"]
    assert Site.hits["/article"] == 2


def test_a_page_is_fetched_from_the_address_that_was_checked(monkeypatch):
    """A name can't answer a public address for the check and a private one for the fetch"""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(urls, "ALLOW_PRIVATE", False)
    monkeypatch.setattr(urls, "resolve", lambda host: ("93.184.215.14",))
    assert urls.pin("https://example.com:8443/a") == (
        "https://93.184.215.14:8443/a", {"Host": "example.com:8443"}, {"sni_hostname": "example.com"})
    monkeypatch.setattr(urls, "resolve", lambda host: ("10.0.0.5",))
    with pytest.raises(urls.Blocked):
        urls.pin("https://example.com/a")


def test_labb_changes_its_settings_and_they_hold_at_once(monkeypatch, tmp_path):
    from labb_web_search_crawler import settings
    monkeypatch.setattr(service, "TOKEN", "t")
    monkeypatch.setenv("CRAWLER_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setattr(settings, "_defaults", {"allow_private": False, "cache_hours": 24.0, "fetch_timeout": 20.0,
                                                "render_timeout": 30.0, "settle_seconds": 12.0, "max_page_mb": 15})
    monkeypatch.setattr(urls, "ALLOW_PRIVATE", False)
    monkeypatch.setattr(store, "CACHE_HOURS", 24.0)
    headers = {"Authorization": "Bearer t"}
    assert client.get("/settings").status_code == 401
    shown = {s["key"]: s for s in client.get("/settings", headers=headers).json()}
    assert shown["allow_private"]["value"] is False and shown["allow_private"]["kind"] == "bool"

    changed = {s["key"]: s["value"] for s in client.put("/settings", json={"allow_private": True, "cache_hours": 2.5},
                                                         headers=headers).json()}
    assert changed["allow_private"] is True and urls.ALLOW_PRIVATE is True and store.CACHE_HOURS == 2.5
    urls.check("http://127.0.0.1/")
    assert '"allow_private": true' in (tmp_path / "settings.json").read_text()
    # Refused whole
    assert client.put("/settings", json={"cache_hours": 1, "max_page_mb": 0}, headers=headers).status_code == 400
    assert client.put("/settings", json={"allow_private": 1}, headers=headers).status_code in (400, 422)
    assert store.CACHE_HOURS == 2.5
    client.put("/settings", json={"allow_private": None, "cache_hours": None}, headers=headers)
    assert urls.ALLOW_PRIVATE is False and store.CACHE_HOURS == 24.0
    with pytest.raises(urls.Blocked):
        urls.check("http://127.0.0.1/")
