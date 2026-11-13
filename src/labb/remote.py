"""How labb reaches the server: its address, and the API key
that `labb --login` saved, which goes with every request to it, model calls and page
reads included."""

import functools
import importlib.metadata
import os
import socket
import tomllib
from pathlib import Path

PACKAGE = "labb"
AUTH_HEADER = "Authorization"
# Which labb and machine a request comes from, for the server's stats (User-Agent can't
# be used: the Ollama client replaces it with its own)
CLIENT_HEADER = "X-Labb-Client"


def server() -> str | None:
    """LABB_SERVER, without a trailing slash; None when labb works on its own"""
    url = os.getenv("LABB_SERVER", "").strip().rstrip("/")
    return url or None


def api_key() -> str | None:
    return os.getenv("LABB_API_KEY", "").strip() or None


@functools.cache
def version() -> str:
    """labb's version: the installed package's, or a source checkout's pyproject.toml"""
    try:
        return importlib.metadata.version(PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        return tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


@functools.cache
def client() -> str:
    """ "0.1.3 (laptop)": labb's version and this machine's name"""
    return f"{version()} ({socket.gethostname()})"


def headers() -> dict[str, str]:
    """The key as a header, if there is one, and which labb is asking"""
    key = api_key()
    return {CLIENT_HEADER: client(), **({AUTH_HEADER: f"Bearer {key}"} if key else {})}
