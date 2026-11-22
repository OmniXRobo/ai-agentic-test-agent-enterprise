"""23.14.0: Integration Testing: Red Hat Fuse (/api/fuse/...) and ActiveMQ (/api/activemq/...).

View-only accounts can read; changes and runs need Admin or Tester (refused centrally and again in the services).
Credentials are accepted only by the credential endpoints and never returned. Broker and route management access is
read-only by construction (see ``amqtest.jolokia`` and ``fusetest.adapters``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..errors import QAError
from ..runtime import Runtime
from .deps import runtime

router = APIRouter()


def amq(rt: Runtime = Depends(runtime)) -> Any:
    s = getattr(rt, "activemq", None)
    if s is None:
        raise QAError("ACTIVEMQ_UNAVAILABLE", "ActiveMQ Testing did not start on this PC (see the application log).",
                      status=503)
    return s


def fuse(rt: Runtime = Depends(runtime)) -> Any:
    s = getattr(rt, "fuse", None)
    if s is None:
        raise QAError("FUSE_UNAVAILABLE", "Red Hat Fuse Testing did not start on this PC (see the application log).",
                      status=503)
    return s


class TargetIn(BaseModel):
    name: str = Field(max_length=120)
    environment: str = Field(default="Test", max_length=20)
    config: dict[str, Any] = {}


class SecretIn(BaseModel):
    name: str = Field(max_length=60)
    value: str | None = Field(default=None, max_length=20000)


class StartIn(BaseModel):
    kind: str = Field(max_length=20)
    options: dict[str, Any] = {}


class ControlIn(BaseModel):
    action: str = Field(max_length=10)


class CompareIn(BaseModel):
    expected: Any = None
    actual: Any = None
    format: str = Field(default="auto", max_length=10)
    rules: dict[str, Any] = {}


# ---- ActiveMQ ---------------------------------------------------------------------------------------------------------
@router.get("/api/activemq/meta")
def amq_meta() -> dict[str, Any]:
    from ..amqtest.service import CASE_TYPES
    from ..itest.common import ENVIRONMENTS

    return {"ok": True, "case_types": CASE_TYPES, "environments": ENVIRONMENTS, "flavors": ["auto", "classic", "artemis"]}


@router.get("/api/activemq/brokers")
def amq_brokers(s: Any = Depends(amq)) -> dict[str, Any]:
    return {"ok": True, "brokers": s.brokers()}


@router.post("/api/activemq/brokers", status_code=201)
def amq_create(body: TargetIn, s: Any = Depends(amq)) -> dict[str, Any]:
    return {"ok": True, "broker": s.save_broker(body.model_dump())}


@router.get("/api/activemq/brokers/{bid}")
def amq_get(bid: str, s: Any = Depends(amq)) -> dict[str, Any]:
    return {"ok": True, "broker": s.broker(bid)}


@router.put("/api/activemq/brokers/{bid}")
def amq_update(bid: str, body: TargetIn, s: Any = Depends(amq)) -> dict[str, Any]:
    return {"ok": True, "broker": s.save_broker(body.model_dump(), bid)}


@router.delete("/api/activemq/brokers/{bid}")
def amq_delete(bid: str, s: Any = Depends(amq)) -> dict[str, Any]:
    s.delete_broker(bid)
    return {"ok": True}


@router.post("/api/activemq/brokers/{bid}/credentials")
def amq_secret(bid: str, body: SecretIn, s: Any = Depends(amq)) -> dict[str, Any]:
    return {"ok": True, "broker": s.set_secret(bid, body.name, body.value)}


@router.post("/api/activemq/brokers/{bid}/test-connection")
def amq_test(bid: str, s: Any = Depends(amq)) -> dict[str, Any]:
    from ..itest.common import actor

    actor("test broker connections")
    return {"ok": True, **s.test_connection(bid)}


@router.get("/api/activemq/brokers/{bid}/monitor")
def amq_monitor(bid: str, s: Any = Depends(amq)) -> dict[str, Any]:
    return {"ok": True, **s.monitor(bid)}


@router.get("/api/activemq/brokers/{bid}/browse")
def amq_browse(bid: str, destination: str, selector: str | None = None, limit: int = 20,
               s: Any = Depends(amq)) -> dict[str, Any]:
    return {"ok": True, **s.browse(bid, destination, selector, limit)}


@router.post("/api/activemq/brokers/{bid}/cases", status_code=201)
def amq_case(bid: str, body: dict[str, Any], s: Any = Depends(amq)) -> dict[str, Any]:
    return {"ok": True, "case": s.save_case(bid, body)}


@router.put("/api/activemq/brokers/{bid}/cases/{case_id}")
def amq_case_update(bid: str, case_id: str, body: dict[str, Any], s: Any = Depends(amq)) -> dict[str, Any]:
    return {"ok": True, "case": s.save_case(bid, body, case_id)}


@router.post("/api/activemq/brokers/{bid}/cases/draft")
def amq_draft(bid: str, s: Any = Depends(amq)) -> dict[str, Any]:
    return {"ok": True, "cases": s.draft_cases(bid)}


@router.post("/api/activemq/brokers/{bid}/runs", status_code=202)
def amq_start(bid: str, body: StartIn, s: Any = Depends(amq)) -> dict[str, Any]:
    return {"ok": True, "run": s.start(bid, body.kind, body.options)}


@router.post("/api/activemq/runs/{run_id}/control")
def amq_control(run_id: str, body: ControlIn, s: Any = Depends(amq)) -> dict[str, Any]:
    return {"ok": True, "run": s.control(run_id, body.action)}


@router.get("/api/activemq/dashboard")
def amq_dashboard(s: Any = Depends(amq)) -> dict[str, Any]:
    return {"ok": True, **s.dashboard()}


# ---- Red Hat Fuse -------------------------------------------------------------------------------------------------------
@router.get("/api/fuse/meta")
def fuse_meta() -> dict[str, Any]:
    from ..fusetest.service import CASE_TYPES, RUNTIMES
    from ..itest.common import ENVIRONMENTS

    return {"ok": True, "case_types": CASE_TYPES, "runtimes": RUNTIMES, "environments": ENVIRONMENTS}


@router.get("/api/fuse/connections")
def fuse_list(s: Any = Depends(fuse)) -> dict[str, Any]:
    return {"ok": True, "connections": s.connections()}


@router.post("/api/fuse/connections", status_code=201)
def fuse_create(body: TargetIn, s: Any = Depends(fuse)) -> dict[str, Any]:
    return {"ok": True, "connection": s.save_connection(body.model_dump())}


@router.get("/api/fuse/connections/{cid}")
def fuse_get(cid: str, s: Any = Depends(fuse)) -> dict[str, Any]:
    return {"ok": True, "connection": s.connection(cid)}


@router.put("/api/fuse/connections/{cid}")
def fuse_update(cid: str, body: TargetIn, s: Any = Depends(fuse)) -> dict[str, Any]:
    return {"ok": True, "connection": s.save_connection(body.model_dump(), cid)}


@router.delete("/api/fuse/connections/{cid}")
def fuse_delete(cid: str, s: Any = Depends(fuse)) -> dict[str, Any]:
    s.delete_connection(cid)
    return {"ok": True}


@router.post("/api/fuse/connections/{cid}/credentials")
def fuse_secret(cid: str, body: SecretIn, s: Any = Depends(fuse)) -> dict[str, Any]:
    return {"ok": True, "connection": s.set_secret(cid, body.name, body.value)}


@router.post("/api/fuse/connections/{cid}/test-connection")
def fuse_test(cid: str, s: Any = Depends(fuse)) -> dict[str, Any]:
    from ..itest.common import actor

    actor("test Fuse connections")
    return {"ok": True, **s.test_connection(cid)}


@router.get("/api/fuse/connections/{cid}/routes")
def fuse_routes(cid: str, s: Any = Depends(fuse)) -> dict[str, Any]:
    return {"ok": True, **s.routes(cid)}


@router.get("/api/fuse/connections/{cid}/routes/{route_id}")
def fuse_route(cid: str, route_id: str, s: Any = Depends(fuse)) -> dict[str, Any]:
    return {"ok": True, "route": s.route(cid, route_id)}


@router.get("/api/fuse/connections/{cid}/monitor")
def fuse_monitor(cid: str, s: Any = Depends(fuse)) -> dict[str, Any]:
    return {"ok": True, **s.monitor(cid)}


@router.post("/api/fuse/connections/{cid}/cases", status_code=201)
def fuse_case(cid: str, body: dict[str, Any], s: Any = Depends(fuse)) -> dict[str, Any]:
    return {"ok": True, "case": s.save_case(cid, body)}


@router.put("/api/fuse/connections/{cid}/cases/{case_id}")
def fuse_case_update(cid: str, case_id: str, body: dict[str, Any], s: Any = Depends(fuse)) -> dict[str, Any]:
    return {"ok": True, "case": s.save_case(cid, body, case_id)}


@router.post("/api/fuse/connections/{cid}/cases/draft")
def fuse_draft(cid: str, body: dict[str, Any] | None = None, s: Any = Depends(fuse)) -> dict[str, Any]:
    return {"ok": True, "cases": s.draft_cases(cid, (body or {}).get("route_ids"))}


@router.post("/api/fuse/connections/{cid}/runs", status_code=202)
def fuse_start(cid: str, body: StartIn, s: Any = Depends(fuse)) -> dict[str, Any]:
    return {"ok": True, "run": s.start(cid, body.kind, body.options)}


@router.post("/api/fuse/runs/{run_id}/control")
def fuse_control(run_id: str, body: ControlIn, s: Any = Depends(fuse)) -> dict[str, Any]:
    return {"ok": True, "run": s.control(run_id, body.action)}


@router.post("/api/fuse/compare")
def fuse_compare(body: CompareIn) -> dict[str, Any]:
    """Transformation Comparison: compares an expected and an actual message (JSON, XML or text) by the rules given;
    nothing is sent anywhere."""
    from ..fusetest import compare

    return {"ok": True, **compare.compare(body.expected, body.actual, fmt=body.format, rules=body.rules)}


@router.get("/api/fuse/dashboard")
def fuse_dashboard(s: Any = Depends(fuse)) -> dict[str, Any]:
    return {"ok": True, **s.dashboard()}
