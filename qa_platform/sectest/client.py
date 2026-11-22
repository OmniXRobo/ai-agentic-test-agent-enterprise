"""The HTTP client of the security module: every request is checked by the scope guard and the throttle first,
redirects are followed by this code one at a time (each target checked again), and each exchange is captured with
sanitized headers for the evidence. Secrets come from the credential vault and are redacted from everything kept."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from ..errors import QAError
from ..itest.common import SANITIZER, Sanitizer, ScopeGuard, Throttle

AUTH_TYPES = ("none", "basic", "bearer", "header", "cookie", "form")
UA = "SkyGuard-QA-Security/23.14 (authorized intranet assessment)"


class _Hidden(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "input" and a.get("type", "").lower() == "hidden" and a.get("name"):
            self.fields[a["name"]] = a.get("value", "")


class SecClient:
    """One account (role) of one application for one run."""

    def __init__(self, scope: ScopeGuard, throttle: Throttle, *, account: dict[str, Any] | None = None,
                 secret: str | None = None, verify: Any = True, timeout: float = 15.0, transport: Any = None,
                 sanitizer: Sanitizer | None = None) -> None:
        self.scope = scope
        self.throttle = throttle
        self.account = account or {"role": "anonymous", "auth": "none"}
        self.secret = secret
        self.sanitizer = sanitizer or Sanitizer([secret] if secret else [])
        kw: dict[str, Any] = {"timeout": timeout, "follow_redirects": False, "verify": verify, "trust_env": False,
                              "headers": {"User-Agent": UA}}
        if transport is not None:
            kw["transport"] = transport
        self.http = httpx.Client(**kw)
        self.logged_in = False
        self.requests = 0

    @property
    def role(self) -> str:
        return self.account.get("role") or "anonymous"

    @property
    def authenticated(self) -> bool:
        return (self.account.get("auth") or "none") != "none"

    def close(self) -> None:
        self.http.close()

    def _auth_headers(self) -> dict[str, str]:
        a = self.account.get("auth") or "none"
        if a == "basic" and self.secret:
            return {}  # httpx auth below
        if a == "bearer" and self.secret:
            return {"Authorization": f"Bearer {self.secret}"}
        if a == "header" and self.secret:
            return {str(self.account.get("header_name") or "X-API-Key"): self.secret}
        return {}

    def login(self) -> dict[str, Any] | None:
        """Form sign-in (the only POST the security module sends without a state-changing permission: it is the
        configured authentication itself). Cookies stay in this client for the run."""
        a = self.account.get("auth") or "none"
        if a == "cookie" and self.secret:
            host = urlsplit(self.account.get("login_url") or self.account.get("base_url") or "").hostname
            self.http.cookies.set(str(self.account.get("cookie_name") or "session"), self.secret, domain=host or "")
            self.logged_in = True
            return None
        if a != "form" or self.logged_in:
            return None
        url = self.account.get("login_url")
        if not url or not self.secret:
            raise QAError("SEC_LOGIN_CONFIG", f"The {self.role} account needs a sign-in URL and a password in the vault.",
                          status=422)
        page = self.get(url)
        hidden = _Hidden()
        try:
            hidden.feed(page.get("body") or "")
        except Exception:  # noqa: BLE001
            pass
        data = {**hidden.fields, **(self.account.get("extra_fields") or {}),
                str(self.account.get("username_field") or "username"): str(self.account.get("username") or ""),
                str(self.account.get("password_field") or "password"): self.secret}
        target = urljoin(url, self.account.get("form_action") or url)
        self.scope.policy.check_url(target)
        self.throttle.take()
        r = self.http.post(target, data=data)
        self.requests += 1
        hops = 0
        while r.is_redirect and hops < 5:
            nxt = urljoin(str(r.url), r.headers.get("location", ""))
            self.scope.policy.check_url(nxt)
            self.throttle.take()
            r = self.http.get(nxt)
            hops += 1
        ok = r.status_code < 400 and not re.search(str(self.account.get("failure_text") or r"invalid (user|password)|"
                                                       r"login failed|incorrect"), r.text[:20000], re.I)
        self.logged_in = ok
        return {"status": r.status_code, "ok": ok, "url": str(r.url)}

    def request(self, method: str, url: str, *, headers: dict[str, str] | None = None, data: Any = None,
                json_body: Any = None, content: bytes | str | None = None, params: dict[str, Any] | None = None,
                follow: bool = True, state_changing_ok: bool = False) -> dict[str, Any]:
        method = method.upper()
        if state_changing_ok:
            self.scope.policy.check_url(url)
            ok, why = self.scope.in_scope(url)
            if not ok:
                raise QAError("ITEST_OUT_OF_SCOPE", f"Refused {method} {url}: {why}.", status=403)
        else:
            self.scope.check(url, method)
        self.throttle.take()
        h = {**self._auth_headers(), **(headers or {})}
        auth = None
        if (self.account.get("auth") == "basic") and self.secret:
            auth = (str(self.account.get("username") or ""), self.secret)
        try:
            r = self.http.request(method, url, headers=h, data=data, json=json_body, content=content, params=params,
                                  auth=auth)
        except httpx.TimeoutException as exc:
            return {"url": url, "method": method, "status": 0, "error": f"timeout: {exc}", "headers": [], "body": ""}
        except httpx.HTTPError as exc:
            tls_error = "certificate" in str(exc).lower() or "ssl" in str(exc).lower()
            return {"url": url, "method": method, "status": 0, "error": SANITIZER.text(str(exc), 300), "headers": [],
                    "body": "", "tls": {"error": str(exc)[:300]} if tls_error else None}
        self.requests += 1
        hops = []
        while follow and r.is_redirect and len(hops) < 5:
            nxt = urljoin(str(r.url), r.headers.get("location", ""))
            ok, _why = self.scope.in_scope(nxt)
            if not ok:
                hops.append({"to": nxt, "followed": False})
                break
            hops.append({"to": nxt, "followed": True})
            self.throttle.take()
            r = self.http.get(nxt, headers=self._auth_headers())
            self.requests += 1
        return self.capture(r, method, hops)

    def get(self, url: str, **kw: Any) -> dict[str, Any]:
        return self.request("GET", url, **kw)

    def capture(self, r: httpx.Response, method: str, hops: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        tls = None
        try:
            stream = r.extensions.get("network_stream")
            ssl_obj = stream.get_extra_info("ssl_object") if stream is not None else None
            if ssl_obj is not None:
                tls = {"version": ssl_obj.version(), "cipher": (ssl_obj.cipher() or [None])[0]}
        except Exception:  # noqa: BLE001
            tls = None
        text = ""
        ctype = r.headers.get("content-type", "")
        if any(t in ctype for t in ("text", "json", "xml", "html", "javascript")) or not ctype:
            try:
                text = r.text[:400000]
            except Exception:  # noqa: BLE001
                text = ""
        return {"url": str(r.url), "method": method, "status": r.status_code, "headers": list(r.headers.multi_items()),
                "body": text, "content_type": ctype, "authenticated": self.authenticated and self.logged_in or
                self.account.get("auth") in ("bearer", "header", "basic", "cookie"), "role": self.role, "tls": tls,
                "redirects": hops or [], "elapsed_ms": _elapsed(r)}

    def evidence(self, ex: dict[str, Any], body_chars: int = 1500) -> dict[str, Any]:
        """What is kept of an exchange: sanitized request line, status, headers and the start of the body."""
        return {"request": f"{ex.get('method')} {self.sanitizer.text(ex.get('url'), 500)}", "status": ex.get("status"),
                "role": ex.get("role"), "response_headers": self.sanitizer.headers(dict(ex.get("headers") or [])),
                "body_start": self.sanitizer.text(ex.get("body") or "", body_chars), "error": ex.get("error")}


def _elapsed(r: Any) -> int | None:
    try:
        return int(r.elapsed.total_seconds() * 1000)
    except (RuntimeError, AttributeError):  # mocked or streamed responses have no timing
        return None
