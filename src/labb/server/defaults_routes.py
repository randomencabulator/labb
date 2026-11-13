"""The default agents and skills (see defaults/): which of them you don't have, and putting
those back (Settings → Account)."""

from fastapi import APIRouter, Depends

from labb.server import defaults
from labb.server.auth_routes import current_user

router = APIRouter()


@router.get("/defaults")
def missing_defaults(user: dict = Depends(current_user)):
    """The default agents (keys) and skills (names) you don't have, and how many there are"""
    return {**defaults.missing(user["id"]), "total": {"agents": len(defaults.AGENTS), "skills": len(defaults.skill_files())}}


@router.post("/defaults/restore")
def restore_defaults(user: dict = Depends(current_user)):
    """Add back the default agents and skills you deleted; yours that are still there stay as they are"""
    return defaults.restore(user["id"])
