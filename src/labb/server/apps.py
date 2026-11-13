"""People's apps: each a container of its own, started by labb-spawner (spawner/, its own service in
Docker, the only one with the Docker socket), on a port of its own of the machine labb runs on
(APP_PORT_FIRST–APP_PORT_LAST): `http://labb.lan:9100`. The container is published on that port
itself, so what it serves goes straight to the browser, not through this server; with https,
Caddy has those ports for TLS and passes them straight to the containers.

The server only asks the spawner for people's apps (as their owner), says where to open one, and
checks it answers first. Without SPAWNER_URL there are no apps, and the page says so."""

import asyncio
import os
import re
import time
from pathlib import Path

import httpx
from fastapi import HTTPException, Request

SPAWNER_URL = os.getenv("SPAWNER_URL", "").rstrip("/")
SPAWNER_TOKEN = os.getenv("SPAWNER_TOKEN", "")
# Or the file the spawner writes its token to on its first start (a volume both share in Docker)
SPAWNER_TOKEN_FILE = os.getenv("SPAWNER_TOKEN_FILE", "")

# The spawner's ids: 12 hex characters
APP_ID = re.compile(r"[0-9a-f]{12}")

# Starting one can mean downloading its image first
client = httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10))


def token() -> str:
    """Read on every call: the spawner may have made its file after the server started"""

    if SPAWNER_TOKEN or not SPAWNER_TOKEN_FILE:
        return SPAWNER_TOKEN
    try:
        return Path(SPAWNER_TOKEN_FILE).read_text().strip()
    except OSError:
        return ""


async def forward(method: str, path: str, **kwargs) -> httpx.Response:
    """A call to the spawner; its refusals as HTTPExceptions, said for the user"""

    if not SPAWNER_URL:
        raise HTTPException(503, "This server can't start apps (SPAWNER_URL isn't set); docker compose runs the spawner")
    headers = {"Authorization": f"Bearer {key}"} if (key := token()) else {}
    try:
        response = await client.request(method, f"{SPAWNER_URL}{path}", headers=headers, **kwargs)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Couldn't reach the spawner: {e}")
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        # Its 401 is the server's token, not the user's: not something they can sign in again for
        raise HTTPException(502 if response.status_code == 401 else response.status_code, str(detail)[:500])
    return response


async def download(path: str, **params):
    """A streamed answer of the spawner's (an app's data, as a tar), with what closes it when it's sent"""
    if not SPAWNER_URL:
        raise HTTPException(503, "This server can't start apps (SPAWNER_URL isn't set)")
    headers = {"Authorization": f"Bearer {key}"} if (key := token()) else {}
    request = client.build_request("GET", f"{SPAWNER_URL}{path}", params=params, headers=headers)
    try:
        response = await client.send(request, stream=True)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Couldn't reach the spawner: {e}")
    if response.status_code >= 400:
        await response.aread()
        await response.aclose()
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise HTTPException(502 if response.status_code == 401 else response.status_code, str(detail)[:500])
    return response


def checked(app_id: str) -> str:
    """The spawner's ids are hex; anything else can't be one, and isn't put in its URL"""
    if not APP_ID.fullmatch(app_id):
        raise HTTPException(404, "No such app")
    return app_id


def split_host(host: str) -> tuple[str, str]:
    """A Host header as (hostname, ":port" or "")"""
    host = host.strip().lower()
    if host.startswith("["):
        name, _, rest = host[1:].partition("]")
        return name, rest
    name, colon, port = host.partition(":")
    return name, f":{port}" if colon else ""


def app_origin(request: Request, port: int) -> str:
    """Where an app is, for the page labb was opened at: the same scheme and host, on the app's port"""
    hostname, _ = split_host(request.headers.get("host", ""))
    host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{request.url.scheme}://{host}:{port}"


async def answers(upstream: str, seconds: float = 15) -> bool:
    """Wait until the app answers there (a new one takes a moment to start)"""
    if not upstream:
        return False
    until = time.monotonic() + seconds
    async with httpx.AsyncClient(timeout=2) as probe:
        while True:
            try:
                await probe.get(upstream)
                return True
            except httpx.HTTPError:
                if time.monotonic() > until:
                    return False
                await asyncio.sleep(0.3)
