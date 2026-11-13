"""What labb's containers use, for the server's Settings → Health (`GET /system`, with the token).
Read-only: the spawner is the one part with the Docker socket, so it's the one that can see.

Two kinds of containers:
- labb's own services: the containers of the compose project the spawner is in (found from its own
  container's labels), each with its CPU, memory and how long it's been up;
- people's apps: their fronts, servers and APIs, added up per app, the busiest first.

CPU is worked out from two quick samples (`one_shot`) of every container at once, half a second
apart, so the whole answer takes about a second however many there are."""

import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from docker.errors import APIError, NotFound

SAMPLE_SECONDS = 0.5
PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"
# The most apps shown one by one; the rest are only in the totals
TOP_APPS = 8


def _sample(container) -> dict | None:
    try:
        return container.stats(stream=False, one_shot=True)
    except (APIError, NotFound):
        return None


def _usage(first: dict | None, second: dict | None) -> dict:
    """CPU (percent of one processor, so 200 is two) and memory from two samples"""

    if not second:
        return {}
    memory = second.get("memory_stats") or {}
    # What the kernel can take back (its file cache) isn't what the container needs, as `docker stats` counts it
    stats = memory.get("stats") or {}
    used = memory.get("usage")
    if used is not None:
        used = max(0, used - stats.get("inactive_file", stats.get("total_inactive_file", 0)))
    found = {"memory": used, "memory_limit": memory.get("limit")}
    cpu, before = second.get("cpu_stats") or {}, (first or {}).get("cpu_stats") or {}
    work = (cpu.get("cpu_usage") or {}).get("total_usage", 0) - (before.get("cpu_usage") or {}).get("total_usage", 0)
    system = cpu.get("system_cpu_usage", 0) - before.get("system_cpu_usage", 0)
    processors = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
    if first and system > 0 and work >= 0:
        found["cpu"] = round(work / system * processors * 100, 1)
    return found


def _since(container) -> str | None:
    started = ((container.attrs or {}).get("State") or {}).get("StartedAt") or ""
    return started if started and not started.startswith("0001") else None


def _measure(containers: list) -> list[dict]:
    """Each container's usage, all sampled together"""
    if not containers:
        return []
    with ThreadPoolExecutor(min(16, len(containers))) as pool:
        first = list(pool.map(_sample, containers))
        time.sleep(SAMPLE_SECONDS)
        second = list(pool.map(_sample, containers))
    return [_usage(a, b) for a, b in zip(first, second)]


def project(client) -> str | None:
    """The compose project the spawner runs in; None from a checkout"""
    try:
        return client.containers.get(socket.gethostname()).labels.get(PROJECT_LABEL)
    except (APIError, NotFound):
        return None


def report(client, app_label: str, server_label: str, api_label: str) -> dict:
    """{"services": [...], "apps": {"running", "memory", "cpu", "top": [...]}, "at"}"""

    name = project(client)
    services = client.containers.list(all=True, filters={"label": [f"{PROJECT_LABEL}={name}"]}) if name else []
    apps = [c for label in (app_label, server_label, api_label) for c in client.containers.list(filters={"label": [label]})]
    running = [c for c in services if c.status == "running"]
    measured = _measure(running + apps)
    used = {id(c): u for c, u in zip(running + apps, measured)}

    shown = []
    for container in sorted(services, key=lambda c: c.labels.get(SERVICE_LABEL) or c.name):
        usage = used.get(id(container), {})
        shown.append({"name": container.labels.get(SERVICE_LABEL) or container.name, "container": container.name,
                      "status": container.status, "since": _since(container),
                      "restarts": (container.attrs or {}).get("RestartCount", 0), **usage})

    # Per app: its front's name, with its server and API added in
    per_app: dict[str, dict] = {}
    for container in apps:
        labels = container.labels
        app_id = labels.get(f"{app_label}.id") or labels.get(server_label) or labels.get(api_label) or container.name
        entry = per_app.setdefault(app_id, {"id": app_id, "name": "", "owner": "", "memory": 0, "cpu": 0.0, "containers": 0})
        if labels.get(f"{app_label}.name"):
            entry["name"], entry["owner"] = labels[f"{app_label}.name"], labels.get(f"{app_label}.owner", "")
        usage = used.get(id(container), {})
        entry["memory"] += usage.get("memory") or 0
        entry["cpu"] = round(entry["cpu"] + (usage.get("cpu") or 0), 1)
        entry["containers"] += 1
    ranked = sorted(per_app.values(), key=lambda a: (a["memory"], a["cpu"]), reverse=True)
    return {
        "project": name,
        "services": shown,
        "apps": {"running": len(per_app), "memory": sum(a["memory"] for a in ranked),
                 "cpu": round(sum(a["cpu"] for a in ranked), 1), "top": ranked[:TOP_APPS]},
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
