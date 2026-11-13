"""Secrets for HTTP actions (API keys, tokens), encrypted in the database with Fernet.

The key is LABB_SECRET_KEY, or a `secret.key` file the server makes next to its data the
first time (in Docker, in the labb-data volume). Back it up with the database: without it
the secrets can't be read, and have to be entered again.

Secrets only ever reach an HTTP action's request. They aren't shown back, aren't given to
models or Python steps, and are masked in what a run's steps record.
"""

import base64
import hashlib
import os
import re
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from labb.agent import CHAT_DB
from labb.server.studio import store

NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
MAX_VALUE = 8000
MASK = "••••••"
KEY_FILE = Path(os.getenv("LABB_SECRET_KEY_FILE", Path(CHAT_DB).parent / "secret.key"))

_fernet: Fernet | None = None


class SecretError(ValueError):
    """A secret that can't be saved, said for the admin"""


def fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        if configured := os.getenv("LABB_SECRET_KEY", "").strip():
            # Any text works: it's turned into a key
            key = base64.urlsafe_b64encode(hashlib.sha256(configured.encode()).digest())
        else:
            if not KEY_FILE.exists():
                KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
                KEY_FILE.write_bytes(Fernet.generate_key())
                KEY_FILE.chmod(0o600)
            key = KEY_FILE.read_bytes().strip()
        _fernet = Fernet(key)
    return _fernet


def put(name: str, value: str, user_id: str):
    name = name.strip()
    if not NAME.match(name):
        raise SecretError("A secret's name is UPPER_CASE letters, digits and underscores, like TODOIST_TOKEN")
    if not value or len(value) > MAX_VALUE:
        raise SecretError(f"A secret is 1 to {MAX_VALUE} characters")
    store.put_secret(name, fernet().encrypt(value.encode()).decode(), user_id)


def values() -> dict[str, str]:
    """Every secret, decrypted, for an HTTP action's request; ones that can't be read are left out"""

    found = {}
    for name, token in store.secret_values().items():
        try:
            found[name] = fernet().decrypt(token.encode()).decode()
        except InvalidToken:
            continue
    return found


def mask(value, secrets: dict[str, str]):
    """Replace secret values in what a step records"""

    if isinstance(value, str):
        for secret in secrets.values():
            if len(secret) >= 4:
                value = value.replace(secret, MASK)
        return value
    if isinstance(value, dict):
        return {k: mask(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v, secrets) for v in value]
    return value
