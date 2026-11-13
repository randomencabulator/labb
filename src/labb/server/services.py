"""Settings → Services, for admins: how labb's services behave, beside Settings → Limits' how far
they go. On/off switches and numbers, each with its default (what its environment variable set).

- The server's own (`SETTINGS`): kept in `server_settings` and put where the code reads them when
  saved and when the server starts (`apply`).
- The crawler's, the browser's and the sandbox's: each service keeps its own (its settings.py,
  `GET|PUT /settings` with its token), and this page shows and saves them through the server
  when the service is set up and answers; `problems` says which didn't.

A change applies at once, to the next page read, browser action or Python step."""

import json
import threading
from dataclasses import asdict, dataclass

import httpx

from labb.chats import conn, lock

KEY = "services"


@dataclass(frozen=True)
class Setting:
    key: str
    group: str
    label: str
    description: str
    kind: str
    default: bool | float
    min: float = 0
    max: float = 0
    unit: str = ""


SETTINGS = [
    Setting("read_private", "Server", "Read private addresses without the crawler",
            "Where there's no crawler, the server reads pages itself: this lets it read private and local addresses "
            "too (an intranet, a NAS). Anyone who can chat could then have the model read them.", "bool", False),
]
BY_KEY = {s.key: s for s in SETTINGS}
_saved: dict | None = None
_lock = threading.Lock()


class SettingError(ValueError):
    """A value that can't be saved, said for the admin"""


def _env_defaults():
    from labb.tools import web
    return {"read_private": web.READ_PRIVATE_DEFAULT}


def saved() -> dict:
    global _saved
    with _lock:
        if _saved is None:
            # Made the server_settings table (imported here: it imports a lot)
            from labb.server import providers  # noqa: F401
            with lock:
                row = conn.execute("SELECT value FROM server_settings WHERE key = ?", (KEY,)).fetchone()
            try:
                values = json.loads(row[0]) if row else {}
            except ValueError:
                values = {}
            _saved = {k: v for k, v in values.items() if k in BY_KEY and _fits(BY_KEY[k], v)}
        return dict(_saved)


def _fits(setting: Setting, value) -> bool:
    if setting.kind == "bool":
        return isinstance(value, bool)
    return not isinstance(value, bool) and isinstance(value, (int, float)) and setting.min <= value <= setting.max


def default(key: str):
    return _env_defaults().get(key, BY_KEY[key].default)


def get(key: str):
    return saved().get(key, default(key))


def apply():
    """Put the server's settings where the code reads them"""
    from labb.tools import web
    web.READ_PRIVATE = get("read_private")


# The services with settings of their own: (what the page calls it, its URL, its headers)
def _services() -> dict:
    from labb.server.studio import sandbox
    from labb.tools import browser, web
    crawler_key, sandbox_key = web.crawler_token(), sandbox.token()
    return {
        "crawler": ("the crawler", web.CRAWLER_URL or "", {"Authorization": f"Bearer {crawler_key}"} if crawler_key else {}),
        "browser": ("the browser", browser.BROWSER_URL or "", browser.service_headers()),
        "sandbox": ("the sandbox", sandbox.SANDBOX_URL, {"Authorization": f"Bearer {sandbox_key}"} if sandbox_key else {}),
    }


SERVICES = ("crawler", "browser", "sandbox")


def _call(name: str, method: str, values: dict | None = None) -> list[dict]:
    label, url, headers = _services()[name]
    try:
        response = httpx.request(method, f"{url}/settings", headers=headers, json=values, timeout=10)
    except httpx.HTTPError as e:
        raise SettingError(f"Couldn't reach {label}: {e}")
    if response.status_code == 404:
        raise SettingError(f"{label.capitalize()} is too old to change its settings from here: update it")
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise SettingError(str(detail)[:300] if response.status_code == 400 else f"{label} refused it: {str(detail)[:300]}")
    found = response.json()
    if not isinstance(found, list):
        raise SettingError(f"{label} answered with something that isn't its settings")
    return found


def _remote(problems: list[str] | None = None) -> list[tuple[str, list[dict]]]:
    """Each service's settings that answers, its keys named with the service's (crawler.allow_private:
    the crawler and the browser both have one)"""
    found = []
    for name in SERVICES:
        _, url, _ = _services()[name]
        if not url:
            continue
        try:
            found.append((name, [{**s, "key": f"{name}.{s['key']}"} for s in _call(name, "GET")]))
        except SettingError as e:
            if problems is not None:
                problems.append(f"{e}, so its settings aren't here")
    return found


def described(problems: list[str] | None = None) -> list[dict]:
    values = saved()
    shown = [{**asdict(s), "default": default(s.key), "value": values.get(s.key, default(s.key))} for s in SETTINGS]
    for _, settings in _remote(problems):
        shown += settings
    return shown


def save(values: dict) -> list[dict]:
    """Save changed settings (None goes back to the default), the services' too; nothing is
    saved if one can't be"""

    global _saved
    current = saved()
    unreachable: list[str] = []
    known = {s["key"]: name for name, found in _remote(unreachable) for s in found}
    remote = {name: {} for name in SERVICES}
    for key, value in values.items():
        if key in BY_KEY:
            setting = BY_KEY[key]
            if value is None or value == default(key):
                current.pop(key, None)
            elif not _fits(setting, value):
                kind = "on or off" if setting.kind == "bool" else f"a number from {setting.min:g} to {setting.max:g}"
                raise SettingError(f"{setting.group}: {setting.label} is {kind}")
            else:
                current[key] = value
        elif key in known:
            remote[known[key]][key.split(".", 1)[1]] = value
        else:
            raise SettingError(unreachable[0] if unreachable else f"There's no setting {key}")
    for name, changed in remote.items():
        if changed:
            _call(name, "PUT", changed)
    with lock, conn:
        conn.execute("INSERT INTO server_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (KEY, json.dumps(current)))
    with _lock:
        _saved = current
    apply()
    return described()
