"""23.3.34: sign-in, accounts (admin), and Network access (share the dashboard on the office network)."""

from __future__ import annotations

import socket
import subprocess
import sys
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from .. import auth
from ..auth import COOKIE, LOCAL_PROGRAM, User, UserStore
from ..config import APP_ROOT
from ..errors import QAError, error_body
from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/auth")

LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "testclient"}  # "testclient": the test suite's in-process client
BROWSER_HEADERS = ("origin", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest")


def users(request: Request) -> UserStore:
    return request.app.state.users


def me(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if user is None:
        raise QAError("AUTH_REQUIRED", "Please sign in.", status=401)
    return user


def is_local_program(request: Request) -> bool:
    """A program on this PC (installer, launcher, PowerShell, tests): loopback and no browser headers at all."""
    client = request.client.host if request.client else ""
    return client in LOOPBACK_CLIENTS and not any(h in request.headers for h in BROWSER_HEADERS)


def install(app: Any) -> None:
    """Every /api request needs a signed-in account allowed to make it (pages and scripts load without one)."""

    @app.middleware("http")
    async def signed_in_only(request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        store: UserStore | None = getattr(request.app.state, "users", None)
        if is_local_program(request):
            user: User | None = LOCAL_PROGRAM
        else:
            user = store.session_user(request.cookies.get(COOKIE)) if store is not None else None
        if user is None:
            if (request.method.upper(), path) not in auth.OPEN_PATHS:
                return JSONResponse(error_body("AUTH_REQUIRED", "Please sign in.", {}), status_code=401)
        else:
            refused = auth.refusal(user, request.method, path)
            if refused:
                return JSONResponse(error_body(refused[0], refused[1], {}), status_code=403)
        request.state.user = user
        token = auth.set_current_user(user)
        try:
            return await call_next(request)
        finally:
            auth.reset_current_user(token)


# ---- sign-in ----------------------------------------------------------------------------------------------------------


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordIn(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


def _set_cookie(response: Response, token: str) -> None:
    # Session cookie (gone when the browser closes); scripts cannot read it and other sites never send it.
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/")


@router.get("/me")
def who_am_i(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if user is None:
        raise QAError("AUTH_REQUIRED", "Please sign in.", status=401)
    return {"ok": True, "user": user.to_dict(), "roles": auth.ROLES}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, store: UserStore = Depends(users)) -> dict[str, Any]:
    user = store.authenticate(body.username, body.password)
    store.end_session(request.cookies.get(COOKIE))
    _set_cookie(response, store.start_session(user))
    return {"ok": True, "user": user.to_dict()}


@router.post("/logout")
def logout(request: Request, response: Response, store: UserStore = Depends(users)) -> dict[str, Any]:
    store.end_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/password")
def change_password(body: PasswordIn, request: Request, response: Response,
                    store: UserStore = Depends(users)) -> dict[str, Any]:
    user = me(request)
    if user.local:
        raise QAError("NOT_SIGNED_IN", "Sign in in the dashboard to change a password.", status=409)
    store.change_own_password(user, body.current, body.new)
    store.end_sessions(user.id)  # other browsers signed in with the old password are signed out
    _set_cookie(response, store.start_session(user))
    return {"ok": True}


# ---- accounts (admin) -------------------------------------------------------------------------------------------------

RoleName = Literal["admin", "tester", "developer", "manager"]


class UserIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    role: RoleName
    display_name: str = Field(default="", max_length=80)


class UserPatch(BaseModel):
    role: RoleName | None = None
    display_name: str | None = Field(default=None, max_length=80)
    disabled: bool | None = None
    password: str | None = Field(default=None, max_length=256)


@router.get("/users")
def list_users(store: UserStore = Depends(users)) -> dict[str, Any]:
    return {"ok": True, "users": store.list(), "roles": auth.ROLES}


@router.post("/users", status_code=201)
def create_user(body: UserIn, store: UserStore = Depends(users)) -> dict[str, Any]:
    return {"ok": True, "user": store.create(body.username, body.password, body.role, body.display_name)}


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch, request: Request, store: UserStore = Depends(users)) -> dict[str, Any]:
    current = me(request)
    if user_id == current.id and (body.disabled or (body.role and body.role != current.role)):
        raise QAError("OWN_ACCOUNT", "You cannot disable your own account or change your own role.", status=409)
    return {"ok": True, "user": store.update(user_id, **body.model_dump(exclude_none=True))}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, request: Request, store: UserStore = Depends(users)) -> dict[str, Any]:
    if user_id == me(request).id:
        raise QAError("OWN_ACCOUNT", "You cannot delete your own account.", status=409)
    store.delete(user_id)
    return {"ok": True}


# ---- Network access ---------------------------------------------------------------------------------------------------


def network_enabled(settings: Any) -> bool:
    return bool((settings.get().get("network") or {}).get("enabled"))


def bind_host(settings_data: dict[str, Any]) -> str:
    """0.0.0.0 (every network card) with Network access on; otherwise this PC only."""
    if (settings_data.get("network") or {}).get("enabled"):
        return "0.0.0.0"
    host = str((settings_data.get("server") or {}).get("host") or "127.0.0.1")
    return host if host in ("127.0.0.1", "localhost", "::1") else "127.0.0.1"


def addresses() -> tuple[str, list[str]]:
    """This PC's name and its IPv4 addresses on the office network."""
    name = socket.gethostname()
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(name, None, socket.AF_INET):
            ips.add(str(info[4][0]))
    except OSError:
        pass
    try:  # the address used to reach other PCs (no packet is sent for a UDP connect)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return name, sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.", "0.")))


class NetworkIn(BaseModel):
    enabled: bool


def _network_info(request: Request, rt: Runtime) -> dict[str, Any]:
    port = int((rt.settings.get().get("server") or {}).get("port") or 8765)
    name, ips = addresses()
    links = [f"http://{name}:{port}/"] + [f"http://{ip}:{port}/" for ip in ips]
    enabled = network_enabled(rt.settings)
    active = bool(getattr(request.app.state, "network_active", False))
    return {"ok": True, "enabled": enabled, "active": active, "restart_needed": enabled != active, "port": port,
            "pc_name": name, "addresses": ips, "links": links,
            "firewall_script": str(APP_ROOT / "ALLOW_NETWORK_ACCESS.cmd")}


@router.get("/network")
def get_network(request: Request, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return _network_info(request, rt)


@router.put("/network")
def put_network(body: NetworkIn, request: Request, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.settings.update({"network": {"enabled": body.enabled}})
    return _network_info(request, rt)


@router.post("/network/firewall")
def allow_through_firewall(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Run ALLOW_NETWORK_ACCESS.cmd as administrator; Windows asks for permission on this PC's screen."""
    script = APP_ROOT / "ALLOW_NETWORK_ACCESS.cmd"
    if sys.platform != "win32":
        raise QAError("NOT_WINDOWS", "The Windows Firewall rule can only be added on Windows.", status=409)
    if not script.is_file():
        raise QAError("FIREWALL_SCRIPT_MISSING", f"{script.name} is missing from the application folder.", status=404)
    port = int((rt.settings.get().get("server") or {}).get("port") or 8765)
    ps = (f"Start-Process -FilePath '{script}' -ArgumentList '{port}' -Verb RunAs")
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                     creationflags=0x08000000)  # CREATE_NO_WINDOW
    return {"ok": True, "asked": True}
