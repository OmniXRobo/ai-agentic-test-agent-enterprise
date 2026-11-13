"""The execution engine: PARSE -> PLAN -> INSPECT -> VALIDATE -> PREVIEW -> CONFIRM -> EXECUTE -> VERIFY.

It is a step machine that never touches a browser itself. A *driver* (SkyGuard's Playwright driver, or the extension's
service worker + content script) sends an observation (a page snapshot and the result of the last action) and gets
back the next directive:

    {"action": {"type": "navigate" | "fill" | "select" | "check" | "click" | "snapshot" | "switch_context", ...}}
    {"wait": "confirmation" | "paused" | "stopped", ...}      {"done": {...summary...}}

Everything is checkpointed in the knowledge database after every call: the stage, record, step and sub-phase, the
verified session, the pause and what was pending. A browser hiccup, a manual intervention or a restart of SkyGuard or
the companion resumes from the checkpoint; a record whose submit may have happened is ``unknown`` until the application
itself shows whether it exists, so nothing is ever submitted twice blindly.

Item outcomes: confirmed_success, confirmed_failure, pending, skipped, manual_required, unknown (never "success"
without application evidence: a success message matching the learned pattern and/or the record found in the
application's own list).
"""

from __future__ import annotations

import re
import threading
from typing import Any
from urllib.parse import quote, urljoin

from . import authz, guard, locator, rules as rule_engine, sessions
from .knowledge import KnowledgeBase, origin_of
from .planner import build_preview, rules_for
from .store import Store
from .util import AgentError, dumps, loads, norm, now_iso

FINAL = {"completed", "partial", "failed", "stopped", "cancelled"}
ITEM_FINAL = {"confirmed_success", "confirmed_failure", "skipped"}
MAX_HEAL_WAITS = 2
MAX_VERIFY_WAITS = 3
MAX_PAGES = 20
STATUS_TEXT = {"confirmed_success": "Confirmed success", "confirmed_failure": "Confirmed failure", "pending": "Pending",
               "skipped": "Skipped", "manual_required": "Manual intervention required",
               "unknown": "Unknown / unverified", "running": "Running"}


class Engine:
    def __init__(self, store: Store, kb: KnowledgeBase, audit: Any) -> None:
        self.store = store
        self.kb = kb
        self.audit = audit
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self.listeners: list[Any] = []  # called with (run_id, event dict) for live updates / notifications
        self.learning: Any = None  # set by the agent: automatic discovery runs in this engine too

    def _resolve(self, app_id: str, screen: str, label: str, snap: dict[str, Any], action: str,
                 fallback: dict[str, Any] | None = None) -> locator.Resolution:
        return resolve_with_memory(self.kb, app_id, screen, label, snap, action, fallback)

    # ---- persistence ---------------------------------------------------------------------------------------------

    def _lock(self, run_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(run_id, threading.Lock())

    def run(self, run_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
        if not row:
            raise AgentError("RUN_NOT_FOUND", "That execution does not exist.", 404)
        for k in ("request", "plan", "preview", "checkpoint", "summary"):
            row[k] = loads(row[k])
        return row

    def items(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.store.query("SELECT * FROM run_items WHERE run_id = ? ORDER BY seq", (run_id,))
        for r in rows:
            r["record"] = loads(r["record"])
            r["validation"] = loads(r["validation"], [])
            r["result"] = loads(r["result"])
            r["status_text"] = STATUS_TEXT.get(r["status"], r["status"])
        return rows

    def events(self, run_id: str, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.store.query("SELECT * FROM run_events WHERE run_id = ? AND id > ? ORDER BY id LIMIT ?",
                                (run_id, after, limit))
        for r in rows:
            r["details"] = loads(r["details"])
        return rows

    def runs(self, limit: int = 50, app_id: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT id, application_id, workflow_key, interface, status, summary, created_by, created_at, " \
                      "updated_at, finished_at FROM runs", []
        if app_id:
            sql += " WHERE application_id = ?"
            params.append(app_id)
        rows = self.store.query(sql + " ORDER BY created_at DESC LIMIT ?", [*params, max(1, min(limit, 500))])
        for r in rows:
            r["summary"] = loads(r["summary"])
        return rows

    def _save(self, run: dict[str, Any], **fields: Any) -> None:
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        vals = [dumps(v) if isinstance(v, (dict, list)) else v for v in fields.values()]
        self.store.execute(f"UPDATE runs SET {cols} WHERE id = ?", [*vals, run["id"]])
        run.update(fields)

    def _item(self, run_id: str, seq: int, **fields: Any) -> None:
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        vals = [dumps(v) if isinstance(v, (dict, list)) else v for v in fields.values()]
        self.store.execute(f"UPDATE run_items SET {cols} WHERE run_id = ? AND seq = ?", [*vals, run_id, seq])

    def _event(self, run_id: str, kind: str, message: str, **details: Any) -> None:
        from .secrets_guard import redact

        safe = redact(details, self.kb.vault.known_values())
        cur = self.store.execute("INSERT INTO run_events (run_id, at, kind, message, details) VALUES (?,?,?,?,?)",
                                 (run_id, now_iso(), kind, message[:500], dumps(safe)))
        evt = {"id": cur.lastrowid, "run_id": run_id, "kind": kind, "message": message, "details": safe}
        for fn in list(self.listeners):
            try:
                fn(run_id, evt)
            except Exception:
                pass

    # ---- creating a run: INSPECT -> VALIDATE -> PREVIEW ---------------------------------------------------------

    def create(self, *, app_id: str, stages: list[dict[str, Any]], records: list[dict[str, Any]], interface: str,
               by: str | None, request: dict[str, Any] | None = None, options: dict[str, Any] | None = None,
               warnings: list[str] | None = None) -> dict[str, Any]:
        """Stages: [{"workflow": key, "account_id": id}] in order. Records: one dict of parameter values each."""
        app = self.kb.application(app_id)
        if not stages:
            raise AgentError("PLAN_EMPTY", "Choose the workflow to run.")
        if not records:
            raise AgentError("PLAN_EMPTY", "There are no records to process.")
        plan_stages = []
        for i, s in enumerate(stages):
            wf = self.kb.approved(app["id"], "workflow", s["workflow"])
            if not wf:
                raise AgentError("WORKFLOW_NOT_APPROVED", f"The workflow '{s['workflow']}' has no approved version. "
                                 "Review and approve it in Knowledge first.", 409)
            acc = self.kb.account(s["account_id"]) if s.get("account_id") else None
            if not acc or acc["application_id"] != app["id"]:
                raise AgentError("ACCOUNT_REQUIRED", f"Choose the account for '{wf['data'].get('title') or wf['key']}'.")
            if not acc["has_credential"] and not (app.get("profile") or {}).get("login", {}).get("manual"):
                raise AgentError("CREDENTIAL_MISSING", f"{acc['label']} has no saved credential. Add it through the "
                                 "secure credential form first.", 409)
            profile = app.get("profile") or {}
            allowed = {sessions.role_key(r, profile) for r in wf["data"].get("roles") or [] if r}
            known = sessions.role_key(acc.get("verified_role") if acc.get("auth_status") == "verified"
                                      else acc.get("role_key"), profile)
            if allowed and known and known not in allowed:
                role_text = acc.get("verified_role") or acc.get("role_key")
                raise AgentError("ROLE_NOT_ALLOWED", f"{acc['label']} has the role '{role_text}', but "
                                 f"'{wf['data'].get('title') or wf['key']}' is approved for: "
                                 f"{', '.join(sorted(allowed))}. Choose an account with one of those roles.", 409)
            plan_stages.append({
                "index": i, "workflow": wf["key"], "workflow_entry_id": wf["id"], "workflow_version": wf["version"],
                "title": wf["data"].get("title") or wf["key"], "account_id": acc["id"], "account": acc["label"],
                "login_id": acc["login_id"], "role_claimed": acc.get("role_key") or acc.get("role_description"),
                "role_verified": acc.get("verified_role") if acc.get("auth_status") == "verified" else None,
                "commits": bool(wf["data"].get("commits", True)), "page": wf["data"].get("start_url"),
                "expected_status": wf["data"].get("expected_status"),
            })
        first_wf = self.kb.entry(plan_stages[0]["workflow_entry_id"])
        needs_inspection = any(first_wf["data"].get(k) for k in ("exists_check", "bookings_source"))
        run_id = f"OPS-{now_iso()[:19].replace('-', '').replace(':', '').replace('T', '-')}-{_rand()}"
        now = now_iso()
        opts = {"on_error": "continue" if len(records) > 1 else "pause", **(options or {})}
        plan = {"app_id": app["id"], "app_name": app["name"], "stages": plan_stages, "options": opts,
                "mode": app.get("mode", "testing"), "warnings": warnings or []}
        checkpoint = {"phase": "inspect" if needs_inspection else "preview", "stage": 0, "item": 0, "step": 0,
                      "sub": "session", "session": {}, "inspection": {"keys": {}, "bookings": [], "done": False}}
        self.store.execute("INSERT INTO runs (id, application_id, workflow_key, interface, status, request, plan, "
                           "checkpoint, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (run_id, app["id"], plan_stages[0]["workflow"], interface,
                            "inspecting" if needs_inspection else "planning", dumps(request or {}), dumps(plan),
                            dumps(checkpoint), by, now, now))
        for i, rec in enumerate(records, start=1):
            self.store.execute("INSERT INTO run_items (run_id, seq, status, record, updated_at) VALUES (?,?,?,?,?)",
                               (run_id, i, "pending", dumps(rec), now))
        self._event(run_id, "plan", f"Planned {len(records)} record(s) for {app['name']}: "
                                    + " -> ".join(s["title"] for s in plan_stages))
        self.audit.record("run.planned", actor=by, interface=interface, target=run_id,
                          details={"application": app["name"], "stages": [s["title"] for s in plan_stages],
                                   "records": len(records)})
        run = self.run(run_id)
        if not needs_inspection:
            self._make_preview(run)
        return self.view(run_id)

    def _make_preview(self, run: dict[str, Any]) -> None:
        plan, cp = run["plan"], run["checkpoint"]
        app = self.kb.application(plan["app_id"])
        wf = self.kb.entry(plan["stages"][0]["workflow_entry_id"])
        rules = rules_for(self.kb, app["id"], wf)
        items = self.items(run["id"])
        records = [i["record"] for i in items]
        insp = cp.get("inspection") or {}
        findings = rule_engine.check_batch(records, rules, existing_keys={k: set(v) for k, v in
                                                                          (insp.get("keys") or {}).items()},
                                           existing_bookings=insp.get("bookings") or [],
                                           options=_options(self.kb, app["id"], wf))
        warnings = list(plan.get("warnings") or [])
        if not insp.get("done") and (wf["data"].get("exists_check") or wf["data"].get("bookings_source")):
            warnings.append("The application could not be inspected before the preview; duplicates and conflicts "
                            "are checked again against the application before every submission.")
        if any(not s.get("role_verified") for s in plan["stages"]):
            warnings.append("Roles not yet verified are verified from the application at sign-in; a stage stops if "
                            "the verified role is not allowed.")
        for i, item in enumerate(items):
            self._item(run["id"], item["seq"], validation=findings.get(i, []))
        insp_view = {"done": bool(insp.get("done")), "existing_keys": sum(len(v) for v in (insp.get("keys") or {}).values()),
                     "existing_bookings": len(insp.get("bookings") or [])}
        preview = build_preview(app=app, stages=plan["stages"], records=records, findings=findings, rules=rules,
                                inspection=insp_view, warnings=warnings)
        cp.update(phase="confirm")
        self._save(run, status="awaiting_confirmation", preview=preview, preview_hash=preview["hash"], checkpoint=cp,
                   confirmed_at=None, confirmed_by=None)
        self._event(run["id"], "preview", f"Preview ready: {preview['totals']['ready']} of "
                                          f"{preview['totals']['records']} record(s) ready, "
                                          f"{preview['totals']['blocked']} blocked by rules. Waiting for confirmation.")

    def confirm(self, run_id: str, preview_hash: str, by: str | None, interface: str | None = None) -> dict[str, Any]:
        with self._lock(run_id):
            run = self.run(run_id)
            if run["status"] != "awaiting_confirmation":
                raise AgentError("NOT_AWAITING_CONFIRMATION", "This execution is not waiting for confirmation.", 409)
            if not preview_hash or preview_hash != run["preview_hash"]:
                raise AgentError("PREVIEW_CHANGED", "The preview changed since you looked at it. Review the new preview "
                                 "and confirm again.", 409)
            if run["preview"]["totals"]["ready"] == 0:
                raise AgentError("NOTHING_TO_RUN", "Every record is blocked by a business rule; nothing would run.", 409)
            cp = run["checkpoint"]
            cp.update(phase="execute", stage=0, item=0, step=0, sub="session", pause=None)
            for item in self.items(run_id):  # records blocked in the preview are not executed
                if rule_engine.blocking(item["validation"]):
                    self._item(run_id, item["seq"], status="skipped",
                               result={"reason": "; ".join(f["message"] for f in item["validation"] if f["enforced"])})
            now = now_iso()
            self._save(run, status="running", confirmed_by=by, confirmed_at=now, checkpoint=cp,
                       summary={"confirmed_hash": preview_hash})
            self._event(run_id, "confirm", f"Confirmed by {by or 'the user'}; execution starts.")
            self.audit.record("run.confirmed", actor=by, interface=interface, target=run_id,
                              details={"preview_hash": preview_hash, "records": run["preview"]["totals"]})
        return self.view(run_id)

    # ---- control -------------------------------------------------------------------------------------------------

    def pause(self, run_id: str, by: str | None = None) -> dict[str, Any]:
        with self._lock(run_id):
            run = self.run(run_id)
            if run["status"] not in ("running", "inspecting"):
                raise AgentError("NOT_RUNNING", "Only a running execution can be paused.", 409)
            cp = run["checkpoint"]
            cp["pause"] = {"code": "user_pause", "kind": "user", "message": "Paused by the user. The browser session "
                           "and checkpoint are kept; press Resume to continue.", "at": now_iso(),
                           "resume_status": run["status"]}
            self._save(run, status="paused", checkpoint=cp)
            self._event(run_id, "pause", "Paused by the user.")
        return self.view(run_id)

    def resume(self, run_id: str, by: str | None = None, manual_events: list[dict[str, Any]] | None = None,
               note: str | None = None, choice: int | None = None) -> dict[str, Any]:
        with self._lock(run_id):
            run = self.run(run_id)
            if run["status"] != "paused":
                raise AgentError("NOT_PAUSED", "This execution is not paused.", 409)
            cp = run["checkpoint"]
            pause = cp.get("pause") or {}
            if choice is not None:
                options = pause.get("choices") or []
                if not 0 <= choice < len(options):
                    raise AgentError("CHOICE_INVALID", "That choice is not one of the controls I listed.", 400)
                # used once, for this step only; not remembered as the control's location for other records
                cp["choice"] = {"target": pause.get("target"), "fingerprint": options[choice]["fingerprint"],
                                "label": options[choice]["label"]}
                manual_events = []
            cp["resumed"] = {**pause, "manual_events": [_safe_event(e) for e in (manual_events or [])[:50]],
                             "note": (note or "")[:500], "by": by}
            cp["pause"] = None
            cp["heal_waits"] = 0
            status = pause.get("resume_status") or "running"
            self._save(run, status=status, checkpoint=cp)
            self._event(run_id, "resume", f"Resumed by {by or 'the user'}"
                        + (f" after {len(manual_events or [])} manual action(s)." if manual_events else "."))
            self.audit.record("run.resumed", actor=by, target=run_id, details={"after": pause.get("code")})
        return self.view(run_id)

    def acknowledge(self, run_id: str) -> dict[str, Any]:
        with self._lock(run_id):
            run = self.run(run_id)
            cp = run["checkpoint"]
            if cp.get("pause"):
                cp["pause"]["acknowledged"] = True
                self._save(run, checkpoint=cp)
        return self.view(run_id)

    def stop(self, run_id: str, by: str | None = None) -> dict[str, Any]:
        with self._lock(run_id):
            run = self.run(run_id)
            if run["status"] in FINAL:
                return self.view(run_id)
            self._finish(run, "stopped", f"Stopped by {by or 'the user'}. No further actions are taken.")
            self.audit.record("run.stopped", actor=by, target=run_id)
        return self.view(run_id)

    def cancel_remaining(self, run_id: str, by: str | None = None) -> dict[str, Any]:
        with self._lock(run_id):
            run = self.run(run_id)
            n = 0
            cp = run["checkpoint"]
            for item in self.items(run_id):
                if item["status"] == "pending" and not (run["status"] == "running" and item["seq"] - 1 == cp.get("item")
                                                        and cp.get("sub") in ("steps", "verify")):
                    self._item(run_id, item["seq"], status="skipped", result={"reason": "cancelled by the user"})
                    n += 1
            self._event(run_id, "cancel", f"{n} remaining record(s) cancelled by {by or 'the user'}.")
            self.audit.record("run.cancel_remaining", actor=by, target=run_id, details={"records": n})
        return self.view(run_id)

    def skip_item(self, run_id: str, seq: int, by: str | None = None) -> dict[str, Any]:
        with self._lock(run_id):
            run = self.run(run_id)
            item = next((i for i in self.items(run_id) if i["seq"] == seq), None)
            if not item:
                raise AgentError("ITEM_NOT_FOUND", "That record does not exist.", 404)
            if item["status"] == "unknown":
                raise AgentError("ITEM_UNKNOWN", "This record may already have been submitted. Resume so the agent "
                                 "checks the application first.", 409)
            self._item(run_id, seq, status="skipped", result={**item["result"], "reason": f"skipped by {by or 'user'}"})
            cp = run["checkpoint"]
            if cp.get("item") == seq - 1:
                cp.update(item=seq, step=0, sub="session")
                self._save(run, checkpoint=cp)
            self._event(run_id, "skip", f"Record {seq} skipped by the user.")
        return self.view(run_id)

    def recover_interrupted(self) -> int:
        """At start-up: executions that were running when the program stopped wait for the user (status paused)."""
        n = 0
        for row in self.store.query("SELECT id FROM runs WHERE status IN ('running', 'inspecting')"):
            run = self.run(row["id"])
            cp = run["checkpoint"]
            cp["pause"] = {"code": "interrupted", "kind": "manual", "at": now_iso(), "resume_status": run["status"],
                           "message": "The agent was restarted while this execution was running. Open the "
                                      "application page again and press Resume: the agent checks the application "
                                      "before continuing, so nothing is submitted twice."}
            if cp.get("sub") in ("steps", "verify", "verify_lookup"):
                cp["sub"] = "precheck"  # re-check the record in the application before anything else
                cp["step"] = 0
            cp["session"] = {}
            cp["awaiting"] = None
            self._save(run, status="paused", checkpoint=cp)
            self._event(run["id"], "pause", cp["pause"]["message"])
            n += 1
        return n

    # ---- the step machine ----------------------------------------------------------------------------------------

    def next(self, run_id: str, observation: dict[str, Any] | None) -> dict[str, Any]:
        with self._lock(run_id):
            run = self.run(run_id)
            obs = observation or {}
            if run["status"] in FINAL:
                return {"done": self.view_summary(run)}
            if run["status"] == "awaiting_confirmation":
                return {"wait": "confirmation", "message": "Waiting for you to confirm the preview."}
            if run["status"] == "planning":
                return {"wait": "planning"}
            if run["status"] == "paused":
                return {"wait": "paused", "pause": run["checkpoint"].get("pause")}
            try:
                directive = self._step(run, obs)
            except AgentError as exc:
                directive = self._pause(run, "agent_error", exc.message)
            except Exception as exc:  # a defect must not lose the session: pause with the checkpoint kept
                directive = self._pause(run, "agent_error", f"Internal error: {type(exc).__name__}. The session and "
                                                            "checkpoint are kept.")
            public = dict(directive)
            act = public.get("action")
            if act:
                stored = {k: v for k, v in act.items() if k != "value"} if act.get("secret") else dict(act)
                run["checkpoint"]["awaiting"] = stored
                self._save(run, checkpoint=run["checkpoint"])
            public["status"] = self.status_line(run)
            return public

    def _step(self, run: dict[str, Any], obs: dict[str, Any]) -> dict[str, Any]:
        cp = run["checkpoint"]
        snap = obs.get("snapshot") or {}
        result = obs.get("result")
        caps = obs.get("capabilities") or {}
        cp["caps"] = caps
        plan = run["plan"]
        app = self.kb.application(plan["app_id"])
        if snap.get("url"):
            cp["page"] = {"url": snap.get("url"), "title": snap.get("title"), "heading": snap.get("heading")}
            if guard.flag_page(snap) and not cp.get("injection_warned"):
                cp["injection_warned"] = True
                self._event(run["id"], "security", "The page contains instruction-like text. It is treated as data "
                                                   "and never followed.")
            if not _url_allowed(snap["url"], app):
                return self._pause(run, "left_application", f"The browser is on {origin_of(snap['url'])}, which is "
                                   "not part of the registered application. I stopped acting. Go back to the "
                                   "application and press Resume.")
        if obs.get("error"):
            return self._pause(run, "browser_error", f"The browser reported a problem: {str(obs['error'])[:200]}. "
                               "The session is kept; press Resume when the page is back.")
        if cp.get("resumed"):
            handled = self._after_resume(run, cp, snap)
            if handled:
                return handled
        awaiting = cp.get("awaiting") or {}
        cp["awaiting"] = None
        if awaiting and result is not None and not result.get("ok", False):
            handled = self._action_failed(run, cp, awaiting, result, snap)
            if handled:
                return handled
        if cp["phase"] == "discover" and self.learning is not None:
            return self.learning.discovery_step(run, cp, snap, app)
        if cp["phase"] == "inspect":
            return self._inspect(run, cp, snap, app)
        if cp["phase"] == "execute":
            return self._execute(run, cp, snap, app)
        return {"wait": cp["phase"]}

    # ---- inspection (read-only) ----------------------------------------------------------------------------------

    def _inspect(self, run: dict[str, Any], cp: dict[str, Any], snap: dict[str, Any], app: dict[str, Any]) -> dict[str, Any]:
        stage = run["plan"]["stages"][0]
        session = self._ensure_session(run, cp, snap, app, stage)
        if session is not None:
            return session
        wf = self.kb.entry(stage["workflow_entry_id"]).get("data")
        insp = cp.setdefault("inspection", {"keys": {}, "bookings": [], "done": False})
        sources = []
        if wf.get("exists_check", {}).get("list_url"):
            sources.append(("keys", wf["exists_check"]))
        if wf.get("bookings_source", {}).get("url"):
            sources.append(("bookings", wf["bookings_source"]))
        idx = insp.get("source", 0)
        while idx < len(sources):
            kind, src = sources[idx]
            url = src.get("list_url") or src.get("url")
            rows = self._lookup(run, cp, snap, app, url, src.get("column"))
            if isinstance(rows, dict):
                return rows  # a directive (navigate / next page / pause)
            if kind == "keys":
                param, column = src.get("param"), src.get("column")
                insp["keys"][param] = sorted({r.get(column, "") for r in rows if r.get(column)})
            else:
                cols = src.get("columns") or {}
                insp["bookings"] = [{param: r.get(col, "") for param, col in cols.items()} for r in rows]
            idx += 1
            insp["source"] = idx
            snap = {}
        insp["done"] = True
        self._event(run["id"], "inspect", f"Inspected the application: {sum(len(v) for v in insp['keys'].values())} "
                                          f"existing key(s), {len(insp['bookings'])} existing assignment(s).")
        self._make_preview(run)
        return {"wait": "confirmation", "message": "Preview ready. Waiting for your confirmation."}

    # ---- execution -----------------------------------------------------------------------------------------------

    def _execute(self, run: dict[str, Any], cp: dict[str, Any], snap: dict[str, Any], app: dict[str, Any]) -> dict[str, Any]:
        plan = run["plan"]
        if not run.get("summary", {}).get("execution_started_at"):
            run["summary"] = {**run.get("summary", {}), "execution_started_at": now_iso()}
            self._save(run, summary=run["summary"])
        items = self.items(run["id"])
        while True:
            if cp["stage"] >= len(plan["stages"]):
                return self._complete(run)
            stage = plan["stages"][cp["stage"]]
            if cp["item"] >= len(items):
                cp.update(stage=cp["stage"] + 1, item=0, step=0, sub="session")
                if cp["stage"] < len(plan["stages"]):
                    self._event(run["id"], "stage", f"Stage {cp['stage'] + 1}: "
                                                    f"{plan['stages'][cp['stage']]['title']} as "
                                                    f"{plan['stages'][cp['stage']]['account']}.")
                continue
            item = items[cp["item"]]
            stage_status = (item["result"].get("stages") or {}).get(str(cp["stage"]))
            prior_ok = cp["stage"] == 0 or (item["result"].get("stages") or {}).get(str(cp["stage"] - 1)) == "confirmed_success"
            if item["status"] in ("skipped", "confirmed_failure") or stage_status in ITEM_FINAL or not prior_ok \
                    or (item["status"] == "pending" and item["result"].get("blocked")):
                if not prior_ok and cp["stage"] > 0 and stage_status is None and item["status"] not in ("skipped",):
                    self._stage_result(run, item, cp["stage"], "skipped", "the earlier stage did not complete")
                cp.update(item=cp["item"] + 1, step=0, sub="session")
                items = self.items(run["id"])
                continue
            break
        wf_entry = self.kb.entry(stage["workflow_entry_id"])
        current = self.kb.approved(app["id"], "workflow", stage["workflow"])
        if not current or current["id"] != wf_entry["id"]:
            return self._reconfirm(run, "The workflow knowledge changed after the preview (a newer version or a "
                                        "revalidation). Review the new preview and confirm again.")
        wf = wf_entry["data"]
        record = item["record"]
        if item["status"] == "pending" and cp["sub"] == "session":
            self._item(run["id"], item["seq"], status="running")
        # 1 session
        if cp["sub"] == "session":
            d = self._ensure_session(run, cp, snap, app, stage)
            if d is not None:
                return d
            cp["sub"] = "precheck"
        # 2 precheck: duplicate prevention / already done / target exists, plus fresh rule check
        if cp["sub"] == "precheck":
            d = self._precheck(run, cp, snap, app, stage, wf, item)
            if d is not None:
                return d
        # 3 steps
        if cp["sub"] == "steps":
            d = self._steps(run, cp, snap, app, stage, wf, item, wf_entry)
            if d is not None:
                return d
        # 4 verify
        if cp["sub"] in ("verify", "verify_lookup"):
            d = self._verify(run, cp, snap, app, stage, wf, item)
            if d is not None:
                return d
        return self._execute(run, cp, {}, app)

    # -- sessions --

    def _ensure_session(self, run: dict[str, Any], cp: dict[str, Any], snap: dict[str, Any], app: dict[str, Any],
                        stage: dict[str, Any]) -> dict[str, Any] | None:
        """None when the right account is signed in and verified; otherwise the next directive."""
        sess = sessions.SessionState(cp.get("session"))
        profile = app.get("profile") or {}
        login = cp.setdefault("login", {})
        account = self.kb.account(stage["account_id"])
        if sess.account_id == stage["account_id"] and sess.verified and not login.get("phase"):
            if snap and sessions.logged_out(snap, profile):  # the application ended the session
                self._event(run["id"], "session", "The application session ended (sign-in page shown). Signing in "
                                                  "again with the same account.")
                sess.clear()
                cp["session"] = sess.to_dict()
                login.clear()
                if cp["phase"] == "execute" and cp.get("sub") == "steps":
                    cp["step"] = 0
                    cp["sub"] = "session"
            else:
                return None
        phase = login.get("phase")
        if not phase:
            if sess.account_id and sess.account_id != stage["account_id"]:
                if (cp.get("caps") or {}).get("isolated_contexts"):
                    login["phase"] = "login_start"
                    sess.clear()
                    cp["session"] = sess.to_dict()
                    self._event(run["id"], "session", f"Switching to an isolated browser session for {account['label']}.")
                    return {"action": {"type": "switch_context", "account_id": account["id"],
                                       "url": profile.get("login", {}).get("url") or app["base_url"]}}
                login["phase"] = "logout"
            else:
                login["phase"] = "login_start"
            phase = login["phase"]
        if phase == "logout":
            ctrl = (profile.get("logout") or {}).get("control")
            if snap and sessions.logged_out(snap, profile):
                login["phase"] = "login_start"
                sess.clear()
                cp["session"] = sess.to_dict()
                return self._ensure_session(run, cp, snap, app, stage)
            if not snap:
                return {"action": {"type": "snapshot"}}
            if ctrl:
                res = self._resolve(app["id"], "*", ctrl, snap, "click")
                if res.status == "found":
                    login["phase"] = "logout_verify"
                    self._event(run["id"], "session", f"Signing out of {self._acc_label(sess.account_id)}.")
                    return {"action": {"type": "click", "handle": res.element["h"], "target": ctrl}}
            if (profile.get("logout") or {}).get("url"):
                login["phase"] = "logout_verify"
                return {"action": {"type": "navigate", "url": profile["logout"]["url"]}}
            return self._pause(run, "logout_unknown", "I could not find how to sign out of the application. Please "
                               "sign out manually, then press Resume. I will verify that the session ended.")
        if phase == "logout_verify":
            if snap and sessions.logged_out(snap, profile):
                self._event(run["id"], "session", "Sign-out verified: the sign-in page is shown.")
                sess.clear()
                cp["session"] = sess.to_dict()
                login["phase"] = "login_start"
            else:
                waits = login.get("waits", 0)
                if waits < 2:
                    login["waits"] = waits + 1
                    return {"action": {"type": "snapshot", "wait_ms": 1000}}
                login["waits"] = 0
                return self._pause(run, "logout_unverified", "I could not verify that the previous account was "
                                   "signed out. Please sign out manually and press Resume.")
        lp = profile.get("login") or {}
        if login["phase"] == "login_start":
            login["waits"] = 0
            if lp.get("manual"):
                login["phase"] = "login_verify"
                return self._pause(run, "manual_login", f"Please sign in to {app['name']} as {account['label']} "
                                   f"({account['login_id']}) yourself, then press Resume. I will verify who is "
                                   "signed in.", kind="manual")
            if snap and (snap.get("auth") or {}).get("password_fields"):
                login["phase"] = "login_user"
            else:
                login["phase"] = "login_user"
                return {"action": {"type": "navigate", "url": lp.get("url") or app["base_url"]}}
        if login["phase"] == "login_user":
            if not snap:
                return {"action": {"type": "snapshot"}}
            res = self._resolve(app["id"], "login", lp.get("username") or "User name", snap, "fill",
                                fallback=_login_fallback(snap, "user"))
            if res.status != "found":
                return self._locate_problem(run, cp, res, lp.get("username") or "the user name field")
            login["phase"] = "login_pass"
            self._event(run["id"], "session", f"Signing in as {account['label']}.")
            return {"action": {"type": "fill", "handle": res.element["h"], "value": account["login_id"],
                               "target": lp.get("username") or "User name"}}
        if login["phase"] == "login_pass":
            res = self._resolve(app["id"], "login", lp.get("password") or "Password", snap, "fill",
                                fallback=_login_fallback(snap, "password"))
            if res.status != "found":
                return self._locate_problem(run, cp, res, lp.get("password") or "the password field")
            password = self.kb.credential_for(account["id"])
            if not password:
                return self._pause(run, "credential_missing", f"{account['label']} has no usable saved credential. "
                                   "Add it in Accounts (secure form), or sign in manually and press Resume.")
            login["phase"] = "login_submit"
            return {"action": {"type": "fill", "handle": res.element["h"], "value": password, "secret": True,
                               "target": lp.get("password") or "Password"}}
        if login["phase"] == "login_submit":
            res = self._resolve(app["id"], "login", lp.get("submit") or "Sign in", snap, "click",
                                fallback=_login_fallback(snap, "submit"))
            if res.status != "found":
                return self._locate_problem(run, cp, res, lp.get("submit") or "the sign-in button")
            login["phase"] = "login_verify"
            return {"action": {"type": "click", "handle": res.element["h"], "target": lp.get("submit") or "Sign in"}}
        if login["phase"] == "login_verify":
            challenge = sessions.auth_challenge(snap) if snap else None
            if challenge:
                msg = {"captcha": "The application shows a CAPTCHA. Please complete it yourself; I never solve "
                                  "CAPTCHAs. Press Resume when you are signed in.",
                       "driver_code": "Driver authentication code required. Please enter it directly into the "
                                      "application. I will not store or capture it. Press Resume when done.",
                       "otp": "Authentication required. Please complete the one-time code / MFA step yourself in "
                              "the application. I will not store or capture the code. Press Resume when done."}[challenge]
                return self._pause(run, f"auth_{challenge}", msg)
            if snap and sessions.logged_out(snap, profile):
                errors = [m["text"] for m in snap.get("messages", []) if m.get("kind") == "error"]
                waits = login.get("waits", 0)
                if not errors and waits < 2:
                    login["waits"] = waits + 1
                    return {"action": {"type": "snapshot", "wait_ms": 1000}}
                self.kb.mark_account(account["id"], auth_status="failed")
                login.clear()
                login["phase"] = "login_start"
                return self._pause(run, "login_failed", f"Sign-in as {account['label']} failed"
                                   + (f": the application says \"{errors[0][:120]}\"" if errors else "")
                                   + ". Check or replace the credential (Accounts), or sign in manually, then Resume.")
            if not snap:
                return {"action": {"type": "snapshot", "wait_ms": 500}}
            check = sessions.verify_identity(snap, profile, account)
            if check["identity"] == "unverified" and norm(account["login_id"]) in norm(snap.get("text")):
                check["identity"] = "verified"  # the application shows the login name on the signed-in page
            if check["identity"] == "mismatch":
                self.kb.mark_account(account["id"], auth_status="mismatch", identity=check["displayed_user"])
                login.clear()
                self._finish(run, "failed", f"The application shows a different signed-in user "
                             f"({check['displayed_user']}) than {account['label']} ({account['login_id']}). "
                             "Stopped: the agent never acts under another identity.")
                return {"done": self.view_summary(self.run(run["id"]))}
            role_ok = check["role_status"] == "verified"
            role = check["role"]
            if check["role_status"] == "unverified":
                probe = (profile.get("role_probes") or {}).get(norm(account.get("role_key")))
                if probe and not login.get("probe_done"):
                    login["phase"] = "role_probe"
                    return {"action": {"type": "navigate", "url": urljoin(app["base_url"], probe["url"])}}
            if check["role_status"] == "mismatch":
                self._event(run["id"], "session", f"The application shows the role '{check['displayed_role']}' for "
                                                  f"{account['label']}, not '{account.get('role_key')}'. The verified "
                                                  "role is used for authorization.")
                role_ok = True
            sess = sessions.SessionState({"account_id": account["id"], "identity": check["displayed_user"] or
                                          account["login_id"], "role": role, "verified": check["identity"] == "verified",
                                          "role_verified": role_ok, "context": account["id"]})
            cp["session"] = sess.to_dict()
            self.kb.mark_account(account["id"], auth_status="verified" if sess.verified else "signed_in_unverified",
                                 identity=sess.identity, role=role if role_ok else None)
            login.clear()
            self._event(run["id"], "session", f"Signed in as {account['label']}: identity "
                                              f"{'verified' if sess.verified else 'NOT verified'}, role "
                                              f"{role if role_ok else 'not verified'}.")
            self._stage_roles(run, stage, sess)
            return None
        if login["phase"] == "role_probe":
            probe = (profile.get("role_probes") or {}).get(norm(account.get("role_key"))) or {}
            res = self._resolve(app["id"], probe.get("screen", "*"), probe.get("control", ""), snap, "click")
            sess = sessions.SessionState(cp.get("session"))
            login["probe_done"] = True
            ok = res.status == "found"
            sess.account_id = account["id"]
            sess.identity = sess.identity or account["login_id"]
            sess.verified = True
            sess.role = norm(account.get("role_key")) if ok else None
            sess.role_verified = ok
            cp["session"] = sess.to_dict()
            self.kb.mark_account(account["id"], auth_status="verified", identity=sess.identity, role=sess.role)
            login.clear()
            self._event(run["id"], "session", f"Role {'verified' if ok else 'NOT verified'} by checking that "
                                              f"'{probe.get('control')}' is available to {account['label']}.")
            self._stage_roles(run, stage, sess)
            return None
        return {"action": {"type": "snapshot"}}

    def _stage_roles(self, run: dict[str, Any], stage: dict[str, Any], sess: sessions.SessionState) -> None:
        plan = run["plan"]
        for s in plan["stages"]:
            if s["account_id"] == sess.account_id:
                s["role_verified"] = sess.role if sess.role_verified else None
                s["identity_verified"] = sess.verified
        self._save(run, plan=plan)

    def _acc_label(self, acc_id: str | None) -> str:
        try:
            return self.kb.account(acc_id)["label"] if acc_id else "the previous account"
        except AgentError:
            return "the previous account"

    # -- table lookup with pagination (read-only) --

    def _lookup(self, run: dict[str, Any], cp: dict[str, Any], snap: dict[str, Any], app: dict[str, Any], url: str,
                column: str | None) -> list[dict[str, str]] | dict[str, Any]:
        lk = cp.get("lookup")
        target = urljoin(app["base_url"], url)
        if not lk or lk.get("url") != target:
            cp["lookup"] = {"url": target, "rows": [], "page": 1}
            return {"action": {"type": "navigate", "url": target}}
        if not snap:
            return {"action": {"type": "snapshot"}}
        table = _pick_table(snap, column)
        rows = [dict(zip(table["headers"], r)) for r in table["rows"]] if table else []
        lk["rows"].extend(rows)
        nxt = (snap.get("pagination") or {}).get("next")
        if nxt and rows and lk["page"] < MAX_PAGES:
            lk["page"] += 1
            return {"action": {"type": "click", "handle": nxt, "target": "next page"}}
        out = lk["rows"]
        cp["lookup"] = None
        return out

    # -- precheck --

    def _precheck(self, run: dict[str, Any], cp: dict[str, Any], snap: dict[str, Any], app: dict[str, Any],
                  stage: dict[str, Any], wf: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
        record = item["record"]
        ex = wf.get("exists_check") or {}
        bk = wf.get("bookings_source") or {}
        pc = cp.setdefault("pre", {})
        if ex.get("url") and "exists" not in pc:
            rows = self._lookup(run, cp, snap, app, _fill(ex["url"], record), ex.get("column"))
            if isinstance(rows, dict):
                return rows
            key = norm(record.get(ex.get("param")))
            match = next((r for r in rows if norm(r.get(ex.get("column"), "")) == key), None)
            pc["exists"] = bool(match)
            pc["row"] = match
            snap = {}
        if bk.get("url") and "bookings" not in pc and stage["index"] == 0:
            rows = self._lookup(run, cp, snap, app, _fill(bk["url"], record), None)
            if isinstance(rows, dict):
                return rows
            pc["bookings"] = [{p: r.get(c, "") for p, c in (bk.get("columns") or {}).items()} for r in rows]
            snap = {}
        cp["pre"] = {}
        exists, row = pc.get("exists"), pc.get("row")
        attempted = item["attempts"] > 0 or item["status"] == "unknown"
        kind = wf.get("target_kind") or ("existing" if wf.get("target") else "new")
        expected_status = wf.get("expected_status")
        status_col = (wf.get("target") or {}).get("status_column") or wf.get("status_column")
        if ex.get("url") and kind == "new" and exists:
            if attempted:
                self._stage_result(run, item, stage["index"], "confirmed_success",
                                   "the record exists in the application (verified before any retry)", evidence={"row": row})
            else:
                self._stage_result(run, item, stage["index"], "skipped", "a record with this key already exists in "
                                   "the application (duplicate prevented)", evidence={"row": row})
            return self._next_item(run, cp)
        if ex.get("url") and kind == "existing":
            if not exists:
                self._stage_result(run, item, stage["index"], "pending", "the target record was not found in the "
                                   "application", blocked=True)
                return self._next_item(run, cp)
            if expected_status and status_col and norm(row.get(status_col)) == norm(expected_status):
                self._stage_result(run, item, stage["index"], "confirmed_success" if attempted else "skipped",
                                   f"the record already has the status {expected_status}", evidence={"row": row})
                return self._next_item(run, cp)
            pre_status = (wf.get("target") or {}).get("requires_status")
            if pre_status and status_col and norm(row.get(status_col)) != norm(pre_status):
                self._stage_result(run, item, stage["index"], "pending", f"the record's status is "
                                   f"'{row.get(status_col)}', the workflow needs '{pre_status}'", blocked=True)
                return self._next_item(run, cp)
        if item["status"] == "unknown" and not ex.get("url"):
            self._stage_result(run, item, stage["index"], "unknown", "the earlier submit could not be verified and "
                               "this workflow has no way to look the record up; it is not repeated")
            return self._next_item(run, cp)
        if stage["index"] == 0:  # business rules again, with what the application shows now
            rules = rules_for(self.kb, app["id"], self.kb.entry(stage["workflow_entry_id"]))
            items = self.items(run["id"])
            done = {i for i, it in enumerate(items) if (it["result"].get("stages") or {}).get("0") == "confirmed_success"}
            records = [it["record"] for it in items]
            insp = cp.get("inspection") or {}
            keys = {k: set(v) for k, v in (insp.get("keys") or {}).items()}
            if ex.get("param"):
                keys.setdefault(ex["param"], set())
                keys[ex["param"]] -= {norm(record.get(ex["param"]))}  # this record's own key was checked above
            bookings = pc.get("bookings") if pc.get("bookings") is not None else insp.get("bookings") or []
            findings = rule_engine.check_batch(records, rules, existing_keys=keys, existing_bookings=bookings,
                                               done=done, options=_options(self.kb, app["id"],
                                                                           self.kb.entry(stage["workflow_entry_id"])))
            mine = findings.get(item["seq"] - 1, [])
            if rule_engine.blocking(mine):
                self._stage_result(run, item, stage["index"], "pending", "; ".join(f["message"] for f in mine
                                                                                    if f["enforced"]), blocked=True)
                return self._next_item(run, cp)
        cp.update(sub="steps", step=0)
        return None

    # -- steps --

    def _steps(self, run: dict[str, Any], cp: dict[str, Any], snap: dict[str, Any], app: dict[str, Any],
               stage: dict[str, Any], wf: dict[str, Any], item: dict[str, Any], wf_entry: dict[str, Any]) -> dict[str, Any] | None:
        steps = wf.get("steps") or []
        record = item["record"]
        while cp["step"] < len(steps):
            step = steps[cp["step"]]
            act = step.get("action")
            if act == "navigate":
                url = urljoin(app["base_url"], _fill(step.get("url") or "", record))
                if not _url_allowed(url, app):
                    return self._pause(run, "url_refused", "The workflow points outside the registered application; "
                                       "it was not opened.")
                cp["step"] += 1
                cp["heal_waits"] = 0
                return {"action": {"type": "navigate", "url": url}}
            if not snap:
                return {"action": {"type": "snapshot", "wait_ms": int(step.get("wait_ms") or 0)}}
            if act == "wait":
                cp["step"] += 1
                return {"action": {"type": "snapshot", "wait_ms": min(int(step.get("ms") or 1000), 15000)}}
            if sessions.logged_out(snap, app.get("profile") or {}) and step.get("screen") != "login":
                cp["session"] = {}
                cp["sub"] = "session"
                cp["step"] = 0
                self._event(run["id"], "session", "The application signed the account out during the workflow; "
                                                  "signing in again and restarting this record from its first step.")
                return self._execute(run, cp, snap, app)
            value = None
            if act in ("fill", "select", "check"):
                value = _value_for(step, record)
                if value is None:
                    if step.get("optional"):
                        cp["step"] += 1
                        continue
                    return self._pause(run, "value_missing", f"No value for '{step.get('target')}' in record "
                                       f"{item['seq']}.")
            if act == "open_row":
                res = _row_control(snap, step, record)
                if res is None:
                    if cp.get("heal_waits", 0) < MAX_HEAL_WAITS:
                        cp["heal_waits"] = cp.get("heal_waits", 0) + 1
                        return {"action": {"type": "snapshot", "wait_ms": 1000}}
                    return self._pause(run, "target_not_found", f"I cannot find record "
                                       f"{record.get(step.get('param'))} with a '{step.get('control')}' control on "
                                       "this page. Please open it manually and press Resume.")
                if res == "ambiguous":
                    return self._pause(run, "ambiguous", f"More than one row matches "
                                       f"{record.get(step.get('param'))}. Please select the correct one manually.")
                cp["step"] += 1
                cp["heal_waits"] = 0
                commit = bool(step.get("commit"))
                if commit:
                    d = self._authorize(run, cp, stage, wf_entry, item)
                    if d:
                        return d
                return {"action": {"type": "click", "handle": res["h"], "target": step.get("control"),
                                   "commit": commit}}
            screen = step.get("screen") or "*"
            chosen = cp.pop("choice", None)
            if chosen and chosen.get("target") == (step.get("target") or "the control"):
                res = locator.pick(chosen["fingerprint"], snap, action=act or "click")
                self._event(run["id"], "manual", f"Using the control you chose: {chosen.get('label')}.")
            else:
                res = self._resolve(app["id"], screen, step.get("target") or "", snap, act or "click")
            if res.status != "found":
                return self._locate_problem(run, cp, res, step.get("target") or "the control",
                                            commit=bool(step.get("commit")))
            cp["heal_waits"] = 0
            known = bool(step.get("target")) and self.kb.locator(app["id"], screen, step["target"]) is not None
            if res.healed and known:
                self._event(run["id"], "heal", f"Self-healing: '{step.get('target')}' found by "
                                               f"{_TIER_TEXT.get(res.tier, 'context')} (confidence {res.confidence}).",
                            screen=screen, target=step.get("target"), confidence=res.confidence, tier=res.tier)
                run["summary"]["heals"] = run["summary"].get("heals", 0) + 1
            if step.get("target") and (res.healed or not known):
                # remembered only once the record is verified in the application
                pending = cp.get("heal_pending") or []
                pending = [p for p in pending if p["target"] != step["target"] or p["screen"] != screen]
                pending.append({"screen": screen, "target": step["target"], "healed": bool(res.healed and known),
                                "fingerprint": locator.fingerprint_of(res.element), "confidence": res.confidence})
                cp["heal_pending"] = pending
            cp["step"] += 1
            if step.get("commit"):
                d = self._authorize(run, cp, stage, wf_entry, item)
                if d:
                    cp["step"] -= 1
                    return d
                return {"action": {"type": "click", "handle": res.element["h"], "target": step.get("target"),
                                   "commit": True}}
            if act in ("fill", "select", "check"):
                return {"action": {"type": act, "handle": res.element["h"], "value": value,
                                   "target": step.get("target")}}
            return {"action": {"type": "click", "handle": res.element["h"], "target": step.get("target")}}
        if not stage.get("commits", True):
            self._stage_result(run, item, stage["index"], "confirmed_success", "read-only workflow completed")
            return self._next_item(run, cp)
        cp.update(sub="verify", verify_waits=0)
        return {"action": {"type": "snapshot", "wait_ms": 700}}

    def _authorize(self, run: dict[str, Any], cp: dict[str, Any], stage: dict[str, Any], wf_entry: dict[str, Any],
                   item: dict[str, Any]) -> dict[str, Any] | None:
        current = self.kb.approved(run["plan"]["app_id"], "workflow", stage["workflow"])
        problems = authz.check(plan=run["plan"], stage=stage, session=cp.get("session") or {}, workflow=current,
                               run={**run, "confirmed_hash": run.get("summary", {}).get("confirmed_hash"),
                                    "execution_started_at": run.get("summary", {}).get("execution_started_at")},
                               record=item["record"], mode=run["plan"].get("mode", "testing"))
        if problems:
            self.audit.record("authz.denied", target=run["id"], details={"record": item["seq"], "problems": problems})
            self._stage_result(run, item, stage["index"], "pending", "not authorized: " + problems[0], blocked=True)
            return self._pause(run, "not_authorized", "I did not submit: " + " ".join(problems), next_item=True)
        attempts = item["attempts"] + 1
        self._item(run["id"], item["seq"], status="unknown", attempts=attempts)
        self.audit.record("operation.submit", target=run["id"],
                          details={"record": item["seq"], "stage": stage["title"], "account": stage["account"],
                                   "attempt": attempts})
        self._event(run["id"], "submit", f"Submitting record {item['seq']} ({stage['title']}).")
        return None

    def _locate_problem(self, run: dict[str, Any], cp: dict[str, Any], res: locator.Resolution, what: str,
                        commit: bool = False) -> dict[str, Any]:
        waits = cp.get("heal_waits", 0)
        if waits < MAX_HEAL_WAITS and res.status in ("not_found", "blocked"):
            cp["heal_waits"] = waits + 1
            run["summary"]["heal_attempts"] = run["summary"].get("heal_attempts", 0) + 1
            self._event(run["id"], "heal", f"'{what}' not usable yet ({res.reason}); inspecting the page again "
                                           f"(attempt {waits + 1}).")
            return {"action": {"type": "snapshot", "wait_ms": 1000 * (waits + 1)}}
        cp["heal_waits"] = 0
        code = {"ambiguous": "ambiguous", "blocked": "control_disabled"}.get(res.status, "control_not_found")
        if code == "ambiguous" and res.candidates:
            # 23.6.0: ask in chat which one, naming each by its row / card, instead of "select manually"
            choices = [{"index": i, "label": f"{what} ({locator.context_of(c['element']) or 'option ' + str(i + 1)})",
                        "fingerprint": locator.fingerprint_of(c["element"])} for i, c in enumerate(res.candidates[:5])]
            listing = "; ".join(f"{i + 1}) {c['label']}" for i, c in enumerate(choices))
            return self._pause(run, code, f"I found {len(res.candidates)} controls that match '{what}': {listing}. "
                               "Tell me which one to use, or click it yourself in the browser and press Resume.",
                               target=what, candidates=res.to_dict()["candidates"], choices=choices)
        msg = {"ambiguous": f"Multiple matching controls found for '{what}'. Please select the correct one "
                            "manually. I will verify your action and continue.",
               "control_disabled": f"'{what}' is disabled on the page ({res.reason}). Please resolve it in the "
                                   "application and press Resume.",
               "control_not_found": f"Automation paused. I cannot locate '{what}'. Please select the correct control "
                                    "manually. I will verify your action and continue."}[code]
        return self._pause(run, code, msg, target=what, candidates=res.to_dict()["candidates"])

    # -- verification --

    def _verify(self, run: dict[str, Any], cp: dict[str, Any], snap: dict[str, Any], app: dict[str, Any],
                stage: dict[str, Any], wf: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
        record = item["record"]
        ver = wf.get("verify") or {}
        if cp["sub"] == "verify":
            if not snap:
                return {"action": {"type": "snapshot", "wait_ms": 500}}
            errors = [m["text"] for m in snap.get("messages", []) if m.get("kind") == "error"]
            invalid = [f"{e.get('label') or e.get('name') or e.get('nearby')}: {e.get('validation') or 'invalid'}"
                       for e in snap.get("elements", []) if e.get("invalid") and e.get("visible", True)]
            pattern = _fill(ver.get("success_pattern") or "", record, regex=True)
            text = " ".join([snap.get("text") or ""] + [m["text"] for m in snap.get("messages", [])])
            success_msg = bool(pattern) and re.search(pattern, text, re.I)
            if (errors or invalid) and not success_msg:
                detail = (errors or invalid)[0][:200]
                self._stage_result(run, item, stage["index"], "confirmed_failure",
                                   f"the application rejected it: {detail}", evidence={"messages": errors[:3],
                                                                                        "invalid": invalid[:5],
                                                                                        "url": snap.get("url")})
                self._clear_heal(cp)
                if run["plan"]["options"].get("on_error") == "pause":
                    cp.update(item=cp["item"] + 1, step=0, sub="session")
                    return self._pause(run, "application_rejected", f"{stage['title']} failed for record "
                                       f"{item['seq']} because the application rejected it: \"{detail}\". I am paused "
                                       "on this page. Correct the data and run the record again, or Resume to "
                                       "continue with the next record.")
                return self._next_item(run, cp)
            if success_msg and not ver.get("record_listed"):
                self._promote_heal(app["id"], cp.pop("heal_pending", None))
                self._stage_result(run, item, stage["index"], "confirmed_success", "the application confirmed it",
                                   evidence={"message": success_msg.group(0)[:200], "url": snap.get("url")})
                return self._next_item(run, cp)
            if ver.get("record_listed"):
                cp["verify_evidence"] = {"message": success_msg.group(0)[:200] if success_msg else None,
                                         "url": snap.get("url")}
                cp["sub"] = "verify_lookup"
                snap = {}
            else:
                waits = cp.get("verify_waits", 0)
                if waits < MAX_VERIFY_WAITS:
                    cp["verify_waits"] = waits + 1
                    return {"action": {"type": "snapshot", "wait_ms": 800}}
                self._stage_result(run, item, stage["index"], "unknown", "no success message or record was found "
                                   "after submitting; the result is unverified and the record is not repeated",
                                   evidence={"url": snap.get("url")})
                return self._next_item(run, cp)
        if cp["sub"] == "verify_lookup":
            rl = ver["record_listed"]
            rows = self._lookup(run, cp, snap, app, _fill(rl["url"], record), rl.get("column"))
            if isinstance(rows, dict):
                return rows
            key = norm(record.get(rl.get("param")))
            row = next((r for r in rows if norm(r.get(rl.get("column"), "")) == key), None)
            status_col = rl.get("status_column")
            expected = wf.get("expected_status")
            if row and (not expected or not status_col or norm(row.get(status_col)) == norm(expected)):
                self._promote_heal(app["id"], cp.pop("heal_pending", None))
                self._stage_result(run, item, stage["index"], "confirmed_success", "verified in the application's list",
                                   evidence={**(cp.get("verify_evidence") or {}), "row": row})
            elif row:
                self._stage_result(run, item, stage["index"], "confirmed_failure", f"the record's status is "
                                   f"'{row.get(status_col)}', expected '{expected}'", evidence={"row": row})
            else:
                self._stage_result(run, item, stage["index"], "unknown", "the record was not found in the "
                                   "application's list after submitting; not repeated",
                                   evidence=cp.get("verify_evidence") or {})
            self._clear_heal(cp)
            return self._next_item(run, cp)
        return None

    def _promote_heal(self, app_id: str, pending: Any) -> None:
        """After the record was verified: remember where each control was found (first use or self-healed)."""
        if isinstance(pending, dict):
            pending = [pending]
        for hp in pending or []:
            self.kb.remember_locator(app_id, hp["screen"], hp["target"], hp["fingerprint"],
                                     confidence=max(0.8, hp["confidence"]) if not hp.get("healed") else hp["confidence"],
                                     reason="self-healed and verified" if hp.get("healed") else "used and verified")

    @staticmethod
    def _clear_heal(cp: dict[str, Any]) -> None:
        cp.pop("heal_pending", None)

    def _stage_result(self, run: dict[str, Any], item: dict[str, Any], stage_index: int, status: str, reason: str,
                      evidence: dict[str, Any] | None = None, blocked: bool = False) -> None:
        result = dict(item["result"])
        stages = dict(result.get("stages") or {})
        stages[str(stage_index)] = status
        result["stages"] = stages
        result["reason"] = reason
        result["blocked"] = blocked
        if evidence:
            result.setdefault("evidence", {})[str(stage_index)] = evidence
        last = stage_index == len(run["plan"]["stages"]) - 1
        overall = status if (last or status != "confirmed_success") else "running"
        if status == "skipped" and stage_index > 0:
            overall = "skipped"
        self._item(run["id"], item["seq"], status=overall if overall != "running" else "pending", result=result)
        item["result"] = result
        item["status"] = overall
        stage = run["plan"]["stages"][stage_index]
        self._event(run["id"], "item", f"Record {item['seq']} - {stage['title']}: {STATUS_TEXT.get(status, status)}"
                                       f" ({reason}).", seq=item["seq"], status=status)

    def _next_item(self, run: dict[str, Any], cp: dict[str, Any]) -> dict[str, Any] | None:
        cp.update(item=cp["item"] + 1, step=0, sub="session", lookup=None, pre={})
        return None

    def _reconfirm(self, run: dict[str, Any], why: str) -> dict[str, Any]:
        plan = run["plan"]
        for s in plan["stages"]:
            wf = self.kb.approved(plan["app_id"], "workflow", s["workflow"])
            if wf:
                s.update(workflow_entry_id=wf["id"], workflow_version=wf["version"])
        self._save(run, plan=plan)
        self._event(run["id"], "reconfirm", why)
        cp = run["checkpoint"]
        self._make_preview(run)
        cp = self.run(run["id"])["checkpoint"]
        cp["phase"] = "execute"
        self._save(run, checkpoint=cp)
        return {"wait": "confirmation", "message": why}

    def _pause(self, run: dict[str, Any], code: str, message: str, *, kind: str = "manual",
               target: str | None = None, candidates: list[dict[str, Any]] | None = None,
               next_item: bool = False, choices: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        cp = run["checkpoint"]
        if next_item:
            cp.update(item=cp["item"] + 1, step=0, sub="session")
        cp["pause"] = {"code": code, "kind": kind, "message": message, "target": target,
                       "candidates": candidates or [], "at": now_iso(), "resume_status": run["status"]
                       if run["status"] in ("running", "inspecting") else "running",
                       "sub": cp.get("sub"), "step": cp.get("step"), "acknowledged": False,
                       "page": cp.get("page"), "choices": choices or []}
        self._save(run, status="paused", checkpoint=cp, summary=run.get("summary") or {})
        self._event(run["id"], "pause", message, code=code)
        self.audit.record("run.paused", target=run["id"], details={"code": code})
        return {"wait": "paused", "pause": cp["pause"]}

    def _after_resume(self, run: dict[str, Any], cp: dict[str, Any], snap: dict[str, Any]) -> dict[str, Any] | None:
        info = cp.pop("resumed")
        code = info.get("code")
        app = self.kb.application(run["plan"]["app_id"])
        events = info.get("manual_events") or []
        if code in ("control_not_found", "ambiguous", "control_disabled", "typing_not_accepted", "option_not_found",
                    "application_validation") and info.get("target"):
            steps = self._current_steps(run, cp)
            step = steps[cp["step"]] if cp.get("sub") == "steps" and cp["step"] < len(steps) else None
            want = step.get("action") if step else "click"
            if want == "click":  # the control the person clicked, not the form it submitted
                matching = ([e for e in events if e.get("element") and e.get("kind") == "click"]
                            or [e for e in events if e.get("element") and e.get("kind") == "submit"])
            else:
                matching = [e for e in events if e.get("element") and e.get("kind") in ("fill", "select", "check")]
            if step and matching:
                ev = matching[-1]
                fp = locator.fingerprint_of(ev["element"])
                self.kb.remember_locator(app["id"], step.get("screen") or "*", step.get("target") or info["target"], fp,
                                         confidence=0.9, reason="manual correction during execution")
                self._event(run["id"], "learn", f"Learned the new location of '{info['target']}' from your manual "
                                                "action.")
                if step.get("commit"):
                    item = self.items(run["id"])[cp["item"]]
                    stage = run["plan"]["stages"][cp["stage"]]
                    d = self._authorize(run, cp, stage, self.kb.entry(stage["workflow_entry_id"]), item)
                    if d:
                        return d
                    cp["step"] += 1
                    cp.update(sub="verify", verify_waits=0)
                    self._event(run["id"], "manual", "You performed the submit manually; verifying the result in the "
                                                     "application.")
                    return {"action": {"type": "snapshot", "wait_ms": 300}}
                cp["step"] += 1
                return None
            return None
        if code in ("auth_otp", "auth_captcha", "auth_driver_code", "manual_login"):
            cp.setdefault("login", {})["phase"] = "login_verify"
            return None
        if code in ("logout_unknown", "logout_unverified"):
            cp.setdefault("login", {})["phase"] = "logout_verify"
            return None
        if code == "login_failed":
            cp.setdefault("login", {})["phase"] = "login_verify" if snap and not sessions.logged_out(
                snap, app.get("profile") or {}) else "login_start"
            return None
        if code in ("application_rejected", "interrupted", "browser_error", "left_application", "agent_error"):
            if cp.get("sub") in ("steps", "verify", "verify_lookup") and cp["phase"] == "execute":
                cp.update(sub="precheck", step=0)  # look at the application before touching anything again
            cp["lookup"] = None
            return None
        return None

    def _current_steps(self, run: dict[str, Any], cp: dict[str, Any]) -> list[dict[str, Any]]:
        stage = run["plan"]["stages"][min(cp["stage"], len(run["plan"]["stages"]) - 1)]
        return self.kb.entry(stage["workflow_entry_id"])["data"].get("steps") or []

    def _action_failed(self, run: dict[str, Any], cp: dict[str, Any], awaiting: dict[str, Any],
                       result: dict[str, Any], snap: dict[str, Any]) -> dict[str, Any] | None:
        err = result.get("error") or "FAILED"
        target = awaiting.get("target") or "the control"
        if awaiting.get("type") == "navigate":
            return self._pause(run, "navigation_failed", f"The page could not be opened ({result.get('message') or err}). "
                               "The session is kept; press Resume to try again.")
        if err in ("STALE", "HIDDEN", "TIMEOUT") and cp.get("heal_waits", 0) < MAX_HEAL_WAITS:
            cp["heal_waits"] = cp.get("heal_waits", 0) + 1
            self._rewind(cp, awaiting)
            self._event(run["id"], "heal", f"'{target}' changed while acting ({err}); inspecting again.")
            return {"action": {"type": "snapshot", "wait_ms": 800}}
        if awaiting.get("commit"):
            # the click on submit failed before it happened: nothing was sent, so the record is not unknown
            item = self.items(run["id"])[cp["item"]]
            self._item(run["id"], item["seq"], status="running", attempts=max(0, item["attempts"] - 1))
        self._rewind(cp, awaiting)
        if err in ("VALUE_NOT_ACCEPTED",) or result.get("invalid"):
            return self._pause(run, "application_validation", f"The application did not accept the value for "
                               f"'{target}'" + (f": {result.get('validation')}" if result.get("validation") else "")
                               + ". Please correct it manually and press Resume.", target=target)
        if err == "OPTION_NOT_FOUND":
            return self._pause(run, "option_not_found", f"'{target}' has no option matching the planned value. "
                               f"Available: {', '.join((result.get('options') or [])[:10])}. Choose it manually and "
                               "press Resume.", target=target)
        if err == "TYPING_NOT_ACCEPTED":
            return self._pause(run, "typing_not_accepted", f"'{target}' did not accept typed text. Please type the "
                               "value yourself and press Resume (or enable trusted input in the extension options).",
                               target=target)
        if err == "DISABLED":
            return self._pause(run, "control_disabled", f"'{target}' is disabled. Please resolve it in the application "
                               "and press Resume.", target=target)
        return self._pause(run, "control_not_found", f"Automation paused. I could not use '{target}' "
                           f"({result.get('message') or err}). Please do this step manually; I will verify your action "
                           "and continue.", target=target)

    @staticmethod
    def _rewind(cp: dict[str, Any], awaiting: dict[str, Any]) -> None:
        if cp.get("sub") == "steps" and cp.get("step", 0) > 0 and awaiting.get("type") in ("fill", "select", "check",
                                                                                             "click"):
            cp["step"] -= 1
        if cp.get("sub") in ("verify",) and awaiting.get("commit"):
            cp["sub"] = "steps"
            cp["step"] = max(0, cp.get("step", 1) - 1)
        login = cp.get("login") or {}
        back = {"login_pass": "login_user", "login_submit": "login_pass", "login_verify": "login_submit"}
        if login.get("phase") in back and awaiting.get("type") in ("fill", "click"):
            login["phase"] = back[login["phase"]]

    def _complete(self, run: dict[str, Any]) -> dict[str, Any]:
        items = self.items(run["id"])
        ok = sum(1 for i in items if i["status"] == "confirmed_success")
        status = "completed" if ok == len(items) else ("partial" if ok else "failed")
        self._finish(run, status, None)
        return {"done": self.view_summary(self.run(run["id"]))}

    def _finish(self, run: dict[str, Any], status: str, note: str | None) -> None:
        for i in self.items(run["id"]):
            if i["status"] == "running":  # interrupted mid-record: never reported as success
                submitted = i["attempts"] > 0
                self._item(run["id"], i["seq"], status="unknown" if submitted else "pending",
                           result={**i["result"], "reason": "the execution ended after this record was submitted; "
                                   "check it in the application" if submitted else "not executed (the execution "
                                   "ended before this record was submitted)"})
        items = self.items(run["id"])
        counts: dict[str, int] = {}
        for i in items:
            counts[i["status"]] = counts.get(i["status"], 0) + 1
        summary = {**(run.get("summary") or {}), "counts": counts, "total": len(items),
                   "message": note or report_text(run, items)}
        cp = run["checkpoint"]
        cp["phase"] = "done"
        self._save(run, status=status, summary=summary, checkpoint=cp, finished_at=now_iso())
        self._event(run["id"], "done", summary["message"])
        self.audit.record("run.finished", target=run["id"], details={"status": status, "counts": counts})

    # ---- views ---------------------------------------------------------------------------------------------------

    def status_line(self, run: dict[str, Any]) -> dict[str, Any]:
        cp = run["checkpoint"]
        plan = run["plan"]
        items = self.items(run["id"])
        stage = plan["stages"][min(cp.get("stage", 0), len(plan["stages"]) - 1)]
        sess = cp.get("session") or {}
        counts = {"success": sum(1 for i in items if i["status"] == "confirmed_success"),
                  "failed": sum(1 for i in items if i["status"] == "confirmed_failure"),
                  "pending": sum(1 for i in items if i["status"] in ("pending", "running")),
                  "skipped": sum(1 for i in items if i["status"] == "skipped"),
                  "unknown": sum(1 for i in items if i["status"] == "unknown")}
        done = len(items) - counts["pending"]
        return {"run_id": run["id"], "state": run["status"], "application": plan["app_name"],
                "account": self._acc_label(sess.get("account_id")) if sess.get("account_id") else None,
                "verified_role": sess.get("role") if sess.get("role_verified") else None,
                "identity_verified": bool(sess.get("verified")), "page": cp.get("page"),
                "stage": f"{stage['title']} ({cp.get('stage', 0) + 1}/{len(plan['stages'])})",
                "record": f"{min(cp.get('item', 0) + 1, len(items))}/{len(items)}",
                "phase": cp.get("sub") if cp.get("phase") == "execute" else cp.get("phase"),
                "progress": round(100 * done / max(1, len(items))), "counts": counts,
                "heal_attempts": (run.get("summary") or {}).get("heal_attempts", 0),
                "heals": (run.get("summary") or {}).get("heals", 0),
                "manual": (cp.get("pause") or {}).get("kind") == "manual" and run["status"] == "paused",
                "pause": cp.get("pause") if run["status"] == "paused" else None}

    def view(self, run_id: str) -> dict[str, Any]:
        run = self.run(run_id)
        return {"run": {k: run[k] for k in ("id", "application_id", "workflow_key", "interface", "status", "preview",
                                            "preview_hash", "confirmed_by", "confirmed_at", "summary", "created_by",
                                            "created_at", "updated_at", "finished_at")},
                "plan": run["plan"], "items": self.items(run_id), "status": self.status_line(run)}

    def view_summary(self, run: dict[str, Any]) -> dict[str, Any]:
        return {"status": run["status"], **(run.get("summary") or {})}


def report_text(run: dict[str, Any], items: list[dict[str, Any]]) -> str:
    """The verified result in words; counts come only from application evidence."""
    n = len(items)
    ok = sum(1 for i in items if i["status"] == "confirmed_success")
    failed = [i for i in items if i["status"] == "confirmed_failure"]
    pending = [i for i in items if i["status"] in ("pending", "running")]
    skipped = [i for i in items if i["status"] == "skipped"]
    unknown = [i for i in items if i["status"] == "unknown"]
    title = " -> ".join(s["title"] for s in run["plan"]["stages"])
    app = run["plan"]["app_name"]
    if ok == n:
        return f"{title}: all {n} record(s) completed and verified in {app}."
    parts = [f"{title}: {ok} of {n} record(s) completed and verified in {app}."]
    for label, group in (("failed (rejected by the application)", failed), ("remain pending", pending),
                         ("were skipped", skipped), ("are unverified (not repeated)", unknown)):
        if group:
            reasons = sorted({(g["result"].get("reason") or "")[:90] for g in group if g["result"].get("reason")})
            parts.append(f"{len(group)} {label}" + (f": {'; '.join(reasons[:3])}" if reasons else "") + ".")
    return " ".join(parts)


_TIER_TEXT = {1: "its accessible role and name", 2: "its label", 3: "a stable attribute", 4: "nearby text",
              5: "its place in the page structure", 6: "its table row", 7: "visible text"}


def _rand() -> str:
    import secrets

    return secrets.token_hex(3).upper()


def _url_allowed(url: str, app: dict[str, Any]) -> bool:
    if not url or url.startswith(("about:", "chrome-error:", "data:")) or url == "about:blank":
        return True
    try:
        return origin_of(url) in app["origins"]
    except Exception:
        return False


def _fill(template: str, record: dict[str, Any], regex: bool = False) -> str:
    def rep(m: re.Match[str]) -> str:
        v = str(record.get(m.group(1), ""))
        return re.escape(v) if regex else quote(v, safe="")
    return re.sub(r"\{(\w+)\}", rep, template or "")


def _value_for(step: dict[str, Any], record: dict[str, Any]) -> Any:
    if "param" in step:
        v = record.get(step["param"])
        return None if v in (None, "") else v
    return step.get("value")


def _pick_table(snap: dict[str, Any], column: str | None) -> dict[str, Any] | None:
    tables = snap.get("tables") or []
    if column:
        for t in tables:
            if any(norm(h) == norm(column) for h in t.get("headers", [])):
                return t
    return tables[0] if tables else None


def _row_control(snap: dict[str, Any], step: dict[str, Any], record: dict[str, Any]) -> Any:
    key = norm(record.get(step.get("param")))
    control = norm(step.get("control"))
    hits = [e for e in snap.get("elements", []) if e.get("visible", True) and not e.get("disabled")
            and (norm(e.get("name")) == control or norm(e.get("text")) == control)
            and key and key in norm((e.get("table") or {}).get("row") if isinstance(e.get("table"), dict) else "")]
    if not hits:
        return None
    exact = [e for e in hits if key in [norm(c) for c in str((e.get("table") or {}).get("row", "")).split("|")]]
    pool = exact or hits
    return pool[0] if len(pool) == 1 else "ambiguous"


def _login_fallback(snap: dict[str, Any], what: str) -> dict[str, Any] | None:
    """When nothing was learned for the sign-in page: the visible user / password field and submit button."""
    els = [e for e in snap.get("elements", []) if e.get("visible", True)]
    if what == "password":
        pw = [e for e in els if e.get("type") == "password"]
        return pw[0] if len(pw) == 1 else None
    if what == "user":
        users = [e for e in els if e.get("tag") == "input" and e.get("type") in ("text", "email", None, "")
                 and not e.get("secret")]
        return users[0] if len(users) == 1 else None
    pw = [e for e in els if e.get("type") == "password"]
    if not pw:
        return None
    buttons = [e for e in els if e.get("role") == "button" and re.search(r"sign ?in|log ?in|login|submit|continue",
                                                                        norm(e.get("name")))]
    return buttons[0] if len(buttons) == 1 else None


def _options(kb: Any, app_id: str, wf: dict[str, Any]) -> dict[str, list[str]]:
    from .planner import field_options

    return field_options(kb, app_id, wf)


def _safe_event(e: dict[str, Any]) -> dict[str, Any]:
    el = e.get("element") or {}
    return {"kind": e.get("kind"), "url": e.get("url"), "element": locator.fingerprint_of(el) if el else None}


def resolve_with_memory(kb: KnowledgeBase, app_id: str, screen: str, label: str, snap: dict[str, Any], action: str,
                        fallback: dict[str, Any] | None = None) -> locator.Resolution:
    mem = kb.locator(app_id, screen, label) if label else None
    if mem:
        res = locator.resolve(mem["fingerprint"], snap, action=action, alternatives=mem["alternatives"])
        if res.status == "not_found" and label:
            # the learned fingerprint no longer fits: look for the control by the name a person uses for it
            by_name = locator.resolve_label(label, snap, action=action)
            if by_name.status == "found":
                by_name.healed = True
                return by_name
        return res
    if fallback:
        return locator.Resolution("found", element=fallback, confidence=0.7, tier=7, healed=False)
    if not label:
        return locator.Resolution("not_found", reason="Nothing was learned for this control.")
    return locator.resolve_label(label, snap, action=action)


