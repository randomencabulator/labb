"""What an app keeps besides its files: its data and its settings.

- Data: a folder per app (SPAWNER_DATA/apps/<id>/data), mounted at /data in its container: the one
  place it can write that outlives a restart or a rebuild. It's the app's user's (65534) to write.
  The spawner reads it to measure it, pack it for a download, or remove it, and never follows a
  link the app made in it.
- Settings: environment variables for its container, kept in SPAWNER_DATA/apps/<id>/settings.json.
  A secret's value is encrypted there with SPAWNER_DATA/secret.key (made on first use), and never
  given back: only to the container, when it starts.
- labb's own variables for it (LABB_URL, LABB_API_KEY: an app that may use labb), which the server
  sets; kept encrypted the same way, never given back, and not the user's to change.
- Its database's password (database.py), kept encrypted the same way: the app gets DATABASE_URL.
- Its API's (its API role's password and the secret its tokens are signed with), the same way.
"""

import json
import os
import re
import tarfile
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from labb_spawner import limits, sources

# The app's user (see app.py's run): its data folder is its own
APP_UID = 65534
DATA_LIMIT = int(os.getenv("APP_DATA_MB", "1024")) * 1024 * 1024
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
# What labb sets itself, so an app always has them as labb means them
LABB_VARIABLES = {"LABB_URL", "LABB_API_KEY"}
RESERVED = {"PORT", "APP_NAME", "DATABASE_URL"} | LABB_VARIABLES
MAX_VARIABLES = 50
MAX_VALUE = 8192

_key_lock = threading.Lock()


def data_dir(app_id: str) -> Path:
    return sources.folder(app_id) / "data"


def ensure_data(app_id: str) -> Path:
    """The app's data folder, made for its user if it isn't there yet"""
    path = data_dir(app_id)
    if not path.is_dir():
        path.mkdir(parents=True, exist_ok=True)
        if os.geteuid() == 0:
            os.chown(path, APP_UID, APP_UID)
            path.chmod(0o700)
        else:
            # A checkout's spawner can't give it away: anyone may write it
            path.chmod(0o777)
    return path


def data_size(app_id: str) -> int:
    """Bytes in the app's data, counting links as themselves, never what they point at"""
    total = 0
    root = data_dir(app_id)
    if not root.is_dir():
        return 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in files + dirs:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                pass
    return total


def pack_data(app_id: str, out):
    """The app's data as a tar, written to `out` as it's read; links stay links"""
    with tarfile.open(fileobj=out, mode="w|") as tar:
        root = data_dir(app_id)
        if root.is_dir():
            tar.add(root, arcname="data", recursive=True)


# Settings

def _fernet() -> Fernet:
    path = sources.DATA / "secret.key"
    with _key_lock:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                file.write(Fernet.generate_key())
        return Fernet(path.read_bytes().strip())


def _settings_file(app_id: str) -> Path:
    return sources.folder(app_id) / "settings.json"


def _read(app_id: str) -> dict:
    try:
        found = json.loads(_settings_file(app_id).read_text())
    except (OSError, ValueError):
        found = {}
    return {"variables": found.get("variables", []), "labb": found.get("labb", {}), "database": found.get("database"),
            "api": found.get("api")}


def _write(app_id: str, stored: dict):
    path = _settings_file(app_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        json.dump(stored, file)
    os.replace(partial, path)


def _stored(app_id: str) -> list[dict]:
    return _read(app_id)["variables"]


def uses_labb(app_id: str) -> bool:
    return bool(_read(app_id)["labb"])


def set_labb(app_id: str, env: dict[str, str] | None):
    """labb's variables for the app (None: none any more), encrypted"""
    if env and set(env) - LABB_VARIABLES:
        raise HTTPException(400, f"labb only sets {', '.join(sorted(LABB_VARIABLES))}")
    stored = _read(app_id)
    stored["labb"] = {name: _fernet().encrypt(value.encode()).decode() for name, value in (env or {}).items()}
    _write(app_id, stored)


def settings(app_id: str) -> list[dict]:
    """The app's variables for its page and its agent: a secret's value is never given back"""
    return [{"name": v["name"], "secret": v["secret"], "value": None if v["secret"] else v["value"]} for v in _stored(app_id)]


def database_password(app_id: str) -> str | None:
    """The app's database role's password, if it has a database"""
    stored = _read(app_id)["database"]
    if not stored:
        return None
    try:
        return _fernet().decrypt(stored.encode()).decode()
    except InvalidToken:
        return None


def set_database_password(app_id: str, password: str | None):
    stored = _read(app_id)
    stored["database"] = _fernet().encrypt(password.encode()).decode() if password else None
    _write(app_id, stored)


def api_secrets(app_id: str) -> dict | None:
    """Its API's role password and token secret, if it has an API"""
    stored = _read(app_id)["api"]
    if not stored:
        return None
    try:
        return json.loads(_fernet().decrypt(stored.encode()))
    except (InvalidToken, ValueError):
        return None


def set_api_secrets(app_id: str, secrets_: dict | None):
    stored = _read(app_id)
    stored["api"] = _fernet().encrypt(json.dumps(secrets_).encode()).decode() if secrets_ else None
    _write(app_id, stored)


def environment(app_id: str) -> dict[str, str]:
    """The app's variables for its container, secrets decrypted; labb's after the user's"""
    env = {}
    stored = _read(app_id)
    for v in stored["variables"]:
        if v["secret"]:
            try:
                env[v["name"]] = _fernet().decrypt(v["value"].encode()).decode()
            except InvalidToken:
                continue
        else:
            env[v["name"]] = v["value"]
    for name, value in stored["labb"].items():
        try:
            env[name] = _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            continue
    if (password := database_password(app_id)) is not None:
        from labb_spawner import database
        env["DATABASE_URL"] = database.url(app_id, password)
    return env


def save_settings(app_id: str, variables: list[dict]) -> list[dict]:
    """Replace the app's variables. A secret sent without a value keeps the one it has."""
    if len(variables) > limits.get("app_variables"):
        raise HTTPException(400, f"An app can have {limits.get('app_variables')} variables at most")
    before = {v["name"]: v for v in _stored(app_id)}
    seen, kept = set(), []
    for v in variables:
        name, secret, value = v.get("name", "").strip(), bool(v.get("secret")), v.get("value")
        if not NAME.fullmatch(name):
            raise HTTPException(400, f"{name!r} can't be a variable's name: letters, digits and _, not starting with a digit")
        if name in RESERVED:
            raise HTTPException(400, f"{name} is labb's to set")
        if name in seen:
            raise HTTPException(400, f"{name} is there twice")
        seen.add(name)
        if value is None:
            # A secret left as it was
            old = before.get(name)
            if not (secret and old and old["secret"]):
                raise HTTPException(400, f"{name} needs a value")
            kept.append(old)
            continue
        if len(value) > MAX_VALUE:
            raise HTTPException(400, f"{name}'s value is over {MAX_VALUE:,} characters")
        stored = _fernet().encrypt(value.encode()).decode() if secret else value
        kept.append({"name": name, "secret": secret, "value": stored})
    _write(app_id, {**_read(app_id), "variables": kept})
    return settings(app_id)
