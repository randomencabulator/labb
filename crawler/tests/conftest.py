"""A small website on localhost for the tests, a store of their own, and private addresses
allowed (the site is on 127.0.0.1) unless a test checks the blocking."""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

# With TEST_DATABASE_URL, the store is in that Postgres (its table emptied for each test)
os.environ["CRAWLER_DATABASE_URL"] = os.getenv("TEST_DATABASE_URL", "")

from labb_web_search_crawler import fetch, reader, robots, store, urls

ARTICLE = """<!doctype html><html><head><title>How magnets work · Physics Notes</title>
<meta name="author" content="Ana Berisha"><meta name="description" content="Why magnets attract iron">
<meta property="og:site_name" content="Physics Notes"><meta property="article:published_time" content="2026-03-14">
</head><body><nav><a href="/">Home</a> <a href="/about">About</a> <a href="/contact">Contact</a></nav>
<article><h1>How magnets work</h1>
<p>Every magnet has a north and a south pole. Inside iron, tiny regions called
<a href="/domains">magnetic domains</a> line up with the field, which is why iron is pulled in.</p>
<h2>Domains</h2><p>""" + "Each domain behaves like a tiny magnet of its own, and in unmagnetized iron they point every which way. " * 12 + """</p>
<ul><li>Iron, nickel and cobalt are ferromagnetic.</li><li>Aluminium is barely affected.</li></ul>
<h2>Strength</h2><table><tr><th>Magnet</th><th>Tesla</th></tr><tr><td>Fridge</td><td>0.005</td></tr><tr><td>MRI</td><td>3</td></tr></table>
<p>""" + "Stronger magnets need rarer materials and careful cooling, which is why MRI machines cost so much. " * 30 + """</p>
</article><footer>© Physics Notes · <a href="/privacy">Privacy</a></footer></body></html>"""

JS_PAGE = """<!doctype html><html><head><title>Loading…</title></head><body><div id="app">Please enable JavaScript.</div>
<script>
document.title = "Rendered by script";
document.getElementById("app").innerHTML = "<article><h1>Built in the browser</h1>" +
  "<p>" + "This text only exists after the script runs, so a plain request sees none of it. ".repeat(12) + "</p></article>";
</script></body></html>"""

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<title>Physics Notes</title><link>https://physics.example/</link><description>Notes on magnets and more</description>
<item><title>How magnets work</title><link>/article</link><pubDate>Sat, 14 Mar 2026 09:00:00 GMT</pubDate>
<author>ana@physics.example (Ana Berisha)</author>
<description>&lt;p&gt;Why iron is pulled in: &lt;a href="/domains"&gt;magnetic domains&lt;/a&gt;.&lt;/p&gt;</description></item>
<item><title>Superconductors</title><link>https://physics.example/cold</link><description>Magnets without resistance.</description></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<title>Lab log</title><updated>2026-04-01T10:00:00Z</updated>
<entry><title>Measured the fridge magnet</title><link href="https://lab.example/fridge"/>
<updated>2026-04-01T10:00:00Z</updated><content type="html">&lt;p&gt;It was 5 mT.&lt;/p&gt;</content></entry>
</feed>"""

# Waits before loading its content, saying it's loading meanwhile, like many apps
LATE_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Late</title></head><body><div id="app">Loading…</div>
<script>
setTimeout(() => fetch("/notes.txt").then(r => r.text()).then(() => {
  document.getElementById("app").innerHTML = "<article><h1>Arrived late</h1><p>" +
    "This text appears two seconds after the page loads, with no request in between. ".repeat(12) + "</p></article>";
}), 2000);
</script></body></html>"""

FRAMED = """<!doctype html><html><head><meta charset="utf-8"><title>Framed</title></head><body>
<h1>A page with an embedded article</h1><iframe src="/article" width="800" height="600"></iframe></body></html>"""

LATIN = ("<html><head><meta charset='windows-1252'><title>Café</title></head><body><article><p>"
         + "Ein schöner Café-Besuch – naïve Grüße. " * 20 + "</p></article></body></html>").encode("cp1252")

PAGES = {
    "/article": (200, "text/html; charset=utf-8", ARTICLE.encode()),
    "/js": (200, "text/html; charset=utf-8", JS_PAGE.encode()),
    "/notes.txt": (200, "text/plain", "Plain text notes.\nSecond line.".encode()),
    "/data.json": (200, "application/json", b'{"answer": 42, "items": [1, 2]}'),
    "/feed.xml": (200, "application/rss+xml; charset=utf-8", RSS.encode()),
    "/atom": (200, "text/xml", ATOM.encode()),
    "/late": (200, "text/html; charset=utf-8", LATE_PAGE.encode()),
    "/framed": (200, "text/html; charset=utf-8", FRAMED.encode()),
    "/latin": (200, "text/html", LATIN),
    "/down": (503, "text/html", b"<html><body>Back soon</body></html>"),
    "/cat.png": (200, "image/png", b"\x89PNG\r\n\x1a\n" + b"\0" * 20),
    "/private/secret": (200, "text/html", b"<html><body><p>secret</p></body></html>"),
    "/robots.txt": (200, "text/plain", b"User-agent: *\nDisallow: /private/\n"),
}


class Site(BaseHTTPRequestHandler):
    hits: dict[str, int] = {}

    def do_GET(self):
        path = self.path.split("?")[0]
        Site.hits[path] = Site.hits.get(path, 0) + 1
        if path == "/old":
            self.send_response(301)
            self.send_header("Location", "/article")
            self.end_headers()
            return
        if path.startswith("/to/"):
            self.send_response(302)
            self.send_header("Location", "http://" + path[len("/to/"):])
            self.end_headers()
            return
        status, kind, body = PAGES.get(path, (404, "text/html", b"<html><body>Not found</body></html>"))
        if path == "/flaky":
            # Down the first time, fine after
            status, kind, body = (503, "text/html", b"") if Site.hits[path] == 1 else PAGES["/notes.txt"]
        if path == "/pdf":
            status, kind, body = 200, "application/pdf", pdf("Page one about magnets", "Page two about iron")
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def pdf(*pages: str) -> bytes:
    """A small PDF with one line of text on each page"""

    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objects)} 0 R "
                       "/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


@pytest.fixture(scope="session")
def site():
    """The site's base URL"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(autouse=True)
def fresh_state(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "pages.sqlite"))
    monkeypatch.setattr(store, "_conn", None)
    if store.postgres():
        store.db().execute("DELETE FROM crawler_pages")
    monkeypatch.setattr(urls, "ALLOW_PRIVATE", True)
    monkeypatch.setattr(fetch, "RETRY_AFTER", 0)
    robots._cache.clear()
    reader._inflight.clear()
    Site.hits.clear()
