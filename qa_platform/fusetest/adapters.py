"""Read-only access to Red Hat Fuse / Apache Camel runtimes.

* **Jolokia** (Fuse on Karaf ``/hawtio/jolokia`` or ``/jolokia``, Fuse on EAP ``/jolokia``, Spring Boot
  ``/actuator/jolokia``): Camel MBeans are read (context, routes, statistics) and the route definition is dumped as
  XML. The only operations ever called are ``dumpRouteAsXml``, ``dumpRoutesAsXml`` and ``dumpRouteStatsAsXml``:
  start, stop, suspend, resume, remove, update, reset and restart are not in the whitelist, so SkyGuard can never
  restart, redeploy or change a route.
* **Spring Boot actuator** (``/actuator/camelroutes``, ``/actuator/health``, ``/actuator/info``): GET only.
* **Black-box**: no management access; the tester describes the route's entry and exit points and SkyGuard tests it
  from the outside only.

The adapter is chosen by version: Camel 2.x (Fuse 6 / 7) and Camel 3 / 4 (Camel Spring Boot / Fuse 7.11+ and the
Red Hat build of Apache Camel) name a few attributes differently; both are read.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from ..amqtest.jolokia import JolokiaClient
from ..errors import QAError

CAMEL_READ_OPS = {"dumpRouteAsXml", "dumpRoutesAsXml", "dumpRouteStatsAsXml"}
ROUTE_ATTRS = ["RouteId", "State", "EndpointUri", "ExchangesTotal", "ExchangesCompleted", "ExchangesFailed",
               "ExchangesInflight", "FailuresHandled", "Redeliveries", "MeanProcessingTime", "MaxProcessingTime",
               "LastProcessingTime", "LastExchangeFailureTimestamp", "LastExchangeCompletedTimestamp", "CamelId",
               "Description", "Uptime"]
CONTEXT_ATTRS = ["CamelId", "CamelVersion", "State", "Uptime", "ExchangesTotal", "ExchangesFailed", "TotalRoutes",
                 "StartedRoutes", "ManagementName"]


class CamelJolokia:
    kind = "jolokia"

    def __init__(self, j: JolokiaClient) -> None:
        self.j = j

    def runtime(self) -> dict[str, Any]:
        """Which runtime hosts Camel (Karaf, EAP / WildFly, Spring Boot, plain JVM) and the Jolokia agent version."""
        v = self.j.version()
        found = "jvm"
        for pattern, name in (("org.apache.karaf:*", "karaf"), ("jboss.as:*", "eap"), ("jboss.as.expr:*", "eap"),
                              ("org.springframework.boot:*", "springboot")):
            try:
                if self.j.search(pattern):
                    found = name
                    break
            except QAError:
                continue
        return {"runtime": found, "jolokia": v.get("agent"), "product": (v.get("info") or {}).get("product"),
                "vendor": (v.get("info") or {}).get("vendor"), "server_version": (v.get("info") or {}).get("version")}

    def contexts(self) -> list[dict[str, Any]]:
        out = []
        for mb in self.j.search("org.apache.camel:type=context,*"):
            try:
                a = self.j.read(mb, CONTEXT_ATTRS) or {}
            except QAError:
                a = {}
            out.append({"mbean": mb, "name": a.get("CamelId") or _field(mb, "name") or _field(mb, "context"),
                        "camel_version": a.get("CamelVersion"), "state": a.get("State"), "uptime": a.get("Uptime"),
                        "exchanges": a.get("ExchangesTotal"), "failed": a.get("ExchangesFailed"),
                        "routes": a.get("TotalRoutes"), "started_routes": a.get("StartedRoutes")})
        return out

    def _route_mbeans(self) -> list[str]:
        return self.j.search("org.apache.camel:type=routes,*")

    def routes(self) -> list[dict[str, Any]]:
        out = []
        for mb in self._route_mbeans():
            try:
                a = self.j.read(mb, ROUTE_ATTRS) or {}
            except QAError:
                a = {}
            out.append(_route_row(mb, a))
        return out

    def _mbean_for(self, route_id: str) -> str:
        if not re.match(r"^[A-Za-z0-9_.:@/$-]{1,200}$", route_id or ""):
            raise QAError("VALIDATION_FAILED", "Invalid route id.", status=422)
        for mb in self._route_mbeans():
            if _field(mb, "name") == route_id:
                return mb
            try:
                if (self.j.read(mb, "RouteId") or "") == route_id:
                    return mb
            except QAError:
                continue
        raise QAError("FUSE_ROUTE_NOT_FOUND", f"Route {route_id} was not found.", status=404)

    def stats(self, route_id: str) -> dict[str, Any]:
        mb = self._mbean_for(route_id)
        return _route_row(mb, self.j.read(mb, ROUTE_ATTRS) or {})

    def route_xml(self, route_id: str) -> str | None:
        mb = self._mbean_for(route_id)
        try:
            return self.j.exec_read_only(mb, "dumpRouteAsXml()", [], CAMEL_READ_OPS)
        except QAError as exc:
            if exc.error_code == "AMQ_OPERATION_REFUSED":
                raise
            return None


class CamelActuator:
    """Camel Spring Boot actuator endpoints (GET only)."""

    kind = "actuator"

    def __init__(self, base: str, *, auth: tuple[str, str] | None, verify: Any, timeout: float, transport: Any,
                 check: Any) -> None:
        self.base = base.rstrip("/")
        self.check = check
        kw: dict[str, Any] = {"timeout": timeout, "verify": verify, "trust_env": False}
        if auth:
            kw["auth"] = auth
        if transport is not None:
            kw["transport"] = transport
        self.http = httpx.Client(**kw)

    def close(self) -> None:
        self.http.close()

    def get(self, path: str) -> Any:
        url = f"{self.base}/{path.lstrip('/')}"
        if self.check:
            self.check(url, "GET")
        try:
            r = self.http.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise QAError("FUSE_UNAVAILABLE", f"The actuator at {self.base} is not answering ({type(exc).__name__}).",
                          status=502) from exc
        if r.status_code in (401, 403):
            raise QAError("FUSE_AUTH", f"The actuator refused the credentials (HTTP {r.status_code}).", status=502)
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise QAError("FUSE_ERROR", f"Actuator HTTP {r.status_code}.", status=502)
        try:
            return r.json()
        except ValueError:
            return None

    def runtime(self) -> dict[str, Any]:
        info = self.get("info") or {}
        return {"runtime": "springboot", "product": "Camel Spring Boot", "info": info}

    def contexts(self) -> list[dict[str, Any]]:
        h = self.get("health") or {}
        camel = ((h.get("components") or h.get("details") or {}).get("camelHealth") or
                 (h.get("components") or {}).get("camel") or {})
        return [{"name": (camel.get("details") or {}).get("name") or "camel", "state": camel.get("status") or h.get("status"),
                 "camel_version": (camel.get("details") or {}).get("version")}]

    def routes(self) -> list[dict[str, Any]]:
        data = self.get("camelroutes") or self.get("camel/routes") or []
        out = []
        for r in data if isinstance(data, list) else []:
            st = r.get("statistics") or r.get("details") or {}
            out.append({"id": r.get("id"), "state": r.get("status") or r.get("state"),
                        "from": r.get("endpointUri") or r.get("uri") or (r.get("details") or {}).get("from"),
                        "exchanges": st.get("exchangesTotal"), "failed": st.get("exchangesFailed"),
                        "inflight": st.get("exchangesInflight"), "mean_ms": st.get("meanProcessingTime"),
                        "max_ms": st.get("maxProcessingTime"), "last_ms": st.get("lastProcessingTime"),
                        "redeliveries": st.get("redeliveries"), "failures_handled": st.get("failuresHandled"),
                        "description": r.get("description")})
        return out

    def stats(self, route_id: str) -> dict[str, Any]:
        for r in self.routes():
            if r["id"] == route_id:
                return r
        raise QAError("FUSE_ROUTE_NOT_FOUND", f"Route {route_id} was not found.", status=404)

    def route_xml(self, route_id: str) -> str | None:
        if not re.match(r"^[A-Za-z0-9_.:@$-]{1,200}$", route_id or ""):
            raise QAError("VALIDATION_FAILED", "Invalid route id.", status=422)
        d = self.get(f"camelroutes/{route_id}/detail") or {}
        return d.get("xml") if isinstance(d, dict) else None


class BlackBox:
    """No management access: routes are the ones the tester described (entry and exit points)."""

    kind = "blackbox"

    def __init__(self, routes: list[dict[str, Any]]) -> None:
        self._routes = routes

    def runtime(self) -> dict[str, Any]:
        return {"runtime": "blackbox", "note": "No management access; tested from its entry and exit points only."}

    def contexts(self) -> list[dict[str, Any]]:
        return []

    def routes(self) -> list[dict[str, Any]]:
        return [{"id": r.get("id"), "state": "unknown (black-box)", "from": r.get("from"), "to": r.get("to") or [],
                 "description": r.get("description"), "blackbox": True} for r in self._routes]

    def stats(self, route_id: str) -> dict[str, Any]:
        for r in self.routes():
            if r["id"] == route_id:
                return r
        raise QAError("FUSE_ROUTE_NOT_FOUND", f"Route {route_id} is not described.", status=404)

    def route_xml(self, route_id: str) -> str | None:
        return None


def _field(mb: str, key: str) -> str:
    m = re.search(rf'[:,]{key}="?([^",]+)"?', mb)
    return m.group(1) if m else ""


def _route_row(mb: str, a: dict[str, Any]) -> dict[str, Any]:
    return {"id": a.get("RouteId") or _field(mb, "name"), "context": a.get("CamelId") or _field(mb, "context"),
            "state": a.get("State"), "from": a.get("EndpointUri"), "exchanges": a.get("ExchangesTotal"),
            "completed": a.get("ExchangesCompleted"), "failed": a.get("ExchangesFailed"),
            "inflight": a.get("ExchangesInflight"), "failures_handled": a.get("FailuresHandled"),
            "redeliveries": a.get("Redeliveries"), "mean_ms": a.get("MeanProcessingTime"),
            "max_ms": a.get("MaxProcessingTime"), "last_ms": a.get("LastProcessingTime"),
            "last_failure_at": a.get("LastExchangeFailureTimestamp"), "description": a.get("Description"),
            "uptime": a.get("Uptime")}


__all__ = ["CamelJolokia", "CamelActuator", "BlackBox", "CAMEL_READ_OPS"]
