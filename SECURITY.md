# Security

## Reporting a vulnerability

Please don't open a public issue for a security problem. Report it privately through GitHub instead: on the repository's **Security** tab, choose **Report a vulnerability**. Include what you found, how to reproduce it, and the version (`labb --version`, or `pyproject.toml` for the server).

You'll get an answer within a week. Once it's fixed, the fix is released and the advisory published, with credit if you'd like it.

## What's in scope

- The server: accounts and sessions, API keys, the gateway to Ollama and the crawler, documents, memories, agents, skills, tasks, and the install script it serves.
- labb in the terminal: its permission checks for commands and files, and that a folder's own `.labb/` (hooks, MCP servers, remembered commands) needs agreeing to before it counts.
- labb-web-search-crawler: its refusal of private addresses and its robots.txt handling.
- labb-browser and the browser tools: its refusal of private addresses, keeping people's browsers and sign-ins apart, sensitive fields staying out of the model's reach, and what asks for approval. A page talking the model into something it then has approved isn't in scope: read what you approve.

## Running it safely

- labb is made for a home or office network. Put a reverse proxy with HTTPS in front of the server if it can be reached from outside.
- Create the admin account with the setup link the server prints in its log (`docker compose logs labb-web`); a fresh server refuses to set up without it, except from the machine itself.
- Turn on HTTPS if more than one device uses the server. Without it, people's apps (on ports of the same name) are published straight by Docker, and browsers send them labb's session cookie; with HTTPS, Caddy takes it out.
- Set `BACKUP_AGE_RECIPIENT` so backups are encrypted: they hold `secret.key`, which opens every stored API key and token.
- Set `POSTGRES_PASSWORD` in `.env`, and keep Ollama and Postgres off the network (the README's quick start shows how).
- Commands the terminal agent runs aren't sandboxed. Read what you approve.
