# labb-browser

A browser [labb](../README.md) can use: it opens pages, clicks, fills in and submits forms, and works in web apps, and people can watch it and take over. Chromium through Playwright, with an HTTP API for labb's server.

**How a model sees a page:** its text in order, with every control it can use on a line of its own under a number that stays the same while the element lives. Open shadow roots and iframes are read too.

    Page: Sign up
    Address: https://example.com/signup

    # Create your account
    [1] textbox "Email" value="" type=email (required)
    [2] textbox "Password" (sensitive: the user fills this in, with browser_handover)
    [3] combobox "Country" value="Albania" options: Albania | Kosovo
    [4] checkbox "I agree" (not checked)
    [5] button "Create account" (submits the form)

**Sign-ins:** each person has a browser context of their own, kept in a file (mode 600) on the `/data` volume between uses. A chat's tabs close after 30 idle minutes.

**What it refuses, whoever asks:** anything but http and https; private addresses, on every request a page makes (`ALLOW_PRIVATE` to allow); the model filling in password, card and one-time code fields. People fill those in themselves in the live view: what they type goes straight to the page. It isn't kept, and values of such fields are never in what the model reads. What needs the user's yes is decided by labb's tools.

In Docker (`compose.yaml`) it's on a network of its own with labb's server, so it can reach the internet but not the database or the other services; its capabilities are dropped, and it has limits on memory, CPU and processes.

## API

| | |
| --- | --- |
| `POST /act` | `{user, thread, action, args}`, answers `{text, image?}` or `{error}`. The actions: `open`, `look`, `click`, `fill`, `press`, `type`, `paste`, `hover`, `scroll`, `drag`, `tabs`, `upload`, `wait`, `files`, `inspect` |
| `GET /view/state`, `GET /view/frame` | The tabs, and what the current tab shows as a JPEG (`?user&thread`) |
| `POST /view/input` | `{user, thread, event}`: what the user did in the live view |
| `POST /view/done` | `{user}`: keep what they signed in to right away |
| `DELETE /chat`, `DELETE /profile` | Close a chat's tabs; sign a person out of everything |
| `GET /health` | |

## Settings

| Setting | Default | |
| --- | --- | --- |
| `BROWSER_TOKEN` | | Require `Authorization: Bearer <token>` |
| `BROWSER_TOKEN_FILE` | | Without `BROWSER_TOKEN`: the token kept in this file, made on the first start. `compose.yaml` shares it with the server on a volume |
| `BROWSER_DATA` | `data` | Where sign-ins are kept |
| `BROWSER_IDLE_MINUTES` | `30` | When a chat's tabs close |
| `BROWSER_PROFILES` | `4` | People with an open browser context at once; the one unused the longest makes way |
| `BROWSER_SNAPSHOT_CHARS` | `10000` | Characters of a page in one snapshot |
| `ALLOW_PRIVATE` | | Allow private addresses, for an intranet |
| `PORT` | `3100` | |

## Development

```bash
uv sync
uv run playwright install chromium
uv run pytest
```
