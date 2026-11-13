"""The agents endpoints (see agents.py): yours and the shared ones to list and chat with, yours
to change, and the tools the editor offers."""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from labb.server.auth_routes import current_user
from labb.server import agents

router = APIRouter()


class AgentFields(BaseModel):
    """What the editor sends; on a change, only the fields it sends change"""

    name: str | None = None
    emoji: str | None = None
    description: str | None = None
    instructions: str | None = None
    # "<provider>:<model>"; empty for the server's default
    model: str | None = None
    # The toolsets it may use (from /agents/tools); null for all of them
    toolsets: list[str] | None = None
    # "mine" (whoever chats, their own documents), "none", or a list of your document ids
    documents: str | list[str] | None = None
    # "mine" (whoever chats, their skills), "none", or a list of skill names you can use
    skills: str | list[str] | None = None
    memories: bool | None = None
    starters: list[str] | None = None
    shared: bool | None = None
    # The agents it can ask, and the Studio actions it can use (ids)
    delegates: list[str] | None = None
    actions: list[str] | None = None
    # The engine its chats use; empty for whoever's chatting's own
    engine_id: str | None = None


def fields(body: AgentFields) -> dict:
    # Left out means unchanged; documents and memories can't be null
    sent = body.model_dump(exclude_unset=True)
    return {k: v for k, v in sent.items() if v is not None or k in ("model", "toolsets", "engine_id")}


@router.get("/agents")
def list_agents(user: dict = Depends(current_user)):
    """Your agents, then the ones others shared (`mine` says which)"""
    return agents.list_for(user["id"])


@router.get("/agents/tools")
def tool_choices(user: dict = Depends(current_user)):
    """The toolsets an agent can be given"""
    return agents.tool_choices()


@router.post("/agents", status_code=201)
def create_agent(body: AgentFields, user: dict = Depends(current_user)):
    try:
        return agents.create(user["id"], fields(body))
    except agents.AgentError as e:
        raise HTTPException(400, str(e))


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str, user: dict = Depends(current_user)):
    if (agent := agents.visible(agent_id, user["id"])) is None:
        raise HTTPException(404, "No such agent")
    return agent


@router.patch("/agents/{agent_id}")
def update_agent(agent_id: str, body: AgentFields, user: dict = Depends(current_user)):
    """Change your agent (a shared one only its owner can)"""
    try:
        agent = agents.update(user["id"], agent_id, fields(body))
    except agents.AgentError as e:
        raise HTTPException(400, str(e))
    if agent is None:
        raise HTTPException(404, "No such agent")
    return agent


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(agent_id: str, user: dict = Depends(current_user)):
    if not agents.delete(user["id"], agent_id):
        raise HTTPException(404, "No such agent")
    return Response(status_code=204)


@router.post("/agents/{agent_id}/duplicate", status_code=201)
def duplicate_agent(agent_id: str, user: dict = Depends(current_user)):
    """Your own copy of an agent, to change as you like"""
    try:
        agent = agents.duplicate(user["id"], agent_id)
    except agents.AgentError as e:
        raise HTTPException(400, str(e))
    if agent is None:
        raise HTTPException(404, "No such agent")
    return agent
