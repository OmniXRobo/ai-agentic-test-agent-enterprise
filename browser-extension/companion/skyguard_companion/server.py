"""The companion's HTTP API on 127.0.0.1 (standard library only).

Every request passes, in order: host check, origin check (the SkyGuard extension), rate limit, size limit, device
pairing, sign-in token and role, schema validation. Bodies, passwords and codes are never logged.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from skyguard_agent.agent import OperationsAgent
from skyguard_agent.rules import validate_rule
from skyguard_agent.secrets_guard import redact_text
from skyguard_agent.util import AgentError
from skyguard_agent.vault import MemoryBackend, Vault, default_backend

from . import NAME, VERSION
from .config import Config, Paths
from .models import LocalLLM, LocalWhisper
from .security import Pairing, Refused, RateLimiter, Users, check_host, check_origin, validate

log = logging.getLogger("companion")
JSON_LIMIT = 256 * 1024
OBSERVATION_LIMIT = 4 * 1024 * 1024  # page snapshots
AUDIO_LIMIT = 10 * 1024 * 1024
KINDS = ("screen", "workflow", "rule", "relationship", "role", "profile")
MODES = ("testing", "role_testing", "production")
WRITE_ROLES = ("admin", "operator")


class Companion:
    """Everything the HTTP handler needs; created once per process."""

    def __init__(self, paths: Paths, *, vault: Vault | None = None, llm: Any = None, whisper: Any = None) -> None:
        self.paths = paths.ensure()
        self.config = Config(paths)
        self.pairing = Pairing(paths.devices_file, paths.pairing_file)
        self.users = Users(paths.users_file, int(self.config.get("token_minutes", 15)),
                           int(self.config.get("session_hours", 8)))
        self.limiter = RateLimiter()
        backend = default_backend(paths.vault)
        self.vault = vault or Vault(backend if backend is not None else None)
        self.llm = llm if llm is not None else LocalLLM(self.config.get("llm"), paths.root, paths.logs)
        self.whisper = whisper if whisper is not None else LocalWhisper(self.config.get("whisper"), paths.root,
                                                                        paths.logs)
        self.agent = OperationsAgent(paths.data / "opsagent", vault=self.vault, llm=None,
                                     transcriber=self.whisper.transcribe, interface="extension")
        self.started = time.time()

    def start_models(self) -> None:
        """Connect to or start the local model and speech recognizer in the background."""
        def run() -> None:
            try:
                status = self.llm.ensure()
                if status.get("state") == "ready":
                    self.agent.set_llm(self.llm)
            except Exception:  # noqa: BLE001 - the companion works without AI (deterministic parsing)
                log.exception("local AI model could not start")
            try:
                self.whisper.ensure()
            except Exception:  # noqa: BLE001
                log.exception("speech recognizer could not start")
        threading.Thread(target=run, name="models", daemon=True).start()

    def close(self) -> None:
        for part in (self.llm, self.whisper):
            try:
                part.stop()
            except Exception:  # noqa: BLE001
                pass
        self.agent.close()


# ---- routing -------------------------------------------------------------------------------------------------------

Route = tuple[str, re.Pattern[str], Callable[..., Any], str, str]  # method, path, handler, access, bucket
ROUTES: list[Route] = []


def route(method: str, path: str, access: str = "read", bucket: str = "default") -> Callable:
    """access: public (origin only) | device (paired) | read (signed in) | write (operator/admin) | admin."""
    pattern = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[A-Za-z0-9._:-]{1,120})", path) + "$")

    def deco(fn: Callable) -> Callable:
        ROUTES.append((method, pattern, fn, access, bucket))
        return fn
    return deco


class Ctx:
    def __init__(self, c: Companion, body: Any, query: dict[str, str], user: str | None, role: str | None,
                 device: dict[str, Any] | None, extension_id: str, token: str | None, raw: bytes = b"",
                 content_type: str = "") -> None:
        self.c, self.body, self.query, self.user, self.role = c, body, query, user, role
        self.device, self.extension_id, self.token, self.raw, self.content_type = device, extension_id, token, raw, content_type

    @property
    def kb(self) -> Any:
        return self.c.agent.kb

    @property
    def engine(self) -> Any:
        return self.c.agent.engine


# public and pairing


@route("GET", "/v1/hello", "public")
def hello(x: Ctx) -> dict[str, Any]:
    return {"name": NAME, "version": VERSION, "agent": x.c.agent.info()["version"]}


@route("POST", "/v1/pair", "public", "pair")
def pair(x: Ctx) -> dict[str, Any]:
    b = validate(x.body, {"code": ("str", True, 20), "name": ("str", False, 60)})
    out = x.c.pairing.pair(b["code"], x.extension_id, b.get("name") or "browser")
    x.c.agent.audit.record("companion.paired", interface="extension", details={"device": out["device_id"]})
    return out


@route("GET", "/v1/setup-state", "device")
def setup_state(x: Ctx) -> dict[str, Any]:
    return {"users_exist": not x.c.users.empty()}


@route("POST", "/v1/setup", "device", "login")
def setup(x: Ctx) -> dict[str, Any]:
    b = validate(x.body, {"username": ("str", True, 40), "password": ("str", True, 200)})
    if not x.c.users.empty():
        raise Refused(409, "SETUP_DONE", "The first user already exists. Sign in instead.")
    user = x.c.users.create(b["username"], b["password"], "admin")
    x.c.agent.audit.record("companion.first_admin", actor=user["username"], interface="extension")
    return {"user": user}


@route("POST", "/v1/auth/login", "device", "login")
def login(x: Ctx) -> dict[str, Any]:
    b = validate(x.body, {"username": ("str", True, 40), "password": ("str", True, 200)})
    try:
        out = x.c.users.login(b["username"], b["password"], x.device["id"])
    except Refused:
        x.c.agent.audit.record("companion.login_failed", actor=b["username"][:40], interface="extension")
        raise
    x.c.agent.audit.record("companion.login", actor=out["user"]["username"], interface="extension")
    return out


@route("POST", "/v1/auth/refresh", "read")
def refresh(x: Ctx) -> dict[str, Any]:
    return x.c.users.refresh(x.token or "", x.device["id"])


@route("POST", "/v1/auth/logout", "read")
def logout(x: Ctx) -> dict[str, Any]:
    x.c.users.logout(x.token)
    return {}


@route("GET", "/v1/auth/me", "read")
def me(x: Ctx) -> dict[str, Any]:
    return {"user": {"username": x.user, "role": x.role}}


@route("POST", "/v1/auth/password", "read", "login")
def change_password(x: Ctx) -> dict[str, Any]:
    b = validate(x.body, {"current": ("str", True, 200), "new": ("str", True, 200)})
    x.c.users.change_password(x.user or "", b["current"], b["new"])
    return {}


# administration


@route("GET", "/v1/users", "admin")
def users(x: Ctx) -> dict[str, Any]:
    return {"users": x.c.users.list()}


@route("POST", "/v1/users", "admin")
def add_user(x: Ctx) -> dict[str, Any]:
    b = validate(x.body, {"username": ("str", True, 40), "password": ("str", True, 200), "role": ("str", True, 10)})
    user = x.c.users.create(b["username"], b["password"], b["role"])
    x.c.agent.audit.record("companion.user_added", actor=x.user, interface="extension", target=user["username"],
                           details={"role": user["role"]})
    return {"user": user}


@route("DELETE", "/v1/users/{username}", "admin")
def delete_user(x: Ctx, username: str) -> dict[str, Any]:
    x.c.users.delete(username)
    x.c.agent.audit.record("companion.user_removed", actor=x.user, interface="extension", target=username)
    return {}


@route("GET", "/v1/devices", "admin")
def devices(x: Ctx) -> dict[str, Any]:
    return {"devices": x.c.pairing.devices()}


@route("DELETE", "/v1/devices/{device_id}", "admin")
def remove_device(x: Ctx, device_id: str) -> dict[str, Any]:
    x.c.pairing.remove(device_id)
    x.c.users.revoke_device(device_id)
    x.c.agent.audit.record("companion.device_removed", actor=x.user, interface="extension", target=device_id)
    return {}


@route("GET", "/v1/info", "read")
def info(x: Ctx) -> dict[str, Any]:
    cfg = x.c.config.data
    return {"companion": {"name": NAME, "version": VERSION, "home": str(x.c.paths.root)}, "agent": x.c.agent.info(),
            "llm": x.c.llm.status(), "whisper": x.c.whisper.status(),
            "config": {"llm": {k: cfg["llm"].get(k) for k in ("mode", "url", "model")},
                       "whisper": {k: cfg["whisper"].get(k) for k in ("mode", "url", "language")},
                       "max_discovery_pages": cfg.get("max_discovery_pages")},
            "user": {"username": x.user, "role": x.role}}


@route("PUT", "/v1/config", "admin")
def put_config(x: Ctx) -> dict[str, Any]:
    b = validate(x.body, {"llm": ("dict", False, 10), "whisper": ("dict", False, 10),
                          "max_discovery_pages": ("int", False, 200)})
    changes: dict[str, Any] = {}
    for part, allowed in (("llm", {"mode": ("str", False, 10), "url": ("str", False, 200), "model": ("str", False, 60)}),
                          ("whisper", {"mode": ("str", False, 10), "url": ("str", False, 200),
                                       "language": ("str", False, 10)})):
        if part in b:
            v = validate(b[part], allowed)
            if "mode" in v and v["mode"] not in ("auto", "connect", "launch", "off"):
                raise Refused(400, "FIELD_INVALID", "mode must be auto, connect, launch or off.")
            if "url" in v and not re.match(r"^http://(127\.0\.0\.1|localhost)(:\d{2,5})?(/.*)?$", v["url"]):
                raise Refused(400, "URL_NOT_LOCAL", "The AI model and speech recognizer must run on this PC "
                                                     "(http://127.0.0.1:...).")
            changes[part] = v
    if "max_discovery_pages" in b:
        changes["max_discovery_pages"] = max(1, b["max_discovery_pages"])
    data = x.c.config.update(changes)
    x.c.llm.cfg.update(data["llm"])
    x.c.whisper.cfg.update(data["whisper"])
    x.c.agent.audit.record("companion.config", actor=x.user, interface="extension", details={"changed": sorted(changes)})
    x.c.start_models()
    return {"config": {"llm": data["llm"], "whisper": data["whisper"]}}


# applications and accounts

APP_FIELDS = {"name": ("str", True, 80), "base_url": ("str", True, 500), "environment": ("str", False, 60),
              "description": ("str", False, 2000), "mode": ("str", False, 20), "app_version": ("str", False, 60),
              "extra_origins": ("list", False, 10)}


@route("GET", "/v1/applications")
def applications(x: Ctx) -> dict[str, Any]:
    return {"applications": x.kb.applications()}


@route("POST", "/v1/applications", "write")
def register(x: Ctx) -> dict[str, Any]:
    b = validate(x.body, APP_FIELDS)
    if b.get("mode", "testing") not in MODES:
        raise Refused(400, "FIELD_INVALID", "mode must be testing, role_testing or production.")
    return {"application": x.kb.register_application(by=x.user, **b)}


@route("PATCH", "/v1/applications/{app_id}", "write")
def update_app(x: Ctx, app_id: str) -> dict[str, Any]:
    b = validate(x.body, {k: (t, False, m) for k, (t, _, m) in APP_FIELDS.items() if k != "name"})
    return {"application": x.kb.update_application(app_id, by=x.user, **b)}


@route("DELETE", "/v1/applications/{app_id}", "write")
def delete_app(x: Ctx, app_id: str) -> dict[str, Any]:
    x.kb.delete_application(app_id, by=x.user)
    return {}


@route("PUT", "/v1/applications/{app_id}/profile", "write")
def put_profile(x: Ctx, app_id: str) -> dict[str, Any]:
    b = validate(x.body, {"profile": ("dict", True, 30)})
    return {"application": x.kb.set_profile(app_id, b["profile"], by=x.user)}


ACC_FIELDS = {"label": ("str", True, 80), "login_id": ("str", True, 120), "role_key": ("str", False, 60),
              "role_description": ("str", False, 300), "environment": ("str", False, 60), "owner": ("str", False, 80)}


@route("GET", "/v1/accounts")
def accounts(x: Ctx) -> dict[str, Any]:
    return {"accounts": x.kb.accounts(x.query.get("app_id")), "vault": x.c.vault.backend_name,
            "vault_available": x.c.vault.available}


@route("POST", "/v1/accounts", "write")
def add_account(x: Ctx) -> dict[str, Any]:
    b = validate(x.body, {"application_id": ("str", True, 80), **ACC_FIELDS})
    app_id = b.pop("application_id")
    return {"account": x.kb.add_account(app_id, by=x.user, **b)}


@route("PATCH", "/v1/accounts/{acc_id}", "write")
def update_account(x: Ctx, acc_id: str) -> dict[str, Any]:
    b = validate(x.body, {k: (t, False, m) for k, (t, _, m) in ACC_FIELDS.items()})
    return {"account": x.kb.update_account(acc_id, by=x.user, **b)}


@route("PUT", "/v1/accounts/{acc_id}/credential", "write")
def set_credential(x: Ctx, acc_id: str) -> dict[str, Any]:
    """The extension's separate credential page posts here; the password goes straight to the vault."""
    b = validate(x.body, {"password": ("str", True, 512), "consent": ("bool", True), "expires_at": ("str", False, 40)})
    return {"account": x.kb.set_credential(acc_id, b["password"], consent=b["consent"],
                                           expires_at=b.get("expires_at"), by=x.user)}


@route("POST", "/v1/accounts/{acc_id}/revoke", "write")
def revoke(x: Ctx, acc_id: str) -> dict[str, Any]:
    return {"account": x.kb.revoke_credential(acc_id, by=x.user)}


@route("DELETE", "/v1/accounts/{acc_id}", "write")
def delete_account(x: Ctx, acc_id: str) -> dict[str, Any]:
    x.kb.delete_account(acc_id, by=x.user)
    return {}


# knowledge


@route("GET", "/v1/knowledge")
def knowledge(x: Ctx) -> dict[str, Any]:
    kind = x.query.get("kind")
    if kind and kind not in KINDS:
        raise Refused(400, "FIELD_INVALID", "Unknown kind.")
    return {"items": x.kb.items(x.query.get("app_id"), kind, x.query.get("state"))}


@route("GET", "/v1/knowledge/{entry_id}")
def knowledge_entry(x: Ctx, entry_id: str) -> dict[str, Any]:
    e = x.kb.entry(_int(entry_id))
    return {"entry": e, "history": x.kb.history(e["application_id"], e["kind"], e["key"])}


@route("POST", "/v1/knowledge", "write")
def add_knowledge(x: Ctx) -> dict[str, Any]:
    b = validate(x.body, {"application_id": ("str", True, 80), "kind": ("str", True, 20), "key": ("str", True, 160),
                          "data": ("dict", True, 200), "note": ("str", False, 500)})
    if b["kind"] not in KINDS:
        raise Refused(400, "FIELD_INVALID", "Unknown kind.")
    if b["kind"] == "rule":
        problems = validate_rule(b["data"])
        if problems:
            raise AgentError("RULE_INVALID", " ".join(problems))
    return {"entry": x.kb.propose(b["application_id"], b["kind"], b["key"], b["data"], source="user",
                                  state="awaiting_approval", confidence=1.0, by=x.user, note=b.get("note"))}


@route("POST", "/v1/knowledge/{entry_id}/approve", "write")
def approve(x: Ctx, entry_id: str) -> dict[str, Any]:
    b = validate(x.body or {}, {"note": ("str", False, 500)})
    e = x.kb.entry(_int(entry_id))
    if e["kind"] == "rule":
        problems = validate_rule(e["data"])
        if problems:
            raise AgentError("RULE_INVALID", " ".join(problems))
    return {"entry": x.kb.approve(e["id"], by=x.user, note=b.get("note"))}


@route("POST", "/v1/knowledge/{entry_id}/reject", "write")
def reject(x: Ctx, entry_id: str) -> dict[str, Any]:
    b = validate(x.body or {}, {"note": ("str", False, 500)})
    return {"entry": x.kb.reject(_int(entry_id), by=x.user, note=b.get("note"))}


@route("POST", "/v1/knowledge/{entry_id}/correct", "write")
def correct(x: Ctx, entry_id: str) -> dict[str, Any]:
    b = validate(x.body, {"data": ("dict", True, 200), "note": ("str", False, 500)})
    return {"entry": x.kb.correct(_int(entry_id), b["data"], by=x.user, note=b.get("note"))}


@route("POST", "/v1/knowledge-rollback", "write")
def rollback(x: Ctx) -> dict[str, Any]:
    b = validate(x.body, {"application_id": ("str", True, 80), "kind": ("str", True, 20), "key": ("str", True, 160),
                          "version": ("int", True, 100000)})
    return {"entry": x.kb.rollback(b["application_id"], b["kind"], b["key"], b["version"], by=x.user)}


@route("GET", "/v1/locators")
def locators(x: Ctx) -> dict[str, Any]:
    return {"locators": x.kb.locators(x.query.get("app_id", ""))}


@route("GET", "/v1/knowledge-export")
def export(x: Ctx) -> dict[str, Any]:
    return {"export": x.kb.export(x.query.get("app_id"))}


@route("POST", "/v1/knowledge-import", "admin")
def import_knowledge(x: Ctx) -> dict[str, Any]:
    b = validate(x.body, {"data": ("dict", True, 20)})
    return {"imported": x.kb.import_(b["data"], by=x.user)}


@route("POST", "/v1/knowledge-backup", "write")
def backup(x: Ctx) -> dict[str, Any]:
    return {"backup": x.kb.backup(x.c.agent.backups_dir, by=x.user)}


@route("GET", "/v1/knowledge-backups")
def backups(x: Ctx) -> dict[str, Any]:
    return {"backups": x.kb.backups(x.c.agent.backups_dir)}


@route("POST", "/v1/knowledge-restore", "admin")
def restore(x: Ctx) -> dict[str, Any]:
    b = validate(x.body, {"name": ("str", True, 120)})
    return x.kb.restore(x.c.agent.backups_dir, b["name"], by=x.user)


# chat and voice


@route("POST", "/v1/chat", "write", "llm")
def chat(x: Ctx) -> dict[str, Any]:
    b = validate(x.body, {"text": ("str", True, 4000), "conversation": ("str", False, 80), "app_id": ("str", False, 80),
                          "source": ("str", False, 10)})
    return x.c.agent.chat(b["text"], conversation=b.get("conversation"), user=x.user, interface="extension",
                          app_id=b.get("app_id"), source="voice" if b.get("source") == "voice" else "typed")


@route("GET", "/v1/chat/{conversation}")
def chat_history(x: Ctx, conversation: str) -> dict[str, Any]:
    return {"messages": x.c.agent.history(conversation)}


@route("POST", "/v1/voice/transcribe", "write", "voice")
def transcribe(x: Ctx) -> dict[str, Any]:
    if x.content_type not in ("audio/wav", "audio/x-wav", "audio/wave"):
        raise Refused(415, "AUDIO_TYPE", "Send a WAV recording.")
    try:
        return {"text": x.c.agent.transcribe(x.raw, x.content_type)}
    except RuntimeError as exc:
        raise AgentError("VOICE_UNAVAILABLE", str(exc), 503) from None


# learning


@route("POST", "/v1/discovery", "write")
def discovery(x: Ctx) -> dict[str, Any]:
    b = validate(x.body, {"application_id": ("str", True, 80), "account_id": ("str", False, 80),
                          "max_pages": ("int", False, 200), "start_url": ("str", False, 500)})
    return x.c.agent.learning.start_discovery(b["application_id"], account_id=b.get("account_id"), by=x.user,
                                              interface="extension",
                                              max_pages=b.get("max_pages") or x.c.config.get("max_discovery_pages"),
                                              start_url=b.get("start_url"))


@route("POST", "/v1/learning", "write")
def start_guided(x: Ctx) -> dict[str, Any]:
    b = validate(x.body, {"application_id": ("str", True, 80), "account_id": ("str", False, 80),
                          "title": ("str", True, 80)})
    return {"session": x.c.agent.learning.start_guided(b["application_id"], account_id=b.get("account_id"),
                                                       title=b["title"], by=x.user)}


@route("GET", "/v1/learning")
def learning_sessions(x: Ctx) -> dict[str, Any]:
    return {"sessions": x.c.agent.learning.sessions(x.query.get("app_id"))}


@route("POST", "/v1/learning/{sid}/events", "write")
def learning_events(x: Ctx, sid: str) -> dict[str, Any]:
    b = validate(x.body, {"events": ("list", True, 200), "snapshot": ("dict", False, 40)})
    return {"session": x.c.agent.learning.add_events(sid, b["events"], b.get("snapshot"))}


@route("POST", "/v1/learning/{sid}/finish", "write", "llm")
def finish_guided(x: Ctx, sid: str) -> dict[str, Any]:
    return x.c.agent.learning.finish_guided(sid, by=x.user)


@route("POST", "/v1/learning/{sid}/cancel", "write")
def cancel_guided(x: Ctx, sid: str) -> dict[str, Any]:
    return {"session": x.c.agent.learning.cancel_guided(sid, by=x.user)}


# executions


@route("GET", "/v1/runs")
def runs(x: Ctx) -> dict[str, Any]:
    return {"runs": x.engine.runs(limit=min(200, _int(x.query.get("limit", "50"))), app_id=x.query.get("app_id"))}


@route("POST", "/v1/runs", "write")
def create_run(x: Ctx) -> dict[str, Any]:
    b = validate(x.body, {"application_id": ("str", True, 80), "stages": ("list", True, 10),
                          "records": ("list", True, 500), "on_error": ("str", False, 10)})
    stages = [validate(s, {"workflow": ("str", True, 160), "account_id": ("str", True, 80)}) for s in b["stages"]]
    records = []
    for r in b["records"]:
        if not isinstance(r, dict) or len(r) > 60:
            raise Refused(400, "FIELD_INVALID", "Each record must be an object of up to 60 fields.")
        records.append({str(k)[:60]: str(v)[:500] for k, v in r.items()})
    opts = {"on_error": b["on_error"]} if b.get("on_error") in ("pause", "continue") else None
    return x.engine.create(app_id=b["application_id"], stages=stages, records=records, interface="extension",
                           by=x.user, options=opts)


@route("GET", "/v1/runs/{run_id}")
def get_run(x: Ctx, run_id: str) -> dict[str, Any]:
    return x.engine.view(run_id)


@route("GET", "/v1/runs/{run_id}/events")
def run_events(x: Ctx, run_id: str) -> dict[str, Any]:
    return {"events": x.engine.events(run_id, after=_int(x.query.get("after", "0")))}


@route("POST", "/v1/runs/{run_id}/next", "write")
def run_next(x: Ctx, run_id: str) -> dict[str, Any]:
    """The extension drives the browser: it sends what it observed and gets the next directive."""
    b = validate(x.body, {"observation": ("dict", True, 10)})
    obs = validate(b["observation"], {"snapshot": ("dict", False, 40), "result": ("dict", False, 30),
                                      "capabilities": ("dict", False, 10)})
    obs["capabilities"] = {"isolated_contexts": False, **{k: bool(v) for k, v in (obs.get("capabilities") or {}).items()
                                                          if k in ("trusted_input",)}}
    return x.engine.next(run_id, obs)


@route("POST", "/v1/runs/{run_id}/confirm", "write")
def confirm(x: Ctx, run_id: str) -> dict[str, Any]:
    b = validate(x.body, {"preview_hash": ("str", True, 64)})
    return x.engine.confirm(run_id, b["preview_hash"], by=x.user, interface="extension")


@route("POST", "/v1/runs/{run_id}/pause", "write")
def pause(x: Ctx, run_id: str) -> dict[str, Any]:
    return x.engine.pause(run_id, by=x.user)


@route("POST", "/v1/runs/{run_id}/resume", "write")
def resume(x: Ctx, run_id: str) -> dict[str, Any]:
    b = validate(x.body or {}, {"manual_events": ("list", False, 50), "note": ("str", False, 500)})
    events = [e for e in b.get("manual_events") or [] if isinstance(e, dict)]
    return x.engine.resume(run_id, by=x.user, manual_events=events, note=b.get("note"))


@route("POST", "/v1/runs/{run_id}/stop", "write")
def stop(x: Ctx, run_id: str) -> dict[str, Any]:
    return x.engine.stop(run_id, by=x.user)


@route("POST", "/v1/runs/{run_id}/cancel-remaining", "write")
def cancel_remaining(x: Ctx, run_id: str) -> dict[str, Any]:
    return x.engine.cancel_remaining(run_id, by=x.user)


@route("POST", "/v1/runs/{run_id}/acknowledge", "write")
def acknowledge(x: Ctx, run_id: str) -> dict[str, Any]:
    return x.engine.acknowledge(run_id)


@route("POST", "/v1/runs/{run_id}/items/{seq}/skip", "write")
def skip(x: Ctx, run_id: str, seq: str) -> dict[str, Any]:
    return x.engine.skip_item(run_id, _int(seq), by=x.user)


# audit


@route("GET", "/v1/audit")
def audit(x: Ctx) -> dict[str, Any]:
    return {"audit": x.c.agent.audit.list(limit=min(2000, _int(x.query.get("limit", "200"))),
                                          target=x.query.get("target"))}


@route("GET", "/v1/audit/verify")
def audit_verify(x: Ctx) -> dict[str, Any]:
    return x.c.agent.audit.verify()


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise Refused(400, "FIELD_INVALID", "A number was expected.") from None


# ---- HTTP handler --------------------------------------------------------------------------------------------------


def make_handler(c: Companion) -> type[BaseHTTPRequestHandler]:
    port = int(c.config.get("port"))

    class Handler(BaseHTTPRequestHandler):
        server_version = "SkyGuardCompanion"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # path only, never bodies or headers
            log.info("%s %s", self.command, urlsplit(self.path).path)

        def _send(self, status: int, payload: dict[str, Any], origin: str | None = None) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802 - CORS preflight, for the extension's own origin only
            try:
                check_host(self.headers.get("Host"), port)
                origin = self.headers.get("Origin")
                check_origin(origin, c.config.extension_origins())
            except Refused as r:
                return self._send(r.status, {"ok": False, "error_code": r.code, "error": r.message})
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", origin or "")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-SG-Device, X-SG-Secret")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._dispatch("PUT")

        def do_PATCH(self) -> None:  # noqa: N802
            self._dispatch("PATCH")

        def do_DELETE(self) -> None:  # noqa: N802
            self._dispatch("DELETE")

        def _dispatch(self, method: str) -> None:
            url = urlsplit(self.path)
            origin = None
            try:
                check_host(self.headers.get("Host"), port)
                if method == "GET" and url.path == "/health":  # installer and prerequisite checker; nothing private
                    return self._send(200, {"ok": True, "name": NAME, "version": VERSION})
                origin = self.headers.get("Origin")
                extension_id = check_origin(origin, c.config.extension_origins())
                match = None
                for m, pattern, fn, access, bucket in ROUTES:
                    if m == method:
                        found = pattern.match(url.path)
                        if found:
                            match = (fn, access, bucket, found.groupdict())
                            break
                if not match:
                    raise Refused(404, "NOT_FOUND", "Unknown request.")
                fn, access, bucket, params = match
                c.limiter.check(extension_id, bucket)
                length = int(self.headers.get("Content-Length") or 0)
                limit = (AUDIO_LIMIT if url.path.endswith("/voice/transcribe") else
                         OBSERVATION_LIMIT if url.path.endswith("/next") or url.path.endswith("/events") else JSON_LIMIT)
                if length > limit:
                    raise Refused(413, "TOO_LARGE", "The request is too large.")
                raw = self.rfile.read(length) if length else b""
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                body: Any = None
                if method in ("POST", "PUT", "PATCH") and not url.path.endswith("/voice/transcribe"):
                    if raw and ctype != "application/json":
                        raise Refused(415, "JSON_REQUIRED", "Send JSON.")
                    try:
                        body = json.loads(raw.decode("utf-8")) if raw else {}
                    except (ValueError, UnicodeDecodeError):
                        raise Refused(400, "BODY_INVALID", "The request body is not valid JSON.") from None
                device = user = role = token = None
                if access != "public":
                    device = c.pairing.device(self.headers.get("X-SG-Device"), self.headers.get("X-SG-Secret"),
                                              extension_id)
                if access not in ("public", "device"):
                    auth = self.headers.get("Authorization") or ""
                    token = auth[7:] if auth.startswith("Bearer ") else None
                    session = c.users.session(token, device["id"])
                    user, role = session.username, session.role
                    if access == "write" and role not in WRITE_ROLES or access == "admin" and role != "admin":
                        raise Refused(403, "ROLE_REQUIRED", "Your companion role cannot do this.")
                    if role == "viewer" and method != "GET" and not url.path.startswith("/v1/auth/"):
                        raise Refused(403, "ROLE_REQUIRED", "Viewers can only look.")
                query = {k: v[0][:200] for k, v in parse_qs(url.query).items()}
                ctx = Ctx(c, body, query, user, role, device, extension_id, token, raw, ctype)
                result = fn(ctx, **params)
                return self._send(200, {"ok": True, **(result or {})}, origin)
            except Refused as r:
                return self._send(r.status, {"ok": False, "error_code": r.code, "error": r.message}, origin)
            except AgentError as e:
                return self._send(e.status, {"ok": False, "error_code": e.code, "error": e.message,
                                             "details": e.details}, origin)
            except Exception as exc:  # noqa: BLE001 - never echo internals (could contain page data)
                log.error("request failed: %s %s: %s", method, url.path, redact_text(type(exc).__name__))
                log.debug("details", exc_info=True)
                return self._send(500, {"ok": False, "error_code": "INTERNAL", "error": "The companion could not "
                                        "complete this request; see the companion log."}, origin)

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def serve(c: Companion, *, port: int | None = None, ready: threading.Event | None = None) -> Server:
    port = int(port or c.config.get("port"))
    c.config.data["port"] = port
    httpd = Server(("127.0.0.1", port), make_handler(c))
    if ready:
        ready.set()
    return httpd


def setup_logging(paths: Paths) -> None:
    handler = logging.handlers.RotatingFileHandler(paths.logs / "companion.log", maxBytes=2_000_000, backupCount=3,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
