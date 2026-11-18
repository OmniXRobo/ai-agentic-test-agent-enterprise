"""23.12.0: what the API Testing module may send, and to where.

Every request (test, connection check, specification import, OAuth token, redirect) goes through ``check_url``:

* Only http and https.
* The host must be one of the application's **authorized targets**: the host of its base URL, recorded with the
  person's confirmation that they are authorized to test it, plus any extra host, ``*.domain`` or IP network an
  administrator or tester added for that application. Nothing else is reachable, so the module cannot be used to
  scan addresses or reach unrelated services.
* Never SkyGuard's own server port on this PC (a request from this PC without a browser counts as a local program
  with full rights, so the module must not be able to call SkyGuard's own API), and never cloud metadata addresses.
* A host name is resolved before each request and every address it resolves to is checked against the same rules
  (a name that points at a forbidden address is refused).
* Redirects are followed by SkyGuard, one at a time, and each target is checked again; credentials are dropped when
  a redirect leaves the original host.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from ..errors import QAError

METADATA = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254"),
            ipaddress.ip_address("100.100.100.200")}
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
MAX_TARGETS = 20


def _ip(host: str) -> ipaddress._BaseAddress | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def normalize_target(value: str) -> str:
    """One authorized target: host, host:port, *.domain, an IP address or an IP network (CIDR, at most /16 for IPv4
    and /64 for IPv6 so a target can never be a whole address space)."""
    v = (value or "").strip().lower()
    if "://" in v:
        v = urlsplit(v).netloc or v
    v = v.rstrip("/")
    if not v or len(v) > 255 or any(c.isspace() for c in v):
        raise QAError("API_TARGET_INVALID", f"'{value}' is not a host name, address or network.", status=422)
    if "/" in v:
        try:
            net = ipaddress.ip_network(v, strict=False)
        except ValueError:
            raise QAError("API_TARGET_INVALID", f"'{value}' is not a valid network.", status=422) from None
        if (net.version == 4 and net.prefixlen < 16) or (net.version == 6 and net.prefixlen < 64):
            raise QAError("API_TARGET_TOO_WIDE", f"The network {value} is too wide to authorize (IPv4 /16 or narrower, "
                          "IPv6 /64 or narrower).", status=422)
        return str(net)
    if v.startswith("*."):
        if v.count(".") < 2:
            raise QAError("API_TARGET_TOO_WIDE", f"'{value}' is too wide; name a domain such as *.company.local.",
                          status=422)
        return v
    return v


def split_host_port(netloc: str, scheme: str) -> tuple[str, int]:
    parts = urlsplit(f"{scheme}://{netloc}")
    host = (parts.hostname or "").lower()
    port = parts.port or (443 if scheme == "https" else 80)
    return host, port


def _target_matches(target: str, host: str, port: int) -> bool:
    if "/" in target:
        ip = _ip(host)
        try:
            return ip is not None and ip in ipaddress.ip_network(target, strict=False)
        except ValueError:
            return False
    if target.startswith("*."):
        return host.endswith(target[1:]) and host != target[2:]
    parts = urlsplit("//" + target)
    try:
        t_host, t_port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        return False
    if _ip(t_host) is not None and _ip(host) is not None:
        same = _ip(t_host) == _ip(host)
    else:
        same = t_host == host
    return same and (t_port is None or t_port == port)


class Policy:
    """The authorized targets of one application, and the PC's own forbidden addresses."""

    def __init__(self, targets: list[str], *, own_port: int = 8765, extra_forbidden_ports: set[int] | None = None,
                 resolver: Any = None) -> None:
        self.targets = [normalize_target(t) for t in targets if str(t).strip()][:MAX_TARGETS]
        self.own_port = int(own_port)
        self.forbidden_ports = {self.own_port} | set(extra_forbidden_ports or set())
        self.resolver = resolver or _resolve

    def authorized(self, host: str, port: int) -> bool:
        return any(_target_matches(t, host, port) for t in self.targets)

    def _forbidden_address(self, ip: ipaddress._BaseAddress, port: int) -> str | None:
        if ip in METADATA or (ip.version == 6 and ip.ipv4_mapped and ip.ipv4_mapped in METADATA):
            return "a cloud metadata address"
        loop = ip.is_loopback or ip.is_unspecified or (ip.version == 6 and ip.ipv4_mapped is not None and
                                                        (ip.ipv4_mapped.is_loopback or ip.ipv4_mapped.is_unspecified))
        if loop and port in self.forbidden_ports:
            return f"SkyGuard QA's own server on this PC (port {port})"
        return None

    def check_url(self, url: str) -> dict[str, Any]:
        """{scheme, host, port, addresses}; raises QAError API_TARGET_REFUSED with the reason."""
        parts = urlsplit(url or "")
        scheme = (parts.scheme or "").lower()
        if scheme not in ("http", "https"):
            raise QAError("API_TARGET_REFUSED", f"Only http and https URLs can be tested (got '{scheme or 'none'}').",
                          status=422)
        if parts.username or parts.password:
            raise QAError("API_TARGET_REFUSED", "Do not put a user name or password in the URL; use Authentication.",
                          status=422)
        try:
            host, port = split_host_port(parts.netloc, scheme)
        except ValueError:
            raise QAError("API_TARGET_REFUSED", "The URL has an invalid port.", status=422) from None
        if not host:
            raise QAError("API_TARGET_REFUSED", "The URL has no host name or address.", status=422)
        if not self.authorized(host, port):
            raise QAError("API_TARGET_NOT_AUTHORIZED",
                          f"{host}:{port} is not an authorized target of this API application. Add it under "
                          "Connection > Authorized targets (you must be authorized to test it).", status=403,
                          details={"host": host, "port": port})
        ip = _ip(host)
        addresses = [ip] if ip is not None else self.resolver(host, port)
        for a in addresses:
            why = self._forbidden_address(a, port)
            if why:
                raise QAError("API_TARGET_REFUSED", f"{host}:{port} resolves to {why}; it cannot be tested.",
                              status=403, details={"host": host, "address": str(a)})
        return {"scheme": scheme, "host": host, "port": port, "addresses": [str(a) for a in addresses]}


def _resolve(host: str, port: int) -> list[ipaddress._BaseAddress]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []  # the request then fails with a real connection error, reported as such
    out = []
    for info in infos:
        ip = _ip(info[4][0].split("%")[0])
        if ip is not None and ip not in out:
            out.append(ip)
    return out


class RateLimiter:
    """At most ``rate`` requests per second per application (a minimum spacing), shared by all runs."""

    def __init__(self) -> None:
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, key: str, rate: float) -> float:
        if rate <= 0:
            return 0.0
        gap = 1.0 / rate
        with self._lock:
            now = time.monotonic()
            at = max(now, self._next.get(key, 0.0))
            self._next[key] = at + gap
        delay = at - now
        if delay > 0:
            time.sleep(min(delay, 5.0))
        return delay


def is_state_changing(method: str) -> bool:
    return (method or "GET").upper() in STATE_CHANGING
