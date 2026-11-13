"""Integrations: apps people connect their accounts to (Google, Microsoft 365, GitHub, Notion), for
the tools that use them.

The server keeps the connections (server/connections.py) and sets `backend`; elsewhere (labb
on a PC) there's none, and the tools that need one aren't offered. A toolset that needs an app
names it in `CONNECTION`, and `tools.offered_for` leaves it out of runs whose user (the
chatter, or a task's or workflow's owner) hasn't connected that app.
"""

from typing import Protocol

import httpx

# For the apps' APIs; tests replace it
http = httpx.Client(timeout=httpx.Timeout(30, connect=10))

# How they're named to the model and the user
LABELS = {"google": "Google", "microsoft": "Microsoft 365", "github": "GitHub", "notion": "Notion"}


def label(app: str) -> str:
    return LABELS.get(app, app.title())


class AppError(Exception):
    """An app that isn't connected or can't be used, said for the model to pass on"""


class Backend(Protocol):
    def configured(self) -> set[str]:
        """The apps the admins have set up"""

    def connected(self, user_id: str) -> set[str]:
        """The apps this user has connected"""

    def token(self, user_id: str, app: str, refresh: bool = False) -> str:
        """A current access token; raises AppError"""


backend: Backend | None = None


def configured(app: str) -> bool:
    return backend is not None and app in backend.configured()


def user_of(config: dict | None) -> str | None:
    return ((config or {}).get("configurable") or {}).get("user_id")


def of(config: dict | None) -> frozenset[str]:
    """The apps the run's user has connected"""
    user_id = user_of(config)
    if backend is None or not user_id:
        return frozenset()
    try:
        return frozenset(backend.connected(user_id))
    except Exception:
        return frozenset()


def request(config: dict | None, app: str, method: str, url: str, **kwargs) -> httpx.Response:
    """An API call as the run's user, with their token (refreshed once if it's turned down)"""

    user_id = user_of(config)
    if backend is None or not user_id:
        raise AppError(f"{label(app)} can only be used in someone's chat on the server")
    headers = kwargs.pop("headers", None) or {}
    response = None
    for refresh in (False, True):
        token = backend.token(user_id, app, refresh=refresh)
        response = http.request(method, url, headers={**headers, "Authorization": f"Bearer {token}"}, **kwargs)
        if response.status_code != 401:
            break
    if response.status_code == 403 and "insufficient" in response.text.lower():
        raise AppError(f"{label(app)} didn't allow this ({problem(response)}): reconnect it in Settings → Integrations "
                       "and allow everything it asks for")
    return response


def problem(response: httpx.Response) -> str:
    """What an API call that failed said"""
    try:
        found = response.json()
        error = found.get("error")
        # Google's and Microsoft's {"error": {"message"}}, GitHub's and Notion's {"message"}
        message = error.get("message") if isinstance(error, dict) else error or found.get("message")
    except (ValueError, AttributeError):
        message = None
    return f"{response.status_code}: {message}" if message else f"it answered {response.status_code}"


def call(config: dict | None, app: str, method: str, url: str, **kwargs) -> dict:
    """request's JSON answer; raises AppError when it fails"""
    try:
        response = request(config, app, method, url, **kwargs)
    except httpx.HTTPError as e:
        raise AppError(f"couldn't reach {label(app)} ({e})")
    if response.status_code >= 400:
        raise AppError(f"{label(app)} said {problem(response)}")
    return response.json() if response.content else {}
