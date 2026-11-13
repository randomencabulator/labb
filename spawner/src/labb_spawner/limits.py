"""The spawner's limits, which labb's Settings → Limits shows and changes (`GET|PUT /limits`, with
the token): how many apps and artifacts people may have and how big they may be, and what each
app's containers and builds get. Each one's default is its environment variable's (compose.yaml), or
the built-in value without one; what an admin saves is kept in SPAWNER_DATA/limits.json and read
when it's used, so it applies to the next app, build or start (a running app keeps what it started
with until it starts again)."""

import json
import os
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from fastapi import HTTPException


@dataclass(frozen=True)
class Limit:
    key: str
    group: str
    label: str
    description: str
    # The module and name of the setting it's the default of, read when it's needed (the tests change them)
    source: tuple[str, str]
    min: int
    max: int
    unit: str = ""
    # From that setting to the limit's number, and back
    read: Callable = int
    write: Callable = int


def _megabytes(docker_size: str) -> int:
    """Docker's "256m", "1g", "512k" or bytes as MB"""
    found = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([bkmg]?)b?\s*", str(docker_size).lower())
    if not found:
        return 256
    number, unit = float(found[1]), found[2]
    return max(1, round(number * {"": 1 / 2**20, "b": 1 / 2**20, "k": 1 / 1024, "m": 1, "g": 1024}[unit]))


MB = 1024 * 1024

LIMITS = [
    Limit("app_count", "Apps", "Apps per person", "How many apps each person may have.", ("app", "MAX_APPS"), 1, 1000),
    Limit("app_total", "Apps", "Apps in all", "How many apps there may be, for everyone; 0 for as many as there are ports.",
          ("app", "MAX_TOTAL"), 0, 100_000),
    Limit("app_memory_mb", "Apps", "Memory per app", "How much memory an app's server may use; for apps started after.",
          ("app", "MEMORY"), 16, 1_000_000, "MB", _megabytes, lambda mb: f"{mb}m"),
    Limit("app_cpu_percent", "Apps", "Processor per app",
          "How much of one processor each of an app's containers may use; 100 is a whole one. For apps started after.",
          ("app", "CPUS"), 5, 6400, "%", lambda cpus: round(cpus * 100), lambda percent: percent / 100),
    Limit("app_processes", "Apps", "Processes per app", "How many processes (and threads) an app's container may have.",
          ("app", "PIDS"), 16, 100_000),
    Limit("app_data_mb", "Apps", "Data per app", "How much an app may keep in /data; one over it is stopped.",
          ("appdata", "DATA_LIMIT"), 1, 10_000_000, "MB", lambda b: b // MB, lambda mb: mb * MB),
    Limit("app_database_mb", "Apps", "Database per app", "How big an app's database may get; one over it is stopped.",
          ("database", "SIZE_LIMIT"), 1, 10_000_000, "MB", lambda b: b // MB, lambda mb: mb * MB),
    Limit("app_database_connections", "Apps", "Database connections per app",
          "How many connections an app may have to its database at once; for databases turned on after.",
          ("database", "CONNECTIONS"), 1, 1000),
    Limit("app_variables", "Apps", "Environment variables per app", "How many variables an app's settings may have.",
          ("appdata", "MAX_VARIABLES"), 1, 1000),
    Limit("app_files", "Apps", "Files per app", "How many files an app's source may have.", ("sources", "MAX_FILES"), 1, 100_000),
    Limit("app_file_mb", "Apps", "File size", "The largest one of an app's files may be.",
          ("sources", "MAX_FILE"), 1, 1000, "MB", lambda b: b // 1_000_000, lambda mb: mb * 1_000_000),
    Limit("app_source_mb", "Apps", "Source size", "How big an app's files may be in all.",
          ("sources", "MAX_SOURCE"), 1, 100_000, "MB", lambda b: b // 1_000_000, lambda mb: mb * 1_000_000),
    Limit("app_idle_minutes", "Apps", "Stop apps unused for",
          "An app nobody used for this long is stopped; opening it starts it again. 0 for never.",
          ("app", "IDLE_MINUTES"), 0, 1_000_000, "minutes", int, float),
    Limit("app_build_seconds", "Apps", "Time per build", "How long building an app may take before it's stopped.",
          ("app", "BUILD_SECONDS"), 30, 86_400, "seconds"),
    Limit("app_build_memory_mb", "Apps", "Memory per build", "How much memory building an app (or its page) may use.",
          ("app", "BUILD_MEMORY"), 128, 1_000_000, "MB", lambda b: b // MB, lambda mb: mb * MB),
    Limit("builds_at_once", "Apps", "Builds at once", "How many apps and artifacts may build at the same time, for everyone.",
          ("app", "BUILDS_AT_ONCE"), 1, 64),
    Limit("artifact_count", "Artifacts", "Artifacts per person", "How many web pages built in chats each person may have.",
          ("artifacts", "MAX_PER_OWNER"), 1, 100_000),
    Limit("artifact_files", "Artifacts", "Files per artifact", "How many files one artifact may have.",
          ("artifacts", "MAX_FILES"), 1, 100_000),
    Limit("artifact_file_mb", "Artifacts", "File size", "The largest one of an artifact's files may be.",
          ("artifacts", "MAX_FILE"), 1, 1000, "MB", lambda b: b // 1_000_000, lambda mb: mb * 1_000_000),
    Limit("artifact_source_mb", "Artifacts", "Artifact size", "How big an artifact's files may be in all.",
          ("artifacts", "MAX_SOURCE"), 1, 100_000, "MB", lambda b: b // 1_000_000, lambda mb: mb * 1_000_000),
    Limit("artifact_build_seconds", "Artifacts", "Time per build", "How long building an artifact may take before it's stopped.",
          ("app", "ARTIFACT_BUILD_SECONDS"), 10, 86_400, "seconds"),
    Limit("artifact_build_memory_mb", "Artifacts", "Memory per build", "How much memory building an artifact may use.",
          ("app", "ARTIFACT_MEMORY"), 128, 1_000_000, "MB", lambda b: b // MB, lambda mb: mb * MB),
]
BY_KEY = {limit.key: limit for limit in LIMITS}

_lock = threading.Lock()


def _file() -> Path:
    from labb_spawner import sources
    return sources.DATA / "limits.json"


def saved() -> dict[str, int]:
    """What admins changed"""
    try:
        values = json.loads(_file().read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(values, dict):
        return {}
    return {key: value for key, value in values.items() if key in BY_KEY and isinstance(value, int)
            and not isinstance(value, bool) and BY_KEY[key].min <= value <= BY_KEY[key].max}


def default(key: str) -> int:
    """Its setting's value (the environment's, or built in), as the limit's number"""
    import importlib
    limit = BY_KEY[key]
    module, name = limit.source
    return limit.read(getattr(importlib.import_module(f"labb_spawner.{module}"), name))


def number(key: str) -> int:
    """The limit as the page shows it: what an admin saved, or its default"""
    return saved().get(key, default(key))


def get(key: str):
    """The limit as the code uses it (bytes, "256m", 0.5 processors)"""
    limit = BY_KEY[key]
    found = saved()
    if key in found:
        return limit.write(found[key])
    module, name = limit.source
    import importlib
    return getattr(importlib.import_module(f"labb_spawner.{module}"), name)


def described() -> list[dict]:
    values = saved()
    shown = []
    for limit in LIMITS:
        first = default(limit.key)
        fields = {k: v for k, v in asdict(limit).items() if k not in ("source", "read", "write")}
        shown.append({**fields, "default": first, "value": values.get(limit.key, first)})
    return shown


def save(values: dict) -> list[dict]:
    """Save changed limits (None goes back to the default); refused whole if one can't be"""

    with _lock:
        current = saved()
        for key, value in values.items():
            if key not in BY_KEY:
                raise HTTPException(400, f"There's no limit {key}")
            limit = BY_KEY[key]
            if value is None or value == default(key):
                current.pop(key, None)
                continue
            if not isinstance(value, int) or isinstance(value, bool) or not limit.min <= value <= limit.max:
                raise HTTPException(400, f"{limit.group}: {limit.label} is a whole number from {limit.min} to {limit.max}")
            current[key] = value
        path = _file()
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".partial")
        partial.write_text(json.dumps(current))
        os.replace(partial, path)
    return described()
