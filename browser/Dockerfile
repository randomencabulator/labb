# labb-browser: a Chromium a model can use, each person's sign-ins in a context of their own

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PORT=3100 \
    BROWSER_DATA=/data

WORKDIR /app

# Dependencies first, cached until the lockfile changes; then Chromium and the system libraries
# and fonts it needs (the whole browser: web apps and sign-in pages can tell a cut-down one)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
 && /app/.venv/bin/playwright install --with-deps --no-shell chromium \
 && rm -rf /var/lib/apt/lists/*

COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 1000 browser && mkdir -p /data /run/labb-browser && chown browser:browser /data /run/labb-browser
USER browser
VOLUME ["/data"]
EXPOSE 3100

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"]

CMD ["/app/.venv/bin/python", "-m", "labb_browser"]
