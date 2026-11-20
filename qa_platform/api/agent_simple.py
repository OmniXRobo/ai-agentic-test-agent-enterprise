"""23.10.0 simple AI Agent API (/api/ops/simple): the four tabs of the AI Agent page.

  AI Chat & Voice          POST /command, POST /voice (local speech-to-text, then the same command path)
  Connect & Learn          POST /connect, POST /learn/{sid}/stop, POST /explore, GET /status
  Applications & Knowledge GET /knowledge/{app_id}, POST /knowledge/{entry_id}/forget, POST /knowledge/{entry_id}/trust
  Tasks & History          GET /tasks, POST /runs/{run_id}/confirm

Same sign-in and roles as the rest of SkyGuard: view-only accounts can read but every POST here is refused for them
by the central guard. Consequential operations (approve, authorize, delete, cancel, record, submit ...) need
``acknowledge: true`` on confirmation, which the page sends only after the person ticks the confirmation box.

23.11.1: a "navigate" reply opens the page (in the learning window when one is open for that application, else in a
new maximized window); GET /suggestions gives the contextual suggestions; GET /runs/{id} the short preview with
``can_confirm``; confirmation is refused for a plan that is not linked to the request and application it was made
for, and for a preview in which no record can run (with the real reasons).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from skyguard_agent.autopilot import run_is_consequential
from skyguard_agent.util import AgentError

from .opsagent import MAX_AUDIO_BYTES, actor, ops

router = APIRouter(prefix="/api/ops/simple")


class ConnectIn(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    name: str | None = Field(default=None, max_length=80)
    environment: str | None = Field(default=None, max_length=60)  # 23.11.1: "Test", "Production" ...
    learn: bool = True
    browser: Literal["msedge", "chrome", "chromium"] | None = None


@router.post("/connect", status_code=201)
async def connect(body: ConnectIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    out = await run_in_threadpool(svc.connect_and_learn, body.url, body.name, actor(request), body.learn,
                                  body.browser, body.environment)
    return {"ok": True, **out}


@router.post("/learn/{sid}/stop")
async def stop_learning(sid: str, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    view = await run_in_threadpool(svc.stop_watch, sid, actor(request))
    app_id = (view.get("application") or {}).get("id")
    trusted = svc.pilot.trust(app_id) if app_id and svc.config().get("auto_trust", True) else None
    # 23.11.1: learning is reported as saved only with proof that it can be retrieved by a command
    verified = svc.pilot.verify_retrieval(app_id) if app_id else None
    return {"ok": True, "understanding": view, "trusted": trusted, "verified": verified}


class ExploreIn(BaseModel):
    application_id: str = Field(max_length=80)
    account_id: str | None = Field(default=None, max_length=80)
    max_pages: int = Field(default=25, ge=1, le=200)


@router.post("/explore", status_code=201)
def explore(body: ExploreIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    """Read-only automatic discovery: menus, pages, forms, tables. Nothing is submitted, created or deleted."""
    view = svc.agent.learning.start_discovery(body.application_id, account_id=body.account_id,
                                              max_pages=body.max_pages, by=actor(request), interface="skyguard")
    svc.start_run(view["run"]["id"])
    return {"ok": True, **view}


@router.get("/status")
def status(svc: Any = Depends(ops)) -> dict[str, Any]:
    apps = svc.agent.kb.applications()
    watching = []
    for sid in svc.watching():
        app_id = svc._session_app(sid)
        watching.append({"session_id": sid, "application_id": app_id,
                         "stats": svc.agent.journal.stats(app_id) if app_id else {}})
    return {"ok": True, "applications": apps, "watching": watching,
            "auto_trust": bool(svc.config().get("auto_trust", True)), "agent": svc.agent.info()}


class CommandIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    conversation: str | None = Field(default=None, max_length=80)
    app_id: str | None = Field(default=None, max_length=80)
    source: Literal["typed", "voice"] = "typed"


@router.post("/command")
async def command(body: CommandIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    # 23.13.0: Arabic (including UAE Arabic) is translated to English by the local AI first; the English command is
    # what the agent plans with, and both are shown
    text, translated = await run_in_threadpool(svc.to_english, body.text)
    out = await run_in_threadpool(svc.pilot.command, text, conversation=body.conversation,
                                  user=actor(request), interface="skyguard", app_id=body.app_id, source=body.source)
    reply = out["reply"]
    if translated:
        reply["translated_from"] = body.text
        reply["translated_to"] = text
    if reply.get("type") == "run" and reply.get("run_id") and not reply.get("duplicate"):
        # the chat browser checks the application read-only and prepares the one preview (23.13.0: the same window
        # for every request of this chat)
        try:
            svc.start_run(reply["run_id"], chat=True)
        except AgentError:
            pass
    if reply.get("type") == "navigate" and reply.get("url"):
        try:
            reply["opened"] = await run_in_threadpool(svc.open_page, reply["app_id"], reply["url"], actor(request),
                                                      new_window=bool(reply.get("new_window")))
        except AgentError as exc:
            reply["opened"] = {"ok": False, "message": exc.message}
    if reply.get("type") == "sign_in" and reply.get("account_id"):
        try:
            view = await run_in_threadpool(svc.sign_in, reply["app_id"], reply["account_id"], actor(request))
            reply["run_id"] = view["run"]["id"]
        except AgentError as exc:
            reply.update(type="error", text=exc.message)
    return {"ok": True, **out}


class NewAccountIn(BaseModel):
    app_id: str = Field(max_length=80)
    label: str = Field(min_length=1, max_length=80)
    login_id: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=512)
    consent: bool


@router.post("/accounts", status_code=201)
def new_account(body: NewAccountIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    """23.13.0: "log in as manager" when no such account exists: the chat's secure form creates the account (label
    and user name) and stores the password in Windows Credential Manager in one step. The password is never
    returned, logged or stored in the chat."""
    if not body.consent:
        raise AgentError("CONSENT_REQUIRED", "Confirm that you may let SkyGuard sign in with this account.", 422)
    kb = svc.agent.kb
    acc = kb.add_account(body.app_id, label=body.label.strip(), login_id=body.login_id.strip(),
                         role_key=body.label.strip().lower(), by=actor(request))
    kb.set_credential(acc["id"], body.password, by=actor(request), consent=True)
    return {"ok": True, "account": kb.account(acc["id"])}


@router.get("/suggestions")
def suggestions(request: Request, app_id: str, conversation: str | None = None,
                svc: Any = Depends(ops)) -> dict[str, Any]:
    """Contextual suggestions: the page open in the learning window, learned tasks the accounts may do, records
    waiting for a step, recent requests. View-only users get reading and navigation suggestions only."""
    user = getattr(request.state, "user", None)
    can_act = not bool(getattr(user, "view_only", False))
    page = svc.current_url(app_id)
    items = svc.pilot.suggestions(app_id, page_url=page, can_act=can_act, conversation=conversation)
    return {"ok": True, "suggestions": items, "page": page,
            "learned": bool(svc.agent.understanding.model(app_id))}


@router.get("/runs/{run_id}")
def run_view(run_id: str, svc: Any = Depends(ops)) -> dict[str, Any]:
    view = svc.agent.engine.view(run_id)
    return {"ok": True, **view, "simple": svc.pilot.simple_view(run_id)}


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, **svc.stop(run_id, actor(request))}


@router.post("/verify/{app_id}")
def verify(app_id: str, svc: Any = Depends(ops)) -> dict[str, Any]:
    """Test learned knowledge: every task and page is retrieved through the same router a command uses."""
    return {"ok": True, **svc.pilot.verify_retrieval(app_id)}


@router.post("/voice")
async def voice(request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    """Local speech-to-text (Faster-Whisper or the built-in recognizer). Returns the text; the page shows it and sends
    it as a command, which can only ever produce a preview to confirm."""
    audio = await request.body()
    if len(audio) > MAX_AUDIO_BYTES:
        raise AgentError("AUDIO_TOO_LARGE", "The recording is too long.", 413)
    content_type = request.headers.get("content-type", "audio/wav").split(";")[0]
    text = await run_in_threadpool(svc.agent.transcribe, audio, content_type)
    return {"ok": True, "text": text}


@router.get("/knowledge/{app_id}")
def knowledge(app_id: str, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, **svc.pilot.knowledge(app_id)}


class NoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.post("/knowledge/{entry_id}/forget")
def forget(entry_id: int, body: NoteIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    """A correction: this task or rule is wrong. It stops being used; the evidence stays in the history."""
    return {"ok": True, "entry": svc.agent.kb.reject(entry_id, by=actor(request), note=body.note or "Marked wrong")}


@router.post("/knowledge/{entry_id}/trust")
def trust(entry_id: int, body: NoteIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    """A correction the other way: use this suggestion (for example an AI-suggested rule)."""
    return {"ok": True, "entry": svc.agent.kb.approve(entry_id, by=actor(request), note=body.note or "Confirmed")}


@router.get("/tasks")
def tasks(app_id: str | None = None, limit: int = 50, svc: Any = Depends(ops)) -> dict[str, Any]:
    return {"ok": True, "tasks": svc.pilot.tasks(app_id, limit=max(1, min(limit, 200)))}


class ConfirmIn(BaseModel):
    preview_hash: str = Field(min_length=64, max_length=64)
    acknowledge: bool = False
    app_id: str | None = Field(default=None, max_length=80)  # the application selected on the page


def consequential(run: dict[str, Any]) -> bool:
    return run_is_consequential(run)


@router.post("/runs/{run_id}/confirm")
def confirm(run_id: str, body: ConfirmIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    view = svc.pilot.confirm(run_id, body.preview_hash, acknowledge=body.acknowledge, by=actor(request),
                             interface="skyguard", app_id=body.app_id)
    if run_id not in svc.jobs:
        svc.start_run(run_id)
    return {"ok": True, **view, "consequential": run_is_consequential(view)}


class PublicIn(BaseModel):
    public: bool


@router.post("/applications/{app_id}/public")
def set_public(app_id: str, body: PublicIn, request: Request, svc: Any = Depends(ops)) -> dict[str, Any]:
    """23.13.0: "this website needs no sign-in": tasks run without an account and the chat never asks for one."""
    kb = svc.agent.kb
    app = kb.application(app_id)
    profile = dict(app.get("profile") or {})
    profile["public"] = bool(body.public)
    return {"ok": True, "application": kb.set_profile(app["id"], profile, by=actor(request))}
