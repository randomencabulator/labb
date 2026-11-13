"""Turning what was fetched into Markdown for a model to read: the main content of an HTML
page (trafilatura, which is made for this), or the whole page; RSS and Atom feeds entry by
entry; PDFs page by page; text as it is. Links are absolute, so the model can follow them."""

import io
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

import charset_normalizer
import feedparser
import trafilatura
from markdownify import markdownify
from pypdf import PdfReader

# Below this, the main-content extraction probably missed the page (a list, a landing page)
MIN_ARTICLE_CHARS = 200
TEXT_TYPES = ("text/plain", "text/markdown", "text/x-markdown", "text/csv")
FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/rdf+xml")
XML_TYPES = ("application/xml", "text/xml")
# How a feed served as plain XML starts
FEED_ROOT = re.compile(rb"<(rss|feed|rdf:RDF)\b")


class Unsupported(ValueError):
    """A kind of file the crawler can't turn into text"""


@dataclass
class Page:
    title: str = ""
    markdown: str = ""
    description: str = ""
    author: str = ""
    published: str = ""
    site: str = ""
    # "article": the main content; "full": the whole page; "feed", "pdf", "text"
    kind: str = "article"
    extra: dict = field(default_factory=dict)


def _tidy(markdown: str) -> str:
    markdown = re.sub(r"[ \t]+\n", "\n", markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip()


# Heading anchors ("¶", "#", "§"): noise for a reader
ANCHOR_LINK = re.compile(r"\[(?:¶|#|§|🔗|link)\]\([^)\s]*\)")
LINK = re.compile(r"(!?\[[^\]]*\]\()([^)\s]+)")


def _links(markdown: str, url: str) -> str:
    """Links and images made absolute, against the page's own URL (not just its site)"""
    markdown = ANCHOR_LINK.sub("", markdown)
    return LINK.sub(lambda m: m.group(1) + (m.group(2) if m.group(2).startswith(("mailto:", "data:")) else urljoin(url, m.group(2))),
                    markdown)


def _full(html: str) -> str:
    """The whole page, without scripts, styles and forms"""
    cleaned = re.sub(r"(?is)<(script|style|noscript|template|svg|form|iframe)\b.*?</\1>", "", html)
    return markdownify(cleaned, heading_style="ATX", bullets="-", strip=["img"])


def from_html(html: str, url: str, full: bool = False, images: bool = False) -> Page:
    """An HTML page as Markdown: its main content, or with `full` the whole page (also when
    the main content can't be found)"""

    meta = trafilatura.extract_metadata(html, default_url=url)
    page = Page(
        title=(meta.title if meta and meta.title else "") or "",
        description=(meta.description if meta else "") or "",
        author=(meta.author if meta else "") or "",
        published=(meta.date if meta else "") or "",
        site=(meta.sitename if meta else "") or "",
    )
    article = None
    if not full:
        # Without the URL, so links stay as written and _links resolves them correctly
        article = trafilatura.extract(
            html, output_format="markdown", include_links=True, include_images=images,
            include_tables=True, include_formatting=True, include_comments=False, favor_recall=True,
        )
    if article and len(article) >= MIN_ARTICLE_CHARS:
        page.markdown = _tidy(_links(article, url))
    else:
        page.markdown = _tidy(_links(_full(html), url))
        page.kind = "full"
    if not page.title:
        found = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        page.title = " ".join(found.group(1).split()) if found else ""
    return page


def from_feed(data: bytes, content_type: str, url: str) -> Page | None:
    """An RSS or Atom feed as Markdown: the feed's details, then each entry with its link,
    date, author and summary. None if it isn't a feed after all."""

    # Bytes, never a URL: feedparser would fetch that itself
    parsed = feedparser.parse(data, response_headers={"content-type": content_type, "content-location": url})
    feed = parsed.feed
    if not parsed.entries and not feed.get("title"):
        return None
    lines = [feed.get("subtitle", "")] if feed.get("subtitle") else []
    if feed.get("link"):
        lines.append(f"Website: {feed.link}")
    for entry in parsed.entries:
        title = " ".join((entry.get("title") or "Untitled").split())
        lines.append(f"## [{title}]({entry.link})" if entry.get("link") else f"## {title}")
        details = [entry.get("published") or entry.get("updated") or "", entry.get("author", "")]
        if any(details):
            lines.append(" · ".join(d for d in details if d))
        body = entry.get("summary") or (entry.content[0].value if entry.get("content") else "")
        if body:
            lines.append(markdownify(body, heading_style="ATX", bullets="-", strip=["img"]))
    return Page(
        title=feed.get("title", ""),
        markdown=_tidy(_links("\n\n".join(lines), url)),
        description=feed.get("subtitle", ""),
        author=feed.get("author", ""),
        published=feed.get("updated") or feed.get("published") or "",
        site=feed.get("title", ""),
        kind="feed",
        extra={"entries": len(parsed.entries)},
    )


def from_pdf(data: bytes) -> Page:
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as e:
        raise Unsupported(f"The PDF can't be read ({e})")
    title = ""
    try:
        title = (reader.metadata.title or "") if reader.metadata else ""
    except Exception:
        pass
    body = "\n\n".join(f"## Page {i}\n\n{text}" for i, text in enumerate(pages, start=1) if text)
    if not body:
        raise Unsupported("The PDF has no text (it may be scanned images)")
    return Page(title=title, markdown=_tidy(body), kind="pdf", extra={"pages": len(pages)})


# <meta charset="…">, <meta http-equiv="Content-Type" content="…; charset=…">, <?xml encoding="…"?>
CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([\w.:-]+)|<\?xml[^>]+encoding\s*=\s*["']([\w.:-]+)""", re.I)


def decode(data: bytes, content_type: str = "") -> str:
    """Text in the encoding the site gave: in the HTTP header, else a byte order mark or the
    page's own <meta charset>; else UTF-8, and if it isn't that, a guess"""

    declared = re.search(r"charset=[\"']?([\w.:-]+)", content_type, re.I)
    names = [declared.group(1)] if declared else []
    if data.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-8-sig" if data[:1] == b"\xef" else "utf-16", errors="replace")
    if not names and (meta := CHARSET.search(data[:4096])):
        names.append((meta.group(1) or meta.group(2)).decode("ascii"))
    for name in names:
        try:
            return data.decode(name, errors="replace")
        except LookupError:
            continue
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        guess = charset_normalizer.from_bytes(data[:200_000]).best()
        return data.decode(guess.encoding if guess else "cp1252", errors="replace")


def from_bytes(data: bytes, content_type: str, url: str, full: bool = False, images: bool = False) -> Page:
    """Whatever was fetched, by its content type"""

    kind = content_type.split(";")[0].strip().lower()
    text = lambda: decode(data, content_type)
    if kind == "application/pdf" or (not kind and data[:5] == b"%PDF-"):
        return from_pdf(data)
    if kind in ("text/html", "application/xhtml+xml") or (not kind and b"<html" in data[:2000].lower()):
        return from_html(text(), url, full=full, images=images)
    if kind in FEED_TYPES or kind in XML_TYPES or (not kind and data.lstrip()[:5] == b"<?xml"):
        if (kind in FEED_TYPES or FEED_ROOT.search(data[:2000])) and (feed := from_feed(data, content_type, url)):
            return feed
        return Page(markdown=f"```xml\n{text().strip()}\n```", kind="text")
    if kind in TEXT_TYPES:
        return Page(markdown=text().strip(), kind="text")
    if kind == "application/json" or kind.endswith("+json"):
        try:
            pretty = json.dumps(json.loads(text()), indent=2, ensure_ascii=False)
        except ValueError:
            pretty = text()
        return Page(markdown=f"```json\n{pretty}\n```", kind="text")
    raise Unsupported(f"Can't read {kind or 'this kind of file'} as text")
