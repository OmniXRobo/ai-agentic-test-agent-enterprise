"""23.3.32: only the dashboard itself may use the local API.

The server listens on 127.0.0.1 only, but any web page open in a browser on this PC can still send requests to
http://127.0.0.1:<port>. Two checks stop that without a login:

* Host: the request must name this PC (127.0.0.1, localhost or ::1). A page that points its own domain name at
  127.0.0.1 (DNS rebinding) sends its own name, and is refused, also for reads.
* Origin: a browser marks every request with the page it comes from (Origin, Sec-Fetch-Site). A request that changes
  something is refused when it comes from any other page than the dashboard. Programs on this PC (the installer,
  PowerShell, tests) send neither header and are not affected.

23.3.34, Network access on: colleagues open the dashboard by this PC's name or address, so the Host check is off;
every request then needs a signed-in account (see qa_platform.auth), which a rebinding page never has. The Origin
check stays: a request that changes something must come from the dashboard page itself.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

from ..errors import error_body

LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def allowed_hosts() -> set[str]:
    """Loopback names, plus any in QA_EXTRA_HOSTS (comma separated; the test suite adds its test client's name)."""
    extra = {h.strip().lower() for h in os.environ.get("QA_EXTRA_HOSTS", "").split(",") if h.strip()}
    return LOOPBACK | extra


def _host_name(value: str) -> str:
    value = (value or "").strip().lower()
    if value.startswith("["):
        return value.split("]", 1)[0] + "]"
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def refusal(method: str, headers: Any, network: bool = False) -> str | None:
    """Why this request is refused, or None when it may go on."""
    hosts = allowed_hosts()
    host = _host_name(headers.get("host", ""))
    if host and host not in hosts and not network:
        return "This address is not this PC. Open the dashboard at http://127.0.0.1."
    if method.upper() in SAFE_METHODS:
        return None
    site = (headers.get("sec-fetch-site") or "").lower()
    if site in ("cross-site", "same-site"):
        return "Requests from other web pages are not accepted."
    origin = headers.get("origin")
    if origin:
        parts = urlsplit(origin)
        if origin == "null" or (not network and (parts.hostname or "").lower() not in hosts | {"::1"}):
            return "Requests from other web pages are not accepted."
        if host and headers.get("host", "").lower() != (parts.netloc or "").lower():
            return "Requests from other web pages are not accepted."
    return None


def install(app: Any) -> None:
    @app.middleware("http")
    async def same_origin_only(request, call_next):  # type: ignore[no-untyped-def]
        reason = refusal(request.method, request.headers, bool(getattr(request.app.state, "network_active", False)))
        if reason:
            return JSONResponse(error_body("REQUEST_REFUSED", reason, {}), status_code=403)
        return await call_next(request)
