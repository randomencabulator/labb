"""Settings → MCP servers, for admins: the servers whose tools the web chat offers (see mcp.py)"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from labb.server import mcp, mcp_registry
from labb.server.auth_routes import admin_user

router = APIRouter()


class McpSettings(BaseModel):
    servers: dict


@router.get("/admin/mcp")
def get_mcp(user: dict = Depends(admin_user)):
    """The servers' settings (with their tokens: only admins see them) and how they're doing"""
    return {"servers": mcp.saved(), **{"status": mcp.status()}}


@router.put("/admin/mcp")
def save_mcp(body: McpSettings, user: dict = Depends(admin_user)):
    try:
        status = mcp.save(body.servers)
    except mcp.McpError as e:
        raise HTTPException(400, str(e))
    return {"servers": mcp.saved(), "status": status}


@router.get("/admin/mcp/catalog")
def mcp_catalog(q: str = "", cursor: str | None = None, user: dict = Depends(admin_user)):
    """Servers to add: the well-known ones that match, then the MCP Registry's (see mcp_registry.py).
    If the registry can't be reached, the well-known ones still come back, with why"""

    featured = [] if cursor else mcp_registry.featured(q)
    found, error, next_cursor = [], None, None
    if q.strip() or cursor:
        try:
            result = mcp_registry.search(q, cursor)
            known = {s["id"] for s in featured}
            found, next_cursor = [s for s in result["servers"] if s["id"] not in known], result["next"]
        except mcp_registry.RegistryError as e:
            error = str(e)
    return {"servers": featured + found, "next": next_cursor, "error": error, "runtimes": mcp_registry.runtimes()}
