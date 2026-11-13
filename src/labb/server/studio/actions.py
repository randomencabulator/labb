"""Custom actions, the server's own reusable steps:

- HTTP actions (only admins make them, for everyone): a request template with named inputs,
  e.g. "Create Todoist task" or "Home Assistant: turn on the lights". `{{name}}` fills in an
  input and `{{secrets.NAME}}` a secret. Private and local addresses are refused unless the
  action allows them (for a server on your own network), on every redirect too.
- Prompt actions (anyone): a saved prompt with its model and answer format.
"""

import json
import re
from urllib.parse import urljoin

import httpx

from labb import safe_http
from labb.server.studio import nodes, secrets, store
from labb.server.studio.template import TemplateError, render_text

KINDS = ("http", "prompt")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
MAX_NAME = 60
MAX_DESCRIPTION = 300
MAX_RESPONSE = 1_000_000
MAX_REDIRECTS = 3


def default_timeout() -> int:
    """Seconds an action waits when it doesn't set its own (Settings → Limits)"""
    from labb.server import limits
    return limits.get("http_seconds")

HEADER = re.compile(r"^[A-Za-z0-9-]{1,64}$")


class ActionError(ValueError):
    """An action that can't be saved or run, said for whoever made or ran it"""


def _inputs(config: dict) -> list[dict]:
    found, names = [], set()
    for item in config.get("inputs") or []:
        name = (item.get("name") or "").strip()
        if not nodes.KEY.match(name) or name in names or name == "secrets":
            raise ActionError(f"An input's name is lowercase letters, digits and underscores, and each once ({name!r})")
        names.add(name)
        found.append({"name": name, "label": str(item.get("label") or name)[:80],
                      "description": str(item.get("description") or "")[:300], "required": bool(item.get("required"))})
    return found


def clean(fields: dict, is_admin: bool) -> dict:
    """An action's fields as they're saved; raises ActionError"""

    kind = fields.get("kind")
    if kind not in KINDS:
        raise ActionError("An action is an HTTP request or a prompt")
    if kind == "http" and not is_admin:
        raise ActionError("Only admins can make HTTP actions: they reach outside the server, with its secrets")
    name = str(fields.get("name") or "").strip()
    if not name or len(name) > MAX_NAME:
        raise ActionError(f"An action needs a name of at most {MAX_NAME} characters")
    config = fields.get("config") or {}
    cleaned = {"inputs": _inputs(config)}
    if kind == "http":
        method = str(config.get("method") or "GET").upper()
        if method not in METHODS:
            raise ActionError(f"The method is one of {', '.join(METHODS)}")
        url = str(config.get("url") or "").strip()
        if not url.startswith(("http://", "https://")) and not url.startswith("{{"):
            raise ActionError("The URL starts with http:// or https://")
        headers = {}
        for key, value in (config.get("headers") or {}).items():
            if not HEADER.match(key):
                raise ActionError(f"{key!r} isn't a header name")
            headers[key] = str(value)
        cleaned |= {"method": method, "url": url, "headers": headers, "body": str(config.get("body") or ""),
                    "allow_private": bool(config.get("allow_private")),
                    "timeout": max(1, min(int(config.get("timeout") or default_timeout()), 120))}
    else:
        prompt = str(config.get("prompt") or "").strip()
        if not prompt:
            raise ActionError("A prompt action needs its prompt")
        cleaned |= {"prompt": prompt, "system": str(config.get("system") or ""), "model": config.get("model") or None,
                    "format": "json" if config.get("format") == "json" else "text", "fields": config.get("fields") or []}
    return {"kind": kind, "name": name, "emoji": str(fields.get("emoji") or ("🌐" if kind == "http" else "✨"))[:16],
            "description": str(fields.get("description") or "")[:MAX_DESCRIPTION], "config": cleaned,
            # HTTP actions are for everyone; a prompt action is shared if its owner says so
            "shared": True if kind == "http" else bool(fields.get("shared"))}


def visible(action_id: str, user_id: str) -> dict | None:
    action = store.get_action(action_id, user_id)
    return action if action and (action["mine"] or action["shared"]) else None


def for_listing(action: dict, is_admin: bool) -> dict:
    """An action as the API shows it: an HTTP action's request only to admins"""

    if action["kind"] == "http" and not is_admin:
        return {**action, "config": {"inputs": action["config"].get("inputs", [])}}
    return action


def arguments(action: dict, args: dict) -> dict:
    """The inputs a step gives an action, checked against the ones it asks for"""

    wanted = {i["name"]: i for i in action["config"].get("inputs", [])}
    missing = [n for n, i in wanted.items() if i["required"] and (args.get(n) in (None, ""))]
    if missing:
        raise ActionError(f"{action['name']} needs {', '.join(missing)}")
    return {name: args.get(name, "") for name in wanted}


# --- HTTP ----------------------------------------------------------------------------

def _check_address(url: str, allow_private: bool) -> tuple[str, dict, dict]:
    """The request aimed at the address checked (safe_http.pin), so the name can't change in between"""
    try:
        return safe_http.pin(url, allow_private)
    except safe_http.Blocked as e:
        message = str(e)
        if message.endswith("is a private or local address"):
            message += "; an admin can allow that for this action"
        raise ActionError(message) from e


def run_http(action: dict, args: dict, secret_values: dict | None = None) -> tuple[dict, dict]:
    """Send the action's request; (output, what to record, with secrets masked)"""

    config = action["config"]
    secret_values = secrets.values() if secret_values is None else secret_values
    context = {**arguments(action, args), "secrets": secret_values}
    try:
        url = render_text(config["url"], context)
        headers = {k: render_text(v, context) for k, v in config.get("headers", {}).items()}
        body = render_text(config.get("body") or "", context)
    except TemplateError as e:
        raise ActionError(str(e)) from e
    recorded = secrets.mask({"method": config["method"], "url": url, "headers": headers, "body": body[:2000]}, secret_values)
    content = body.encode() if body and config["method"] not in ("GET", "DELETE") else None
    if content and "content-type" not in {k.lower() for k in headers}:
        headers["Content-Type"] = "application/json" if body.lstrip()[:1] in ("{", "[") else "text/plain"
    with httpx.Client(timeout=config.get("timeout") or default_timeout(), follow_redirects=False) as client:
        method = config["method"]
        for _ in range(MAX_REDIRECTS + 1):
            target, pinned, extensions = _check_address(url, config.get("allow_private", False))
            with client.stream(method, target, headers={**headers, **pinned}, content=content, extensions=extensions) as response:
                if response.is_redirect and "location" in response.headers:
                    url = urljoin(url, response.headers["location"])
                    if response.status_code in (301, 302, 303):
                        method, content = "GET", None
                    continue
                raw = b""
                for chunk in response.iter_bytes():
                    raw += chunk
                    if len(raw) > MAX_RESPONSE:
                        raise ActionError(f"{action['name']}: the answer is over {MAX_RESPONSE // 1000} KB")
                text = raw.decode(response.encoding or "utf-8", errors="replace")
                try:
                    parsed = json.loads(text) if text.strip() else None
                except ValueError:
                    parsed = text
                output = {"status": response.status_code, "body": parsed}
                if response.status_code >= 400:
                    raise ActionError(secrets.mask(f"{action['name']}: {response.status_code} {str(text)[:300]}", secret_values))
                return secrets.mask(output, secret_values), recorded
        raise ActionError(f"{action['name']}: too many redirects")


def render_prompt_config(action: dict, args: dict) -> dict:
    """A prompt action as a prompt step's settings, with its inputs filled in"""

    context = arguments(action, args)
    config = action["config"]
    try:
        return {"prompt": render_text(config["prompt"], context), "system": render_text(config.get("system") or "", context),
                "model": config.get("model"), "format": config.get("format", "text"), "fields": config.get("fields") or []}
    except TemplateError as e:
        raise ActionError(str(e)) from e

