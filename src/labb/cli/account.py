"""labb's account on the server and its updates: --login, --logout, --update, and the
server's status and version that labb checks when it starts."""

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import time

import httpx

from labb import remote
from labb.agent import DATA_DIR, SOURCE_DIR
from labb.cli.render import console


ENV_FILE = DATA_DIR / ".env"


def set_env(name: str, value: str | None):
    """Save (or with None, remove) a setting in labb's .env, keeping the other lines"""

    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    lines = [line for line in lines if not line.startswith(f"{name}=")]
    if value is not None:
        lines.append(f"{name}={value}")
        os.environ[name] = value
    else:
        os.environ.pop(name, None)
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text("\n".join(lines) + "\n")
    ENV_FILE.chmod(0o600)


def login() -> int:
    """labb --login: approve this PC in the browser; the server makes an API key for it"""

    server = remote.server()
    if not server:
        console.print("[red]No server to log in to.[/] Set LABB_SERVER in ~/.labb/.env, or install with the server's install.sh.")
        return 1
    try:
        response = httpx.post(f"{server}/auth/device", json={"machine": socket.gethostname()}, timeout=10)
        response.raise_for_status()
        start = response.json()
    except (httpx.HTTPError, ValueError) as e:
        console.print(f"[red]Couldn't reach {server}:[/] {e}")
        return 1

    console.print(f"Open [bold]{start['verification_url']}[/] and check that it shows the code [bold]{start['user_code']}[/].")
    console.print("[dim]Opening it in your browser… (over SSH, open the link on any device)[/]")
    try:
        import webbrowser
        webbrowser.open(start["verification_url"])
    except Exception:
        pass

    deadline = time.monotonic() + start["expires_in"]
    try:
        with console.status("Waiting for you to approve this PC…"):
            while time.monotonic() < deadline:
                time.sleep(start["interval"])
                try:
                    result = httpx.post(f"{server}/auth/device/token", json={"device_code": start["device_code"]}, timeout=10).json()
                except (httpx.HTTPError, ValueError):
                    continue
                if result["status"] != "pending":
                    break
            else:
                result = {"status": "expired"}
    except KeyboardInterrupt:
        console.print("\n[dim]Login cancelled.[/]")
        return 1

    if result["status"] == "approved":
        set_env("LABB_API_KEY", result["api_key"])
        console.print(f"Logged in as [bold]{result['username']}[/]. labb sends its requests to {server} with a key saved in {ENV_FILE}.")
        return 0
    console.print("[red]The login was denied.[/]" if result["status"] == "denied" else "[red]The login expired; run labb --login again.[/]")
    return 1


def logout() -> int:
    """labb --logout: revoke this PC's key on the server and forget it"""

    key, server = remote.api_key(), remote.server()
    if not key:
        console.print("Not logged in.")
        return 0
    if server:
        try:
            httpx.post(f"{server}/auth/revoke", headers=remote.headers(), timeout=10)
        except httpx.HTTPError:
            console.print(f"[yellow]Couldn't reach {server} to revoke the key; revoke it in the web page (API keys).[/]")
    set_env("LABB_API_KEY", None)
    console.print("Logged out: this PC's key is revoked and removed.")
    return 0


def server_status(timeout: float) -> dict | None:
    """Who the server says we are ({"user": … or null}), or None without a server"""

    server = remote.server()
    if not server:
        return None
    try:
        response = httpx.get(f"{server}/auth/status", headers=remote.headers(), timeout=timeout)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        return None


def server_version(timeout: float) -> dict | None:
    """The version the server hands out ({"version", "wheel"}), or None without a server"""

    server = remote.server()
    if not server:
        return None
    try:
        response = httpx.get(f"{server}/version", timeout=timeout)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        return None


def update() -> int:
    """labb --update: install the server's version, if it's different"""

    server = remote.server()
    if not server:
        console.print("[red]No server to update from.[/] Set LABB_SERVER in ~/.labb/.env, or install with the server's install.sh.")
        return 1
    if DATA_DIR == SOURCE_DIR:
        console.print(f"This labb runs from a source checkout ({SOURCE_DIR}); update it with git instead.")
        return 1
    info = server_version(timeout=10)
    if info is None:
        console.print(f"[red]Couldn't reach {server}.[/]")
        return 1
    if info["version"] == remote.version():
        console.print(f"labb {remote.version()} is up to date.")
        return 0
    if not info.get("wheel"):
        console.print(f"[red]The server has version {info['version']}, but no wheel to install it from.[/]")
        return 1
    uv = shutil.which("uv")
    if uv is None:
        console.print("[red]uv isn't on your PATH.[/] Run the server's install.sh again.")
        return 1
    console.print(f"Updating labb {remote.version()} → {info['version']}…")
    wheel_url = f"{server.rstrip('/')}/wheels/{info['wheel']}"
    # --refresh-package: the server's wheel, not one uv cached from the same address before
    command = [uv, "tool", "install", "--force", "--refresh-package", remote.PACKAGE, "--python", "3.14", *_with_browser(), wheel_url]
    return subprocess.run(command).returncode


def _with_browser() -> list[str]:
    """What an update keeps: Playwright, if labb --install-browser added it"""
    return ["--with", "playwright"] if importlib.util.find_spec("playwright") else []


def install_browser() -> int:
    """labb --install-browser: Playwright and Chromium on this PC, so the browser runs here"""

    if not importlib.util.find_spec("playwright"):
        if DATA_DIR == SOURCE_DIR:
            console.print("This labb runs from a source checkout: run [bold]uv sync --extra browser[/], then this again.")
            return 1
        server, uv = remote.server(), shutil.which("uv")
        info = server_version(timeout=10) if server else None
        if not uv or not info or not info.get("wheel"):
            console.print("[red]Playwright can only be added with uv and the server labb came from.[/] Run the server's install.sh again, or check LABB_SERVER.")
            return 1
        wheel_url = f"{server.rstrip('/')}/wheels/{info['wheel']}"
        command = [uv, "tool", "install", "--force", "--refresh-package", remote.PACKAGE, "--python", "3.14", "--with", "playwright", wheel_url]
        if code := subprocess.run(command).returncode:
            return code
        # This process doesn't have it yet: the one uv just installed does
        return subprocess.run([shutil.which("labb") or "labb", "--install-browser"]).returncode
    console.print("Downloading Chromium…")
    code = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"]).returncode
    if not code:
        console.print("The browser is ready: [bold]labb --browser[/], or [bold]/browser on[/] in a conversation.")
    return code
