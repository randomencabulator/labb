"""What the inbox learns by looking rather than being told: a thread in the server that every minute
asks the spawner how people's apps are, and every HEALTH_MINUTES runs Settings → Health's checks
of the services labb depends on.

- An app that crashed, keeps crashing, ran out of memory or was stopped for its data tells its
  owner, once per change (an app stopped for being idle, or by its owner, doesn't), and so does
  a page that stopped building on its own after a change to its files.
- A service that stops answering tells the admins, and so does it coming back (in the place of
  the first, if that's still unread: inbox.py's keys).

What it saw last is kept in memory: the first look after a start only takes note, so a restart
doesn't repeat what was said before it.
"""

import logging
import threading
import time

import httpx

from labb.server import auth, health, inbox

log = logging.getLogger(__name__)

TICK_SECONDS = 60
HEALTH_MINUTES = 5
# The checks of services that can go down while labb runs (not the database it runs on, or settings)
HEALTH_CHECKS = (health.ollama, health.crawler, health.python_sandbox, health.mcp_servers)
# An app's `detail` (the spawner's `detail()`) that's worth telling its owner about
APP_TROUBLE = ("Crashed", "Keeps crashing", "Ran out of memory", "Stopped: its")

_started = False
# app id → (status, detail) and app id → the page build last seen
_apps: dict[str, tuple[str, str]] | None = None
_pages: dict[str, int] = {}
# A check's name → whether it was working
_health: dict[str, bool] | None = None


def _trouble(detail: str) -> bool:
    return detail.startswith(APP_TROUBLE)


def _what_happened(status: str, detail: str) -> str:
    if status == "restarting" or detail.startswith("Keeps crashing"):
        return "keeps crashing"
    if detail.startswith("Ran out of memory"):
        return "ran out of memory"
    if detail.startswith("Stopped: its"):
        return "was stopped"
    return "crashed and was restarted" if status == "running" else "crashed"


def check_apps(listed: dict[str, list[dict]]) -> list[tuple[str, dict]]:
    """Compare people's apps (owner → the spawner's apps) with the last look; what to tell, as
    (owner, inbox.add's fields). The first look only takes note."""

    global _apps
    first, seen, told = _apps is None, {}, []
    before = _apps or {}
    for owner, apps in listed.items():
        for app in apps:
            app_id, name = app.get("id", ""), app.get("name") or "Your app"
            state = (app.get("status", ""), app.get("detail") or "")
            seen[app_id] = state
            link = f"/apps/{app_id}"
            if not first and state != before.get(app_id) and _trouble(state[1]):
                told.append((owner, {"kind": "app", "title": f"{name} {_what_happened(*state)}",
                                     "body": state[1], "link": link, "key": f"app:{app_id}:state", "level": "error"}))
            page = app.get("page_build") or {}
            build = page.get("build") or 0
            if not first and app_id in _pages and build > _pages[app_id] and page.get("ok") is False:
                told.append((owner, {"kind": "app", "title": f"{name}'s page didn't build",
                                     "body": page.get("error") or "See its Build tab", "link": link,
                                     "key": f"app:{app_id}:page", "level": "error"}))
            if build:
                _pages[app_id] = build
    _apps = seen
    return told


def check_health(results: list[dict]) -> list[dict]:
    """Compare Health's results with the last look; inbox.add's fields for the admins. The first
    look only tells of what isn't working (a restart while it's down says so again, unless the
    one before is still unread: the same key)."""

    global _health
    first, told = _health is None, []
    before = _health or {}
    now = {}
    for result in results:
        name, ok = result["name"], result["ok"]
        if ok is None:
            # A warning: not down, so it doesn't change what was said
            if name in before:
                now[name] = before[name]
            continue
        now[name] = ok
        key = f"health:{name}"
        if ok is False and (first or before.get(name, True)):
            told.append({"kind": "health", "title": f"{name} isn't working", "body": result["detail"],
                         "link": "/settings/health", "key": key, "level": "error"})
        elif ok is True and before.get(name) is False:
            told.append({"kind": "health", "title": f"{name} is working again", "body": result["detail"],
                         "link": "/settings/health", "key": key, "level": "success"})
    _health = now
    return told


def _list_apps() -> dict[str, list[dict]] | None:
    """Everyone's apps from the spawner; None without one, or when it doesn't answer"""

    from labb.server import apps
    if not apps.SPAWNER_URL:
        return None
    headers = {"Authorization": f"Bearer {token}"} if (token := apps.token()) else {}
    found = {}
    try:
        with httpx.Client(timeout=10, headers=headers) as client:
            for user in auth.list_users():
                if user["disabled"]:
                    continue
                response = client.get(f"{apps.SPAWNER_URL}/apps", params={"owner": user["id"]})
                response.raise_for_status()
                found[user["id"]] = response.json()
    except (httpx.HTTPError, ValueError) as e:
        log.info("The spawner didn't say how the apps are: %s", e)
        return None
    return found


def _health_results() -> list[dict]:
    results = []
    for probe in HEALTH_CHECKS:
        try:
            results.extend(probe())
        except Exception as e:
            results.append(health.check(probe.__name__.replace("_", " ").capitalize(), False, f"The check failed: {e}"))
    return results


def tick(count: int):
    listed = _list_apps()
    if listed is not None:
        for owner, fields in check_apps(listed):
            inbox.add(owner, **fields)
    if count % HEALTH_MINUTES == 0:
        for fields in check_health(_health_results()):
            inbox.add_admins(**fields)


def _loop():
    count = 0
    while True:
        try:
            tick(count)
        except Exception:
            log.exception("Watching the apps and services failed")
        count += 1
        time.sleep(TICK_SECONDS)


def start():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="inbox-watch").start()
