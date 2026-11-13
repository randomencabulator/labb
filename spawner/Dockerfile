# labb-spawner: starts people's apps as containers of their own (see compose.yaml for its side)

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PORT=4100 \
    SPAWNER_DATA=/data

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src ./src
# What new apps start from (sources.py)
COPY starters ./starters
# Apps' APIs: their image is built from this (app.py's backend_image)
COPY backend ./backend
# Every app's front, which serves its page: its image is built from this (app.py's front_image)
COPY front ./front
# Artifacts' template and their builder's image (artifacts.py, app.py's artifact_image)
COPY artifacts ./artifacts
RUN uv sync --frozen --no-dev

EXPOSE 4100
# Root inside the container: the Docker socket it's given belongs to root (or the host's docker group)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4100/health', timeout=4)"]

CMD ["/app/.venv/bin/python", "-m", "labb_spawner"]
