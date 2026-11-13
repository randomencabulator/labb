"""The server's side of accounts (see auth.py): who's asking, and the endpoints for logging
in, setting up the server, invites, API keys, labb --login and the admin page.

A request is logged in by an API key (`Authorization: Bearer lb_…`, from labb and
scripts) or by the web session cookie. Requests that change something with the cookie also
need the `X-Labb: 1` header: other sites can't add it, so they can't make them for you. Logging
in, setting up and joining need it too, so another site can't log you into its account.

Over https the cookie is `__Host-labb_session`: only labb's own address can set it, so an app on
another port of the same name can't plant one. People's apps are on other ports of labb's name, and
browsers send a host's cookies to every port, so Caddy also takes labb's cookie out of the requests
it passes to apps (caddy/Caddyfile).
"""

import ipaddress


from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from labb.server import auth
from labb import usage

COOKIE = "labb_session"
# Over https: the name browsers only accept from this host itself, over https, for the whole site
SECURE_COOKIE = "__Host-labb_session"
CSRF_HEADER = "x-labb"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# How often labb asks whether its login was approved
DEVICE_POLL_SECONDS = 2


def bearer_key(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, key = header.partition(" ")
    return key.strip() if scheme.lower() == "bearer" and key.strip() else None


def authenticate(request: Request) -> dict | None:
    """The user making the request, if any"""

    if key := bearer_key(request):
        if not (found := auth.key_owner(key)):
            return None
        user, key_id = found
        # For the gateway's usage stats, and the labb version this key runs
        request.state.key_id = key_id
        usage.seen(key_id, request.headers.get(usage.CLIENT_HEADER))
        return user
    token = session_token(request)
    if not token:
        return None
    user = auth.user_for_session(token)
    if user and request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, "Requests that change something need the X-Labb: 1 header")
    return user


def session_token(request: Request) -> str | None:
    return request.cookies.get(SECURE_COOKIE) or request.cookies.get(COOKIE)


def needs_csrf_header(request: Request):
    """For the requests that log someone in, which have no session to check yet"""
    if request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, "This needs the X-Labb: 1 header")


def client_address(request: Request) -> str:
    return request.client.host if request.client else ""


def is_local(request: Request) -> bool:
    """A request from this machine itself (a checkout run), which sets up without the token"""
    try:
        return ipaddress.ip_address(client_address(request)).is_loopback
    except ValueError:
        return False


def current_user(request: Request) -> dict:
    user = authenticate(request)
    if user is None:
        raise HTTPException(401, "Log in first" if auth.has_users() else "This server isn't set up yet: open it in a browser")
    return user


def admin_user(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Only admins can do that")
    return user


def base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def log_in(request: Request, response: Response, user: dict) -> dict:
    """Start a web session: the cookie lasts as long as the session"""

    secure = request.url.scheme == "https"
    response.set_cookie(
        SECURE_COOKIE if secure else COOKIE, auth.create_session(user["id"]), max_age=auth.session_days() * 24 * 3600,
        httponly=True, samesite="lax", secure=secure, path="/",
    )
    if secure and COOKIE in request.cookies:
        # The name it had before, over https too
        response.delete_cookie(COOKIE, path="/")
    return user


class Credentials(BaseModel):
    username: str
    password: str


class SetupCredentials(Credentials):
    # The setup link's token (?setup=…), which the server prints when it starts without accounts
    token: str | None = None


class PasswordChange(BaseModel):
    current: str
    new: str


class KeyRequest(BaseModel):
    name: str


class InviteRequest(BaseModel):
    role: str = "member"


class UserChange(BaseModel):
    role: str | None = None
    disabled: bool | None = None


class DeviceStart(BaseModel):
    machine: str


class DevicePoll(BaseModel):
    device_code: str


class DeviceAnswer(BaseModel):
    approve: bool


router = APIRouter()


# --- Logging in --------------------------------------------------------------

@router.get("/auth/status")
def status(request: Request):
    """Whether the server needs setting up (and whether that takes the setup link's token), and
    who's logged in (null if nobody)"""
    try:
        user = authenticate(request)
    except HTTPException:
        user = None
    setup_needed = not auth.has_users()
    return {"setup_needed": setup_needed, "setup_token_needed": setup_needed and not is_local(request), "user": user}


@router.post("/auth/setup", dependencies=[Depends(needs_csrf_header)])
def setup(credentials: SetupCredentials, request: Request, response: Response):
    """Create the first account, the admin (only on a server without accounts). From another
    machine, it takes the token of the setup link the server printed when it started."""
    user = auth.setup(credentials.username, credentials.password, credentials.token, need_token=not is_local(request))
    return log_in(request, response, user)


@router.post("/auth/login", dependencies=[Depends(needs_csrf_header)])
def login(credentials: Credentials, request: Request, response: Response):
    address = client_address(request)
    if (wait := auth.wait_before_login(credentials.username.strip().lower(), address)) > 0:
        raise HTTPException(429, f"Too many wrong passwords; try again in {int(wait) + 1} seconds")
    user = auth.login(credentials.username, credentials.password, address)
    if user is None:
        raise HTTPException(401, "Wrong username or password")
    return log_in(request, response, user)


@router.post("/auth/logout", status_code=204)
def logout(request: Request):
    if token := session_token(request):
        auth.end_session(token)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/")
    response.delete_cookie(SECURE_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return response


@router.post("/auth/password", status_code=204)
def change_password(change: PasswordChange, request: Request, user: dict = Depends(current_user)):
    """Change your password; your other web sessions are logged out"""
    auth.change_password(user["id"], change.current, change.new, keep_session=session_token(request))
    return Response(status_code=204)


@router.get("/auth/invite/{token}")
def invite(token: str):
    """What an invite link is for (404 if it's used or expired)"""
    if (found := auth.open_invite(token)) is None:
        raise HTTPException(404, "This invite link was used already or has expired; ask for a new one")
    return {"role": found["role"], "expires_at": found["expires_at"]}


@router.post("/auth/invite/{token}", dependencies=[Depends(needs_csrf_header)])
def accept_invite(token: str, credentials: Credentials, request: Request, response: Response):
    """Create your account from an invite, and log in"""
    return log_in(request, response, auth.accept_invite(token, credentials.username, credentials.password))


# --- labb --login -----------------------------------------------------------

@router.post("/auth/device")
def device_start(start: DeviceStart, request: Request):
    """labb starts a login: show `user_code`, open `verification_url`, then poll /auth/device/token"""
    login_ = auth.start_device_login(start.machine)
    return {**login_, "interval": DEVICE_POLL_SECONDS,
            "verification_url": f"{base_url(request)}/?device={login_['user_code']}"}


@router.post("/auth/device/token")
def device_token(poll: DevicePoll):
    """labb asks whether its login was approved; the key comes once, with "approved\""""
    return auth.poll_device_login(poll.device_code)


@router.get("/auth/device/{user_code}")
def device_waiting(user_code: str, user: dict = Depends(current_user)):
    """The login waiting for approval under a code"""
    if (found := auth.device_login(user_code)) is None:
        raise HTTPException(404, "That code isn't waiting for approval; run labb --login again")
    return found


@router.post("/auth/device/{user_code}", status_code=204)
def device_answer(user_code: str, answer: DeviceAnswer, user: dict = Depends(current_user)):
    auth.answer_device_login(user_code, user, answer.approve)
    return Response(status_code=204)


@router.post("/auth/revoke", status_code=204)
def revoke_own_key(request: Request):
    """Revoke the API key this request was made with (labb --logout)"""
    if not (key := bearer_key(request)) or not auth.revoke_key_value(key):
        raise HTTPException(404, "No such key")
    return Response(status_code=204)


# --- Your API keys -------------------------------------------------------------

@router.get("/keys")
def keys(user: dict = Depends(current_user)):
    return auth.list_keys(user["id"])


@router.post("/keys")
def create_key(request: KeyRequest, user: dict = Depends(current_user)):
    """A new key: the key itself is in this answer only"""
    key, info = auth.create_key(user["id"], request.name)
    return {**info, "key": key}


@router.delete("/keys/{key_id}", status_code=204)
def revoke_key(key_id: str, user: dict = Depends(current_user)):
    if not auth.revoke_key(key_id, user["id"]):
        raise HTTPException(404, "No such key")
    return Response(status_code=204)


# --- Admin ---------------------------------------------------------------------

@router.get("/admin/users")
def users(admin: dict = Depends(admin_user)):
    return auth.list_users()


@router.patch("/admin/users/{user_id}")
def change_user(user_id: str, change: UserChange, admin: dict = Depends(admin_user)):
    return auth.update_user(user_id, role=change.role, disabled=change.disabled)


@router.get("/admin/invites")
def invites(admin: dict = Depends(admin_user)):
    return auth.list_invites()


@router.post("/admin/invites")
def create_invite(request: InviteRequest, http: Request, admin: dict = Depends(admin_user)):
    """A single-use invite link; the link is in this answer only"""
    token, info = auth.create_invite(admin["id"], request.role)
    return {**info, "url": f"{base_url(http)}/?invite={token}"}


@router.delete("/admin/invites/{invite_id}", status_code=204)
def cancel_invite(invite_id: str, admin: dict = Depends(admin_user)):
    if not auth.cancel_invite(invite_id):
        raise HTTPException(404, "No such invite")
    return Response(status_code=204)


@router.get("/admin/keys")
def all_keys(admin: dict = Depends(admin_user)):
    return auth.list_keys()


@router.delete("/admin/keys/{key_id}", status_code=204)
def revoke_any_key(key_id: str, admin: dict = Depends(admin_user)):
    if not auth.revoke_key(key_id):
        raise HTTPException(404, "No such key")
    return Response(status_code=204)
