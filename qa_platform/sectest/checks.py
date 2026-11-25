"""Passive security rules (OWASP WSTG / Top 10 / API Security Top 10 references).

Each rule looks at one captured exchange and never sends anything. An exchange is a dict::

    {"url", "method", "status", "headers": [(name, value), ...], "body": str, "content_type": str,
     "authenticated": bool, "role": str | None, "tls": {...} | None}

A finding is a dict with ``rule_id``, ``title``, ``category``, ``owasp``, ``severity``, ``confidence``, ``url``,
``param``, ``description`` and ``evidence`` (``expected`` / ``actual`` / details). The signature (rule + URL path +
parameter) makes the same issue on the same place one finding across runs.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlsplit

# rule id -> (title, category, OWASP reference, severity)
RULES: dict[str, tuple[str, str, str, str]] = {
    "HDR-CSP": ("Content-Security-Policy header missing", "Configuration", "A05:2021 Security Misconfiguration; WSTG-CONF-12", "medium"),
    "HDR-HSTS": ("Strict-Transport-Security header missing on HTTPS", "Configuration", "A05:2021; WSTG-CONF-07", "medium"),
    "HDR-XFO": ("Clickjacking protection missing (X-Frame-Options / frame-ancestors)", "Configuration", "A05:2021; WSTG-CLNT-09", "medium"),
    "HDR-XCTO": ("X-Content-Type-Options: nosniff missing", "Configuration", "A05:2021", "low"),
    "HDR-REF": ("Referrer-Policy header missing", "Configuration", "A05:2021", "low"),
    "HDR-SERVER": ("Server software version disclosed in headers", "Configuration", "A05:2021; WSTG-INFO-02", "low"),
    "COOKIE-SECURE": ("Cookie without the Secure flag", "Authentication and Sessions", "A07:2021; WSTG-SESS-02", "medium"),
    "COOKIE-HTTPONLY": ("Session cookie without HttpOnly", "Authentication and Sessions", "A07:2021; WSTG-SESS-02", "medium"),
    "COOKIE-SAMESITE": ("Cookie without SameSite", "Authentication and Sessions", "A01:2021 (CSRF); WSTG-SESS-05", "low"),
    "CORS-WILDCARD-CRED": ("CORS allows any origin with credentials", "Configuration", "A05:2021; API8:2023", "high"),
    "CORS-WILDCARD": ("CORS allows any origin", "Configuration", "A05:2021; API8:2023", "low"),
    "TLS-NONE": ("Application served over plain HTTP", "Configuration", "A02:2021 Cryptographic Failures; WSTG-CRYP-03", "high"),
    "TLS-OLD": ("Weak TLS protocol version", "Configuration", "A02:2021; WSTG-CRYP-01", "high"),
    "TLS-CERT": ("TLS certificate problem", "Configuration", "A02:2021; WSTG-CRYP-01", "medium"),
    "ERR-STACK": ("Stack trace or framework error disclosed", "Configuration", "A05:2021; WSTG-ERRH-01", "medium"),
    "ERR-DEBUG": ("Debug information exposed", "Configuration", "A05:2021; WSTG-CONF-05", "medium"),
    "DATA-SECRET": ("Secret or private key exposed in a response", "Sensitive Data Exposure", "A02:2021; API3:2023", "high"),
    "DATA-PII-CARD": ("Card-number-like value in a response", "Sensitive Data Exposure", "A02:2021", "medium"),
    "CACHE-AUTH": ("Authenticated response may be cached", "Configuration", "A04:2021; WSTG-ATHN-06", "low"),
    "FORM-HTTP": ("Form submits over plain HTTP", "Configuration", "A02:2021", "high"),
    "FORM-PASSWORD-GET": ("Password form uses GET", "Authentication and Sessions", "A07:2021", "medium"),
    "FORM-CSRF": ("State-changing form without an anti-CSRF token", "Authorization", "A01:2021; WSTG-SESS-05", "medium"),
    "FORM-AUTOCOMPLETE": ("Password field allows browser autocomplete", "Authentication and Sessions", "A07:2021", "info"),
    "DIR-LISTING": ("Directory listing enabled", "Configuration", "A05:2021; WSTG-CONF-04", "medium"),
    "MIXED-CONTENT": ("HTTPS page loads HTTP resources", "Configuration", "A02:2021", "low"),
    "URL-SECRET": ("Secret value in the URL query string", "Sensitive Data Exposure", "A04:2021; WSTG-INFO-05", "medium"),
    "REDIRECT-PARAM": ("Redirect parameter accepts a URL (open redirect candidate)", "Input Validation", "A01:2021; WSTG-CLNT-04", "info"),
    "SSRF-PARAM": ("Parameter takes a URL (server-side request forgery candidate)", "Input Validation", "A10:2021 SSRF; API7:2023", "info"),
    "UPLOAD-FORM": ("File upload found (validate type, size and content)", "Input Validation", "A04:2021; WSTG-BUSL-08", "info"),
}

SECURITY_HEADERS = ("content-security-policy", "strict-transport-security", "x-frame-options", "x-content-type-options",
                    "referrer-policy", "permissions-policy", "cache-control")
SESSION_COOKIE = re.compile(r"sess|sid|auth|token|jwt|login|asp\.net|jsession|phpsess|connect\.sid", re.I)
STACK = re.compile(r"Traceback \(most recent call last\)|at [a-zA-Z0-9_$.]+\([A-Za-z0-9_]+\.java:\d+\)|"
                   r"System\.[A-Za-z.]+Exception|Microsoft\.AspNetCore|ORA-\d{5}|SQLSTATE\[|PG::|"
                   r"java\.lang\.[A-Za-z]+Exception|Fatal error</b>:|Warning</b>:.*on line|"
                   r"(?:Unhandled|Uncaught) (?:exception|Error)|django\.core\.|werkzeug\.exceptions", re.I)
DEBUG = re.compile(r"(?:DEBUG\s*=\s*True|Whoops! There was an error|phpinfo\(\)|<title>phpinfo|X-Debug-Token|"
                   r"Laravel debug|Symfony Profiler|Spring Boot Actuator|/actuator/env|Server Error in '/' Application)",
                   re.I)
SECRET = re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----|AKIA[0-9A-Z]{16}|"
                    r"(?:api[_-]?key|secret[_-]?key|client[_-]?secret)\s*[\"':=]+\s*[\"']?[A-Za-z0-9_\-]{16,}", re.I)
CARD = re.compile(r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2})(?:[ -]?\d{4}){2}[ -]?\d{3,4}\b")
VERSION = re.compile(r"\d+\.\d+")
URL_PARAM = re.compile(r"^(?:url|uri|link|href|src|source|target|dest|destination|path|file|feed|host|site|image|"
                       r"callback|webhook|endpoint|proxy|fetch|load)$", re.I)
REDIRECT_PARAM = re.compile(r"^(?:redirect|redirect_uri|redirecturl|return|returnurl|return_to|next|continue|goto|"
                            r"forward|back|r|u)$", re.I)
SECRET_PARAM = re.compile(r"pass(word)?|pwd|secret|token|api[_-]?key|session|sid|auth", re.I)
CSRF_NAMES = re.compile(r"csrf|xsrf|token|authenticity|__requestverificationtoken|nonce", re.I)


def header(ex: dict[str, Any], name: str) -> str | None:
    name = name.lower()
    for k, v in ex.get("headers") or []:
        if k.lower() == name:
            return v
    return None


def headers_all(ex: dict[str, Any], name: str) -> list[str]:
    name = name.lower()
    return [v for k, v in ex.get("headers") or [] if k.lower() == name]


def signature(rule_id: str, url: str, param: str | None = None) -> str:
    p = urlsplit(url)
    host_path = f"{p.scheme}://{p.netloc.lower()}{re.sub(r'/[0-9]+(?=/|$)', '/{id}', p.path or '/')}"
    if rule_id.startswith(("HDR-", "TLS-", "CORS-", "COOKIE-")):
        host_path = f"{p.scheme}://{p.netloc.lower()}"  # a site-wide setting is one finding per site
    return hashlib.sha256(f"{rule_id}|{host_path}|{param or ''}".encode()).hexdigest()[:32]


def finding(rule_id: str, url: str, actual: str, *, expected: str | None = None, param: str | None = None,
            confidence: str = "high", severity: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    title, category, owasp, sev = RULES[rule_id]
    return {"rule_id": rule_id, "title": title, "category": category, "owasp": owasp, "severity": severity or sev,
            "confidence": confidence, "url": url, "param": param, "signature": signature(rule_id, url, param),
            "description": actual, "evidence": {"expected": expected or _EXPECTED.get(rule_id, "The control is in place."),
                                                "actual": actual, **(extra or {})}}


_EXPECTED = {
    "HDR-CSP": "A Content-Security-Policy header restricts where scripts and frames come from.",
    "HDR-HSTS": "HTTPS responses send Strict-Transport-Security with a max-age of at least 180 days.",
    "HDR-XFO": "X-Frame-Options DENY/SAMEORIGIN or CSP frame-ancestors prevents framing by other sites.",
    "HDR-XCTO": "X-Content-Type-Options: nosniff.",
    "HDR-REF": "A Referrer-Policy such as strict-origin-when-cross-origin.",
    "HDR-SERVER": "Server / X-Powered-By headers do not reveal product versions.",
    "COOKIE-SECURE": "Cookies on HTTPS carry the Secure flag.",
    "COOKIE-HTTPONLY": "Session cookies carry HttpOnly so scripts cannot read them.",
    "COOKIE-SAMESITE": "Cookies set SameSite=Lax or Strict.",
    "CORS-WILDCARD-CRED": "Credentialed CORS only for a fixed list of trusted origins.",
    "CORS-WILDCARD": "CORS limited to trusted origins.",
    "TLS-NONE": "The application is only served over HTTPS.",
    "TLS-OLD": "TLS 1.2 or newer only.",
    "TLS-CERT": "A valid certificate for the host name, issued by a trusted (company) CA.",
    "ERR-STACK": "Errors show a generic message; details only go to the server log.",
    "ERR-DEBUG": "Debug features are disabled outside development.",
    "DATA-SECRET": "Responses never contain keys or secrets.",
    "DATA-PII-CARD": "Card numbers are masked.",
    "CACHE-AUTH": "Authenticated pages send Cache-Control: no-store (or private, no-cache).",
    "FORM-HTTP": "Forms post only to HTTPS addresses.",
    "FORM-PASSWORD-GET": "Passwords are sent with POST, never in the URL.",
    "FORM-CSRF": "Every state-changing form carries an anti-CSRF token (or the cookie is SameSite=Strict).",
    "FORM-AUTOCOMPLETE": "Password fields set autocomplete=\"new-password\" or \"off\" where policy requires.",
    "DIR-LISTING": "Directory listings are disabled.",
    "MIXED-CONTENT": "HTTPS pages load every resource over HTTPS.",
    "URL-SECRET": "Secrets are never sent in the query string (they end up in logs and history).",
    "REDIRECT-PARAM": "Redirect targets are validated against an allow list.",
    "SSRF-PARAM": "URLs supplied by users are validated against an allow list before the server fetches them.",
    "UPLOAD-FORM": "Uploads are limited by type, size and content and stored outside the web root.",
}


def check_exchange(ex: dict[str, Any], *, first_for_site: bool = False) -> list[dict[str, Any]]:
    """Every passive rule for one response. Site-wide rules (headers, TLS) run on ``first_for_site`` exchanges."""
    out: list[dict[str, Any]] = []
    url = ex["url"]
    parts = urlsplit(url)
    https = parts.scheme == "https"
    ctype = (ex.get("content_type") or header(ex, "content-type") or "").lower()
    is_html = "html" in ctype
    body = ex.get("body") or ""
    status = int(ex.get("status") or 0)
    if first_for_site:
        if not https:
            out.append(finding("TLS-NONE", url, f"{parts.netloc} answered over http://."))
        if is_html and 200 <= status < 400:
            csp = header(ex, "content-security-policy")
            if not csp:
                out.append(finding("HDR-CSP", url, "No Content-Security-Policy header."))
            xfo = header(ex, "x-frame-options")
            if not xfo and not (csp and "frame-ancestors" in csp.lower()):
                out.append(finding("HDR-XFO", url, "Neither X-Frame-Options nor CSP frame-ancestors is set."))
            if not header(ex, "referrer-policy"):
                out.append(finding("HDR-REF", url, "No Referrer-Policy header."))
        if (header(ex, "x-content-type-options") or "").lower() != "nosniff" and 200 <= status < 400:
            out.append(finding("HDR-XCTO", url, f"X-Content-Type-Options is {header(ex, 'x-content-type-options') or 'missing'}."))
        if https:
            hsts = header(ex, "strict-transport-security")
            m = re.search(r"max-age=(\d+)", hsts or "")
            if not hsts or not m or int(m.group(1)) < 15552000:
                out.append(finding("HDR-HSTS", url, f"Strict-Transport-Security is {hsts or 'missing'}."))
        for h in ("server", "x-powered-by", "x-aspnet-version", "x-aspnetmvc-version"):
            v = header(ex, h)
            if v and VERSION.search(v):
                out.append(finding("HDR-SERVER", url, f"{h}: {v}", param=h))
        tls = ex.get("tls") or {}
        if https and tls.get("version") in ("TLSv1", "TLSv1.1", "SSLv3"):
            out.append(finding("TLS-OLD", url, f"Negotiated {tls['version']}."))
        if https and tls.get("error"):
            out.append(finding("TLS-CERT", url, str(tls["error"])[:300]))
    # cookies
    for raw in headers_all(ex, "set-cookie"):
        name = raw.split("=", 1)[0].strip()
        attrs = raw.lower()
        session = bool(SESSION_COOKIE.search(name))
        if https and "secure" not in [a.strip().split("=")[0] for a in attrs.split(";")[1:]]:
            out.append(finding("COOKIE-SECURE", url, f"Set-Cookie {name} has no Secure flag.", param=name,
                               severity="medium" if session else "low"))
        if session and "httponly" not in attrs:
            out.append(finding("COOKIE-HTTPONLY", url, f"Session cookie {name} has no HttpOnly flag.", param=name))
        if "samesite" not in attrs:
            out.append(finding("COOKIE-SAMESITE", url, f"Set-Cookie {name} has no SameSite attribute.", param=name))
    # CORS (from the response to a normal request; the active CORS check sends a foreign Origin)
    acao = header(ex, "access-control-allow-origin")
    if acao == "*":
        cred = (header(ex, "access-control-allow-credentials") or "").lower() == "true"
        out.append(finding("CORS-WILDCARD-CRED" if cred else "CORS-WILDCARD", url,
                           f"Access-Control-Allow-Origin: *{' with Allow-Credentials: true' if cred else ''}."))
    # disclosure
    if body:
        m = STACK.search(body)
        if m:
            out.append(finding("ERR-STACK", url, f"HTTP {status}: '{_around(body, m)}'", confidence="medium"))
        m = DEBUG.search(body) or (DEBUG.search(" ".join(f"{k}: {v}" for k, v in ex.get("headers") or [])))
        if m:
            out.append(finding("ERR-DEBUG", url, f"'{m.group(0)[:120]}'", confidence="medium"))
        m = SECRET.search(body)
        if m:
            out.append(finding("DATA-SECRET", url, f"A value matching a secret pattern ({m.group(0)[:12]}…) is in the "
                               "response.", confidence="medium"))
        m = CARD.search(body)
        if m and _luhn(re.sub(r"\D", "", m.group(0))):
            out.append(finding("DATA-PII-CARD", url, "A Luhn-valid card number appears unmasked.", confidence="medium"))
        if is_html and re.search(r"<title>\s*Index of /|Directory listing for /", body, re.I):
            out.append(finding("DIR-LISTING", url, "The page is a directory listing."))
        if https and is_html:
            m = re.search(r"""(?:src|href)\s*=\s*["']http://[^"']+\.(?:js|css|png|jpg|gif|svg|woff2?)["']""", body, re.I)
            if m:
                out.append(finding("MIXED-CONTENT", url, f"Loads {m.group(0)[:160]}.", confidence="medium"))
    # caching of authenticated content
    if ex.get("authenticated") and 200 <= status < 300 and (is_html or "json" in ctype):
        cc = (header(ex, "cache-control") or "").lower()
        if not any(x in cc for x in ("no-store", "private", "no-cache")):
            out.append(finding("CACHE-AUTH", url, f"Cache-Control is '{cc or 'missing'}' on an authenticated response."))
    # parameters
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if SECRET_PARAM.search(k) and v:
            out.append(finding("URL-SECRET", url, f"Query parameter '{k}' carries a value.", param=k))
        if REDIRECT_PARAM.match(k) and (v.startswith(("http", "/", "//")) or "%2F" in v):
            out.append(finding("REDIRECT-PARAM", url, f"Parameter '{k}' takes the address '{v[:80]}'.", param=k,
                               confidence="low"))
        elif URL_PARAM.match(k) and v.startswith(("http://", "https://")):
            out.append(finding("SSRF-PARAM", url, f"Parameter '{k}' takes the address '{v[:80]}'.", param=k,
                               confidence="low"))
    return out


def check_forms(page_url: str, forms: Iterable[dict[str, Any]], cookies_samesite_strict: bool = False) -> list[dict[str, Any]]:
    out = []
    for f in forms:
        action = f.get("action") or page_url
        method = (f.get("method") or "GET").upper()
        fields = f.get("fields") or []
        has_password = any(x.get("type") == "password" for x in fields)
        if action.startswith("http://") and (has_password or method == "POST"):
            out.append(finding("FORM-HTTP", page_url, f"The form posts to {action}.", param=action))
        if has_password and method == "GET":
            out.append(finding("FORM-PASSWORD-GET", page_url, "A form with a password field uses GET.", param=action))
        if method == "POST" and not has_password and not cookies_samesite_strict and \
                not any(CSRF_NAMES.search(x.get("name") or "") and x.get("type") == "hidden" for x in fields):
            out.append(finding("FORM-CSRF", page_url, f"POST form to {action} has no hidden anti-CSRF field.",
                               param=action, confidence="medium"))
        for x in fields:
            if x.get("type") == "password" and (x.get("autocomplete") or "").lower() not in ("off", "new-password",
                                                                                           "current-password"):
                out.append(finding("FORM-AUTOCOMPLETE", page_url, f"Password field '{x.get('name')}' has no "
                                   "autocomplete policy.", param=x.get("name") or "password", confidence="low"))
            if x.get("type") == "file":
                out.append(finding("UPLOAD-FORM", page_url, f"File field '{x.get('name')}' (accept="
                                   f"{x.get('accept') or 'any'}).", param=x.get("name") or "file", confidence="low"))
    return out


def _around(text: str, m: re.Match, n: int = 90) -> str:
    return " ".join(text[max(0, m.start() - 20): m.end() + n].split())[:200]


def _luhn(digits: str) -> bool:
    if not 13 <= len(digits) <= 19:
        return False
    total, alt = 0, False
    for d in reversed(digits):
        n = int(d)
        if alt:
            n *= 2
            n -= 9 if n > 9 else 0
        total += n
        alt = not alt
    return total % 10 == 0


__all__ = ["RULES", "check_exchange", "check_forms", "finding", "signature", "header"]
