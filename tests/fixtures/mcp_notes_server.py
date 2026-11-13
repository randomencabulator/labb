"""A tiny MCP server for the tests: one read-only tool and one that changes something"""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

server = FastMCP("notes")
notes: list[str] = []


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def count_notes() -> str:
    """How many notes there are"""
    return f"{len(notes)} notes"


@server.tool()
def add_note(text: str) -> str:
    """Add a note"""
    notes.append(text)
    return f"Added: {text}"


if __name__ == "__main__":
    server.run()
