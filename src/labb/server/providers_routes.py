"""Settings → Models (admins): the providers and the models people can use (see providers.py),
and what the models cost, for the usage stats (prices.py)."""

import os

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from labb import llm, usage
from labb.server import prices, providers
from labb.server.auth_routes import admin_user

router = APIRouter()


class ProviderFields(BaseModel):
    base_url: str | None = None
    # A new key; empty keeps the one there is
    api_key: str | None = None
    # Remove the saved key (a built-in provider goes back to .env's)
    clear_key: bool = False
    enabled: bool | None = None
    # Strict tool calls: true or false, or null to follow the default
    strict_tools: bool | None = None


class ModelSettings(BaseModel):
    default: str | None = None
    # Tokens to compact to; null for .env's CONTEXT_WINDOW or the model's own
    context_window: int | None = None
    # Tokens Ollama keeps in memory (num_ctx); null for .env's OLLAMA_NUM_CTX
    ollama_num_ctx: int | None = None
    offer: str | None = None
    chosen: list[dict] | None = None


@router.get("/admin/providers")
def list_providers(user: dict = Depends(admin_user)):
    return providers.listing()


@router.put("/admin/providers/{name}")
def save_provider(name: str, body: ProviderFields, user: dict = Depends(admin_user)):
    try:
        providers.save(name, body.model_dump(exclude_unset=True))
    except providers.ProviderError as e:
        raise HTTPException(400, str(e))
    return providers.listing()


@router.delete("/admin/providers/{name}", status_code=204)
def remove_provider(name: str, user: dict = Depends(admin_user)):
    if not providers.remove(name):
        raise HTTPException(404, "Nothing is saved for that provider")
    return Response(status_code=204)


@router.post("/admin/providers/{name}/test")
def test_provider(name: str, body: ProviderFields, user: dict = Depends(admin_user)):
    """Ask the provider for its models with these settings, without saving them"""
    return providers.test(name, body.model_dump(exclude_unset=True))


@router.get("/admin/models")
def get_models(user: dict = Depends(admin_user)):
    """The model settings, and every model the providers have, to choose from"""

    available = [f"ollama:{name}" for name in llm.ollama_models()] + llm.provider_models()
    settings = providers.model_settings()
    available += [c["spec"] for c in settings["chosen"]]
    return {**settings, "current_default": llm.default.spec, "current_window": llm.default.context_window,
            "env_window": int(window) if (window := os.getenv("CONTEXT_WINDOW", "").strip()).isdigit() else None,
            "current_num_ctx": llm.ollama_num_ctx, "env_num_ctx": llm.ENV_NUM_CTX,
            "available": list(dict.fromkeys(available))}


@router.put("/admin/models")
def save_models(body: ModelSettings, user: dict = Depends(admin_user)):
    try:
        providers.save_model_settings(body.model_dump(exclude_unset=True))
    except providers.ProviderError as e:
        raise HTTPException(400, str(e))
    return get_models(user)


class Prices(BaseModel):
    # {model or pattern: [input, output, cached input, cache write]}, dollars per million tokens
    prices: dict[str, list[float | None]]


class Suggest(BaseModel):
    models: list[str]


class Reprice(BaseModel):
    # Every reply at today's prices, not only the ones without a cost
    everything: bool = False


@router.get("/admin/prices")
def get_prices(user: dict = Depends(admin_user)):
    """The prices, and the models to price: the ones used so far and the ones offered"""

    offered = [c["spec"] for c in providers.model_settings()["chosen"]] + [llm.default.spec]
    return {"prices": prices.get(), "models": list(dict.fromkeys(prices.used_models() + offered)),
            # Earlier replies without a cost that the prices there are now could cost
            "fillable": prices.fillable()}


@router.put("/admin/prices")
def save_prices(body: Prices, user: dict = Depends(admin_user)):
    try:
        prices.save(body.prices)
    except prices.PriceError as e:
        raise HTTPException(400, str(e))
    return get_prices(user)


@router.post("/admin/prices/suggest")
def suggest_prices(body: Suggest, user: dict = Depends(admin_user)):
    """Prices for these models from LiteLLM's public list, to check before saving"""
    try:
        return {"suggestions": prices.suggest(body.models[:prices.MAX_PRICES]), "source": prices.SUGGESTIONS_URL}
    except ValueError as e:
        raise HTTPException(502, str(e))


@router.post("/admin/usage/reprice")
def reprice_usage(body: Reprice, user: dict = Depends(admin_user)):
    """Cost the replies recorded without one (or all of them) at the prices there are now"""
    return {"updated": usage.reprice(prices.get(), body.everything)}
