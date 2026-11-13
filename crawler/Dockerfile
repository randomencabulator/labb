# labb-web-search-crawler: reads web pages (in headless Chromium when they need it) as Markdown for labb

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PORT=3000 \
    DB_PATH=/data/pages.sqlite

WORKDIR /app

# Dependencies first, cached until the lockfile changes; then Chromium's headless shell and
# the system libraries it needs
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
 && /app/.venv/bin/playwright install --with-deps --only-shell chromium \
 && rm -rf /var/lib/apt/lists/*

COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# /run/labb-crawler: where it makes its token on the first start (a volume labb-web reads it from)
RUN useradd --create-home --uid 1000 crawler && mkdir -p /data /run/labb-crawler && chown crawler:crawler /data /run/labb-crawler
USER crawler
VOLUME ["/data"]
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"]

CMD ["/app/.venv/bin/python", "-m", "labb_web_search_crawler"]
