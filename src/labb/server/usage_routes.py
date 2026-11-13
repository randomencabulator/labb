"""The usage stats endpoints: your own usage for everyone, and the
whole server for admins. The numbers come from usage.py.

Every endpoint takes `since` and `until` (ISO times; leave them out for everything) and
`tz`, the viewer's offset from UTC in minutes, for days and hours. With `Accept: text/csv`
they answer as CSV: /usage and /admin/usage give the ledger's rows (one per reply), the
others their table.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from labb.server.auth_routes import admin_user, current_user
from labb.server import auth
from labb.server import install
from labb import usage

router = APIRouter()


class Range:
    """The query parameters every endpoint shares"""

    def __init__(self, since: str | None = None, until: str | None = None, tz: int = Query(0, ge=-14 * 60, le=14 * 60)):
        for value in (since, until):
            if value:
                try:
                    usage.stamp(value)
                except ValueError:
                    raise HTTPException(400, f"Not a time: {value}")
        self.since, self.until, self.tz = since, until, tz


def wants_csv(request: Request) -> bool:
    return "text/csv" in request.headers.get("accept", "")


def csv_response(rows: list[dict], name: str) -> Response:
    return Response(
        usage.to_csv(rows), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


@router.get("/usage")
def my_usage(request: Request, range_: Range = Depends(), user: dict = Depends(current_user)):
    """Your usage: totals and series, and the chats and sessions that used the most tokens"""

    if wants_csv(request):
        return csv_response(usage.ledger(user["id"], range_.since, range_.until), "usage")
    return usage.summary(user["id"], range_.since, range_.until, range_.tz, titles=True)


@router.get("/admin/usage")
def server_usage(request: Request, user_id: str | None = None, range_: Range = Depends(), admin: dict = Depends(admin_user)):
    """Everyone's usage, or one user's (without their chats' titles); for everyone also how
    many people are active and what the database holds"""

    if user_id is not None and auth.get_user(user_id) is None:
        raise HTTPException(404, "No such user")
    if wants_csv(request):
        return csv_response(usage.ledger(user_id, range_.since, range_.until), "usage")
    result = usage.summary(user_id, range_.since, range_.until, range_.tz)
    if user_id is None:
        result["active_users"] = usage.active_users()
        result["storage"] = usage.storage()
    return result


@router.get("/admin/usage/users")
def users_usage(request: Request, range_: Range = Depends(), admin: dict = Depends(admin_user)):
    rows = usage.users(range_.since, range_.until)
    return csv_response(rows, "users") if wants_csv(request) else rows


@router.get("/admin/usage/gateway")
def gateway_usage(request: Request, range_: Range = Depends(), admin: dict = Depends(admin_user)):
    """The model calls and page reads that went through the gateway"""

    result = usage.gateway(range_.since, range_.until, range_.tz)
    return csv_response(result["by_model"], "gateway") if wants_csv(request) else result


@router.get("/admin/usage/machines")
def machines_usage(request: Request, range_: Range = Depends(), admin: dict = Depends(admin_user)):
    """Each key with its machine and labb version, and whether that's older than the server's"""

    rows = usage.machines(install.version(), range_.since, range_.until)
    if wants_csv(request):
        return csv_response(rows, "machines")
    return {"server_version": install.version(), "machines": rows}
