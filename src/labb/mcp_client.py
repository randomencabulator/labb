"""Tools from MCP servers, with langchain-mcp-adapters: the terminal's (its settings' mcpServers,
see cli/mcp_servers.py) and the server's (Settings → MCP servers, see server/mcp.py).

    "mcpServers": {
      "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
      "docs": {"url": "https://example.com/mcp"}
    }

A server with `command` runs as a process (stdio; `args`, `env` and `cwd` are optional);
one with `url` is reached over streamable HTTP (or set `transport` to "sse"). Each
server's session stays open while labb runs, on a background event loop, since the
graph runs the tools synchronously. What servers write to stderr goes to
~/.labb/logs/mcp-<server>.log, not the terminal.

The tools are named mcp__<server>__<tool>. Before one runs, the `permission`
function the servers were connected with decides: the terminal asks like for a command, the
web chat runs read-only tools right away and asks in the chat for the others.
"""

import asyncio
import re
import threading
from contextlib import AsyncExitStack
from typing import Callable

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from labb import settings

LOG_DIR = settings.USER_DIR / "logs"

CONNECT_TIMEOUT = 30
CALL_TIMEOUT = 300


def connection(config: dict) -> dict:
    """A server's settings as a langchain-mcp-adapters connection"""

    if "url" in config:
        return {"transport": config.get("transport", "streamable_http"), "url": config["url"], **({"headers": config["headers"]} if "headers" in config else {})}
    if "command" not in config:
        raise ValueError('needs a "command" or a "url"')
    stdio = {"transport": "stdio", "command": config["command"], "args": config.get("args", [])}
    for key in ("env", "cwd"):
        if key in config:
            stdio[key] = config[key]
    return stdio


def safe_name(text: str) -> str:
    # Providers allow letters, digits, _ and - in tool names
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text)


def tool_name(server: str, tool: str) -> str:
    return safe_name(f"mcp__{server}__{tool}")[:64]


def result_text(content) -> str:
    """An MCP tool's result as text: its text blocks, and a note for anything else"""

    if isinstance(content, str):
        return content
    if isinstance(content, tuple):
        content = content[0]
    parts = []
    for block in content if isinstance(content, list) else [content]:
        if isinstance(block, dict):
            parts.append(block.get("text") or f"[{block.get('type', 'content')} not shown]")
        else:
            parts.append(str(block))
    return "\n".join(parts) or "(no result)"


# permission(tool name, args, read-only, the run's configurable): None if it may run, else why not
Permission = Callable[[str, dict, bool, dict], str | None]


class McpServers:
    """MCP servers, connected, and their tools"""

    def __init__(self, servers: dict, permission: Permission):
        self.permission = permission
        self.tools: list = []
        # server name: number of tools, or why it couldn't connect
        self.status: dict[str, int | str] = {}
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="mcp").start()
        self.stack = AsyncExitStack()
        for name, config in servers.items():
            try:
                tools = self.call(self.connect(name, config), CONNECT_TIMEOUT)
            except Exception as e:
                self.status[name] = str(e) or type(e).__name__
                continue
            self.status[name] = len(tools)
            # "alwaysLoad": its tools are always sent with their schemas (tools/tool_search.py)
            self.tools += [self.wrap(name, tool, bool(config.get("alwaysLoad"))) for tool in tools]

    def call(self, coroutine, timeout: float):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout)

    async def connect(self, name: str, config: dict) -> list:
        server = connection(config)
        if server["transport"] != "stdio":
            client = MultiServerMCPClient({name: server})
            session = await self.stack.enter_async_context(client.session(name))
            return await load_mcp_tools(session, server_name=name)
        # With the SDK directly, which can send the server's stderr to a log file
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = self.stack.enter_context(open(LOG_DIR / f"mcp-{safe_name(name)}.log", "a"))
        parameters = StdioServerParameters(**{k: v for k, v in server.items() if k != "transport"})
        read, write = await self.stack.enter_async_context(stdio_client(parameters, errlog=log))
        session = await self.stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return await load_mcp_tools(session, server_name=name)

    def wrap(self, server: str, tool, always_load: bool = False) -> StructuredTool:
        """The tool, run on the event loop, after asking"""

        name = tool_name(server, tool.name)
        read_only = bool((tool.metadata or {}).get("readOnlyHint"))

        def run(config: RunnableConfig, **args) -> str:
            if refusal := self.permission(name, args, read_only, config.get("configurable") or {}):
                return refusal
            return result_text(self.call(tool.ainvoke(args), CALL_TIMEOUT))

        return StructuredTool(
            name=name,
            description=f"(From the MCP server {server}.) {tool.description or ''}".strip(),
            args_schema=tool.args_schema,
            func=run,
            metadata={"always_load": True} if always_load else None,
        )

    def close(self):
        try:
            self.call(self.stack.aclose(), 10)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
