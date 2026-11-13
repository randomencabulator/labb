"""Settings → Health's resources, for admins: what the server and its parts use right now.

- `host`: the machine (or the VM Docker runs in) as this container sees it, from /proc: its CPU
  in use (two samples of /proc/stat), load, memory and the disk the data is on.
- `server`: this process (memory, threads, CPU time, up since) and its container's memory limit
  (cgroup v2), when there is one.
- `database`: its size.
- `ollama`: the models Ollama has loaded (`/api/ps`), with the memory and video memory each holds.
- `containers`: from the spawner (`GET /system`, spawner/system.py), the only part with the Docker
  socket: each of labb's services and people's apps, with their CPU and memory.

Each part is read at once with a short timeout, and one that can't be says why in `problems`
instead of holding up the rest. Off Linux (a checkout on a Mac) the /proc parts are left out."""

import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from labb import db, llm, usage
from labb.agent import CHAT_DB, DATA_DIR

TIMEOUT = 4
SAMPLE_SECONDS = 0.5
STARTED = time.time()
PROC = Path("/proc")
CGROUP = Path("/sys/fs/cgroup")


def _cpu_times() -> tuple[int, int] | None:
    """(busy, total) jiffies of all processors, from /proc/stat"""
    try:
        fields = [int(v) for v in (PROC / "stat").read_text().split("\n", 1)[0].split()[1:]]
    except (OSError, ValueError):
        return None
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    return sum(fields) - idle, sum(fields)


def _meminfo() -> dict[str, int]:
    found = {}
    try:
        for line in (PROC / "meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            found[key] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return found


def host() -> dict:
    before = _cpu_times()
    time.sleep(SAMPLE_SECONDS)
    after = _cpu_times()
    found: dict = {"processors": os.cpu_count()}
    if before and after and after[1] > before[1]:
        found["cpu"] = round((after[0] - before[0]) / (after[1] - before[1]) * 100, 1)
    try:
        found["load"] = [float(v) for v in (PROC / "loadavg").read_text().split()[:3]]
    except (OSError, ValueError):
        pass
    memory = _meminfo()
    if "MemTotal" in memory:
        found["memory_total"] = memory["MemTotal"]
        found["memory_used"] = memory["MemTotal"] - memory.get("MemAvailable", memory.get("MemFree", 0))
    if memory.get("SwapTotal"):
        found["swap_total"], found["swap_used"] = memory["SwapTotal"], memory["SwapTotal"] - memory.get("SwapFree", 0)
    try:
        found["up_seconds"] = int(float((PROC / "uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    try:
        disk = shutil.disk_usage(Path(CHAT_DB).parent if not db.POSTGRES else DATA_DIR)
        found["disk_total"], found["disk_used"] = disk.total, disk.used
    except OSError:
        pass
    return found


def _cgroup(name: str) -> int | None:
    try:
        value = (CGROUP / name).read_text().strip()
    except OSError:
        return None
    return int(value) if value.isdigit() else None


def server() -> dict:
    found: dict = {"pid": os.getpid(), "up_seconds": int(time.time() - STARTED), "threads": threading.active_count()}
    try:
        for line in (PROC / "self" / "status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                found["memory"] = int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    times = os.times()
    found["cpu_seconds"] = round(times.user + times.system, 1)
    # Its container's, with Docker: what all of its processes use, and the most it may
    if (used := _cgroup("memory.current")) is not None:
        found["container_memory"] = used
        if (most := _cgroup("memory.max")) is not None:
            found["container_memory_limit"] = most
    return found


def database() -> dict:
    stored = usage.storage()
    return {"kind": "Postgres" if db.POSTGRES else "SQLite", "bytes": stored["bytes"], "chats": stored["chats"]}


def ollama() -> dict:
    host_url = llm.ollama_host()
    response = httpx.get(f"{host_url}/api/ps", timeout=TIMEOUT)
    response.raise_for_status()
    models = [{"name": m.get("name") or m.get("model"), "memory": m.get("size"), "gpu_memory": m.get("size_vram"),
               "until": m.get("expires_at"), "context": m.get("context_length")}
              for m in response.json().get("models", [])]
    return {"url": host_url, "models": models}


def containers() -> dict | None:
    from labb.server import apps
    if not apps.SPAWNER_URL:
        return None
    headers = {"Authorization": f"Bearer {key}"} if (key := apps.token()) else {}
    response = httpx.get(f"{apps.SPAWNER_URL}/system", headers=headers, timeout=TIMEOUT + 6)
    response.raise_for_status()
    return response.json()


PARTS = {"host": host, "server": server, "database": database, "ollama": ollama, "containers": containers}
LABELS = {"host": "the machine", "server": "the server", "database": "the database", "ollama": "Ollama",
          "containers": "the spawner (the containers' usage)"}


def report() -> dict:
    """Every part read at once; those that couldn't be say why in `problems`"""

    def read(item):
        key, probe = item
        try:
            return key, probe(), None
        except Exception as e:
            return key, None, f"Couldn't read {LABELS[key]}: {e}"

    found: dict = {"problems": []}
    with ThreadPoolExecutor(len(PARTS)) as pool:
        for key, value, problem in pool.map(read, PARTS.items()):
            found[key] = value
            if problem:
                found["problems"].append(problem)
    return found
