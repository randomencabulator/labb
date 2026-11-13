# labb-spawner

Starts and stops labb's apps, and builds them. Each app is its own files, made from a starter in
`starters/`: its page in `frontend/` (React with shadcn/ui, the same for every starter and kept once in
`starters/_frontend/`), and, with `python` (FastAPI) and `node` (Express), its server in `backend/`
(`react` is a page only). `_frontend/choices.json` is what a new app is asked about its look (colours,
spacing and shadows, font); the answers fill `{{COLOR}}`, `{{STYLE}}` and `{{FONT}}` in its `index.html`.

Each app runs as two containers:
- its **front**, `labb-app-<id>` (`front/`: Caddy, one image for every app), published on a port of its
  own of `APP_PORT_FIRST`–`APP_PORT_LAST` (9100–9199), where the browser opens it
  (`http://labb.lan:9100`). It serves the app's page and passes `/api/…` to its server. On the apps'
  network (`APP_NETWORK`, labb-apps) it has the alias `labb-app-<port>`; with the https profile
  (`LABB_PROFILES`, or `APP_PUBLISH=0`) it isn't published: Caddy has those ports and passes each to
  `labb-app-<port>:8080`.
- its **server**, `labb-app-<id>-server`, built from `backend/Dockerfile` (with the app's folder as
  its context, so it can copy `db/`), on the apps' network only.

The page is built apart (`pages.py`): a change to `frontend/` builds it by itself a moment later
(`npm install`, `npm run build`, in a container of `APP_PAGE_IMAGE`), and the new build replaces the
one served at once; one that fails leaves the last good page up. Building the app (`POST
/apps/{id}/build`) builds the page first if it changed, then the server, and runs the new server.

It's the only part of labb with the Docker socket, so it's kept off the network: only the server
reaches it, with a token (made on the first start when `SPAWNER_TOKEN` isn't set, on a volume the
server reads it from).

## API

All but `/health` need `Authorization: Bearer <token>`. The server sends whose app it is as `owner`.

| | |
|---|---|
| `POST /apps {"owner", "name"}` | start a new app → the app (201) |
| `GET /apps?owner=` | someone's apps, newest first |
| `GET /apps/{id}?owner=` | one app |
| `DELETE /apps/{id}?owner=` | stop and remove it (204) |
| `POST /apps/{id}/stop`, `/start`, `/restart` `?owner=` | change its state → the app |
| `GET /apps/{id}/logs?owner=&tail=` | `{"lines"}`: the end of its output (300, at most 2000) |
| `GET /starters` | what a new app can start from, with the `choices` each asks (`POST /apps` takes `"starter"`, `react` by default, and `"choices"`, by name; the defaults for those not given) |
| `GET /apps/{id}/files?owner=` | its files: `[{"path", "size"}]` |
| `GET`, `PUT {"content"}`, `DELETE /apps/{id}/files/{path}?owner=` | one file, as text |
| `POST /apps/{id}/build?owner=` | build its files again → `{"ok", "error", "log", "app", "server", "page"}`: the page first if it changed, then the server, whose new build runs if it worked |
| `GET /apps/{id}/page?owner=&wait=` | its page's last build: `{"build", "ok", "error", "at", "log", "served", "pending", "changed", "page"}`; with `wait`, after building what's pending |
| `GET /apps/{id}/build?owner=` | the last build: `{"build", "ok", "error", "at", "log", "building"}` |
| `GET /apps/{id}/settings?owner=` | `{"variables": [{"name", "value", "secret"}], "data": {"path", "size", "limit"}}`; a secret's value is null |
| `PUT /apps/{id}/settings?owner= {"variables"}` | replace them (a secret with a null value keeps it); the app starts again with them |
| `GET /apps/{id}/data/download?owner=` | its `/data`, as a tar |
| `PUT /apps/{id}/labb?owner= {"env"}` | the server's: `LABB_URL` and `LABB_API_KEY` for an app that may use labb (null: none); kept encrypted, never given back; it restarts with them |
| `GET /apps/{id}/versions?owner=` | its versions (`versions.py`: git, one at each build), newest first, and `unsaved` files |
| `GET /apps/{id}/versions/{version}/diff`, `…/versions/unsaved/diff` | what a version changed, or what changed since the last |
| `POST /apps/{id}/versions/{version}/restore?owner=` | its files as they were then, saved as a new version |
| `GET`, `POST`, `DELETE /apps/{id}/database?owner=` | its database (`database.py`): what's in it; make it (DATABASE_URL); drop it |
| `GET /apps/{id}/database/rows?owner=&table=&schema=&offset=&limit=`, `POST …/database/query {"sql", "write"}`, `GET …/database/dump` | read it (read-only unless `write`), and dump it as SQL |
| `POST`, `DELETE /apps/{id}/api?owner=` | its API (`backend/`: PostgREST behind a gateway) at its port + `APP_API_PORT_OFFSET` (100): `/rest/…`, `/auth/…` sign-in for its own users, `/client.js` |
| `PATCH /apps/{id}/api?owner=` `{"signups"}` | whether new users may sign up on it |
| `GET /apps/{id}/users?owner=&offset=`, `PATCH`, `DELETE /apps/{id}/users/{user}?owner=` | its own users: listed, disabled (`{"disabled"}`, signed out at once) or removed |
| `POST /apps/{id}/labb-helper?owner=&language=` | `labb.py` (`python`) or `labb.js` (`node`) from the starters, into the app's `backend/` (its top folder, for an app from before `backend/`) |
| `GET /health` | `{"ok", "docker"}` |

**Artifacts** (`artifacts.py`): web pages labb's agent builds in a chat, each a folder of its own,
`SPAWNER_DATA/artifacts/<name>/`, made from `artifacts/template/` (Next.js as a static site, Base UI
components, ten typed charts, typed diagrams, and an `AGENTS.md` for the model). They're built by
`labb-artifact-builder`, an image made from `artifacts/` (the template with its `node_modules`
installed once), with the files mounted read-only, no network and no capabilities; labb publishes
the site to labb-sites (`sites/`).

| | |
|---|---|
| `POST /artifacts {"owner", "title", "thread"}` | a new one from the template → `{"name", "owner", "thread", "title", "created", "build"}` (201); the name is the title's slug and 6 random characters |
| `GET /artifacts?owner=&thread=` | someone's, newest first (one chat's with `thread`) |
| `GET`, `DELETE /artifacts/{name}?owner=` | one; remove it (204) |
| `GET /artifacts/{name}/files?owner=`; `GET`, `PUT {"content"}`, `DELETE /artifacts/{name}/files/{path}?owner=` | its files, as text (not `node_modules/`, `.next/` or `out/`) |
| `POST /artifacts/{name}/build?owner=` | `next build` → `{"ok", "error", "log", "at", "files"}`; `error` is the type errors or Turbopack's, with their file and line. One whose `package.json` asks for other packages is refused before it builds; the last good site stays when one fails |
| `GET /artifacts/{name}/site?owner=` | the last good build's site, as a .tar.gz |

An app: `{"id", "name", "owner", "port", "status", "detail", "restarts", "created", "upstream", "memory_limit", "server", "page"}`, and `memory_used` for one app (its front's and its server's). `status` is its front's, or its server's while the front runs; `server` says whether it has one, `page` which page build it serves. `detail` says why it isn't running. `port` is where people
open it; `upstream` is where the server checks it answers (`http://labb-app-<id>:8080` on the
network, `http://127.0.0.1:<port>` without).

## Settings

| | default | |
|---|---|---|
| `APP_NETWORK` | | the Docker network apps go on (made if missing); compose sets labb-apps, and from a checkout without it fronts and servers meet on one called labb-apps |
| `APP_PORT_FIRST`, `APP_PORT_LAST` | 9100, 9199 | apps' ports, one each |
| `APP_BIND` | 0.0.0.0 | the address they're published on (127.0.0.1: this machine only) |
| `APP_PUBLISH` | 1, 0 with https | whether containers are published on their ports (else Caddy has them) |
| `APP_UPSTREAM_HOST` | 127.0.0.1 | without a network: where the server checks a published app |
| `APP_PAGE_IMAGE` | node:24-alpine | what pages are built in |
| `APP_PAGE_DEBOUNCE_SECONDS` | 1.5 | how long after a change to `frontend/` its page builds |
| `APP_FRONT_MEMORY` | 64m | each front's memory |
| `APP_MEMORY`, `APP_CPUS`, `APP_PIDS` | 256m, 0.5, 128 | limits per app's server |
| `APP_MAX_PER_OWNER` | 10 | apps per person |
| `APP_MAX_TOTAL` | 0 | apps on the server in all (0: as many as there are ports) |
| `APP_IDLE_MINUTES` | 0 | stop apps whose container sent and got nothing this long (0: never) |
| `SPAWNER_DATA` | /data | apps' files (`apps/<id>/src`), data (`apps/<id>/data`), built pages (`apps/<id>/pages`, built in `apps/<id>/page-work`), settings, last builds, and `secret.key` |
| `APP_DATA_MB` | 1024 | how much an app may keep in `/data`; one past it is stopped |
| `APP_BUILD_SECONDS`, `APP_BUILD_MEMORY_MB`, `APP_BUILDS_AT_ONCE` | 600, 1024, 2 | limits on builds |
| `ARTIFACT_MAX` | 50 | artifacts per person |
| `ARTIFACT_BUILD_SECONDS`, `ARTIFACT_BUILD_MEMORY_MB` | 180, 2048 | limits on an artifact's build |
| `SPAWNER_TOKEN` / `SPAWNER_TOKEN_FILE` | | the token, or where it's made |

The limits among these (apps per person and in all, memory, processor and processes per app, data, database and its
connections, variables, files and source size, idle minutes, builds, artifacts) are only defaults: labb's Settings →
Limits shows and changes them through `GET|PUT /limits` (`limits.py`), kept in `SPAWNER_DATA/limits.json` and read
when they're used.

`GET /system` (`system.py`, read-only) is what Settings → Health shows: each container of the compose project the
spawner runs in, and people's apps added up per app, with CPU (two quick samples half a second apart) and memory.

## Run from the checkout

```bash
uv run labb-spawner          # :4100, uses the Docker of DOCKER_HOST or the local socket
uv run pytest
```
