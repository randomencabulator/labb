"""The server's MCP servers (Settings → MCP servers): their tools are offered in the web chat,
to the default assistant. Admins set them in the same format as the terminal's mcpServers:

    {"github": {"url": "https://api.githubcopilot.com/mcp/", "headers": {"Authorization": "Bearer …"}},
     "time": {"command": "uvx", "args": ["mcp-server-time"]}}

The settings are kept encrypted (they often hold tokens), and the servers are connected when the
server starts and again when an admin saves. Tools the MCP server marks read-only run right
away; the others ask in the chat (see `approve` in server/app.py), and where nobody can be asked
(scheduled tasks), they don't run.
"""

import json
import threading

from labb import mcp_client
from labb import model
from labb.chats import conn, lock
# Made the server_settings table this uses
from labb.server import providers  # noqa: F401
from labb.server.studio import secrets
from labb.tools import set_server_tools
from labb.server import limits

KEY = "mcp_servers"

_servers: mcp_client.McpServers | None = None
_lock = threading.Lock()


class McpError(ValueError):
    """Settings that can't be saved, said for the admin"""


def permission(name: str, args: dict, read_only: bool, configurable: dict) -> str | None:
    """None if the tool may run: read-only ones right away, others with a yes in the chat"""

    if read_only:
        return None
    approve = configurable.get("approve")
    if approve is None:
        return "Not run: this tool changes something and needs the user's approval, which isn't possible here."
    answer = approve(json.dumps(args, indent=2, ensure_ascii=False), [name], None, f"Use {name}?")
    if answer in ("yes", "always"):
        return None
    reason = answer[1] if isinstance(answer, tuple) and len(answer) > 1 and answer[1] else None
    said = f" They said: {reason.rstrip('.')}." if reason else ""
    return f"Not run: the user didn't allow this tool.{said} Don't call it again unless they ask."


def saved() -> dict:
    """The MCP servers' settings, decrypted ({} if none or unreadable)"""

    with lock:
        row = conn.execute("SELECT value FROM server_settings WHERE key = ?", (KEY,)).fetchone()
    if not row:
        return {}
    try:
        servers = json.loads(secrets.fernet().decrypt(row[0].encode()).decode())
    except Exception:
        return {}
    return servers if isinstance(servers, dict) else {}


def check(servers) -> dict:
    """The settings if they're usable; raises McpError saying what's wrong"""

    if not isinstance(servers, dict):
        raise McpError("The MCP servers should be a JSON object, by name")
    if len(servers) > (most := limits.get("mcp_servers")):
        raise McpError(f"At most {most} MCP servers")
    for name, config in servers.items():
        if not isinstance(config, dict):
            raise McpError(f"{name}: should be an object with a command or a url")
        try:
            mcp_client.connection(config)
        except ValueError as e:
            raise McpError(f"{name}: {e}")
    return servers


def save(servers) -> dict:
    """Save the settings and connect them; the status of each server"""

    check(servers)
    value = secrets.fernet().encrypt(json.dumps(servers).encode()).decode()
    with lock, conn:
        conn.execute("INSERT INTO server_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (KEY, value))
    return apply()


def apply() -> dict:
    """Connect the saved servers (closing the ones before) and offer their tools; their status"""

    global _servers
    with _lock:
        if _servers is not None:
            _servers.close()
            _servers = None
        servers = saved()
        if servers:
            _servers = mcp_client.McpServers(servers, permission)
        set_server_tools(_servers.tools if _servers else [])
        # The tools a model is bound to are cached
        model.model_with_tools.cache_clear()
        return status()


def status() -> dict:
    """Each server's number of tools, or why it couldn't connect, and the tools' names"""

    if _servers is None:
        return {"servers": {}, "tools": []}
    return {"servers": dict(_servers.status), "tools": [tool.name for tool in _servers.tools]}
