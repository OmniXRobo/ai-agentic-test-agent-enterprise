"""Route Mapping: turns a Camel route definition (XML DSL, as ``dumpRouteAsXml`` returns it) into a map of its entry
points, destinations, content-based routing, transformations and error handling. Parsed without DTDs or entities."""

from __future__ import annotations

import re
from typing import Any

from ..errors import QAError

SEND = {"to", "toD", "wireTap", "inOnly", "inOut", "enrich", "pollEnrich", "recipientList", "routingSlip",
        "dynamicRouter"}
TRANSFORM = {"marshal", "unmarshal", "transform", "setBody", "convertBodyTo", "bean", "process", "setHeader",
             "removeHeader", "removeHeaders", "setProperty", "xslt", "jolt", "velocity", "freemarker", "mustache",
             "script"}
FLOW = {"split", "aggregate", "multicast", "filter", "loadBalance", "throttle", "delay", "idempotentConsumer",
        "resequence", "loop"}


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _expr(el: Any) -> str | None:
    for c in el:
        name = _local(c.tag)
        if name in ("simple", "xpath", "jsonpath", "header", "constant", "groovy", "ognl", "method", "spel", "jq",
                    "exchangeProperty", "tokenize", "language"):
            return f"{name}: {(c.text or c.get('ref') or c.get('method') or '').strip()}"[:300]
    return None


def parse_route_xml(xml_text: str) -> list[dict[str, Any]]:
    if not xml_text:
        return []
    if re.search(r"<!DOCTYPE|<!ENTITY", xml_text, re.I):
        raise QAError("FUSE_ROUTE_XML", "Route XML with a DTD or entities is not parsed.", status=422)
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as exc:
        raise QAError("FUSE_ROUTE_XML", f"The route definition is not well-formed XML ({exc}).", status=422) from exc
    routes = [root] if _local(root.tag) == "route" else [el for el in root.iter() if _local(el.tag) == "route"]
    return [_route(r) for r in routes]


def _route(r: Any) -> dict[str, Any]:
    m: dict[str, Any] = {"id": r.get("id"), "from": [], "to": [], "choices": [], "transformations": [], "flow": [],
                         "error_handling": {"on_exception": [], "dead_letter": None, "redelivery": None,
                                            "do_try": False, "error_handler_ref": r.get("errorHandlerRef")},
                         "description": None, "steps": 0}

    def walk(el: Any, ctx: str | None) -> None:
        for c in el:
            name = _local(c.tag)
            m["steps"] += 1
            if name == "description":
                m["description"] = (c.text or "").strip()[:300]
            elif name == "from":
                m["from"].append(c.get("uri") or c.get("ref"))
            elif name in SEND:
                uri = c.get("uri") or _expr(c) or c.get("ref")
                m["to"].append({"uri": uri, "kind": name, "when": ctx})
            elif name == "choice":
                for w in c:
                    wn = _local(w.tag)
                    if wn == "when":
                        cond = _expr(w) or "?"
                        targets = [x.get("uri") for x in w.iter() if _local(x.tag) in SEND and x.get("uri")]
                        m["choices"].append({"when": cond, "to": targets})
                        walk(w, cond)
                    elif wn == "otherwise":
                        targets = [x.get("uri") for x in w.iter() if _local(x.tag) in SEND and x.get("uri")]
                        m["choices"].append({"when": "otherwise", "to": targets})
                        walk(w, "otherwise")
            elif name in TRANSFORM:
                detail = c.get("uri") or c.get("ref") or c.get("beanType") or c.get("method") or _expr(c)
                if not detail:
                    first = next(iter(c), None)
                    detail = _local(first.tag) if first is not None else None
                if name in ("setHeader", "setProperty"):
                    detail = f"{c.get('name') or c.get('headerName') or c.get('propertyName')} = {_expr(c) or ''}"
                m["transformations"].append({"kind": name, "detail": str(detail or "")[:300], "when": ctx})
            elif name in FLOW:
                m["flow"].append({"kind": name, "detail": _expr(c) or c.get("strategyRef") or "", "when": ctx})
                walk(c, ctx)
            elif name == "onException":
                exc = [(x.text or "").strip() for x in c if _local(x.tag) == "exception"]
                red = next((x.attrib for x in c if _local(x.tag) == "redeliveryPolicy"), None)
                handled = next((_expr(x) for x in c if _local(x.tag) == "handled"), None)
                targets = [x.get("uri") for x in c.iter() if _local(x.tag) in SEND and x.get("uri")]
                m["error_handling"]["on_exception"].append({"exceptions": exc, "redelivery": dict(red) if red else None,
                                                            "handled": handled, "to": targets})
            elif name == "doTry":
                m["error_handling"]["do_try"] = True
                walk(c, ctx)
            elif name in ("errorHandler", "deadLetterChannel"):
                m["error_handling"]["dead_letter"] = c.get("deadLetterUri")
                red = next((x.attrib for x in c if _local(x.tag) == "redeliveryPolicy"), None)
                if red:
                    m["error_handling"]["redelivery"] = dict(red)
            else:
                walk(c, ctx)

    walk(r, None)
    eh = m["error_handling"]
    eh["has_error_handling"] = bool(eh["on_exception"] or eh["dead_letter"] or eh["do_try"] or eh["error_handler_ref"])
    m["endpoints"] = sorted({u for u in m["from"] if u} | {t["uri"] for t in m["to"] if t.get("uri")})
    return m


def queue_of(uri: str | None) -> tuple[str, str] | None:
    """('queue' | 'topic', name) for jms: / activemq: / amq: / sjms: / amqp: endpoint URIs."""
    if not uri:
        return None
    m = re.match(r"^(?:jms|activemq|amq|sjms2?|amqp):(?://)?(?:(queue|topic):)?([^?]+)", uri)
    if not m:
        return None
    return (m.group(1) or "queue", m.group(2))


def http_of(uri: str | None) -> str | None:
    if not uri:
        return None
    m = re.match(r"^(?:jetty:|netty-http:|undertow:|platform-http:|servlet:|rest:\w+:|cxfrs:|cxf:)?(https?://[^?]+)", uri)
    return m.group(1) if m else None


__all__ = ["parse_route_xml", "queue_of", "http_of"]
