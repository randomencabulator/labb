"""Integrations (Google here; test_integrations.py has the others): admins set up Google's client, people connect their own account (OAuth with
PKCE, by redirect or by pasting the address), tokens are encrypted and refreshed, and the
calendar and mail tools are offered only to those who connected. Google is faked."""

import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from labb import connections as core
from labb import tools
from labb.chats import conn, lock
from labb.server import connections
from labb.tools import gmail, google_calendar


class FakeGoogle:
    """Google's sign-in and APIs, as far as the tools use them"""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.refresh_error: str | None = None
        self.issued = 0
        self.events: dict[str, dict] = {}
        self.drafts: list[dict] = []

    def token(self, extra: dict | None = None) -> httpx.Response:
        self.issued += 1
        claims = base64.urlsafe_b64encode(json.dumps({"email": "ann@example.com"}).encode()).decode().rstrip("=")
        return httpx.Response(200, json={"access_token": f"access-{self.issued}", "expires_in": 3600,
                                         "scope": " ".join(connections.APPS["google"].scopes), "id_token": f"x.{claims}.y",
                                         **(extra or {})})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url, path = str(request.url), request.url.path
        if url.startswith("https://oauth2.googleapis.com/token"):
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                if form["code"] != ["good-code"] or not form.get("code_verifier"):
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return self.token({"refresh_token": "refresh-1"})
            if self.refresh_error:
                return httpx.Response(400, json={"error": self.refresh_error})
            return self.token()
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            return httpx.Response(200)
        if not request.headers.get("authorization", "").startswith("Bearer access-"):
            return httpx.Response(401, json={"error": {"message": "bad token"}})
        if path == "/calendar/v3/calendars/primary":
            return httpx.Response(403, json={"error": {"code": 403, "message": "Request had insufficient authentication scopes."}})
        if path == "/calendar/v3/users/me/calendarList/primary":
            return httpx.Response(200, json={"id": "primary", "timeZone": "Europe/Berlin"})
        if path == "/calendar/v3/calendars/primary/events":
            if request.method == "POST":
                event = {**json.loads(request.content), "id": f"e{len(self.events) + 1}", "htmlLink": "https://calendar/x"}
                self.events[event["id"]] = event
                return httpx.Response(200, json=event)
            return httpx.Response(200, json={"items": list(self.events.values())})
        if path.startswith("/calendar/v3/calendars/primary/events/"):
            event = self.events[path.rsplit("/", 1)[1]]
            if request.method == "PATCH":
                event.update(json.loads(request.content))
            if request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(200, json=event)
        if path == "/gmail/v1/users/me/messages":
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        if path == "/gmail/v1/users/me/messages/m1":
            body = base64.urlsafe_b64encode(b"<p>Lunch <b>Friday</b>?</p>").decode().rstrip("=")
            return httpx.Response(200, json={
                "id": "m1", "threadId": "t1", "snippet": "Lunch Friday?", "labelIds": ["UNREAD", "INBOX"],
                "payload": {"mimeType": "multipart/mixed", "headers": [
                    {"name": "From", "value": "Bo <bo@example.com>"}, {"name": "Subject", "value": "Lunch"},
                    {"name": "Message-ID", "value": "<abc@mail>"}],
                    "parts": [{"mimeType": "text/html", "body": {"data": body}},
                              {"mimeType": "application/pdf", "filename": "menu.pdf", "body": {"attachmentId": "a1"}}]},
            })
        if path == "/gmail/v1/users/me/drafts":
            self.drafts.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "d1"})
        return httpx.Response(404, json={"error": {"message": f"no {path}"}})


@pytest.fixture
def google(monkeypatch):
    fake = FakeGoogle()
    client = httpx.Client(transport=httpx.MockTransport(fake))
    monkeypatch.setattr(connections, "http", client)
    monkeypatch.setattr(core, "http", client)
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    with lock, conn:
        conn.execute("DELETE FROM app_clients")
        conn.execute("DELETE FROM app_connections")
    google_calendar._zones.clear()
    yield fake
    with lock, conn:
        conn.execute("DELETE FROM app_clients")
        conn.execute("DELETE FROM app_connections")


def run_config(user_id: str) -> dict:
    return {"configurable": {"user_id": user_id}}


def connect(client, base="http://localhost:8000") -> str:
    """Set Google up and connect the admin through the callback; the user's id"""
    assert client.put("/admin/apps/google", json={"client_id": "id-1", "client_secret": "secret-1"}).status_code == 200
    started = connections.start(client.get("/auth/status").json()["user"]["id"], "google", base)
    state = parse_qs(urlsplit(started["url"]).query)["state"][0]
    response = client.get(f"/connections/google/callback?code=good-code&state={state}", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].endswith("connected=google")
    return client.get("/auth/status").json()["user"]["id"]


def test_admins_set_up_the_client_and_its_secret_stays_secret(client, google):
    listing = client.get("/admin/apps").json()
    assert listing[0]["name"] == "google" and not listing[0]["configured"]
    assert client.put("/admin/apps/google", json={"client_id": "id-1"}).status_code == 400

    saved = client.put("/admin/apps/google", json={"client_id": "id-1", "client_secret": "secret-1"}).json()[0]
    assert saved["configured"] and saved["client_id"] == "id-1" and saved["source"] == "saved"
    assert "secret-1" not in json.dumps(saved)
    with lock:
        stored = conn.execute("SELECT client_secret FROM app_clients").fetchone()[0]
    assert stored != "secret-1"
    # An empty secret keeps the one there is
    client.put("/admin/apps/google", json={"client_id": "id-2", "client_secret": ""})
    assert connections.client("google") == ("id-2", "secret-1")
    assert client.delete("/admin/apps/google").status_code == 204
    assert connections.configured() == set()


def test_members_cant_set_up_apps(accounts, google):
    from fastapi.testclient import TestClient
    from labb.server import app as server

    member = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert member.put("/admin/apps/google", json={"client_id": "a", "client_secret": "b"}).status_code == 403
    assert member.get("/connections").json()[0]["available"] is False


@pytest.mark.parametrize("url, mode", [
    ("http://localhost:8000", "redirect"),
    ("http://127.0.0.1:8000", "redirect"),
    ("https://labb.example.com", "redirect"),
    ("http://labb.example.com", "paste"),
    ("http://192.168.1.5:8000", "paste"),
    ("https://192.168.1.5", "paste"),
    ("https://server.local", "paste"),
])
def test_google_only_sends_people_back_to_localhost_or_public_https(url, mode):
    uri, found = connections.redirect_uri("google", url)
    assert found == mode
    if mode == "paste":
        assert uri.startswith("http://localhost") and uri.endswith("/connections/google/callback")


def test_connecting_by_redirect_keeps_encrypted_tokens(client, google):
    user_id = connect(client)
    assert not connections._pending  # the sign-in was used up
    token_call = parse_qs(google.requests[0].content.decode())
    assert token_call["code_verifier"] and token_call["redirect_uri"] == ["http://localhost:8000/connections/google/callback"]

    mine = client.get("/connections").json()[0]
    assert mine["connection"]["account"] == "ann@example.com" and mine["connection"]["status"] == "ok"
    assert mine["connection"]["missing"] == []
    with lock:
        access, refresh = conn.execute("SELECT access_token, refresh_token FROM app_connections WHERE user_id = ?", (user_id,)).fetchone()
    assert "access-1" not in access and "refresh-1" not in refresh
    assert connections.token(user_id, "google") == "access-1"


def test_the_authorize_address_asks_for_offline_access_with_pkce(client, google):
    client.put("/admin/apps/google", json={"client_id": "id-1", "client_secret": "secret-1"})
    started = client.post("/connections/google/start").json()
    query = parse_qs(urlsplit(started["url"]).query)
    assert query["client_id"] == ["id-1"] and query["access_type"] == ["offline"]
    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"]
    assert "https://www.googleapis.com/auth/gmail.compose" in query["scope"][0].split()
    # Sending mail isn't asked for
    assert "https://www.googleapis.com/auth/gmail.send" not in query["scope"][0]


def test_connecting_by_pasting_the_address(client, google):
    client.put("/admin/apps/google", json={"client_id": "id-1", "client_secret": "secret-1"})
    started = connections.start(client.get("/auth/status").json()["user"]["id"], "google", "http://192.168.1.5:8000")
    assert started["mode"] == "paste"
    state = parse_qs(urlsplit(started["url"]).query)["state"][0]
    assert client.post("/connections/google/finish", json={"url": "nonsense"}).status_code == 400
    pasted = f"http://localhost:8000/connections/google/callback?state={state}&code=good-code&scope=x"
    finished = client.post("/connections/google/finish", json={"url": pasted})
    assert finished.status_code == 200 and finished.json()["connection"]["status"] == "ok"
    # It works once
    assert client.post("/connections/google/finish", json={"url": pasted}).status_code == 400


def test_a_sign_in_only_finishes_for_whoever_started_it(client, accounts, google):
    client.put("/admin/apps/google", json={"client_id": "id-1", "client_secret": "secret-1"})
    started = connections.start(accounts["member"][0]["id"], "google", "http://localhost:8000")
    state = parse_qs(urlsplit(started["url"]).query)["state"][0]
    response = client.get(f"/connections/google/callback?code=good-code&state={state}", follow_redirects=False)
    assert "error=" in response.headers["location"]
    assert client.get("/connections").json()[0]["connection"] is None


def test_the_callback_without_a_login_says_to_paste_the_address(google):
    from fastapi.testclient import TestClient
    from labb.server import app as server

    page = TestClient(server.app).get("/connections/google/callback?code=c&state=s")
    assert page.status_code == 200 and "paste" in page.text and "state=s" in page.text


def test_tokens_are_refreshed_and_a_refusal_means_connecting_again(client, google):
    user_id = connect(client)
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(microsecond=0).isoformat()
    with lock, conn:
        conn.execute("UPDATE app_connections SET expires_at = ? WHERE user_id = ?", (past, user_id))
    assert connections.token(user_id, "google") == "access-2"
    assert connections.token(user_id, "google") == "access-2"

    google.refresh_error = "invalid_grant"
    with pytest.raises(core.AppError, match="connecting again"):
        connections.token(user_id, "google", refresh=True)
    assert client.get("/connections").json()[0]["connection"]["status"] == "expired"
    assert connections.Backend().connected(user_id) == set()


def test_the_tools_are_offered_only_to_people_who_connected(client, accounts, google):
    names = lambda config: {tools.name_of(t) for t in tools.available(False, tools.offered_for(config))}
    user_id = client.get("/auth/status").json()["user"]["id"]
    assert not names(run_config(user_id)) & {"google_calendar", "gmail"}

    connect(client)
    assert {"google_calendar", "gmail"} <= names(run_config(user_id))
    assert not names(run_config(accounts["member"][0]["id"])) & {"google_calendar", "gmail"}
    # Agents can be given them
    assert {"google_calendar", "gmail"} <= {t["name"] for t in client.get("/agents/tools").json()}

    assert client.delete("/connections/google").status_code == 204
    assert any(str(r.url).startswith("https://oauth2.googleapis.com/revoke") for r in google.requests)
    assert not names(run_config(user_id)) & {"google_calendar", "gmail"}


def test_calendar_events_in_the_calendars_time_zone(client, google):
    config = run_config(connect(client))

    made = json.loads(google_calendar.create_event.invoke({"title": "Dentist", "start": "2026-10-30T15:00"}, config))["created"]
    sent = google.events[made["id"]]
    assert sent["start"] == {"dateTime": "2026-10-30T15:00:00+01:00", "timeZone": "Europe/Berlin"}
    assert sent["end"]["dateTime"] == "2026-10-30T16:00:00+01:00"

    trip = json.loads(google_calendar.create_event.invoke({"title": "Trip", "start": "2026-11-02", "end": "2026-11-04"}, config))["created"]
    assert google.events[trip["id"]]["end"] == {"date": "2026-11-05"}
    assert trip["end"] == "2026-11-04" and trip["all_day"]

    listed = json.loads(google_calendar.list_events.invoke({"start": "2026-10-30", "end": "2026-11-05"}, config))
    assert listed["time_zone"] == "Europe/Berlin" and len(listed["events"]) == 2
    query = parse_qs(urlsplit(str(google.requests[-1].url)).query)
    assert query["timeMin"] == ["2026-10-30T00:00:00+01:00"] and query["timeMax"] == ["2026-11-06T00:00:00+01:00"]

    # Moving the start keeps the length
    moved = json.loads(google_calendar.update_event.invoke({"event_id": made["id"], "start": "2026-10-31T09:30"}, config))["updated"]
    assert moved["start"] == "2026-10-31T09:30:00+01:00" and moved["end"] == "2026-10-31T10:30:00+01:00"

    assert "Error" in google_calendar.create_event.invoke({"title": "x", "start": "soon"}, config)
    assert "Error" in google_calendar.create_event.invoke({"title": "x", "start": "2026-10-30T15:00", "end": "2026-10-30T14:00"}, config)
    assert json.loads(google_calendar.delete_event.invoke({"event_id": made["id"]}, config)) == {"deleted": made["id"]}


def test_without_a_connection_the_tools_say_so(client, google):
    client.put("/admin/apps/google", json={"client_id": "id-1", "client_secret": "secret-1"})
    answer = google_calendar.list_events.invoke({}, run_config(client.get("/auth/status").json()["user"]["id"]))
    assert answer.startswith("Error") and "Settings → Integrations" in answer


def test_mail_is_searched_read_and_drafted_never_sent(client, google):
    config = run_config(connect(client))

    found = json.loads(gmail.search_mail.invoke({"query": "is:unread"}, config))
    assert found == [{"id": "m1", "from": "Bo <bo@example.com>", "subject": "Lunch", "snippet": "Lunch Friday?", "unread": True}]

    read = json.loads(gmail.read_mail.invoke({"message_id": "m1"}, config))
    assert read["text"] == "Lunch Friday?" and read["attachments"] == ["menu.pdf"] and read["thread_id"] == "t1"

    made = json.loads(gmail.draft_mail.invoke({"to": "bo@example.com", "subject": "", "body": "Yes!", "reply_to": "m1"}, config))
    assert made["not_sent"] and made["draft_id"] == "d1"
    draft = google.drafts[0]["message"]
    raw = base64.urlsafe_b64decode(draft["raw"]).decode()
    assert draft["threadId"] == "t1" and "Subject: Re: Lunch" in raw and "In-Reply-To: <abc@mail>" in raw
    assert not any("send" in r.url.path for r in google.requests)
