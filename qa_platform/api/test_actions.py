"""23.14.0: the unified result actions: /api/test-actions/{kind}/{id}/...

``kind`` is ``result`` (Security / Fuse / ActiveMQ result), ``finding`` (security finding), ``run`` (suite summary) or
``exec`` (a run in Execution History). Analyze with AI, Create TFS Defect (preview first, a Defect Review draft
after), Generate PDF Report, Email Developer (draft, edit, send only on confirmation), View Evidence and Retest.
Also the shared run / result / test-case / audit endpoints of the three new modules (/api/itest/...).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from ..errors import QAError
from ..runtime import Runtime
from .deps import runtime

router = APIRouter()
ID = r"^[A-Za-z0-9_.:-]{1,80}$"


def actions(rt: Runtime = Depends(runtime)) -> Any:
    s = getattr(rt, "test_actions", None)
    if s is None:
        raise QAError("ITEST_UNAVAILABLE", "The result actions did not start on this PC (see the application log).",
                      status=503)
    return s


def store(rt: Runtime = Depends(runtime)) -> Any:
    s = getattr(rt, "itest", None)
    if s is None:
        raise QAError("ITEST_UNAVAILABLE", "Integration testing did not start on this PC (see the application log).",
                      status=503)
    return s


class DefectIn(BaseModel):
    # 23.15.0: the page sends severity as a number (1-4); 23.14.x declared it a string, so pydantic refused every
    # "Create draft" with "Request validation failed". Numbers and the TFS labels ("2 - High", "high") are accepted.
    title: str | None = Field(default=None, max_length=250)
    severity: int | str | None = Field(default=None)
    priority: int | str | None = Field(default=None)
    repro_steps: str | None = Field(default=None, max_length=60000)
    repro_text: str | None = Field(default=None, max_length=60000)  # 23.15.0: the readable text the person edited
    attach: bool = True  # 23.15.0: attach the screenshots and result files to the TFS bug
    assigned_to: str | None = Field(default=None, max_length=256)
    send_now: bool = False  # approve and send in one action (needs both permissions; never past the duplicate check)

    @field_validator("severity", "priority", mode="before")
    @classmethod
    def _level(cls, v: Any) -> int | None:
        if v in (None, ""):
            return None
        if isinstance(v, bool):
            raise ValueError("Choose a level from 1 to 4.")
        if isinstance(v, (int, float)):
            n = int(v)
        else:
            text = str(v).strip().lower()[:40]
            words = {"critical": 1, "high": 2, "medium": 3, "low": 4, "info": 4}
            n = int(text[0]) if text[:1].isdigit() else next((x for k, x in words.items() if k in text), 0)
        if not 1 <= n <= 4:
            raise ValueError("Choose a level from 1 to 4.")
        return n


class EmailIn(BaseModel):
    to: list[str] | str | None = None
    cc: list[str] | str | None = None
    bcc: list[str] | str | None = None
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=60000)
    context: dict[str, Any] | None = None
    attachments: list[str] | None = Field(default=None, max_length=20)


class SendIn(BaseModel):
    confirm: bool = False
    allow_resend: bool = False


# ---- actions on one subject -----------------------------------------------------------------------------------------
@router.get("/api/test-actions/{kind}/{subject_id}/facts")
def facts(kind: str, subject_id: str, s: Any = Depends(actions)) -> dict[str, Any]:
    return {"ok": True, "facts": s.facts(kind, subject_id), "analysis": s.latest(kind, subject_id)}


@router.get("/api/test-actions/{kind}/{subject_id}/evidence")
def evidence(kind: str, subject_id: str, s: Any = Depends(actions)) -> dict[str, Any]:
    return {"ok": True, **s.evidence(kind, subject_id)}


@router.get("/api/test-actions/{kind}/{subject_id}/screenshot/{index}")
def screenshot(kind: str, subject_id: str, index: int, s: Any = Depends(actions)) -> FileResponse:
    p = s.screenshot(kind, subject_id, index)
    return FileResponse(p, media_type="image/png" if p.suffix.lower() == ".png" else "image/jpeg",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/api/test-actions/{kind}/{subject_id}/analyze")
def analyze(kind: str, subject_id: str, s: Any = Depends(actions)) -> dict[str, Any]:
    return {"ok": True, **s.analyze(kind, subject_id)}


@router.get("/api/test-actions/{kind}/{subject_id}/analyses")
def analyses(kind: str, subject_id: str, s: Any = Depends(actions)) -> dict[str, Any]:
    return {"ok": True, "history": s.history(kind, subject_id)}


@router.get("/api/test-actions/{kind}/{subject_id}/defect")
def defect_preview(kind: str, subject_id: str, s: Any = Depends(actions)) -> dict[str, Any]:
    preview = s.defect_preview(kind, subject_id)
    preview.pop("_files", None)  # local file paths stay on the server
    return {"ok": True, "preview": preview}


@router.post("/api/test-actions/{kind}/{subject_id}/defect", status_code=201)
def defect_create(kind: str, subject_id: str, body: DefectIn, s: Any = Depends(actions)) -> dict[str, Any]:
    return {"ok": True, **s.defect_create(kind, subject_id, body.model_dump(exclude_none=True))}


@router.post("/api/test-actions/{kind}/{subject_id}/pdf")
def make_pdf(kind: str, subject_id: str, type: str | None = None, s: Any = Depends(actions)) -> dict[str, Any]:
    return {"ok": True, "pdf": s.make_pdf(kind, subject_id, type)}


@router.get("/api/test-actions/{kind}/{subject_id}/pdf/{rtype}")
def get_pdf(kind: str, subject_id: str, rtype: str, download: bool = False, s: Any = Depends(actions)) -> FileResponse:
    p = s.pdf_file(kind, subject_id, rtype)
    return FileResponse(p, media_type="application/pdf", filename=p.name if download else None,
                        content_disposition_type="attachment" if download else "inline",
                        headers={"Cache-Control": "no-store"})


@router.post("/api/test-actions/{kind}/{subject_id}/email")
def email_draft(kind: str, subject_id: str, s: Any = Depends(actions)) -> dict[str, Any]:
    return {"ok": True, "draft": s.email_draft(kind, subject_id)}


@router.get("/api/test-actions/{kind}/{subject_id}/emails")
def emails(kind: str, subject_id: str, s: Any = Depends(actions)) -> dict[str, Any]:
    return {"ok": True, "emails": s.emails(kind, subject_id)}


@router.post("/api/test-actions/{kind}/{subject_id}/retest")
def retest(kind: str, subject_id: str, s: Any = Depends(actions)) -> dict[str, Any]:
    return {"ok": True, "run": s.retest(kind, subject_id)}


@router.get("/api/test-actions/email-history")
def email_history(status: str | None = None, limit: int = 200, s: Any = Depends(actions)) -> dict[str, Any]:
    return {"ok": True, "emails": s.email_history(status, min(max(limit, 1), 1000))}


# ---- email drafts ---------------------------------------------------------------------------------------------------
@router.get("/api/test-actions/email/{draft_id}")
def email_get(draft_id: str, s: Any = Depends(actions)) -> dict[str, Any]:
    return {"ok": True, "draft": s.email_get(draft_id)}


@router.put("/api/test-actions/email/{draft_id}")
def email_update(draft_id: str, body: EmailIn, s: Any = Depends(actions)) -> dict[str, Any]:
    return {"ok": True, "draft": s.email_update(draft_id, body.model_dump(exclude_none=True))}


@router.post("/api/test-actions/email/{draft_id}/send")
def email_send(draft_id: str, body: SendIn, s: Any = Depends(actions)) -> dict[str, Any]:
    return {"ok": True, "draft": s.email_send(draft_id, confirm=body.confirm, allow_resend=body.allow_resend)}


# ---- shared runs, results, test cases and audit of the new modules ----------------------------------------------------
@router.get("/api/itest/runs")
def runs(module: str, target_id: str | None = None, limit: int = 50, st: Any = Depends(store)) -> dict[str, Any]:
    return {"ok": True, "runs": st.runs(module, target_id, min(max(limit, 1), 500))}


@router.get("/api/itest/runs/{run_id}")
def run(run_id: str, st: Any = Depends(store)) -> dict[str, Any]:
    return {"ok": True, "run": st.run(run_id), "counts": st.counts(run_id)}


@router.get("/api/itest/results")
def results(module: str | None = None, run_id: str | None = None, status: str | None = None,
            category: str | None = None, limit: int = 500, st: Any = Depends(store)) -> dict[str, Any]:
    return {"ok": True, "results": st.results(module=module, run_id=run_id, status=status, category=category,
                                              limit=min(max(limit, 1), 2000))}


@router.get("/api/itest/results/{result_id}")
def result(result_id: int, st: Any = Depends(store)) -> dict[str, Any]:
    return {"ok": True, "result": st.result(result_id)}


@router.get("/api/itest/cases")
def cases(module: str, target_id: str | None = None, st: Any = Depends(store)) -> dict[str, Any]:
    return {"ok": True, "cases": st.cases(module, target_id)}


@router.post("/api/itest/cases/{case_id}/approve")
def approve_case(case_id: str, st: Any = Depends(store)) -> dict[str, Any]:
    return {"ok": True, "case": st.approve_case(case_id)}


@router.delete("/api/itest/cases/{case_id}")
def delete_case(case_id: str, st: Any = Depends(store)) -> dict[str, Any]:
    st.delete_case(case_id)
    return {"ok": True}


@router.get("/api/itest/audit")
def audit(module: str | None = None, limit: int = 200, st: Any = Depends(store)) -> dict[str, Any]:
    return {"ok": True, "audit": st.audit_log(module, min(max(limit, 1), 1000))}


@router.get("/api/itest/summary")
def summary(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Dashboard figures of Security, Fuse, ActiveMQ and the shared actions (real data only; a module that did not
    start says so)."""
    out: dict[str, Any] = {"ok": True}
    for name in ("security", "fuse", "activemq"):
        svc = getattr(rt, name, None)
        try:
            out[name] = svc.dashboard() if svc is not None else {"unavailable": True}
        except Exception as exc:  # noqa: BLE001 - one module's figures never break the dashboard
            out[name] = {"unavailable": True, "error": str(getattr(exc, "message", exc))[:200]}
    out["shared"] = _shared(rt)
    return out


def _shared(rt: Runtime) -> dict[str, Any]:
    db = rt.db
    q = lambda sql: (db.query_one(sql) or {}).get("n") or 0  # noqa: E731
    acts = getattr(rt, "test_actions", None)
    return {
        "ai_analyses": int(q("SELECT COUNT(*) AS n FROM ai_analyses")),
        "pending_analyses": int(acts.pending_analyses()) if acts else 0,
        "defect_drafts": int(q("SELECT COUNT(*) AS n FROM itest_results WHERE draft_id IS NOT NULL") +
                             q("SELECT COUNT(*) AS n FROM sec_findings WHERE draft_id IS NOT NULL")),
        "reports": int(q("SELECT COUNT(DISTINCT target) AS n FROM itest_audit WHERE action = 'pdf.created'")),
        "emails_draft": int(q("SELECT COUNT(*) AS n FROM email_outbox WHERE status = 'draft'")),
        "emails_accepted": int(q("SELECT COUNT(*) AS n FROM email_outbox WHERE status = 'accepted'")),
        "emails_failed": int(q("SELECT COUNT(*) AS n FROM email_outbox WHERE status = 'failed'")),
        "failed_results": int(q("SELECT COUNT(*) AS n FROM itest_results WHERE status IN ('FAILED','ERROR')")),
        "retests": int(q("SELECT COUNT(*) AS n FROM itest_runs WHERE kind = 'retest'")),
    }
