"""What sessions cost, from the prices in the settings, and the limit that stops them"""

import threading

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from labb import costs, runner, settings
from conftest import ai

PRICES = {"openai:gpt-5.5": [1.25, 10], "anthropic:claude-*": [3, 15], "broken": "free"}


def test_prices_are_found_by_spec_name_or_pattern():
    # Cached input and cache writes cost the input price unless they're given
    assert costs.price_of("openai:gpt-5.5", PRICES) == (1.25, 10, 1.25, 1.25)
    assert costs.price_of("gpt-5.5", PRICES) == (1.25, 10, 1.25, 1.25)
    assert costs.price_of("claude-sonnet-5-5", PRICES) == (3, 15, 3, 3)
    assert costs.price_of("anthropic:claude-haiku-5-5", PRICES) == (3, 15, 3, 3)
    assert costs.price_of("qwen3.5:4b", PRICES) is None and costs.price_of("broken", PRICES) is None
    # An exact key wins over a pattern
    assert costs.price_of("claude-x", {"anthropic:claude-*": [3, 15], "claude-x": [1, 1]})[:2] == (1, 1)


def test_cached_input_and_cache_writes_have_their_own_prices():
    price = costs.price_of("claude-x", {"claude-x": [3, 15, 0.3, 3.75]})
    assert price == (3, 15, 0.3, 3.75)
    # 1M input of which 600k read from the cache and 100k written to it, and 100k output
    assert round(costs.cost_at(price, 1_000_000, 100_000, 600_000, 100_000), 6) == round(0.9 + 1.5 + 0.18 + 0.375, 6)
    assert costs.price_of("x", {"x": [1, 2, None, 4]}) == (1, 2, 1, 4)
    for bad in ([1], [1, 2, 3, 4, 5], [1, -2], [1, True], "free", [1, 2, "a"]):
        assert not costs.valid(bad), bad
    assert costs.cached({"input_token_details": {"cache_read": 5, "cache_creation": 2}}) == (5, 2) and costs.cached({}) == (0, 0)


def test_a_sessions_cost_adds_up_its_replies():
    stats = [{"model": "gpt-5.5", "input_tokens": 1_000_000, "output_tokens": 100_000},
             {"model": "qwen3.5:4b", "input_tokens": 50_000, "output_tokens": 1_000}]
    cost, unpriced = costs.of_stats(stats, PRICES)
    assert round(cost, 6) == 2.25 and unpriced == {"qwen3.5:4b"}


def test_the_budget_stops_a_reply_once_the_limit_is_reached():
    stop = threading.Event()
    budget = costs.Budget(limit=1.0, spent=0.9, model="openai:gpt-5.5", prices=PRICES, stop_requested=stop)

    def call(input_tokens, output_tokens):
        message = AIMessage("", usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens,
                                                "total_tokens": input_tokens + output_tokens})
        budget.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))

    call(10_000, 1_000)  # $0.0225
    assert not stop.is_set() and not budget.reached
    call(10_000, 10_000)  # $0.1125: over
    assert stop.is_set() and budget.reached and budget.spent > 1.0


def test_the_budget_ends_a_real_run(fake_model, config):
    fake_model(ai("thinking", tool_calls=[("list_files", {})], tokens=(1_000_000, 0)), ai("never sent"))
    stop = threading.Event()
    budget = costs.Budget(limit=1.0, spent=0.0, model="openai:gpt-5.5", prices=PRICES, stop_requested=stop)
    events = []
    runner.run_reply("cli-budget", "hi", stop, events.append, configurable={**config["configurable"], "budget": budget})
    assert budget.reached and events[-1]["type"] == "stopped"


def test_prices_and_the_limit_are_settings(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(settings, "user_file", lambda: tmp_path / "none.json")
    (tmp_path / ".labb").mkdir()
    (tmp_path / ".labb" / "settings.json").write_text(json.dumps({"prices": PRICES, "maxCostPerSession": 2.5}))
    merged, problems = settings.load(str(tmp_path))
    assert merged["maxCostPerSession"] == 2.5 and merged["prices"] == PRICES and problems == []
    (tmp_path / ".labb" / "settings.json").write_text(json.dumps({"maxCostPerSession": "lots"}))
    merged, problems = settings.load(str(tmp_path))
    assert "maxCostPerSession" not in merged and "a number" in problems[0]
