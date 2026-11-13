"""What labb's Settings → Services changes in the crawler (`GET|PUT /settings`, with the token).

Each setting's default is what its environment variable set when the crawler started (ALLOW_PRIVATE, CACHE_HOURS, FETCH_TIMEOUT, RENDER_TIMEOUT, SETTLE_SECONDS, MAX_BYTES); an
admin's change is kept in settings.json beside the token file (in memory without one) and put in place at once, on the module setting the code reads
(`apply`), so it holds for the next request without a restart. Saved again at each start."""

import importlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from fastapi import HTTPException


@dataclass(frozen=True)
class Setting:
    key: str
    group: str
    label: str
    description: str
    # "bool", "int" or "number"
    kind: str
    # The module setting it changes, and its value then as the page shows it, and back
    module: str
    name: str
    min: float = 0
    max: float = 0
    unit: str = ""
    shown: Callable = lambda v: v
    used: Callable = lambda v: v


SETTINGS = [
    Setting("allow_private", "Crawler", "Read private addresses",
            "Let the crawler read pages on private and local addresses: your intranet, NAS or router. "
            "Anyone who can chat could then have the model read them, including labb's own services.",
            "bool", "urls", "ALLOW_PRIVATE"),
    Setting("cache_hours", "Crawler", "Keep pages read for", "A page read again within this time comes from the cache.",
            "number", "store", "CACHE_HOURS", 0, 720, "hours"),
    Setting("fetch_timeout", "Crawler", "Wait for a page", "How long to wait for a site at each step of fetching a page.",
            "number", "fetch", "TIMEOUT", 1, 300, "seconds"),
    Setting("render_timeout", "Crawler", "Wait for a page in the browser",
            "How long to wait for a page that needs a browser (one built with JavaScript) to load.",
            "number", "fetch", "RENDER_TIMEOUT", 1, 300, "seconds"),
    Setting("settle_seconds", "Crawler", "Wait for a page to settle",
            "The most to wait, after it loads, for a JavaScript page to stop changing.", "number", "fetch", "SETTLE_SECONDS", 0, 120, "seconds"),
    Setting("max_page_mb", "Crawler", "Largest page", "Pages bigger than this aren't read.", "int", "fetch", "MAX_BYTES",
            1, 500, "MB", shown=lambda b: b // 1_000_000, used=lambda mb: int(mb) * 1_000_000),
]
BY_KEY = {setting.key: setting for setting in SETTINGS}

# Each setting's value from the environment, before anything saved changed it
_defaults: dict[str, object] = {}
_saved_here: dict[str, object] = {}
_lock = threading.Lock()


def _module(setting: Setting):
    return importlib.import_module(f"labb_web_search_crawler.{setting.module}")


def file() -> Path | None:
    token = os.getenv("CRAWLER_TOKEN_FILE", "")
    return Path(token).with_name("settings.json") if token else None


def default(key: str):
    if key not in _defaults:
        setting = BY_KEY[key]
        _defaults[key] = setting.shown(getattr(_module(setting), setting.name))
    return _defaults[key]


def saved() -> dict[str, object]:
    path = file()
    if path is None:
        return dict(_saved_here)
    try:
        values = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in values.items() if k in BY_KEY and _fits(BY_KEY[k], v)} if isinstance(values, dict) else {}


def _fits(setting: Setting, value) -> bool:
    if setting.kind == "bool":
        return isinstance(value, bool)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if setting.kind == "int" and not float(value).is_integer():
        return False
    return setting.min <= value <= setting.max


def apply():
    """Put every setting's value (saved, else its default) where the code reads it"""
    values = saved()
    for setting in SETTINGS:
        value = values.get(setting.key, default(setting.key))
        setattr(_module(setting), setting.name, setting.used(value))
    for hook in after_apply:
        hook()


# What else needs to know (a value held elsewhere, e.g. by an object made at start)
after_apply: list[Callable[[], None]] = []


def described() -> list[dict]:
    values = saved()
    return [{"key": s.key, "group": s.group, "label": s.label, "description": s.description, "kind": s.kind,
             "min": s.min, "max": s.max, "unit": s.unit, "default": default(s.key),
             "value": values.get(s.key, default(s.key))} for s in SETTINGS]


def save(values: dict) -> list[dict]:
    """Save changed settings (None goes back to the default); refused whole if one can't be"""

    global _saved_here
    with _lock:
        for key in SETTINGS:
            default(key.key)
        current = saved()
        for key, value in values.items():
            if key not in BY_KEY:
                raise HTTPException(400, f"There's no setting {key}")
            setting = BY_KEY[key]
            if value is None or value == default(key):
                current.pop(key, None)
            elif not _fits(setting, value):
                kind = "on or off" if setting.kind == "bool" else f"a number from {setting.min:g} to {setting.max:g}"
                raise HTTPException(400, f"{setting.group}: {setting.label} is {kind}")
            else:
                current[key] = value
        path = file()
        if path is None:
            _saved_here = current
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_name(path.name + ".partial")
            partial.write_text(json.dumps(current))
            os.replace(partial, path)
        apply()
    return described()
