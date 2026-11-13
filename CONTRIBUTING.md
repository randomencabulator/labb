# Contributing to labb

Thanks for helping! Bug reports, ideas and pull requests are all welcome.

## Reporting a bug

Open an issue with what you did, what you expected and what happened. Include labb's version (`labb --version`), the model (`MODEL`), and whether it was the web chat or the terminal. For the server, `docker compose logs` usually shows the error.

## Making a change

1. Set up a checkout: `uv sync` (Python 3.14 and every dependency), and `cd frontend && npm install` for the web app and the design system (one npm workspace).
2. Read [`AGENTS.md`](AGENTS.md): it explains the architecture, the conventions and the gotchas, for people and coding agents alike.
3. Make the change, with a test when it changes behavior. The tests need no model or Ollama: `uv run pytest`. For the web app, `npm run build` (in `frontend/web/`) also checks the types. The crawler, the sandbox and the browser are their own projects: `cd crawler && uv run pytest`, `cd sandbox && uv run pytest`, `cd browser && uv run pytest` (with a real Chromium: `uv run playwright install chromium`).
4. Update the docs it touches: [`docs/guide.md`](docs/guide.md) for how things work, and the [README](README.md) when it changes what labb is or how to set it up.
5. If the change touches what `labb` runs on the PCs (anything in `src/labb` outside `server/`), raise `version` in `pyproject.toml` and run `uv lock`, or `labb --update` won't pick it up.

Keep code, comments and docs in the existing plain, short style. One change per pull request makes it easier to review.

By sending a pull request, you agree that your contribution is licensed under the [MIT License](LICENSE), like the rest of the project.
