"""The speech endpoints (see speech.py): what the web chat can do, transcribing what was said,
reading text aloud, Speak steps' clips, and the admins' speech settings."""

import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from labb.server import speech
from labb.server.auth_routes import admin_user, current_user
from labb.server import limits

router = APIRouter()


class Recording(BaseModel):
    # The audio, as base64, and its file name (its type is read from the suffix)
    data: str
    name: str = "recording.webm"
    language: str | None = Field(None, max_length=16)


class SpeakRequest(BaseModel):
    text: str
    voice: str | None = None
    # Markdown (a reply) is read as its words
    markdown: bool = True


class SpeechSettings(BaseModel):
    listen: str | None = None
    speak: str | None = None
    voice: str | None = None


@router.get("/speech")
def what_speech_can_do(user: dict = Depends(current_user)):
    """Whether the server can listen and speak, and the voice it speaks with"""
    return speech.available()


@router.post("/speech/transcribe")
def transcribe(recording: Recording, user: dict = Depends(current_user)):
    """What's said in a recording"""

    try:
        data = base64.b64decode(recording.data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "The recording isn't valid base64")
    try:
        return {"text": speech.transcribe(data, recording.name, recording.language)}
    except speech.SpeechError as e:
        raise HTTPException(400, str(e))


@router.post("/speech/speak")
def speak(request: SpeakRequest, user: dict = Depends(current_user)):
    """The text read aloud, as MP3"""

    text = speech.spoken(request.text) if request.markdown else request.text
    try:
        return Response(speech.speak(text, request.voice), media_type="audio/mpeg")
    except speech.SpeechError as e:
        raise HTTPException(400, str(e))


class PartsRequest(BaseModel):
    text: str


@router.post("/speech/parts")
def speak_parts(request: PartsRequest, user: dict = Depends(current_user)):
    """A reply split into the parts it's read aloud in (each then goes to /speech/speak with
    markdown off), so the first one plays while the next is made"""

    found = speech.parts(request.text)
    if sum(len(p) for p in found) > (most := limits.get("speak_chars")):
        raise HTTPException(400, f"At most {most:,} characters can be read aloud at once")
    return {"parts": found}


@router.get("/speech/clips/{clip_id}")
def clip(clip_id: str, user: dict = Depends(current_user)):
    """A clip a Speak step made, for its owner"""

    found = speech.get_clip(user["id"], clip_id)
    if found is None:
        raise HTTPException(404, "No such clip")
    mime, audio = found
    return Response(audio, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/admin/speech")
def get_settings(user: dict = Depends(admin_user)):
    """The speech settings, with models and voices to pick from"""
    return {**speech.settings(), **{f"{k}_options": v for k, v in speech.suggestions().items()}}


@router.put("/admin/speech")
def save_settings(body: SpeechSettings, user: dict = Depends(admin_user)):
    try:
        speech.save_settings(body.model_dump(exclude_unset=True))
    except speech.SpeechError as e:
        raise HTTPException(400, str(e))
    return get_settings(user)


@router.post("/admin/speech/test")
def test(user: dict = Depends(admin_user)):
    """Say a sentence with the speaking model, and listen to it with the listening model"""

    current = speech.settings()
    result: dict = {"spoke": None, "heard": None, "error": None}
    try:
        if current["speak"]:
            audio = speech.speak("Hello from labb. Speech is working.")
            result["spoke"] = len(audio)
            if current["listen"]:
                result["heard"] = speech.transcribe(audio, "test.mp3")
        elif current["listen"]:
            result["error"] = "Pick a speaking model too to test listening: the test listens to what it says"
    except speech.SpeechError as e:
        result["error"] = str(e)
    return result
