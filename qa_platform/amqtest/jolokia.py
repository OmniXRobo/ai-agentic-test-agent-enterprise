"""Read-only broker management over Jolokia (HTTP/JSON) with separate adapters for ActiveMQ Classic and Artemis.

Only ``read``, ``search``, ``version`` and a whitelist of read-only operations (browse, count, list as JSON, address
settings) are ever sent. Purge, remove, move, retry, copy, send, pause, resume, delete, reset and stop operations are
not in the whitelist, so they cannot be called through this module whatever a test definition says. Browsing does not
consume messages.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ..errors import QAError

CLASSIC_READ_OPS = {"browse", "browseAsTable", "browseMessages"}
ARTEMIS_READ_OPS = {"browse", "countMessages", "listMessagesAsJSON", "listScheduledMessagesAsJSON",
                    "getAddressSettingsAsJSON", "listAddresses", "listQueues"}
NEVER = re.compile(r"purge|remove|move|retry|copy|send|pause|resume|delete|destroy|reset|stop|restart|create|"
                   r"kill|close|drop|disable|enable|force|update|set[A-Z]", re.I)


class JolokiaClient:
    def __init__(self, url: str, *, username: str | None = None, password: str | None = None, verify: Any = True,
                 timeout: float = 15.0, transport: Any = None, check: Any = None) -> None:
        self.url = url.rstrip("/")
        self.check = check  # ScopeGuard.check (the broker's management URL must be an authorized target)
        kw: dict[str, Any] = {"timeout": timeout, "verify": verify, "trust_env": False,
                              # Jolokia's CORS / CSRF protection on Artemis and Classic wants an Origin header
                              "headers": {"Origin": self._origin(url), "Content-Type": "application/json"}}
        if username:
            kw["auth"] = (username, password or "")
        if transport is not None:
            kw["transport"] = transport
        self.http = httpx.Client(**kw)

    @staticmethod
    def _origin(url: str) -> str:
        from urllib.parse import urlsplit

        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}"

    def close(self) -> None:
        self.http.close()

    def request(self, payload: dict[str, Any]) -> Any:
        t = payload.get("type")
        if t not in ("read", "search", "version", "list", "exec"):
            raise QAError("AMQ_OPERATION_REFUSED", "Only read-only management requests are allowed.", status=403)
        if self.check is not None:
            self.check(self.url, "GET")  # read-only request, sent as POST because MBean names are long
        try:
            r = self.http.post(self.url, content=json.dumps(payload))
        except httpx.HTTPError as exc:
            raise QAError("AMQ_MANAGEMENT_UNAVAILABLE", f"The broker management API at {self.url} is not answering "
                          f"({type(exc).__name__}).", status=502, details=str(exc)[:200]) from exc
        if r.status_code in (401, 403):
            raise QAError("AMQ_MANAGEMENT_AUTH", f"The broker management API refused the credentials (HTTP "
                          f"{r.status_code}).", status=502)
        if r.status_code >= 400:
            raise QAError("AMQ_MANAGEMENT_ERROR", f"Management API HTTP {r.status_code}.", status=502,
                          details=r.text[:300])
        try:
            data = r.json()
        except ValueError as exc:
            raise QAError("AMQ_MANAGEMENT_ERROR", "The management API did not answer JSON (is this the Jolokia URL?).",
                          status=502) from exc
        if isinstance(data, dict) and data.get("status", 200) != 200:
            raise QAError("AMQ_MANAGEMENT_ERROR", f"Jolokia: {str(data.get('error') or 'error')[:200]}", status=502,
                          details={"error_type": data.get("error_type")})
        return data.get("value") if isinstance(data, dict) else data

    def version(self) -> dict[str, Any]:
        return self.request({"type": "version"}) or {}

    def read(self, mbean: str, attribute: str | list[str] | None = None) -> Any:
        p: dict[str, Any] = {"type": "read", "mbean": mbean}
        if attribute:
            p["attribute"] = attribute
        return self.request(p)

    def search(self, pattern: str) -> list[str]:
        return list(self.request({"type": "search", "mbean": pattern}) or [])

    def exec_read_only(self, mbean: str, operation: str, args: list[Any], allowed: set[str]) -> Any:
        name = operation.split("(", 1)[0]
        if name not in allowed or NEVER.search(name):
            raise QAError("AMQ_OPERATION_REFUSED", f"The management operation '{name}' is not a read-only operation "
                          "and is never called.", status=403)
        return self.request({"type": "exec", "mbean": mbean, "operation": operation, "arguments": args})


def _props(m: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("properties", "StringProperties", "IntProperties", "LongProperties", "BooleanProperties",
                "DoubleProperties", "FloatProperties", "ByteProperties", "ShortProperties"):
        v = m.get(key)
        if isinstance(v, dict):
            for k, x in v.items():
                out[k] = x.get("value") if isinstance(x, dict) and "value" in x else x
    return out


def _body(v: Any) -> Any:
    """Classic shows a BytesMessage body as signed bytes; show it as text when it is UTF-8."""
    if isinstance(v, list) and v and all(isinstance(x, int) for x in v[:64]):
        try:
            return bytes((x + 256) % 256 for x in v).decode("utf-8")
        except UnicodeDecodeError:
            return f"<{len(v)} bytes>"
    return v


class ClassicAdapter:
    """ActiveMQ Classic 5.x / 6.x (org.apache.activemq MBeans, Jolokia at /api/jolokia on the web console)."""

    flavor = "classic"

    def __init__(self, j: JolokiaClient) -> None:
        self.j = j
        self._broker: str | None = None

    def broker_mbean(self) -> str:
        if self._broker is None:
            names = self.j.search("org.apache.activemq:type=Broker,brokerName=*")
            names = [n for n in names if n.count(",") == 1]
            if not names:
                raise QAError("AMQ_NOT_CLASSIC", "No ActiveMQ Classic broker MBean was found.", status=502)
            self._broker = names[0]
        return self._broker

    def info(self) -> dict[str, Any]:
        b = self.j.read(self.broker_mbean(), ["BrokerName", "BrokerVersion", "BrokerId", "Uptime", "MemoryPercentUsage",
                                              "StorePercentUsage", "TempPercentUsage", "TotalConnectionsCount",
                                              "TotalConsumerCount", "TotalProducerCount", "TotalEnqueueCount",
                                              "TotalDequeueCount", "TotalMessageCount", "TransportConnectors"]) or {}
        return {"flavor": self.flavor, "name": b.get("BrokerName"), "version": b.get("BrokerVersion"),
                "uptime": b.get("Uptime"), "memory_pct": b.get("MemoryPercentUsage"), "store_pct": b.get("StorePercentUsage"),
                "temp_pct": b.get("TempPercentUsage"), "connections": b.get("TotalConnectionsCount"),
                "consumers": b.get("TotalConsumerCount"), "producers": b.get("TotalProducerCount"),
                "enqueued": b.get("TotalEnqueueCount"), "dequeued": b.get("TotalDequeueCount"),
                "messages": b.get("TotalMessageCount"), "connectors": b.get("TransportConnectors")}

    def _dest_mbean(self, name: str, kind: str = "Queue") -> str:
        if not re.match(r"^[A-Za-z0-9_./$>#-]{1,200}$", name):
            raise QAError("VALIDATION_FAILED", "Invalid destination name.", status=422)
        return f"{self.broker_mbean()},destinationType={kind},destinationName={name}"

    def destinations(self) -> list[dict[str, Any]]:
        out = []
        for kind in ("Queue", "Topic"):
            for mb in self.j.search(f"{self.broker_mbean()},destinationType={kind},destinationName=*"):
                name = mb.split("destinationName=", 1)[1].split(",", 1)[0]
                if name.startswith("ActiveMQ.Advisory"):
                    continue
                out.append({"name": name, "type": kind.lower(), **self.stats(name, kind.lower())})
        return out

    def stats(self, name: str, kind: str = "queue") -> dict[str, Any]:
        a = self.j.read(self._dest_mbean(name, kind.title()), ["QueueSize", "EnqueueCount", "DequeueCount",
                                                              "ConsumerCount", "ProducerCount", "ExpiredCount",
                                                              "InFlightCount", "DispatchCount", "MemoryPercentUsage",
                                                              "AverageEnqueueTime", "PrioritizedMessages"]) or {}
        return {"depth": a.get("QueueSize"), "enqueued": a.get("EnqueueCount"), "dequeued": a.get("DequeueCount"),
                "consumers": a.get("ConsumerCount"), "producers": a.get("ProducerCount"), "expired": a.get("ExpiredCount"),
                "in_flight": a.get("InFlightCount"), "dispatched": a.get("DispatchCount"),
                "memory_pct": a.get("MemoryPercentUsage"), "avg_enqueue_ms": a.get("AverageEnqueueTime"),
                "prioritized": a.get("PrioritizedMessages")}

    def browse(self, name: str, selector: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        mb = self._dest_mbean(name)
        if selector:
            raw = self.j.exec_read_only(mb, "browse(java.lang.String)", [selector], CLASSIC_READ_OPS)
        else:
            raw = self.j.exec_read_only(mb, "browse()", [], CLASSIC_READ_OPS)
        out = []
        for m in (raw or [])[:limit]:
            props = _props(m)
            out.append({"id": m.get("JMSMessageID"), "correlation_id": m.get("JMSCorrelationID"),
                        "timestamp": m.get("JMSTimestamp"), "priority": m.get("JMSPriority"),
                        "expiration": m.get("JMSExpiration"), "redelivered": m.get("JMSRedelivered"),
                        "delivery_count": props.get("JMSXDeliveryCount") or m.get("JMSXDeliveryCount"),
                        "type": m.get("JMSType"), "properties": props,
                        "body": m.get("Text") if m.get("Text") is not None else _body(m.get("BodyPreview")),
                        "dlq_cause": props.get("dlqDeliveryFailureCause"),
                        "original_destination": m.get("OriginalDestination")})
        return out

    def dlq_for(self, name: str, configured: str | None = None) -> str:
        return configured or "ActiveMQ.DLQ"

    def redelivery(self, name: str) -> dict[str, Any]:
        return {"note": "Classic redelivery policy is set on the client / broker plugin and is not readable over JMX."}


class ArtemisAdapter:
    """ActiveMQ Artemis / AMQ Broker 7 (org.apache.activemq.artemis MBeans, Jolokia at /console/jolokia)."""

    flavor = "artemis"

    def __init__(self, j: JolokiaClient) -> None:
        self.j = j
        self._broker: str | None = None

    def broker_mbean(self) -> str:
        if self._broker is None:
            names = [n for n in self.j.search("org.apache.activemq.artemis:broker=*") if n.count(",") == 0]
            if not names:
                raise QAError("AMQ_NOT_ARTEMIS", "No ActiveMQ Artemis broker MBean was found.", status=502)
            self._broker = names[0]
        return self._broker

    def info(self) -> dict[str, Any]:
        b = self.j.read(self.broker_mbean(), ["Name", "Version", "Uptime", "AddressMemoryUsagePercentage",
                                              "DiskStoreUsage", "ConnectionCount", "TotalConsumerCount",
                                              "TotalMessagesAdded", "TotalMessagesAcknowledged", "TotalMessageCount",
                                              "Clustered", "Backup"]) or {}
        return {"flavor": self.flavor, "name": b.get("Name"), "version": b.get("Version"), "uptime": b.get("Uptime"),
                "memory_pct": b.get("AddressMemoryUsagePercentage"), "store_pct": b.get("DiskStoreUsage"),
                "connections": b.get("ConnectionCount"), "consumers": b.get("TotalConsumerCount"),
                "enqueued": b.get("TotalMessagesAdded"), "dequeued": b.get("TotalMessagesAcknowledged"),
                "messages": b.get("TotalMessageCount"), "clustered": b.get("Clustered"), "backup": b.get("Backup")}

    def _queue_mbeans(self) -> list[str]:
        return self.j.search(f"{self.broker_mbean()},component=addresses,address=*,subcomponent=queues,"
                             "routing-type=*,queue=*")

    @staticmethod
    def _field(mb: str, key: str) -> str:
        m = re.search(rf'{key}="?([^",]+)"?', mb)
        return m.group(1) if m else ""

    def _queue_mbean(self, name: str) -> str:
        if not re.match(r"^[A-Za-z0-9_./$>#-]{1,200}$", name):
            raise QAError("VALIDATION_FAILED", "Invalid destination name.", status=422)
        for mb in self._queue_mbeans():
            if self._field(mb, "queue") == name:
                return mb
        raise QAError("AMQ_DESTINATION_NOT_FOUND", f"Queue {name} does not exist on the broker.", status=404)

    def destinations(self) -> list[dict[str, Any]]:
        out = []
        for mb in self._queue_mbeans():
            q = self._field(mb, "queue")
            if q.startswith(("$sys", "activemq.management", "activemq.notifications")):
                continue
            rt = self._field(mb, "routing-type")
            out.append({"name": q, "address": self._field(mb, "address"),
                        "type": "queue" if rt == "anycast" else "topic-subscription", "routing_type": rt,
                        **self._stats(mb)})
        return out

    def _stats(self, mb: str) -> dict[str, Any]:
        a = self.j.read(mb, ["MessageCount", "MessagesAdded", "MessagesAcknowledged", "ConsumerCount",
                             "DeliveringCount", "MessagesExpired", "MessagesKilled", "DeadLetterAddress",
                             "ExpiryAddress", "Durable", "Paused", "ScheduledCount"]) or {}
        return {"depth": a.get("MessageCount"), "enqueued": a.get("MessagesAdded"),
                "dequeued": a.get("MessagesAcknowledged"), "consumers": a.get("ConsumerCount"),
                "in_flight": a.get("DeliveringCount"), "expired": a.get("MessagesExpired"), "killed": a.get("MessagesKilled"),
                "dead_letter_address": a.get("DeadLetterAddress"), "expiry_address": a.get("ExpiryAddress"),
                "durable": a.get("Durable"), "paused": a.get("Paused"), "scheduled": a.get("ScheduledCount")}

    def stats(self, name: str, kind: str = "queue") -> dict[str, Any]:
        return self._stats(self._queue_mbean(name))

    def browse(self, name: str, selector: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        mb = self._queue_mbean(name)
        raw = self.j.exec_read_only(mb, "browse(java.lang.String)", [selector or ""], ARTEMIS_READ_OPS)
        out = []
        for m in (raw or [])[:limit]:
            props = _props(m)
            internal = {k: v for k, v in props.items() if k.startswith("_AMQ")}
            user = {k: v for k, v in props.items() if not k.startswith(("_AMQ", "JMS")) and k not in
                    ("destination", "destination-type", "receipt")}
            body = m.get("text")
            if body is None and m.get("BodyPreview") is not None:
                body = _body(m.get("BodyPreview"))
            out.append({"id": m.get("messageID") or m.get("userID"),
                        "correlation_id": props.get("JMSCorrelationID") or m.get("correlationID"),
                        "timestamp": m.get("timestamp"), "priority": m.get("priority"), "expiration": m.get("expiration"),
                        "redelivered": m.get("redelivered"), "delivery_count": m.get("deliveryCount"),
                        "properties": user, "body": body,
                        "dlq_cause": f"moved from {internal['_AMQ_ORIG_QUEUE']}" if internal.get("_AMQ_ORIG_QUEUE") else None,
                        "original_destination": internal.get("_AMQ_ORIG_ADDRESS")})
        return out

    def address_settings(self, address: str) -> dict[str, Any]:
        try:
            raw = self.j.exec_read_only(self.broker_mbean(), "getAddressSettingsAsJSON(java.lang.String)", [address],
                                        ARTEMIS_READ_OPS)
            return json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (QAError, ValueError):
            return {}

    def dlq_for(self, name: str, configured: str | None = None) -> str:
        if configured:
            return configured
        try:
            return str(self.stats(name).get("dead_letter_address") or "DLQ")
        except QAError:
            return "DLQ"

    def redelivery(self, name: str) -> dict[str, Any]:
        mb = self._queue_mbean(name)
        s = self.address_settings(self._field(mb, "address"))
        return {k: s.get(k) for k in ("maxDeliveryAttempts", "redeliveryDelay", "redeliveryMultiplier", "DLA",
                                      "deadLetterAddress", "expiryAddress") if k in s}


def detect(j: JolokiaClient, flavor: str = "auto") -> Any:
    """The adapter for this broker: by configuration, otherwise by the MBeans the broker has."""
    if flavor == "classic":
        return ClassicAdapter(j)
    if flavor == "artemis":
        return ArtemisAdapter(j)
    if j.search("org.apache.activemq.artemis:broker=*"):
        return ArtemisAdapter(j)
    if j.search("org.apache.activemq:type=Broker,brokerName=*"):
        return ClassicAdapter(j)
    raise QAError("AMQ_UNKNOWN_BROKER", "Neither an ActiveMQ Classic nor an Artemis broker was found at this "
                  "management URL.", status=502)


__all__ = ["JolokiaClient", "ClassicAdapter", "ArtemisAdapter", "detect", "CLASSIC_READ_OPS", "ARTEMIS_READ_OPS"]
