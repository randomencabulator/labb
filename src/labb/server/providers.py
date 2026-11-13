"""Models and providers, managed by admins in Settings → Models.

- Providers: the built-in ones (Ollama, Ollama Cloud, OpenAI, Anthropic) and any number of custom ones that
  speak OpenAI's API (LM Studio, vLLM, llama.cpp, OpenRouter, Groq…), each `<name>:<model>`.
  `.env` sets the built-in ones up (OLLAMA_HOST, OPENAI_API_KEY, OPENAI_BASE_URL,
  ANTHROPIC_API_KEY, OLLAMA_API_KEY); what's saved here wins, field by field, and removing it goes
  back to `.env`. The cloud ones start turned off, keys or not, until an admin turns them on (or
  MODEL is one of theirs).
  Keys are encrypted with Studio's secret key (studio/secrets.py) and never sent back.
- Models: the default, and which ones the pickers offer: all of the providers' models, or a
  chosen list with labels, some for admins only. MODEL and MODELS are where a fresh server starts.

`apply()` hands the providers and the default to `llm` (no restart needed), and `check` is
`llm.check_access`: members can't use admins-only models, nor ones the chosen list leaves out.
"""

import dataclasses
import json
import os
import re
import threading

from labb import llm
from labb.chats import conn, lock
from labb.server import auth
from labb.server.studio import secrets
from labb.server.studio.store import now

NAME = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
BUILT_IN = ("ollama", llm.CLOUD, "openai", "anthropic")
# Off until an admin turns them on
OFF_AT_FIRST = (llm.CLOUD, "openai", "anthropic")
ENV_URLS = {"ollama": "OLLAMA_HOST", llm.CLOUD: "OLLAMA_CLOUD_URL", "openai": "OPENAI_BASE_URL"}
ENV_KEYS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", llm.CLOUD: "OLLAMA_API_KEY"}
MAX_LABEL = 60


def _limit(key: str) -> int:
    # limits imports this (for its table) when it first reads, so not at the top
    from labb.server import limits
    return limits.get(key)
OFFER = ("all", "chosen")
# The context window an admin can set, in tokens
MIN_WINDOW, MAX_WINDOW = 1_000, 10_000_000

with lock, conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS providers ("
        "name TEXT PRIMARY KEY, kind TEXT NOT NULL, base_url TEXT, api_key TEXT, enabled INTEGER NOT NULL DEFAULT 1, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS server_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

_settings: dict | None = None
_settings_lock = threading.Lock()


class ProviderError(ValueError):
    """Settings that can't be saved, said for the admin"""


# --- Providers ---------------------------------------------------------------------------

def _rows() -> dict[str, dict]:
    with lock:
        rows = conn.execute("SELECT name, kind, base_url, api_key, enabled FROM providers").fetchall()
    return {name: {"kind": kind, "base_url": url, "api_key": key, "enabled": bool(enabled)}
            for name, kind, url, key, enabled in rows}


def _decrypt(token: str | None) -> str:
    if not token:
        return ""
    try:
        return secrets.fernet().decrypt(token.encode()).decode()
    except Exception:
        return ""


def strict_settings() -> dict[str, bool]:
    """Strict tool calls set per provider (the others follow STRICT_TOOLS, see llm.uses_strict)"""
    with lock:
        row = conn.execute("SELECT value FROM server_settings WHERE key = 'strict_tools'").fetchone()
    try:
        saved = json.loads(row[0]) if row else {}
    except ValueError:
        return {}
    return {name: value for name, value in saved.items() if isinstance(value, bool)} if isinstance(saved, dict) else {}


def _save_strict(name: str, value: bool | None):
    saved = strict_settings()
    if value is None:
        saved.pop(name, None)
    else:
        saved[name] = bool(value)
    with lock, conn:
        conn.execute("INSERT INTO server_settings (key, value) VALUES ('strict_tools', ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (json.dumps(saved),))


def on_at_first(name: str) -> bool:
    """Whether a built-in provider is on before an admin says: the cloud ones only when MODEL is theirs"""
    return name not in OFF_AT_FIRST or llm.DEFAULT_MODEL.partition(":")[0] == name


def effective() -> dict[str, llm.Provider]:
    """The providers as they're used: `.env`'s, with what's saved here on top"""

    env = llm.env_providers()
    found = {name: dataclasses.replace(p, enabled=on_at_first(name)) if name in BUILT_IN else p for name, p in env.items()}
    for name, row in _rows().items():
        if name in BUILT_IN:
            base = env[name]
            found[name] = llm.Provider(name, base.kind, row["base_url"] or base.base_url,
                                       _decrypt(row["api_key"]) or base.api_key, row["enabled"])
        else:
            found[name] = llm.Provider(name, "openai_compatible", row["base_url"] or "", _decrypt(row["api_key"]), row["enabled"])
    for name, strict in strict_settings().items():
        if name in found:
            found[name] = dataclasses.replace(found[name], strict_tools=strict)
    return found


def _from(name: str, saved: bool, variable: str | None) -> str:
    if saved:
        return "saved"
    return ".env" if variable and os.getenv(variable, "").strip() else "default"


def listing() -> list[dict]:
    """The providers for the admin's page: where each setting comes from, and never the keys"""

    rows = _rows()
    out = []
    for name, provider in effective().items():
        row = rows.get(name) or {}
        built_in = name in BUILT_IN
        out.append({
            "name": name, "kind": provider.kind, "built_in": built_in, "enabled": provider.enabled,
            "base_url": provider.base_url,
            "base_url_from": _from(name, bool(row.get("base_url")), ENV_URLS.get(name)) if built_in
            else "saved" if row.get("base_url") else ".env",
            "key_set": bool(provider.api_key),
            "key_from": (_from(name, bool(row.get("api_key")), ENV_KEYS.get(name)) if built_in else "saved")
            if provider.api_key else None,
            "needs_key": provider.kind in ("openai", "anthropic"),
            "takes_key": provider.kind != "ollama" or name == llm.CLOUD,
            # Strict tool calls: null when it follows the default (`strict_default`)
            "can_be_strict": provider.kind in llm.STRICT_KINDS,
            "strict_tools": provider.strict_tools,
            "strict_default": llm.uses_strict(provider.kind, None),
        })
    order = {n: i for i, n in enumerate(BUILT_IN)}
    return sorted(out, key=lambda p: (order.get(p["name"], len(order)), p["name"]))


def _clean_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        raise ProviderError("An address starts with http:// or https://")
    return url


def save(name: str, fields: dict):
    """Add or change a provider. `api_key` sets the key ("" keeps it), `clear_key` removes the
    saved one, `base_url` "" goes back to .env's or the default (built-in ones)."""

    name = name.strip().lower()
    rows = _rows()
    built_in = name in BUILT_IN
    if not built_in:
        if not NAME.match(name):
            raise ProviderError("A provider's name is lowercase letters, digits, - and _, like lmstudio or openrouter")
        if name not in rows and len([n for n in rows if n not in BUILT_IN]) >= (most := _limit("providers")):
            raise ProviderError(f"At most {most} custom providers")
    row = rows.get(name) or {"kind": llm.env_providers()[name].kind if built_in else "openai_compatible",
                             "base_url": None, "api_key": None, "enabled": on_at_first(name)}
    if "base_url" in fields:
        row["base_url"] = _clean_url(fields["base_url"]) or None
    if not built_in and not row["base_url"] and name in (env := llm.env_providers()):
        # One the environment sets up (speaches): saving it keeps its address
        row["base_url"] = env[name].base_url
    if not built_in and not row["base_url"]:
        raise ProviderError("A custom provider needs its address, e.g. http://192.168.1.20:1234/v1")
    if fields.get("api_key"):
        row["api_key"] = secrets.fernet().encrypt(str(fields["api_key"]).strip().encode()).decode()
    if fields.get("clear_key"):
        row["api_key"] = None
    if "enabled" in fields:
        row["enabled"] = bool(fields["enabled"])
    if "strict_tools" in fields:
        if row["kind"] not in llm.STRICT_KINDS:
            raise ProviderError("Ollama has no strict tool calls; labb rewrites its broken calls instead")
        _save_strict(name, fields["strict_tools"])
    at = now()
    with lock, conn:
        conn.execute(
            "INSERT INTO providers (name, kind, base_url, api_key, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (name) DO UPDATE SET base_url = excluded.base_url, api_key = excluded.api_key, "
            "enabled = excluded.enabled, updated_at = excluded.updated_at",
            (name, row["kind"], row["base_url"], row["api_key"], int(row["enabled"]), at, at),
        )
    apply()


def remove(name: str) -> bool:
    """Delete a custom provider, or take a built-in one back to .env"""

    with lock, conn:
        gone = conn.execute("DELETE FROM providers WHERE name = ?", (name,)).rowcount > 0
    if name in strict_settings():
        _save_strict(name, None)
        gone = True
    apply()
    return gone


def test(name: str, fields: dict) -> dict:
    """Ask a provider for its models with these settings (the saved key if none is given)"""

    current = effective().get(name)
    kind = current.kind if current else "openai_compatible"
    try:
        base = _clean_url(fields.get("base_url", "")) or (current.base_url if current else "")
    except ProviderError as e:
        return {"ok": False, "error": str(e)}
    key = str(fields.get("api_key") or "").strip() or (current.api_key if current else "")
    provider = llm.Provider(name or "test", kind, base, key)
    if not base:
        return {"ok": False, "error": "It needs an address"}
    try:
        models = llm.models_of(provider)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    return {"ok": True, "models": models[:200], "count": len(models)}


# --- Models -----------------------------------------------------------------------------

def _env_settings() -> dict:
    listed = llm.listed_models()
    return {"default": None, "context_window": None, "ollama_num_ctx": None, "offer": "chosen" if listed else "all",
            "chosen": [{"spec": s, "label": "", "admins_only": False} for s in listed]}


def model_settings() -> dict:
    global _settings
    with _settings_lock:
        if _settings is None:
            with lock:
                row = conn.execute("SELECT value FROM server_settings WHERE key = 'models'").fetchone()
            # Settings saved before the context window and num_ctx were ones have none
            _settings = {"context_window": None, "ollama_num_ctx": None, **json.loads(row[0])} if row else _env_settings()
        return json.loads(json.dumps(_settings))


def save_model_settings(fields: dict) -> dict:
    current = model_settings()
    if "default" in fields:
        spec = (fields["default"] or "").strip() or None
        if spec:
            try:
                llm.load(spec)
            except ValueError as e:
                raise ProviderError(f"The default model can't be used: {e}")
        current["default"] = spec
    if "context_window" in fields:
        window = fields["context_window"]
        if window is not None and not (isinstance(window, int) and MIN_WINDOW <= window <= MAX_WINDOW):
            raise ProviderError(f"The context window is a number of tokens from {MIN_WINDOW:,} to {MAX_WINDOW:,}")
        current["context_window"] = window
    if "ollama_num_ctx" in fields:
        num_ctx = fields["ollama_num_ctx"]
        if num_ctx is not None and not (isinstance(num_ctx, int) and llm.MIN_NUM_CTX <= num_ctx <= llm.MAX_NUM_CTX):
            raise ProviderError(f"Ollama's context size is a number of tokens from {llm.MIN_NUM_CTX:,} to {llm.MAX_NUM_CTX:,}")
        current["ollama_num_ctx"] = num_ctx
    if "offer" in fields:
        if fields["offer"] not in OFFER:
            raise ProviderError("Offer all the models, or the chosen ones")
        current["offer"] = fields["offer"]
    if "chosen" in fields:
        chosen, seen = [], set()
        for item in fields["chosen"] or []:
            spec = str(item.get("spec") or "").strip()
            if ":" not in spec or spec in seen:
                continue
            seen.add(spec)
            chosen.append({"spec": spec, "label": str(item.get("label") or "").strip()[:MAX_LABEL],
                           "admins_only": bool(item.get("admins_only"))})
        if len(chosen) > (most := _limit("chosen_models")):
            raise ProviderError(f"At most {most} models")
        current["chosen"] = chosen
    with lock, conn:
        conn.execute("INSERT INTO server_settings (key, value) VALUES ('models', ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (json.dumps(current),))
    global _settings
    with _settings_lock:
        _settings = current
    apply()
    return model_settings()


def apply():
    """Use the providers and default model as they're set now"""
    settings = model_settings()
    llm.configure(effective(), settings["default"], settings["context_window"], settings["ollama_num_ctx"])


# --- Who can use what --------------------------------------------------------------------

def allowed(spec: str, role: str) -> bool:
    if role == "admin" or spec == llm.default.spec:
        return True
    settings = model_settings()
    entry = next((c for c in settings["chosen"] if c["spec"] == spec), None)
    if entry is not None:
        return not entry["admins_only"]
    return settings["offer"] == "all"


def why_not(spec: str, role: str) -> str | None:
    if allowed(spec, role):
        return None
    entry = next((c for c in model_settings()["chosen"] if c["spec"] == spec), None)
    return f"{spec} is for admins only" if entry else f"{spec} isn't one of the models offered on this server"


def check(spec: str, configurable: dict):
    """llm.check_access: refuse a model the run's person may not use"""

    user_id = configurable.get("user_id")
    if not user_id:
        return
    user = auth.get_user(user_id)
    if user and (reason := why_not(spec, user["role"])):
        raise ValueError(reason)


def label_of(spec: str) -> str:
    entry = next((c for c in model_settings()["chosen"] if c["spec"] == spec), None)
    return (entry or {}).get("label") or spec.partition(":")[2]


def offered(role: str) -> list[str]:
    """The specs the pickers show this person: the default first"""

    settings = model_settings()
    if settings["offer"] == "chosen":
        specs = [c["spec"] for c in settings["chosen"]]
    else:
        # provider_models has Ollama Cloud's, when it's on
        specs = [f"ollama:{name}" for name in llm.ollama_models()] + llm.provider_models()
        specs += [c["spec"] for c in settings["chosen"]]
    specs = [llm.default.spec, *specs]
    return [s for s in dict.fromkeys(specs) if allowed(s, role)]
