# The Labb web chat: builds the React app with Node, then runs the server (labb.server.app) with uv,
# with Node and uv's uvx there too for MCP servers.
# It also builds the labb wheel, which the server hands to other PCs.

FROM node:22-bookworm-slim AS web
WORKDIR /frontend
# The npm workspace in frontend/ (web and the design system): its manifests first, so this layer is
# cached until one changes. -w web installs the web app's dependencies, the design system's packages
# it uses, and theirs; not the docs site's
COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/web/package.json web/
COPY frontend/design-system/packages/tokens/package.json design-system/packages/tokens/
COPY frontend/design-system/packages/ui/package.json design-system/packages/ui/
COPY frontend/design-system/docs/package.json design-system/docs/
RUN npm ci -w web
COPY frontend/design-system/packages design-system/packages
COPY frontend/web/ web/
RUN npm run build -w web


FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    CHAT_DB=/app/data/chats.sqlite

WORKDIR /app

# Dependencies first, cached until the lockfile changes. uv's downloads stay in a build cache, out of the image
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
# The browser's code goes in the wheel for the PCs; this image never runs it
COPY browser/src ./browser/src
COPY --from=web /frontend/web/dist ./frontend/web/dist
# labb itself, editable: the server finds pyproject.toml and frontend/web/dist above src/labb
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# The labb wheel for the PCs, in /app/wheels
RUN --mount=type=cache,target=/root/.cache/uv uv build --wheel --out-dir /app/wheels && rm -rf build

# Node.js (node, npm, npx) from the web stage, the same Debian: MCP servers that are npm packages
# run with npx (Settings → MCP servers), as the ones from PyPI run with uvx
COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY --from=web /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# git: labb's git context and snapshots if it's run in the container
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 labb \
 && mkdir -p /app/data /home/labb/.cache && chown labb:labb /app/data /home/labb/.cache
# What npx and uvx download for MCP servers, kept in one place (compose keeps it in a volume)
ENV NPM_CONFIG_CACHE=/home/labb/.cache/npm \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false
USER labb
VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"]

CMD ["/app/.venv/bin/python", "-m", "labb.server.app"]
