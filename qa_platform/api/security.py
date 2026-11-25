"""23.14.0: AI Security & Vulnerability Testing: /api/security/...

View-only accounts can read; changes, assessments and reviews need Admin or Tester (refused centrally by the sign-in
check and again in the service). Credentials are accepted only by the credential endpoint and are never returned.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..errors import QAError
from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/security")


def svc(rt: Runtime = Depends(runtime)) -> Any:
    s = getattr(rt, "security", None)
    if s is None:
        raise QAError("SECURITY_UNAVAILABLE", "Security Testing did not start on this PC (see the application log).",
                      status=503)
    return s


class AppIn(BaseModel):
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


class ReviewIn(BaseModel):
    status: str = Field(max_length=20)
    note: str = Field(default="", max_length=1000)


@router.get("/meta")
def meta() -> dict[str, Any]:
    from ..itest.common import ENVIRONMENTS
    from ..sectest import active, checks, service
    from ..sectest.client import AUTH_TYPES

    return {"ok": True, "categories": active.CATEGORIES, "state_changing": sorted(active.NEEDS_STATE_CHANGE),
            "intensity": service.INTENSITY, "kinds": [k for k in service.KINDS if k != "retest"],
            "environments": ENVIRONMENTS, "auth_types": AUTH_TYPES, "finding_statuses": service.FINDING_STATUSES,
            "rules": {k: {"title": v[0], "category": v[1], "owasp": v[2], "severity": v[3]}
                      for k, v in checks.RULES.items()}}


@router.get("/apps")
def apps(s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "apps": s.apps()}


@router.post("/apps", status_code=201)
def create_app(body: AppIn, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "app": s.save_app(body.model_dump())}


@router.get("/apps/{app_id}")
def get_app(app_id: str, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "app": s.app(app_id)}


@router.put("/apps/{app_id}")
def update_app(app_id: str, body: AppIn, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "app": s.save_app(body.model_dump(), app_id)}


@router.delete("/apps/{app_id}")
def delete_app(app_id: str, s: Any = Depends(svc)) -> dict[str, Any]:
    s.delete_app(app_id)
    return {"ok": True}


@router.post("/apps/{app_id}/credentials")
def set_secret(app_id: str, body: SecretIn, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "app": s.set_secret(app_id, body.name, body.value)}


@router.post("/apps/{app_id}/runs", status_code=202)
def start(app_id: str, body: StartIn, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "run": s.start(app_id, body.kind, body.options)}


@router.post("/runs/{run_id}/control")
def control(run_id: str, body: ControlIn, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "run": s.control(run_id, body.action)}


@router.get("/apps/{app_id}/maps")
def maps(app_id: str, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "maps": s.maps(app_id)}


@router.get("/apps/{app_id}/maps/{map_id}")
def get_map(app_id: str, map_id: int, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "map": s.get_map(app_id, map_id)}


@router.post("/apps/{app_id}/maps/{map_id}/approve")
def approve_map(app_id: str, map_id: int, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "map": s.approve_map(app_id, map_id)}


@router.post("/apps/{app_id}/scenarios/draft")
def draft_scenarios(app_id: str, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "cases": s.draft_scenarios(app_id)}


@router.post("/apps/{app_id}/scenarios", status_code=201)
def save_scenario(app_id: str, body: dict[str, Any], s: Any = Depends(svc)) -> dict[str, Any]:
    from ..itest.common import actor

    actor("save security scenarios")
    return {"ok": True, "case": s.save_scenario(app_id, body)}


@router.put("/apps/{app_id}/scenarios/{case_id}")
def update_scenario(app_id: str, case_id: str, body: dict[str, Any], s: Any = Depends(svc)) -> dict[str, Any]:
    from ..itest.common import actor

    actor("change security scenarios")
    return {"ok": True, "case": s.save_scenario(app_id, body, case_id)}


@router.get("/findings")
def findings(app_id: str | None = None, status: str | None = None, severity: str | None = None,
             s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "findings": s.findings(app_id, status=status, severity=severity), "counts": s.counts(app_id)}


@router.get("/findings/{fid}")
def finding(fid: str, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "finding": s.finding(fid)}


@router.post("/findings/{fid}/review")
def review(fid: str, body: ReviewIn, s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, "finding": s.review(fid, body.status, body.note)}


@router.get("/dashboard")
def dashboard(s: Any = Depends(svc)) -> dict[str, Any]:
    return {"ok": True, **s.dashboard()}
