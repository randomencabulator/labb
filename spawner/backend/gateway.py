"""An app's API, in a container of its own: `/rest/…` is its tables, through PostgREST, and `/auth/…`
sign-in for the app's own users.

PostgREST runs here, on 127.0.0.1:3000, and is started again if it stops; this passes requests to it.
It logs in as the app's API role (API_DB_URI), which can only become `anon`, or `authenticated` for a
request with a token signed here: what those may do is what the app's migrations grant them, and its
row-level security policies decide which rows a signed-in user (`auth.uid()`) has.

Sign-in logs in as the app's auth role (AUTH_DB_URI), which has the `auth` schema's tables and nothing
else. Passwords are kept as scrypt hashes. A sign-in is a session: a short-lived access token (a JWT
signed with API_JWT_SECRET, which PostgREST checks) and a refresh token (random; only its hash is
kept), replaced at each refresh. A refresh token used twice, after a short grace for two tabs
refreshing at once, ends its session: someone has a copy. Ending a session (signing out, the owner
disabling or removing the user) stops its access token at once, as PostgREST asks the database
(`auth.check_request()`) before each request.

/            what's here
/health      200 once PostgREST answers
/client.js   the page's client (an ES module: sign-in, and the tables with the user's token)
/rest/…      the tables (GET, POST, PATCH, DELETE…), and /rest/ the OpenAPI description
/auth/signup POST {"email", "password"}: a new user, signed in (unless AUTH_SIGNUPS=0)
/auth/signin POST {"email", "password"}: signed in
/auth/refresh POST {"refresh_token"}: new tokens
/auth/signout POST {"refresh_token"} or with the access token: that sign-in ended
/auth/user   GET with the access token: who's signed in
/auth/password POST {"password", "new_password"} with the access token: changed, other sign-ins ended

Only the standard library and psycopg.
"""

import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", "8080"))
POSTGREST_PORT = 3000
MAX_BODY = 10 * 1024 * 1024
MAX_AUTH_BODY = 16 * 1024
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
              "transfer-encoding", "upgrade", "host", "content-length"}

JWT_SECRET = os.environ.get("API_JWT_SECRET", "").encode()
AUTH_DB_URI = os.environ.get("AUTH_DB_URI", "")
SIGNUPS = os.environ.get("AUTH_SIGNUPS", "1") == "1"
ACCESS_SECONDS = 15 * 60
SESSION_DAYS = 30
# Two tabs refreshing with the same token at once both get new ones; a token used again after this is a copy
REUSE_GRACE_SECONDS = 10
# scrypt, as OWASP suggests for 16 MB of memory (N=2^14, r=8, p=5); the parameters are kept in each hash
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 5
PASSWORD_MIN, PASSWORD_MAX = 8, 256
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CLIENT_JS = Path(__file__).with_name("client.js")

ready = threading.Event()
# Hashing takes memory (16 MB each) and time: two at once, the rest wait
hashing = threading.BoundedSemaphore(2)
# Its database role has five connections
connecting = threading.BoundedSemaphore(4)


class Refused(Exception):
    def __init__(self, status: int, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.status, self.message, self.retry_after = status, message, retry_after


# --- Passwords ---------------------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    with hashing:
        return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * n * r * 2, dklen=32)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def check_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        n, r, p = int(n), int(r), int(p)
        if kind != "scrypt" or not (2 ** 10 <= n <= 2 ** 20 and 1 <= r <= 32 and 1 <= p <= 16):
            return False
        return hmac.compare_digest(_scrypt(password, _unb64(salt), n, r, p), _unb64(digest))
    except (ValueError, TypeError):
        return False


# An unknown email takes as long as a wrong password
_NOBODY = hash_password(secrets.token_urlsafe(16))


# --- Tokens ------------------------------------------------------------------------------------------

def sign(claims: dict) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signature = hmac.new(JWT_SECRET, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(signature)}"


def verify(token: str, expired_too: bool = False) -> dict | None:
    """A token's claims if it was signed here (and hasn't expired), else None"""
    try:
        header, payload, signature = token.split(".")
        if json.loads(_unb64(header)).get("alg") != "HS256":
            return None
        expected = hmac.new(JWT_SECRET, f"{header}.{payload}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(signature)):
            return None
        claims = json.loads(_unb64(payload))
        if not isinstance(claims, dict) or not isinstance(claims.get("exp"), int):
            return None
        if not expired_too and claims["exp"] <= time.time():
            return None
        return claims
    except (ValueError, TypeError, AttributeError):
        return None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- Limits ------------------------------------------------------------------------------------------

class Limit:
    """At most `count` events per key in `seconds`"""

    def __init__(self, count: int, seconds: int):
        self.count, self.seconds = count, seconds
        self.events: dict[str, deque] = {}
        self.lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque:
        found = self.events.setdefault(key, deque())
        while found and found[0] <= now - self.seconds:
            found.popleft()
        return found

    def check(self, key: str):
        now = time.monotonic()
        with self.lock:
            found = self._recent(key, now)
            if len(found) >= self.count:
                raise Refused(429, "Too many tries: wait a while and try again", int(found[0] + self.seconds - now) + 1)

    def add(self, key: str):
        now = time.monotonic()
        with self.lock:
            if len(self.events) > 50_000:
                for k in [k for k, v in self.events.items() if not v or v[-1] <= now - self.seconds]:
                    del self.events[k]
            self._recent(key, now).append(now)

    def clear(self, key: str):
        with self.lock:
            self.events.pop(key, None)


failed_by_email = Limit(10, 15 * 60)
failed_by_address = Limit(50, 15 * 60)
signups_by_address = Limit(30, 60 * 60)


# --- Sign-in -----------------------------------------------------------------------------------------

def database():
    import psycopg  # here, so the rest of this works (and is tested) without it

    if not AUTH_DB_URI:
        raise Refused(503, "Sign-in isn't set up for this app")
    return psycopg.connect(AUTH_DB_URI, connect_timeout=5)


def public_user(row) -> dict:
    user_id, email, created_at, last_sign_in_at = row
    return {"id": str(user_id), "email": email, "created_at": created_at.isoformat(),
            "last_sign_in_at": last_sign_in_at.isoformat() if last_sign_in_at else None}


def start_session(c, user_id) -> dict:
    """A new sign-in for the user (in c's transaction): its tokens"""
    session_id = c.execute("INSERT INTO auth.sessions (user_id, expires_at) VALUES (%s, now() + make_interval(days => %s)) "
                           "RETURNING id", (user_id, SESSION_DAYS)).fetchone()[0]
    return issue(c, session_id, user_id)


def issue(c, session_id, user_id) -> dict:
    refresh_token = secrets.token_urlsafe(32)
    c.execute("INSERT INTO auth.refresh_tokens (token_hash, session_id) VALUES (%s, %s)", (token_hash(refresh_token), session_id))
    user = c.execute("SELECT id, email, created_at, last_sign_in_at FROM auth.users WHERE id = %s", (user_id,)).fetchone()
    now = int(time.time())
    claims = {"sub": str(user_id), "role": "authenticated", "email": user[1], "session_id": str(session_id),
              "iat": now, "exp": now + ACCESS_SECONDS}
    return {"access_token": sign(claims), "token_type": "bearer", "expires_in": ACCESS_SECONDS, "expires_at": claims["exp"],
            "refresh_token": refresh_token, "user": public_user(user)}


def credentials(body: dict) -> tuple[str, str]:
    email, password = body.get("email"), body.get("password")
    if not isinstance(email, str) or not isinstance(password, str):
        raise Refused(400, "An email and a password, please")
    email = email.strip().lower()
    if len(email) > 254 or not EMAIL.match(email):
        raise Refused(400, "That isn't an email address")
    return email, password


def new_password(password) -> str:
    if not isinstance(password, str) or not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise Refused(400, f"A password has {PASSWORD_MIN} to {PASSWORD_MAX} characters")
    return password


def signup(body: dict, address: str) -> tuple[int, dict]:
    if not SIGNUPS:
        raise Refused(403, "New accounts can't be made here")
    email, password = credentials(body)
    new_password(password)
    signups_by_address.check(address)
    hashed = hash_password(password)
    with connecting, database() as c:
        found = c.execute("INSERT INTO auth.users (email, password_hash, last_sign_in_at) VALUES (%s, %s, now()) "
                          "ON CONFLICT (email) DO NOTHING RETURNING id", (email, hashed)).fetchone()
        if found is None:
            raise Refused(409, "There's an account with that email already: sign in")
        signups_by_address.add(address)
        return 201, start_session(c, found[0])


def signin(body: dict, address: str) -> tuple[int, dict]:
    email, password = credentials(body)
    failed_by_email.check(email)
    failed_by_address.check(address)
    with connecting, database() as c:
        found = c.execute("SELECT id, password_hash, disabled FROM auth.users WHERE email = %s", (email,)).fetchone()
        if not check_password(password, found[1] if found else _NOBODY) or found is None:
            failed_by_email.add(email)
            failed_by_address.add(address)
            raise Refused(401, "Wrong email or password")
        if found[2]:
            raise Refused(403, "This account is disabled")
        failed_by_email.clear(email)
        c.execute("UPDATE auth.users SET last_sign_in_at = now() WHERE id = %s", (found[0],))
        return 200, start_session(c, found[0])


ENDED = "This sign-in has ended: sign in again"


def refresh(body: dict) -> tuple[int, dict]:
    token = body.get("refresh_token")
    if not isinstance(token, str) or not 0 < len(token) <= 200:
        raise Refused(400, "A refresh_token, please")
    with connecting, database() as c:
        found = c.execute(
            "SELECT t.used_at IS NOT NULL, t.used_at < now() - make_interval(secs => %s), s.id, "
            "s.ended_at IS NULL AND s.expires_at > now() AND NOT u.disabled, u.id "
            "FROM auth.refresh_tokens t JOIN auth.sessions s ON s.id = t.session_id JOIN auth.users u ON u.id = s.user_id "
            "WHERE t.token_hash = %s FOR UPDATE OF t, s", (REUSE_GRACE_SECONDS, token_hash(token))).fetchone()
        if found is None:
            raise Refused(401, ENDED)
        used, used_long_ago, session_id, live, user_id = found
        if not live:
            raise Refused(401, ENDED)
        if used and used_long_ago:
            # Used before, a while ago: someone else has a copy. The sign-in ends, for both.
            c.execute("UPDATE auth.sessions SET ended_at = now() WHERE id = %s", (session_id,))
            c.commit()
            raise Refused(401, ENDED)
        if not used:
            c.execute("UPDATE auth.refresh_tokens SET used_at = now() WHERE token_hash = %s", (token_hash(token),))
        c.execute("UPDATE auth.sessions SET refreshed_at = now(), expires_at = now() + make_interval(days => %s) WHERE id = %s",
                  (SESSION_DAYS, session_id))
        c.execute("DELETE FROM auth.refresh_tokens WHERE session_id = %s AND used_at < now() - interval '1 day'", (session_id,))
        return 200, issue(c, session_id, user_id)


def bearer(headers) -> str | None:
    found = headers.get("Authorization") or ""
    return found[7:].strip() if found[:7].lower() == "bearer " else None


def signout(body: dict, headers) -> tuple[int, dict | None]:
    token, access = body.get("refresh_token"), bearer(headers)
    claims = verify(access, expired_too=True) if access else None
    if not (isinstance(token, str) and 0 < len(token) <= 200) and not claims:
        raise Refused(400, "A refresh_token, or the access token, please")
    with connecting, database() as c:
        if claims and claims.get("session_id"):
            c.execute("UPDATE auth.sessions SET ended_at = now() WHERE id = %s AND ended_at IS NULL", (claims["session_id"],))
        if isinstance(token, str) and 0 < len(token) <= 200:
            c.execute("UPDATE auth.sessions SET ended_at = now() WHERE ended_at IS NULL AND id = "
                      "(SELECT session_id FROM auth.refresh_tokens WHERE token_hash = %s)", (token_hash(token),))
    return 204, None


def signed_in(c, headers) -> tuple[str, str]:
    """The (user id, session id) of a request's access token, if its sign-in is still on"""
    access = bearer(headers)
    claims = verify(access) if access else None
    if not claims or claims.get("role") != "authenticated":
        raise Refused(401, "Sign in first")
    found = c.execute("SELECT 1 FROM auth.sessions s JOIN auth.users u ON u.id = s.user_id WHERE s.id::text = %s "
                      "AND u.id::text = %s AND s.ended_at IS NULL AND s.expires_at > now() AND NOT u.disabled",
                      (str(claims.get("session_id")), str(claims.get("sub")))).fetchone()
    if not found:
        raise Refused(401, ENDED)
    return claims["sub"], claims["session_id"]


def user(headers) -> tuple[int, dict]:
    with connecting, database() as c:
        user_id, _ = signed_in(c, headers)
        return 200, public_user(c.execute("SELECT id, email, created_at, last_sign_in_at FROM auth.users WHERE id = %s",
                                          (user_id,)).fetchone())


def change_password(body: dict, headers, address: str) -> tuple[int, None]:
    password, wanted = body.get("password"), new_password(body.get("new_password"))
    if not isinstance(password, str):
        raise Refused(400, "The password now, and the new one, please")
    with connecting, database() as c:
        user_id, session_id = signed_in(c, headers)
        email, stored = c.execute("SELECT email, password_hash FROM auth.users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
        failed_by_email.check(email)
        if not check_password(password, stored):
            failed_by_email.add(email)
            failed_by_address.add(address)
            raise Refused(401, "That isn't the password now")
        c.execute("UPDATE auth.users SET password_hash = %s WHERE id = %s", (hash_password(wanted), user_id))
        c.execute("UPDATE auth.sessions SET ended_at = now() WHERE user_id = %s AND id <> %s AND ended_at IS NULL",
                  (user_id, session_id))
    return 204, None


# --- PostgREST ---------------------------------------------------------------------------------------

def postgrest_env() -> dict:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PGRST_DB_URI": os.environ["API_DB_URI"],
        "PGRST_DB_SCHEMAS": "public",
        "PGRST_DB_ANON_ROLE": "anon",
        "PGRST_JWT_SECRET": os.environ["API_JWT_SECRET"],
        # Before each request: a signed-in one's sign-in must still be on
        "PGRST_DB_PRE_REQUEST": "auth.check_request",
        "PGRST_SERVER_HOST": "127.0.0.1",
        "PGRST_SERVER_PORT": str(POSTGREST_PORT),
        "PGRST_DB_MAX_ROWS": "1000",
        "PGRST_DB_POOL": "5",
        # Listens on `pgrst` for "reload schema" after each migration
        "PGRST_DB_CHANNEL_ENABLED": "true",
        "PGRST_LOG_LEVEL": "error",
    }


def keep_postgrest_running():
    """PostgREST, started again a moment after it stops; `ready` while it answers"""
    while True:
        process = subprocess.Popen(["postgrest"], env=postgrest_env())
        while process.poll() is None:
            try:
                connection = http.client.HTTPConnection("127.0.0.1", POSTGREST_PORT, timeout=2)
                connection.request("GET", "/")
                connection.getresponse().read()
                ready.set()
            except OSError:
                ready.clear()
            time.sleep(2)
        ready.clear()
        print(f"gateway: PostgREST stopped ({process.returncode}); starting it again", file=sys.stderr)
        time.sleep(2)


# --- HTTP --------------------------------------------------------------------------------------------

AUTH_ROUTES = {
    ("POST", "/auth/signup"): lambda h, body: signup(body, h.client_address[0]),
    ("POST", "/auth/signin"): lambda h, body: signin(body, h.client_address[0]),
    ("POST", "/auth/refresh"): lambda h, body: refresh(body),
    ("POST", "/auth/signout"): lambda h, body: signout(body, h.headers),
    ("GET", "/auth/user"): lambda h, body: user(h.headers),
    ("POST", "/auth/password"): lambda h, body: change_password(body, h.headers, h.client_address[0]),
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        print(f"{self.address_string()} {format % args}", file=sys.stderr)

    def answer(self, status: int, body: dict | None, headers: dict | None = None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def handle_any(self):
        path = self.path
        if path in ("/", ""):
            return self.answer(200, {"rest": "/rest/", "auth": "/auth/", "client": "/client.js", "signups": SIGNUPS,
                                     "ready": ready.is_set()})
        if path == "/health":
            return self.answer(200 if ready.is_set() else 503, {"ready": ready.is_set()})
        if path == "/client.js" and self.command in ("GET", "HEAD"):
            return self.client_js()
        if path == "/auth" or path.startswith("/auth/") or path.startswith("/auth?"):
            return self.auth()
        if not (path == "/rest" or path.startswith("/rest/") or path.startswith("/rest?")):
            return self.answer(404, {"message": "Not here: the tables are under /rest/, sign-in under /auth/"})
        self.rest()

    def client_js(self):
        data = CLIENT_JS.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/javascript; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def auth(self):
        path = self.path.split("?", 1)[0]
        if self.command == "OPTIONS":
            return self.answer(204, None, {"Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                                           "Access-Control-Allow-Headers": "Authorization, Content-Type",
                                           "Access-Control-Max-Age": "600"})
        route = AUTH_ROUTES.get((self.command, path))
        if route is None:
            known = {p for _, p in AUTH_ROUTES}
            if path in known:
                return self.answer(405, {"message": f"Not with {self.command}"})
            return self.answer(404, {"message": "Not here: " + ", ".join(sorted(known))})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self.answer(400, {"message": "A bad Content-Length"})
        if length > MAX_AUTH_BODY:
            return self.answer(413, {"message": "Too much"})
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw.strip() else {}
        except (ValueError, UnicodeDecodeError):
            return self.answer(400, {"message": "The body has to be JSON"})
        if not isinstance(body, dict):
            return self.answer(400, {"message": "The body has to be a JSON object"})
        try:
            status, found = route(self, body)
        except Refused as e:
            return self.answer(e.status, {"message": e.message},
                               {"Retry-After": str(e.retry_after)} if e.retry_after else None)
        except Exception as e:  # the database unreachable, mostly: said, not shown in full
            print(f"gateway: {self.command} {path} failed: {type(e).__name__}: {e}", file=sys.stderr)
            return self.answer(503, {"message": "Sign-in isn't working right now: try again in a moment"})
        self.answer(status, found)

    def rest(self):
        path = self.path
        if not ready.wait(timeout=10):
            return self.answer(503, {"message": "The API is starting: try again in a moment"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self.answer(413, {"message": f"A request can be {MAX_BODY // (1024 * 1024)} MB at most"})
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
        upstream = path[len("/rest"):] or "/"
        if not upstream.startswith("/"):
            upstream = "/" + upstream
        try:
            connection = http.client.HTTPConnection("127.0.0.1", POSTGREST_PORT, timeout=60)
            connection.request(self.command, upstream, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
        except OSError as e:
            return self.answer(502, {"message": f"The API didn't answer: {e}"})
        self.send_response(response.status, response.reason)
        for key, value in response.getheaders():
            if key.lower() not in HOP_BY_HOP:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = do_OPTIONS = do_HEAD = handle_any


if __name__ == "__main__":
    if len(JWT_SECRET) < 32:
        sys.exit("gateway: API_JWT_SECRET has to be 32 characters or more")
    threading.Thread(target=keep_postgrest_running, daemon=True).start()
    print(f"gateway: on port {PORT}", file=sys.stderr)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
