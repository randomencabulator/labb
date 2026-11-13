"""Shared setup: a throwaway chat database and a fake model, so the tests need no Ollama."""

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

# Before the project's modules are imported: their own database, and a fixed context
# window so llm.py doesn't ask the Ollama server for one
os.environ["CHAT_DB"] = str(Path(tempfile.mkdtemp(prefix="labb-tests-")) / "chats.sqlite")
os.environ["CONTEXT_WINDOW"] = "32768"
# Ollama's cloud models aren't asked of ollama.com (tests that need them set a fake list)
os.environ["OLLAMA_CLOUD_CATALOG"] = "off"
# With TEST_DATABASE_URL, the same tests run on Postgres instead, in that database emptied first
if os.getenv("TEST_DATABASE_URL"):
    import psycopg

    os.environ["LABB_DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(os.environ["LABB_DATABASE_URL"], autocommit=True) as pg:
        pg.execute("DROP SCHEMA public CASCADE")
        pg.execute("CREATE SCHEMA public")
else:
    # Set, so a LABB_DATABASE_URL in .env isn't loaded either
    os.environ["LABB_DATABASE_URL"] = ""

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk


class FakeModel(GenericFakeChatModel):
    """Replies with the given messages in order; tools are accepted and ignored"""

    def bind_tools(self, tools, **kwargs):
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        # The whole reply as one chunk, tool calls included (the parent only streams text)
        message = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs).generations[0].message
        chunk = ChatGenerationChunk(message=AIMessageChunk(
            content=message.content,
            id=message.id,
            usage_metadata=message.usage_metadata,
            response_metadata=message.response_metadata,
            tool_call_chunks=[
                {"name": call["name"], "args": json.dumps(call["args"]), "id": call["id"], "index": i}
                for i, call in enumerate(message.tool_calls)
            ],
        ))
        if run_manager and message.content:
            run_manager.on_llm_new_token(message.content, chunk=chunk)
        yield chunk


DIMENSIONS = 256


class WordEmbeddings(Embeddings):
    """Counts of the (hashed) words: texts with words in common point the same way"""

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * DIMENSIONS
        for word in text.lower().split():
            word = word.strip(".,:;!?()\"'")
            if word:
                vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIMENSIONS] += 1
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]


def ai(text: str = "", tool_calls: list | None = None, tokens: tuple[int, int] | None = None) -> AIMessage:
    """A model reply; tool_calls as [(name, args), ...], tokens as (input, output)"""
    calls = [{"name": name, "args": args, "id": f"call-{i}-{name}"} for i, (name, args) in enumerate(tool_calls or [])]
    usage = {"input_tokens": tokens[0], "output_tokens": tokens[1], "total_tokens": sum(tokens)} if tokens else None
    return AIMessage(content=text, tool_calls=calls, usage_metadata=usage)


@pytest.fixture
def fake_model(monkeypatch):
    """Make the graph use a FakeModel with these replies: fake_model(ai("hi"), ...)"""

    from labb import model

    def use(*replies):
        fake = FakeModel(messages=iter(list(replies)))
        monkeypatch.setattr(model, "model_with_tools", lambda *args: fake)
        # Broken tool calls aren't rewritten by a real model (tests that want it set their own)
        monkeypatch.setattr(model, "constrainer", lambda *args: None)
        return fake

    return use


@pytest.fixture(autouse=True)
def trust_file(tmp_path_factory):
    """What a project's .labb was agreed to goes in a file of the test's own, not ~/.labb. (Not with
    monkeypatch: an autouse one would undo the tests' own patches after their fixtures' teardowns.)"""
    from labb import settings

    path = tmp_path_factory.mktemp("trust") / "trusted-projects.json"
    original = settings.trust_file
    settings.trust_file = lambda: path
    yield path
    settings.trust_file = original


@pytest.fixture
def config(tmp_path):
    """A run config with a working directory, like the terminal's"""
    return {"configurable": {"workdir": str(tmp_path)}}


@pytest.fixture(scope="session")
def accounts():
    """An admin and a member, with an API key each: {"admin": (user, key), "member": (user, key)}"""

    from labb.server import auth

    admin = auth.setup("admin", "admin-password") if not auth.has_users() else auth.login("admin", "admin-password")
    member = auth.create_user("bob", "bob-password")
    return {
        "admin": (admin, auth.create_key(admin["id"], "tests")[0]),
        "member": (member, auth.create_key(member["id"], "tests")[0]),
    }


@pytest.fixture
def client(accounts):
    """The server, called as the admin (with their API key)"""

    from fastapi.testclient import TestClient
    from labb.server import app as server

    return TestClient(server.app, headers={"Authorization": f"Bearer {accounts['admin'][1]}"})
