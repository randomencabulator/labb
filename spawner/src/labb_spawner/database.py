"""Apps' databases: a database and a role of their own per app, on the apps' Postgres (apps-postgres,
not labb's own), which the spawner manages with its admin login (APPS_DATABASE_ADMIN_URL).

Each app's role (`app_<id>`) owns its database (`app_<id>`) and nothing else: it can't create
databases or roles, bypass row-level security, or connect to any other database (`CONNECT` is
revoked from everyone on each app's database, and on the server's own `postgres` and `template1`).
It has a connection limit and statement, lock and idle timeouts. The admin creates the `vector`
extension in each (it isn't one an owner may create); the trusted ones (pgcrypto, citext, …) the
app's migrations can create themselves.

Reading for the page or the agent is done three ways read-only at once: as `app_<id>_read` (a role
with `pg_read_all_data` and nothing else, which only the app's own role may become; it reads every row,
row-level security or not, as the app's role, which owns them, can anyway), in a read-only
transaction, and one statement at a time (prepared, so `COMMIT; DROP …` is refused as two).

An app's API (its backend container, with PostgREST) logs in as `app_<id>_api`, which can only become
the server-wide `anon` and `authenticated` roles: what those may do in an app's database is what its
migrations grant them there (grants are per database, so one app's grants mean nothing in another's).
In each app's database, functions aren't runnable by everyone by default (Postgres's default is that
they are), so a function is only on the API when a migration grants it.

Its own users (signing up and in, on its API's /auth/…) are in its database's `auth` schema, owned by
the app's role like the rest: `auth.users` (scrypt hashes), `auth.sessions`, `auth.refresh_tokens`.
The API's sign-in part logs in as `app_<id>_auth`, which can use those three tables and nothing else.
A signed-in user's requests run as `authenticated`, and `auth.uid()` is their id, for row-level
security; `auth.check_request()`, run before each request, refuses a token whose sign-in has ended
(signed out, the user disabled or removed), so that takes effect at once rather than at its expiry.

Migrations are the app's files `db/migrations/*.sql`, applied in name order, each in a transaction,
as the app's own role (never the admin), recorded in its `labb_migrations` table. One that ran
can't be changed afterwards (its checksum is kept): a change is a new migration.
"""

import hashlib
import os
import secrets
from pathlib import Path
from datetime import date, datetime, time
from decimal import Decimal
from urllib.parse import quote
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from labb_spawner import limits

ADMIN_URL = os.getenv("APPS_DATABASE_ADMIN_URL", "")
# Its admin password, in a file the spawner makes on its first start (strong and random; the apps'
# Postgres reads it from the same volume): apps can reach that server, so it's never a guessable one
PASSWORD_FILE = os.getenv("APPS_DATABASE_PASSWORD_FILE", "")
# The user the apps' Postgres runs as in its image, who has to read that file
POSTGRES_UID = 999
# Where the apps reach it, on their network (the spawner reaches it through ADMIN_URL's host)
APP_HOST = os.getenv("APPS_DATABASE_HOST", "apps-postgres")
APP_PORT = int(os.getenv("APPS_DATABASE_PORT", "5432"))
SIZE_LIMIT = int(os.getenv("APP_DB_MB", "512")) * 1024 * 1024
CONNECTIONS = int(os.getenv("APP_DB_CONNECTIONS", "10"))
# The app's role's own settings: what it runs is cut off rather than left hanging
ROLE_SETTINGS = {"statement_timeout": "30s", "lock_timeout": "10s", "idle_in_transaction_session_timeout": "60s"}
MIGRATION_TIMEOUT = "5min"
QUERY_TIMEOUT = "15s"
MAX_ROWS = 500
# Tables' rows are counted exactly up to this many (so a big one stays quick to show)
COUNT_UP_TO = 10_000
MIGRATIONS = "db/migrations"


class DatabaseError(Exception):
    """Said for the user: what went wrong with an app's database"""


class MigrationError(DatabaseError):
    def __init__(self, name: str, message: str):
        super().__init__(f"{name}: {message}")
        self.name = name


def load_admin_password() -> str:
    """The admin password from PASSWORD_FILE, made there if there's none yet; "" without a file"""
    if not PASSWORD_FILE:
        return ""
    path = Path(PASSWORD_FILE)
    try:
        if found := path.read_text().strip():
            return found
    except FileNotFoundError:
        pass
    password = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(password)
    if os.geteuid() == 0:
        os.chown(partial, POSTGRES_UID, POSTGRES_UID)
    os.replace(partial, path)
    return password


if ADMIN_URL and PASSWORD_FILE:
    ADMIN_URL = make_conninfo(ADMIN_URL, password=load_admin_password())


def available() -> bool:
    return bool(ADMIN_URL)


def reader_of(app_id: str) -> str:
    return name_of(app_id) + "_read"


def api_role_of(app_id: str) -> str:
    return name_of(app_id) + "_api"


def auth_role_of(app_id: str) -> str:
    return name_of(app_id) + "_auth"


# The roles an API request runs as, the same on the whole server, with no login
API_ROLES = ("anon", "authenticated")
API_ROLE_SETTINGS = {"statement_timeout": "10s"}
AUTH_CONNECTIONS = 5


def name_of(app_id: str) -> str:
    """The app's database and role. App ids are 12 hex characters, checked again here."""
    if not (len(app_id) == 12 and all(c in "0123456789abcdef" for c in app_id)):
        raise DatabaseError("Not an app's id")
    return f"app_{app_id}"


def _admin(dbname: str = "postgres"):
    if not ADMIN_URL:
        raise DatabaseError("This server has no database for apps (APPS_DATABASE_ADMIN_URL isn't set)")
    return psycopg.connect(make_conninfo(ADMIN_URL, dbname=dbname), autocommit=True, connect_timeout=10)


def _as_app(app_id: str, password: str, autocommit: bool = False):
    """A connection as the app's own role, through the spawner's way to the server"""
    name = name_of(app_id)
    return psycopg.connect(make_conninfo(ADMIN_URL, user=name, password=password, dbname=name), autocommit=autocommit,
                           connect_timeout=10)


_server_ready = False


def ensure_server():
    """Once: nobody but the admin connects to the server's own databases"""
    global _server_ready
    if _server_ready:
        return
    with _admin() as c:
        for db in ("postgres", "template1"):
            c.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db)))
        for role in API_ROLES:
            if not c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                c.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS").format(sql.Identifier(role)))
            for setting, value in API_ROLE_SETTINGS.items():
                c.execute(sql.SQL("ALTER ROLE {} SET {} = {}").format(sql.Identifier(role), sql.Identifier(setting), sql.Literal(value)))
    _server_ready = True


def exists(app_id: str) -> bool:
    with _admin() as c:
        return c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name_of(app_id),)).fetchone() is not None


def create(app_id: str) -> str:
    """The app's database and role (made again where a half-made one is left): its role's new password"""
    ensure_server()
    name, password = name_of(app_id), secrets.token_urlsafe(32)
    role = sql.Identifier(name)
    with _admin() as c:
        has_role = c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
        verb = "ALTER" if has_role else "CREATE"
        c.execute(sql.SQL(verb + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
                          "CONNECTION LIMIT {} PASSWORD {}").format(role, sql.Literal(limits.get("app_database_connections")), sql.Literal(password)))
        for setting, value in ROLE_SETTINGS.items():
            c.execute(sql.SQL("ALTER ROLE {} SET {} = {}").format(role, sql.Identifier(setting), sql.Literal(value)))
        if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            c.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(role, role))
        c.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(role))
        c.execute(sql.SQL("GRANT CONNECT, TEMPORARY ON DATABASE {} TO {}").format(role, role))
        # The role reading is done as: it can read everything in the app's database (every row, whatever its
        # row-level security says, so the owner sees what their users wrote), change nothing
        reader = sql.Identifier(reader_of(app_id))
        verb = "ALTER" if c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (reader_of(app_id),)).fetchone() else "CREATE"
        c.execute(sql.SQL(verb + " ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS").format(reader))
        c.execute(sql.SQL("GRANT pg_read_all_data TO {}").format(reader))
        c.execute(sql.SQL("GRANT {} TO {}").format(reader, role))
    with _admin(name) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        # Its own schema: nobody else may use it (Postgres 15+ already keeps others from creating in it)
        c.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM PUBLIC"))
        c.execute(sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(role))
        _functions_not_public(c, app_id)
    ensure_auth(app_id)
    return password


def _functions_not_public(c, app_id: str):
    """Functions the app makes aren't runnable by everyone (and so not on its API) until granted"""
    c.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC").format(
        sql.Identifier(name_of(app_id))))


# Its users, in its own database: made as the app's role (so they're its, like its tables), the same
# every time (each statement can run again), before its migrations, which may refer to them
AUTH_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email text NOT NULL UNIQUE CHECK (email = lower(email) AND length(email) BETWEEN 3 AND 254),
    password_hash text NOT NULL,
    disabled boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_sign_in_at timestamptz
);
CREATE TABLE IF NOT EXISTS auth.sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES auth.users ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    refreshed_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    ended_at timestamptz
);
CREATE INDEX IF NOT EXISTS sessions_user_id ON auth.sessions (user_id);
CREATE TABLE IF NOT EXISTS auth.refresh_tokens (
    token_hash text PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES auth.sessions ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    used_at timestamptz
);
CREATE INDEX IF NOT EXISTS refresh_tokens_session_id ON auth.refresh_tokens (session_id);

-- The request's signed token's claims ({} without one), and the signed-in user's id (null without one)
CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb LANGUAGE sql STABLE
    AS $$ SELECT coalesce(nullif(current_setting('request.jwt.claims', true), '')::jsonb, '{}'::jsonb) $$;
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE
    AS $$ SELECT nullif(auth.jwt() ->> 'sub', '')::uuid $$;

-- Before each API request: a signed-in one's sign-in must still be on (not signed out, its user not
-- disabled or removed). It reads auth's tables, which the request's role can't, as their owner.
CREATE OR REPLACE FUNCTION auth.check_request() RETURNS void LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    claims jsonb := auth.jwt();
BEGIN
    IF coalesce(claims ->> 'role', 'anon') <> 'anon' AND NOT EXISTS (
        SELECT 1 FROM auth.sessions s JOIN auth.users u ON u.id = s.user_id
        WHERE s.id::text = claims ->> 'session_id' AND u.id::text = claims ->> 'sub'
          AND s.ended_at IS NULL AND s.expires_at > now() AND NOT u.disabled
    ) THEN
        RAISE EXCEPTION 'This sign-in has ended: sign in again' USING ERRCODE = 'PT401';
    END IF;
END $$;

REVOKE ALL ON ALL TABLES IN SCHEMA auth FROM PUBLIC;
REVOKE ALL ON FUNCTION auth.jwt(), auth.uid(), auth.check_request() FROM PUBLIC;
-- The API's roles may use the functions (policies call auth.uid()), not the tables
GRANT USAGE ON SCHEMA auth TO anon, authenticated;
GRANT EXECUTE ON FUNCTION auth.jwt(), auth.uid(), auth.check_request() TO anon, authenticated;
"""
AUTH_TABLES = ("users", "sessions", "refresh_tokens")


def ensure_auth(app_id: str):
    """Its `auth` schema, made or brought up to date (before each build's migrations); its reader too"""
    ensure_server()
    with _admin() as c:
        c.execute(sql.SQL("ALTER ROLE {} BYPASSRLS").format(sql.Identifier(reader_of(app_id))))
    with _admin(name_of(app_id)) as c:
        c.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(name_of(app_id))))
        c.execute(AUTH_SCHEMA)


def _login_role(c, role: str, password: str, connections: int):
    verb = "ALTER" if c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() else "CREATE"
    c.execute(sql.SQL(verb + " ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
                      "CONNECTION LIMIT {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(connections), sql.Literal(password)))


def enable_api(app_id: str) -> dict:
    """The roles its API logs in as, made (or again, with new passwords): {"password", "auth_password"}"""
    ensure_server()
    ensure_auth(app_id)
    name, api_role, auth_role = name_of(app_id), api_role_of(app_id), auth_role_of(app_id)
    found = {"password": secrets.token_urlsafe(32), "auth_password": secrets.token_urlsafe(32)}
    with _admin() as c:
        _login_role(c, api_role, found["password"], limits.get("app_database_connections"))
        _login_role(c, auth_role, found["auth_password"], AUTH_CONNECTIONS)
        c.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = '10s'").format(sql.Identifier(auth_role)))
        for role in (api_role, auth_role):
            c.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(name), sql.Identifier(role)))
        c.execute(sql.SQL("GRANT {} TO {}").format(sql.SQL(", ").join(sql.Identifier(r) for r in API_ROLES), sql.Identifier(api_role)))
    with _admin(name) as c:
        for r in API_ROLES:
            c.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(r)))
        _functions_not_public(c, app_id)
        # Sign-in's role: auth's three tables, and nothing else
        c.execute(sql.SQL("GRANT USAGE ON SCHEMA auth TO {}").format(sql.Identifier(auth_role)))
        c.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(
            sql.SQL(", ").join(sql.Identifier("auth", t) for t in AUTH_TABLES), sql.Identifier(auth_role)))
    return found


def disable_api(app_id: str):
    """Its API's roles, gone (their connections ended, their privileges taken back first, or they can't be)"""
    name, roles = name_of(app_id), [api_role_of(app_id), auth_role_of(app_id)]
    with _admin() as c:
        roles = [r for r in roles if c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (r,)).fetchone()]
        if not roles:
            return
        has_database = c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is not None
        for role in roles:
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s", (role,))
    if has_database:
        # In the app's database: its privileges there, and on the database itself (they own nothing)
        with _admin(name) as c:
            c.execute(sql.SQL("DROP OWNED BY {}").format(sql.SQL(", ").join(sql.Identifier(r) for r in roles)))
    with _admin() as c:
        for role in roles:
            c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def api_url(app_id: str, password: str) -> str:
    """Where its API container reaches its database, as its API role"""
    name = name_of(app_id)
    return f"postgresql://{api_role_of(app_id)}:{quote(password, safe='')}@{APP_HOST}:{APP_PORT}/{name}"


def auth_url(app_id: str, password: str) -> str:
    """Where its API's sign-in reaches its database, as its auth role"""
    name = name_of(app_id)
    return f"postgresql://{auth_role_of(app_id)}:{quote(password, safe='')}@{APP_HOST}:{APP_PORT}/{name}"


def reload_api(app_id: str, password: str):
    """Its API reads the tables again (after migrations): PostgREST listens on `pgrst`"""
    with _as_app(app_id, password, autocommit=True) as c:
        c.execute("NOTIFY pgrst, 'reload schema'")


def drop(app_id: str):
    """The app's database and role, gone (its connections ended first)"""
    name = name_of(app_id)
    with _admin() as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))
        c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(reader_of(app_id))))
        for role in (api_role_of(app_id), auth_role_of(app_id)):
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s", (role,))
            c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def url(app_id: str, password: str) -> str:
    """DATABASE_URL, as the app reaches its database"""
    name = name_of(app_id)
    return f"postgresql://{name}:{quote(password, safe='')}@{APP_HOST}:{APP_PORT}/{name}"


def size(app_id: str) -> int:
    with _admin() as c:
        found = c.execute("SELECT pg_database_size(%s)", (name_of(app_id),)).fetchone()
    return int(found[0]) if found and found[0] is not None else 0


def checksum(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def applied(app_id: str, password: str) -> list[dict]:
    with _as_app(app_id, password, autocommit=True) as c:
        if not c.execute("SELECT to_regclass('public.labb_migrations')").fetchone()[0]:
            return []
        rows = c.execute("SELECT name, checksum, applied_at FROM labb_migrations ORDER BY name").fetchall()
    return [{"name": n, "checksum": s, "applied_at": a.isoformat(timespec="seconds")} for n, s, a in rows]


def migrate(app_id: str, password: str, migrations: list[tuple[str, str]]) -> list[str]:
    """Apply the migrations not applied yet, in name order, each in a transaction, as the app's role.
    The names applied now; MigrationError on the first that fails (the ones before it stay applied)."""
    done: list[str] = []
    with _as_app(app_id, password) as c:
        c.execute("CREATE TABLE IF NOT EXISTS labb_migrations ("
                  "name text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
        c.commit()
        before = {name: sums for name, sums in c.execute("SELECT name, checksum FROM labb_migrations").fetchall()}
        c.commit()
        for name, text in sorted(migrations):
            if name in before:
                if before[name] != checksum(text):
                    raise MigrationError(name, "it was applied already and has changed since. A migration that ran stays "
                                               "as it was: put the change in a new one")
                continue
            try:
                with c.transaction():
                    c.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(MIGRATION_TIMEOUT)))
                    c.execute(text)
                    c.execute("INSERT INTO labb_migrations (name, checksum) VALUES (%s, %s)", (name, checksum(text)))
            except psycopg.Error as e:
                raise MigrationError(name, (getattr(e.diag, "message_primary", None) or str(e)).strip()) from None
            done.append(name)
    return done


def _plain(value):
    """A value as JSON can carry it"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "\\x" + bytes(value).hex()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return str(value)


def tables(app_id: str, password: str) -> list[dict]:
    """The app's own tables and views (not Postgres's, nor `auth`'s: see users()), with their columns and rows: counted exactly up to
    COUNT_UP_TO (`rows_more` past it), read-only"""
    with _as_app(app_id, password) as c, c.transaction() as tx:
        _read_only(c, app_id)
        found = c.execute(
            "SELECT n.nspname, c.relname, c.relkind "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('r', 'v', 'm', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
            "AND n.nspname NOT LIKE 'pg_temp%%' AND n.nspname <> 'auth' AND pg_has_role(c.relowner, %s, 'USAGE') ORDER BY 1, 2",
            (name_of(app_id),),
        ).fetchall()
        counts = {}
        for schema, table, kind in found:
            if kind in ("r", "p"):
                counted = sql.SQL("SELECT count(*) FROM (SELECT 1 FROM {}.{} LIMIT %s) AS counted").format(
                    sql.Identifier(schema), sql.Identifier(table))
                counts[(schema, table)] = c.execute(counted, (COUNT_UP_TO + 1,)).fetchone()[0]
        columns = c.execute(
            "SELECT table_schema, table_name, column_name, data_type, is_nullable = 'YES', column_default "
            "FROM information_schema.columns WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
            "ORDER BY table_schema, table_name, ordinal_position"
        ).fetchall()
        raise psycopg.Rollback(tx)
    by_table: dict[tuple[str, str], list[dict]] = {}
    for schema, table, column, kind, nullable, default in columns:
        by_table.setdefault((schema, table), []).append({"name": column, "type": kind, "nullable": nullable, "default": default})
    kinds = {"r": "table", "p": "table", "v": "view", "m": "view"}
    return [{"schema": s, "name": t, "kind": kinds[k], "rows": min(counts.get((s, t), 0), COUNT_UP_TO),
             "rows_more": counts.get((s, t), 0) > COUNT_UP_TO, "columns": by_table.get((s, t), [])} for s, t, k in found]


USERS_PAGE = 100


def _user_id(user_id: str) -> str:
    try:
        return str(UUID(user_id))
    except (ValueError, TypeError, AttributeError):
        raise DatabaseError("Not a user's id") from None


def users(app_id: str, password: str, offset: int = 0, limit: int = USERS_PAGE) -> dict:
    """Its own users (who signed up on its API), newest first, read-only: never their password hashes"""
    limit, offset = max(1, min(limit, USERS_PAGE)), max(0, offset)
    total, found = 0, []
    with _as_app(app_id, password) as c, c.transaction() as tx:
        _read_only(c, app_id)
        if c.execute("SELECT to_regclass('auth.users')").fetchone()[0]:
            total = c.execute("SELECT count(*) FROM auth.users").fetchone()[0]
            found = c.execute(
                "SELECT u.id, u.email, u.disabled, u.created_at, u.last_sign_in_at, "
                "(SELECT count(*) FROM auth.sessions s WHERE s.user_id = u.id AND s.ended_at IS NULL AND s.expires_at > now()) "
                "FROM auth.users u ORDER BY u.created_at DESC, u.id LIMIT %s OFFSET %s", (limit, offset)).fetchall()
        raise psycopg.Rollback(tx)
    return {"users": [{"id": str(i), "email": e, "disabled": d, "created_at": _plain(c_), "last_sign_in_at": _plain(l),
                       "sessions": n} for i, e, d, c_, l, n in found], "total": total, "offset": offset}


def set_user_disabled(app_id: str, password: str, user_id: str, disabled: bool) -> bool:
    """A user disabled (their sign-ins ended: their tokens stop working at once) or enabled again; False if there's none"""
    user_id = _user_id(user_id)
    with _as_app(app_id, password) as c, c.transaction():
        found = c.execute("UPDATE auth.users SET disabled = %s WHERE id = %s RETURNING id", (disabled, user_id)).fetchone()
        if found and disabled:
            c.execute("UPDATE auth.sessions SET ended_at = now() WHERE user_id = %s AND ended_at IS NULL", (user_id,))
    return found is not None


def delete_user(app_id: str, password: str, user_id: str) -> bool:
    """A user removed, with their sign-ins (and their rows, where the app's tables say ON DELETE CASCADE)"""
    user_id = _user_id(user_id)
    with _as_app(app_id, password) as c:
        try:
            with c.transaction():
                found = c.execute("DELETE FROM auth.users WHERE id = %s RETURNING id", (user_id,)).fetchone()
        except psycopg.Error as e:
            raise DatabaseError((getattr(e.diag, "message_primary", None) or str(e)).strip()) from None
    return found is not None


def _read_only(c, app_id: str):
    """The transaction's three read-only ways (see the top)"""
    c.execute("SET TRANSACTION READ ONLY")
    c.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(reader_of(app_id))))
    c.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(QUERY_TIMEOUT)))


def rows(app_id: str, password: str, schema: str, table: str, offset: int = 0, limit: int = 100) -> dict:
    """A page of a table's rows, read-only"""
    limit, offset = max(1, min(limit, MAX_ROWS)), max(0, offset)
    with _as_app(app_id, password) as c:
        try:
            with c.transaction() as tx:
                _read_only(c, app_id)
                cur = c.execute(sql.SQL("SELECT * FROM {}.{} LIMIT %s OFFSET %s").format(sql.Identifier(schema), sql.Identifier(table)),
                                (limit + 1, offset), prepare=True)
                names = [d.name for d in cur.description]
                found = cur.fetchall()
                raise psycopg.Rollback(tx)
        except psycopg.Error as e:
            raise DatabaseError((getattr(e.diag, "message_primary", None) or str(e)).strip()) from None
    return {"columns": names, "rows": [[_plain(v) for v in r] for r in found[:limit]], "more": len(found) > limit, "offset": offset}


def query(app_id: str, password: str, text: str, write: bool = False) -> dict:
    """Run SQL: read-only unless `write` (one statement, as the reader, rolled back); with `write`, as the
    app's own role, several statements allowed. The last statement's rows, at most MAX_ROWS of them."""
    with _as_app(app_id, password) as c:
        try:
            with c.transaction() as tx:
                if write:
                    c.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(QUERY_TIMEOUT)))
                    cur = c.execute(text)
                    # Several statements: their results in turn, the last one's kept
                    while cur.nextset():
                        pass
                else:
                    _read_only(c, app_id)
                    cur = c.execute(text, prepare=True)
                names = [d.name for d in cur.description] if cur.description else []
                found = cur.fetchmany(MAX_ROWS + 1) if cur.description else []
                count = cur.rowcount
                if not write:
                    raise psycopg.Rollback(tx)
        except psycopg.Error as e:
            raise DatabaseError((getattr(e.diag, "message_primary", None) or str(e)).strip()) from None
    return {"columns": names, "rows": [[_plain(v) for v in r] for r in found[:MAX_ROWS]], "truncated": len(found) > MAX_ROWS,
            "rowcount": count, "written": write}


def admin_host() -> str:
    """The server's host and port as the spawner reaches it (for a dump)"""
    found = conninfo_to_dict(ADMIN_URL)
    return f"{found.get('host', 'localhost')}:{found.get('port', 5432)}"
