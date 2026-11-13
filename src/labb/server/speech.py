"""Speech: listening (speech to text) and speaking (text to speech), with OpenAI's audio API.

That API (`/audio/transcriptions`, `/audio/speech`) is spoken by OpenAI and by servers that copy
it: speaches (faster-whisper, Kokoro and Piper voices; compose.yaml runs it), LocalAI,
vLLM, Kokoro-FastAPI… So the models are "<provider>:<model>" specs of a provider of kind `openai`
or `openai_compatible` in Settings → Models, like the chat models. Ollama has no speech API.

- The settings (which model listens, which speaks, and its voice) are the admins', in
  `server_settings` under `speech`, else the environment's.
- The web chat's mic, read-aloud and voice mode, audio documents and Studio's Transcribe and
  Speak steps all come through here.
- What Speak steps make is kept as clips (`speech_clips`, base64 text, so both databases keep it
  alike), served to their owner at /speech/clips/<id>; each person keeps their newest MAX_CLIPS.
- speaches starts with no models: the ones picked of it are downloaded into it (`fetch_models`),
  when the server starts and when they're picked.
"""

import base64
import json
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx

from labb import llm
from labb.chats import conn, lock
from labb.usage import stamp
from labb.server import limits

LISTEN_MODEL = os.getenv("SPEECH_TO_TEXT_MODEL", "").strip() or None
SPEAK_MODEL = os.getenv("TEXT_TO_SPEECH_MODEL", "").strip() or None
VOICE = os.getenv("TTS_VOICE", "").strip() or "alloy"

# speaches' download can take minutes; it's asked again for a while when it isn't up yet
FETCH_SECONDS = 1800
FETCH_TRIES = 30
RETRY_SECONDS = 20

# OpenAI's own speech models, which its model list leaves out of the chat models
OPENAI_LISTEN = ("gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1")
OPENAI_SPEAK = ("gpt-4o-mini-tts", "tts-1", "tts-1-hd")
OPENAI_VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse")
# What other servers call their speech models (speaches' "Systran/faster-whisper-small", "speaches-ai/Kokoro-82M…")
LISTEN_NAMES = re.compile(r"whisper|transcri|stt|parakeet|canary|moonshine|asr", re.I)
SPEAK_NAMES = re.compile(r"tts|kokoro|piper|speech|voice|orpheus|kitten|chatterbox|xtts", re.I)

# OpenAI speaks at most 4,096 characters at once (and takes recordings up to 25 MB: Settings → Limits)
SPEAK_CHUNK = 4000
MAX_CLIPS = 200


def max_audio_bytes() -> int:
    return limits.get("recording_mb") * 1_000_000


def max_clips() -> int:
    """Settings → Limits' clips_kept; MAX_CLIPS while that's at its default (the tests change it)"""
    value = limits.get("clips_kept")
    return MAX_CLIPS if value == limits.BY_KEY["clips_kept"].default else value
LISTEN_SECONDS = 600
SPEAK_SECONDS = 180
AUDIO_TYPES = {".mp3": "audio/mpeg", ".mpga": "audio/mpeg", ".mpeg": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4",
               ".wav": "audio/wav", ".webm": "audio/webm", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
               ".flac": "audio/flac"}

with lock, conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS speech_clips (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, mime TEXT NOT NULL, "
        "chars INTEGER NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS speech_clips_by_user ON speech_clips (user_id, created_at)")


class SpeechError(ValueError):
    """Speech that can't be done, said for the person"""


# --- Settings ----------------------------------------------------------------------------

_settings: dict | None = None
_settings_lock = threading.Lock()


def _env_settings() -> dict:
    return {"listen": LISTEN_MODEL, "speak": SPEAK_MODEL, "voice": VOICE}


def settings() -> dict:
    global _settings
    with _settings_lock:
        if _settings is None:
            with lock:
                row = conn.execute("SELECT value FROM server_settings WHERE key = 'speech'").fetchone()
            _settings = {**_env_settings(), **json.loads(row[0])} if row else _env_settings()
        return dict(_settings)


def save_settings(fields: dict) -> dict:
    """Change the speech models and voice; a model is checked to be one a provider can serve"""

    current = settings()
    for key in ("listen", "speak"):
        if key in fields:
            spec = (fields[key] or "").strip() or None
            if spec:
                _provider(spec)
            current[key] = spec
    if "voice" in fields:
        current["voice"] = (fields["voice"] or "").strip()[:80] or VOICE
    with lock, conn:
        conn.execute("INSERT INTO server_settings (key, value) VALUES ('speech', ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (json.dumps(current),))
    global _settings
    with _settings_lock:
        _settings = current
    fetch_models(wait=False)
    return settings()


def fetch_models(wait: bool = False, retry: bool = False):
    """Download the picked models that speaches doesn't have yet (in the background unless `wait`;
    `retry`: ask again while speaches is starting)"""

    def run():
        for attempt in range(FETCH_TRIES if retry else 1):
            try:
                _fetch_missing()
                return
            except httpx.TransportError:
                if attempt + 1 < FETCH_TRIES and retry:
                    time.sleep(RETRY_SECONDS)
            except (httpx.HTTPError, ValueError, KeyError) as e:
                print(f"labb: couldn't download speech models into speaches: {e}", file=sys.stderr)
                return

    if wait:
        run()
    else:
        threading.Thread(target=run, daemon=True).start()


def _fetch_missing():
    current = settings()
    for spec in (current["listen"], current["speak"]):
        name, _, model = (spec or "").partition(":")
        provider = llm.providers.get(name)
        if name != "speaches" or not model or provider is None or not provider.enabled:
            continue
        if model in _all_models(provider):
            continue
        print(f"labb: downloading {model} into speaches", file=sys.stderr)
        response = httpx.post(f"{provider.base_url.rstrip('/')}/models/{model}", headers=_headers(provider),
                              timeout=FETCH_SECONDS)
        response.raise_for_status()


def _provider(spec: str) -> tuple[llm.Provider, str]:
    """The provider and model of a speech spec; raises SpeechError"""

    name, _, model = spec.partition(":")
    provider = llm.providers.get(name)
    if not model or provider is None:
        raise SpeechError(f'{spec}: speech models are "<provider>:<model>", of a provider in Settings → Models')
    if provider.kind not in ("openai", "openai_compatible"):
        raise SpeechError(f"{spec}: {name} has no speech API; use OpenAI or a server that speaks its API (like speaches)")
    if not provider.enabled:
        raise SpeechError(f"{spec}: the provider {name} is turned off")
    return provider, model


def available() -> dict:
    """What the web chat can offer: listening, speaking, and the voice"""

    current = settings()
    return {"listen": bool(current["listen"]), "speak": bool(current["speak"]), "voice": current["voice"]}


def suggestions() -> dict:
    """Speech models and voices to pick from in Settings → Models"""

    listen, speak = [], []
    for provider in llm.providers.values():
        if not provider.enabled or provider.kind not in ("openai", "openai_compatible"):
            continue
        if provider.kind == "openai" and provider.base_url.rstrip("/") == llm.OPENAI_URL:
            if provider.api_key:
                listen += [f"{provider.name}:{m}" for m in OPENAI_LISTEN]
                speak += [f"{provider.name}:{m}" for m in OPENAI_SPEAK]
            continue
        try:
            names = _all_models(provider)
        except (httpx.HTTPError, ValueError, KeyError):
            continue
        listen += [f"{provider.name}:{m}" for m in names if LISTEN_NAMES.search(m)]
        speak += [f"{provider.name}:{m}" for m in names if SPEAK_NAMES.search(m)]
    return {"listen": listen, "speak": speak, "voices": list(OPENAI_VOICES)}


def _all_models(provider: llm.Provider) -> list[str]:
    """Every model an OpenAI-style server lists (the chat lists keep only the newest 30)"""
    response = httpx.get(f"{provider.base_url.rstrip('/')}/models", headers=_headers(provider), timeout=5)
    response.raise_for_status()
    return [m["id"] for m in response.json().get("data", [])]


def _headers(provider: llm.Provider) -> dict:
    return {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}


def _failed(what: str, spec: str, error: Exception) -> SpeechError:
    if isinstance(error, httpx.HTTPStatusError):
        try:
            detail = error.response.json()
            detail = (detail.get("error") or {}).get("message") if isinstance(detail.get("error"), dict) else detail.get("detail") or detail
        except ValueError:
            detail = error.response.text
        error = f"{error.response.status_code}: {str(detail)[:300]}"
    return SpeechError(f"{what} failed: {llm.explain_error(str(error), spec)}")


# --- Listening ---------------------------------------------------------------------------

def is_audio(name: str) -> bool:
    return Path(name).suffix.lower() in AUDIO_TYPES


def transcribe(data: bytes, name: str = "audio.webm", language: str | None = None) -> str:
    """What's said in a recording; raises SpeechError"""

    spec = settings()["listen"]
    if not spec:
        raise SpeechError("No model listens to speech yet: an admin picks one in Settings → Models → Speech")
    if not data:
        raise SpeechError("The recording is empty")
    if len(data) > max_audio_bytes():
        raise SpeechError(f"Recordings can be at most {max_audio_bytes() // 1_000_000} MB")
    provider, model = _provider(spec)
    name = Path(name).name or "audio.webm"
    form = {"model": model, "response_format": "json"}
    if language:
        form["language"] = language
    try:
        response = httpx.post(f"{provider.base_url.rstrip('/')}/audio/transcriptions", headers=_headers(provider),
                              files={"file": (name, data, AUDIO_TYPES.get(Path(name).suffix.lower(), "application/octet-stream"))},
                              data=form, timeout=LISTEN_SECONDS)
        response.raise_for_status()
        answer = response.json() if "json" in response.headers.get("content-type", "") else {"text": response.text}
    except (httpx.HTTPError, ValueError) as e:
        raise _failed("Transcribing", spec, e) from e
    return str(answer.get("text") or "").strip()


# --- Speaking ----------------------------------------------------------------------------

CODE_BLOCK = re.compile(r"```.*?(?:```|$)", re.S)
IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
URL = re.compile(r"https?://\S+")


def spoken(markdown: str) -> str:
    """A reply as it's read aloud: its words, without code, link targets or Markdown's marks"""

    text = CODE_BLOCK.sub(" (code) ", markdown)
    text = IMAGE.sub(lambda m: m.group(1), text)
    text = LINK.sub(lambda m: m.group(1), text)
    text = URL.sub("a link", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"^[ \t]{0,3}(#{1,6}|>|[-*+]|\d+[.)])[ \t]+", "", text, flags=re.M)
    # A table: its rows as their cells, without the line under the header
    text = re.sub(r"^[ \t]*\|?[ \t:-]*-[ \t:|-]*\|[ \t:|-]*$\n?", "", text, flags=re.M)
    text = re.sub(r"^[ \t]*\|(.*)\|[ \t]*$", lambda row: ", ".join(c.strip() for c in row.group(1).split("|")), text, flags=re.M)
    text = re.sub(r"(\*\*|__|\*|_|~~)(\S.*?\S|\S)\1", r"\2", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    # A line without an end (a heading, a list item) ends with a pause
    text = re.sub(r"([^\s.!?:;,…])(?=\n|$)", r"\1.", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _chunks(text: str, size: int = SPEAK_CHUNK) -> list[str]:
    """Pieces of at most `size` characters, at paragraph or sentence ends where it can"""

    pieces, current = [], ""
    # Sentences with the space after them, so a piece keeps its line breaks
    for sentence in re.findall(r".*?(?:[.!?](?=\s)|\n|$)\s*", text, flags=re.S):
        while len(sentence) > size:
            cut = sentence.rfind(" ", 0, size)
            cut = cut if cut > 0 else size
            pieces.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        if current and len(current) + len(sentence) > size:
            pieces.append(current)
            current = ""
        current += sentence
    pieces.append(current)
    return [p.strip() for p in pieces if p.strip()]


# Reading a reply aloud in the web chat: a short first part, so it starts soon, then longer ones,
# each made while the one before plays
FIRST_PART, PART = 220, 900


def parts(markdown: str) -> list[str]:
    """A reply as the parts the web chat reads aloud one after another, as they're said"""

    text = spoken(markdown)
    if not text:
        return []
    first = _chunks(text, FIRST_PART)[0]
    rest = text[text.index(first) + len(first):].strip()
    return [first, *(_chunks(rest, PART) if rest else [])]


def speak(text: str, voice: str | None = None) -> bytes:
    """MP3 of the text, read by the speaking model; raises SpeechError"""

    current = settings()
    spec = current["speak"]
    if not spec:
        raise SpeechError("No model speaks yet: an admin picks one in Settings → Models → Speech")
    text = text.strip()
    if not text:
        raise SpeechError("There's nothing to say")
    if len(text) > (most := limits.get("speak_chars")):
        raise SpeechError(f"At most {most:,} characters can be read aloud at once")
    provider, model = _provider(spec)
    audio = b""
    for piece in _chunks(text):
        try:
            response = httpx.post(f"{provider.base_url.rstrip('/')}/audio/speech", headers=_headers(provider), timeout=SPEAK_SECONDS,
                                  json={"model": model, "input": piece, "voice": (voice or "").strip() or current["voice"],
                                        "response_format": "mp3"})
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise _failed("Speaking", spec, e) from e
        # MP3 is a stream of frames, so the pieces play one after another
        audio += response.content
    return audio


# --- Clips -------------------------------------------------------------------------------

CLIP_PATH = "/speech/clips/"


def save_clip(user_id: str, audio: bytes, chars: int, mime: str = "audio/mpeg") -> dict:
    clip_id = uuid.uuid4().hex
    with lock, conn:
        conn.execute("INSERT INTO speech_clips (id, user_id, mime, chars, data, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (clip_id, user_id, mime, chars, base64.b64encode(audio).decode(), stamp()))
        # The newest stay (the one just made too, when others were made in the same second)
        ids = [row[0] for row in conn.execute("SELECT id FROM speech_clips WHERE user_id = ? ORDER BY created_at DESC",
                                              (user_id,)).fetchall()]
        ids.remove(clip_id)
        conn.executemany("DELETE FROM speech_clips WHERE id = ?", [(i,) for i in ids[max_clips() - 1:]])
    return {"id": clip_id, "url": CLIP_PATH + clip_id, "chars": chars}


def get_clip(user_id: str, clip_id: str) -> tuple[str, bytes] | None:
    with lock:
        row = conn.execute("SELECT mime, data FROM speech_clips WHERE id = ? AND user_id = ?", (clip_id, user_id)).fetchone()
    return (row[0], base64.b64decode(row[1])) if row else None


def clip_of(reference: str, user_id: str) -> bytes | None:
    """A clip's audio from its link (/speech/clips/<id>, or the whole URL), if it's the person's"""

    if CLIP_PATH not in reference:
        return None
    found = get_clip(user_id, reference.split(CLIP_PATH, 1)[1].split("?")[0].strip("/ "))
    return found[1] if found else None
