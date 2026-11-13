# labb-sites: takes websites' uploads (see compose.yaml; front/ is the Caddy that serves them)

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PORT=4200 \
    SITES_DIR=/srv/sites

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev

# A user of its own; the folders are made here so new volumes mounted on them are its
RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin sites \
 && mkdir -p /srv/sites /run/labb-sites && chown sites:sites /srv/sites /run/labb-sites
USER sites

EXPOSE 4200
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4200/health', timeout=4)"]

CMD ["/app/.venv/bin/python", "-m", "labb_sites"]
