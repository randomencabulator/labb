"""What models cost, for the usage stats: set by admins in Settings → Models → Prices.

Prices are costs.py's, in dollars per million tokens, `[input, output, cached input, cache write]`
(the last two optional), keyed by a model's spec, its name or a pattern. They're kept in
`server_settings` ('prices') and handed to usage.py, which costs each reply with them as it's
recorded. No provider says what its models cost through its API, so they're set here; `suggest`
offers ones from LiteLLM's public price list for an admin to check and save, never by itself.
"""

import json
import threading
import time

import httpx

from labb import costs, usage
from labb.chats import conn, lock

MAX_PRICES = 500
MAX_KEY = 200
# LiteLLM's list of models' prices (per token), kept up by its community
SUGGESTIONS_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
# The list is fetched again after this long
SUGGESTIONS_SECONDS = 24 * 3600
# Provider prefixes LiteLLM uses, for a model name without one
LITELLM_PREFIXES = ("", "openai/", "anthropic/", "openrouter/", "ollama/")

_prices: dict | None = None
_prices_lock = threading.Lock()
_list: tuple[float, dict] | None = None
http = httpx.Client(timeout=httpx.Timeout(20, connect=6), follow_redirects=True)


class PriceError(ValueError):
    """Prices that can't be saved, said for the admin"""


def get() -> dict:
    """The prices, as costs.py takes them"""
    global _prices
    with _prices_lock:
        if _prices is None:
            with lock:
                row = conn.execute("SELECT value FROM server_settings WHERE key = 'prices'").fetchone()
            _prices = json.loads(row[0]) if row else {}
        return dict(_prices)


def save(prices: dict) -> dict:
    """New prices in place of the old ones; replies recorded before keep the cost they had"""
    if not isinstance(prices, dict) or len(prices) > MAX_PRICES:
        raise PriceError(f"At most {MAX_PRICES} prices")
    cleaned = {}
    for key, price in prices.items():
        key = str(key).strip()
        if not key or len(key) > MAX_KEY:
            raise PriceError(f"A model's name is 1 to {MAX_KEY} characters")
        price = list(price) if isinstance(price, (list, tuple)) else price
        # Trailing cache prices left empty are the input price
        while isinstance(price, list) and len(price) > 2 and price[-1] is None:
            price.pop()
        if not costs.valid(price):
            raise PriceError(f"{key}: a price is dollars per million tokens, input and output (and cached input, cache write), none below 0")
        cleaned[key] = price
    global _prices
    with lock, conn:
        conn.execute("INSERT INTO server_settings (key, value) VALUES ('prices', ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (json.dumps(cleaned),))
    with _prices_lock:
        _prices = cleaned
    return dict(cleaned)


def used_models() -> list[str]:
    """The models replies were recorded with, most used first, to price"""
    with lock:
        rows = conn.execute("SELECT model FROM usage GROUP BY model ORDER BY SUM(input_tokens + output_tokens) DESC").fetchall()
    return [model for (model,) in rows if model and model != "unknown"]


def fillable() -> int:
    """Replies recorded without a cost whose model has a price now (usage.reprice would cost them)"""
    with lock:
        rows = conn.execute("SELECT model, COUNT(*) FROM usage WHERE cost IS NULL GROUP BY model").fetchall()
    priced = get()
    return sum(count for model, count in rows if costs.price_of(model, priced) is not None)


def _price_list() -> dict:
    global _list
    if _list and time.monotonic() - _list[0] < SUGGESTIONS_SECONDS:
        return _list[1]
    response = http.get(SUGGESTIONS_URL)
    response.raise_for_status()
    found = response.json()
    if not isinstance(found, dict):
        raise ValueError("Not a price list")
    _list = (time.monotonic(), found)
    return found


def _entry_for(model: str, listed: dict) -> tuple[str, dict] | None:
    """LiteLLM's entry for a model ("openai:gpt-5.5", "gpt-5.5", "qwen/qwen3-coder"), if it has one with prices"""
    provider, _, name = model.partition(":")
    names = [model] if provider not in costs.KNOWN_PROVIDERS or not name else [name, f"{provider}/{name}"]
    for candidate in names:
        for prefix in LITELLM_PREFIXES:
            entry = listed.get(prefix + candidate)
            if isinstance(entry, dict) and isinstance(entry.get("input_cost_per_token"), (int, float)) \
                    and isinstance(entry.get("output_cost_per_token"), (int, float)):
                return prefix + candidate, entry
    return None


def _per_million(value) -> float | None:
    return round(value * 1_000_000, 6) if isinstance(value, (int, float)) and value >= 0 else None


def suggest(models: list[str]) -> dict:
    """Prices for these models from LiteLLM's list: {model: {"price": [...], "from": its key there}};
    models it hasn't are left out. Raises ValueError if the list can't be had."""
    try:
        listed = _price_list()
    except (httpx.HTTPError, ValueError) as e:
        raise ValueError(f"Couldn't get LiteLLM's price list: {e}")
    found = {}
    for model in models:
        if not (match := _entry_for(model, listed)):
            continue
        key, entry = match
        price = [_per_million(entry["input_cost_per_token"]), _per_million(entry["output_cost_per_token"]),
                 _per_million(entry.get("cache_read_input_token_cost")), _per_million(entry.get("cache_creation_input_token_cost"))]
        while len(price) > 2 and price[-1] is None:
            price.pop()
        found[model] = {"price": price, "from": key}
    return found


def apply():
    """Cost the replies recorded from now on at these prices"""
    usage.prices = get
