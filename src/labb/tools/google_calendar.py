"""The user's Google Calendar, once they've connected Google (see connections.py).

Times without an offset are in the calendar's own time zone, which every answer names, so a
model can say "tomorrow at 3" as 2026-10-30T15:00. A date alone is a whole day."""

import json
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig

from labb import connections
from labb.connections import AppError

API = "https://www.googleapis.com/calendar/v3"
MAX_EVENTS = 50
DESCRIPTION_CHARS = 500
# A calendar's time zone, kept a while: (user, calendar) -> (zone, when it was looked up)
ZONE_SECONDS = 600
_zones: dict[tuple[str, str], tuple[str, float]] = {}

CONNECTION = "google"
# Over the engine's tool_share of the window, these wait to be found (tools/tool_search.py)
DEFERRABLE = True


def enabled() -> bool:
    return connections.configured(CONNECTION)


def _call(config, method: str, path: str, **kwargs) -> dict:
    return connections.call(config, CONNECTION, method, API + path, **kwargs)


def _cal(calendar: str | None) -> str:
    return quote(calendar or "primary", safe="")


def _zone(config, calendar: str) -> ZoneInfo:
    key = (connections.user_of(config) or "", calendar)
    found = _zones.get(key)
    if not found or time.monotonic() - found[1] > ZONE_SECONDS:
        # From the calendar list: /calendars/{id} needs more than the scopes labb asks for
        found = _call(config, "GET", f"/users/me/calendarList/{_cal(calendar)}").get("timeZone") or "UTC", time.monotonic()
        _zones[key] = found
    try:
        return ZoneInfo(found[0])
    except Exception:
        return ZoneInfo("UTC")


def _parse(value: str, zone: ZoneInfo) -> date | datetime:
    """A date (a whole day), or a time in the calendar's zone unless it says its own"""
    value = value.strip()
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        moment = datetime.fromisoformat(value.replace(" ", "T"))
    except ValueError:
        raise AppError(f"`{value}` isn't a date (2026-10-30) or a time (2026-10-30T15:00)")
    return moment if moment.tzinfo else moment.replace(tzinfo=zone)


def _when(value: date | datetime, zone: ZoneInfo) -> dict:
    if isinstance(value, datetime):
        return {"dateTime": value.isoformat(), "timeZone": str(zone)}
    return {"date": value.isoformat()}


def _times(start: str, end: str | None, zone: ZoneInfo) -> tuple[dict, dict]:
    begins = _parse(start, zone)
    if isinstance(begins, datetime):
        ends = _parse(end, zone) if end else begins + timedelta(hours=1)
        if not isinstance(ends, datetime):
            raise AppError("Give the end as a time too, like the start")
    else:
        ends = _parse(end, zone) if end else begins
        if isinstance(ends, datetime):
            raise AppError("Give the end as a date too, like the start")
        # Google's end date is the day after; the model's is the last day
        ends += timedelta(days=1)
    if ends <= begins:
        raise AppError("The end has to be after the start")
    return _when(begins, zone), _when(ends, zone)


def _shown(event: dict) -> dict:
    start, end = event.get("start") or {}, event.get("end") or {}
    shown = {
        "id": event.get("id"),
        "title": event.get("summary") or "(no title)",
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
    }
    if "date" in end:
        # Back to the last day, as the tools take it
        shown["end"] = (date.fromisoformat(end["date"]) - timedelta(days=1)).isoformat()
        shown["all_day"] = True
    for key in ("location", "status"):
        if event.get(key) and not (key == "status" and event[key] == "confirmed"):
            shown[key] = event[key]
    if text := event.get("description"):
        shown["description"] = text[:DESCRIPTION_CHARS] + ("…" if len(text) > DESCRIPTION_CHARS else "")
    if attendees := event.get("attendees"):
        shown["attendees"] = [a.get("email") for a in attendees if a.get("email")]
    if event.get("htmlLink"):
        shown["link"] = event["htmlLink"]
    return shown


def _run(work) -> str:
    try:
        return json.dumps(work(), ensure_ascii=False)
    except AppError as e:
        return f"Error: {e}"


@tool
def list_calendars(config: RunnableConfig = None) -> str:
    """List the user's calendars: their ids, names and time zones. Only needed for a calendar
    other than their main one."""

    def work():
        found = _call(config, "GET", "/users/me/calendarList", params={"maxResults": 100}).get("items", [])
        return [{"id": c.get("id"), "name": c.get("summaryOverride") or c.get("summary"), "time_zone": c.get("timeZone"),
                 **({"primary": True} if c.get("primary") else {}), **({"read_only": True} if c.get("accessRole") in ("reader", "freeBusyReader") else {})}
                for c in found]

    return _run(work)


@tool
def list_events(start: str | None = None, end: str | None = None, query: str | None = None, max_results: int = 20,
                calendar: str = "primary", config: RunnableConfig = None) -> str:
    """List the events in the user's calendar between two times, soonest first.

    Returns JSON with the calendar's `time_zone`, `now` there, and `events` (each with `id`,
    `title`, `start`, `end`, and `location`, `description`, `attendees` when set).

    Args:
        start: From when, as a date (2026-10-30) or a time (2026-10-30T09:00); now if not given
        end: Until when; a week after the start if not given. A date includes that whole day
        query: Only events with these words (in the title, description, place or people)
        max_results: How many events at most (1-50)
        calendar: A calendar id from list_calendars; the user's main calendar if not given
    """

    def work():
        zone = _zone(config, calendar)
        now = datetime.now(zone).replace(microsecond=0)
        begins = _parse(start, zone) if start else now
        if not isinstance(begins, datetime):
            begins = datetime.combine(begins, datetime.min.time(), zone)
        ends = _parse(end, zone) if end else begins + timedelta(days=7)
        if not isinstance(ends, datetime):
            ends = datetime.combine(ends + timedelta(days=1), datetime.min.time(), zone)
        params = {"timeMin": begins.isoformat(), "timeMax": ends.isoformat(), "singleEvents": "true", "orderBy": "startTime",
                  "maxResults": max(1, min(max_results, MAX_EVENTS)), "timeZone": str(zone)}
        if query:
            params["q"] = query
        found = _call(config, "GET", f"/calendars/{_cal(calendar)}/events", params=params).get("items", [])
        return {"time_zone": str(zone), "now": now.isoformat(), "events": [_shown(e) for e in found]}

    return _run(work)


@tool
def create_event(title: str, start: str, end: str | None = None, description: str | None = None, location: str | None = None,
                 calendar: str = "primary", config: RunnableConfig = None) -> str:
    """Add an event to the user's calendar. No one is invited.

    Args:
        title: What it is, e.g. "Dentist"
        start: A time (2026-10-30T15:00) in the calendar's time zone, or a date (2026-10-30) for a whole day
        end: When it ends, the same way; an hour after a time, or the same day for a date, if not given
        description: Notes for the event
        location: Where it is
        calendar: A calendar id from list_calendars; the user's main calendar if not given
    """

    def work():
        zone = _zone(config, calendar)
        begins, ends = _times(start, end, zone)
        body = {"summary": title, "start": begins, "end": ends}
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        return {"created": _shown(_call(config, "POST", f"/calendars/{_cal(calendar)}/events", json=body))}

    return _run(work)


@tool
def update_event(event_id: str, title: str | None = None, start: str | None = None, end: str | None = None,
                 description: str | None = None, location: str | None = None, calendar: str = "primary",
                 config: RunnableConfig = None) -> str:
    """Change an event in the user's calendar: only what's given changes. Moving the start
    without an end keeps the event as long as it was.

    Args:
        event_id: The event's id, from list_events
        title: Its new title
        start: Its new start, as in create_event
        end: Its new end
        description: Its new notes
        location: Its new place
        calendar: The calendar it's in, if not the user's main one
    """

    def work():
        path = f"/calendars/{_cal(calendar)}/events/{quote(event_id, safe='')}"
        body = {k: v for k, v in (("summary", title), ("description", description), ("location", location)) if v is not None}
        if start or end:
            zone = _zone(config, calendar)
            current = _shown(_call(config, "GET", path))
            new_end = end
            if start and not end:
                was_start, was_end, moved = _parse(current["start"], zone), _parse(current["end"], zone), _parse(start, zone)
                # The same length, when it's still a time (or still whole days)
                if type(was_start) is type(moved):
                    new_end = (moved + (was_end - was_start)).isoformat()
            body["start"], body["end"] = _times(start or current["start"], new_end, zone)
        if not body:
            raise AppError("Nothing to change")
        return {"updated": _shown(_call(config, "PATCH", path, json=body))}

    return _run(work)


@tool
def delete_event(event_id: str, calendar: str = "primary", config: RunnableConfig = None) -> str:
    """Delete an event from the user's calendar, when they ask for it.

    Args:
        event_id: The event's id, from list_events
        calendar: The calendar it's in, if not the user's main one
    """

    def work():
        _call(config, "DELETE", f"/calendars/{_cal(calendar)}/events/{quote(event_id, safe='')}")
        return {"deleted": event_id}

    return _run(work)


# For the web chat's agent editor
LABEL = "Google Calendar"
DESCRIPTION = "See and change events in the user's Google Calendar"

RUN_TOGETHER = ("list_calendars", "list_events")
TOOLS = [list_calendars, list_events, create_event, update_event, delete_event]

PROMPT = (
    "The user connected their Google Calendar. Use `list_events` to see what's on (it also tells you "
    "the time now and the calendar's time zone), and `create_event`, `update_event` and `delete_event` "
    "when they ask you to change it. Give times like 2026-10-30T15:00, in the calendar's time zone. "
    "Find an event's id with `list_events` before changing it, and only delete events the user asked to remove."
)
