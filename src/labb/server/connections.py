"""Integrations: people connect their own accounts (Google, Microsoft 365, GitHub, Notion), so
the assistant can use their calendar, mail, issues and pages (the toolsets that name the app in
`CONNECTION`: tools/google_calendar.py, gmail.py, outlook_calendar.py, outlook_mail.py, github.py,
notion.py; the core's side is connections.py). The routes and tables still say "apps".

- Admins set an integration up in Settings → Integrations (the server's) with an OAuth client from
  its developer console, or with <APP>_CLIENT_ID and <APP>_CLIENT_SECRET in .env; what's saved
  wins. The secret is encrypted with Studio's key and never sent back.
- Connecting is OAuth's authorization code flow, with PKCE where the app takes it. Google and
  Microsoft send people back only to localhost or an https address on a public domain (GitHub to
  any address). When the server's address (LABB_PUBLIC_URL, else the one the browser used) is one,
  it comes back to /connections/<app>/callback. Otherwise it goes to http://localhost:<port>/…,
  which doesn't load, and the person pastes that address into Settings (`finish_pasted`).
- Tokens are encrypted too, refreshed shortly before they run out (those that do: GitHub's and
  Notion's don't), and revoked when someone disconnects where the app can. A refresh the app turns
  down marks the connection `expired`: connect again.
"""

import base64
import hashlib
import ipaddress
import json
import os
import secrets as random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from labb.chats import conn, lock
from labb.connections import AppError
from labb.server.studio import secrets
from labb.server.studio.store import now


@dataclass(frozen=True)
class App:
    name: str
    label: str
    auth_url: str
    token_url: str
    # "" when the app can't be asked to revoke a token
    revoke_url: str
    scopes: tuple[str, ...]
    # What the connection lets the assistant do, for Settings
    uses: tuple[str, ...]
    # Where admins make an OAuth client, and how, for Settings (the redirect URI comes after these)
    console: str
    steps: tuple[str, ...] = ()
    id_hint: str = ""
    secret_hint: str = ""
    params: tuple[tuple[str, str], ...] = ()
    # How the token endpoint takes the client: in the form ("form"), or as Basic auth with a JSON body (Notion)
    token_auth: str = "form"
    pkce: bool = True
    # Whether it sends people back to any address (GitHub), not only localhost and public https
    any_redirect: bool = False
    # Whether a refresh asks for the scopes again (Microsoft)
    refresh_scopes: bool = False
    # The account's name: from the ID token (Google, Microsoft), else this API address's `field` with the token
    account_url: str = ""
    account_field: str = ""
    # How the API is told about the client's revoke: "form" (token=…, Google), "github"
    revoke_style: str = "form"


MICROSOFT_TENANT = os.getenv("MICROSOFT_TENANT", "common").strip() or "common"

APPS = {
    "google": App(
        "google", "Google",
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        revoke_url="https://oauth2.googleapis.com/revoke",
        scopes=(
            "openid", "email",
            "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
        ),
        uses=("See and change your calendar's events", "Search and read your mail, and write drafts (never send)"),
        console="https://console.cloud.google.com/apis/credentials",
        steps=(
            "In Google Cloud, make a project and turn on the Google Calendar API and the Gmail API.",
            "On the OAuth consent screen, choose External and add the people who'll connect as test users, or publish it. "
            "While it's in testing, Google signs people out every 7 days.",
            "Make an OAuth client ID of type Web application, with this authorized redirect URI:",
        ),
        id_hint="….apps.googleusercontent.com", secret_hint="GOCSPX-…",
        # A refresh token every time, so the connection keeps working
        params=(("access_type", "offline"), ("prompt", "consent"), ("include_granted_scopes", "true")),
    ),
    "microsoft": App(
        "microsoft", "Microsoft 365",
        auth_url=f"https://login.microsoftonline.com/{MICROSOFT_TENANT}/oauth2/v2.0/authorize",
        token_url=f"https://login.microsoftonline.com/{MICROSOFT_TENANT}/oauth2/v2.0/token",
        revoke_url="",
        scopes=("openid", "email", "profile", "offline_access", "User.Read", "MailboxSettings.Read",
                "Mail.ReadWrite", "Calendars.ReadWrite"),
        uses=("See and change your Outlook calendar's events",
              "Search and read your Outlook mail, and write drafts (never send)"),
        console="https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
        steps=(
            "In Microsoft Entra, under App registrations, make a New registration. For who can use it, pick accounts in any "
            "organizational directory and personal Microsoft accounts (or only your organization's, with MICROSOFT_TENANT "
            "set to its tenant id).",
            "Under Authentication, add the Web platform with this redirect URI:",
        ),
        id_hint="The Application (client) ID", secret_hint="A client secret's Value (Certificates & secrets)",
        params=(("prompt", "select_account"),),
        refresh_scopes=True,
    ),
    "github": App(
        "github", "GitHub",
        auth_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        revoke_url="https://api.github.com/applications/{client_id}/grant",
        scopes=("repo", "read:user"),
        uses=("Search and read issues, pull requests and files in your repositories",
              "Open issues and comment on issues and pull requests, when you ask"),
        console="https://github.com/settings/applications/new",
        steps=("On GitHub, under Settings → Developer settings → OAuth Apps, make a New OAuth App "
               "(for an organization: its settings). Its homepage is this server's address.",
               "Give it this authorization callback URL:"),
        id_hint="Ov23li…", secret_hint="A client secret (Generate a new client secret)",
        any_redirect=True, account_url="https://api.github.com/user", account_field="login", revoke_style="github",
    ),
    "notion": App(
        "notion", "Notion",
        auth_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",
        revoke_url="",
        scopes=(),
        uses=("Search and read the pages you share with labb", "Write new pages and add to pages, when you ask"),
        console="https://www.notion.so/profile/integrations",
        steps=("In Notion's integrations, make a New integration of type Public, with read, update and insert content.",
               "Give it this redirect URI:",
               "Each person picks the pages labb may use when they connect."),
        id_hint="The OAuth client ID", secret_hint="The OAuth client secret",
        params=(("owner", "user"),),
        token_auth="basic_json", pkce=False,
    ),
}

PUBLIC_URL = os.getenv("LABB_PUBLIC_URL", "").strip().rstrip("/")
# How long someone has to finish connecting
PENDING_SECONDS = 15 * 60
# Refresh a token this long before it runs out
EARLY = timedelta(seconds=60)
# The expiry of a token that doesn't run out
NEVER = "9999-12-31T00:00:00+00:00"
MAX_FIELD = 500
# Hosts Google doesn't take for a redirect, though they look like domains
LOCAL_SUFFIXES = (".local", ".lan", ".home", ".internal", ".localdomain", ".home.arpa")

# For the apps' sign-in endpoints; tests replace it
http = httpx.Client(timeout=httpx.Timeout(30, connect=10))

with lock, conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS app_clients ("
        "app TEXT PRIMARY KEY, client_id TEXT NOT NULL, client_secret TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS app_connections ("
        "user_id TEXT NOT NULL, app TEXT NOT NULL, account TEXT NOT NULL, scopes TEXT NOT NULL, "
        "access_token TEXT NOT NULL, refresh_token TEXT, expires_at TEXT NOT NULL, status TEXT NOT NULL, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (user_id, app))"
    )

# state -> who's connecting what: {user_id, app, verifier, redirect_uri, at}
_pending: dict[str, dict] = {}
_pending_lock = threading.Lock()
# One refresh at a time per (user, app)
_refreshing: dict[tuple[str, str], threading.Lock] = {}


class SetupError(ValueError):
    """Settings that can't be saved or a connection that can't be made, said for the person"""


def _app(name: str) -> App:
    if name not in APPS:
        raise SetupError(f"There's no integration called {name}")
    return APPS[name]


def _encrypt(value: str) -> str:
    return secrets.fernet().encrypt(value.encode()).decode()


def _decrypt(token: str | None) -> str:
    if not token:
        return ""
    try:
        return secrets.fernet().decrypt(token.encode()).decode()
    except Exception:
        return ""


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


# --- The apps' OAuth clients (admins) -----------------------------------------------------

def _env(app: str) -> tuple[str, str]:
    prefix = app.upper()
    return os.getenv(f"{prefix}_CLIENT_ID", "").strip(), os.getenv(f"{prefix}_CLIENT_SECRET", "").strip()


def _saved(app: str) -> tuple[str, str] | None:
    with lock:
        row = conn.execute("SELECT client_id, client_secret FROM app_clients WHERE app = ?", (app,)).fetchone()
    return (row[0], _decrypt(row[1])) if row else None


def client(app: str) -> tuple[str, str] | None:
    """The app's client id and secret: the saved ones, else .env's"""
    found = _saved(app) or _env(app)
    return found if found[0] and found[1] else None


def configured() -> set[str]:
    """The apps with a client (without decrypting: every model call asks)"""
    with lock:
        saved = {app for (app,) in conn.execute("SELECT app FROM app_clients").fetchall()}
    return {name for name in APPS if name in saved or all(_env(name))}


def public_url(base: str) -> str:
    return PUBLIC_URL or base.rstrip("/")


def can_redirect(url: str, app: App | None = None) -> bool:
    """Whether the app may send people back to this address: localhost, or https on a public domain
    (GitHub: anywhere)"""

    if app is not None and app.any_redirect:
        return True
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    return parts.scheme == "https" and "." in host and not host.endswith(LOCAL_SUFFIXES)


def redirect_uri(app: str, base: str) -> tuple[str, str]:
    """Where the app sends people back, and how they finish: "redirect", or "paste" the address"""

    url = public_url(base)
    if can_redirect(url, APPS.get(app)):
        return f"{url}/connections/{app}/callback", "redirect"
    port = urlsplit(url).port
    return f"http://localhost{f':{port}' if port else ''}/connections/{app}/callback", "paste"


def apps_listing(base: str) -> list[dict]:
    """The integrations for Settings → Integrations (the server's): whether each is set up, and where its settings come from"""

    listing = []
    for app in APPS.values():
        saved, env = _saved(app.name), _env(app.name)
        uri, mode = redirect_uri(app.name, base)
        with lock:
            users = conn.execute("SELECT COUNT(*) FROM app_connections WHERE app = ?", (app.name,)).fetchone()[0]
        listing.append({
            "name": app.name, "label": app.label, "uses": list(app.uses), "console": app.console,
            "steps": list(app.steps), "id_hint": app.id_hint, "secret_hint": app.secret_hint,
            "configured": client(app.name) is not None,
            "client_id": (saved or env)[0] or None,
            "source": "saved" if saved else ".env" if env[0] and env[1] else None,
            "redirect_uri": uri, "mode": mode, "public_url": public_url(base), "connected_users": users,
        })
    return listing


def save_client(app: str, client_id: str, client_secret: str | None):
    """Save the app's client; an empty secret keeps the saved one"""

    _app(app)
    client_id = client_id.strip()
    secret = (client_secret or "").strip() or (_saved(app) or ("", ""))[1]
    if not client_id or not secret:
        raise SetupError("Give the client ID and the client secret")
    if len(client_id) > MAX_FIELD or len(secret) > MAX_FIELD:
        raise SetupError(f"The client ID and secret are at most {MAX_FIELD} characters")
    with lock, conn:
        conn.execute(
            "INSERT INTO app_clients (app, client_id, client_secret, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (app) DO UPDATE SET client_id = excluded.client_id, client_secret = excluded.client_secret, "
            "updated_at = excluded.updated_at",
            (app, client_id, _encrypt(secret), now()),
        )


def remove_client(app: str) -> bool:
    with lock, conn:
        return conn.execute("DELETE FROM app_clients WHERE app = ?", (app,)).rowcount > 0


# --- Connecting ------------------------------------------------------------------------------

def start(user_id: str, app: str, base: str) -> dict:
    """The address to send the person to, to allow labb in, and how they'll finish"""

    found = _app(app)
    if not (pair := client(app)):
        raise SetupError(f"{found.label} isn't set up on this server yet: an admin can do that in Settings → Integrations")
    uri, mode = redirect_uri(app, base)
    state, verifier = random.token_urlsafe(24), random.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    pkce = {"code_challenge": challenge, "code_challenge_method": "S256"} if found.pkce else {}
    with _pending_lock:
        cutoff = time.monotonic() - PENDING_SECONDS
        for old in [k for k, v in _pending.items() if v["at"] < cutoff]:
            del _pending[old]
        _pending[state] = {"user_id": user_id, "app": app, "verifier": verifier, "redirect_uri": uri, "at": time.monotonic()}
    query = {"client_id": pair[0], "redirect_uri": uri, "response_type": "code",
             **({"scope": " ".join(found.scopes)} if found.scopes else {}), "state": state, **pkce, **dict(found.params)}
    return {"url": f"{found.auth_url}?{urlencode(query)}", "mode": mode, "redirect_uri": uri}


def _account(found: App, tokens: dict) -> str:
    """Who connected: the email in the ID token the app gave us directly (so it needn't be checked),
    the app's own answer (GitHub's login), or what came with the token (Notion's)"""

    if "id_token" in tokens:
        try:
            payload = tokens["id_token"].split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            return claims.get("email") or claims.get("preferred_username") or ""
        except Exception:
            return ""
    if found.account_url:
        try:
            answer = http.get(found.account_url, headers={"Authorization": f"Bearer {tokens['access_token']}",
                                                          "Accept": "application/json"})
            return str(answer.json().get(found.account_field) or "") if answer.status_code == 200 else ""
        except (httpx.HTTPError, ValueError):
            return ""
    owner = ((tokens.get("owner") or {}).get("user") or {})
    person = owner.get("person") or {}
    return person.get("email") or owner.get("name") or tokens.get("workspace_name") or ""


def _token_request(found: App, pair: tuple[str, str], fields: dict) -> httpx.Response:
    """A call to the app's token endpoint, with the client as the app takes it"""
    if found.token_auth == "basic_json":
        return http.post(found.token_url, json=fields, auth=(pair[0], pair[1]), headers={"Accept": "application/json"})
    return http.post(found.token_url, data={**fields, "client_id": pair[0], "client_secret": pair[1]},
                     headers={"Accept": "application/json"})


def _expires(tokens: dict) -> str:
    """When the access token runs out: never, for apps whose tokens don't (GitHub's, Notion's)"""
    if not tokens.get("expires_in") and not tokens.get("refresh_token"):
        return NEVER
    return _iso(datetime.now(timezone.utc) + timedelta(seconds=int(tokens.get("expires_in") or 3600)) - EARLY)


def finish(user_id: str, app: str, code: str | None, state: str | None, error: str | None = None) -> dict:
    """Trade the code the app sent back for tokens, and keep them"""

    found = _app(app)
    with _pending_lock:
        pending = _pending.get(state or "")
        if pending and pending["user_id"] == user_id and pending["app"] == app:
            del _pending[state]
        else:
            pending = None
    if error:
        raise SetupError("You didn't allow it, so nothing was connected" if error == "access_denied" else f"{found.label} said: {error}")
    if not pending or time.monotonic() - pending["at"] > PENDING_SECONDS:
        raise SetupError("That sign-in is too old or isn't yours: connect again")
    if not code:
        raise SetupError(f"{found.label} didn't send a code back: connect again")
    pair = client(app)
    if not pair:
        raise SetupError(f"{found.label} isn't set up on this server any more")
    try:
        fields = {"grant_type": "authorization_code", "code": code, "redirect_uri": pending["redirect_uri"]}
        if found.pkce:
            fields["code_verifier"] = pending["verifier"]
        response = _token_request(found, pair, fields)
    except httpx.HTTPError as e:
        raise SetupError(f"Couldn't reach {found.label} ({e})")
    tokens = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if response.status_code != 200 or "access_token" not in tokens:
        raise SetupError(f"{found.label} didn't accept the sign-in: {tokens.get('error_description') or tokens.get('error') or response.status_code}")
    # GitHub separates its scopes with commas
    scopes = " ".join(str(tokens.get("scope") or " ".join(found.scopes)).replace(",", " ").split())
    refresh = tokens.get("refresh_token")
    with lock, conn:
        conn.execute(
            "INSERT INTO app_connections (user_id, app, account, scopes, access_token, refresh_token, expires_at, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'ok', ?, ?) "
            "ON CONFLICT (user_id, app) DO UPDATE SET account = excluded.account, scopes = excluded.scopes, "
            "access_token = excluded.access_token, "
            "refresh_token = COALESCE(excluded.refresh_token, app_connections.refresh_token), "
            "expires_at = excluded.expires_at, status = 'ok', updated_at = excluded.updated_at",
            (user_id, app, _account(found, tokens), scopes, _encrypt(tokens["access_token"]), _encrypt(refresh) if refresh else None,
             _expires(tokens), now(), now()),
        )
    return next(c for c in listing(user_id) if c["name"] == app)


def finish_pasted(user_id: str, app: str, pasted: str) -> dict:
    """finish, with the address the person was sent back to and pasted in"""

    query = parse_qs(urlsplit(pasted.strip()).query)
    first = lambda key: (query.get(key) or [None])[0]
    if not first("code") and not first("error"):
        raise SetupError("Paste the whole address from the browser's address bar, the one that starts with http://localhost")
    return finish(user_id, app, first("code"), first("state"), first("error"))


def listing(user_id: str) -> list[dict]:
    """The integrations for Settings → Integrations, and the person's connection to each"""

    with lock:
        rows = conn.execute("SELECT app, account, scopes, status, created_at FROM app_connections WHERE user_id = ?",
                            (user_id,)).fetchall()
    mine = {app: (account, scopes, status, created) for app, account, scopes, status, created in rows}
    setup = configured()
    out = []
    for app in APPS.values():
        entry = {"name": app.name, "label": app.label, "uses": list(app.uses), "available": app.name in setup, "connection": None}
        if app.name in mine:
            account, scopes, status, created = mine[app.name]
            granted = {s.lower() for s in scopes.split()}
            # Microsoft leaves the sign-in scopes out of what it says it granted
            asked = [s for s in app.scopes if s not in ("openid", "email", "profile", "offline_access")]
            entry["connection"] = {"account": account, "status": status, "connected_at": created,
                                   "missing": [s for s in asked if s.lower() not in granted]}
        out.append(entry)
    return out


def disconnect(user_id: str, app: str) -> bool:
    """Forget the connection, and ask the app to revoke its tokens"""

    found = _app(app)
    with lock, conn:
        row = conn.execute("SELECT access_token, refresh_token FROM app_connections WHERE user_id = ? AND app = ?",
                           (user_id, app)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM app_connections WHERE user_id = ? AND app = ?", (user_id, app))
    token = _decrypt(row[1]) or _decrypt(row[0])
    pair = client(app)
    try:
        if token and found.revoke_url and found.revoke_style == "form":
            http.post(found.revoke_url, data={"token": token})
        elif token and found.revoke_url and found.revoke_style == "github" and pair:
            http.request("DELETE", found.revoke_url.format(client_id=pair[0]), auth=pair, json={"access_token": _decrypt(row[0])},
                         headers={"Accept": "application/vnd.github+json"})
    except httpx.HTTPError:
        pass
    return True


# --- Tokens, for the tools --------------------------------------------------------------------

def token(user_id: str, app: str, refresh: bool = False) -> str:
    """A current access token for the person's connection; raises AppError"""

    found = APPS.get(app)
    if not found:
        raise AppError(f"There's no app called {app}")
    reconnect = f"{found.label} needs connecting again: the user can do that in Settings → Integrations"
    with _pending_lock:
        refreshing = _refreshing.setdefault((user_id, app), threading.Lock())
    with refreshing:
        with lock:
            row = conn.execute("SELECT access_token, refresh_token, expires_at, status FROM app_connections "
                               "WHERE user_id = ? AND app = ?", (user_id, app)).fetchone()
        if not row:
            raise AppError(f"The user hasn't connected {found.label}: they can in Settings → Integrations")
        access, refresh_token, expires_at, status = row
        if status != "ok":
            raise AppError(reconnect)
        if not refresh and expires_at > _iso(datetime.now(timezone.utc)) and (current := _decrypt(access)):
            return current
        pair, refresh_token = client(app), _decrypt(refresh_token)
        if not pair:
            raise AppError(f"{found.label} isn't set up on this server any more")
        if not refresh_token:
            _mark_expired(user_id, app)
            raise AppError(reconnect)
        try:
            fields = {"grant_type": "refresh_token", "refresh_token": refresh_token}
            if found.refresh_scopes:
                fields["scope"] = " ".join(found.scopes)
            response = _token_request(found, pair, fields)
        except httpx.HTTPError as e:
            raise AppError(f"couldn't reach {found.label} ({e})")
        try:
            tokens = response.json()
        except ValueError:
            tokens = {}
        if response.status_code in (400, 401) and tokens.get("error") in ("invalid_grant", "unauthorized_client", "invalid_client"):
            _mark_expired(user_id, app)
            raise AppError(reconnect)
        if response.status_code != 200 or "access_token" not in tokens:
            raise AppError(f"{found.label} didn't give a new token ({response.status_code})")
        with lock, conn:
            conn.execute(
                "UPDATE app_connections SET access_token = ?, refresh_token = COALESCE(?, refresh_token), expires_at = ?, "
                "updated_at = ? WHERE user_id = ? AND app = ?",
                (_encrypt(tokens["access_token"]), _encrypt(tokens["refresh_token"]) if tokens.get("refresh_token") else None,
                 _iso(datetime.now(timezone.utc) + timedelta(seconds=int(tokens.get("expires_in") or 3600)) - EARLY),
                 now(), user_id, app),
            )
        return tokens["access_token"]


def _mark_expired(user_id: str, app: str):
    with lock, conn:
        conn.execute("UPDATE app_connections SET status = 'expired', updated_at = ? WHERE user_id = ? AND app = ?",
                     (now(), user_id, app))


class Backend:
    """What the core's connections.py (and so the tools) use"""

    def configured(self) -> set[str]:
        return configured()

    def connected(self, user_id: str) -> set[str]:
        with lock:
            rows = conn.execute("SELECT app FROM app_connections WHERE user_id = ? AND status = 'ok'", (user_id,)).fetchall()
        return {app for (app,) in rows} & configured()

    def token(self, user_id: str, app: str, refresh: bool = False) -> str:
        return token(user_id, app, refresh)
