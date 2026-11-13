"""Speech (server/speech.py): the settings, listening and speaking through an OpenAI-style audio
API (a fake one here), reading Markdown aloud, clips, recordings as documents, and Studio's
Transcribe and Speak steps."""

import base64
import time
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from labb import chats, llm
from labb.server import app as server
from labb.server import auth, documents, speech, vectors
from labb.server.studio import nodes

from conftest import WordEmbeddings
from test_studio import alice, flow, make, node, run, step_of  # noqa: F401 (alice is a fixture)

AUDIO = b"ID3fake-mp3-audio"


class FakeSpeechServer:
    """An OpenAI-style audio API: hears `heard`, and speaks text as b"ID3<text>" """

    def __init__(self):
        self.heard = "Buy milk and call the plumber."
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, **kwargs):
        request = httpx.Request("POST", url)
        if url.endswith("/audio/transcriptions"):
            name, data, mime = kwargs["files"]["file"]
            self.calls.append(("listen", {**kwargs["data"], "name": name, "bytes": len(data), "mime": mime}))
            return httpx.Response(200, json={"text": self.heard}, request=request)
        if url.endswith("/audio/speech"):
            body = kwargs["json"]
            self.calls.append(("speak", body))
            if body["voice"] == "nobody":
                return httpx.Response(400, json={"error": {"message": "Voice nobody isn't there"}}, request=request)
            return httpx.Response(200, content=b"ID3" + body["input"].encode(), request=request)
        return httpx.Response(404, request=request)


@pytest.fixture
def fake(monkeypatch):
    """A provider "local" of kind openai_compatible, served by the fake; no speech settings saved"""

    monkeypatch.setitem(llm.providers, "local", llm.Provider("local", "openai_compatible", "http://speech/v1"))
    serving = FakeSpeechServer()
    monkeypatch.setattr(speech.httpx, "post", serving.post)
    with chats.lock, chats.conn:
        chats.conn.execute("DELETE FROM server_settings WHERE key = 'speech'")
    monkeypatch.setattr(speech, "_settings", None)
    monkeypatch.setattr(speech, "LISTEN_MODEL", None)
    monkeypatch.setattr(speech, "SPEAK_MODEL", None)
    yield serving
    with chats.lock, chats.conn:
        chats.conn.execute("DELETE FROM server_settings WHERE key = 'speech'")
    speech._settings = None


@pytest.fixture
def member(accounts):
    user = auth.create_user(f"s{uuid4().hex[:10]}", "a-password")
    key, _ = auth.create_key(user["id"], "laptop")
    return user, TestClient(server.app, headers={"Authorization": f"Bearer {key}"})


def turn_on(client: TestClient, **fields):
    response = client.put("/admin/speech", json={"listen": "local:whisper-small", "speak": "local:kokoro", "voice": "af_heart", **fields})
    assert response.status_code == 200, response.text
    return response.json()


def test_nothing_until_an_admin_picks_models(fake, client, member):
    _, people = member
    assert people.get("/speech").json() == {"listen": False, "speak": False, "voice": "alloy"}
    refused = people.post("/speech/transcribe", json={"data": base64.b64encode(AUDIO).decode()})
    assert refused.status_code == 400 and "Settings → Models → Speech" in refused.json()["detail"]
    assert people.put("/admin/speech", json={"listen": "local:whisper-small"}).status_code == 403

    # Ollama has no speech API; a provider that isn't there isn't either
    assert "no speech API" in client.put("/admin/speech", json={"listen": "ollama:qwen3.5:4b"}).json()["detail"]
    assert client.put("/admin/speech", json={"speak": "nowhere:tts"}).status_code == 400

    saved = turn_on(client)
    assert saved["listen"] == "local:whisper-small" and saved["voice"] == "af_heart"
    assert people.get("/speech").json() == {"listen": True, "speak": True, "voice": "af_heart"}


def test_listening_and_speaking(fake, client, member):
    _, people = member
    turn_on(client)
    heard = people.post("/speech/transcribe", json={"data": base64.b64encode(AUDIO).decode(), "name": "note.webm", "language": "en"})
    assert heard.json() == {"text": "Buy milk and call the plumber."}
    assert fake.calls[-1] == ("listen", {"model": "whisper-small", "response_format": "json", "language": "en",
                                         "name": "note.webm", "bytes": len(AUDIO), "mime": "audio/webm"})
    assert people.post("/speech/transcribe", json={"data": "not base64!"}).status_code == 400

    # A reply is read as its words: no Markdown marks, code or link targets
    spoken = people.post("/speech/speak", json={"text": "## Plan\n\n- **Buy** [milk](https://shop.example)\n\n```py\nx = 1\n```"})
    assert spoken.headers["content-type"] == "audio/mpeg"
    said = fake.calls[-1][1]
    assert said["model"] == "kokoro" and said["voice"] == "af_heart" and said["response_format"] == "mp3"
    assert said["input"] == "Plan.\n\nBuy milk.\n\n(code)"

    # Long text is read in pieces, one after another
    long = " ".join(["This sentence is about forty characters."] * 250)
    fake.calls.clear()
    audio = people.post("/speech/speak", json={"text": long}).content
    assert len(fake.calls) == 3 and all(len(c[1]["input"]) <= speech.SPEAK_CHUNK for c in fake.calls)
    assert audio.count(b"ID3") == 3 and " ".join(c[1]["input"] for c in fake.calls) == long

    # The provider's refusal, said plainly
    failed = people.post("/speech/speak", json={"text": "Hi", "voice": "nobody"})
    assert failed.status_code == 400 and "Voice nobody isn't there" in failed.json()["detail"]

    assert client.post("/admin/speech/test").json() == {"spoke": len(b"ID3Hello from labb. Speech is working."),
                                                        "heard": "Buy milk and call the plumber.", "error": None}


def test_clips_are_their_owners(fake, member, accounts, monkeypatch):
    user, people = member
    monkeypatch.setattr(speech, "MAX_CLIPS", 3)
    clips = [speech.save_clip(user["id"], AUDIO + bytes([i]), 5) for i in range(5)]
    response = people.get(clips[-1]["url"])
    assert response.content == AUDIO + bytes([4]) and response.headers["content-type"] == "audio/mpeg"
    others = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert others.get(clips[-1]["url"]).status_code == 404
    # The newest MAX_CLIPS stay
    kept = [c for c in clips if speech.get_clip(user["id"], c["id"])]
    assert len(kept) == 3 and clips[-1] in kept
    assert speech.clip_of("https://labb.example" + clips[-1]["url"], user["id"]) == AUDIO + bytes([4])
    assert speech.clip_of(clips[-1]["url"], accounts["member"][0]["id"]) is None


def test_a_recording_becomes_a_document(fake, client, member, monkeypatch):
    user, people = member
    monkeypatch.setattr(documents, "_store", vectors.Store("documents", WordEmbeddings(), "test"))
    added = people.post("/documents", json={"name": "memo.m4a", "data": base64.b64encode(AUDIO).decode()})
    assert "Settings → Models → Speech" in added.json()["detail"]

    turn_on(client)
    doc = people.post("/documents", json={"name": "memo.m4a", "data": base64.b64encode(AUDIO).decode()}).json()
    assert fake.calls[-1][1]["mime"] == "audio/mp4"
    for _ in range(100):
        if documents.get(user["id"], doc["id"])["status"] != "indexing":
            break
        time.sleep(0.05)
    assert documents.pages_of(user["id"], doc["id"])["pages"] == ["Buy milk and call the plumber."]
    assert documents.search(user["id"], "plumber")[0]["document"] == "memo.m4a"


def test_transcribe_and_speak_steps(fake, client, alice, monkeypatch):  # noqa: F811 (the fixture)
    _, studio = alice
    definition = flow(
        node("say", "speak", text="**Good morning**, {{trigger.name}}"),
        node("hear", "transcribe", audio="{{say.url}}"),
        node("hear_data", "transcribe", audio="data:audio/wav;base64," + base64.b64encode(AUDIO).decode(), language="de"),
        ("trigger", "say"), ("say", "hear"), ("trigger", "hear_data"), inputs=[{"name": "name"}])
    workflow = make(studio, definition)
    # Without the models, the builder says what's missing
    problems = [p["message"] for p in workflow["problems"]]
    assert any("Speak steps need a model that speaks" in m for m in problems)
    assert any("Transcribe steps need a model that listens" in m for m in problems)

    turn_on(client)
    done = run(studio, make(studio, definition), {"name": "Ada"})
    assert done["status"] == "done", done
    said = step_of(done, "say")["output"]
    assert said["url"].startswith("/speech/clips/") and said["chars"] == len("Good morning, Ada.")
    assert studio.get(said["url"]).content == b"ID3Good morning, Ada."
    assert step_of(done, "hear")["output"] == "Buy milk and call the plumber."
    listened = [c[1] for c in fake.calls if c[0] == "listen"]
    assert {c["bytes"] for c in listened} == {len(b"ID3Good morning, Ada."), len(AUDIO)}
    from_data = next(c for c in listened if c["bytes"] == len(AUDIO))
    assert from_data["name"] == "recording.wav" and from_data["language"] == "de"

    # Not a recording, or a private address
    for audio, message in (("just words", "A recording is a link"), ("http://127.0.0.1/a.mp3", "private or local")):
        broken = run(studio, make(studio, flow(node("hear", "transcribe", audio=audio), ("trigger", "hear"))))
        assert broken["status"] == "failed" and message in step_of(broken, "hear")["error"], broken


def test_speaking_markdown_cleans_up_what_isnt_said():
    assert speech.spoken("| Name | Age |\n| --- | :-: |\n| Ada | 36 |") == "Name, Age.\nAda, 36."
    assert speech.spoken("See ![a chart](x.png) and https://example.com/a?b=c `code` _here_") == "See a chart and a link code here."
    assert speech.spoken("1. First\n2. Second\n> quoted!") == "First.\nSecond.\nquoted!"
    assert nodes.CATALOG["speak"]["category"] == "ai" and "transcribe" in nodes.LOOP_STEPS


def test_speaches_gets_the_models_picked_of_it(fake, monkeypatch):
    monkeypatch.setitem(llm.providers, "speaches", llm.Provider("speaches", "openai_compatible", "http://speaches:8000/v1"))
    has = ["Systran/faster-whisper-small"]
    asked = []

    def get(url, **kwargs):
        return httpx.Response(200, json={"data": [{"id": m} for m in has]}, request=httpx.Request("GET", url))

    def post(url, **kwargs):
        asked.append(url)
        has.append(url.split("/models/", 1)[1])
        return httpx.Response(200, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(speech.httpx, "get", get)
    monkeypatch.setattr(speech.httpx, "post", post)
    monkeypatch.setattr(speech, "LISTEN_MODEL", "speaches:Systran/faster-whisper-small")
    monkeypatch.setattr(speech, "SPEAK_MODEL", "speaches:speaches-ai/Kokoro-82M-v1.0-ONNX")
    speech._settings = None
    # Only what it doesn't have yet, and only once
    speech.fetch_models(wait=True)
    speech.fetch_models(wait=True)
    assert asked == ["http://speaches:8000/v1/models/speaches-ai/Kokoro-82M-v1.0-ONNX"]

    # Other providers' models are theirs to have
    monkeypatch.setattr(speech, "SPEAK_MODEL", "local:kokoro")
    speech._settings = None
    speech.fetch_models(wait=True)
    assert len(asked) == 1


def test_a_reply_is_read_in_parts_the_first_short(client):
    reply = "## Frost tonight\n\n" + " ".join(f"Sentence number {i} says something about the weather." for i in range(60))
    found = client.post("/speech/parts", json={"text": reply}).json()["parts"]
    assert found[0].startswith("Frost tonight.") and len(found[0]) <= speech.FIRST_PART
    assert len(found) > 2 and all(len(p) <= speech.PART for p in found[1:])
    # Nothing lost or doubled, in order
    assert " ".join(found).split() == speech.spoken(reply).split()
    assert client.post("/speech/parts", json={"text": "```py\n```"}).json() == {"parts": ["(code)"]}
    assert client.post("/speech/parts", json={"text": "x " * 20_001}).status_code == 400
