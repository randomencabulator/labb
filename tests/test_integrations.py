"""Integrations besides Google: Microsoft 365 (Outlook mail and calendar), GitHub and Notion. How
each signs in (PKCE or not, the client in the form or as Basic auth, tokens that expire or don't,
who connected, revoking) and what their tools send and show. The apps are faked."""

import base64
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from labb import connections as core
from labb import tools
from labb.chats import conn, lock
from labb.server import connections
from labb.tools import github, notion, outlook_calendar, outlook_mail


def claims(**values) -> str:
    return "x." + base64.urlsafe_b64encode(json.dumps(values).encode()).decode().rstrip("=") + ".y"


class FakeApps:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.issued = 0
        self.events: dict[str, dict] = {}
        self.messages: list[dict] = []
        self.issues: list[dict] = []
        self.blocks: list[dict] = []
        self.pages: list[dict] = []

    def body(self, request) -> dict:
        return json.loads(request.content) if request.content else {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url, path = str(request.url), request.url.path
        # Sign-ins
        if url.startswith("https://login.microsoftonline.com/common/oauth2/v2.0/token"):
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["authorization_code"] and not form.get("code_verifier"):
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.issued += 1
            return httpx.Response(200, json={"access_token": f"access-{self.issued}", "refresh_token": f"refresh-{self.issued}",
                                             "expires_in": 3600, "scope": "User.Read MailboxSettings.Read Mail.ReadWrite Calendars.ReadWrite",
                                             "id_token": claims(preferred_username="ann@contoso.com")})
        if url.startswith("https://github.com/login/oauth/access_token"):
            assert request.headers["accept"] == "application/json"
            return httpx.Response(200, json={"access_token": "access-gh", "scope": "repo,read:user", "token_type": "bearer"})
        if url.startswith("https://api.notion.com/v1/oauth/token"):
            # The client as Basic auth, and a JSON body
            assert request.headers["authorization"] == "Basic " + base64.b64encode(b"id-1:secret-1").decode()
            assert "code_verifier" not in self.body(request)
            return httpx.Response(200, json={"access_token": "access-notion", "workspace_name": "Ann's",
                                             "owner": {"type": "user", "user": {"person": {"email": "ann@example.com"}}}})
        if url.startswith("https://api.github.com/applications/"):
            return httpx.Response(204)
        if not request.headers.get("authorization", "").startswith("Bearer access-"):
            return httpx.Response(401, json={"message": "Bad credentials"})
        # Microsoft Graph
        if path == "/v1.0/me/mailboxSettings":
            return httpx.Response(200, json={"timeZone": "W. Europe Standard Time"})
        if path == "/v1.0/me/events" and request.method == "POST":
            event = {**self.body(request), "id": f"e{len(self.events) + 1}"}
            self.events[event["id"]] = event
            return httpx.Response(201, json=event)
        if path.startswith("/v1.0/me/events/"):
            event = self.events[path.rsplit("/", 1)[1]]
            if request.method == "PATCH":
                event.update(self.body(request))
            if request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(200, json=event)
        if path == "/v1.0/me/calendarView":
            return httpx.Response(200, json={"value": list(self.events.values())})
        if path in ("/v1.0/me/messages", "/v1.0/me/mailFolders/inbox/messages") and request.method == "GET":
            return httpx.Response(200, json={"value": [{"id": "m1", "subject": "Lunch", "isRead": False, "bodyPreview": "Friday?",
                                                        "receivedDateTime": "2026-11-10T09:00:00Z",
                                                        "from": {"emailAddress": {"name": "Bo", "address": "bo@example.com"}}}]})
        if path == "/v1.0/me/messages/m1":
            return httpx.Response(200, json={"id": "m1", "subject": "Lunch", "hasAttachments": True, "body": {"content": "Friday?"},
                                             "from": {"emailAddress": {"name": "Bo", "address": "bo@example.com"}},
                                             "toRecipients": [{"emailAddress": {"address": "ann@contoso.com"}}]})
        if path == "/v1.0/me/messages/m1/attachments":
            return httpx.Response(200, json={"value": [{"name": "menu.pdf"}]})
        if path == "/v1.0/me/messages/m1/createReply":
            self.messages.append({"reply": self.body(request)})
            return httpx.Response(201, json={"id": "d1", "webLink": "https://outlook/d1"})
        if path == "/v1.0/me/messages/d1" and request.method == "PATCH":
            self.messages[-1]["patch"] = self.body(request)
            return httpx.Response(200, json={"id": "d1", "webLink": "https://outlook/d1"})
        if path == "/v1.0/me/messages" and request.method == "POST":
            self.messages.append({"new": self.body(request)})
            return httpx.Response(201, json={"id": "d2"})
        # GitHub
        if path == "/user":
            return httpx.Response(200, json={"login": "ann"})
        if path == "/search/issues":
            return httpx.Response(200, json={"items": [{"number": 7, "title": "Crash", "state": "open", "user": {"login": "bo"},
                                                        "repository_url": "https://api.github.com/repos/acme/app",
                                                        "html_url": "https://github.com/acme/app/issues/7"}]})
        if path == "/repos/acme/app/issues/7":
            return httpx.Response(200, json={"number": 7, "title": "Crash", "state": "open", "body": "It crashes", "comments": 1,
                                             "user": {"login": "bo"}, "labels": [{"name": "bug"}], "pull_request": {"url": "x"}})
        if path == "/repos/acme/app/pulls/7":
            return httpx.Response(200, json={"merged": False, "head": {"label": "bo:fix"}, "base": {"ref": "main"}})
        if path == "/repos/acme/app/pulls/7/files":
            return httpx.Response(200, json=[{"filename": "app.py", "status": "modified", "additions": 3, "deletions": 1}])
        if path == "/repos/acme/app/issues/7/comments":
            if request.method == "POST":
                return httpx.Response(201, json={"html_url": "https://github.com/acme/app/issues/7#c2"})
            return httpx.Response(200, json=[{"user": {"login": "ann"}, "body": "Looking"}])
        if path == "/repos/acme/app/contents/README.md":
            return httpx.Response(200, json={"path": "README.md", "encoding": "base64",
                                             "content": base64.b64encode(b"# App\n").decode()})
        if path == "/repos/acme/app/issues" and request.method == "POST":
            self.issues.append(self.body(request))
            return httpx.Response(201, json={"number": 8, "html_url": "https://github.com/acme/app/issues/8"})
        # Notion
        if path == "/v1/search":
            return httpx.Response(200, json={"results": [{"object": "page", "id": "1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f",
                                                          "url": "https://www.notion.so/Plans-1a2b3c4d1a2b1a2b1a2b1a2b3c4d5e6f",
                                                          "properties": {"Name": {"type": "title", "title": [{"plain_text": "Plans"}]}}}]})
        if path == "/v1/pages/1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f":
            return httpx.Response(200, json={"object": "page", "id": "1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f",
                                             "properties": {"title": {"type": "title", "title": [{"plain_text": "Plans"}]}}})
        if path == "/v1/blocks/1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f/children":
            if request.method == "PATCH":
                self.blocks += self.body(request)["children"]
                return httpx.Response(200, json={"results": []})
            return httpx.Response(200, json={"has_more": False, "results": [
                {"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Q1"}]}},
                {"type": "to_do", "to_do": {"checked": True, "rich_text": [{"plain_text": "Ship"}]}}]})
        if path == "/v1/pages" and request.method == "POST":
            self.pages.append(self.body(request))
            return httpx.Response(200, json={"id": "new-page", "url": "https://www.notion.so/new"})
        return httpx.Response(404, json={"message": f"no {path}"})


@pytest.fixture
def apps(monkeypatch):
    fake = FakeApps()
    client = httpx.Client(transport=httpx.MockTransport(fake))
    monkeypatch.setattr(connections, "http", client)
    monkeypatch.setattr(core, "http", client)
    with lock, conn:
        conn.execute("DELETE FROM app_clients")
        conn.execute("DELETE FROM app_connections")
    outlook_calendar._zones.clear()
    yield fake
    with lock, conn:
        conn.execute("DELETE FROM app_clients")
        conn.execute("DELETE FROM app_connections")


def connect(client, app: str, base="http://localhost:8000") -> tuple[dict, dict]:
    """Set the app up and connect the admin through the callback: (the authorize query, the run config)"""
    assert client.put(f"/admin/apps/{app}", json={"client_id": "id-1", "client_secret": "secret-1"}).status_code == 200
    user_id = client.get("/auth/status").json()["user"]["id"]
    started = connections.start(user_id, app, base)
    query = parse_qs(urlsplit(started["url"]).query)
    response = client.get(f"/connections/{app}/callback?code=good-code&state={query['state'][0]}", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == f"/settings/integrations?connected={app}"
    return query, {"configurable": {"user_id": user_id}}


def mine(client, app: str) -> dict:
    return next(a for a in client.get("/connections").json() if a["name"] == app)


def test_every_integration_is_listed_with_how_to_set_it_up(client, apps):
    listed = {a["name"]: a for a in client.get("/admin/apps").json()}
    assert list(listed) == ["google", "microsoft", "github", "notion"]
    assert all(a["steps"] and a["console"].startswith("https://") for a in listed.values())
    # GitHub sends people back to any address, so a LAN server needn't paste
    assert listed["github"]["mode"] == "redirect"
    lan = {a["name"]: a["mode"] for a in connections.apps_listing("http://labb.lan:8000")}
    assert lan == {"google": "paste", "microsoft": "paste", "github": "redirect", "notion": "paste"}


def test_microsoft_signs_in_with_pkce_and_refreshes_asking_for_its_scopes(client, apps):
    query, config = connect(client, "microsoft")
    assert query["code_challenge_method"] == ["S256"] and "offline_access" in query["scope"][0]
    shown = mine(client, "microsoft")["connection"]
    assert shown["account"] == "ann@contoso.com" and shown["missing"] == []
    connections.token(config["configurable"]["user_id"], "microsoft", refresh=True)
    refresh = parse_qs(apps.requests[-1].content.decode())
    assert refresh["grant_type"] == ["refresh_token"] and "Mail.ReadWrite" in refresh["scope"][0]


def test_github_tokens_dont_expire_and_disconnecting_revokes_the_grant(client, apps):
    query, config = connect(client, "github")
    assert query["scope"] == ["repo read:user"]
    assert mine(client, "github")["connection"]["account"] == "ann"
    with lock:
        expires = conn.execute("SELECT expires_at FROM app_connections WHERE app = 'github'").fetchone()[0]
    assert expires == connections.NEVER
    assert connections.token(config["configurable"]["user_id"], "github") == "access-gh"
    assert client.delete("/connections/github").status_code == 204
    revoke = apps.requests[-1]
    assert revoke.method == "DELETE" and revoke.url.path == "/applications/id-1/grant"
    assert json.loads(revoke.content) == {"access_token": "access-gh"}


def test_notion_signs_in_with_basic_auth_and_no_pkce(client, apps):
    query, _ = connect(client, "notion")
    assert "code_challenge" not in query and query["owner"] == ["user"] and "scope" not in query
    assert mine(client, "notion")["connection"]["account"] == "ann@example.com"


def test_each_toolset_is_offered_only_to_people_who_connected_its_app(client, apps):
    names = lambda config: {tools.name_of(t) for t in tools.available(False, tools.offered_for(config))}
    _, config = connect(client, "github")
    found = names(config)
    assert "github" in found and not found & {"outlook_mail", "outlook_calendar", "notion"}


def test_outlook_mail_is_searched_read_and_drafted_never_sent(client, apps):
    _, config = connect(client, "microsoft")
    found = json.loads(outlook_mail.search_outlook_mail.invoke({"query": 'from:bo "lunch"'}, config))
    assert found[0] == {"id": "m1", "from": "Bo <bo@example.com>", "subject": "Lunch", "date": "2026-11-10T09:00:00Z",
                        "snippet": "Friday?", "unread": True}
    assert parse_qs(urlsplit(str(apps.requests[-1].url)).query)["$search"] == ['"from:bo  lunch "']

    read = json.loads(outlook_mail.read_outlook_mail.invoke({"message_id": "m1"}, config))
    assert read["text"] == "Friday?" and read["attachments"] == ["menu.pdf"]
    assert apps.requests[-2].headers["prefer"] == 'outlook.body-content-type="text"'

    reply = json.loads(outlook_mail.draft_outlook_mail.invoke({"to": "Bo <bo@example.com>", "subject": "", "body": "Yes!",
                                                               "reply_to": "m1"}, config))
    assert reply == {"draft_id": "d1", "not_sent": True, "open_drafts": "https://outlook/d1"}
    assert apps.messages[0]["reply"] == {"comment": "Yes!"}
    assert apps.messages[0]["patch"] == {"toRecipients": [{"emailAddress": {"address": "bo@example.com", "name": "Bo"}}]}
    json.loads(outlook_mail.draft_outlook_mail.invoke({"to": "cy@example.com", "subject": "Hi", "body": "Hello"}, config))
    assert apps.messages[1]["new"]["subject"] == "Hi"
    assert not any("send" in r.url.path.lower() for r in apps.requests)


def test_outlook_events_in_the_mailboxs_time_zone(client, apps):
    _, config = connect(client, "microsoft")
    made = json.loads(outlook_calendar.create_outlook_event.invoke({"title": "Dentist", "start": "2026-10-30T15:00"}, config))
    sent = apps.events["e1"]
    # Graph gets the wall time and the mailbox's own zone name
    assert sent["start"] == {"dateTime": "2026-10-30T15:00:00", "timeZone": "W. Europe Standard Time"}
    assert sent["end"]["dateTime"] == "2026-10-30T16:00:00" and sent["isAllDay"] is False
    assert made["created"]["start"] == "2026-10-30T15:00:00+01:00"
    assert apps.requests[-1].headers["prefer"] == 'outlook.timezone="W. Europe Standard Time"'

    trip = json.loads(outlook_calendar.create_outlook_event.invoke({"title": "Trip", "start": "2026-11-02", "end": "2026-11-04"}, config))
    assert apps.events["e2"]["end"]["dateTime"] == "2026-11-05T00:00:00" and apps.events["e2"]["isAllDay"]
    assert trip["created"]["end"] == "2026-11-04" and trip["created"]["all_day"]

    listed = json.loads(outlook_calendar.list_outlook_events.invoke({"start": "2026-10-30", "end": "2026-11-05", "query": "dentist"}, config))
    assert listed["time_zone"] == "Europe/Berlin" and [e["title"] for e in listed["events"]] == ["Dentist"]
    view = parse_qs(urlsplit(str(apps.requests[-1].url)).query)
    assert view["startDateTime"] == ["2026-10-30T00:00:00+01:00"] and view["endDateTime"] == ["2026-11-06T00:00:00+01:00"]

    moved = json.loads(outlook_calendar.update_outlook_event.invoke({"event_id": "e1", "start": "2026-10-31T09:30"}, config))["updated"]
    assert moved["start"] == "2026-10-31T09:30:00+01:00" and moved["end"] == "2026-10-31T10:30:00+01:00"
    assert json.loads(outlook_calendar.delete_outlook_event.invoke({"event_id": "e1"}, config)) == {"deleted": "e1"}


def test_a_zone_labb_doesnt_know_is_worked_in_utc(client, apps, monkeypatch):
    _, config = connect(client, "microsoft")
    monkeypatch.setitem(outlook_calendar.WINDOWS_ZONES, "W. Europe Standard Time", "Nowhere/Atlantis")
    json.loads(outlook_calendar.create_outlook_event.invoke({"title": "Call", "start": "2026-10-30T15:00"}, config))
    assert apps.events["e1"]["start"] == {"dateTime": "2026-10-30T15:00:00", "timeZone": "UTC"}


def test_github_is_searched_and_read_and_posts_only_what_its_asked(client, apps):
    _, config = connect(client, "github")
    found = json.loads(github.search_github.invoke({"query": "repo:acme/app crash"}, config))
    assert found[0]["repo"] == "acme/app" and found[0]["number"] == 7
    assert parse_qs(urlsplit(str(apps.requests[-1].url)).query)["q"] == ["repo:acme/app crash is:issue"]
    assert apps.requests[-1].headers["accept"] == "application/vnd.github+json"

    read = json.loads(github.read_github_issue.invoke({"repo": "acme/app", "number": 7}, config))
    assert read["labels"] == ["bug"] and read["comments"][0]["text"] == "Looking"
    assert read["files"] == [{"path": "app.py", "status": "modified", "added": 3, "removed": 1}]
    assert read["pull_request"]["into"] == "main"

    assert json.loads(github.read_github_file.invoke({"repo": "https://github.com/acme/app", "path": "README.md"}, config))["text"] == "# App"
    assert github.read_github_file.invoke({"repo": "not a repo", "path": "x"}, config).startswith("Error")

    made = json.loads(github.create_github_issue.invoke({"repo": "acme/app", "title": "Flaky", "body": "Sometimes"}, config))
    assert made["created"]["number"] == 8 and apps.issues == [{"title": "Flaky", "body": "Sometimes"}]
    assert "#c2" in json.loads(github.comment_on_github.invoke({"repo": "acme/app", "number": 7, "body": "Fixed"}, config))["commented"]


def test_notion_pages_are_found_read_and_written(client, apps):
    _, config = connect(client, "notion")
    found = json.loads(notion.search_notion.invoke({"query": "plans"}, config))
    assert found[0]["title"] == "Plans" and found[0]["type"] == "page"
    assert apps.requests[-1].headers["notion-version"] == "2022-06-28"

    # From its link
    read = json.loads(notion.read_notion_page.invoke({"page": found[0]["url"]}, config))
    assert read["title"] == "Plans" and read["text"] == "# Q1\n[x] Ship"

    made = json.loads(notion.create_notion_page.invoke({"parent": found[0]["id"], "title": "Notes",
                                                        "content": "# Goals\n- Ship it\n[ ] Test\nPlain words"}, config))
    assert made["created"]["id"] == "new-page"
    assert [b["type"] for b in apps.pages[0]["children"]] == ["heading_1", "bulleted_list_item", "to_do", "paragraph"]
    assert apps.pages[0]["parent"] == {"page_id": "1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f"}

    added = json.loads(notion.append_to_notion_page.invoke({"page": found[0]["id"], "content": "x" * 4500}, config))
    # Notion takes 2,000 characters a piece
    assert added == {"added_blocks": 3} and len(apps.blocks) == 3
    assert notion.read_notion_page.invoke({"page": "nope"}, config).startswith("Error")
