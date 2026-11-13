"""Accounts for the server: users and roles, web sessions, API keys, invites, and the
browser login labb uses.

- Passwords are hashed with scrypt (Python's standard library), with a salt per password.
- Session tokens, API keys and invite tokens are random and only their SHA-256 is stored:
  they're long enough that a fast hash is safe, and a leaked database gives none of them away.
- The first account becomes the admin (`setup`), with the setup token the server prints to its log
  (`setup_token`), so whoever reaches a fresh server first can't take it; after that, admins invite people.
"""

import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from labb.agent import CHAT_DB
from labb.chats import conn, lock

ROLES = ("admin", "member")
DEVICE_MINUTES = 10
KEY_PREFIX = "lb_"
# How many characters of a key are kept to show which one it is
SHOWN_KEY_CHARS = 12
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
MIN_PASSWORD = 8


def _limit(key: str) -> int:
    # Settings → Limits; limits imports what imports this, so not at the top
    from labb.server import limits
    return limits.get(key)


def session_days() -> int:
    return _limit("session_days")
# scrypt's cost: 16 MB of memory and a few dozen milliseconds per check
SCRYPT = {"n": 2**14, "r": 8, "p": 1}
# The words in a device code: no letters that are easy to mix up (0/O, 1/I/L)
CODE_LETTERS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

with lock, conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, "
        "role TEXT NOT NULL, created_at TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS web_sessions ("
        "token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS api_keys ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, shown TEXT NOT NULL, "
        "key_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS invites ("
        "id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, role TEXT NOT NULL, created_by TEXT NOT NULL, "
        "created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_by TEXT, used_at TEXT)"
    )
    # labb --login: the PC waits with `device_code` while you approve `user_code` in the browser;
    # the key waits here until labb picks it up (or the login expires)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS device_logins ("
        "device_hash TEXT PRIMARY KEY, user_code TEXT NOT NULL UNIQUE, machine TEXT NOT NULL, "
        "created_at TEXT NOT NULL, expires_at TEXT NOT NULL, status TEXT NOT NULL, user_id TEXT, api_key TEXT)"
    )


class AuthError(ValueError):
    """Something a person did wrong (a taken username, a short password…), said for them"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime) -> str:
    return moment.isoformat()


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- Passwords ---------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    encode = lambda b: base64.b64encode(b).decode()
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${encode(salt)}${encode(key)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, key = stored.split("$")
        expected = base64.b64decode(key)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def check_password_rules(password: str):
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"The password needs at least {MIN_PASSWORD} characters")


# Wrong passwords per (address, username) and per address: after a few, each attempt waits longer
# (up to 15 minutes). Per address too, so someone failing on purpose from elsewhere doesn't lock the
# real person out, and trying one password on many usernames is slowed as well.
failures: dict[tuple[str, str], tuple[int, float]] = {}
failures_lock = threading.Lock()
_setup_lock = threading.Lock()
# Wrong passwords for a username before it waits: Settings → Limits (sign_in_tries); this is its default
FREE_TRIES = 5
# Wrong passwords from one address, whatever the username, before it waits too
FREE_TRIES_PER_ADDRESS = 20
# Kept at most, so made-up usernames can't fill the memory; the oldest go first
MAX_FAILURES = 10_000
FORGET_SECONDS = 24 * 3600


def _delay(count: int, last: float, free: int) -> float:
    if count < free:
        return 0.0
    delay = min(30 * 2 ** (count - free), 15 * 60)
    return max(0.0, last + delay - time.monotonic())


def wait_before_login(username: str, address: str = "") -> float:
    """Seconds until this username may be tried again from this address (0: now)"""

    with failures_lock:
        count, last = failures.get((address, username), (0, 0.0))
        everyone, last_any = failures.get((address, ""), (0, 0.0))
    return max(_delay(count, last, _limit("sign_in_tries")), _delay(everyone, last_any, FREE_TRIES_PER_ADDRESS))


def note_login(username: str, ok: bool, address: str = ""):
    with failures_lock:
        if ok:
            failures.pop((address, username), None)
            return
        now_ = time.monotonic()
        for key in ((address, username), (address, "")):
            count, _ = failures.get(key, (0, 0.0))
            failures.pop(key, None)
            failures[key] = (count + 1, now_)
        # Oldest first (dicts keep insertion order, and a key is put back at the end when it fails)
        while failures and (len(failures) > MAX_FAILURES or next(iter(failures.values()))[1] < now_ - FORGET_SECONDS):
            failures.pop(next(iter(failures)))


# --- Setting up a fresh server --------------------------------------------------

SETUP_TOKEN_FILE = Path(os.getenv("LABB_SETUP_TOKEN_FILE", Path(CHAT_DB).parent / "setup-token"))


def setup_token() -> str | None:
    """The token that creating the first account takes, made when it's first needed and kept in
    the data folder (so a restart prints the same one); None once the server has accounts"""

    if has_users():
        return None
    try:
        if token := SETUP_TOKEN_FILE.read_text().strip():
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(18)
    SETUP_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(SETUP_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(token + "\n")
    return token


def check_setup_token(given: str | None):
    expected = setup_token()
    if expected and not hmac.compare_digest((given or "").strip().encode(), expected.encode()):
        raise SetupTokenError(
            "Creating the first account takes the setup link the server printed when it started "
            "(docker compose logs labb-web)"
        )


class SetupTokenError(AuthError):
    """A first account asked for without the setup token"""


# --- Users -------------------------------------------------------------------

def public(row) -> dict:
    user_id, username, role, created_at, disabled = row
    return {"id": user_id, "username": username, "role": role, "created_at": created_at, "disabled": bool(disabled)}


USER_COLUMNS = "id, username, role, created_at, disabled"


def has_users() -> bool:
    with lock:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def get_user(user_id: str) -> dict | None:
    with lock:
        row = conn.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone()
    return public(row) if row else None


def list_users() -> list[dict]:
    with lock:
        rows = conn.execute(f"SELECT {USER_COLUMNS} FROM users ORDER BY created_at").fetchall()
    return [public(row) for row in rows]


def create_user(username: str, password: str, role: str = "member") -> dict:
    username = username.strip().lower()
    if not USERNAME.match(username):
        raise AuthError("A username is 2 to 32 lowercase letters, digits, dots, dashes or underscores, starting with a letter or digit")
    check_password_rules(password)
    if role not in ROLES:
        raise AuthError(f"The role is {' or '.join(ROLES)}")
    user_id = str(uuid.uuid4())
    password_hash = hash_password(password)
    with lock, conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise AuthError(f"The username {username} is taken")
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, password_hash, role, stamp(now())),
        )
    # The agents and skills a new account starts with
    from labb.server import defaults

    defaults.ensure(user_id)
    return get_user(user_id)


def setup(username: str, password: str, token: str | None = None, need_token: bool = False) -> dict:
    """The first account on a fresh server: the admin. With need_token, it takes the setup token."""

    # Two first visits at once can't both become the admin
    with _setup_lock:
        if has_users():
            raise AuthError("This server is set up already; ask an admin for an invite")
        if need_token:
            check_setup_token(token)
        user = create_user(username, password, role="admin")
    SETUP_TOKEN_FILE.unlink(missing_ok=True)
    return user


def login(username: str, password: str, address: str = "") -> dict | None:
    """The user, if the password is right and the account is active"""

    username = username.strip().lower()
    with lock:
        row = conn.execute(f"SELECT {USER_COLUMNS}, password_hash FROM users WHERE username = ?", (username,)).fetchone()
    # Check a password even for unknown usernames, so the time taken doesn't tell which exist
    ok = verify_password(password, row[5] if row else hash_password("not a user"))
    ok = ok and row is not None and not row[4]
    note_login(username, ok, address)
    return public(row[:5]) if ok else None


def change_password(user_id: str, current: str, new: str, keep_session: str | None = None):
    """Change a password; the user's other web sessions are logged out"""

    with lock:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None or not verify_password(current, row[0]):
        raise AuthError("The current password isn't right")
    check_password_rules(new)
    with lock, conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new), user_id))
        conn.execute(
            "DELETE FROM web_sessions WHERE user_id = ? AND token_hash != ?",
            (user_id, digest(keep_session) if keep_session else ""),
        )


def active_admins(excluding: str | None = None) -> int:
    with lock:
        return conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0 AND id != ?", (excluding or "",)
        ).fetchone()[0]


def update_user(user_id: str, *, role: str | None = None, disabled: bool | None = None) -> dict:
    """An admin changes someone's role, or disables or enables their account"""

    user = get_user(user_id)
    if user is None:
        raise AuthError("No such user")
    if role is not None and role not in ROLES:
        raise AuthError(f"The role is {' or '.join(ROLES)}")
    losing_admin = user["role"] == "admin" and not user["disabled"] and (role == "member" or disabled)
    if losing_admin and active_admins(excluding=user_id) == 0:
        raise AuthError("There has to be at least one active admin")
    with lock, conn:
        if role is not None:
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        if disabled is not None:
            conn.execute("UPDATE users SET disabled = ? WHERE id = ?", (int(disabled), user_id))
            if disabled:
                # Logged out everywhere; their keys stop working while they're disabled
                conn.execute("DELETE FROM web_sessions WHERE user_id = ?", (user_id,))
    return get_user(user_id)


# --- Web sessions ------------------------------------------------------------

def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    with lock, conn:
        # Sessions that ran out, which nothing else deletes
        conn.execute("DELETE FROM web_sessions WHERE expires_at < ?", (stamp(now()),))
        conn.execute(
            "INSERT INTO web_sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (digest(token), user_id, stamp(now()), stamp(now() + timedelta(days=session_days()))),
        )
    return token


def user_for_session(token: str) -> dict | None:
    with lock:
        row = conn.execute(
            "SELECT user_id, expires_at FROM web_sessions WHERE token_hash = ?", (digest(token),)
        ).fetchone()
    if row is None or datetime.fromisoformat(row[1]) < now():
        return None
    user = get_user(row[0])
    return user if user and not user["disabled"] else None


def end_session(token: str):
    with lock, conn:
        conn.execute("DELETE FROM web_sessions WHERE token_hash = ?", (digest(token),))


# --- API keys ----------------------------------------------------------------

def key_info(row) -> dict:
    key_id, user_id, name, shown, created_at, last_used_at, revoked_at = row
    return {"id": key_id, "user_id": user_id, "name": name, "shown": shown, "created_at": created_at,
            "last_used_at": last_used_at, "revoked_at": revoked_at}


KEY_COLUMNS = "id, user_id, name, shown, created_at, last_used_at, revoked_at"


def create_key(user_id: str, name: str) -> tuple[str, dict]:
    """A new key for a user: (the key, which is only ever shown now, and its details)"""

    name = " ".join(name.split())[:80]
    if not name:
        raise AuthError("Give the key a name, e.g. the PC it's for")
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    key_id = str(uuid.uuid4())
    with lock, conn:
        conn.execute(
            "INSERT INTO api_keys (id, user_id, name, shown, key_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (key_id, user_id, name, key[:SHOWN_KEY_CHARS], digest(key), stamp(now())),
        )
        row = conn.execute(f"SELECT {KEY_COLUMNS} FROM api_keys WHERE id = ?", (key_id,)).fetchone()
    return key, key_info(row)


def user_for_key(key: str) -> dict | None:
    """The user a key belongs to, if it's valid; notes when it was used (every minute at most)"""

    found = key_owner(key)
    return found[0] if found else None


def key_owner(key: str) -> tuple[dict, str] | None:
    """The user a valid key belongs to, and the key's id"""

    if not key.startswith(KEY_PREFIX):
        return None
    with lock:
        row = conn.execute(
            "SELECT id, user_id, last_used_at FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL", (digest(key),)
        ).fetchone()
    if row is None:
        return None
    user = get_user(row[1])
    if user is None or user["disabled"]:
        return None
    if row[2] is None or now() - datetime.fromisoformat(row[2]) > timedelta(minutes=1):
        with lock, conn:
            conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (stamp(now()), row[0]))
    return user, row[0]


def list_keys(user_id: str | None = None) -> list[dict]:
    """A user's keys (everyone's for None, with the username), newest first; revoked ones too"""

    with lock:
        rows = conn.execute(
            f"SELECT {', '.join('k.' + c for c in KEY_COLUMNS.split(', '))}, u.username FROM api_keys k "
            "JOIN users u ON u.id = k.user_id WHERE ? IS NULL OR k.user_id = ? ORDER BY k.created_at DESC",
            (user_id, user_id),
        ).fetchall()
    return [{**key_info(row[:7]), "username": row[7]} for row in rows]


def revoke_key(key_id: str, user_id: str | None = None) -> bool:
    """Revoke a key (only the user's own, unless user_id is None, for admins)"""

    with lock, conn:
        return conn.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL AND (? IS NULL OR user_id = ?)",
            (stamp(now()), key_id, user_id, user_id),
        ).rowcount > 0


def revoke_key_value(key: str) -> bool:
    """Revoke the key itself (labb --logout)"""

    with lock, conn:
        return conn.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE key_hash = ? AND revoked_at IS NULL", (stamp(now()), digest(key))
        ).rowcount > 0


# --- Invites -----------------------------------------------------------------

def create_invite(created_by: str, role: str = "member") -> tuple[str, dict]:
    """A single-use invite: (its token, for the link, and its details)"""

    if role not in ROLES:
        raise AuthError(f"The role is {' or '.join(ROLES)}")
    token = secrets.token_urlsafe(24)
    with lock, conn:
        # Unused invites a month past their end
        conn.execute("DELETE FROM invites WHERE used_at IS NULL AND expires_at < ?", (stamp(now() - timedelta(days=30)),))
    invite = {"id": str(uuid.uuid4()), "role": role, "created_by": created_by, "created_at": stamp(now()),
              "expires_at": stamp(now() + timedelta(days=_limit("invite_days")))}
    with lock, conn:
        conn.execute(
            "INSERT INTO invites (id, token_hash, role, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (invite["id"], digest(token), role, created_by, invite["created_at"], invite["expires_at"]),
        )
    return token, invite


def open_invite(token: str) -> dict | None:
    """An invite that can still be used"""

    with lock:
        row = conn.execute(
            "SELECT id, role, expires_at FROM invites WHERE token_hash = ? AND used_at IS NULL", (digest(token),)
        ).fetchone()
    if row is None or datetime.fromisoformat(row[2]) < now():
        return None
    return {"id": row[0], "role": row[1], "expires_at": row[2]}


def accept_invite(token: str, username: str, password: str) -> dict:
    invite = open_invite(token)
    if invite is None:
        raise AuthError("This invite link was used already or has expired; ask for a new one")
    check_password_rules(password)
    # Taken first, so two people using one link at once can't both join with it
    with lock, conn:
        taken = conn.execute(
            "UPDATE invites SET used_at = ? WHERE id = ? AND used_at IS NULL", (stamp(now()), invite["id"])
        ).rowcount == 1
    if not taken:
        raise AuthError("This invite link was used already or has expired; ask for a new one")
    try:
        user = create_user(username, password, invite["role"])
    except Exception:
        # A taken username, say: the link still works
        with lock, conn:
            conn.execute("UPDATE invites SET used_at = NULL WHERE id = ?", (invite["id"],))
        raise
    with lock, conn:
        conn.execute("UPDATE invites SET used_by = ? WHERE id = ?", (user["id"], invite["id"]))
        row = conn.execute("SELECT created_by FROM invites WHERE id = ?", (invite["id"],)).fetchone()
    # Whoever made the invite hears that it was used
    if row:
        from labb.server import inbox
        inbox.add(row[0], "account", f"{user['username']} joined", f"They used your invite and joined as {user['role']}.",
                  link="/settings/users", key=f"invite:{invite['id']}", level="info")
    return user


def list_invites() -> list[dict]:
    """The invites nobody used yet that haven't expired"""

    with lock:
        rows = conn.execute(
            "SELECT i.id, i.role, i.created_at, i.expires_at, u.username FROM invites i "
            "LEFT JOIN users u ON u.id = i.created_by WHERE i.used_at IS NULL ORDER BY i.created_at DESC"
        ).fetchall()
    return [{"id": i, "role": r, "created_at": c, "expires_at": e, "created_by": u}
            for i, r, c, e, u in rows if datetime.fromisoformat(e) > now()]


def cancel_invite(invite_id: str) -> bool:
    with lock, conn:
        return conn.execute("DELETE FROM invites WHERE id = ? AND used_at IS NULL", (invite_id,)).rowcount > 0


# --- labb --login ---------------------------------------------------------

def start_device_login(machine: str) -> dict:
    """labb asks to log in: a code for the person to approve, and one for labb to wait with"""

    device_code = secrets.token_urlsafe(32)
    user_code = "-".join("".join(secrets.choice(CODE_LETTERS) for _ in range(4)) for _ in range(2))
    with lock, conn:
        # Old logins nobody finished
        conn.execute("DELETE FROM device_logins WHERE expires_at < ?", (stamp(now()),))
        conn.execute(
            "INSERT INTO device_logins (device_hash, user_code, machine, created_at, expires_at, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (digest(device_code), user_code, " ".join(machine.split())[:64] or "a PC", stamp(now()),
             stamp(now() + timedelta(minutes=DEVICE_MINUTES))),
        )
    return {"device_code": device_code, "user_code": user_code, "expires_in": DEVICE_MINUTES * 60}


def device_login(user_code: str) -> dict | None:
    """A login waiting for approval, by the code the person sees"""

    with lock:
        row = conn.execute(
            "SELECT machine, expires_at, status FROM device_logins WHERE user_code = ?", (user_code.strip().upper(),)
        ).fetchone()
    if row is None or row[2] != "pending" or datetime.fromisoformat(row[1]) < now():
        return None
    return {"machine": row[0], "expires_at": row[1]}


def answer_device_login(user_code: str, user: dict, approve: bool):
    """The person approves (a key is made for them, named after the PC) or denies the login"""

    login_ = device_login(user_code)
    if login_ is None:
        raise AuthError("That code isn't waiting for approval; run labb --login again")
    key = None
    if approve:
        key, _ = create_key(user["id"], f"labb on {login_['machine']}")
    with lock, conn:
        conn.execute(
            "UPDATE device_logins SET status = ?, user_id = ?, api_key = ? WHERE user_code = ?",
            ("approved" if approve else "denied", user["id"], key, user_code.strip().upper()),
        )


def poll_device_login(device_code: str) -> dict:
    """What labb sees while it waits: pending, denied, expired, or approved with the key
    (handed over once: the server forgets it right after)"""

    with lock, conn:
        row = conn.execute(
            "SELECT status, expires_at, api_key, user_id FROM device_logins WHERE device_hash = ?", (digest(device_code),)
        ).fetchone()
        if row is None:
            return {"status": "expired"}
        status, expires_at, key, user_id = row
        if status == "approved":
            conn.execute("DELETE FROM device_logins WHERE device_hash = ?", (digest(device_code),))
            user = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
            return {"status": "approved", "api_key": key, "username": user[0] if user else None}
        if status == "denied":
            conn.execute("DELETE FROM device_logins WHERE device_hash = ?", (digest(device_code),))
            return {"status": "denied"}
    if datetime.fromisoformat(expires_at) < now():
        return {"status": "expired"}
    return {"status": "pending"}
