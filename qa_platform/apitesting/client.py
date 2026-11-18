"""23.12.0: sends one API request safely and records what happened, with secrets redacted.

* The target is checked by safety.Policy before the request and before every redirect (followed here, at most 5).
* Authentication is added from the secure credential store at the moment of sending; secret values never enter the
  saved request, the evidence, the logs or a generated script.
* Retries (the application's retry policy) are made only for idempotent methods (GET, HEAD, OPTIONS) after a
  connection error, a timeout or 502/503/504. A state-changing request is never repeated after an uncertain outcome.
* A response larger than the application's limit is cut off and marked as truncated.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode, urljoin, urlsplit

import httpx

from ..errors import QAError
from .safety import Policy, RateLimiter

IDEMPOTENT = {"GET", "HEAD", "OPTIONS"}
SECRET_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key",
                  "x-auth-token", "x-csrf-token", "x-xsrf-token"}
SECRET_KEYS = re.compile(r"(?i)pass(word|wd)?|secret|token|api[_-]?key|otp|cookie|session|authorization|credential|pin$")
VAR = re.compile(r"\{\{\s*([A-Za-z_][\w.-]*)\s*\}\}")
MAX_BODY_KEEP = 200_000
NAMESPACE = "apicred"


class UnresolvedVariable(Exception):
    pass


def substitute(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        whole = VAR.fullmatch(value.strip())
        if whole:
            name = whole.group(1)
            if name not in variables:
                raise UnresolvedVariable(name)
            return variables[name]

        def rep(m: re.Match) -> str:
            if m.group(1) not in variables:
                raise UnresolvedVariable(m.group(1))
            return str(variables[m.group(1)])

        return VAR.sub(rep, value)
    if isinstance(value, dict):
        return {k: substitute(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, variables) for v in value]
    return value


class Redactor:
    def __init__(self, extra_header_names: set[str] | None = None) -> None:
        self.values: set[str] = set()
        self.headers = SECRET_HEADERS | {h.lower() for h in (extra_header_names or set())}

    def add(self, value: str | None) -> None:
        if value and len(str(value)) >= 3:
            self.values.add(str(value))
            if ":" not in value:
                self.values.add(base64.b64encode(value.encode()).decode())

    def text(self, s: Any) -> Any:
        if not isinstance(s, str):
            return s
        for v in sorted(self.values, key=len, reverse=True):
            s = s.replace(v, "[redacted]")
        return s

    def header_map(self, headers: dict[str, str]) -> dict[str, str]:
        return {k: ("[redacted]" if k.lower() in self.headers else self.text(v)) for k, v in headers.items()}

    def data(self, value: Any, depth: int = 0) -> Any:
        if depth > 30:
            return value
        if isinstance(value, dict):
            return {k: ("[redacted]" if SECRET_KEYS.search(str(k)) and v not in (None, "") else self.data(v, depth + 1))
                    for k, v in value.items()}
        if isinstance(value, list):
            return [self.data(v, depth + 1) for v in value]
        return self.text(value)

    def url(self, url: str, secret_params: set[str]) -> str:
        parts = urlsplit(url)
        if not parts.query:
            return self.text(url)
        q = []
        for pair in parts.query.split("&"):
            k, _, v = pair.partition("=")
            q.append(f"{k}=[redacted]" if k.lower() in secret_params or SECRET_KEYS.search(k) else pair)
        return self.text(parts._replace(query="&".join(q)).geturl())


class AuthProvider:
    """Builds the authentication of one request from an application's (or a role's) settings and its stored secrets."""

    def __init__(self, app_id: str, auth: dict[str, Any], secrets: Any, prefix: str = "") -> None:
        self.app_id = app_id
        self.auth = auth or {"type": "none"}
        self.secrets = secrets
        self.prefix = prefix  # "" for the application, "role:<name>:" for a role
        self._token: tuple[str, float] | None = None
        self._lock = threading.Lock()

    def secret(self, field: str) -> str | None:
        if self.secrets is None:
            return None
        return self.secrets.get(NAMESPACE, f"{self.app_id}:{self.prefix}{field}")

    @property
    def type(self) -> str:
        return self.auth.get("type") or "none"

    def missing(self) -> list[str]:
        t = self.type
        need = {"basic": ["password"], "bearer": ["token"], "api_key": ["api_key"], "session": ["password"],
                "oauth2": ["client_secret"]}.get(t, [])
        if t == "oauth2" and self.auth.get("grant") == "password":
            need = need + ["password"]
        out = [f for f in need if not self.secret(f)]
        if t in ("basic", "session") and not self.auth.get("username"):
            out.append("username")
        if t == "oauth2" and not self.auth.get("token_url"):
            out.append("token URL")
        if t == "client_cert" and not self.auth.get("cert_file"):
            out.append("client certificate file")
        return out

    def apply(self, headers: dict[str, str], query: list[tuple[str, str]], redactor: Redactor,
              token_fetch: Callable[[dict[str, Any]], tuple[str, int]] | None = None) -> None:
        t = self.type
        if t == "basic":
            user, pw = self.auth.get("username") or "", self.secret("password") or ""
            redactor.add(pw)
            token = base64.b64encode(f"{user}:{pw}".encode()).decode()
            redactor.add(token)
            headers["Authorization"] = f"Basic {token}"
        elif t == "bearer":
            tok = self.secret("token") or ""
            redactor.add(tok)
            headers["Authorization"] = f"Bearer {tok}"
        elif t == "api_key":
            key, name = self.secret("api_key") or "", self.auth.get("name") or "X-API-Key"
            redactor.add(key)
            redactor.headers.add(name.lower())
            if self.auth.get("in") == "query":
                query.append((name, key))
            else:
                headers[name] = key
        elif t == "oauth2" and token_fetch is not None:
            tok = self.oauth_token(token_fetch)
            redactor.add(tok)
            headers["Authorization"] = f"Bearer {tok}"
        elif t == "custom":
            for h in self.auth.get("headers") or []:
                name = str(h.get("name") or "")
                if not name:
                    continue
                if h.get("secret"):
                    v = self.secret(f"custom:{name}") or ""
                    redactor.add(v)
                    redactor.headers.add(name.lower())
                else:
                    v = str(h.get("value") or "")
                headers[name] = v

    def oauth_token(self, fetch: Callable[[dict[str, Any]], tuple[str, int]], force: bool = False) -> str:
        with self._lock:
            if self._token and not force and self._token[1] > time.monotonic() + 30:
                return self._token[0]
            form = {"grant_type": self.auth.get("grant") or "client_credentials",
                    "client_id": self.auth.get("client_id") or "", "client_secret": self.secret("client_secret") or ""}
            if self.auth.get("scope"):
                form["scope"] = self.auth["scope"]
            if form["grant_type"] == "password":
                form.update(username=self.auth.get("username") or "", password=self.secret("password") or "")
            token, expires = fetch({"url": self.auth.get("token_url"), "form": form})
            self._token = (token, time.monotonic() + max(30, int(expires or 300)))
            return token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None


class Sender:
    """One application's HTTP client for one run (cookies of a session sign-in live only as long as the run)."""

    limiter = RateLimiter()

    def __init__(self, app: dict[str, Any], policy: Policy, secrets: Any, *, cert_root: Path | None = None,
                 transport: Any = None) -> None:
        self.app = app
        self.cfg = app.get("config") or {}
        self.policy = policy
        self.secrets = secrets
        self.cert_root = cert_root
        self.transport = transport
        self.auths: dict[str, AuthProvider] = {}
        self.redactor = Redactor()
        self.warnings: list[str] = []
        self._client: httpx.Client | None = None
        self._session_done: set[str] = set()

    # ---- configuration -------------------------------------------------------------------------------------------
    def base_url(self) -> str:
        return build_base_url(self.cfg)

    def _verify(self) -> Any:
        tls = self.cfg.get("tls") or {}
        if tls.get("verify", True) is False:
            if (self.app.get("environment") or "").lower() == "production":
                return True  # never turned off in Production (refused when saving too)
            self.warnings.append("TLS certificate verification is OFF for this application (set by a person).")
            return False
        ca = tls.get("ca_file")
        if ca and self.cert_root is not None:
            p = (self.cert_root / Path(ca).name)
            if p.is_file():
                return str(p)
            self.warnings.append(f"The CA certificate {Path(ca).name} is missing; the system certificates are used.")
        return True

    def _cert(self, auth: dict[str, Any], provider: AuthProvider) -> Any:
        if auth.get("type") != "client_cert" or self.cert_root is None:
            return None
        cert = self.cert_root / Path(auth.get("cert_file") or "").name
        key = self.cert_root / Path(auth.get("key_file") or "").name if auth.get("key_file") else None
        if not cert.is_file():
            raise QAError("API_CERT_MISSING", "The client certificate file of this application is missing.", status=422)
        pw = provider.secret("cert_password")
        if pw:
            self.redactor.add(pw)
        return (str(cert), str(key) if key else None, pw) if key or pw else str(cert)

    def client(self) -> httpx.Client:
        if self._client is None:
            auth = self.cfg.get("auth") or {}
            provider = self.provider("app")
            timeout = max(1.0, min(600.0, float(self.cfg.get("timeout_ms") or 30000) / 1000))
            proxy = (self.cfg.get("proxy") or "").strip() or None
            kw: dict[str, Any] = {"verify": self._verify(), "timeout": httpx.Timeout(timeout),
                                  "follow_redirects": False, "trust_env": bool(self.cfg.get("use_system_proxy"))}
            cert = self._cert(auth, provider)
            if cert:
                kw["cert"] = cert
            if proxy:
                kw["proxy"] = proxy
            if self.transport is not None:
                kw["transport"] = self.transport
            self._client = httpx.Client(**kw)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def provider(self, which: str) -> AuthProvider:
        if which not in self.auths:
            if which.startswith("role:"):
                name = which[5:]
                role = next((r for r in self.cfg.get("roles") or [] if r.get("name") == name), None)
                if role is None:
                    raise QAError("API_ROLE_MISSING", f"The role {name} is not configured on this application.",
                                  status=422)
                self.auths[which] = AuthProvider(self.app["id"], role.get("auth") or {}, self.secrets,
                                                 prefix=f"role:{name}:")
            else:
                self.auths[which] = AuthProvider(self.app["id"], self.cfg.get("auth") or {}, self.secrets)
        return self.auths[which]

    # ---- sending ----------------------------------------------------------------------------------------------------
    def _token_fetch(self, req: dict[str, Any]) -> tuple[str, int]:
        url = req["url"] or ""
        if not url.startswith(("http://", "https://")):
            url = urljoin(self.base_url().rstrip("/") + "/", url.lstrip("/"))
        self.policy.check_url(url)
        self.redactor.add(req["form"].get("client_secret"))
        self.redactor.add(req["form"].get("password"))
        r = self.client().post(url, data=req["form"], headers={"Accept": "application/json"})
        if r.status_code != 200:
            raise QAError("API_OAUTH_FAILED", f"The OAuth token request returned HTTP {r.status_code}.", status=502)
        try:
            data = r.json()
        except ValueError:
            raise QAError("API_OAUTH_FAILED", "The OAuth token response is not JSON.", status=502) from None
        tok = data.get("access_token")
        if not tok:
            raise QAError("API_OAUTH_FAILED", "The OAuth token response has no access_token.", status=502)
        self.redactor.add(tok)
        return tok, int(data.get("expires_in") or 300)

    def _session_login(self, which: str, provider: AuthProvider) -> None:
        if which in self._session_done:
            return
        a = provider.auth
        pw = provider.secret("password") or ""
        self.redactor.add(pw)
        variables = {"username": a.get("username") or "", "password": pw}
        body = substitute(a.get("login_body") or {"username": "{{username}}", "password": "{{password}}"}, variables)
        url = urljoin(self.base_url().rstrip("/") + "/", str(a.get("login_path") or "login").lstrip("/"))
        self.policy.check_url(url)
        r = self.client().post(url, json=body) if (a.get("login_content_type") or "json") == "json" else \
            self.client().post(url, data=body)
        for c in r.cookies.values() if hasattr(r.cookies, "values") else []:
            self.redactor.add(c)
        if r.status_code >= 400:
            raise QAError("API_SESSION_LOGIN_FAILED", f"The session sign-in returned HTTP {r.status_code}.", status=502)
        self._session_done.add(which)

    def build(self, test: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
        """The concrete request (URL, method, headers, body) with variables filled in; no secrets added yet."""
        path = str(substitute(test.get("path") or "/", variables))
        for k, v in (substitute(test.get("path_params") or {}, variables)).items():
            path = path.replace("{" + str(k) + "}", quote(str(v), safe=""))
        if re.search(r"\{[A-Za-z_][\w-]*\}", path):
            missing = re.findall(r"\{([A-Za-z_][\w-]*)\}", path)
            raise UnresolvedVariable(f"path parameter {', '.join(missing)}")
        if path.startswith(("http://", "https://")):
            url = path
        else:
            url = self.base_url().rstrip("/") + "/" + path.lstrip("/") if path not in ("", "/") else self.base_url()
        query = [(str(q.get("name")), "" if q.get("value") is None else
                  (json.dumps(q["value"]) if isinstance(q.get("value"), (dict, list)) else
                   str(q["value"]).lower() if isinstance(q.get("value"), bool) else str(q.get("value"))))
                 for q in substitute(test.get("query") or [], variables) if q.get("name") and not q.get("disabled")]
        headers = {str(h["name"]): str(h.get("value") if h.get("value") is not None else "")
                   for h in substitute(self.cfg.get("headers") or [], variables) + substitute(test.get("headers") or [], variables)
                   if h.get("name") and not h.get("disabled")}
        body = substitute(test.get("body"), variables)
        ctype = test.get("content_type") or (("application/json") if isinstance(body, (dict, list)) else None)
        return {"method": (test.get("method") or "GET").upper(), "url": url, "query": query, "headers": headers,
                "body": body, "content_type": ctype}

    def send(self, req: dict[str, Any], *, auth: str = "app", max_bytes: int | None = None) -> dict[str, Any]:
        """Send and record. Never raises for HTTP or network problems: they are part of the result."""
        max_bytes = int(max_bytes or self.cfg.get("max_response_bytes") or 5 * 1024 * 1024)
        method = req["method"]
        headers = dict(req["headers"])
        query = list(req["query"])
        record: dict[str, Any] = {"method": method}
        provider = None
        if auth != "none":
            provider = self.provider(auth)
            missing = provider.missing()
            if missing:
                record.update(error=f"Authentication is not complete: {', '.join(missing)} missing. Save it under "
                                    "Authentication.", error_kind="auth_config", blocked=True)
                record["request"] = self._request_view(req, headers, query)
                return record
        content: bytes | None = None
        if req["body"] is not None:
            ctype = req["content_type"] or "application/json"
            headers.setdefault("Content-Type", ctype)
            if "json" in ctype.lower():
                content = json.dumps(req["body"], ensure_ascii=False).encode("utf-8") if not isinstance(req["body"], str) \
                    else req["body"].encode("utf-8")
            elif "x-www-form-urlencoded" in ctype.lower() and isinstance(req["body"], dict):
                content = urlencode({k: v for k, v in req["body"].items()}).encode()
            else:
                content = (req["body"] if isinstance(req["body"], str) else json.dumps(req["body"])).encode("utf-8")
        headers.setdefault("Accept", "application/json, application/xml;q=0.9, text/plain;q=0.8, */*;q=0.5")
        headers.setdefault("User-Agent", "SkyGuard-QA-API-Testing/23.12")
        try:
            if provider is not None:
                if provider.type == "session":
                    self._session_login(auth, provider)
                provider.apply(headers, query, self.redactor, self._token_fetch)
        except QAError as exc:
            record.update(error=exc.message, error_kind="auth", blocked=False)
            record["request"] = self._request_view(req, headers, query)
            return record
        record["request"] = self._request_view(req, headers, query)
        retries = max(0, min(3, int(self.cfg.get("retries") or 0))) if method in IDEMPOTENT else 0
        rate = float(self.cfg.get("rate_per_second") or 10)
        attempt = 0
        url = req["url"]
        while True:
            attempt += 1
            try:
                self.policy.check_url(url)
            except QAError as exc:
                record.update(error=exc.message, error_kind="policy", blocked=True)
                return record
            self.limiter.wait(self.app["id"], rate)
            started = time.perf_counter()
            try:
                resp, body, truncated, hops = self._exchange(method, url, headers, query, content, max_bytes, provider)
            except QAError as exc:
                record.update(error=exc.message, error_kind="policy", blocked=True)
                return record
            except httpx.TimeoutException as exc:
                record.update(error=f"Timed out after {int((time.perf_counter() - started) * 1000)} ms "
                                    f"({type(exc).__name__}).", error_kind="timeout",
                              time_ms=int((time.perf_counter() - started) * 1000))
                if attempt <= retries:
                    time.sleep(0.5 * 2 ** (attempt - 1))
                    continue
                return record
            except httpx.HTTPError as exc:
                msg = str(exc) or type(exc).__name__
                kind = "tls" if "CERTIFICATE" in msg.upper() or "SSL" in msg.upper() else "connection"
                record.update(error=f"{type(exc).__name__}: {self.redactor.text(msg)[:300]}", error_kind=kind,
                              time_ms=int((time.perf_counter() - started) * 1000))
                if attempt <= retries and kind == "connection":
                    time.sleep(0.5 * 2 ** (attempt - 1))
                    continue
                return record
            ms = int((time.perf_counter() - started) * 1000)
            if resp.status_code == 401 and provider is not None and provider.type == "oauth2" and not record.get("refreshed"):
                provider.invalidate()  # the server refused the token: get a new one once and send again
                record["refreshed"] = True
                headers.pop("Authorization", None)
                provider.apply(headers, query, self.redactor, self._token_fetch)
                continue
            if resp.status_code in (502, 503, 504) and attempt <= retries:
                time.sleep(0.5 * 2 ** (attempt - 1))
                continue
            record.update(self._response_view(resp, body, truncated, ms, attempt, hops))
            return record

    def _exchange(self, method: str, url: str, headers: dict[str, str], query: list[tuple[str, str]],
                  content: bytes | None, max_bytes: int, provider: AuthProvider | None) -> tuple[Any, bytes, bool, list[str]]:
        hops: list[str] = []
        origin = urlsplit(url).netloc
        cur_method, cur_url, cur_query, cur_content = method, url, query, content
        cur_headers = dict(headers)
        for _ in range(6):
            self.policy.check_url(cur_url)
            with self.client().stream(cur_method, cur_url, params=cur_query or None, headers=cur_headers,
                                      content=cur_content) as resp:
                if resp.is_redirect and self.cfg.get("follow_redirects", True) and len(hops) < 5:
                    loc = resp.headers.get("location") or ""
                    nxt = urljoin(str(resp.url), loc)
                    hops.append(self.redactor.url(nxt, set()))
                    self.policy.check_url(nxt)  # every hop is checked before it is followed
                    if urlsplit(nxt).netloc != origin:
                        cur_headers = {k: v for k, v in cur_headers.items() if k.lower() not in self.redactor.headers}
                    if resp.status_code in (301, 302, 303) and cur_method not in ("GET", "HEAD"):
                        cur_method, cur_content = "GET", None
                    cur_url, cur_query = nxt, []
                    continue
                buf = bytearray()
                truncated = False
                for chunk in resp.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        truncated = True
                        del buf[max_bytes:]
                        break
                return resp, bytes(buf), truncated, hops
        raise QAError("API_TOO_MANY_REDIRECTS", "More than 5 redirects.", status=502)

    def _request_view(self, req: dict[str, Any], headers: dict[str, str], query: list[tuple[str, str]]) -> dict[str, Any]:
        url = req["url"]
        if query:
            url = url + ("&" if "?" in url else "?") + urlencode(query)
        secret_params = {str((self.cfg.get("auth") or {}).get("name") or "").lower()} if \
            (self.cfg.get("auth") or {}).get("in") == "query" else set()
        body = req["body"]
        return {"method": req["method"], "url": self.redactor.url(url, secret_params),
                "headers": self.redactor.header_map(headers), "body": self.redactor.data(body),
                "content_type": req.get("content_type")}

    def _response_view(self, resp: Any, body: bytes, truncated: bool, ms: int, attempts: int,
                       hops: list[str]) -> dict[str, Any]:
        ctype = resp.headers.get("content-type", "")
        text = body.decode(resp.encoding or "utf-8", errors="replace") if body else ""
        parsed, is_json = None, False
        if body and ("json" in ctype.lower() or text.lstrip()[:1] in ("{", "[")):
            try:
                parsed, is_json = json.loads(text), True
            except ValueError:
                pass
        kind = "json" if is_json else "xml" if "xml" in ctype.lower() or text.lstrip().startswith("<?xml") else "text"
        return {"status": resp.status_code, "reason": resp.reason_phrase, "headers": self.redactor.header_map(dict(resp.headers)),
                "content_type": ctype, "time_ms": ms, "size": len(body), "truncated": truncated, "attempts": attempts,
                "redirects": hops, "is_json": is_json, "json": parsed, "body_kind": kind,
                "text": text, "http_version": getattr(resp, "http_version", None)}

    def public(self, record: dict[str, Any]) -> dict[str, Any]:
        """What is saved: redacted, body kept up to 200 KB."""
        out = dict(record)
        text = self.redactor.text(out.pop("text", "") or "")
        parsed = out.pop("json", None)
        out["body"] = text[:MAX_BODY_KEEP]
        out["body_cut"] = len(text) > MAX_BODY_KEEP
        if out.get("is_json"):
            out["json"] = self.redactor.data(parsed) if len(text) <= MAX_BODY_KEEP else None
        if out.get("error"):
            out["error"] = self.redactor.text(out["error"])
        if self.warnings:
            out["warnings"] = sorted(set(self.warnings))
        return out


def build_base_url(cfg: dict[str, Any]) -> str:
    if (cfg.get("base_url") or "").strip():
        return cfg["base_url"].strip().rstrip("/")
    proto = (cfg.get("protocol") or "http").lower()
    host = (cfg.get("host") or "").strip()
    if not host:
        return ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # IPv6 literal
    port = str(cfg.get("port") or "").strip()
    default = (proto == "http" and port == "80") or (proto == "https" and port == "443")
    base_path = "/" + str(cfg.get("base_path") or "").strip("/") if str(cfg.get("base_path") or "").strip("/") else ""
    return f"{proto}://{host}{'' if not port or default else ':' + port}{base_path}"
