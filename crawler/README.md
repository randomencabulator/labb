# labb-web-search-crawler

Web pages as Markdown for AI to read, and web search that reads its results. labb's `web_search` and `fetch_page` tools use it. It's made for models rather than people, so it writes Markdown instead of a styled HTML page.

How it reads a page:

1. **Fetch.** It makes a plain HTTP request first, which is fast and enough for most pages. If the connection drops or a gateway is briefly down (502, 503, 504), it tries once more. If that gives little text, or the site turns it away, it renders the page in headless Chromium (Playwright). It reads a rendered page once its text has stopped changing, not when the network goes quiet, so pages that load their content late, or never stop making requests, are read whole and without waiting longer than needed. It scrolls down for lazy content, and reads the text of iframes into the page. Images, media, fonts and trackers aren't loaded.
2. **Extract.** It keeps the main content with [trafilatura](https://trafilatura.readthedocs.io), which is made for this. Headings, lists, tables, code and links are kept as Markdown. If no article is found, or with `full`, it keeps the whole page.
3. **Tidy.** It makes links absolute against the page, drops heading anchors (`¶`), and adds the title, author, date and site.
4. **Store.** It keeps the result for `CACHE_HOURS`, so reading the next part of a long page, or the same page again, doesn't fetch it again: in Postgres with `CRAWLER_DATABASE_URL` (in Docker, labb's database, in a `crawler_pages` table), else in a SQLite file. It's only a cache: if the database is down, pages are fetched again.

RSS and Atom feeds come out entry by entry (title and link, date, author, summary), also when they're served as plain XML; other XML as it is. PDFs come out page by page, text files as they are, and JSON pretty-printed. Text is read in the encoding the site gives, in the HTTP header or the page's `<meta charset>`, and guessed when there's none and it isn't UTF-8.

## API

| Request | What it does |
| --- | --- |
| `GET /read?url=…` | The page as JSON: `title`, `author`, `published`, `site`, `markdown`, `total_chars`, `next_start`, `rendered`, `cached` |
| `GET /read?url=…&format=markdown` | The same as Markdown text, with a short header, and a line saying how to read on |
| `GET /search?q=…&k=5&read=2` | The top `k` DuckDuckGo results (`title`, `url`, `snippet`), each of the first `read` with `page.markdown` |
| `GET /health` | Liveness |

These options go on `/read`:

| Option | Default | |
| --- | --- | --- |
| `start`, `max_chars` | `0`, `20000` | Which part of a long page; `next_start` says where the next part begins |
| `full` | off | The whole page, not just its main content |
| `images` | off | Keep images as `![alt](url)` |
| `render` | `auto` | `always` renders in the browser; `never` only fetches plainly |
| `fresh` | off | Read it again instead of using the stored copy |
| `ignore_robots` | off | Read pages that robots.txt asks crawlers to leave alone |

Errors come back as `{"detail": "…"}`, written for the model that asked:
- `400`: a bad URL.
- `403`: robots.txt says no, or the address is private.
- `415`: a file it can't read.
- `502`: the site failed.

## Safety

A model decides what gets fetched, so the crawler won't read private, local or reserved addresses: `localhost`, `192.168.…`, `10.…`, `169.254.169.254`, and names that resolve to them. That holds for every redirect and every request the browser makes, so a public page can't send it into your network. Set `ALLOW_PRIVATE=1` for a crawler that's meant to read an intranet.

It respects robots.txt, as `labb-web-search-crawler`. Set `CRAWLER_TOKEN` to require `Authorization: Bearer <token>`. labb's server sends it when it has the same setting.

## Running it

With labb, `docker compose up -d` in the repository root runs it next to the server (`--build`, after `./setup.sh dev`, builds it from this checkout). It isn't published: the server reaches it at `http://crawler:3000`, and PCs reach it through the server's `/crawler`, with their API key.

On its own:

```bash
cd crawler
uv sync
uv run playwright install chromium     # once, for pages that need a browser
uv run labb-web-search-crawler     # on :3000
curl "localhost:3000/read?url=en.wikipedia.org/wiki/Magnet&format=markdown"
```

| Setting | Default | |
| --- | --- | --- |
| `PORT`, `HOST` | `3000`, `0.0.0.0` | Where it listens |
| `CRAWLER_DATABASE_URL` | (none; labb's Postgres in Docker) | The store in Postgres: `postgresql://user:password@host:5432/db` |
| `DB_PATH` | `data/pages.sqlite` | The store without `CRAWLER_DATABASE_URL` |
| `CACHE_HOURS` | `24` | How long a page is kept |
| `RENDERS` | `2` | Pages rendered at once (browser tabs) |
| `TAB_WAIT` | `30` | Seconds a page waits for a free tab before giving up |
| `SETTLE_SECONDS` | `12` | The longest a rendered page is given for its text to settle |
| `FETCH_TIMEOUT`, `RENDER_TIMEOUT` | `20`, `30` | Seconds |
| `MAX_BYTES` | `15000000` | The largest page or file it reads |
| `ALLOW_PRIVATE` | off | Allow private addresses |
| `CRAWLER_TOKEN` | | Require this token |

## Development

```bash
uv run pytest      # a small website on localhost; the browser test needs Chromium (skipped without it)
```
