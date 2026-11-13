"""The API container's gateway (backend/gateway.py): passwords and tokens on their own, and sign-in's
endpoints against a real Postgres (SPAWNER_TEST_DATABASE_URL, as in test_database.py) without PostgREST."""

import http.client
import importlib.util
import json
import os
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest

from labb_spawner import database

URL = os.getenv("SPAWNER_TEST_DATABASE_URL", "")
A = "aaaaaaaaaaa1"
BACKEND = Path(__file__).parent.parent / "backend"


@pytest.fixture(scope="module")
def gateway():
    os.environ["API_JWT_SECRET"] = "s" * 48
    spec = importlib.util.spec_from_file_location("gateway", BACKEND / "gateway.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_passwords_are_scrypt_hashes_that_check(gateway):
    stored = gateway.hash_password("correct horse")
    kind, n, r, p, salt, digest = stored.split("$")
    assert (kind, int(n), int(r), int(p)) == ("scrypt", 2 ** 14, 8, 5)
    assert gateway.check_password("correct horse", stored)
    assert not gateway.check_password("correct horsf", stored)
    assert gateway.hash_password("correct horse") != stored  # salted
    for broken in ("", "x", "md5$1$2$3$4$5", stored.replace("$16384$", "$999999999$"), "scrypt$a$b$c$d$e"):
        assert not gateway.check_password("correct horse", broken)


def test_tokens_are_checked_whole(gateway):
    claims = {"sub": "u", "role": "authenticated", "exp": int(time.time()) + 60}
    token = gateway.sign(claims)
    assert gateway.verify(token) == claims
    header, payload, signature = token.split(".")
    forged = gateway._b64(json.dumps({**claims, "role": "app_x"}).encode())
    none_alg = gateway._b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    for bad in (f"{header}.{forged}.{signature}", f"{none_alg}.{payload}.", f"{none_alg}.{payload}.{signature}",
                token[:-2] + "AA", "a.b", "", "...", f"{header}.{payload}.{gateway._b64(b'x' * 32)}"):
        assert gateway.verify(bad) is None, bad
    expired = gateway.sign({**claims, "exp": int(time.time()) - 1})
    assert gateway.verify(expired) is None and gateway.verify(expired, expired_too=True)["sub"] == "u"
    assert gateway.verify(gateway.sign({"sub": "u"})) is None  # no expiry: not ours


def test_limits(gateway):
    limit = gateway.Limit(2, 60)
    limit.add("k"); limit.check("k"); limit.add("k")
    with pytest.raises(gateway.Refused) as caught:
        limit.check("k")
    assert caught.value.status == 429 and 0 < caught.value.retry_after <= 61
    limit.check("other")
    limit.clear("k"); limit.check("k")


# Its endpoints, against a real database -------------------------------------------------------------

needs_database = pytest.mark.skipif(not URL, reason="SPAWNER_TEST_DATABASE_URL isn't set")


@pytest.fixture
def served(gateway, monkeypatch):
    monkeypatch.setattr(database, "ADMIN_URL", URL)
    monkeypatch.setattr(database, "_server_ready", False)
    database.drop(A)
    owner = database.create(A)
    made = database.enable_api(A)
    monkeypatch.setattr(gateway, "AUTH_DB_URI", psycopg.conninfo.make_conninfo(
        URL, user=f"app_{A}_auth", password=made["auth_password"], dbname=f"app_{A}"))
    for limit in (gateway.failed_by_email, gateway.failed_by_address, gateway.signups_by_address):
        monkeypatch.setattr(limit, "events", {})
    server = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def call(method, path, body=None, token=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        connection.request(method, path, body=raw if raw is not None else (json.dumps(body) if body is not None else None),
                           headers=headers)
        response = connection.getresponse()
        data = response.read()
        return response.status, (json.loads(data) if data and response.getheader("Content-Type", "").startswith("application/json")
                                 else data), dict(response.getheaders())

    call.owner = owner
    yield call
    server.shutdown()
    database.drop(A)


@needs_database
def test_sign_up_sign_in_and_who(gateway, served):
    status, body, headers = served("POST", "/auth/signup", {"email": " Ann@Example.com ", "password": "hunter2hunter2"})
    assert status == 201 and body["user"]["email"] == "ann@example.com" and body["token_type"] == "bearer"
    assert headers["Access-Control-Allow-Origin"] == "*" and headers["Cache-Control"] == "no-store"
    claims = gateway.verify(body["access_token"])
    assert claims["role"] == "authenticated" and claims["sub"] == body["user"]["id"] and claims["exp"] - claims["iat"] == 900
    assert served("GET", "/auth/user", token=body["access_token"])[1]["email"] == "ann@example.com"
    # Kept as a hash, the refresh token too
    with psycopg.connect(gateway.AUTH_DB_URI) as c:
        assert c.execute("SELECT password_hash LIKE 'scrypt$%%' FROM auth.users").fetchone() == (True,)
        assert c.execute("SELECT token_hash FROM auth.refresh_tokens").fetchone()[0] != body["refresh_token"]
    # The same email again (any case) is refused; a bad one, or a short password, too
    assert served("POST", "/auth/signup", {"email": "ANN@example.com", "password": "another one"})[0] == 409
    assert served("POST", "/auth/signup", {"email": "not-an-email", "password": "hunter2hunter2"})[0] == 400
    assert served("POST", "/auth/signup", {"email": "bob@example.com", "password": "short"})[0] == 400
    assert served("POST", "/auth/signup", {"email": "bob@example.com"})[0] == 400
    assert served("POST", "/auth/signup", raw="{not json")[0] == 400
    assert served("POST", "/auth/signup", raw="[1]")[0] == 400
    # Signing in
    status, signed, _ = served("POST", "/auth/signin", {"email": "ann@example.com", "password": "hunter2hunter2"})
    assert status == 200 and signed["refresh_token"] != body["refresh_token"]
    assert served("POST", "/auth/signin", {"email": "ann@example.com", "password": "wrong password"})[:2] == \
        (401, {"message": "Wrong email or password"})
    assert served("POST", "/auth/signin", {"email": "nobody@example.com", "password": "wrong password"})[:2] == \
        (401, {"message": "Wrong email or password"})
    # Who, without a good token
    for token in (None, "nonsense", gateway.sign({"sub": body["user"]["id"], "role": "authenticated", "exp": int(time.time()) - 5,
                                                  "session_id": claims["session_id"]})):
        assert served("GET", "/auth/user", token=token)[0] == 401


@needs_database
def test_wrong_passwords_are_limited(gateway, served):
    served("POST", "/auth/signup", {"email": "ann@example.com", "password": "hunter2hunter2"})
    for _ in range(10):
        assert served("POST", "/auth/signin", {"email": "ann@example.com", "password": "guess guess"})[0] == 401
    status, body, headers = served("POST", "/auth/signin", {"email": "ann@example.com", "password": "hunter2hunter2"})
    assert status == 429 and int(headers["Retry-After"]) > 0


@needs_database
def test_refreshing_replaces_the_token_and_a_copy_ends_the_sign_in(gateway, served, monkeypatch):
    first = served("POST", "/auth/signup", {"email": "ann@example.com", "password": "hunter2hunter2"})[1]
    status, second, _ = served("POST", "/auth/refresh", {"refresh_token": first["refresh_token"]})
    assert status == 200 and second["refresh_token"] != first["refresh_token"]
    assert gateway.verify(second["access_token"])["session_id"] == gateway.verify(first["access_token"])["session_id"]
    # The old one again, right away (two tabs at once): new tokens, the same sign-in
    assert served("POST", "/auth/refresh", {"refresh_token": first["refresh_token"]})[0] == 200
    # ... but after the grace, it's a copy: the sign-in ends, for whoever had the newer one too
    monkeypatch.setattr(gateway, "REUSE_GRACE_SECONDS", 0)
    time.sleep(0.05)
    assert served("POST", "/auth/refresh", {"refresh_token": first["refresh_token"]})[:2] == (401, {"message": gateway.ENDED})
    assert served("POST", "/auth/refresh", {"refresh_token": second["refresh_token"]})[0] == 401
    assert served("GET", "/auth/user", token=second["access_token"])[0] == 401
    assert served("POST", "/auth/refresh", {"refresh_token": "made-up"})[0] == 401
    assert served("POST", "/auth/refresh", {})[0] == 400


@needs_database
def test_signing_out_ends_that_sign_in_only(gateway, served):
    here = served("POST", "/auth/signup", {"email": "ann@example.com", "password": "hunter2hunter2"})[1]
    there = served("POST", "/auth/signin", {"email": "ann@example.com", "password": "hunter2hunter2"})[1]
    assert served("POST", "/auth/signout", {"refresh_token": here["refresh_token"]})[0] == 204
    assert served("GET", "/auth/user", token=here["access_token"])[0] == 401
    assert served("POST", "/auth/refresh", {"refresh_token": here["refresh_token"]})[0] == 401
    assert served("GET", "/auth/user", token=there["access_token"])[0] == 200
    # With the access token alone, expired even
    assert served("POST", "/auth/signout", token=there["access_token"])[0] == 204
    assert served("GET", "/auth/user", token=there["access_token"])[0] == 401
    assert served("POST", "/auth/signout", {})[0] == 400


@needs_database
def test_a_disabled_user_cant_sign_in_or_refresh(gateway, served):
    signed = served("POST", "/auth/signup", {"email": "ann@example.com", "password": "hunter2hunter2"})[1]
    database.set_user_disabled(A, served.owner, signed["user"]["id"], True)
    assert served("POST", "/auth/signin", {"email": "ann@example.com", "password": "hunter2hunter2"})[:2] == \
        (403, {"message": "This account is disabled"})
    # A wrong password says only that, disabled or not
    assert served("POST", "/auth/signin", {"email": "ann@example.com", "password": "not it at all"})[0] == 401
    assert served("POST", "/auth/refresh", {"refresh_token": signed["refresh_token"]})[0] == 401
    assert served("GET", "/auth/user", token=signed["access_token"])[0] == 401
    database.set_user_disabled(A, served.owner, signed["user"]["id"], False)
    assert served("POST", "/auth/signin", {"email": "ann@example.com", "password": "hunter2hunter2"})[0] == 200


@needs_database
def test_changing_the_password_ends_the_other_sign_ins(gateway, served):
    here = served("POST", "/auth/signup", {"email": "ann@example.com", "password": "hunter2hunter2"})[1]
    there = served("POST", "/auth/signin", {"email": "ann@example.com", "password": "hunter2hunter2"})[1]
    change = {"password": "hunter2hunter2", "new_password": "a better one"}
    assert served("POST", "/auth/password", change)[0] == 401
    assert served("POST", "/auth/password", {**change, "password": "wrong"}, token=here["access_token"])[0] == 401
    assert served("POST", "/auth/password", {**change, "new_password": "short"}, token=here["access_token"])[0] == 400
    assert served("POST", "/auth/password", change, token=here["access_token"])[0] == 204
    assert served("GET", "/auth/user", token=here["access_token"])[0] == 200
    assert served("GET", "/auth/user", token=there["access_token"])[0] == 401
    assert served("POST", "/auth/signin", {"email": "ann@example.com", "password": "hunter2hunter2"})[0] == 401
    assert served("POST", "/auth/signin", {"email": "ann@example.com", "password": "a better one"})[0] == 200


@needs_database
def test_sign_ups_can_be_closed_and_the_rest(gateway, served, monkeypatch):
    monkeypatch.setattr(gateway, "SIGNUPS", False)
    assert served("POST", "/auth/signup", {"email": "ann@example.com", "password": "hunter2hunter2"})[0] == 403
    status, _, headers = served("OPTIONS", "/auth/signin")
    assert status == 204 and "Authorization" in headers["Access-Control-Allow-Headers"]
    assert served("GET", "/auth/signin")[0] == 405
    assert served("POST", "/auth/nope", {})[0] == 404
    status, body, headers = served("GET", "/client.js")
    assert status == 200 and headers["Content-Type"].startswith("text/javascript") and b"export async function signIn" in body
    assert served("GET", "/")[1]["signups"] is False
    # Its database gone: said plainly
    monkeypatch.setattr(gateway, "AUTH_DB_URI", "postgresql://nobody:x@127.0.0.1:1/x")
    assert served("POST", "/auth/signin", {"email": "ann@example.com", "password": "hunter2hunter2"})[0] == 503
