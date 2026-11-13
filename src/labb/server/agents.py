"""Agents: assistants people set up in the web chat, each with its instructions, model, tools
and documents, which chats are started with (see model.py and tools/__init__.py for how a
run uses one).

- An agent belongs to whoever made it. A shared one shows up for everyone on the server,
  who can chat with it or duplicate it, but only its owner changes or deletes it.
- Chatting with an agent uses the chatter's memories and history, never the owner's. Its
  `documents` are "mine" (the chatter's own, like a chat without an agent), "none", or a
  list of the owner's documents, which a shared agent searches for everyone who uses it.
  Its `skills` are the same: "mine" (the chatter's), "none", or a list of skill names the
  owner can use (their own and shared ones), which the agent loads as the owner.
- A chat keeps the agent it started with (chats.agent_id). When the agent is deleted, or
  stops being shared, the chat goes on with the default assistant.
"""

import json
import uuid

from labb import db, llm
from labb.chats import conn, lock, now
from labb.server import engines
from labb.server import documents as documents_store
from labb.server import skills as skills_store
from labb.tools import TOOLSETS, choosable, name_of
from labb.server import limits

MAX_NAME = 60
MAX_EMOJI = 16
MAX_DESCRIPTION = 200
MAX_STARTERS = 4
MAX_STARTER = 200
DEFAULT_EMOJI = "🤖"
KNOWN_TOOLSETS = {name_of(t) for t in TOOLSETS}
FIELDS = ("name", "emoji", "description", "instructions", "model", "toolsets", "documents", "skills", "memories", "starters", "shared",
          "delegates", "actions", "engine_id")

with lock, conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agents ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, emoji TEXT NOT NULL, "
        "description TEXT NOT NULL, instructions TEXT NOT NULL, model TEXT, toolsets TEXT, "
        "documents TEXT NOT NULL, memories INTEGER NOT NULL, starters TEXT NOT NULL, "
        "shared INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        # The skills it loads, the agents it can ask and the Studio actions it can use (studio/team.py)
        """skills TEXT NOT NULL DEFAULT '"mine"', delegates TEXT NOT NULL DEFAULT '[]', actions TEXT NOT NULL DEFAULT '[]')"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS agents_by_user ON agents (user_id)")
    # The engine its chats use, tasks and workflows too (engines.py); added after the table
    db.add_column(conn, "agents", "engine_id", "TEXT")

COLUMNS = (
    "a.id, a.user_id, u.username, a.name, a.emoji, a.description, a.instructions, a.model, a.toolsets, "
    "a.documents, a.memories, a.starters, a.shared, a.created_at, a.updated_at, a.skills, a.delegates, a.actions, a.engine_id"
)
FROM = "agents a LEFT JOIN users u ON u.id = a.user_id"


class AgentError(ValueError):
    """An agent that can't be saved, said for the person who's setting it up"""


def as_dict(row, viewer: str | None = None) -> dict:
    (id, user_id, username, name, emoji, description, instructions, model, toolsets,
     documents, memories, starters, shared, created_at, updated_at, skills, delegates, actions, engine_id) = row
    return {
        "id": id,
        "owner_id": user_id,
        "owner": username,
        "mine": viewer is None or viewer == user_id,
        "name": name,
        "emoji": emoji,
        "description": description,
        "instructions": instructions,
        "model": model,
        # Toolsets that were removed since are left out
        "toolsets": [t for t in json.loads(toolsets) if t in KNOWN_TOOLSETS] if toolsets is not None else None,
        "documents": json.loads(documents),
        "skills": json.loads(skills),
        "delegates": json.loads(delegates),
        "actions": json.loads(actions),
        "engine_id": engine_id,
        "memories": bool(memories),
        "starters": json.loads(starters),
        "shared": bool(shared),
        "created_at": created_at,
        "updated_at": updated_at,
    }


def tool_choices() -> list[dict]:
    """The toolsets the editor offers"""
    return [
        {"name": name_of(t), "label": getattr(t, "LABEL", name_of(t)), "description": getattr(t, "DESCRIPTION", "")}
        for t in choosable()
    ]


def clean(user_id: str, fields: dict) -> dict:
    """Check and tidy what the editor sends; raises AgentError"""

    out = {}
    if "name" in fields:
        name = " ".join(str(fields["name"] or "").split())
        if not name:
            raise AgentError("Give the agent a name")
        if len(name) > MAX_NAME:
            raise AgentError(f"The name is over {MAX_NAME} characters")
        out["name"] = name
    if "emoji" in fields:
        emoji = str(fields["emoji"] or "").strip() or DEFAULT_EMOJI
        if len(emoji) > MAX_EMOJI:
            raise AgentError("The icon should be one emoji")
        out["emoji"] = emoji
    if "description" in fields:
        description = " ".join(str(fields["description"] or "").split())
        if len(description) > MAX_DESCRIPTION:
            raise AgentError(f"The description is over {MAX_DESCRIPTION} characters")
        out["description"] = description
    if "instructions" in fields:
        instructions = str(fields["instructions"] or "").strip()
        if len(instructions) > (most := limits.get("agent_instructions_chars")):
            raise AgentError(f"The instructions are over {most:,} characters")
        out["instructions"] = instructions
    if "model" in fields:
        spec = (fields["model"] or "").strip() or None
        if spec:
            try:
                spec = llm.load(spec).spec
            except ValueError as e:
                raise AgentError(f"That model can't be used: {e}")
            from labb.server import auth, providers

            owner = auth.get_user(user_id)
            if owner and (reason := providers.why_not(spec, owner["role"])):
                raise AgentError(reason)
        out["model"] = spec
    if "toolsets" in fields:
        toolsets = fields["toolsets"]
        if toolsets is not None:
            known = {t["name"] for t in tool_choices()}
            if unknown := [t for t in toolsets if t not in known]:
                raise AgentError(f"Unknown tools: {', '.join(unknown)}")
            toolsets = sorted(set(toolsets))
        out["toolsets"] = toolsets
    if "documents" in fields:
        docs = fields["documents"]
        if isinstance(docs, list):
            docs = list(dict.fromkeys(docs))
            if missing := [d for d in docs if documents_store.get(user_id, d) is None]:
                raise AgentError(f"No such document: {missing[0]}")
        elif docs not in ("mine", "none"):
            raise AgentError('Documents are "mine", "none" or a list of your documents')
        out["documents"] = docs
    if "skills" in fields:
        chosen = fields["skills"]
        if isinstance(chosen, list):
            chosen = list(dict.fromkeys(chosen))
            known = {s["name"] for s in skills_store.list_for(user_id)}
            if missing := [name for name in chosen if name not in known]:
                raise AgentError(f"No such skill: {missing[0]}")
        elif chosen not in ("mine", "none"):
            raise AgentError('Skills are "mine", "none" or a list of skill names')
        out["skills"] = chosen
    if "delegates" in fields:
        chosen = list(dict.fromkeys(fields["delegates"] or []))
        if len(chosen) > (most := limits.get("agent_team")):
            raise AgentError(f"An agent can ask at most {most} other agents")
        if missing := [a for a in chosen if visible(a, user_id) is None]:
            raise AgentError(f"No such agent to ask: {missing[0]}")
        out["delegates"] = chosen
    if "actions" in fields:
        from labb.server.studio import actions as studio_actions

        chosen = list(dict.fromkeys(fields["actions"] or []))
        if len(chosen) > (most := limits.get("agent_team")):
            raise AgentError(f"An agent can use at most {most} actions")
        if missing := [a for a in chosen if studio_actions.visible(a, user_id) is None]:
            raise AgentError(f"No such action: {missing[0]}")
        out["actions"] = chosen
    if "engine_id" in fields:
        chosen = fields["engine_id"] or None
        if chosen:
            try:
                engines.picked(chosen, user_id)
            except engines.EngineError as e:
                raise AgentError(str(e))
        out["engine_id"] = chosen
    if "memories" in fields:
        out["memories"] = bool(fields["memories"])
    if "starters" in fields:
        starters = [" ".join(str(s).split()) for s in fields["starters"] or []]
        starters = [s for s in starters if s]
        if len(starters) > MAX_STARTERS:
            raise AgentError(f"At most {MAX_STARTERS} conversation starters")
        if any(len(s) > MAX_STARTER for s in starters):
            raise AgentError(f"A conversation starter is over {MAX_STARTER} characters")
        out["starters"] = starters
    if "shared" in fields:
        out["shared"] = bool(fields["shared"])
    return out


def draft(user_id: str, fields: dict) -> dict:
    """An agent as the editor has it, not saved: for trying it out"""

    agent = {"id": None, "owner_id": user_id, "name": "Agent", "emoji": DEFAULT_EMOJI, "description": "",
             "instructions": "", "model": None, "toolsets": None, "documents": "mine", "skills": "mine", "memories": True,
             "starters": [], "shared": False, "delegates": [], "actions": [], "engine_id": None}
    agent.update(clean(user_id, {k: v for k, v in fields.items() if k in FIELDS}))
    return agent


def _db(value):
    return json.dumps(value) if value is not None else None


def create(user_id: str, fields: dict) -> dict:
    agent = draft(user_id, {"name": "", **fields})
    with lock, conn:
        if conn.execute("SELECT COUNT(*) FROM agents WHERE user_id = ?", (user_id,)).fetchone()[0] >= (most := limits.get("agents_per_person")):
            raise AgentError(f"You have {most} agents already; delete one first")
        agent_id, at = uuid.uuid4().hex[:12], now()
        conn.execute(
            "INSERT INTO agents (id, user_id, name, emoji, description, instructions, model, toolsets, documents, "
            "memories, starters, shared, created_at, updated_at, skills, delegates, actions, engine_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (agent_id, user_id, agent["name"], agent["emoji"], agent["description"], agent["instructions"],
             agent["model"], _db(agent["toolsets"]), json.dumps(agent["documents"]), int(agent["memories"]),
             json.dumps(agent["starters"]), int(agent["shared"]), at, at, json.dumps(agent["skills"]),
             json.dumps(agent["delegates"]), json.dumps(agent["actions"]), agent["engine_id"]),
        )
    return get(agent_id)


def get(agent_id: str, viewer: str | None = None) -> dict | None:
    with lock:
        row = conn.execute(f"SELECT {COLUMNS} FROM {FROM} WHERE a.id = ?", (agent_id,)).fetchone()
    return as_dict(row, viewer) if row else None


def visible(agent_id: str, user_id: str) -> dict | None:
    """The agent, if it's `user_id`'s or shared"""
    agent = get(agent_id, user_id)
    return agent if agent and (agent["mine"] or agent["shared"]) else None


def list_for(user_id: str) -> list[dict]:
    """Your agents, then the ones others shared, each newest first"""

    with lock:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM {FROM} WHERE a.user_id = ? OR a.shared = 1 "
            "ORDER BY a.user_id != ?, a.updated_at DESC",
            (user_id, user_id),
        ).fetchall()
    return [as_dict(row, user_id) for row in rows]


def update(user_id: str, agent_id: str, fields: dict) -> dict | None:
    """Change your agent; None if you have no such agent"""

    change = clean(user_id, {k: v for k, v in fields.items() if k in FIELDS})
    if agent_id in change.get("delegates", []):
        raise AgentError("An agent can't ask itself")
    columns = {k: (_db(v) if k in ("toolsets", "documents", "skills", "starters", "delegates", "actions")
                   else int(v) if isinstance(v, bool) else v)
               for k, v in change.items()}
    with lock, conn:
        if not conn.execute("SELECT 1 FROM agents WHERE id = ? AND user_id = ?", (agent_id, user_id)).fetchone():
            return None
        if columns:
            sets = ", ".join(f"{k} = ?" for k in columns)
            conn.execute(f"UPDATE agents SET {sets}, updated_at = ? WHERE id = ?", (*columns.values(), now(), agent_id))
    return get(agent_id, user_id)


def delete(user_id: str, agent_id: str) -> bool:
    """Delete your agent; its chats go on with the default assistant"""
    with lock, conn:
        return conn.execute("DELETE FROM agents WHERE id = ? AND user_id = ?", (agent_id, user_id)).rowcount > 0


def duplicate(user_id: str, agent_id: str) -> dict | None:
    """Your own copy of an agent you can see; another's documents aren't copied"""

    agent = visible(agent_id, user_id)
    if agent is None:
        return None
    fields = {k: agent[k] for k in FIELDS}
    fields["name"] = f"{agent['name']} (copy)"[:MAX_NAME]
    fields["shared"] = False
    # Only the agents and actions the copy's owner can use too
    from labb.server.studio import actions as studio_actions

    fields["delegates"] = [i for i in agent.get("delegates") or [] if visible(i, user_id)]
    fields["actions"] = [i for i in agent.get("actions") or [] if studio_actions.visible(i, user_id)]
    if not agent["mine"] and isinstance(agent["documents"], list):
        fields["documents"] = "none"
    # The skills it names that the new owner can't use are left out
    if isinstance(agent["skills"], list):
        usable = {s["name"] for s in skills_store.list_for(user_id)}
        fields["skills"] = [name for name in agent["skills"] if name in usable]
    return create(user_id, fields)


def remove_document(user_id: str, doc_id: str):
    """A deleted document leaves the owner's agents that searched it"""

    with lock, conn:
        rows = conn.execute("SELECT id, documents FROM agents WHERE user_id = ? AND documents LIKE ?",
                            (user_id, f'%"{doc_id}"%')).fetchall()
        for agent_id, docs in rows:
            kept = [d for d in json.loads(docs) if d != doc_id]
            conn.execute("UPDATE agents SET documents = ? WHERE id = ?", (json.dumps(kept), agent_id))


def for_run(agent: dict) -> dict:
    """What a run needs of an agent (configurable["agent"]): with the names of its documents,
    for the system prompt, and without the ones that are gone"""

    run = {k: agent[k] for k in ("id", "owner_id", "name", "description", "instructions", "toolsets", "documents", "skills", "memories")}
    # As its owner sees it; tasks and workflows that run the agent get it through here too
    run["engine"] = engines.for_owner(agent.get("engine_id"), agent["owner_id"])
    # The agents it can ask and the actions it can use, as its owner sees them now
    run["delegates"] = [{"id": a["id"], "name": a["name"], "description": a["description"]}
                        for a in (visible(i, agent["owner_id"]) for i in agent.get("delegates") or []) if a and a["id"] != agent["id"]]
    if agent.get("actions"):
        from labb.server.studio import actions as studio_actions

        run["actions"] = [{"id": a["id"], "name": a["name"], "description": a["description"],
                           "inputs": [i["name"] for i in a["config"].get("inputs", [])]}
                          for a in (studio_actions.visible(i, agent["owner_id"]) for i in agent["actions"]) if a]
    else:
        run["actions"] = []
    if isinstance(agent["documents"], list):
        found = [d for d in (documents_store.get(agent["owner_id"], i) for i in agent["documents"]) if d]
        run["documents"] = [d["id"] for d in found]
        run["document_names"] = [d["name"] for d in found]
    return run
