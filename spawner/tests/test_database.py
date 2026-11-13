"""Apps' databases against a real Postgres (the pgvector image, as compose runs it): set
SPAWNER_TEST_DATABASE_URL to an admin URL of a throwaway server, e.g.
postgresql://labb_apps:adminpw@127.0.0.1:55433/postgres. Without it, these are skipped."""

import os

import psycopg
import pytest

from labb_spawner import database

URL = os.getenv("SPAWNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="SPAWNER_TEST_DATABASE_URL isn't set")

A, B = "aaaaaaaaaaa1", "bbbbbbbbbbb2"


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(database, "ADMIN_URL", URL)
    monkeypatch.setattr(database, "_server_ready", False)
    for app_id in (A, B):
        database.drop(app_id)
    yield
    for app_id in (A, B):
        database.drop(app_id)


def connect_as(app_id, password, dbname=None):
    return psycopg.connect(psycopg.conninfo.make_conninfo(URL, user=database.name_of(app_id), password=password,
                                                          dbname=dbname or database.name_of(app_id)))


def test_each_app_gets_its_own_database_and_nothing_else(server):
    a, _ = database.create(A), database.create(B)
    assert database.exists(A) and database.exists(B)
    assert database.url(A, a) == f"postgresql://app_{A}:{a}@{database.APP_HOST}:{database.APP_PORT}/app_{A}".replace(a, a)
    with connect_as(A, a) as c:
        c.execute("CREATE TABLE notes (id serial primary key, text text)")
        c.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")  # trusted: an owner may
        assert c.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'").fetchone()  # made for it
        c.commit()
    # Not into another app's database, nor the server's own
    for other in (f"app_{B}", "postgres", "template1"):
        with pytest.raises(psycopg.OperationalError, match="CONNECT privilege|permission denied"):
            connect_as(A, a, other)
    # Not a role that can do more than its own database
    with connect_as(A, a) as c:
        for statement in ("CREATE DATABASE other", "CREATE ROLE other", "CREATE EXTENSION dblink", "ALTER ROLE CURRENT_USER BYPASSRLS"):
            with pytest.raises(psycopg.Error):
                with c.transaction():
                    c.execute(statement)
        settings = dict(c.execute("SELECT name, setting FROM pg_settings WHERE name IN ('statement_timeout', 'lock_timeout')").fetchall())
        assert settings == {"statement_timeout": "30000", "lock_timeout": "10000"}
    # A wrong password is a wrong password
    with pytest.raises(psycopg.OperationalError):
        connect_as(A, "nope")


def test_making_it_again_gives_a_new_password_and_keeps_the_data(server):
    first = database.create(A)
    with connect_as(A, first) as c:
        c.execute("CREATE TABLE kept (id int)"); c.execute("INSERT INTO kept VALUES (1)"); c.commit()
    second = database.create(A)
    with pytest.raises(psycopg.OperationalError):
        connect_as(A, first)
    with connect_as(A, second) as c:
        assert c.execute("SELECT count(*) FROM kept").fetchone() == (1,)


def test_migrations_in_order_once_and_not_changed(server):
    password = database.create(A)
    one = ("0001_items.sql", "CREATE TABLE items (id serial primary key, name text not null);")
    two = ("0002_done.sql", "ALTER TABLE items ADD COLUMN done boolean NOT NULL DEFAULT false; INSERT INTO items (name) VALUES ('milk');")
    assert database.migrate(A, password, [two, one]) == ["0001_items.sql", "0002_done.sql"]
    assert database.migrate(A, password, [one, two]) == []
    assert [m["name"] for m in database.applied(A, password)] == ["0001_items.sql", "0002_done.sql"]
    # Changing one that ran is refused, with what to do instead
    with pytest.raises(database.MigrationError, match="0001_items.sql: it was applied already.*new one"):
        database.migrate(A, password, [("0001_items.sql", one[1] + " -- changed"), two])


def test_a_failing_migration_changes_nothing_of_its_own(server):
    password = database.create(A)
    good = ("0001_items.sql", "CREATE TABLE items (id int);")
    bad = ("0002_bad.sql", "CREATE TABLE half (id int); INSERT INTO nowhere VALUES (1);")
    later = ("0003_later.sql", "CREATE TABLE later (id int);")
    with pytest.raises(database.MigrationError, match='0002_bad.sql: relation "nowhere" does not exist'):
        database.migrate(A, password, [good, bad, later])
    with connect_as(A, password) as c:
        tables = {r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()}
    # The one before it stays, its own half is undone, and the ones after it didn't run
    assert tables == {"items", "labb_migrations"}
    assert [m["name"] for m in database.applied(A, password)] == ["0001_items.sql"]


def test_reading_is_read_only_whatever_the_sql(server):
    password = database.create(A)
    database.migrate(A, password, [("0001.sql", "CREATE TABLE x (id int); INSERT INTO x VALUES (1), (2);")])
    assert database.query(A, password, "SELECT id FROM x ORDER BY id")["rows"] == [[1], [2]]
    for text in ("COMMIT; DROP TABLE x", "RESET ROLE; DROP TABLE x", "DROP TABLE x", "INSERT INTO x VALUES (3)",
                 "DO $$ BEGIN DROP TABLE x; END $$", "SET TRANSACTION READ WRITE"):
        try:
            database.query(A, password, text)
        except database.DatabaseError:
            pass
    assert database.query(A, password, "SELECT count(*) AS n FROM x") == {
        "columns": ["n"], "rows": [[2]], "truncated": False, "rowcount": 1, "written": False}
    # As the reader, not the owner
    assert database.query(A, password, "SELECT current_user")["rows"] == [[f"app_{A}_read"]]
    # Writing, when asked, as the app's own role, several statements at once
    written = database.query(A, password, "INSERT INTO x VALUES (3); SELECT count(*) FROM x", write=True)
    assert written["rows"] == [[3]] and written["written"] is True


def test_tables_rows_and_size(server):
    password = database.create(A)
    database.migrate(A, password, [("0001.sql", "CREATE TABLE people (id serial primary key, name text, born date, data jsonb);"
                                                "INSERT INTO people (name, born, data) SELECT 'p' || g, '2000-01-02', '{\"a\": 1}' FROM generate_series(1, 120) g;"
                                                "CREATE VIEW names AS SELECT name FROM people;")])
    found = {(t["schema"], t["name"]): t for t in database.tables(A, password)}
    assert found[("public", "people")]["kind"] == "table" and found[("public", "names")]["kind"] == "view"
    assert [c["name"] for c in found[("public", "people")]["columns"]] == ["id", "name", "born", "data"]
    # Counted exactly (before Postgres has analyzed it), up to a cap
    assert found[("public", "people")]["rows"] == 120 and found[("public", "people")]["rows_more"] is False
    page = database.rows(A, password, "public", "people", offset=100, limit=50)
    assert page["columns"] == ["id", "name", "born", "data"] and len(page["rows"]) == 20 and page["more"] is False
    assert page["rows"][0][2] == "2000-01-02" and page["rows"][0][3] == {"a": 1}
    assert database.rows(A, password, "public", "people", limit=10)["more"] is True
    with pytest.raises(database.DatabaseError):
        database.rows(A, password, "public", 'people"; DROP TABLE people; --')
    assert database.size(A) > 0


def test_dropping_takes_everything(server):
    password = database.create(A)
    held = connect_as(A, password)
    database.drop(A)
    held.close()
    assert not database.exists(A)
    with psycopg.connect(URL) as c:
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname LIKE %s", (f"app_{A}%",)).fetchone() == (0,)


def test_ids_that_arent_apps_are_refused():
    for bad in ("x", "AAAAAAAAAAA1", "aaaaaaaaaaa1; drop", ""):
        with pytest.raises(database.DatabaseError):
            database.name_of(bad)


# Through the spawner's API: the app's side (a stand-in for Docker) and a real Postgres

from labb_spawner import app as spawner  # noqa: E402


@pytest.fixture
def app_with_db(http, docker, server):
    app = http.post("/apps", json={"owner": "u1", "name": "Shop", "starter": "python"}).json()
    yield app
    database.drop(app["id"])


def files(http, app, path, content):
    return http.put(f"/apps/{app['id']}/files/{path}", params={"owner": "u1"}, json={"content": content})


def test_turning_a_database_on_gives_the_app_database_url(http, docker, app_with_db):
    app, owner = app_with_db, {"owner": "u1"}
    assert http.get(f"/apps/{app['id']}/database", params=owner).json() == {"available": True, "enabled": False}
    files(http, app, "db/migrations/0001_items.sql", "CREATE TABLE items (id serial primary key, name text not null);")
    made = http.post(f"/apps/{app['id']}/database", params=owner).json()
    assert made["enabled"] and made["migrated"]["ok"] and "Applied the migration 0001_items.sql" in made["migrated"]["log"]
    assert made["app"]["database"] is True and made["pending"] == []
    assert [t["name"] for t in made["tables"]] == ["items", "labb_migrations"]
    env = docker.runs[-1]["environment"]
    assert env["DATABASE_URL"].startswith(f"postgresql://app_{app['id']}:") and env["DATABASE_URL"].endswith(f"/app_{app['id']}")
    # Its password, encrypted where it's kept; and not a variable the user can set
    assert env["DATABASE_URL"].split(":")[2].split("@")[0] not in (spawner.sources.folder(app["id"]) / "settings.json").read_text()
    assert http.put(f"/apps/{app['id']}/settings", params=owner, json={"variables": [{"name": "DATABASE_URL", "value": "x"}]}).status_code == 400
    assert http.post(f"/apps/{app['id']}/database", params=owner).status_code == 409
    # Someone else's: nothing
    assert http.get(f"/apps/{app['id']}/database", params={"owner": "u2"}).status_code == 404


def test_migrations_at_each_build_and_a_failed_one_keeps_the_last_build(http, docker, app_with_db):
    app, owner = app_with_db, {"owner": "u1"}
    # The starter's own 0001_items.sql runs when the database is added
    http.post(f"/apps/{app['id']}/database", params=owner)
    files(http, app, "db/migrations/0002_notes.sql", "CREATE TABLE notes (id serial primary key, body text);")
    built = http.post(f"/apps/{app['id']}/build", params=owner).json()
    assert built["ok"] and "Applied the migration 0002_notes.sql" in built["log"] and built["app"]["build"] == 2
    files(http, app, "db/migrations/0003_bad.sql", "ALTER TABLE nowhere ADD COLUMN x int;")
    failed = http.post(f"/apps/{app['id']}/build", params=owner).json()
    assert failed["ok"] is False and "0003_bad.sql" in failed["error"] and 'relation "nowhere" does not exist' in failed["error"]
    # The last good build runs on (its server: the front is the same for every app), and the new image is gone
    servers = [r for r in docker.runs if r.get("labels", {}).get(spawner.SERVER_LABEL)]
    assert failed["app"]["build"] == 2 and servers[-1]["image"] == f"labb-app-{app['id']}:2"
    assert f"labb-app-{app['id']}:3" not in docker.tags
    assert http.get(f"/apps/{app['id']}/build", params=owner).json()["ok"] is False
    state = http.get(f"/apps/{app['id']}/database", params=owner).json()
    assert [m["name"] for m in state["migrations"]] == ["0001_items.sql", "0002_notes.sql"] and state["pending"] == ["0003_bad.sql"]
    # Fixed: it goes through
    files(http, app, "db/migrations/0003_bad.sql", "ALTER TABLE notes ADD COLUMN done boolean DEFAULT false;")
    assert http.post(f"/apps/{app['id']}/build", params=owner).json()["ok"] is True


def test_reading_and_writing_through_the_api(http, app_with_db):
    app, owner = app_with_db, {"owner": "u1"}
    files(http, app, "db/migrations/0001.sql", "CREATE TABLE items (id serial primary key, name text); INSERT INTO items (name) VALUES ('milk'), ('bread');")
    http.post(f"/apps/{app['id']}/database", params=owner)
    page = http.get(f"/apps/{app['id']}/database/rows", params={**owner, "table": "items"}).json()
    assert page["columns"] == ["id", "name"] and page["rows"] == [[1, "milk"], [2, "bread"]]
    read = http.post(f"/apps/{app['id']}/database/query", params=owner, json={"sql": "COMMIT; DELETE FROM items"})
    assert read.status_code == 400
    assert http.post(f"/apps/{app['id']}/database/query", params=owner, json={"sql": "DELETE FROM items"}).status_code == 400
    wrote = http.post(f"/apps/{app['id']}/database/query", params=owner, json={"sql": "DELETE FROM items WHERE id = 1", "write": True}).json()
    assert wrote["rowcount"] == 1
    assert http.post(f"/apps/{app['id']}/database/query", params=owner, json={"sql": "SELECT name FROM items"}).json()["rows"] == [["bread"]]
    assert http.post(f"/apps/{app['id']}/database/query", params={"owner": "u2"}, json={"sql": "SELECT 1"}).status_code == 404


def test_removing_the_database_or_the_app_drops_it(http, docker, app_with_db, server):
    app, owner = app_with_db, {"owner": "u1"}
    http.post(f"/apps/{app['id']}/database", params=owner)
    assert http.delete(f"/apps/{app['id']}/database", params=owner).json()["app"]["database"] is False
    assert not database.exists(app["id"]) and "DATABASE_URL" not in docker.runs[-1]["environment"]
    http.post(f"/apps/{app['id']}/database", params=owner)
    http.delete(f"/apps/{app['id']}", params=owner)
    assert not database.exists(app["id"])


def test_a_database_over_its_limit_stops_the_app(http, docker, app_with_db, monkeypatch):
    app, owner = app_with_db, {"owner": "u1"}
    http.post(f"/apps/{app['id']}/database", params=owner)
    monkeypatch.setattr(database, "SIZE_LIMIT", 1000)
    assert spawner.data_sweep() == [app["id"]]
    assert "its database is over" in http.get(f"/apps/{app['id']}", params=owner).json()["detail"]


# An app's API: its container (a stand-in for Docker) and its roles (the real Postgres)

def api_runs(docker):
    return [r for r in docker.runs if r.get("labels", {}).get(spawner.API_LABEL)]


def test_an_api_needs_a_database_and_runs_beside_the_app(http, docker, app_with_db, monkeypatch):
    app, owner = app_with_db, {"owner": "u1"}
    assert http.post(f"/apps/{app['id']}/api", params=owner).status_code == 409
    http.post(f"/apps/{app['id']}/database", params=owner)
    state = http.post(f"/apps/{app['id']}/api", params=owner).json()
    assert state["api"] == {"enabled": True, "port": app["port"] + 100, "status": "running", "signups": True}
    run = api_runs(docker)[-1]
    # As locked down as the app, on its port + 100, logging in as its own API role
    assert run["ports"] == {"8080/tcp": (spawner.BIND, app["port"] + 100)} and run["read_only"] and run["cap_drop"] == ["ALL"]
    assert run["user"] == "65534:65534" and run["labels"] == {spawner.API_LABEL: app["id"], spawner.API_IMAGE_LABEL: run["image"]}
    assert run["environment"]["API_DB_URI"].startswith(f"postgresql://app_{app['id']}_api:")
    assert run["environment"]["AUTH_DB_URI"].startswith(f"postgresql://app_{app['id']}_auth:")
    assert run["environment"]["AUTH_SIGNUPS"] == "1"
    assert len(run["environment"]["API_JWT_SECRET"]) >= 32
    # Not listed as an app
    assert [a["id"] for a in http.get("/apps", params=owner).json()] == [app["id"]]
    assert http.post(f"/apps/{app['id']}/api", params=owner).status_code == 409
    with psycopg.connect(URL) as c:
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname = %s", (f"app_{app['id']}_api",)).fetchone() == (1,)


def test_on_the_network_caddy_finds_it_by_its_port(http, docker, app_with_db, monkeypatch):
    monkeypatch.setattr(spawner, "NETWORK", "labb-apps")
    monkeypatch.setattr(spawner, "PUBLISH", False)
    app, owner = app_with_db, {"owner": "u1"}
    http.post(f"/apps/{app['id']}/database", params=owner)
    http.post(f"/apps/{app['id']}/api", params=owner)
    run = api_runs(docker)[-1]
    assert run["ports"] is None and run["networking_config"] == {"labb-apps": {"Aliases": [f"labb-app-api-{app['port'] + 100}"]}}


def test_the_api_follows_the_app_and_goes_with_it(http, docker, app_with_db):
    app, owner = app_with_db, {"owner": "u1"}
    http.post(f"/apps/{app['id']}/database", params=owner)
    http.post(f"/apps/{app['id']}/api", params=owner)
    api = spawner.api_container(app["id"])
    http.post(f"/apps/{app['id']}/stop", params=owner)
    assert api.status == "exited"
    http.post(f"/apps/{app['id']}/start", params=owner)
    assert api.status == "running"
    # Off: its container and role go, the database stays
    assert http.delete(f"/apps/{app['id']}/api", params=owner).json()["api"]["enabled"] is False
    assert spawner.api_container(app["id"]) is None and database.exists(app["id"])
    with psycopg.connect(URL) as c:
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname = %s", (f"app_{app['id']}_api",)).fetchone() == (0,)
    # On again, then the database removed: the API goes first
    http.post(f"/apps/{app['id']}/api", params=owner)
    http.delete(f"/apps/{app['id']}/database", params=owner)
    assert spawner.api_container(app["id"]) is None
    http.post(f"/apps/{app['id']}/database", params=owner)
    http.post(f"/apps/{app['id']}/api", params=owner)
    http.delete(f"/apps/{app['id']}", params=owner)
    assert spawner.api_container(app["id"]) is None and not database.exists(app["id"])


def test_anon_only_has_what_migrations_grant_and_functions_arent_public(server):
    owner, api = database.create(A), database.enable_api(A)["password"]
    database.migrate(A, owner, [("0001.sql", "CREATE TABLE open_items (t text); INSERT INTO open_items VALUES ('ok');"
                                            "GRANT SELECT ON open_items TO anon; CREATE TABLE closed (t text);"
                                            "CREATE FUNCTION hi() RETURNS text LANGUAGE sql AS 'select 1::text';")])
    b_owner = database.create(B)
    database.migrate(B, b_owner, [("0001.sql", "CREATE TABLE b_secret (t text); INSERT INTO b_secret VALUES ('b');")])
    as_api = psycopg.conninfo.make_conninfo(URL, user=f"app_{A}_api", password=api, dbname=f"app_{A}")
    with psycopg.connect(as_api) as c:
        c.execute("SET ROLE anon")
        assert c.execute("SELECT t FROM open_items").fetchall() == [("ok",)]
        for denied in ("SELECT * FROM closed", "SELECT hi()", "INSERT INTO open_items VALUES ('x')", "CREATE TABLE z (i int)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with c.transaction():
                    c.execute(denied)
    # Not another app's database, nor the server's
    for other in (f"app_{B}", "postgres"):
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(psycopg.conninfo.make_conninfo(URL, user=f"app_{A}_api", password=api, dbname=other))
    # Its owner can't touch the shared roles
    with connect_as(A, owner) as c:
        for statement in ("GRANT anon TO CURRENT_USER", f"GRANT app_{A} TO anon", "ALTER ROLE anon SET statement_timeout = 0"):
            with pytest.raises(psycopg.Error):
                with c.transaction():
                    c.execute(statement)


# Its own users: the auth schema, the roles around it, and row-level security with auth.uid()

OWN_ROWS = """
CREATE TABLE notes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id uuid NOT NULL DEFAULT auth.uid() REFERENCES auth.users ON DELETE CASCADE,
    body text NOT NULL
);
ALTER TABLE notes ENABLE ROW LEVEL SECURITY;
CREATE POLICY own_rows ON notes FOR ALL TO authenticated USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
GRANT SELECT, INSERT, UPDATE, DELETE ON notes TO authenticated;
"""


def as_role(app_id, role, password):
    return psycopg.connect(psycopg.conninfo.make_conninfo(URL, user=role, password=password, dbname=f"app_{app_id}"))


def a_user(auth_password, email, ended=False, disabled=False):
    """A user and a session, as sign-in makes them: (user id, session id)"""
    with as_role(A, f"app_{A}_auth", auth_password) as c:
        user = c.execute("INSERT INTO auth.users (email, password_hash, disabled) VALUES (%s, 'x', %s) RETURNING id",
                         (email, disabled)).fetchone()[0]
        session = c.execute("INSERT INTO auth.sessions (user_id, expires_at, ended_at) VALUES (%s, now() + interval '1 day', "
                            "CASE WHEN %s THEN now() END) RETURNING id", (user, ended)).fetchone()[0]
    return str(user), str(session)


def signed_in_as(c, user, session):
    import json
    c.execute("SET ROLE authenticated")
    c.execute("SELECT set_config('request.jwt.claims', %s, false)",
              (json.dumps({"role": "authenticated", "sub": user, "session_id": session}),))


def test_each_user_sees_only_their_rows(server):
    owner = database.create(A)
    made = database.enable_api(A)
    assert database.migrate(A, owner, [("0001_notes.sql", OWN_ROWS)]) == ["0001_notes.sql"]
    ann, bob = a_user(made["auth_password"], "ann@example.com"), a_user(made["auth_password"], "bob@example.com")
    with as_role(A, f"app_{A}_api", made["password"]) as c:
        c.autocommit = True
        signed_in_as(c, *ann)
        c.execute("SELECT auth.check_request()")
        assert c.execute("SELECT auth.uid()::text").fetchone() == (ann[0],)
        c.execute("INSERT INTO notes (body) VALUES ('ann''s')")  # user_id from auth.uid(); the reference checked as its owner
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            c.execute("INSERT INTO notes (user_id, body) VALUES (%s, 'as bob')", (bob[0],))
        signed_in_as(c, *bob)
        c.execute("INSERT INTO notes (body) VALUES ('bob''s')")
        assert c.execute("SELECT body FROM notes").fetchall() == [("bob's",)]
        assert c.execute("UPDATE notes SET body = 'taken' WHERE body = 'ann''s'").rowcount == 0
        assert c.execute("DELETE FROM notes").rowcount == 1
        # The users' tables aren't theirs to read, signed in or not
        for role in ("authenticated", "anon"):
            c.execute(f"SET ROLE {role}")
            for table in ("auth.users", "auth.sessions", "auth.refresh_tokens"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    c.execute(f"SELECT * FROM {table}")
        # anon has no auth.uid() and no rows of the table
        c.execute("SET ROLE anon")
        c.execute("SELECT set_config('request.jwt.claims', '{\"role\": \"anon\"}', false)")
        c.execute("SELECT auth.check_request()")
        assert c.execute("SELECT auth.uid()").fetchone() == (None,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM notes")
    with connect_as(A, owner) as c:
        assert sorted(r[0] for r in c.execute("SELECT body FROM notes")) == ["ann's"]
    # The owner's page and agent read every row of it, read-only
    assert [t["rows"] for t in database.tables(A, owner) if t["name"] == "notes"] == [1]
    assert database.rows(A, owner, "public", "notes")["rows"][0][2] == "ann's"
    assert database.query(A, owner, "SELECT body FROM notes")["rows"] == [["ann's"]]
    with pytest.raises(database.DatabaseError, match="read-only"):
        database.query(A, owner, "DELETE FROM notes")


def test_a_request_whose_sign_in_ended_is_refused(server):
    database.create(A)
    made = database.enable_api(A)
    live = a_user(made["auth_password"], "live@example.com")
    cases = {"ended": a_user(made["auth_password"], "ended@example.com", ended=True),
             "disabled": a_user(made["auth_password"], "off@example.com", disabled=True),
             "another's session": (live[0], a_user(made["auth_password"], "x@example.com")[1]),
             "no session": (live[0], "")}
    with as_role(A, f"app_{A}_api", made["password"]) as c:
        c.autocommit = True
        signed_in_as(c, *live)
        c.execute("SELECT auth.check_request()")
        for case, (user, session) in cases.items():
            signed_in_as(c, user, session)
            with pytest.raises(psycopg.Error, match="sign-in has ended") as caught:
                c.execute("SELECT auth.check_request()")
            assert caught.value.sqlstate == "PT401", case
    # The owner disabling them ends theirs at once too
    owner = database.create(A)
    database.set_user_disabled(A, owner, live[0], True)
    with as_role(A, f"app_{A}_api", made["password"]) as c:
        c.autocommit = True
        signed_in_as(c, *live)
        with pytest.raises(psycopg.Error, match="sign-in has ended"):
            c.execute("SELECT auth.check_request()")


def test_sign_ins_role_has_the_users_tables_and_nothing_else(server):
    owner = database.create(A)
    made = database.enable_api(A)
    database.migrate(A, owner, [("0001_notes.sql", OWN_ROWS)])
    with as_role(A, f"app_{A}_auth", made["auth_password"]) as c:
        c.autocommit = True
        assert c.execute("SELECT count(*) FROM auth.users").fetchone() == (0,)
        for denied in ("SELECT * FROM notes", "SET ROLE anon", "SET ROLE authenticated", f"SET ROLE app_{A}",
                       "CREATE TABLE auth.mine (i int)", "CREATE TABLE public.mine (i int)", "DROP TABLE auth.users",
                       "ALTER TABLE auth.users ADD COLUMN x int", "CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid "
                       "LANGUAGE sql AS 'select null::uuid'"):
            with pytest.raises(psycopg.Error):
                c.execute(denied)
    for other in (f"app_{B}", "postgres"):
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(psycopg.conninfo.make_conninfo(URL, user=f"app_{A}_auth", password=made["auth_password"], dbname=other))
    # The same once more (each build brings it up to date) changes nothing, and keeps its users
    a_user(made["auth_password"], "kept@example.com")
    database.ensure_auth(A)
    database.ensure_auth(A)
    assert database.users(A, owner)["total"] == 1
    # Not on the tables list (the users are listed on their own)
    assert [t["name"] for t in database.tables(A, owner)] == ["labb_migrations", "notes"]


def test_users_listed_disabled_and_removed(server):
    owner = database.create(A)
    made = database.enable_api(A)
    database.migrate(A, owner, [("0001_notes.sql", OWN_ROWS)])
    ann, _ = a_user(made["auth_password"], "ann@example.com")
    bob, _ = a_user(made["auth_password"], "bob@example.com")
    with connect_as(A, owner) as c:
        c.execute("INSERT INTO notes (user_id, body) VALUES (%s, 'ann''s')", (ann,))
    listed = database.users(A, owner)
    assert listed["total"] == 2 and [u["email"] for u in listed["users"]] == ["bob@example.com", "ann@example.com"]
    assert set(listed["users"][0]) == {"id", "email", "disabled", "created_at", "last_sign_in_at", "sessions"}
    assert listed["users"][0]["sessions"] == 1
    # Disabled: their sign-ins end with it
    assert database.set_user_disabled(A, owner, ann, True)
    found = {u["email"]: u for u in database.users(A, owner)["users"]}
    assert found["ann@example.com"]["disabled"] and found["ann@example.com"]["sessions"] == 0
    assert database.set_user_disabled(A, owner, ann, False)
    # Removed, with the rows that cascade
    assert database.delete_user(A, owner, ann)
    with connect_as(A, owner) as c:
        assert c.execute("SELECT count(*) FROM notes").fetchone() == (0,)
    assert not database.delete_user(A, owner, ann) and not database.set_user_disabled(A, owner, ann, True)
    with pytest.raises(database.DatabaseError, match="Not a user's id"):
        database.delete_user(A, owner, "1; DROP TABLE notes")
    assert database.users(A, owner, offset=1)["users"] == []


def test_migrations_may_refer_to_users_without_an_api_and_turning_it_off_drops_both_roles(server):
    owner = database.create(A)
    assert database.migrate(A, owner, [("0001_notes.sql", OWN_ROWS)]) == ["0001_notes.sql"]
    made = database.enable_api(A)
    a_user(made["auth_password"], "ann@example.com")
    database.disable_api(A)
    with psycopg.connect(URL) as c:
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname IN (%s, %s)", (f"app_{A}_api", f"app_{A}_auth")).fetchone() == (0,)
    # Its users stay with the database, for when it's turned on again
    again = database.enable_api(A)
    assert database.users(A, owner)["total"] == 1 and again["auth_password"] != made["auth_password"]
    database.drop(A)
    with psycopg.connect(URL) as c:
        assert c.execute("SELECT count(*) FROM pg_roles WHERE rolname LIKE %s", (f"app_{A}%",)).fetchone() == (0,)


def test_sign_up_setting_and_users_through_the_spawner(http, docker, app_with_db, monkeypatch):
    app, owner = app_with_db, {"owner": "u1"}
    assert http.patch(f"/apps/{app['id']}/api", params=owner, json={"signups": False}).status_code == 409
    http.post(f"/apps/{app['id']}/database", params=owner)
    assert http.get(f"/apps/{app['id']}/users", params=owner).json() == {"users": [], "total": 0, "offset": 0}
    http.post(f"/apps/{app['id']}/api", params=owner)
    state = http.patch(f"/apps/{app['id']}/api", params=owner, json={"signups": False}).json()
    assert state["api"]["signups"] is False and state["api"]["status"] == "running"
    assert api_runs(docker)[-1]["environment"]["AUTH_SIGNUPS"] == "0" and len(api_runs(docker)) == 2
    stored = spawner.appdata.api_secrets(app["id"])
    with as_role(app["id"], f"app_{app['id']}_auth", stored["auth_password"]) as c:
        user = str(c.execute("INSERT INTO auth.users (email, password_hash) VALUES ('ann@example.com', 'x') RETURNING id").fetchone()[0])
    listed = http.get(f"/apps/{app['id']}/users", params=owner).json()
    assert [u["email"] for u in listed["users"]] == ["ann@example.com"]
    assert http.patch(f"/apps/{app['id']}/users/{user}", params=owner, json={"disabled": True}).status_code == 204
    assert http.get(f"/apps/{app['id']}/users", params=owner).json()["users"][0]["disabled"] is True
    assert http.get(f"/apps/{app['id']}/users", params={"owner": "u2"}).status_code == 404
    assert http.delete(f"/apps/{app['id']}/users/{user}", params=owner).status_code == 204
    assert http.delete(f"/apps/{app['id']}/users/{user}", params=owner).status_code == 404
    assert http.delete(f"/apps/{app['id']}/users/nope", params=owner).status_code == 400


def test_an_api_from_an_older_spawner_is_renewed_at_the_next_build(http, docker, app_with_db):
    app, owner = app_with_db, {"owner": "u1"}
    http.post(f"/apps/{app['id']}/database", params=owner)
    http.post(f"/apps/{app['id']}/api", params=owner)
    # As step 10 left it: no sign-in role, an older image
    stored = spawner.appdata.api_secrets(app["id"])
    spawner.appdata.set_api_secrets(app["id"], {"password": stored["password"], "jwt_secret": stored["jwt_secret"]})
    spawner.api_container(app["id"]).labels[spawner.API_IMAGE_LABEL] = "labb-app-api:older"
    before = len(api_runs(docker))
    assert http.post(f"/apps/{app['id']}/build", params=owner).json()["ok"]
    run = api_runs(docker)[-1]
    assert len(api_runs(docker)) == before + 1 and run["environment"]["AUTH_DB_URI"].startswith(f"postgresql://app_{app['id']}_auth:")
    assert spawner.appdata.api_secrets(app["id"])["jwt_secret"] == stored["jwt_secret"]
    # Up to date: the next build doesn't make it again
    assert http.post(f"/apps/{app['id']}/build", params=owner).json()["ok"]
    assert len(api_runs(docker)) == before + 1
