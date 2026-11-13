"""Python steps run in the sandbox service (sandbox/, its own container in Docker): no network,
a read-only system, a fresh folder and its own user for each run, and limits on time, memory
and processes. The server sends the code and the run's outputs so far, and gets `result` back.
Without SANDBOX_URL there's no sandbox, and Python steps say so instead of running."""

import os
from pathlib import Path

import httpx

SANDBOX_URL = os.getenv("SANDBOX_URL", "").rstrip("/")
SANDBOX_TOKEN = os.getenv("SANDBOX_TOKEN", "")
# Or the file the sandbox writes its token to on its first start (a volume both share in Docker)
SANDBOX_TOKEN_FILE = os.getenv("SANDBOX_TOKEN_FILE", "")


class SandboxError(RuntimeError):
    """The code failed, or the sandbox couldn't run it; said for whoever wrote the step"""


def token() -> str:
    """Read on every run: the sandbox may have made its file after the server started"""

    if SANDBOX_TOKEN or not SANDBOX_TOKEN_FILE:
        return SANDBOX_TOKEN
    try:
        return Path(SANDBOX_TOKEN_FILE).read_text().strip()
    except OSError:
        return ""


def available() -> bool:
    return bool(SANDBOX_URL)


def run(code: str, inputs: dict, timeout: int = 30) -> dict:
    """Run `code` with `inputs`; {"result", "stdout"}. Raises SandboxError with the traceback."""

    if not SANDBOX_URL:
        raise SandboxError("This server has no sandbox for Python steps (SANDBOX_URL); docker compose runs one")
    from labb.server import limits
    timeout = max(1, min(int(timeout), limits.get("python_seconds")))
    headers = {"Authorization": f"Bearer {key}"} if (key := token()) else {}
    try:
        response = httpx.post(f"{SANDBOX_URL}/run", json={"code": code, "inputs": inputs, "timeout": timeout},
                              headers=headers, timeout=timeout + 15)
    except httpx.HTTPError as e:
        raise SandboxError(f"Couldn't reach the sandbox at {SANDBOX_URL}: {e}") from e
    if response.status_code != 200:
        raise SandboxError(f"The sandbox answered {response.status_code}: {response.text[:300]}")
    body = response.json()
    if not body.get("ok"):
        raise SandboxError(body.get("error") or "The code failed")
    return {"result": body.get("result"), "stdout": body.get("stdout", "")}
