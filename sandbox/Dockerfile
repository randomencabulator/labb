# labb-sandbox: runs workflows' Python steps, isolated (see compose.yaml for the container's side)

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PORT=4000

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev

EXPOSE 4000
# Root inside the container only to give each run a user of its own; compose drops every other
# capability, makes the system read-only and keeps the container off the internet
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/health', timeout=4)"]

CMD ["/app/.venv/bin/python", "-m", "labb_sandbox"]
