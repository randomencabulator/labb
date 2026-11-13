"""Settings → Limits, for admins (see limits.py)"""

from fastapi import APIRouter, Depends, HTTPException

from labb.server import limits
from labb.server.auth_routes import admin_user, current_user

router = APIRouter()


@router.get("/admin/limits")
def get_limits(problems: bool = False, user: dict = Depends(admin_user)):
    """Every limit: its group, label, description, range, default and value. With ?problems=true,
    {"limits", "problems"}: why a service's limits (the apps', the published pages') aren't there."""
    found: list[str] = []
    described = limits.described(found)
    return {"limits": described, "problems": found} if problems else described


@router.put("/admin/limits")
def save_limits(values: dict[str, int | None], problems: bool = False, user: dict = Depends(admin_user)):
    """Change some limits; null puts one back to its default"""
    try:
        limits.save(values)
    except limits.LimitError as e:
        raise HTTPException(400, str(e))
    return get_limits(problems, user)


@router.get("/limits")
def public_limits(user: dict = Depends(current_user)):
    """What the pages check before sending: the limits people meet when they attach or type"""
    return {key: limits.get(key) for key in limits.SHOWN}
