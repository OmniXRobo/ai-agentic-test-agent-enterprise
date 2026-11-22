"""Approved active checks. Each category runs only when it is approved for the assessment; the payloads are benign
markers (no script execution, no data change, no denial of service), bounded per parameter, and every request still
passes the scope guard and the throttle. Checks that send POST (XML parser, business logic) need the target's
state-changing permission and a non-production environment; account lockout needs a dedicated test account.

``run_checks`` returns (results, findings): one result per check and place (PASSED / FAILED / BLOCKED), and the
findings for the failed ones.
"""

from __future__ import annotations

import re
import secrets
from difflib import SequenceMatcher
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..itest.common import CONTROL
from . import checks

checks.RULES.update({
    "CORS-REFLECT": ("CORS reflects any Origin", "Configuration", "A05:2021; API8:2023; WSTG-CLNT-07", "medium"),
    "XSS-REFLECTED": ("Reflected cross-site scripting (unescaped input)", "Input Validation", "A03:2021 Injection; WSTG-INPV-01", "high"),
    "SQLI-ERROR": ("SQL injection indicator (database error on a quote)", "Input Validation", "A03:2021; WSTG-INPV-05; API8:2023", "high"),
    "PATH-TRAVERSAL": ("Path traversal (file outside the web root read)", "Input Validation", "A01:2021; WSTG-ATHZ-01", "critical"),
    "OPEN-REDIRECT": ("Open redirect", "Input Validation", "A01:2021; WSTG-CLNT-04", "medium"),
    "CMD-INJECTION": ("Command injection indicator", "Input Validation", "A03:2021; WSTG-INPV-12", "high"),
    "XML-ENTITY": ("XML parser expands document type entities", "Input Validation", "A05:2021 (XXE); WSTG-INPV-07", "medium"),
    "AUTHN-MISSING": ("Protected page reachable without signing in", "Authorization", "A01:2021; A07:2021; WSTG-ATHN-04", "high"),
    "AUTHZ-VERTICAL": ("Role can reach a page reserved for another role (vertical access control)", "Authorization", "A01:2021; API5:2023; WSTG-ATHZ-02", "high"),
    "AUTHZ-BOLA": ("Object of another user readable (broken object-level authorization)", "Authorization", "A01:2021; API1:2023; WSTG-ATHZ-04", "high"),
    "SESSION-LOGOUT": ("Session still valid after logout", "Authentication and Sessions", "A07:2021; WSTG-SESS-06", "medium"),
    "SESSION-FIXATION": ("Session identifier not renewed at sign-in (fixation indicator)", "Authentication and Sessions", "A07:2021; WSTG-SESS-03", "medium"),
    "AUTH-LOCKOUT": ("No lockout or throttling after repeated failed sign-ins", "Authentication and Sessions", "A07:2021; WSTG-ATHN-03", "medium"),
})

CATEGORIES = {
    "cors": "CORS origin reflection", "xss": "Cross-site scripting (reflection)", "sqli": "SQL injection (error based)",
    "path_traversal": "Path traversal", "open_redirect": "Unsafe redirects", "command_injection": "Command injection",
    "xml": "XML parser security", "authz": "Authorization (anonymous, vertical, object level)",
    "session": "Session management (logout, fixation)", "lockout": "Account lockout",
}
NEEDS_STATE_CHANGE = {"xml", "lockout"}
DB_ERRORS = re.compile(r"SQL syntax|mysql_fetch|ORA-\d{5}|PostgreSQL.*ERROR|SQLSTATE|unterminated quoted string|"
                       r"Unclosed quotation mark|quoted string not properly terminated|SQLite3?::|sqlite3\.Operational"
                       r"Error|Microsoft OLE DB Provider for SQL Server|ODBC SQL Server Driver|syntax error at or near",
                       re.I)
FILE_PARAMS = re.compile(r"^(file|filename|path|page|template|doc|document|folder|dir|include|view|lang|img|image)$", re.I)
TRAVERSAL = [("../../../../../../etc/passwd", re.compile(r"root:[x*]?:0:0:")),
             ("..\\..\\..\\..\\..\\windows\\win.ini", re.compile(r"\[(fonts|extensions)\]", re.I))]
MAX_PARAMS = 25


def _with(url: str, name: str, value: str) -> str:
    p = urlsplit(url)
    q = [(k, value if k == name else v) for k, v in parse_qsl(p.query, keep_blank_values=True)]
    return urlunsplit(p._replace(query=urlencode(q)))


def _param_urls(app_map: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for page in app_map.get("pages") or []:
        for u in filter(None, (page["url"], page.get("requested"))):
            for k, _v in parse_qsl(urlsplit(u).query, keep_blank_values=True):
                out.append((u, k))
        for f in page.get("forms") or []:
            if f.get("method") == "GET":
                names = [x["name"] for x in f.get("fields") or [] if x.get("name") and x["type"] not in ("submit", "hidden",
                                                                                                       "password")]
                if names:
                    base = f["action"] + ("&" if "?" in f["action"] else "?") + urlencode({n: "test" for n in names})
                    out += [(base, n) for n in names]
    seen, uniq = set(), []
    for u, k in out:
        key = (urlsplit(u).path, k)
        if key not in seen:
            seen.add(key)
            uniq.append((u, k))
    return uniq[:MAX_PARAMS]


class Checker:
    def __init__(self, client_for: Callable[[str], Any], app_map: dict[str, Any], cfg: dict[str, Any], run_id: str,
                 record: Callable[..., None]) -> None:
        self.client_for = client_for
        self.map = app_map
        self.cfg = cfg
        self.run_id = run_id
        self.record = record  # record(name, status, category, finding=None, evidence=None, expected=, actual=)
        self.primary = cfg.get("primary_role") or (cfg.get("accounts") or [{}])[0].get("role") or "anonymous"

    def _client(self) -> Any:
        return self.client_for(self.primary)

    def _point(self) -> None:
        CONTROL.point(self.run_id)

    # -- input validation -----------------------------------------------------------------------------------------------
    def cors(self) -> None:
        c = self._client()
        sites = sorted({f"{urlsplit(p['url']).scheme}://{urlsplit(p['url']).netloc}/" for p in self.map.get("pages") or []})
        for site in sites[:5]:
            self._point()
            origin = "https://skyguard-origin-check.invalid"
            ex = c.get(site, headers={"Origin": origin}, follow=False)
            acao = checks.header(ex, "access-control-allow-origin")
            cred = (checks.header(ex, "access-control-allow-credentials") or "").lower() == "true"
            if acao == origin:
                f = checks.finding("CORS-REFLECT", site, f"Access-Control-Allow-Origin echoed {origin}"
                                   f"{' with credentials' if cred else ''}.", severity="high" if cred else "medium",
                                   extra={"exchange": c.evidence(ex, 300)})
                self.record(f"CORS reflection {site}", "FAILED", "cors", finding=f)
            else:
                self.record(f"CORS reflection {site}", "PASSED", "cors", evidence={"acao": acao},
                            expected="A foreign Origin is not allowed", actual=f"Access-Control-Allow-Origin: {acao or 'none'}")

    def xss(self) -> None:
        c = self._client()
        for url, name in _param_urls(self.map):
            self._point()
            mark = "sg" + secrets.token_hex(3)
            probe = f"{mark}\"'<sgx>"
            ex = c.get(_with(url, name, probe))
            body = ex.get("body") or ""
            hit = probe in body or f"{mark}'<sgx>" in body or ("<sgx>" in body and mark in body)
            test = f"Reflected XSS: {urlsplit(url).path} ?{name}"
            if hit and "html" in (ex.get("content_type") or ""):
                self.record(test, "FAILED", "xss", finding=checks.finding(
                    "XSS-REFLECTED", url, f"The value of '{name}' is written into the page without HTML escaping "
                    "(the marker <sgx> came back unescaped).", param=name, extra={"exchange": c.evidence(ex, 600)}))
            else:
                self.record(test, "PASSED", "xss", expected="Input is HTML-escaped", actual="Marker escaped or not reflected")

    def sqli(self) -> None:
        c = self._client()
        for url, name in _param_urls(self.map):
            self._point()
            base = c.get(url)
            if DB_ERRORS.search(base.get("body") or ""):
                self.record(f"SQL error indicator: {urlsplit(url).path} ?{name}", "BLOCKED", "sqli",
                            actual="The page shows a database error without any probe; not decidable")
                continue
            ex = c.get(_with(url, name, "1'\""))
            m = DB_ERRORS.search(ex.get("body") or "")
            test = f"SQL error indicator: {urlsplit(url).path} ?{name}"
            if m:
                self.record(test, "FAILED", "sqli", finding=checks.finding(
                    "SQLI-ERROR", url, f"A quote in '{name}' produced a database error: '{m.group(0)}'.", param=name,
                    confidence="medium", extra={"exchange": c.evidence(ex, 800)}))
            else:
                self.record(test, "PASSED", "sqli", expected="No database error", actual=f"HTTP {ex.get('status')}, no error")

    def path_traversal(self) -> None:
        c = self._client()
        for url, name in _param_urls(self.map):
            if not FILE_PARAMS.match(name):
                continue
            hit = None
            for payload, sig in TRAVERSAL:
                self._point()
                ex = c.get(_with(url, name, payload))
                if sig.search(ex.get("body") or ""):
                    hit = (payload, ex)
                    break
            test = f"Path traversal: {urlsplit(url).path} ?{name}"
            if hit:
                self.record(test, "FAILED", "path_traversal", finding=checks.finding(
                    "PATH-TRAVERSAL", url, f"'{name}={hit[0]}' returned the content of a system file.", param=name,
                    extra={"exchange": c.evidence(hit[1], 300)}))
            else:
                self.record(test, "PASSED", "path_traversal", expected="Files outside the application are refused",
                            actual="No system file content")

    def open_redirect(self) -> None:
        c = self._client()
        for url, name in _param_urls(self.map):
            if not checks.REDIRECT_PARAM.match(name):
                continue
            self._point()
            target = "https://skyguard-redirect-check.invalid/"
            ex = c.get(_with(url, name, target), follow=False)
            loc = checks.header(ex, "location") or ""
            test = f"Open redirect: {urlsplit(url).path} ?{name}"
            if 300 <= int(ex.get("status") or 0) < 400 and "skyguard-redirect-check.invalid" in loc:
                self.record(test, "FAILED", "open_redirect", finding=checks.finding(
                    "OPEN-REDIRECT", url, f"'{name}' redirects to any site (Location: {loc}).", param=name))
            else:
                self.record(test, "PASSED", "open_redirect", expected="Foreign redirect refused",
                            actual=f"HTTP {ex.get('status')} Location {loc or 'none'}")

    def command_injection(self) -> None:
        c = self._client()
        for url, name in _param_urls(self.map):
            self._point()
            a, b = 1000 + secrets.randbelow(8000), 3 + secrets.randbelow(90)
            product = str(a * b)
            base = c.get(url)
            if product in (base.get("body") or ""):
                continue
            hit = None
            for payload in (f";echo $(({a}*{b}))", f"|echo $(({a}*{b}))", f"& set /a {a}*{b}"):
                ex = c.get(_with(url, name, "test" + payload))
                body = ex.get("body") or ""
                if product in body and payload not in body:
                    hit = (payload, ex)
                    break
            test = f"Command injection indicator: {urlsplit(url).path} ?{name}"
            if hit:
                self.record(test, "FAILED", "command_injection", finding=checks.finding(
                    "CMD-INJECTION", url, f"'{name}' with a shell arithmetic payload returned its result ({product}).",
                    param=name, confidence="medium", extra={"exchange": c.evidence(hit[1], 400)}))
            else:
                self.record(test, "PASSED", "command_injection", expected="No shell evaluation", actual="Not evaluated")

    def xml(self) -> None:
        c = self._client()
        targets = [e for e in (self.map.get("soap") or []) + [e for e in self.map.get("endpoints") or [] if "xml" in e.lower()]][:5]
        for url in targets:
            self._point()
            mark = "SGXML" + secrets.token_hex(3).upper()
            doc = f'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY sg "{mark}">]><r>&sg;</r>'
            ex = c.request("POST", url, content=doc, headers={"Content-Type": "application/xml"}, follow=False)
            test = f"XML entity expansion: {urlsplit(url).path}"
            if mark in (ex.get("body") or ""):
                self.record(test, "FAILED", "xml", finding=checks.finding(
                    "XML-ENTITY", url, "An internal DTD entity was expanded in the response; external entities may be "
                    "processed too (verify manually in an isolated environment).", confidence="medium"))
            else:
                self.record(test, "PASSED", "xml", expected="DTD entities refused", actual=f"HTTP {ex.get('status')}")

    # -- authorization ------------------------------------------------------------------------------------------------
    def authz(self) -> None:
        roles = [a["role"] for a in self.cfg.get("accounts") or []]
        rules = self.cfg.get("restricted") or []  # [{url, allowed_roles: [...]}]
        anon = self.client_for("anonymous")
        protected = [p for p in self.map.get("pages") or [] if any(r != "anonymous" and 200 <= int(s or 0) < 300
                                                                   for r, s in p.get("access", {}).items())]
        for p in protected[:20]:
            self._point()
            if "anonymous" in p.get("access", {}) and 200 <= int(p["access"]["anonymous"] or 0) < 300:
                continue  # public page
            ex = anon.get(p["url"], follow=False)
            st = int(ex.get("status") or 0)
            ref_role = next(r for r, s in p["access"].items() if r != "anonymous" and 200 <= int(s or 0) < 300)
            test = f"Anonymous access: {urlsplit(p['url']).path}"
            if 200 <= st < 300 and not re.search(r"<input[^>]+type=[\"']?password", ex.get("body") or "", re.I):
                self.record(test, "FAILED", "authz", finding=checks.finding(
                    "AUTHN-MISSING", p["url"], f"Without signing in the page answered HTTP {st} (the {ref_role} account "
                    "reached it after signing in).", extra={"exchange": anon.evidence(ex, 300)}))
            else:
                self.record(test, "PASSED", "authz", expected="Sign-in required", actual=f"HTTP {st}")
        for rule in rules[:40]:
            url, allowed = rule.get("url"), set(rule.get("allowed_roles") or [])
            if not url:
                continue
            ref = None
            for role in roles:
                if role in allowed:
                    ref = ref or self.client_for(role).get(url)
            for role in roles:
                if role in allowed:
                    continue
                self._point()
                ex = self.client_for(role).get(url, follow=False)
                st = int(ex.get("status") or 0)
                similar = ref is not None and SequenceMatcher(None, (ex.get("body") or "")[:4000],
                                                              (ref.get("body") or "")[:4000]).quick_ratio() > 0.8
                test = f"Vertical access: {role} -> {urlsplit(url).path}"
                if 200 <= st < 300 and (similar or ref is None):
                    self.record(test, "FAILED", "authz", finding=checks.finding(
                        "AUTHZ-VERTICAL", url, f"The {role} account (not allowed) got HTTP {st} with the same content as "
                        f"an allowed role.", param=role, extra={"exchange": self.client_for(role).evidence(ex, 300)}))
                else:
                    self.record(test, "PASSED", "authz", expected=f"{role} refused", actual=f"HTTP {st}")
        for obj in (self.cfg.get("objects") or [])[:20]:  # [{template: "/orders/{id}", ids: {role: id}}]
            ids = obj.get("ids") or {}
            for owner, oid in ids.items():
                for other in ids:
                    if other == owner:
                        continue
                    self._point()
                    url = str(obj.get("template") or "").replace("{id}", str(oid))
                    if not url.startswith("http"):
                        url = (self.cfg.get("base_url") or "").rstrip("/") + url
                    ex = self.client_for(other).get(url, follow=False)
                    st = int(ex.get("status") or 0)
                    test = f"Object level access: {other} -> {owner}'s {urlsplit(url).path}"
                    if 200 <= st < 300:
                        self.record(test, "FAILED", "authz", finding=checks.finding(
                            "AUTHZ-BOLA", url, f"The {other} account read the object of {owner} (HTTP {st}).",
                            param=other, extra={"exchange": self.client_for(other).evidence(ex, 300)}))
                    else:
                        self.record(test, "PASSED", "authz", expected="Refused (403/404)", actual=f"HTTP {st}")

    def session(self) -> None:
        for acc in self.cfg.get("accounts") or []:
            if acc.get("auth") not in ("form", "cookie"):
                continue
            c = self.client_for(acc["role"])
            protected = acc.get("protected_url") or next((p["url"] for p in self.map.get("pages") or []
                                                          if p.get("access", {}).get(acc["role"]) == 200), None)
            if acc.get("auth") == "form" and acc.get("login_url"):
                fresh = self.client_for(f"{acc['role']}#fixation")
                fresh.get(acc["login_url"])
                before = dict(fresh.http.cookies.items())
                fresh.login()
                after = dict(fresh.http.cookies.items())
                same = [k for k in before if checks.SESSION_COOKIE.search(k) and before.get(k) == after.get(k)]
                test = f"Session renewal at sign-in ({acc['role']})"
                if same:
                    self.record(test, "FAILED", "session", finding=checks.finding(
                        "SESSION-FIXATION", acc["login_url"], f"Cookie(s) {', '.join(same)} kept the same value across "
                        "sign-in.", param=",".join(same), confidence="medium"))
                else:
                    self.record(test, "PASSED", "session", expected="New session id after sign-in", actual="Renewed")
                fresh.close()
            logout = acc.get("logout_url")
            if logout and protected:
                self._point()
                cookies = dict(c.http.cookies.items())
                c.get(logout)  # the configured logout of the test account
                replay = self.client_for(f"{acc['role']}#replay")
                for k, v in cookies.items():
                    replay.http.cookies.set(k, v)
                ex = replay.get(protected, follow=False)
                st = int(ex.get("status") or 0)
                still = 200 <= st < 300 and not re.search(r"type=[\"']?password", ex.get("body") or "", re.I)
                test = f"Logout invalidation ({acc['role']})"
                if still:
                    self.record(test, "FAILED", "session", finding=checks.finding(
                        "SESSION-LOGOUT", logout, f"The old session cookies still opened {protected} after logout "
                        f"(HTTP {st})."))
                else:
                    self.record(test, "PASSED", "session", expected="Old session refused", actual=f"HTTP {st}")
                c.logged_in = False
                c.login()

    def lockout(self) -> None:
        acc = self.cfg.get("lockout_account")  # a dedicated test account only
        if not acc or not acc.get("login_url"):
            self.record("Account lockout", "BLOCKED", "lockout", actual="No dedicated lockout test account configured")
            return
        c = self.client_for("lockout#probe")
        statuses = []
        for i in range(int(min(8, max(3, self.cfg.get("lockout_attempts") or 6)))):
            self._point()
            ex = c.request("POST", acc["login_url"], data={acc.get("username_field") or "username": acc.get("username"),
                                                           acc.get("password_field") or "password": f"wrong-{i}-"
                                                           + secrets.token_hex(3)}, follow=False, state_changing_ok=True)
            statuses.append(int(ex.get("status") or 0))
            if ex.get("status") in (423, 429) or re.search(r"locked|too many|try again later|captcha",
                                                             ex.get("body") or "", re.I):
                self.record("Account lockout", "PASSED", "lockout", expected="Lockout or throttling",
                            actual=f"Throttled after {i + 1} attempts")
                return
        self.record("Account lockout", "FAILED", "lockout", finding=checks.finding(
            "AUTH-LOCKOUT", acc["login_url"], f"{len(statuses)} wrong passwords in a row were all answered normally "
            f"({sorted(set(statuses))}) without lockout or throttling.", confidence="medium"))


def run_checks(categories: list[str], **kw: Any) -> None:
    ch = Checker(**kw)
    for cat in categories:
        fn = getattr(ch, cat, None)
        if fn is not None:
            fn()
