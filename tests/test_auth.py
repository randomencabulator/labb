"""Accounts, logging in, API keys, invites, labb --login, and the gateway."""

import httpx
import pytest
from fastapi.testclient import TestClient

from labb.server import auth
from labb import chats
from labb.server import gateway
from labb.server import app as server


def browser() -> TestClient:
    """A client with no key: it logs in with the session cookie, like the web page"""
    return TestClient(server.app)


def logged_in(username: str, password: str) -> TestClient:
    page = browser()
    assert page.post("/auth/login", json={"username": username, "password": password}, headers={"X-Labb": "1"}).status_code == 200
    return page


def with_key(key: str) -> TestClient:
    return TestClient(server.app, headers={"Authorization": f"Bearer {key}"})


def test_nothing_works_without_logging_in(accounts):
    page = browser()
    for path in ["/threads", "/models", "/cli_sessions", "/keys", "/ollama/api/tags", "/crawler/read"]:
        assert page.get(path).status_code == 401, path
    assert page.post("/chat", json={"message": "hi"}).status_code == 401
    assert with_key("lb_not-a-key").get("/threads").status_code == 401
    # What's public: health, the page, and installing labb
    assert page.get("/health").status_code == 200 and page.get("/version").status_code == 200
    assert page.get("/auth/status").json() == {"setup_needed": False, "setup_token_needed": False, "user": None}


def test_the_server_is_set_up_only_once(accounts):
    response = browser().post("/auth/setup", json={"username": "intruder", "password": "password123"}, headers={"X-Labb": "1"})
    assert response.status_code == 400 and "set up already" in response.json()["detail"]


def test_logging_in_and_out_with_the_cookie(accounts):
    page = logged_in("bob", "bob-password")
    assert page.get("/auth/status").json()["user"]["username"] == "bob"
    # Changes need the X-Labb header with the cookie, so other sites can't make them
    assert page.post("/keys", json={"name": "x"}).status_code == 403
    assert page.post("/keys", json={"name": "x"}, headers={"X-Labb": "1"}).status_code == 200
    page.post("/auth/logout", headers={"X-Labb": "1"})
    assert page.get("/threads").status_code == 401


def test_logging_in_needs_the_x_labb_header(accounts):
    """So another site can't post a login form and log you into its account"""
    page = browser()
    assert page.post("/auth/login", json={"username": "bob", "password": "bob-password"}).status_code == 403
    assert page.post("/auth/setup", json={"username": "x", "password": "password123"}).status_code == 403
    assert page.post("/auth/invite/nope", json={"username": "x", "password": "password123"}).status_code == 403


def test_the_first_account_takes_the_setup_token(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "SETUP_TOKEN_FILE", tmp_path / "setup-token")
    monkeypatch.setattr(auth, "has_users", lambda: False)
    made = []
    monkeypatch.setattr(auth, "create_user", lambda username, password, role="member": made.append(role) or {"id": "x"})
    token = auth.setup_token()
    assert token and auth.setup_token() == token and (tmp_path / "setup-token").stat().st_mode & 0o777 == 0o600
    for wrong in (None, "", "nope"):
        with pytest.raises(auth.SetupTokenError):
            auth.setup("first", "password123", wrong, need_token=True)
    assert made == []
    auth.setup("first", "password123", token, need_token=True)
    assert made == ["admin"] and not (tmp_path / "setup-token").exists()
    # From another machine, the endpoint asks for it (TestClient's address isn't this machine's)
    monkeypatch.setattr(auth, "SETUP_TOKEN_FILE", tmp_path / "again")
    response = browser().post("/auth/setup", json={"username": "first", "password": "password123"}, headers={"X-Labb": "1"})
    assert response.status_code == 403 and "setup link" in response.json()["detail"]
    status = browser().get("/auth/status").json()
    assert status["setup_needed"] and status["setup_token_needed"]


def test_failing_from_one_address_doesnt_lock_out_another(accounts):
    auth.failures.clear()
    for _ in range(auth.FREE_TRIES + 1):
        auth.note_login("bob", False, "10.0.0.66")
    assert auth.wait_before_login("bob", "10.0.0.66") > 0
    assert auth.wait_before_login("bob", "10.0.0.7") == 0
    # One address trying many usernames waits too
    auth.failures.clear()
    for n in range(auth.FREE_TRIES_PER_ADDRESS + 1):
        auth.note_login(f"user{n}", False, "10.0.0.66")
    assert auth.wait_before_login("someone-else", "10.0.0.66") > 0
    auth.failures.clear()


def test_failures_kept_are_bounded(monkeypatch):
    auth.failures.clear()
    monkeypatch.setattr(auth, "MAX_FAILURES", 50)
    for n in range(200):
        auth.note_login(f"made-up-{n}", False, f"10.1.0.{n % 250}")
    assert len(auth.failures) <= 50
    auth.failures.clear()


def test_wrong_passwords_are_refused_and_slowed_down(accounts):
    page = browser()
    assert page.post("/auth/login", json={"username": "bob", "password": "nope"}, headers={"X-Labb": "1"}).status_code == 401
    auth.failures.clear()
    for _ in range(auth.FREE_TRIES):
        page.post("/auth/login", json={"username": "ghost", "password": "nope"}, headers={"X-Labb": "1"})
    assert page.post("/auth/login", json={"username": "ghost", "password": "nope"}, headers={"X-Labb": "1"}).status_code == 429
    auth.failures.clear()


def test_everyone_sees_only_their_own_chats(client, accounts, fake_model):
    from conftest import ai

    fake_model(ai("Hi admin"))
    thread = next(line for line in client.post("/chat", json={"message": "mine"}).text.splitlines() if "thread_id" in line)
    thread_id = thread.split('"thread_id": "')[1].split('"')[0]
    assert chats.owner(thread_id) == (True, accounts["admin"][0]["id"])

    bob = with_key(accounts["member"][1])
    assert all(c["thread_id"] != thread_id for c in bob.get("/threads").json())
    for path in ["/history", "/export"]:
        assert bob.get(path, params={"thread_id": thread_id}).status_code == 404
    assert bob.patch(f"/threads/{thread_id}", json={"title": "taken"}).status_code == 404
    assert bob.delete(f"/threads/{thread_id}").status_code == 404
    assert bob.post("/chat", json={"message": "hi", "thread_id": thread_id}).status_code == 404
    assert client.get("/history", params={"thread_id": thread_id}).status_code == 200


def test_api_keys_are_shown_once_and_can_be_revoked(accounts):
    bob = with_key(accounts["member"][1])
    created = bob.post("/keys", json={"name": "CI"}).json()
    assert created["key"].startswith("lb_") and created["shown"] == created["key"][:12]
    listed = next(k for k in bob.get("/keys").json() if k["id"] == created["id"])
    assert "key" not in listed and listed["last_used_at"] is None

    ci = with_key(created["key"])
    assert ci.get("/threads").status_code == 200
    assert next(k for k in bob.get("/keys").json() if k["id"] == created["id"])["last_used_at"] is not None
    assert bob.delete(f"/keys/{created['id']}").status_code == 204
    assert ci.get("/threads").status_code == 401


def test_members_cant_use_the_admin_page(accounts):
    bob = with_key(accounts["member"][1])
    for path in ["/admin/users", "/admin/invites", "/admin/keys"]:
        assert bob.get(path).status_code == 403
    assert bob.post("/admin/invites", json={"role": "admin"}).status_code == 403


def test_invites_make_one_account(client):
    invite = client.post("/admin/invites", json={"role": "member"}).json()
    token = invite["url"].split("invite=")[1]
    assert browser().get(f"/auth/invite/{token}").json()["role"] == "member"
    page = browser()
    joined = page.post(f"/auth/invite/{token}", json={"username": "carol", "password": "carol-password"}, headers={"X-Labb": "1"})
    assert joined.status_code == 200 and joined.json()["role"] == "member"
    assert page.get("/auth/status").json()["user"]["username"] == "carol"
    # Once only
    assert browser().post(f"/auth/invite/{token}", json={"username": "dave", "password": "dave-password"}, headers={"X-Labb": "1"}).status_code == 400


def test_a_failed_join_keeps_the_invite(client):
    invite = client.post("/admin/invites", json={"role": "member"}).json()
    token = invite["url"].split("invite=")[1]
    # A taken username: the link still works afterwards
    taken = browser().post(f"/auth/invite/{token}", json={"username": "bob", "password": "password123"}, headers={"X-Labb": "1"})
    assert taken.status_code == 400 and "taken" in taken.json()["detail"]
    assert auth.open_invite(token) is not None
    assert browser().post(f"/auth/invite/{token}", json={"username": "frank", "password": "password123"}, headers={"X-Labb": "1"}).status_code == 200
    assert auth.open_invite(token) is None


def test_disabled_users_are_locked_out(client):
    user = auth.create_user("erin", "erin-password")
    key, _ = auth.create_key(user["id"], "laptop")
    assert with_key(key).get("/threads").status_code == 200
    assert client.patch(f"/admin/users/{user['id']}", json={"disabled": True}).json()["disabled"]
    assert with_key(key).get("/threads").status_code == 401
    assert browser().post("/auth/login", json={"username": "erin", "password": "erin-password"}, headers={"X-Labb": "1"}).status_code == 401
    # The last admin can't be demoted or disabled
    admins = [u for u in client.get("/admin/users").json() if u["role"] == "admin" and not u["disabled"]]
    if len(admins) == 1:
        assert client.patch(f"/admin/users/{admins[0]['id']}", json={"role": "member"}).status_code == 400


def test_labb_login_through_the_browser(accounts):
    labb = browser()
    start = labb.post("/auth/device", json={"machine": "laptop"}).json()
    assert start["verification_url"].endswith(f"/?device={start['user_code']}")
    assert labb.post("/auth/device/token", json={"device_code": start["device_code"]}).json() == {"status": "pending"}

    page = logged_in("bob", "bob-password")
    assert page.get(f"/auth/device/{start['user_code']}").json()["machine"] == "laptop"
    assert page.post(f"/auth/device/{start['user_code']}", json={"approve": True}, headers={"X-Labb": "1"}).status_code == 204

    approved = labb.post("/auth/device/token", json={"device_code": start["device_code"]}).json()
    assert approved["status"] == "approved" and approved["username"] == "bob"
    assert with_key(approved["api_key"]).get("/auth/status").json()["user"]["username"] == "bob"
    # The key is handed over once
    assert labb.post("/auth/device/token", json={"device_code": start["device_code"]}).json()["status"] == "expired"
    # labb --logout revokes it
    assert with_key(approved["api_key"]).post("/auth/revoke").status_code == 204
    assert with_key(approved["api_key"]).get("/threads").status_code == 401


@pytest.fixture
def upstream(monkeypatch):
    """Fake Ollama and crawler behind the gateway; returns the requests they got"""

    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/chat":
            return httpx.Response(200, content=b'{"message":{"content":"hi"},"done":false}\n{"done":true}\n',
                                  headers={"content-type": "application/x-ndjson"})
        if request.url.path == "/read":
            return httpx.Response(200, text="# A page", headers={"content-type": "text/markdown"})
        return httpx.Response(200, json={"models": []})

    monkeypatch.setattr(gateway, "client", httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(gateway, "pages", httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(gateway, "CRAWLER_URL", "http://crawler:3000")
    monkeypatch.setattr(gateway, "crawler_token", lambda: "crawler-token")
    return seen


def test_the_gateway_forwards_model_calls_and_page_reads(client, upstream):
    reply = client.post("/ollama/api/chat", json={"model": "qwen3.5:4b", "messages": []})
    assert reply.status_code == 200 and reply.text.count("\n") == 2
    assert upstream[-1].url.path == "/api/chat"
    assert client.get("/ollama/api/tags").json() == {"models": []}
    # Managing models isn't forwarded
    assert client.post("/ollama/api/pull", json={"model": "x"}).status_code == 404
    assert client.delete("/ollama/api/delete").status_code == 405
    # Page reads go to the crawler with its own token, not the PC's key
    page = client.get("/crawler/read", params={"url": "https://example.com/a?b=1", "format": "markdown"})
    assert page.text == "# A page"
    assert dict(upstream[-1].url.params) == {"url": "https://example.com/a?b=1", "format": "markdown"}
    assert upstream[-1].headers["authorization"] == "Bearer crawler-token"
    assert client.get("/crawler/admin").status_code == 404


def test_the_gateway_keeps_to_the_models_people_may_use(accounts, upstream, monkeypatch):
    from labb.server import providers

    def handle(request: httpx.Request) -> httpx.Response:
        upstream.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "small:latest"}, {"name": "big:70b"}]})
        return httpx.Response(200, content=b'{"done":true}\n')

    monkeypatch.setattr(gateway, "client", httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    offered = {"ollama:small", "ollama:big:70b"}
    monkeypatch.setattr(providers, "why_not", lambda spec, role: None if role == "admin" or spec == "ollama:small" else
                        (f"{spec} is for admins only" if spec in offered else f"{spec} isn't offered"))
    member = with_key(accounts["member"][1])
    assert member.post("/ollama/api/chat", json={"model": "small", "messages": []}).status_code == 200
    refused = member.post("/ollama/api/chat", json={"model": "big:70b", "messages": []})
    assert refused.status_code == 403 and "admins only" in refused.json()["detail"]
    assert [m["name"] for m in member.get("/ollama/api/tags").json()["models"]] == ["small:latest"]
    admin = with_key(accounts["admin"][1])
    assert admin.post("/ollama/api/chat", json={"model": "big:70b", "messages": []}).status_code == 200


def test_answers_carry_the_security_headers():
    response = browser().get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in response.headers["content-security-policy"]
