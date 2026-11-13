import functools
import os
import re
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable

import httpx
from langchain.chat_models import init_chat_model
from langchain_core.messages import BaseMessage, is_data_content_block
from langchain_ollama import ChatOllama

from labb import engine, remote


# MODEL is "<provider>:<model>", e.g. "openai:gpt-5.5" or "ollama:gemma4:e4b"
DEFAULT_MODEL = os.getenv("MODEL", "ollama:gemma4:e4b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OPENAI_URL = "https://api.openai.com/v1"
ANTHROPIC_URL = "https://api.anthropic.com"
# Ollama's cloud models, the provider "ollama-cloud": with OLLAMA_API_KEY (ollama.com → Keys) straight
# from ollama.com, without one through the Ollama server once it's signed in (`ollama signin`)
CLOUD = "ollama-cloud"
OLLAMA_CLOUD_URL = os.getenv("OLLAMA_CLOUD_URL", "https://ollama.com").strip().rstrip("/")
# speaches (speech on this machine, see compose.yaml): set, it's the provider "speaches"
SPEACHES_URL = os.getenv("SPEACHES_URL", "").strip().rstrip("/")
KINDS = ("ollama", "openai", "anthropic", "openai_compatible")
# Strict tool calls (the provider constrains the arguments to the schema): "auto" uses them
# with OpenAI and Anthropic, "on" also with OpenAI-compatible servers (vLLM, LM Studio…), "off" never
STRICT_TOOLS = os.getenv("STRICT_TOOLS", "auto").strip().lower()
STRICT_KINDS = ("openai", "anthropic", "openai_compatible")


@dataclass(frozen=True)
class Provider:
    """Where a spec's models come from: `name` is the spec's prefix ("openai" in "openai:gpt-5.5").
    `openai_compatible` is any server that speaks OpenAI's API (LM Studio, vLLM, OpenRouter…)."""

    name: str
    kind: str
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    enabled: bool = True
    # Strict tool calls: None follows STRICT_TOOLS; the server's Settings → Models can set it per provider
    strict_tools: bool | None = None


def uses_strict(kind: str, source: "Provider | None") -> bool:
    """Whether tool calls to this provider are strict (see STRICT_TOOLS)"""
    if kind not in STRICT_KINDS:
        return False
    if source is not None and source.strict_tools is not None:
        return source.strict_tools
    if STRICT_TOOLS in ("on", "off"):
        return STRICT_TOOLS == "on"
    return kind != "openai_compatible"


def env_providers() -> dict[str, Provider]:
    """The built-in providers as the environment sets them up"""
    found = {
        "ollama": Provider("ollama", "ollama", OLLAMA_HOST),
        "openai": Provider("openai", "openai", os.getenv("OPENAI_BASE_URL", OPENAI_URL).rstrip("/"),
                           os.getenv("OPENAI_API_KEY", "").strip()),
        "anthropic": Provider("anthropic", "anthropic", ANTHROPIC_URL, os.getenv("ANTHROPIC_API_KEY", "").strip()),
        CLOUD: Provider(CLOUD, "ollama", OLLAMA_CLOUD_URL, os.getenv("OLLAMA_API_KEY", "").strip()),
    }
    if SPEACHES_URL:
        found["speaches"] = Provider("speaches", "openai_compatible", SPEACHES_URL)
    return found


# The providers in use: the environment's, until the server sets its own (`configure`)
providers: dict[str, Provider] = env_providers()

# Set by the server: raises ValueError if the run's person may not use this model (admins-only models)
check_access: Callable[[str, dict], None] | None = None


def ollama_host() -> str:
    return providers["ollama"].base_url if "ollama" in providers else OLLAMA_HOST

def cloud_name(name: str) -> str:
    """ollama.com's name → the local server's: gpt-oss:120b → gpt-oss:120b-cloud, kimi-k2.6 → kimi-k2.6:cloud"""
    return f"{name}-cloud" if ":" in name else f"{name}:cloud"


def ollama_target(provider: str, name: str) -> tuple[str, str, dict[str, str]]:
    """Where an Ollama-kind model runs: the address, the model's name there, and the headers.
    A cloud model with a key goes to ollama.com; without one, to the Ollama server by its -cloud name."""

    source = providers.get(provider)
    if provider == CLOUD:
        if source is not None and source.api_key:
            return source.base_url or OLLAMA_CLOUD_URL, name, {"Authorization": f"Bearer {source.api_key}"}
        return ollama_host(), cloud_name(name), remote.headers()
    return (source.base_url if source else OLLAMA_HOST), name, remote.headers()


def is_ollama(provider: str) -> bool:
    source = providers.get(provider)
    return (source.kind if source else provider) == "ollama"


# The tokens Ollama keeps in memory for a model (its num_ctx): OLLAMA_NUM_CTX, else 32768.
# The terminal's settings and /num-ctx, and the server's Settings → Models, change it (set_num_ctx).
ENV_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "32768"))
ollama_num_ctx = ENV_NUM_CTX
MIN_NUM_CTX, MAX_NUM_CTX = 2_048, 1_048_576
# Called when the models are made again (set_num_ctx, configure), for caches of what was made
# with the old ones: model.py adds model_with_tools' cache_clear
on_reload: list = []
# How long a model call may go without sending anything before it's given up (and tried again,
# see model.call_model). Ollama's client would wait forever; it's quiet while it loads a model
# and reads a long prompt, which can take minutes on a CPU. OpenAI's and Anthropic's clients
# have the same 10 minutes of their own.
STALL_SECONDS = float(os.getenv("MODEL_STALL_SECONDS", "600"))
# For models whose context window LangChain doesn't know
FALLBACK_WINDOW = 128_000
# The server's Settings → Models can set the context window too, over CONTEXT_WINDOW (see configure)
context_window_setting: int | None = None


@functools.cache
def ollama_info(name: str, provider: str = "ollama") -> dict:
    """What Ollama says about a model: its `capabilities` and `context_length` ({} if unreachable)"""

    base, name, headers = ollama_target(provider, name)
    try:
        response = httpx.post(f"{base}/api/show", json={"model": name}, headers=headers, timeout=3)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return {}
    context = next((v for k, v in data.get("model_info", {}).items() if k.endswith(".context_length")), None)
    return {"capabilities": data.get("capabilities", []), "context_length": context}


@dataclass(frozen=True)
class Models:
    """A model for the replies, the one that writes summaries, and the context window"""

    spec: str
    provider: str
    name: str
    model: Any
    summary_model: Any
    # Token budget the conversation is compacted to stay within (see context.py)
    context_window: int
    # The provider's kind, whether its tool calls are strict, and (Ollama) a model for
    # constrained retries of broken tool calls (see tool_calls.py)
    kind: str = ""
    strict: bool = False
    constrained_model: Any = None


# --- What Ollama's adapter takes ------------------------------------------------------

# Another model's thinking, kept in its replies' content: not for this one
THINKING_BLOCKS = ("thinking", "redacted_thinking", "reasoning")


def _ollama_block(block) -> dict | str | None:
    """A content block as Ollama's adapter takes it (text, images), or None to leave it out.
    It refuses anything else, and a chat can have more: another provider's thinking from
    before the model was changed, a tool's resource, a file."""

    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return None
    kind = block.get("type")
    if kind in ("text", "tool_use", "image_url"):
        return block
    if kind == "image" and is_data_content_block(block) and (block.get("base64") or block.get("source_type") == "base64"):
        return block
    # Thinking isn't passed on, and tool calls go with the message's own tool_calls
    if kind in THINKING_BLOCKS or str(kind).startswith(("tool_call", "server_tool")):
        return None
    if isinstance(block.get("text"), str) and block["text"]:
        return {"type": "text", "text": block["text"]}
    what = block.get("mime_type") or kind or "content"
    return {"type": "text", "text": f"[{what} left out: this model can't read it]"}


def for_ollama(messages: list[BaseMessage]) -> list[BaseMessage]:
    """The messages with only content Ollama's adapter takes (_ollama_block)"""

    cleaned = []
    for message in messages:
        if isinstance(message.content, list):
            blocks = [b for b in map(_ollama_block, message.content) if b is not None]
            if blocks != message.content:
                message = message.model_copy(update={"content": blocks or ""})
        cleaned.append(message)
    return cleaned


class OllamaChat(ChatOllama):
    """Ollama's chat model, given only content it takes (for_ollama), whatever the chat holds"""

    def _convert_messages_to_ollama_messages(self, messages):
        return super()._convert_messages_to_ollama_messages(for_ollama(messages))


def is_openai_api(base_url: str | None) -> bool:
    """OpenAI's own API (not a server that only speaks its Chat Completions)"""
    return not base_url or "api.openai.com" in base_url


@functools.cache
def load(spec: str) -> Models:
    """The models for "<provider>:<model>". Raises ValueError if they can't be set up."""

    provider, sep, name = spec.partition(":")
    if not sep or not provider or not name:
        raise ValueError(f'"{spec}" isn\'t "<provider>:<model>", e.g. "ollama:gemma4:e4b" or "openai:gpt-5.5"')
    source = providers.get(provider)
    if source is not None and not source.enabled:
        raise ValueError(f"can't use {spec}: the {provider} provider is turned off")
    kind = source.kind if source else provider

    constrained_model = None
    try:
        if kind == "ollama":
            # Ollama's default context is too small to hold an article. Through the server
            # (OLLAMA_HOST is its /ollama), the API key goes with each request.
            # A cloud model goes to ollama.com with its key, or to the Ollama server by its -cloud name.
            base, there, headers = ollama_target(provider, name)
            ollama = dict(model_provider="ollama", base_url=base, num_ctx=ollama_num_ctx,
                          client_kwargs={"headers": headers, "timeout": httpx.Timeout(STALL_SECONDS, connect=15)})
            ollama.pop("model_provider")
            # Keep the model's thinking out of the reply text
            model = OllamaChat(model=there, reasoning=True, **ollama)
            # Summarizing needs no thinking, which can take minutes on a small model; cap the length too
            # (context.summarize sets it per summary, by the window)
            summary_model = OllamaChat(model=there, reasoning=False, num_predict=1024, **ollama)
            # Rewriting a broken tool call with `format` (a JSON schema): no thinking, no cap
            constrained_model = OllamaChat(model=there, reasoning=False, temperature=0, **ollama)
        elif kind in ("openai", "openai_compatible"):
            # A local server may not want a key; OpenAI's client wants one all the same.
            # OpenAI's own API through Responses: newer models take tools with a reasoning effort
            # only there, not through Chat Completions, which other servers speak
            responses = kind == "openai" and is_openai_api(source.base_url)
            model = init_chat_model(name, model_provider="openai", base_url=source.base_url or None,
                                    api_key=source.api_key or (None if kind == "openai" else "none"),
                                    stream_usage=True, use_responses_api=responses)
            summary_model = model
        elif kind == "anthropic":
            model = init_chat_model(name, model_provider="anthropic", base_url=source.base_url or None,
                                    api_key=source.api_key or None)
            summary_model = model
        else:
            # Another provider LangChain knows, with its key in the environment
            model = init_chat_model(name, model_provider=provider)
            summary_model = model
    except Exception as e:
        raise ValueError(f"can't use {spec}: {e}") from e

    # The server's setting or CONTEXT_WINDOW (set it lower to compact earlier), or the model's own window
    if context_window_setting:
        context_window = context_window_setting
    elif env_window := os.getenv("CONTEXT_WINDOW"):
        context_window = int(env_window)
    elif provider == CLOUD:
        # num_ctx is the Ollama server's memory: the cloud runs the model's whole window
        context_window = ollama_info(name, provider).get("context_length") or FALLBACK_WINDOW
    elif kind == "ollama":
        # Ollama only uses num_ctx tokens, even if the model could take more
        context_window = min(ollama_num_ctx, ollama_info(name, provider).get("context_length") or ollama_num_ctx)
    else:
        context_window = (getattr(model, "profile", None) or {}).get("max_input_tokens") or FALLBACK_WINDOW
    return Models(spec, provider, name, model, summary_model, context_window, kind,
                  uses_strict(kind, source), constrained_model)


default = load(DEFAULT_MODEL)
provider, model_name = default.provider, default.name
model, summary_model, context_window = default.model, default.summary_model, default.context_window


# --- What went wrong, in a line people can act on -----------------------------------------

# The usual failures of a model call, by what the provider says (lowercased) or its status code
CREDITS = ("credits", "subscribe", "insufficient_quota", "credit balance", "billing", "payment required", "quota")
BAD_KEY = ("api key", "api_key", "x-api-key", "unauthorized", "authentication", "permission denied", "invalid token")
RATE_LIMIT = ("rate limit", "rate_limit", "too many requests", "overloaded", "capacity", "try again later")
NOT_FOUND = ("not found", "does not exist", "no such model", "unknown model", "model_not_found")
UNREACHABLE = ("connection refused", "connecterror", "connection error", "failed to connect", "name or service not known",
               "timed out", "nodename nor servname", "all connection attempts failed")


def explain_error(error: Exception | str, spec: str) -> str:
    """A model call's error as one line saying what to do, with the provider's own words after it.
    Errors that aren't one of the usual ones come back as they are."""

    said = str(error).strip() or type(error).__name__
    text = f"{type(error).__name__} {said}".lower()
    status = getattr(error, "status_code", None) or getattr(getattr(error, "response", None), "status_code", None)
    provider, _, name = spec.partition(":")
    cloud = providers.get(CLOUD)
    keyless_cloud = provider == CLOUD and not (cloud and cloud.api_key)
    where = OLLAMA_CLOUD_HINT if provider == "ollama" and name.endswith("cloud") or keyless_cloud else ""
    if status == 402 or any(word in text for word in CREDITS):
        advice = f"{spec} can't answer: the account is out of credits or needs a plan{where}. Add credits, or switch to another model."
    elif status in (401, 403) or any(word in text for word in BAD_KEY):
        advice = f"{spec} refused the API key{where}. Check the key for {provider} (in .env, or Settings → Models on the server)."
    elif status == 429 or any(word in text for word in RATE_LIMIT):
        advice = f"{spec} is busy or rate-limited. Wait a little and try again, or switch to another model."
    elif status == 404 or any(word in text for word in NOT_FOUND) and "model" in text:
        advice = (f"{provider} has no model {name}." + (f" Pull it with `ollama pull {name}`, or pick another model."
                                                         if provider == "ollama" else " Pick another model."))
    elif any(word in text for word in UNREACHABLE):
        target = f" at {ollama_host()}" if provider == "ollama" else ""
        advice = f"Couldn't reach {provider}{target}. Check that it's running and the address is right."
    else:
        return said
    return f"{advice}\n({provider} said: {said[:300]})"


# Failures worth trying again: the provider is busy, or the connection broke or went quiet
TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}
TRANSIENT = (*RATE_LIMIT, *UNREACHABLE, "timeout", "connection reset", "connection aborted", "remoteprotocolerror",
             "server disconnected", "incomplete chunked read", "peer closed connection", "internal server error",
             "bad gateway", "service unavailable", "gateway timeout", "temporarily unavailable")


def is_transient(error: Exception) -> bool:
    """Whether a model call that failed this way may work if tried again in a moment. Running
    out of credits isn't (OpenAI says so with a 429), nor a bad key or model."""

    text = f"{type(error).__name__} {error}".lower()
    status = getattr(error, "status_code", None) or getattr(getattr(error, "response", None), "status_code", None)
    if status in (400, 401, 402, 403, 404, 422) or any(word in text for word in CREDITS + BAD_KEY):
        return False
    return status in TRANSIENT_STATUS or any(word in text for word in TRANSIENT)


def retry_after(error: Exception) -> float | None:
    """The seconds the provider asked to wait (Retry-After), if it said"""

    headers = getattr(getattr(error, "response", None), "headers", None) or {}
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


# Cloud models run through the Ollama server's ollama.com account
OLLAMA_CLOUD_HINT = " (an Ollama cloud model: the Ollama server's ollama.com account)"


def for_config(config: dict | None) -> Models:
    """The models of a run: the terminal's /model and the web page's model picker switch them"""

    configurable = (config or {}).get("configurable") or {}
    h = engine.of(config)
    spec = configurable.get("model") or h.model("model")
    if spec and check_access is not None:
        check_access(spec, configurable)
    models = load(spec) if spec else default
    if summary := h.model("summary_model"):
        if check_access is not None:
            check_access(summary, configurable)
        models = replace(models, summary_model=load(summary).summary_model)
    return models


# How much the model thinks before it answers: "auto" is the
# model's own default; configurable["answer_mode"] picks one for a run
ANSWER_MODES = {
    "fast": "answers right away, with little or no thinking",
    "auto": "the model decides how much to think",
    "thinking": "thinks longer, for hard questions",
}
# What each asks of models with effort levels (LangChain's profile `reasoning_effort_levels`):
# the first level the model has
EFFORTS = {"fast": ("none", "minimal", "low"), "thinking": ("xhigh", "high")}


def answer_mode(config: dict | None) -> str:
    mode = ((config or {}).get("configurable") or {}).get("answer_mode")
    # "auto" leaves it to the engine, if it says
    if mode in (None, "auto"):
        mode = engine.of(config).model("answer_mode")
    return mode if mode in ANSWER_MODES else "auto"


def mode_options(models: Models, mode: str) -> dict:
    """The call's arguments for an answer mode: Ollama turns thinking off for "fast" (it has
    no levels for most models); OpenAI's and Anthropic's reasoning models get an effort level.
    Models without a knob get none: the mode's prompt (model.MODE_PROMPTS) still applies."""

    if mode not in EFFORTS:
        return {}
    if models.kind == "ollama":
        return {"reasoning": False} if mode == "fast" else {}
    if models.kind not in ("openai", "anthropic"):
        return {}
    levels = (getattr(models.model, "profile", None) or {}).get("reasoning_effort_levels") or ()
    level = next((level for level in EFFORTS[mode] if level in levels), None)
    return {"reasoning_effort": level} if level else {}


def set_num_ctx(tokens: int | None):
    """Have Ollama models keep this many tokens in memory (None: OLLAMA_NUM_CTX) from now on:
    the models made with the old size are forgotten"""

    global ollama_num_ctx, default
    if tokens is not None and not MIN_NUM_CTX <= tokens <= MAX_NUM_CTX:
        raise ValueError(f"num_ctx is a number of tokens from {MIN_NUM_CTX:,} to {MAX_NUM_CTX:,}")
    ollama_num_ctx = tokens or ENV_NUM_CTX
    spec = default.spec
    load.cache_clear()
    for forget in on_reload:
        forget()
    try:
        default = load(spec)
    except ValueError:
        pass


def configure(new_providers: dict[str, Provider], default_spec: str | None = None, context_window: int | None = None,
              num_ctx: int | None = None):
    """Use these providers, this default model, context window (None: CONTEXT_WINDOW or the
    model's) and Ollama num_ctx (None: OLLAMA_NUM_CTX) from now on (the server's settings): the
    models and lists made with the old ones are forgotten. A default that can't be set up falls
    back to MODEL."""

    global default, providers, context_window_setting, ollama_num_ctx
    providers = dict(new_providers)
    context_window_setting = context_window
    ollama_num_ctx = num_ctx or ENV_NUM_CTX
    load.cache_clear()
    ollama_info.cache_clear()
    for forget in on_reload:
        forget()
    with _provider_lock:
        _provider_cache.clear()
    for spec in (default_spec, DEFAULT_MODEL):
        if not spec:
            continue
        try:
            default = load(spec)
            return
        except ValueError:
            continue
    # Neither can be set up now (a provider turned off): the one before stays


def tool_support(spec: str) -> bool | None:
    """Whether the model can call tools: Ollama says so per model (the `tools` capability);
    None if unknown, which is taken as yes"""

    provider, _, name = spec.partition(":")
    if not is_ollama(provider):
        return None
    info = ollama_info(name, provider)
    return "tools" in info["capabilities"] if info else None


def no_tools_warning(spec: str) -> str | None:
    if tool_support(spec) is False:
        return (f"{spec} can't call tools (Ollama doesn't list the tools capability): it can't read files, "
                "run commands or search. Pick a model with tool support for that.")
    return None


def vision(spec: str) -> bool | None:
    """Whether the model can see images: asked of Ollama, or from LangChain's model profile
    for other providers; None if unknown"""

    provider, _, name = spec.partition(":")
    if is_ollama(provider):
        info = ollama_info(name, provider)
        return "vision" in info["capabilities"] if info else None
    try:
        profile = getattr(load(spec).model, "profile", None) or {}
    except ValueError:
        return None
    return profile.get("image_inputs")


def ollama_models() -> list[str]:
    """The models the Ollama server has, or none if it can't be reached"""

    try:
        response = httpx.get(f"{ollama_host()}/api/tags", headers=remote.headers(), timeout=3)
        response.raise_for_status()
        return sorted(m["name"] for m in response.json().get("models", []))
    except (httpx.HTTPError, ValueError):
        return []


# --- OpenAI's and Anthropic's models, for the pickers ------------------------------------

# OpenAI's list has much more than chat models: speech, images, embeddings, dated snapshots
OPENAI_CHAT = re.compile(r"^(gpt-|o\d|chatgpt-)")
NOT_CHAT = re.compile(r"embed|tts|whisper|transcribe|audio|realtime|image|dall-e|moderation|search|instruct|codex|"
                      r"computer-use|babbage|davinci|sora|deep-research", re.I)
# Speech models that OpenAI-compatible servers (speaches, LocalAI…) list with their chat models
SPEECH_MODEL = re.compile(r"whisper|transcri|tts|kokoro|piper|parakeet|moonshine|silero", re.I)
DATED = re.compile(r"-\d{4}-\d{2}-\d{2}$|-\d{4}$")
MAX_PROVIDER_MODELS = 30
# How long a provider's list is kept, and a failure (a wrong key, no network)
PROVIDER_SECONDS = 3600
FAILED_SECONDS = 300

_provider_cache: dict[str, tuple[float, list[str]]] = {}
_provider_lock = threading.Lock()


def _openai(provider: Provider) -> list[str]:
    headers = {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}
    response = httpx.get(f"{provider.base_url.rstrip('/')}/models", headers=headers, timeout=5)
    response.raise_for_status()
    models = response.json().get("data", [])
    if provider.kind == "openai" and provider.base_url.rstrip("/") == OPENAI_URL:
        models = [m for m in models if OPENAI_CHAT.match(m["id"]) and not NOT_CHAT.search(m["id"]) and not DATED.search(m["id"])]
    else:
        models = [m for m in models if not SPEECH_MODEL.search(m["id"])]
    # Newest first
    models.sort(key=lambda m: m.get("created", 0), reverse=True)
    return [f"{provider.name}:{m['id']}" for m in models[:MAX_PROVIDER_MODELS]]


def _anthropic(provider: Provider) -> list[str]:
    response = httpx.get(f"{(provider.base_url or ANTHROPIC_URL).rstrip('/')}/v1/models", params={"limit": 100}, timeout=5,
                         headers={"x-api-key": provider.api_key, "anthropic-version": "2023-06-01"})
    response.raise_for_status()
    # Newest first already
    return [f"{provider.name}:{m['id']}" for m in response.json().get("data", [])][:MAX_PROVIDER_MODELS]


def _ollama(provider: Provider) -> list[str]:
    if provider.name == CLOUD:
        return [f"{CLOUD}:{name}" for name in _cloud_catalog(provider)]
    response = httpx.get(f"{provider.base_url.rstrip('/')}/api/tags", headers=remote.headers(), timeout=5)
    response.raise_for_status()
    return [f"{provider.name}:{m['name']}" for m in sorted(response.json().get("models", []), key=lambda m: m["name"])]


LISTS = {"ollama": _ollama, "openai": _openai, "openai_compatible": _openai, "anthropic": _anthropic}


def models_of(provider: Provider) -> list[str]:
    """A provider's chat models, asked now (to test it); raises httpx.HTTPError or ValueError"""
    return LISTS[provider.kind](provider)


def provider_models(only: list[Provider] | None = None) -> list[str]:
    """The chat models of every provider in use other than the Ollama server (whose list is ollama_models):
    asked of their APIs and kept for an hour. One without a key (a cloud one), or that refuses
    it or can't be reached, has none."""

    found = []
    for provider in only if only is not None else list(providers.values()):
        if not provider.enabled or provider.kind == "ollama" and provider.name != CLOUD:
            continue
        if provider.kind in ("openai", "anthropic") and not provider.api_key:
            continue
        with _provider_lock:
            at, specs = _provider_cache.get(provider.name, (0.0, None))
        if specs is None or time.monotonic() - at > (PROVIDER_SECONDS if specs else FAILED_SECONDS):
            try:
                specs = models_of(provider)
            except (httpx.HTTPError, ValueError, KeyError):
                specs = []
            with _provider_lock:
                _provider_cache[provider.name] = (time.monotonic(), specs)
        found += specs
    return found


# Where the list of Ollama's cloud models comes from ("" or "off": none are listed)
OLLAMA_CLOUD_CATALOG = os.getenv("OLLAMA_CLOUD_CATALOG", "https://ollama.com/api/tags")


def _cloud_catalog(provider: Provider) -> list[str]:
    """ollama.com's names of the cloud models that can use tools"""
    if OLLAMA_CLOUD_CATALOG in ("", "off"):
        return []
    headers = {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}
    response = httpx.get(OLLAMA_CLOUD_CATALOG, headers=headers, timeout=5)
    response.raise_for_status()
    return sorted(m["name"] for m in response.json().get("models", []) if "tools" in m.get("capabilities", ["tools"]))


def ollama_cloud_models() -> list[str]:
    """The cloud models' specs ("ollama-cloud:gpt-oss:120b"), from ollama.com's list and kept for an hour
    (a failure for 5 minutes); none while the Ollama Cloud provider is off"""

    source = providers.get(CLOUD)
    return provider_models([source]) if source is not None and source.enabled else []


def listed_models() -> list[str]:
    """MODELS from the environment: "openai:gpt-5.5,anthropic:claude-sonnet-5-5", for the pickers"""
    return [spec.strip() for spec in os.getenv("MODELS", "").split(",") if spec.strip()]
