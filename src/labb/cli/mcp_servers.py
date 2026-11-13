"""The terminal's MCP servers: those in labb's settings (see settings.py and labb/mcp_client.py).

    "mcpServers": {
      "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
      "docs": {"url": "https://example.com/mcp"}
    }

Their tools ask before they run, like commands that change something: "always" remembers the
tool's name, and `permissions.allow` in the settings can allow them by name or with wildcards
(mcp__github__*). In plan mode, only tools the server marks read-only run.
"""

import json

from labb import mcp_client, permissions


class McpServers(mcp_client.McpServers):
    """The settings' MCP servers, whose tools ask in the terminal"""

    def __init__(self, servers: dict, workdir: str):
        self.workdir = workdir
        super().__init__(servers, lambda name, args, read_only, configurable:
                         ask_permission(name, args, read_only, workdir, configurable))


def ask_permission(name: str, args: dict, read_only: bool, workdir: str, configurable: dict) -> str | None:
    """None if the tool may run, otherwise why not (for the model)"""

    if configurable.get("mode") == "plan" and not read_only:
        return "Not run: you're in plan mode, and this tool isn't marked read-only. Finish your plan first."
    if configurable.get("allow_all") or permissions.tool_allowed(name, permissions.allowed(workdir)):
        return None
    approve = configurable.get("approve")
    if approve is None:
        return "Not run: this tool needs the user's approval, which isn't possible here."
    answer = approve(f"{name}\n{json.dumps(args, indent=2, ensure_ascii=False)}", [name], None, "Use this tool?")
    if answer == "always":
        permissions.allow(workdir, [name])
    if answer in ("yes", "always"):
        return None
    reason = answer[1] if isinstance(answer, tuple) and len(answer) > 1 and answer[1] else None
    said = f" They said: {reason.rstrip('.')}." if reason else ""
    return f"Not run: the user didn't allow this tool.{said} Don't call it again unless they ask."
