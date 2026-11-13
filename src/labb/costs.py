"""What model calls cost, from prices someone set: the terminal's /cost and maxCostPerSession
(prices in its settings), and the server's usage stats (Settings → Models → Prices).

Prices are in dollars per million tokens, `[input, output]`, keyed by a model's spec, its name,
or a pattern of either: {"openai:gpt-5.5": [1.25, 10], "anthropic:claude-*": [3, 15]}. Two more
are optional, `[input, output, cached input, cache write]`: what reading input from the
provider's prompt cache costs, and what writing it there does; each is the input price when left
out. labb ships none: they change, and local models are free. Models without a price count as free.

Input tokens are all of them, cached ones included (LangChain counts them so), and
`input_token_details` says how many were read from the cache or written to it."""

import fnmatch
import threading

from langchain_core.callbacks import BaseCallbackHandler


Price = tuple[float, float, float, float]


def price_of(model: str, prices: dict) -> Price | None:
    """The price of a model, by its spec ("openai:gpt-5.5") or its name ("gpt-5.5", as the
    replies' stats have it); an exact key wins over a pattern"""

    names = forms(model)
    exact = [price for key, price in prices.items() if forms(key) & names]
    patterns = [price for key, price in prices.items()
                if any(fnmatch.fnmatchcase(name, pattern) for name in names for pattern in forms(key))]
    price = next((p for p in exact + patterns if valid(p)), None)
    if not price:
        return None
    input_price, output_price = float(price[0]), float(price[1])
    # Cached input and cache writes: the input price unless they're given
    rest = [float(p) if p is not None else input_price for p in [*price[2:], None, None][:2]]
    return input_price, output_price, rest[0], rest[1]


def forms(model: str) -> set[str]:
    """A model's spec and its name: {"openai:gpt-5.5", "gpt-5.5"} (Ollama's names have colons too)"""
    provider, _, name = model.partition(":")
    return {model, name} if provider in KNOWN_PROVIDERS and name else {model}


def valid(price) -> bool:
    """[input, output], and optionally cached input and cache write (null: the input price)"""
    number = lambda p: isinstance(p, (int, float)) and not isinstance(p, bool) and p >= 0
    return (isinstance(price, list) and 2 <= len(price) <= 4 and number(price[0]) and number(price[1])
            and all(p is None or number(p) for p in price[2:]))


# Spec prefixes, so "openai:gpt-5.5" and "gpt-5.5" are the same model
KNOWN_PROVIDERS = {"ollama", "openai", "anthropic", "google_genai", "mistralai", "groq", "openai_compatible"}


def cached(usage: dict) -> tuple[int, int]:
    """Input tokens a call read from the provider's cache, and ones it wrote there (usage_metadata)"""
    details = usage.get("input_token_details") or {}
    return int(details.get("cache_read") or 0), int(details.get("cache_creation") or 0)


def call_cost(model: str, input_tokens: int, output_tokens: int, prices: dict, cache_read: int = 0,
              cache_write: int = 0) -> float:
    price = price_of(model, prices)
    return 0.0 if price is None else cost_at(price, input_tokens, output_tokens, cache_read, cache_write)


def cost_at(price: Price, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
    """Dollars for these tokens at this price; `input_tokens` counts the cached ones too"""
    uncached = max(input_tokens - cache_read - cache_write, 0)
    return (uncached * price[0] + output_tokens * price[1] + cache_read * price[2] + cache_write * price[3]) / 1_000_000


def of_stats(stats: list[dict], prices: dict) -> tuple[float, set[str]]:
    """The cost of these replies' stats, and the models among them without a price"""

    total, unpriced = 0.0, set()
    for reply in stats:
        model = reply.get("model") or ""
        if price_of(model, prices) is None:
            unpriced.add(model)
        total += call_cost(model, reply.get("input_tokens") or 0, reply.get("output_tokens") or 0, prices,
                           reply.get("cache_read_tokens") or 0, reply.get("cache_write_tokens") or 0)
    return total, unpriced


class Budget(BaseCallbackHandler):
    """Adds up what each model call of a reply costs, on top of what the session spent before,
    and stops the reply (through its stop event) once the limit is reached"""

    def __init__(self, limit: float, spent: float, model: str, prices: dict, stop_requested: threading.Event):
        self.limit = limit
        self.spent = spent
        self.model = model
        self.prices = prices
        self.stop_requested = stop_requested
        self.reached = spent >= limit

    def on_llm_end(self, response, **kwargs):
        for generations in response.generations:
            for generation in generations:
                usage = getattr(getattr(generation, "message", None), "usage_metadata", None) or {}
                self.spent += call_cost(self.model, usage.get("input_tokens", 0), usage.get("output_tokens", 0), self.prices,
                                        *cached(usage))
        if self.spent >= self.limit:
            self.reached = True
            self.stop_requested.set()
