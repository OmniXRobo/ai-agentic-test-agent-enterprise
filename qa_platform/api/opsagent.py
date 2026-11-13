"""AI Operations Agent API (/api/ops): learning, knowledge, accounts, chat and voice, preview / confirm / execute.

Signed-in SkyGuard accounts only (the existing sign-in; no second login). Developer and Manager accounts are view
only, as everywhere in SkyGuard: they can read knowledge, executions and audit, but cannot register, learn, confirm or
run anything. Restoring or importing knowledge and changing the agent configuration need an admin.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from skyguard_agent.util import AgentError

from ..errors import QAError, error_body
from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/ops")
MAX_AUDIO_BYTES = 10 * 1024 * 1024


def ops(rt: Runtime = Depends(runtime)) -> Any:
    if getattr(rt, "opsagent", None) is None:
        raise QAError("OPS_AGENT_UNAVAILABLE", "The AI Operations Agent did not start; see the application log.",
                      status=503)
    return rt.opsagent


def actor(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return getattr(user, "username", None) or "local-program"


def install(app: Any) -> None:
    @app.exception_handler(AgentError)
    async def _agent_error(_: Request, exc: AgentError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status)


# ---- info and configuration -------------------------------------------------------------------------------------


@router.get("/info")
def info(svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, **svc.agent.info(), "config": svc.config(), "llm_health": None}


class ConfigIn(BaseModel):
    headless: bool | None = None
    browser: Literal["chromium", "msedge", "chrome"] | None = None
    use_ai: bool | None = None
    max_discovery_pages: int | None = Field(default=None, ge=1, le=200)


@router.put("/config")
def put_config(body: ConfigIn, rt: Runtime = Depends(runtime), svc: Any = Depends(ops)) -> dict[str, Any]:
    rt.settings.update({"opsagent": body.model_dump(exclude_none=True)})
    return {"ok": True, "config": svc.config()}


# ---- applications -----------------------------------------------------------------------------------------------


class AppIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=8, max_length=500)
    environment: str = Field(default="", max_length=60)
    description: str = Field(default="", max_length=2000)
    mode: Literal["testing", "role_testing", "production"] = "testing"
    app_version: str = Field(default="", max_length=60)
    extra_origins: list[str] = Field(default_factory=list, max_length=10)


class AppPatch(BaseModel):
    base_url: str | None = Field(default=None, max_length=500)
    environment: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=2000)
    mode: Literal["testing", "role_testing", "production"] | None = None
    app_version: str | None = Field(default=None, max_length=60)
    extra_origins: list[str] | None = Field(default=None, max_length=10)


@router.get("/applications")
def applications(svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "applications": svc.agent.kb.applications()}


@router.post("/applications", status_code=201)
def register(body: AppIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "application": svc.agent.kb.register_application(by=actor(request), **body.model_dump())}


@router.patch("/applications/{app_id}")
def update_app(app_id: str, body: AppPatch, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "application": svc.agent.kb.update_application(app_id, by=actor(request),
                                                                        **body.model_dump(exclude_none=True))}


@router.delete("/applications/{app_id}")
def delete_app(app_id: str, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    svc.agent.kb.delete_application(app_id, by=actor(request))
    return {"ok": True}


class ProfileIn(BaseModel):
    profile: dict[str, Any]


@router.put("/applications/{app_id}/profile")
def put_profile(app_id: str, body: ProfileIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "application": svc.agent.kb.set_profile(app_id, body.profile, by=actor(request))}


# ---- accounts and credentials ----------------------------------------------------------------------------------


class AccountIn(BaseModel):
    application_id: str
    label: str = Field(min_length=1, max_length=80)
    login_id: str = Field(min_length=1, max_length=120)
    role_key: str = Field(default="", max_length=60)
    role_description: str = Field(default="", max_length=300)
    environment: str = Field(default="", max_length=60)
    owner: str = Field(default="", max_length=80)


class AccountPatch(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    login_id: str | None = Field(default=None, max_length=120)
    role_key: str | None = Field(default=None, max_length=60)
    role_description: str | None = Field(default=None, max_length=300)
    environment: str | None = Field(default=None, max_length=60)
    owner: str | None = Field(default=None, max_length=80)


class CredentialIn(BaseModel):
    password: str = Field(min_length=1, max_length=512)
    consent: bool
    expires_at: str | None = Field(default=None, max_length=40)


@router.get("/accounts")
def accounts(app_id: str | None = None, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "accounts": svc.agent.kb.accounts(app_id), "vault": svc.agent.vault.backend_name,
            "vault_available": svc.agent.vault.available}


@router.post("/accounts", status_code=201)
def add_account(body: AccountIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    data = body.model_dump()
    app_id = data.pop("application_id")
    return {"ok": True, "account": svc.agent.kb.add_account(app_id, by=actor(request), **data)}


@router.patch("/accounts/{acc_id}")
def update_account(acc_id: str, body: AccountPatch, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "account": svc.agent.kb.update_account(acc_id, by=actor(request),
                                                                **body.model_dump(exclude_none=True))}


@router.put("/accounts/{acc_id}/credential")
def set_credential(acc_id: str, body: CredentialIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    """The secure credential form: the password goes straight to the vault, never to chat, logs or the model."""
    return {"ok": True, "account": svc.agent.kb.set_credential(acc_id, body.password, consent=body.consent,
                                                                expires_at=body.expires_at, by=actor(request))}


@router.post("/accounts/{acc_id}/revoke")
def revoke(acc_id: str, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "account": svc.agent.kb.revoke_credential(acc_id, by=actor(request))}


@router.delete("/accounts/{acc_id}")
def delete_account(acc_id: str, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    svc.agent.kb.delete_account(acc_id, by=actor(request))
    return {"ok": True}


# ---- knowledge ----------------------------------------------------------------------------------------------------

Kind = Literal["screen", "workflow", "rule", "relationship", "role", "profile"]


class KnowledgeIn(BaseModel):
    application_id: str
    kind: Kind
    key: str = Field(min_length=1, max_length=160)
    data: dict[str, Any]
    note: str | None = Field(default=None, max_length=500)


class DecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class CorrectIn(BaseModel):
    data: dict[str, Any]
    note: str | None = Field(default=None, max_length=500)


class RollbackIn(BaseModel):
    application_id: str
    kind: Kind
    key: str
    version: int = Field(ge=1)


@router.get("/knowledge")
def knowledge(app_id: str | None = None, kind: Kind | None = None, state: str | None = None,
              svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "items": svc.agent.kb.items(app_id, kind, state)}


@router.get("/knowledge/{entry_id}")
def knowledge_entry(entry_id: int, svc: Any = Depends(ops)) -> dict[str, Any]:
    e = svc.agent.kb.entry(entry_id)
    return {"ok": True, "entry": e, "history": svc.agent.kb.history(e["application_id"], e["kind"], e["key"])}


@router.post("/knowledge", status_code=201)
def add_knowledge(body: KnowledgeIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    if body.kind == "rule":
        from skyguard_agent.rules import validate_rule

        problems = validate_rule(body.data)
        if problems:
            raise AgentError("RULE_INVALID", " ".join(problems))
    return {"ok": True, "entry": svc.agent.kb.propose(body.application_id, body.kind, body.key, body.data,
                                                      source="user", state="awaiting_approval", confidence=1.0,
                                                      by=actor(request), note=body.note)}


@router.post("/knowledge/{entry_id}/approve")
def approve(entry_id: int, body: DecisionIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    e = svc.agent.kb.entry(entry_id)
    if e["kind"] == "rule":
        from skyguard_agent.rules import validate_rule

        problems = validate_rule(e["data"])
        if problems:
            raise AgentError("RULE_INVALID", " ".join(problems))
    return {"ok": True, "entry": svc.agent.kb.approve(entry_id, by=actor(request), note=body.note)}


@router.post("/knowledge/{entry_id}/reject")
def reject(entry_id: int, body: DecisionIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "entry": svc.agent.kb.reject(entry_id, by=actor(request), note=body.note)}


@router.post("/knowledge/{entry_id}/correct")
def correct(entry_id: int, body: CorrectIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "entry": svc.agent.kb.correct(entry_id, body.data, by=actor(request), note=body.note)}


@router.post("/knowledge-rollback")
def rollback(body: RollbackIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "entry": svc.agent.kb.rollback(body.application_id, body.kind, body.key, body.version,
                                                       by=actor(request))}


@router.get("/locators")
def locators(app_id: str, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "locators": svc.agent.kb.locators(app_id)}


@router.get("/knowledge-export")
def export(app_id: str | None = None, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "export": svc.agent.kb.export(app_id)}


class ImportIn(BaseModel):
    data: dict[str, Any]


@router.post("/admin/knowledge-import")
def import_knowledge(body: ImportIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "imported": svc.agent.kb.import_(body.data, by=actor(request))}


@router.post("/knowledge-backup")
def backup(request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "backup": svc.agent.kb.backup(svc.agent.backups_dir, by=actor(request))}


@router.get("/knowledge-backups")
def backups(svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "backups": svc.agent.kb.backups(svc.agent.backups_dir)}


class RestoreIn(BaseModel):
    name: str = Field(max_length=120)


@router.post("/admin/knowledge-restore")
def restore(body: RestoreIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, **svc.agent.kb.restore(svc.agent.backups_dir, body.name, by=actor(request))}


# ---- chat and voice -----------------------------------------------------------------------------------------------


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    conversation: str | None = Field(default=None, max_length=80)
    app_id: str | None = Field(default=None, max_length=80)
    source: Literal["typed", "voice"] = "typed"


@router.post("/chat")
async def chat(body: ChatIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    out = await run_in_threadpool(svc.agent.chat, body.text, conversation=body.conversation, user=actor(request),
                                  interface="skyguard", app_id=body.app_id, source=body.source)
    return {"ok": True, **out}


@router.get("/chat/{conversation}")
def chat_history(conversation: str, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "messages": svc.agent.history(conversation)}


@router.post("/voice/transcribe")
async def transcribe(request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    """Local speech-to-text only; the text comes back for editing and is never executed by itself."""
    audio = await request.body()
    if len(audio) > MAX_AUDIO_BYTES:
        raise AgentError("AUDIO_TOO_LARGE", "The recording is too long.", 413)
    content_type = request.headers.get("content-type", "audio/wav").split(";")[0]
    text = await run_in_threadpool(svc.agent.transcribe, audio, content_type)
    return {"ok": True, "text": text}


# ---- learning -----------------------------------------------------------------------------------------------------


class DiscoveryIn(BaseModel):
    application_id: str
    account_id: str | None = None
    max_pages: int = Field(default=25, ge=1, le=200)
    start_url: str | None = Field(default=None, max_length=500)


@router.post("/discovery", status_code=201)
def discovery(body: DiscoveryIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    view = svc.agent.learning.start_discovery(body.application_id, account_id=body.account_id, by=actor(request),
                                              interface="skyguard", max_pages=body.max_pages, start_url=body.start_url)
    svc.start_run(view["run"]["id"])
    return {"ok": True, **view}


class GuidedIn(BaseModel):
    application_id: str
    account_id: str | None = None
    title: str = Field(min_length=1, max_length=80)


@router.post("/learning", status_code=201)
def start_guided(body: GuidedIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "session": svc.start_guided(body.application_id, body.account_id, body.title, actor(request))}


@router.get("/learning")
def learning_sessions(app_id: str | None = None, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "sessions": svc.agent.learning.sessions(app_id)}


@router.post("/learning/{sid}/finish")
async def finish_guided(sid: str, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, **(await run_in_threadpool(svc.finish_guided, sid, actor(request)))}


@router.post("/learning/{sid}/cancel")
def cancel_guided(sid: str, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "session": svc.cancel_guided(sid, actor(request))}


# ---- executions ---------------------------------------------------------------------------------------------------


class StageIn(BaseModel):
    workflow: str = Field(min_length=1, max_length=160)
    account_id: str


class RunIn(BaseModel):
    application_id: str
    stages: list[StageIn] = Field(min_length=1, max_length=10)
    records: list[dict[str, Any]] = Field(min_length=1, max_length=500)
    on_error: Literal["pause", "continue"] | None = None


class ConfirmIn(BaseModel):
    preview_hash: str = Field(min_length=64, max_length=64)


class ResumeIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)
    choice: int | None = Field(default=None, ge=0, le=4, description="23.6.0: which listed look-alike control to use")


@router.get("/runs")
def runs(app_id: str | None = None, limit: int = 50, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "runs": svc.agent.engine.runs(limit=limit, app_id=app_id)}


@router.post("/runs", status_code=201)
def create_run(body: RunIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    records = [{str(k)[:60]: str(v)[:500] for k, v in r.items()} for r in body.records]
    view = svc.agent.engine.create(app_id=body.application_id, stages=[s.model_dump() for s in body.stages],
                                   records=records, interface="skyguard", by=actor(request),
                                   options={"on_error": body.on_error} if body.on_error else None)
    return {"ok": True, **view}


@router.get("/runs/{run_id}")
def get_run(run_id: str, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, **svc.agent.engine.view(run_id), "browser": svc.browser_state(run_id)}


@router.get("/runs/{run_id}/events")
def run_events(run_id: str, after: int = 0, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "events": svc.agent.engine.events(run_id, after=after)}


@router.post("/runs/{run_id}/start")
def start(run_id: str, svc: Any = Depends(ops)) -> dict[str, Any]:
    """Open the SkyGuard-controlled browser for this execution (inspection, then execution after confirmation)."""
    return {"ok": True, **svc.start_run(run_id)}


@router.post("/runs/{run_id}/confirm")
def confirm(run_id: str, body: ConfirmIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    view = svc.agent.engine.confirm(run_id, body.preview_hash, by=actor(request), interface="skyguard")
    if run_id not in svc.jobs:
        svc.start_run(run_id)
    return {"ok": True, **view}


@router.post("/runs/{run_id}/pause")
def pause(run_id: str, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, **svc.agent.engine.pause(run_id, by=actor(request))}


@router.post("/runs/{run_id}/resume")
def resume(run_id: str, body: ResumeIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, **svc.resume(run_id, actor(request), body.note, body.choice)}


@router.post("/runs/{run_id}/stop")
def stop(run_id: str, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, **svc.stop(run_id, actor(request))}


@router.post("/runs/{run_id}/cancel-remaining")
def cancel_remaining(run_id: str, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, **svc.agent.engine.cancel_remaining(run_id, by=actor(request))}


@router.post("/runs/{run_id}/acknowledge")
def acknowledge(run_id: str, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, **svc.agent.engine.acknowledge(run_id)}


@router.post("/runs/{run_id}/items/{seq}/skip")
def skip(run_id: str, seq: int, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, **svc.agent.engine.skip_item(run_id, seq, by=actor(request))}


# ---- audit --------------------------------------------------------------------------------------------------------


@router.get("/audit")
def audit(limit: int = 200, target: str | None = None, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "audit": svc.agent.audit.list(limit=limit, target=target)}


@router.get("/audit/verify")
def audit_verify(svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, **svc.agent.audit.verify()}
