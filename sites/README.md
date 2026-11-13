# labb-sites

Static websites next to [labb](../README.md): upload a site's files as one archive, and it's at
`http://<labb's host>:8300/artifacts/<name>/`, all of it at once.

```bash
TOKEN=$(docker compose exec sites-deploy cat /run/labb-sites/token)
cd my-site/dist && zip -r ../site.zip . && cd -
curl -T my-site/site.zip -H "Authorization: Bearer $TOKEN" http://labb.lan:8300/_api/artifacts/blog
# → {"name": "blog", "files": 12, "bytes": 48213, "updated": "…", "index": true, "url": "http://labb.lan:8300/artifacts/blog/"}
```

It's two containers sharing the `sites-data` volume:

- **`sites`** (`front/`): Caddy, serving every site from the volume, read-only, and passing `/_api/` to
  the one below. Only this one is published.
- **`sites-deploy`** (this project): FastAPI, taking uploads behind a token, on an internal network
  only the front reaches.

## The API

Every route needs `Authorization: Bearer <token>`; through the front, they're under `/_api`
(`/_api/artifacts/blog`).

| | |
| --- | --- |
| `PUT /artifacts/<name>` | The body is a zip or tar (`.tar`, `.tar.gz`, `.tar.bz2`, `.tar.xz`) of the site. It replaces the site in one step: visitors get the old one or the new one, never a mix |
| `GET /artifacts` | Every site (`artifacts`): `name`, `files`, `bytes`, `updated`, `index` (whether it has an `index.html`), `url` |
| `GET /artifacts/<name>` | One of them |
| `DELETE /artifacts/<name>` | Removes it |
| `GET /health` | No token needed |

A name is lowercase letters, digits and hyphens, up to 63, like a DNS name. An archive holding a
single folder and no `index.html` (zipping the `dist` folder itself) is served from inside that
folder. `__MACOSX/`, `._*` files, `.DS_Store` and `Thumbs.db` are left out. Links, devices and paths
leading out of the archive are refused, and nothing of a refused upload is kept.

## Serving

`/artifacts/<name>/` serves `index.html`, and so does any folder in it; `/artifacts/<name>` sends you
to `/artifacts/<name>/`. Nothing outside `/artifacts/` (and `/_api/`) is served. Files
are compressed (zstd, gzip) and sent with `Cache-Control: no-cache` and an ETag, so a new upload shows
on the next load and an unchanged file costs a `304`.

Since every site is a path on the same address:

- **links must be relative** (`css/site.css`, not `/css/site.css`). For Vite, build with
  `base: "./"` (or `base: "/artifacts/<name>/"`); for Next.js's static export,
  `basePath: "/artifacts/<name>"`.
- **sites share an origin:** one site's scripts can read another's `localStorage` and cookies. Put
  only sites you trust here.
- **everything uploaded is public**, `.git` or `.env` folders included if the archive has them.

## On disk

Each site is a link, `<name>`, to the folder of its latest upload, `.versions/<name>/<stamp>`. An
upload is unpacked in `.staging/` first (on the same volume, so moving it in is a rename) and the link
is replaced in one step; the upload before stays until the next one, so a page that was loading just
then still finds its files. On start, `tidy()` clears what an upload cut off left behind.

## Settings

| Setting | Default | |
| --- | --- | --- |
| `SITES_TOKEN` | | The token uploads need |
| `SITES_TOKEN_FILE` | | Without `SITES_TOKEN`: the token kept in this file, made on the first start (mode 600). `compose.yaml` keeps it on the `sites-token` volume, never on the one that's served |
| `SITES_MAX_MB` | `100` | Largest archive, and largest site unpacked |
| `SITES_MAX_FILES` | `10000` | Files per site |

`SITES_MAX_MB` and `SITES_MAX_FILES` are defaults: labb's Settings → Limits changes them through `GET|PUT /limits`,
kept in `limits.json` beside the token file (in memory without one).
| `SITES_PUBLIC_URL` | | Where the sites are, for the answers' `url`; else the address the request came to |
| `SITES_DIR` | `/srv/sites` | |
| `PORT` | `4200` | |

In `compose.yaml`, `SITES_PORT` (default `8300`) is where the front is published and `SITES_BIND`
which address it's on. With the `https` profile, labb's Caddy serves them over https on
`SITES_HTTPS_PORT` (default `8301`), and `./setup.sh https` sets `SITES_BIND=127.0.0.1` so plain http
stays on the machine.

Run from a checkout: `uv run labb-sites` (on :4200, `SITES_DIR` to choose the folder). Tests: `uv run pytest`.
