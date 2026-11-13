"""The skills endpoints (see server/skills.py): yours and the shared ones to list and read,
yours to add (a SKILL.md, a zipped folder, or one the model writes from a description: /skills/draft),
change, share and delete. labb on other PCs reads them by name."""

import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from labb import skills as skill_format
from labb.server.auth_routes import current_user
from labb.server import skill_drafts, skills

router = APIRouter()


class NewSkill(BaseModel):
    # A SKILL.md's text, or a zipped skill folder as base64
    skill_md: str | None = None
    zip: str | None = None
    shared: bool | None = None


class DraftAsk(BaseModel):
    # What the skill should do, or the change to make to `current`
    ask: str
    current: str | None = None
    model: str | None = None


class SkillChange(BaseModel):
    skill_md: str | None = None
    shared: bool | None = None


@router.get("/skills")
def list_skills(user: dict = Depends(current_user)):
    """Your skills, then the ones others shared (`mine` says which), with their files"""
    return skills.list_all(user["id"])


@router.post("/skills", status_code=201)
def add_skill(body: NewSkill, user: dict = Depends(current_user)):
    """Add a skill, or replace yours with the same name"""

    try:
        if body.zip is not None:
            try:
                data = base64.b64decode(body.zip, validate=True)
            except (binascii.Error, ValueError):
                raise HTTPException(400, "The zip isn't valid base64")
            contents = skills.from_zip(data)
        elif body.skill_md is not None:
            contents = {skill_format.SKILL_FILE: body.skill_md}
        else:
            raise HTTPException(400, "Send a SKILL.md or a zip")
        return skills.save(user["id"], contents, body.shared)
    except skill_format.SkillError as e:
        raise HTTPException(400, str(e))


@router.post("/skills/draft")
def draft_skill(body: DraftAsk, user: dict = Depends(current_user)):
    """A SKILL.md the model wrote from a description (or a change to the one given), to look over: nothing is saved"""
    try:
        return skill_drafts.draft(user["id"], body.ask, body.current, body.model)
    except skill_drafts.DraftError as e:
        raise HTTPException(400, str(e))


@router.get("/skills/by-name/{name}/files")
def skill_files(name: str, user: dict = Depends(current_user)):
    """The files of the skill a run of yours gets by that name"""
    if (found := skills.files(user["id"], name)) is None:
        raise HTTPException(404, "No such skill")
    return {"files": found}


@router.get("/skills/by-name/{name}/file")
def skill_file(name: str, path: str = skill_format.SKILL_FILE, user: dict = Depends(current_user)):
    if (content := skills.read(user["id"], name, path)) is None:
        raise HTTPException(404, "No such file")
    return {"path": path, "content": content}


@router.get("/skills/{skill_id}")
def get_skill(skill_id: str, user: dict = Depends(current_user)):
    """A skill with its files and its SKILL.md"""
    if (skill := skills.get(user["id"], skill_id)) is None:
        raise HTTPException(404, "No such skill")
    return skill


@router.patch("/skills/{skill_id}")
def update_skill(skill_id: str, change: SkillChange, user: dict = Depends(current_user)):
    """Change your skill's SKILL.md, or share it"""
    try:
        skill = skills.update(user["id"], skill_id, change.skill_md, change.shared)
    except skill_format.SkillError as e:
        raise HTTPException(400, str(e))
    if skill is None:
        raise HTTPException(404, "No such skill")
    return skill


@router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: str, user: dict = Depends(current_user)):
    if not skills.delete(user["id"], skill_id):
        raise HTTPException(404, "No such skill")
    return Response(status_code=204)


@router.get("/skills/{skill_id}/download")
def download_skill(skill_id: str, user: dict = Depends(current_user)):
    """The skill's folder as a zip, e.g. to put in ~/.labb/skills/"""
    if (found := skills.zip_of(user["id"], skill_id)) is None:
        raise HTTPException(404, "No such skill")
    name, data = found
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})
