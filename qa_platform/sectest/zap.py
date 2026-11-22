"""OWASP ZAP adapter (a locally installed ZAP daemon, started by the security tester or by SkyGuard).

ZAP is used through its local REST API (``http://127.0.0.1:8090`` by default; the API key is kept in the credential
vault). SkyGuard creates a context limited to the approved scope (include / exclude regular expressions), spiders
(optionally as the configured user), waits for the passive scanner, runs the active scanner only when active scanning
is approved, supports pause / resume / stop, and turns ZAP alerts into SkyGuard findings. ZAP's own add-ons and rules
must be installed offline in ZAP (ZAP's "check for updates" stays off on an offline PC).

Only loopback ZAP addresses are accepted, so the adapter cannot be pointed at another machine's service.
"""

from __future__ import annotations

import ipaddress
import re
import time
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from ..errors import QAError
from . import checks

checks.RULES.update({"ZAP": ("OWASP ZAP alert", "Scanner", "OWASP ZAP", "medium")})
RISK = {"3": "high", "2": "medium", "1": "low", "0": "info", "High": "high", "Medium": "medium", "Low": "low",
        "Informational": "info"}
CONF = {"3": "high", "2": "medium", "1": "low", "4": "high", "High": "high", "Medium": "medium", "Low": "low",
        "Confirmed": "high", "False Positive": "low"}


class ZapClient:
    def __init__(self, base_url: str, api_key: str | None, *, timeout: float = 30.0, transport: Any = None) -> None:
        parts = urlsplit(base_url or "http://127.0.0.1:8090")
        host = (parts.hostname or "").strip("[]")
        try:
            loop = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loop = host == "localhost"
        if parts.scheme not in ("http", "https") or not loop:
            raise QAError("ZAP_NOT_LOCAL", "The ZAP address must be on this PC (127.0.0.1 or localhost).", status=422)
        self.base = f"{parts.scheme}://{parts.netloc}"
        self.key = api_key or ""
        kw: dict[str, Any] = {"timeout": timeout, "trust_env": False}
        if transport is not None:
            kw["transport"] = transport
        self.http = httpx.Client(**kw)

    def close(self) -> None:
        self.http.close()

    def call(self, component: str, kind: str, name: str, **params: Any) -> dict[str, Any]:
        if not re.match(r"^[a-zA-Z]+$", component + kind + name):
            raise QAError("VALIDATION_FAILED", "Invalid ZAP call.", status=422)
        try:
            r = self.http.get(f"{self.base}/JSON/{component}/{kind}/{name}/",
                              params={**{k: v for k, v in params.items() if v is not None}, "apikey": self.key})
        except httpx.HTTPError as exc:
            raise QAError("ZAP_UNAVAILABLE", f"OWASP ZAP is not answering at {self.base}. Start ZAP (daemon mode, API "
                          "enabled) and try again.", status=502, details=str(exc)[:200]) from exc
        if r.status_code >= 400:
            raise QAError("ZAP_ERROR", f"ZAP refused {component}/{name}: HTTP {r.status_code}", status=502,
                          details=r.text[:300])
        try:
            return r.json()
        except ValueError as exc:
            raise QAError("ZAP_ERROR", "ZAP answered something that is not JSON.", status=502) from exc

    # high level
    def version(self) -> str:
        return str(self.call("core", "view", "version").get("version"))

    def setup_context(self, name: str, include: list[str], exclude: list[str]) -> str:
        try:
            self.call("context", "action", "removeContext", contextName=name)
        except QAError:
            pass
        cid = str(self.call("context", "action", "newContext", contextName=name).get("contextId"))
        for rx in include:
            self.call("context", "action", "includeInContext", contextName=name, regex=rx)
        for rx in exclude:
            self.call("context", "action", "excludeFromContext", contextName=name, regex=rx)
        self.call("context", "action", "setContextInScope", contextName=name, booleanInScope="true")
        return cid

    def spider(self, url: str, context: str, max_children: int = 0) -> str:
        return str(self.call("spider", "action", "scan", url=url, contextName=context, maxChildren=max_children,
                             recurse="true").get("scan"))

    def spider_status(self, scan_id: str) -> int:
        return int(self.call("spider", "view", "status", scanId=scan_id).get("status") or 0)

    def passive_pending(self) -> int:
        return int(self.call("pscan", "view", "recordsToScan").get("recordsToScan") or 0)

    def active_scan(self, url: str, context_id: str, policy: str | None = None) -> str:
        return str(self.call("ascan", "action", "scan", url=url, contextId=context_id, recurse="true", inScopeOnly="true",
                             scanPolicyName=policy).get("scan"))

    def active_status(self, scan_id: str) -> int:
        return int(self.call("ascan", "view", "status", scanId=scan_id).get("status") or 0)

    def control(self, kind: str, scan_id: str, action: str) -> None:
        if kind not in ("spider", "ascan") or action not in ("pause", "resume", "stop"):
            raise QAError("VALIDATION_FAILED", "Invalid ZAP control.", status=422)
        self.call(kind, "action", action, scanId=scan_id)

    def alerts(self, base_url: str, start: int = 0, count: int = 500) -> list[dict[str, Any]]:
        return list(self.call("core", "view", "alerts", baseurl=base_url, start=start, count=count).get("alerts") or [])

    def set_rate(self, per_second: float, threads: int) -> None:
        try:
            self.call("ascan", "action", "setOptionThreadPerHost", Integer=max(1, int(threads)))
            delay = int(1000 / per_second) if per_second and per_second > 0 else 0
            self.call("ascan", "action", "setOptionDelayInMs", Integer=delay)
            self.call("spider", "action", "setOptionThreadCount", Integer=max(1, int(threads)))
        except QAError:
            pass  # older ZAP: defaults stay


def alert_to_finding(a: dict[str, Any]) -> dict[str, Any]:
    url = a.get("url") or ""
    rule = f"ZAP-{a.get('pluginId') or a.get('alertRef') or 'x'}"
    f = checks.finding("ZAP", url, str(a.get("description") or a.get("alert") or "")[:1500], param=a.get("param") or None,
                       severity=RISK.get(str(a.get("risk")), "medium"), confidence=CONF.get(str(a.get("confidence")), "medium"),
                       extra={"zap_alert": a.get("alert"), "attack": a.get("attack"), "evidence": str(a.get("evidence") or "")[:500],
                              "solution": str(a.get("solution") or "")[:800], "cwe": a.get("cweid"), "wasc": a.get("wascid"),
                              "reference": str(a.get("reference") or "")[:500]})
    f["rule_id"] = rule
    f["title"] = f"ZAP: {a.get('alert') or a.get('name') or 'alert'}"
    f["owasp"] = f"CWE-{a.get('cweid')}" if a.get("cweid") not in (None, "", "-1", "0") else "OWASP ZAP"
    f["signature"] = checks.signature(rule, url, a.get("param"))
    f["source"] = "zap"
    return f


def wait(fn: Callable[[], int], point: Callable[[], None], deadline: float, target: int = 100,
         sleep: float = 2.0) -> int:
    while True:
        point()
        v = fn()
        if v >= target or time.monotonic() > deadline:
            return v
        time.sleep(sleep)
