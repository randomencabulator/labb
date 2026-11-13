"""What models cost in the usage stats: the admins' prices (server/prices.py), each reply costed as
it's recorded, earlier ones filled in, and prices suggested from LiteLLM's list."""

from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from labb import usage
from labb.server import app as server
from labb.server import auth, prices
from conftest import ai


@pytest.fixture(autouse=True)
def no_prices():
    prices.save({})
    yield
    prices.save({})


@pytest.fixture
def person(accounts):
    user = auth.create_user(f"u{uuid4().hex[:10]}", "a-password")
    key, _ = auth.create_key(user["id"], "laptop")
    return user, TestClient(server.app, headers={"Authorization": f"Bearer {key}"})


def reply(user, model, i=1_000_000, o=100_000, **stats):
    usage.record(f"t-{uuid4()}", "m1", {"model": model, "input_tokens": i, "output_tokens": o, **stats},
                 user_id=user["id"], source="web")


def test_replies_are_costed_at_the_prices_when_they_were_recorded(client, person):
    user, mine = person
    model = f"paid-{uuid4().hex[:6]}"
    reply(user, model)  # No price yet: no cost
    saved = client.put("/admin/prices", json={"prices": {model: [3, 15, 0.3, 3.75]}}).json()
    assert saved["prices"] == {model: [3, 15, 0.3, 3.75]} and saved["fillable"] == 1 and model in saved["models"]
    # 1M input (600k from the cache, 100k written to it) and 100k output
    reply(user, model, cache_read_tokens=600_000, cache_write_tokens=100_000)
    totals = mine.get("/usage").json()["totals"]
    assert round(totals["cost"], 6) == 2.955 and totals["priced_replies"] == 1 and totals["replies"] == 2
    assert totals["cache_read_tokens"] == 600_000

    # A new price doesn't change what was spent
    client.put("/admin/prices", json={"prices": {model: [6, 30]}})
    assert round(mine.get("/usage").json()["totals"]["cost"], 6) == 2.955
    # The one without a cost gets one at today's price: 1M × $6 + 100k × $30
    assert client.post("/admin/usage/reprice", json={}).json()["updated"] >= 1
    numbers = mine.get("/usage").json()
    assert round(numbers["totals"]["cost"], 6) == round(2.955 + 9, 6)
    assert round(numbers["by_model"][0]["cost"], 6) == round(2.955 + 9, 6) and numbers["by_day"][0]["cost"] is not None
    # Everything again at today's prices, if asked
    client.post("/admin/usage/reprice", json={"everything": True})
    assert round(mine.get("/usage").json()["totals"]["cost"], 6) == 18
    user_row = next(u for u in client.get("/admin/usage/users").json() if u["id"] == user["id"])
    assert round(user_row["cost"], 6) == 18
    assert "cost" in mine.get("/usage", headers={"Accept": "text/csv"}).text.splitlines()[0]


def test_recording_reads_prices_not_cached_yet(person, monkeypatch):
    # The prices come from the database the first time, while a reply is being recorded
    user, mine = person
    monkeypatch.setattr(prices, "_prices", None)
    reply(user, f"m-{uuid4().hex[:6]}")
    assert mine.get("/usage").json()["totals"]["replies"] == 1


def test_free_models_have_no_cost(client, person):
    user, mine = person
    client.put("/admin/prices", json={"prices": {"openai:gpt-*": [1, 2]}})
    reply(user, "qwen3.5:4b")
    reply(user, "gpt-5.5", i=1_000_000, o=0)
    totals = mine.get("/usage").json()["totals"]
    assert totals["cost"] == 1 and totals["priced_replies"] == 1


def test_prices_are_checked_and_only_for_admins(client, accounts):
    for bad in ({"m": [1]}, {"m": [-1, 2]}, {"": [1, 2]}, {"m": [1, 2, 3, 4, 5]}):
        assert client.put("/admin/prices", json={"prices": bad}).status_code in (400, 422), bad
    # Empty cache prices are the input price
    assert client.put("/admin/prices", json={"prices": {"m": [1, 2, None, None]}}).json()["prices"] == {"m": [1, 2]}
    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert member.get("/admin/prices").status_code == 403
    assert member.put("/admin/prices", json={"prices": {}}).status_code == 403
    assert member.post("/admin/usage/reprice", json={}).status_code == 403


def test_prices_suggested_from_litellms_list(client, monkeypatch):
    listed = {
        "gpt-5.5": {"input_cost_per_token": 1.25e-6, "output_cost_per_token": 1e-5, "cache_read_input_token_cost": 1.25e-7},
        "anthropic/claude-x": {"input_cost_per_token": 3e-6, "output_cost_per_token": 1.5e-5,
                               "cache_read_input_token_cost": 3e-7, "cache_creation_input_token_cost": 3.75e-6},
        "sample_spec": {"input_cost_per_token": "x"},
    }
    asked = []

    def get(url):
        asked.append(url)
        return httpx.Response(200, json=listed, request=httpx.Request("GET", url))

    monkeypatch.setattr(prices, "_list", None)
    monkeypatch.setattr(prices.http, "get", get)
    found = client.post("/admin/prices/suggest", json={"models": ["openai:gpt-5.5", "claude-x", "qwen3.5:4b", "sample_spec"]}).json()
    assert found["suggestions"] == {
        "openai:gpt-5.5": {"price": [1.25, 10, 0.125], "from": "gpt-5.5"},
        "claude-x": {"price": [3, 15, 0.3, 3.75], "from": "anthropic/claude-x"},
    }
    # Fetched once a day at most
    client.post("/admin/prices/suggest", json={"models": ["claude-x"]})
    assert len(asked) == 1
    # Nothing is saved by suggesting
    assert client.get("/admin/prices").json()["prices"] == {}


def test_a_list_that_cant_be_had_says_so(client, monkeypatch):
    def fail(url):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(prices, "_list", None)
    monkeypatch.setattr(prices.http, "get", fail)
    response = client.post("/admin/prices/suggest", json={"models": ["gpt-5.5"]})
    assert response.status_code == 502 and "LiteLLM" in response.json()["detail"]


def test_a_reply_records_its_cached_tokens(fake_model, config):
    from langchain_core.messages import AIMessage
    from labb import runner

    message = AIMessage("hi", usage_metadata={"input_tokens": 100, "output_tokens": 5, "total_tokens": 105,
                                              "input_token_details": {"cache_read": 60, "cache_creation": 30}})
    fake_model(message, ai("again", tokens=(10, 1)))
    events = []
    runner.run_reply(f"cli-{uuid4()}", "hi", __import__("threading").Event(), events.append, configurable=config["configurable"])
    stats = next(e for e in events if e["type"] == "done")["stats"]
    assert stats["cache_read_tokens"] == 60 and stats["cache_write_tokens"] == 30
