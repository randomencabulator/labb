"""The user's Outlook calendar, once they've connected Microsoft 365 (see connections.py), through
Microsoft Graph.

Like Google Calendar's tools: times without an offset are in the mailbox's time zone, which every
answer names, and a date alone is a whole day. Microsoft names zones the Windows way ("W. Europe
Standard Time"); Graph is given that name as it is, and `WINDOWS_ZONES` turns the common ones into
IANA's for the offsets. One that isn't there is worked in UTC, which the answers then say."""

import json
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig

from labb import connections
from labb.connections import AppError
from labb.tools.google_calendar import _parse

API = "https://graph.microsoft.com/v1.0/me"
MAX_EVENTS = 50
DESCRIPTION_CHARS = 500
ZONE_SECONDS = 600
# user -> ((Graph's zone name, IANA's), when it was looked up)
_zones: dict[str, tuple[tuple[str, str], float]] = {}

# Windows' names of the zones most mailboxes are in, as IANA's (from CLDR's windowsZones)
WINDOWS_ZONES = {
    "Dateline Standard Time": "Etc/GMT+12", "Hawaiian Standard Time": "Pacific/Honolulu",
    "Alaskan Standard Time": "America/Anchorage", "Pacific Standard Time": "America/Los_Angeles",
    "Pacific Standard Time (Mexico)": "America/Tijuana", "US Mountain Standard Time": "America/Phoenix",
    "Mountain Standard Time": "America/Denver", "Central America Standard Time": "America/Guatemala",
    "Central Standard Time": "America/Chicago", "Central Standard Time (Mexico)": "America/Mexico_City",
    "Canada Central Standard Time": "America/Regina", "SA Pacific Standard Time": "America/Bogota",
    "Eastern Standard Time": "America/New_York", "Eastern Standard Time (Mexico)": "America/Cancun",
    "Venezuela Standard Time": "America/Caracas", "Atlantic Standard Time": "America/Halifax",
    "SA Western Standard Time": "America/La_Paz", "Pacific SA Standard Time": "America/Santiago",
    "Newfoundland Standard Time": "America/St_Johns", "E. South America Standard Time": "America/Sao_Paulo",
    "Argentina Standard Time": "America/Argentina/Buenos_Aires", "SA Eastern Standard Time": "America/Cayenne",
    "UTC": "UTC", "GMT Standard Time": "Europe/London", "Greenwich Standard Time": "Atlantic/Reykjavik",
    "W. Europe Standard Time": "Europe/Berlin", "Central Europe Standard Time": "Europe/Budapest",
    "Romance Standard Time": "Europe/Paris", "Central European Standard Time": "Europe/Warsaw",
    "W. Central Africa Standard Time": "Africa/Lagos", "GTB Standard Time": "Europe/Bucharest",
    "E. Europe Standard Time": "Europe/Chisinau", "FLE Standard Time": "Europe/Kiev", "Egypt Standard Time": "Africa/Cairo",
    "South Africa Standard Time": "Africa/Johannesburg", "Israel Standard Time": "Asia/Jerusalem",
    "Turkey Standard Time": "Europe/Istanbul", "Russian Standard Time": "Europe/Moscow", "Arab Standard Time": "Asia/Riyadh",
    "E. Africa Standard Time": "Africa/Nairobi", "Iran Standard Time": "Asia/Tehran", "Arabian Standard Time": "Asia/Dubai",
    "Pakistan Standard Time": "Asia/Karachi", "India Standard Time": "Asia/Kolkata", "Nepal Standard Time": "Asia/Katmandu",
    "Central Asia Standard Time": "Asia/Almaty", "Bangladesh Standard Time": "Asia/Dhaka", "SE Asia Standard Time": "Asia/Bangkok",
    "China Standard Time": "Asia/Shanghai", "Singapore Standard Time": "Asia/Singapore", "Taipei Standard Time": "Asia/Taipei",
    "W. Australia Standard Time": "Australia/Perth", "Tokyo Standard Time": "Asia/Tokyo", "Korea Standard Time": "Asia/Seoul",
    "Cen. Australia Standard Time": "Australia/Adelaide", "AUS Central Standard Time": "Australia/Darwin",
    "E. Australia Standard Time": "Australia/Brisbane", "AUS Eastern Standard Time": "Australia/Sydney",
    "New Zealand Standard Time": "Pacific/Auckland",
}

CONNECTION = "microsoft"
DEFERRABLE = True


def enabled() -> bool:
    return connections.configured(CONNECTION)


def _call(config, method: str, path: str, **kwargs) -> dict:
    return connections.call(config, CONNECTION, method, API + path, **kwargs)


def _zone(config) -> tuple[str, ZoneInfo]:
    """The mailbox's zone: (its name for Graph, its ZoneInfo); UTC for both when it isn't known"""

    user = connections.user_of(config) or ""
    found = _zones.get(user)
    if not found or time.monotonic() - found[1] > ZONE_SECONDS:
        name = _call(config, "GET", "/mailboxSettings", params={"$select": "timeZone"}).get("timeZone") or "UTC"
        iana = WINDOWS_ZONES.get(name, name)
        try:
            ZoneInfo(iana)
        except Exception:
            name = iana = "UTC"
        found = (name, iana), time.monotonic()
        _zones[user] = found
    name, iana = found[0]
    return name, ZoneInfo(iana)


def _prefer(name: str) -> dict:
    return {"Prefer": f'outlook.timezone="{name}"'}


def _graph_time(value: date | datetime, zone: ZoneInfo, name: str) -> dict:
    """A time as Graph takes it: the wall time in the mailbox's zone, and the zone's name"""
    moment = value if isinstance(value, datetime) else datetime.combine(value, datetime.min.time(), zone)
    local = moment.astimezone(zone).replace(tzinfo=None)
    return {"dateTime": local.isoformat(timespec="seconds"), "timeZone": name}


def _times(start: str, end: str | None, zone: ZoneInfo, name: str) -> dict:
    begins = _parse(start, zone)
    if isinstance(begins, datetime):
        ends = _parse(end, zone) if end else begins + timedelta(hours=1)
        if not isinstance(ends, datetime):
            raise AppError("Give the end as a time too, like the start")
        all_day = False
    else:
        ends = _parse(end, zone) if end else begins
        if isinstance(ends, datetime):
            raise AppError("Give the end as a date too, like the start")
        # An all-day event ends at the start of the day after its last
        ends += timedelta(days=1)
        all_day = True
    if ends <= begins:
        raise AppError("The end has to be after the start")
    return {"start": _graph_time(begins, zone, name), "end": _graph_time(ends, zone, name), "isAllDay": all_day}


def _local(value: dict | None, zone: ZoneInfo) -> datetime | None:
    text = (value or {}).get("dateTime")
    if not text:
        return None
    # Graph gives seven digits of a second, in the zone it was asked for
    return datetime.fromisoformat(text[:19]).replace(tzinfo=zone)


def _shown(event: dict, zone: ZoneInfo) -> dict:
    start, end = _local(event.get("start"), zone), _local(event.get("end"), zone)
    shown = {"id": event.get("id"), "title": event.get("subject") or "(no title)",
             "start": start.isoformat() if start else None, "end": end.isoformat() if end else None}
    if event.get("isAllDay") and start and end:
        shown.update(start=start.date().isoformat(), end=(end.date() - timedelta(days=1)).isoformat(), all_day=True)
    if place := (event.get("location") or {}).get("displayName"):
        shown["location"] = place
    if text := event.get("bodyPreview"):
        shown["description"] = text[:DESCRIPTION_CHARS] + ("…" if len(text) > DESCRIPTION_CHARS else "")
    if attendees := event.get("attendees"):
        shown["attendees"] = [a["emailAddress"]["address"] for a in attendees if (a.get("emailAddress") or {}).get("address")]
    if event.get("isCancelled"):
        shown["status"] = "cancelled"
    if event.get("webLink"):
        shown["link"] = event["webLink"]
    return shown


def _event(event_id: str) -> str:
    return f"/events/{quote(event_id.strip(), safe='')}"


def _run(work) -> str:
    try:
        return json.dumps(work(), ensure_ascii=False)
    except AppError as e:
        return f"Error: {e}"


SELECT = "id,subject,start,end,isAllDay,location,bodyPreview,attendees,isCancelled,webLink"


@tool
def list_outlook_events(start: str | None = None, end: str | None = None, query: str | None = None, max_results: int = 20,
                        config: RunnableConfig = None) -> str:
    """List the events in the user's Outlook calendar between two times, soonest first.

    Returns JSON with the calendar's `time_zone`, `now` there, and `events` (each with `id`,
    `title`, `start`, `end`, and `location`, `description`, `attendees` when set).

    Args:
        start: From when, as a date (2026-10-30) or a time (2026-10-30T09:00); now if not given
        end: Until when; a week after the start if not given. A date includes that whole day
        query: Only events with these words in the title, notes or place
        max_results: How many events at most (1-50)
    """

    def work():
        name, zone = _zone(config)
        now = datetime.now(zone).replace(microsecond=0)
        begins = _parse(start, zone) if start else now
        if not isinstance(begins, datetime):
            begins = datetime.combine(begins, datetime.min.time(), zone)
        ends = _parse(end, zone) if end else begins + timedelta(days=7)
        if not isinstance(ends, datetime):
            ends = datetime.combine(ends + timedelta(days=1), datetime.min.time(), zone)
        most = max(1, min(max_results, MAX_EVENTS))
        # Graph's calendar view can't search: words are matched here, in up to 100 events
        params = {"startDateTime": begins.isoformat(), "endDateTime": ends.isoformat(), "$orderby": "start/dateTime",
                  "$top": 100 if query else most, "$select": SELECT}
        found = _call(config, "GET", "/calendarView", params=params, headers=_prefer(name)).get("value", [])
        if query:
            words = query.lower().split()
            found = [e for e in found if all(w in " ".join([e.get("subject") or "", e.get("bodyPreview") or "",
                                                           (e.get("location") or {}).get("displayName") or ""]).lower()
                                             for w in words)]
        return {"time_zone": str(zone), "now": now.isoformat(), "events": [_shown(e, zone) for e in found[:most]]}

    return _run(work)


@tool
def create_outlook_event(title: str, start: str, end: str | None = None, description: str | None = None,
                         location: str | None = None, config: RunnableConfig = None) -> str:
    """Add an event to the user's Outlook calendar. No one is invited.

    Args:
        title: What it is, e.g. "Dentist"
        start: A time (2026-10-30T15:00) in the calendar's time zone, or a date (2026-10-30) for a whole day
        end: When it ends, the same way; an hour after a time, or the same day for a date, if not given
        description: Notes for the event
        location: Where it is
    """

    def work():
        name, zone = _zone(config)
        body = {"subject": title, **_times(start, end, zone, name)}
        if description:
            body["body"] = {"contentType": "Text", "content": description}
        if location:
            body["location"] = {"displayName": location}
        return {"created": _shown(_call(config, "POST", "/events", json=body, headers=_prefer(name)), zone)}

    return _run(work)


@tool
def update_outlook_event(event_id: str, title: str | None = None, start: str | None = None, end: str | None = None,
                         description: str | None = None, location: str | None = None, config: RunnableConfig = None) -> str:
    """Change an event in the user's Outlook calendar: only what's given changes. Moving the start
    without an end keeps the event as long as it was.

    Args:
        event_id: The event's id, from list_outlook_events
        title: Its new title
        start: Its new start, as in create_outlook_event
        end: Its new end
        description: Its new notes
        location: Its new place
    """

    def work():
        name, zone = _zone(config)
        body: dict = {}
        if title is not None:
            body["subject"] = title
        if description is not None:
            body["body"] = {"contentType": "Text", "content": description}
        if location is not None:
            body["location"] = {"displayName": location}
        if start or end:
            current = _shown(_call(config, "GET", _event(event_id), params={"$select": SELECT}, headers=_prefer(name)), zone)
            new_end = end
            if start and not end:
                was_start, was_end, moved = _parse(current["start"], zone), _parse(current["end"], zone), _parse(start, zone)
                if type(was_start) is type(moved):
                    new_end = (moved + (was_end - was_start)).isoformat()
            body.update(_times(start or current["start"], new_end, zone, name))
        if not body:
            raise AppError("Nothing to change")
        return {"updated": _shown(_call(config, "PATCH", _event(event_id), json=body, headers=_prefer(name)), zone)}

    return _run(work)


@tool
def delete_outlook_event(event_id: str, config: RunnableConfig = None) -> str:
    """Delete an event from the user's Outlook calendar, when they ask for it.

    Args:
        event_id: The event's id, from list_outlook_events
    """

    def work():
        _call(config, "DELETE", _event(event_id))
        return {"deleted": event_id}

    return _run(work)


# For the web chat's agent editor
LABEL = "Outlook calendar"
DESCRIPTION = "See and change events in the user's Outlook calendar"

RUN_TOGETHER = ("list_outlook_events",)
TOOLS = [list_outlook_events, create_outlook_event, update_outlook_event, delete_outlook_event]

PROMPT = (
    "The user connected their Outlook calendar (Microsoft 365). Use `list_outlook_events` to see what's on (it also "
    "tells you the time now and the calendar's time zone), and `create_outlook_event`, `update_outlook_event` and "
    "`delete_outlook_event` when they ask you to change it. Give times like 2026-10-30T15:00, in the calendar's time zone. "
    "Find an event's id with `list_outlook_events` before changing it, and only delete events the user asked to remove."
)
