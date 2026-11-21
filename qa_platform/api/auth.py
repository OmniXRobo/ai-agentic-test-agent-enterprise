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
    # 23.13.0: each account has its own email, TFS project and TFS Personal Access Token (mandatory)
    email: str = Field(min_length=3, max_length=254)
    tfs_project: str = Field(min_length=1, max_length=200)
    tfs_pat: str = Field(min_length=8, max_length=512, description="Write-only. Kept in the OS credential store.")
    tfs_mode: Literal["online", "offline"] | None = None
    send_welcome: bool = True


class UserPatch(BaseModel):
    role: RoleName | None = None
    display_name: str | None = Field(default=None, max_length=80)
    disabled: bool | None = None
    password: str | None = Field(default=None, max_length=256)
    email: str | None = Field(default=None, max_length=254)
    tfs_project: str | None = Field(default=None, max_length=200)
    tfs_pat: str | None = Field(default=None, max_length=512)
    tfs_mode: Literal["online", "offline"] | None = None


@router.get("/users")
def list_users(store: UserStore = Depends(users)) -> dict[str, Any]:
    return {"ok": True, "users": store.list(), "roles": auth.ROLES}


def app_link(request: Request, rt: Runtime) -> str:
    """The address colleagues open: the friendly HTTPS name when HTTPS is on, otherwise this PC's network name."""
    from ..https_setup import public_url
    url = public_url(rt.settings.get())
    if url:
        return url
    port = int((rt.settings.get().get("server") or {}).get("port") or 8765)
    if network_enabled(rt.settings):
        return f"http://{addresses()[0]}:{port}/"
    return f"http://127.0.0.1:{port}/"


@router.post("/users", status_code=201)
def create_user(body: UserIn, request: Request, store: UserStore = Depends(users),
                rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    store.check_email(body.email)
    mode = body.tfs_mode or rt.tfs.active_mode()
    check = rt.tfs.check_pat(body.tfs_pat, body.tfs_project, mode)  # an invalid PAT stops here (422)
    user = store.create(body.username, body.password, body.role, body.display_name, email=body.email,
                        tfs_project=body.tfs_project)
    try:
        rt.tfs.save_personal(user["username"], mode, pat=body.tfs_pat)
    except QAError:
        store.delete(user["id"])  # no account without its PAT
        raise
    welcome: dict[str, Any] = {"sent": False, "reason": "Not asked."}
    if body.send_welcome:
        from ..welcome import send_welcome
        welcome = send_welcome(rt, user, body.password, app_link(request, rt))
        if welcome.get("sent"):  # the first password travelled by email: it must be changed at the first sign-in
            store.db.execute("UPDATE users SET must_change = 1 WHERE id = ?", (user["id"],))
            user = store._public(store._row(user["id"]))
    return {"ok": True, "user": user, "pat_check": check, "welcome": welcome}


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch, request: Request, store: UserStore = Depends(users),
                rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    current = me(request)
    if user_id == current.id and (body.disabled or (body.role and body.role != current.role)):
        raise QAError("OWN_ACCOUNT", "You cannot disable your own account or change your own role.", status=409)
    values = body.model_dump(exclude_none=True)
    pat, mode = values.pop("tfs_pat", None), values.pop("tfs_mode", None) or rt.tfs.active_mode()
    row = store._row(user_id)
    if pat:
        rt.tfs.check_pat(pat, values.get("tfs_project") or row.get("tfs_project") or "", mode)
    user = store.update(user_id, **values)
    if pat:
        rt.tfs.save_personal(user["username"], mode, pat=pat)
    rt.tfs.invalidate()
    return {"ok": True, "user": user}


@router.post("/users/{user_id}/welcome")
def resend_welcome(user_id: int, request: Request, store: UserStore = Depends(users),
                   rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """23.13.0: email the sign-in address and the user manual again (the password is not known any more)."""
    from ..welcome import send_welcome
    user = store._public(store._row(user_id))
    return {"ok": True, "welcome": send_welcome(rt, user, None, app_link(request, rt))}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, request: Request, store: UserStore = Depends(users)) -> dict[str, Any]:
    if user_id == me(request).id:
        raise QAError("OWN_ACCOUNT", "You cannot delete your own account.", status=409)
    name = store._row(user_id)["username"]
    store.delete(user_id)
    request.app.state.runtime.tfs.forget_personal(name)
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


class HttpsIn(BaseModel):
    enabled: bool
    hostname: str = Field(default="skyguard-qa", max_length=63)
    port: int = Field(default=443, ge=1, le=65535)
    renew: bool = False


def _https_info(request: Request, rt: Runtime) -> dict[str, Any]:
    from .. import https_setup
    info = https_setup.status(rt.paths.root, rt.settings.get())
    http_port = int((rt.settings.get().get("server") or {}).get("port") or 8765)
    launcher = getattr(request.app.state, "launcher", None)
    running = getattr(getattr(launcher, "https_server", None), "started", False)
    return {"ok": True, **info, "running": bool(running), "restart_needed": bool(info["enabled"]) != bool(running),
            "network_enabled": network_enabled(rt.settings), "http_port": http_port,
            "script": str(APP_ROOT / "ENABLE_HTTPS.cmd"), "colleague_script": str(APP_ROOT / "TRUST_SKYGUARD_QA.cmd")}


@router.get("/network/https")
def get_https(request: Request, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return _https_info(request, rt)


@router.put("/network/https")
def put_https(body: HttpsIn, request: Request, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """23.13.0: HTTPS with a friendly name; the certificates are made on this PC (offline) the first time."""
    from .. import https_setup
    try:
        hostname = https_setup.check_hostname(body.hostname)
    except ValueError as exc:
        raise QAError("HTTPS_NAME_INVALID", str(exc), status=422) from exc
    http_port = int((rt.settings.get().get("server") or {}).get("port") or 8765)
    if body.port == http_port:
        raise QAError("HTTPS_PORT_TAKEN", f"Port {http_port} is the HTTP port. Use 443 (the default) for HTTPS.",
                      status=422)
    made = None
    if body.enabled:
        current = https_setup.config(rt.settings.get())
        if body.renew or not https_setup.ready(rt.paths.root) or current["hostname"] != hostname:
            made = https_setup.make_certificates(rt.paths.root, hostname)
    rt.settings.update({"server": {"https": {"enabled": body.enabled, "hostname": hostname, "port": body.port}}})
    return {**_https_info(request, rt), "made": made}


@router.get("/https-ca.crt")
def https_ca(rt: Runtime = Depends(runtime)) -> Any:
    """The local CA certificate (public, no key): colleagues add it with TRUST_SKYGUARD_QA.cmd."""
    from starlette.responses import FileResponse

    from .. import https_setup
    ca = https_setup.files(rt.paths.root)["ca"]
    if not ca.is_file():
        raise QAError("HTTPS_NOT_READY", "HTTPS is not set up on this PC.", status=404)
    return FileResponse(ca, media_type="application/x-x509-ca-cert", filename=https_setup.CA_FILE)


@router.post("/network/https/trust")
def trust_https(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Run ENABLE_HTTPS.cmd as administrator: trusts the local CA, maps the name to this PC, opens the HTTPS port."""
    from .. import https_setup
    c = https_setup.config(rt.settings.get())
    script = APP_ROOT / "ENABLE_HTTPS.cmd"
    if sys.platform != "win32":
        raise QAError("NOT_WINDOWS", "This step can only run on Windows.", status=409)
    if not script.is_file():
        raise QAError("HTTPS_SCRIPT_MISSING", f"{script.name} is missing from the application folder.", status=404)
    if not https_setup.ready(rt.paths.root):
        raise QAError("HTTPS_NOT_READY", "Turn HTTPS on first (the certificate is made then).", status=409)
    ca = https_setup.files(rt.paths.root)["ca"]
    def q(v: object) -> str:  # a PowerShell single-quoted literal: a quote inside a folder name cannot end it
        return "'" + str(v).replace("'", "''") + "'"
    args = f"{q(c['hostname'])},{q(int(c['port']))},{q(chr(34) + str(ca) + chr(34))}"
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                      f"Start-Process -FilePath {q(script)} -ArgumentList {args} -Verb RunAs"],
                     creationflags=0x08000000)  # CREATE_NO_WINDOW
    return {"ok": True, "asked": True}


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
