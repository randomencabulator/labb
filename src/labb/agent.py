import os
from pathlib import Path

from dotenv import load_dotenv

# A source checkout (also an editable install) keeps its .env and chats.sqlite in the
# project dir, above src/labb; an installed copy (`uv tool install git+...`) keeps them in ~/.labb
SOURCE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = SOURCE_DIR if (SOURCE_DIR / "pyproject.toml").exists() else Path.home() / ".labb"

# Load .env before other imports, since llm.py creates the model at import time. What the
# environment already has wins, so remember what that was (to say where a bad key came from).
ENV_FILE = DATA_DIR / ".env"
FROM_ENVIRONMENT = frozenset(os.environ)
load_dotenv(ENV_FILE)

from langgraph.graph import StateGraph, START, END

from labb import db
from labb.state import MessagesState
from labb.model import compact_context, llm_call, recall_memories
from labb.tool_node import tool_node
from labb.end_logic import should_continue

# Build workflow
agent_builder = StateGraph(MessagesState)

# Add nodes
agent_builder.add_node("recall", recall_memories)
agent_builder.add_node("compact", compact_context)
agent_builder.add_node("llm_call", llm_call)
agent_builder.add_node("tool_node", tool_node)

# Add edges to connect nodes
agent_builder.add_edge(START, "recall")
agent_builder.add_edge("recall", "compact")
agent_builder.add_edge("compact", "llm_call")
agent_builder.add_conditional_edges(
    "llm_call",
    should_continue,
    ["tool_node", "compact", END]
)
agent_builder.add_edge("tool_node", "compact")

# Compile the agent
agent = agent_builder.compile()

# Chats keep their history in the database (db.py: SQLite, or the server's Postgres), keyed by
# thread_id, so they survive restarts. The web server runs the graph in worker threads; both
# checkpointers lock around their connection.
if "CHAT_DB" not in os.environ:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
CHAT_DB = os.getenv("CHAT_DB", str(DATA_DIR / "chats.sqlite"))
checkpointer = db.checkpointer(CHAT_DB)
chat_agent = agent_builder.compile(checkpointer=checkpointer)
