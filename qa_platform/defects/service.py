"""Defect Intelligence: drafts, duplicate detection, review and TFS publishing.

Creation modes (settings ``defects.creation_mode``):
  review     - failures become drafts; a person reviews, approves and publishes them (default)
  automatic  - 23.11.0: failures become drafts automatically as well, marked "Approval required". Nothing is ever
               published to TFS without a person's approval (it was published right away before 23.11.0).
  disabled   - no drafts are made automatically; analysis can still be requested by hand

23.11.0: one review and publishing path for AI-Driven Automation, Guided Browser Automation (recorded scripts, edited
Python, data-driven, bulk and scheduled runs) and Integration Comparison. Statuses: Draft, Needs Review, Approved,
Publishing, Published to TFS, Publication Failed, Rejected, Duplicate. Every draft is classified (triage.py); every
action is written to defect_events. Publishing checks approval, mandatory fields (TFS Preferences), the connection,
the attachments and duplicates; it tags the work item with a correlation ID so that a lost response is reconciled
(the item is found by its tag) instead of creating it twice, and success is reported only after TFS returned the item.

The same failure seen again (same script step/locator or same plan/SLA) is added
to the open draft as another occurrence instead of a new draft.
"""

from __future__ import annotations

import hashlib
import html
import re
import threading
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .. import auth, branding
from ..agents.base import BaseAgent
from ..clock import now_iso
from ..config import Settings
from ..errors import QAError
from ..events import names
from ..events.bus import EventBus, QAEvent
from ..logging_setup import get_logger
from ..storage.db import Database, dumps, loads
from ..tfs.client import wiql_quote
from ..tfs.service import TfsService
from . import analysis, triage

log = get_logger("defects")

LIKELY_DUPLICATE = 0.85
POSSIBLE_DUPLICATE = 0.6
SEVERITY_WORDS = {1: "critical", 2: "high", 3: "medium", 4: "low"}
OPEN_STATUSES = ("draft", "needs_review", "approved", "publication_failed")
STATUS_LABELS = {"draft": "Draft", "needs_review": "Needs Review", "approved": "Approved", "publishing": "Publishing",
                 "created": "Published to TFS", "publication_failed": "Publication Failed", "dismissed": "Rejected",
                 "linked": "Duplicate"}
SOURCE_LABELS = {"functional": "Guided Browser Automation", "framework": "AI-Driven Automation",
                 "python": "Guided Browser Automation (Python)", "comparison": "Integration Comparison",
                 "performance": "Performance", "api": "API Testing"}
MAX_ATTACHMENT = 20 * 1024 * 1024


class FailureAnalysisAgent(BaseAgent):
    agent_id = "failure_analysis"


class DefectIntelligenceAgent(BaseAgent):
    agent_id = "defect_intelligence"


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z][a-z0-9_]{2,}", analysis.normalize(text))
            if t not in {"the", "and", "for", "with", "was", "not", "step", "failed", "test", "from"}}


def _text(value: str | None) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", value or ""))


def similarity(draft: dict[str, Any], bug: dict[str, Any]) -> tuple[float, list[str]]:
    """Score 0-1 with the reasons, comparing a draft with an existing TFS defect."""
    reasons = []
    t1, t2 = analysis.normalize(draft["title"]), analysis.normalize(bug.get("title") or "")
    title = SequenceMatcher(None, t1, t2).ratio() if t1 and t2 else 0.0
    a, b = _tokens(draft["title"] + " " + _text(draft["repro_steps"])), _tokens((bug.get("title") or "") + " " + _text(bug.get("repro_steps")))
    body = len(a & b) / len(a | b) if a and b else 0.0
    # An identical title is strong evidence on its own; descriptions in TFS are often edited or brief.
    score = max(0.75 * title + 0.25 * body, 0.85 * title)
    if title >= 0.8:
        reasons.append(f"title {int(title * 100)}% similar")
    if body >= 0.4:
        reasons.append(f"{len(a & b)} shared terms in the description")
    selector = (draft.get("facts") or {}).get("selector")
    bug_text = (bug.get("title") or "") + " " + _text(bug.get("repro_steps"))
    if selector and selector in bug_text:
        score += 0.15
        reasons.append(f"same element {selector}")
    if draft.get("test_case_id") and bug.get("linked_test_cases") and str(draft["test_case_id"]) in bug["linked_test_cases"]:
        score += 0.15
        reasons.append(f"linked to the same Test Case #{draft['test_case_id']}")
    return round(min(score, 1.0), 3), reasons


class DefectService:
    def __init__(self, settings: Settings, bus: EventBus, db: Database, tfs: TfsService, automation: Any,
                 performance: Any) -> None:
        self.settings = settings
        self.db = db
        self.tfs = tfs
        self.automation = automation
        self.performance = performance
        self.analyst = FailureAnalysisAgent(bus)
        self.agent = DefectIntelligenceAgent(bus)
        self.on_published: list[Any] = []  # callbacks(draft) that link a published defect back to its source
        self.ai: Any = None  # the AI gateway (runtime.py); the local AI's view is optional and validated (triage.py)
        self._lock = threading.Lock()
        self._finished: dict[str, threading.Event] = {}  # run id -> automatic analysis (and creation) finished
        bus.add_listener(self._on_event)

    @property
    def mode(self) -> str:
        return self.settings.section("defects").get("creation_mode", "review")

    # ---- automatic trigger ------------------------------------------------------------------

    def _finished_event(self, run_id: str) -> threading.Event:
        with self._lock:
            if len(self._finished) > 500:
                for key in [k for k, ev in self._finished.items() if ev.is_set()][:250]:
                    del self._finished[key]
            return self._finished.setdefault(run_id, threading.Event())

    def wait_for_automatic(self, run_id: str, timeout: float) -> bool:
        """Wait until the automatic analysis of a failed run (and any automatic creation) is done."""
        return self._finished_event(run_id).wait(timeout)

    def _on_event(self, event: QAEvent) -> None:
        if self.mode == "disabled" or not event.run_id:
            return
        if event.name == names.EXECUTION_FAILED:
            if str((event.details or {}).get("script_id") or "").startswith("FWS-"):
                return  # AI-Driven framework runs are diagnosed by the Script Studio (see record_draft)
            if (event.details or {}).get("python"):
                return  # 23.11.0: an edited Python test run makes its own draft (python_scripts.py)
            kind = "functional"
        elif event.name == names.PERFORMANCE_FAILED and (event.details or {}).get("sla") == "FAIL":
            kind = "performance"
        else:
            return
        run_id = event.run_id

        def work() -> None:
            try:
                self.analyze(kind, run_id, automatic=True)
            except QAError as exc:
                log.info(f"Automatic failure analysis skipped: {exc.message}", extra={"run_id": run_id})
            except Exception:
                log.exception("Automatic failure analysis failed")
            finally:
                self._finished_event(run_id).set()

        # Results are written right after the failure event; let the run finish first.
        timer = threading.Timer(1.5, work)
        timer.daemon = True
        timer.start()

    # ---- analysis ------------------------------------------------------------------------------

    def analyze(self, kind: str, run_id: str, *, automatic: bool = False) -> dict[str, Any]:
        with self.analyst.activity(f"Analyzing failed {kind} run {run_id}", run_id=run_id) as act:
            act.event(names.DEFECT_ANALYSIS_STARTED, "Collecting evidence from the failed run")
            if kind == "functional":
                rows = self.automation.runner.results(run_id=run_id)
                if not rows:
                    raise QAError("RUN_NOT_FOUND", f"No execution result for {run_id}.", status=404)
                execution = rows[0]
                if execution["status"] != "FAILED":
                    raise QAError("RUN_NOT_FAILED", "Only failed runs can be analyzed for defects.", status=409)
                try:
                    script = self.automation.scripts.get(execution["script_id"]).model_dump()
                except QAError:
                    script = {"id": execution["script_id"], "name": execution["script_id"], "steps": []}
                healing = [h for h in self.automation.runner.healing_events(execution["script_id"]) if h["run_id"] == run_id]
                draft = analysis.functional_draft(execution, script, healing)
            elif kind == "performance":
                run = self.performance.get_run(run_id)
                try:
                    plan = self.performance.get_plan(run["plan_id"]).model_dump()
                except QAError:
                    plan = None
                draft = analysis.performance_draft(run, plan)
                if draft is None:
                    raise QAError("SLA_NOT_FAILED", "This performance run met its SLAs; there is nothing to report.",
                                  status=409)
            else:
                raise QAError("ANALYSIS_KIND_INVALID", "Analysis kind must be functional or performance.")
            act.progress(50, f"Classified as {draft['category'].replace('_', ' ')}")
            saved = self._store(draft)
            act.complete(f"Failure analysis: {analysis.CATEGORIES[draft['category']][2]}")
        if saved.get("_new"):
            self.check_duplicates(saved["id"])
            if automatic and self.mode == "automatic":
                self._auto_create(saved["id"])
        return self.get(saved["id"])

    def manual_draft(self, *, source: str, module: str, title: str, severity: int = 2, priority: int = 2,
                     repro_steps: str = "", assigned_to: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        """23.13.0: "Create TFS defect" on a module page: a draft a person wrote (it still goes through Defect Review's
        approval and duplicate check before it is sent to TFS)."""
        by = self._actor("edit")
        title = (title or "").strip()[:250]
        if len(title) < 3:
            raise QAError("VALIDATION_FAILED", "Write a title of at least 3 characters.", status=422)
        if source not in SOURCE_LABELS:
            source = "functional"
        stamp = now_iso()
        run = (run_id or "").strip()[:80] or f"MANUAL-{stamp.replace(':', '').replace('-', '')[:15]}"
        steps = repro_steps.strip() or html.escape(title)
        if "<" not in steps:
            steps = "<br>".join(html.escape(line) for line in steps.splitlines())
        draft = {"source": source, "source_run_id": run,
                 "signature": hashlib.sha256(f"manual|{module}|{title.lower()}".encode()).hexdigest()[:40],
                 "category": "manual", "product_defect": True, "title": title, "severity": int(severity),
                 "priority": int(priority), "repro_steps": steps,
                 "facts": {"module": module, "reported_by": by, "error": "", "manual": True}, "evidence": []}
        saved = self._store(draft)
        if assigned_to is not None:
            self.db.execute("UPDATE defect_drafts SET assigned_to = ? WHERE id = ?",
                            ((assigned_to or "").strip()[:256] or None, saved["id"]))
        if saved.get("_new"):
            self._event(saved["id"], by, "edited", f"Written on {module}")
            try:
                self.check_duplicates(saved["id"])
            except QAError as exc:
                log.info(f"Duplicate check skipped: {exc.message}")
        return self.get(saved["id"])

    def record_draft(self, draft: dict[str, Any]) -> dict[str, Any] | None:
        """Store a draft built by another analysis (AI-Driven framework runs), with the usual duplicate check."""
        if self.mode == "disabled":
            return None
        try:
            saved = self._store(draft)
            if saved.get("_new"):
                try:
                    self.check_duplicates(saved["id"])
                except QAError as exc:
                    log.info(f"Duplicate check skipped: {exc.message}", extra={"run_id": draft["source_run_id"]})
                if self.mode == "automatic":
                    self._auto_create(saved["id"])
            return self.get(saved["id"])
        finally:
            self._finished_event(draft["source_run_id"]).set()

    def _store(self, draft: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            ts = now_iso()
            existing = self.db.query_one(
                f"SELECT * FROM defect_drafts WHERE signature = ? AND (status IN ({','.join('?' * len(OPEN_STATUSES))},"
                " 'publishing') OR (status = 'created' AND tfs_id IS NOT NULL)) ORDER BY id DESC LIMIT 1",
                (draft["signature"], *OPEN_STATUSES))
            if existing:
                occ = loads(existing["occurrences"])
                if draft["source_run_id"] not in [o["run_id"] for o in occ]:
                    occ.append({"run_id": draft["source_run_id"], "at": ts})
                    self.db.execute("UPDATE defect_drafts SET occurrences = ?, updated_at = ? WHERE id = ?",
                                    (dumps(occ), ts, existing["id"]))
                    self._event(existing["id"], "system", "occurrence",
                                f"Same failure seen again in run {draft['source_run_id']}")
                    if existing["tfs_id"]:
                        self._comment_recurrence(existing["tfs_id"], draft["source_run_id"])
                return self.get(existing["id"])
            tri = draft.get("triage") or triage.rules_classify(draft["category"], draft.get("facts") or {})
            cur = self.db.execute(
                "INSERT INTO defect_drafts (source, source_run_id, signature, status, category, product_defect, title,"
                " severity, priority, repro_steps, facts, evidence, occurrences, test_case_id, user_story_id,"
                " created_at, updated_at, classification, confidence, analysis, source_ref)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (draft["source"], draft["source_run_id"], draft["signature"],
                 "draft" if draft["product_defect"] else "needs_review", draft["category"], int(draft["product_defect"]),
                 draft["title"], draft["severity"], draft["priority"], draft["repro_steps"], dumps(draft["facts"]),
                 dumps(draft["evidence"]), dumps([{"run_id": draft["source_run_id"], "at": ts}]),
                 draft.get("test_case_id"), draft.get("user_story_id"), ts, ts, tri["classification"],
                 tri["confidence"], dumps(tri), draft.get("source_ref")))
            self._event(cur.lastrowid, "system", "drafted", f"{tri['label']} ({int(tri['confidence'] * 100)}%)")
        saved = self.get(cur.lastrowid)
        self.agent.emit(names.DEFECT_DRAFTED,
                        f"Defect draft #{saved['id']} needs review ({SOURCE_LABELS.get(saved['source'], saved['source'])}):"
                        f" {saved['title'][:120]}", run_id=saved["source_run_id"],
                        details={"draft_id": saved["id"], "classification": saved["classification"],
                                 "link": f"#/defect-review?id={saved['id']}"})
        self._ai_in_background(saved["id"])
        return {**saved, "_new": True}

    def _comment_recurrence(self, tfs_id: int, run_id: str) -> None:
        try:
            self.tfs.add_links(tfs_id, [], comment=f"Failure seen again in automated run {html.escape(run_id)} "
                                                   f"({html.escape(now_iso())}).")
            self.agent.emit(names.AGENT_LOG, f"Recurrence added to defect #{tfs_id}", run_id=run_id)
        except QAError as exc:
            log.info(f"Could not comment on defect #{tfs_id}: {exc.message}")

    # ---- duplicates ---------------------------------------------------------------------------

    def _open_bugs(self) -> list[dict[str, Any]]:
        meta = self.tfs.meta()
        types = self.tfs.types_for("bug")
        closed = meta.get("closed_states") or []
        where = ("[System.TeamProject] = @project AND [System.WorkItemType] IN (" + ", ".join(wiql_quote(t) for t in types) + ")"
                 + (" AND [System.State] NOT IN (" + ", ".join(wiql_quote(s) for s in closed) + ")" if closed else ""))
        wiql = f"SELECT [System.Id] FROM WorkItems WHERE {where} ORDER BY [System.ChangedDate] DESC"

        def run(c: Any) -> list[dict[str, Any]]:
            ids = c.wiql(wiql, top=500)
            items = c.work_items(ids, expand_relations=True) if ids else []
            out = []
            for w in items:
                f = w.get("fields", {})
                linked = [r["url"].rsplit("/", 1)[1] for r in w.get("relations") or [] if "/workItems/" in r.get("url", "")]
                out.append({"id": w["id"], "title": f.get("System.Title"), "state": f.get("System.State"),
                            "repro_steps": f.get("Microsoft.VSTS.TCM.ReproSteps") or f.get("System.Description"),
                            "linked_test_cases": linked})
            return out

        return self.tfs._with_client(run)

    def check_duplicates(self, draft_id: int) -> list[dict[str, Any]]:
        draft = self.get(draft_id)
        with self.agent.activity(f"Searching TFS for existing defects like draft #{draft_id}",
                                 run_id=draft["source_run_id"], test_case_id=draft.get("test_case_id")) as act:
            try:
                bugs = self._open_bugs()
            except QAError as exc:
                self.db.execute("UPDATE defect_drafts SET note = ?, updated_at = ? WHERE id = ?",
                                (f"Duplicate check not done: {exc.message}", now_iso(), draft_id))
                act.complete(f"Duplicate check skipped: {exc.message}")
                return []
            scored = []
            for bug in bugs:
                score, reasons = similarity(draft, bug)
                if score >= POSSIBLE_DUPLICATE:
                    scored.append({"id": bug["id"], "title": bug["title"], "state": bug["state"], "score": score,
                                   "reasons": reasons})
            scored.sort(key=lambda d: -d["score"])
            scored = scored[:5]
            self.db.execute("UPDATE defect_drafts SET duplicates = ?, note = NULL, updated_at = ? WHERE id = ?",
                            (dumps(scored), now_iso(), draft_id))
            if scored:
                top = scored[0]
                act.event(names.DEFECT_DUPLICATE_FOUND,
                          f"Possible existing defect #{top['id']} ({int(top['score'] * 100)}% match): {top['title']}",
                          link="#/defect-review?id=" + str(draft_id), tfs_id=top["id"], score=top["score"])
            act.complete(f"Compared with {len(bugs)} open defects; {len(scored)} possible duplicates")
        return scored

    # ---- create / link / dismiss -------------------------------------------------------------------

    def _auto_create(self, draft_id: int) -> None:
        """23.11.0: automatic mode drafts only. A defect is never published to TFS without a person's approval."""
        draft = self.get(draft_id)
        if draft["duplicates"] and draft["duplicates"][0]["score"] >= LIKELY_DUPLICATE:
            reason = f"Approval required. Likely duplicate of #{draft['duplicates'][0]['id']}."
        elif not draft["product_defect"]:
            reason = "Approval required. This failure may not be a product defect; review the classification."
        else:
            reason = "Approval required before it is sent to TFS."
        if draft["note"] and draft["note"] not in reason:
            reason += " " + draft["note"]
        self.db.execute("UPDATE defect_drafts SET note = ?, updated_at = ? WHERE id = ?", (reason, now_iso(), draft_id))

    def _severity_value(self, type_name: str, level: int) -> str | None:
        allowed = self.tfs.allowed_values(type_name, "Microsoft.VSTS.Common.Severity")
        for value in allowed:
            if value.strip().startswith(str(level)):
                return value
        word = SEVERITY_WORDS[level]
        return next((v for v in allowed if word in v.lower()), None)

    def _environment_values(self, type_name: str, draft: dict[str, Any]) -> dict[str, Any]:
        """Final patch: the environment and system information of the failing run, written to the fields this
        project has for them (Preferences > TFS Integration > field mappings, else the standard System Info field and
        a field named Environment). Nothing is guessed: a value is only written to a field the type has."""
        try:
            fields = self.tfs.field_schema(type_name)
        except QAError:
            return {}
        mapping = (self.tfs.settings.section("tfs").get("concept_fields") or {}).get(self.tfs.project_key()) or {}
        facts = draft.get("facts") or {}
        env_bits = [f"{k}: {facts[k]}" for k in ("application", "environment", "base_url", "browser", "build")
                    if facts.get(k)]
        out: dict[str, Any] = {}
        sys_ref = mapping.get("system_info") or ("Microsoft.VSTS.TCM.SystemInfo"
                                                 if "Microsoft.VSTS.TCM.SystemInfo" in fields else None)
        if sys_ref and sys_ref in fields:
            import platform

            out[sys_ref] = html.escape(
                f"Found by {branding.name()} automated run {draft['source_run_id']} on {platform.system()} {platform.release()}"
                + ("; " + "; ".join(env_bits) if env_bits else ""))
        env_ref = mapping.get("environment") or next(
            (r for r, f in fields.items() if f.name.strip().lower() in ("environment", "test environment")), None)
        env_value = facts.get("environment") or facts.get("base_url")
        if env_ref and env_ref in fields and env_value:
            out[env_ref] = env_value
        return out

    # ---- review: approve / reject / AI analysis (23.11.0) --------------------------------------------------------

    def _event(self, draft_id: int, by: str, action: str, detail: str = "") -> None:
        self.db.execute("INSERT INTO defect_events (draft_id, at, by, action, detail) VALUES (?,?,?,?,?)",
                        (draft_id, now_iso(), by, action, triage.redact(detail or "", 600)))

    def events(self, draft_id: int) -> list[dict[str, Any]]:
        return self.db.query("SELECT at, by, action, detail FROM defect_events WHERE draft_id = ? ORDER BY id", (draft_id,))

    def _actor(self, action: str) -> str:
        """The person doing a review action. Approving and publishing need a role in defects.approver_roles /
        defects.publisher_roles (default Admin and Tester); view-only accounts never can."""
        user = auth.current_user() or auth.LOCAL_PROGRAM
        key = {"approve": "approver_roles", "publish": "publisher_roles"}.get(action)
        roles = set(self.settings.section("defects").get(key) or auth.FULL_ROLES) if key else auth.FULL_ROLES
        if user.role not in roles or user.view_only:
            verb = {"approve": "approve or reject", "publish": "send to TFS"}.get(action, "change")
            raise QAError("DEFECT_NOT_ALLOWED", f"Your account ({auth.ROLES.get(user.role, user.role)}) may not {verb} "
                          "defects. Ask a person with that permission.", status=403)
        return user.username

    def _facts_for_triage(self, draft: dict[str, Any]) -> dict[str, Any]:
        facts = dict(draft.get("facts") or {})
        facts.setdefault("title", draft["title"])
        return facts

    def _ai_in_background(self, draft_id: int) -> None:
        """The local AI's view of a new draft, when a local (on-premise) AI server is configured. Offline or slow is
        fine: the rules' classification stands and says the AI was not used."""
        ai = self.ai
        if ai is None or self.settings.section("defects").get("ai_on_draft") is False:
            return
        try:
            cfg = ai.config()
        except Exception:
            return
        if cfg.get("mode") != "on_premise" or not cfg.get("base_url"):
            return  # failure facts are never sent to a cloud AI automatically
        t = threading.Thread(target=self._ai_quietly, args=(draft_id,), daemon=True, name=f"defect-ai-{draft_id}")
        t.start()

    def _ai_quietly(self, draft_id: int) -> None:
        try:
            self.ai.models(timeout=3)  # a quick check: no retries against a server that is not running
        except Exception as exc:
            log.info(f"Local AI not reachable; draft {draft_id} keeps the rules' classification ({exc})")
            return
        try:
            self.ai_analyze(draft_id, by="system")
        except Exception as exc:  # never breaks the run or the draft
            log.info(f"AI analysis of draft {draft_id} not done: {exc}")

    def ai_analyze(self, draft_id: int, *, by: str | None = None) -> dict[str, Any]:
        draft = self.get(draft_id)
        by = by or (auth.current_user() or auth.LOCAL_PROGRAM).username
        facts = self._facts_for_triage(draft)
        category = "comparison" if draft["source"] == "comparison" else "api" if draft["source"] == "api" \
            else draft["category"]
        rules = triage.rules_classify(category, facts)
        timeout = float(self.settings.section("defects").get("ai_timeout_seconds") or 90)
        if draft["source"] == "api":  # 23.12.0: API failures have their own classes and AI instructions
            from ..apitesting import analysis as api_analysis

            result = api_analysis.ai_view(self.ai, facts, rules, timeout=timeout)
        else:
            result = triage.ai_classify(self.ai, facts, rules, timeout=timeout)
        self.db.execute("UPDATE defect_drafts SET classification = ?, confidence = ?, analysis = ?, updated_at = ?"
                        " WHERE id = ?", (result["classification"], result["confidence"], dumps(result), now_iso(), draft_id))
        ai_used = result.get("ai", {}).get("used")
        self._event(draft_id, by, "ai_analyzed", f"{result['label']} ({int(result['confidence'] * 100)}%), "
                    + ("local AI" if ai_used else result.get("ai", {}).get("reason", "rules only")))
        return self.get(draft_id)

    def approve(self, draft_id: int, note: str = "") -> dict[str, Any]:
        by = self._actor("approve")
        draft = self.get(draft_id)
        if draft["status"] not in ("draft", "needs_review", "publication_failed", "approved"):
            raise QAError("DEFECT_NOT_REVIEWABLE", f"A draft that is {STATUS_LABELS.get(draft['status'])} cannot be "
                          "approved.", status=409)
        if draft["status"] == "publication_failed" and draft.get("approved_by"):
            return draft
        self.db.execute("UPDATE defect_drafts SET status = 'approved', approved_by = ?, approved_at = ?, updated_at = ?"
                        " WHERE id = ?", (by, now_iso(), now_iso(), draft_id))
        self._event(draft_id, by, "approved", note)
        self.agent.emit(names.DEFECT_APPROVED, f"Defect draft #{draft_id} approved by {by}: {draft['title'][:120]}",
                        run_id=draft["source_run_id"], details={"draft_id": draft_id,
                                                                "link": f"#/defect-review?id={draft_id}"})
        return self.get(draft_id)

    def reject(self, draft_id: int, reason: str = "") -> dict[str, Any]:
        by = self._actor("approve")
        draft = self.get(draft_id)
        if draft["status"] in ("created", "linked", "publishing"):
            raise QAError("DEFECT_LOCKED", "This defect is already in TFS (or being sent); it cannot be rejected here.",
                          status=409)
        self.db.execute("UPDATE defect_drafts SET status = 'dismissed', note = ?, updated_at = ? WHERE id = ?",
                        (reason or "Rejected", now_iso(), draft_id))
        self._event(draft_id, by, "rejected", reason)
        return self.get(draft_id)

    # ---- publishing (one service for every module) ------------------------------------------------------------------

    def web_url(self, item_id: int) -> str:
        from urllib.parse import quote

        p = self.tfs.profile()
        return f"{p['url'].rstrip('/')}/{quote(p['project'])}/_workitems/edit/{int(item_id)}"

    def _find_by_correlation(self, corr: str) -> list[int]:
        tag = f"SkyGuard-{corr}"
        wiql = ("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project AND [System.Tags] CONTAINS "
                + wiql_quote(tag))
        return [int(i) for i in self.tfs._with_client(lambda c: c.wiql(wiql, top=5))]

    def _read_back(self, item_id: int) -> dict[str, Any]:
        return self.tfs._with_client(lambda c: c.work_item(int(item_id)))

    def check_attachments(self, draft: dict[str, Any]) -> tuple[list[Path], list[str]]:
        ok, problems = [], []
        for ev in draft["evidence"] or []:
            path = Path(str(ev.get("path") or ""))
            if not ev.get("path") or not path.is_file():
                problems.append(f"{path.name or 'evidence'}: file not found")
            elif path.stat().st_size > MAX_ATTACHMENT:
                problems.append(f"{path.name}: larger than 20 MB, not attached")
            elif path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".zip", ".txt", ".log", ".json", ".html",
                                             ".csv", ".webm", ".mp4", ".xlsx", ".pdf"):
                problems.append(f"{path.name}: file type not attached")
            else:
                ok.append(path)
        return ok, problems

    def _publish_patch(self, draft: dict[str, Any], corr: str,
                       extra: dict[str, Any] | None = None) -> tuple[str, list[dict[str, Any]], list[str]]:
        type_name = self.tfs.default_type("bug")  # Bug, Defect or Issue, from the process
        kind = {"performance": "Performance", "comparison": "Integration Comparison",
                "api": "API Testing"}.get(draft["source"], "Functional")
        values: dict[str, Any] = {
            "System.Title": draft["title"],
            "Microsoft.VSTS.TCM.ReproSteps": draft["repro_steps"],
            "Microsoft.VSTS.Common.Priority": draft["priority"],
            "Microsoft.VSTS.Common.Severity": self._severity_value(type_name, draft["severity"]),
            "System.Tags": f"QA-Automation; {kind}; SkyGuard-{corr}",
        }
        if draft.get("assigned_to"):  # 23.13.0: chosen in Defect Review from the TFS project's people
            values["System.AssignedTo"] = draft["assigned_to"]
        values.update(self._environment_values(type_name, draft))  # Environment, System Info
        values.update({k: v for k, v in (extra or {}).items() if v not in (None, "")})
        patch, skipped = self.tfs.build_patch(type_name, values)
        if values["Microsoft.VSTS.Common.Severity"] is None:
            has_field = "Microsoft.VSTS.Common.Severity" in self.tfs.type_field_refs(type_name)
            skipped.append("Microsoft.VSTS.Common.Severity" + ("" if not has_field else " (no matching allowed value)"))
        if "Microsoft.VSTS.TCM.ReproSteps" in skipped:  # e.g. an Issue type without repro steps
            extra, _ = self.tfs.build_patch(type_name, {"System.Description": draft["repro_steps"]})
            patch += extra
        for wid, rel in ((draft.get("test_case_id"), "test case"), (draft.get("user_story_id"), "user story")):
            if wid and str(wid).isdigit():
                patch.append({"op": "add", "path": "/relations/-", "value": {
                    "rel": "System.LinkTypes.Related", "url": self.tfs.work_item_url(int(wid)),
                    "attributes": {"comment": f"Failing {rel} (run {draft['source_run_id']})"}}})
        return type_name, patch, skipped

    def publish(self, draft_id: int, *, fields: dict[str, Any] | None = None, allow_duplicate: bool = False,
                extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send an approved draft to TFS. Success only after TFS returned the work item; on failure the draft is kept
        (Publication Failed) with a correlation ID, and Retry reconciles before creating again."""
        by = self._actor("publish")
        with self._lock:
            draft = self.get(draft_id)
            if draft["status"] in ("created", "linked"):
                raise QAError("DEFECT_ALREADY_CREATED", f"This draft is already defect #{draft['tfs_id']}.", status=409)
            if draft["status"] == "publishing":
                raise QAError("DEFECT_PUBLISHING", "This draft is being sent to TFS right now.", status=409)
            if draft["status"] not in ("approved", "publication_failed") or not draft.get("approved_by"):
                raise QAError("DEFECT_NOT_APPROVED", "Approve the draft before sending it to TFS. Nothing is published "
                              "without a person's approval.", status=409)
            dup = (draft.get("duplicates") or [None])[0]
            if dup and dup["score"] >= LIKELY_DUPLICATE and not allow_duplicate:
                raise QAError("DEFECT_LIKELY_DUPLICATE", f"Likely duplicate of #{dup['id']} ({int(dup['score'] * 100)}%):"
                              f" {dup['title']}. Link it to that defect, or send it anyway.", status=409,
                              details={"duplicate": dup})
            corr = draft.get("correlation_id") or uuid.uuid4().hex[:12]
            self.db.execute("UPDATE defect_drafts SET status = 'publishing', correlation_id = ?, publish_attempts ="
                            " publish_attempts + 1, updated_at = ? WHERE id = ?", (corr, now_iso(), draft_id))
        self._event(draft_id, by, "publishing", f"Correlation ID {corr}")
        sent = False
        attached: list[str] = []
        skipped: list[str] = []
        type_name = ""
        reconciled = False
        try:
            with self.agent.activity(f"Sending defect to TFS: {draft['title'][:80]}", run_id=draft["source_run_id"],
                                     test_case_id=draft.get("test_case_id")) as act:
                found = self._find_by_correlation(corr)  # an earlier attempt that lost its response
                if found:
                    item = self._read_back(found[0])
                    reconciled = True
                else:
                    type_name, patch, skipped = self._publish_patch(draft, corr, extra)
                    self.tfs.create_checked(type_name, patch, fields, validate_only=True)  # mandatory fields
                    files, problems = self.check_attachments(draft)
                    for p in problems:
                        self._event(draft_id, by, "attachment_skipped", p)
                    for path in files:
                        up = self.tfs.upload_attachment(path.name, path.read_bytes())
                        patch.append({"op": "add", "path": "/relations/-", "value": {
                            "rel": "AttachedFile", "url": up["url"], "attributes": {"comment": "Test evidence"}}})
                        attached.append(path.name)
                    sent = True
                    created, _report = self.tfs.create_checked(type_name, patch, fields)
                    self.tfs.invalidate()
                    item = self._read_back(int(created["id"]))  # confirmed by the server
                act.complete(f"Defect #{item['id']} in TFS")
        except Exception as exc:
            if sent and isinstance(exc, QAError) and exc.error_code in ("TFS_TIMEOUT", "TFS_UNREACHABLE"):
                try:  # the response was lost: did TFS create it?
                    found = self._find_by_correlation(corr)
                    if found:
                        return self._published(draft_id, by, self._read_back(found[0]), corr, skipped, attached, True)
                except Exception:
                    pass
            return self._publish_failed(draft_id, by, draft, corr, exc)
        return self._published(draft_id, by, item, corr, skipped, attached, reconciled, type_name)

    def _published(self, draft_id: int, by: str, item: dict[str, Any], corr: str, skipped: list[str],
                   attached: list[str], reconciled: bool, type_name: str = "") -> dict[str, Any]:
        item_id = int(item["id"])
        url = self.web_url(item_id)
        at = now_iso()
        note = ("Skipped fields not in this process: " + ", ".join(skipped)) if skipped else None
        self.db.execute("UPDATE defect_drafts SET status = 'created', tfs_id = ?, tfs_url = ?, published_at = ?, note = ?,"
                        " updated_at = ? WHERE id = ?", (item_id, url, at, note, at, draft_id))
        self._event(draft_id, by, "published", f"#{item_id} {url}" + (" (found after a lost response)" if reconciled else ""))
        draft = self.get(draft_id)
        self.agent.emit(names.DEFECT_CREATED, f"Defect #{item_id}: {draft['title'][:120]}", run_id=draft["source_run_id"],
                        details={"link": f"#/defect-review?id={draft_id}", "tfs_id": item_id, "url": url,
                                 "attachments": attached, "skipped_fields": skipped, "draft_id": draft_id})
        for hook in list(self.on_published):
            try:
                hook(draft)
            except Exception:
                log.exception("Linking the published defect back to its source failed")
        try:
            from .. import aura  # AURA remembers known defects (title and signature, no evidence data)

            aura.learn("known_defect", (draft.get("facts") or {}).get("application"), f"tfs:{item_id}", {
                "title": draft["title"], "category": draft.get("category"), "severity": draft.get("severity"),
                "source": draft.get("source"), "signature": draft.get("signature"), "tfs_id": item_id},
                source="defects", approved=True, agent="defect_intelligence")
        except Exception:
            log.info("AURA could not record the defect")
        return {**draft, "type": type_name or None, "skipped_fields": skipped, "attachments": attached,
                "reconciled": reconciled}

    def _publish_failed(self, draft_id: int, by: str, draft: dict[str, Any], corr: str, exc: Exception) -> dict[str, Any]:
        if isinstance(exc, QAError):
            code, message, status, details = exc.error_code, exc.message, exc.status, exc.details
        else:
            log.exception(f"Defect publication failed (correlation ID {corr})")
            code, message, status, details = "DEFECT_PUBLISH_FAILED", "TFS could not be reached or answered " \
                "unexpectedly.", 502, None
        message = triage.redact(message, 400)
        self.db.execute("UPDATE defect_drafts SET status = 'publication_failed', note = ?, updated_at = ? WHERE id = ?",
                        (f"Publication failed: {message} (correlation ID {corr})", now_iso(), draft_id))
        self._event(draft_id, by, "publication_failed", f"{code}: {message} (correlation ID {corr})")
        self.agent.emit(names.DEFECT_FAILED, f"Defect could not be sent to TFS: {message} (correlation ID {corr})",
                        run_id=draft["source_run_id"], details={"draft_id": draft_id, "correlation_id": corr})
        log.info(f"Defect draft {draft_id} not published: {code} (correlation ID {corr})")
        extra = {"correlation_id": corr, "draft_id": draft_id}
        if isinstance(details, dict):
            extra = {**details, **extra}
        raise QAError(code, f"{message} The draft was kept; you can retry. Correlation ID {corr}.", status=status or 502,
                      details=extra)

    def retry(self, draft_id: int, *, fields: dict[str, Any] | None = None) -> dict[str, Any]:
        draft = self.get(draft_id)
        if draft["status"] != "publication_failed":
            raise QAError("DEFECT_NOT_FAILED", "Only a draft whose publication failed can be retried.", status=409)
        return self.publish(draft_id, fields=fields, allow_duplicate=True)

    def create(self, draft_id: int, overrides: dict[str, Any] | None = None, *,
               fields: dict[str, Any] | None = None) -> dict[str, Any]:
        """Approve and send in one explicit action by a person allowed to do both (the earlier Create button)."""
        if overrides:
            self.update(draft_id, overrides)
        draft = self.get(draft_id)
        if draft["status"] == "created":
            raise QAError("DEFECT_ALREADY_CREATED", f"This draft is already defect #{draft['tfs_id']}.", status=409)
        if draft["status"] in ("draft", "needs_review") or not draft.get("approved_by"):
            self.approve(draft_id)
        return self.publish(draft_id, fields=fields, allow_duplicate=True)

    def link_existing(self, draft_id: int, tfs_id: int) -> dict[str, Any]:
        draft = self.get(draft_id)
        relations = [{"rel": "System.LinkTypes.Related", "url": self.tfs.work_item_url(int(draft["test_case_id"])),
                      "attributes": {"comment": "Failing test case"}}] if draft.get("test_case_id") else []
        try:
            self.tfs.add_links(tfs_id, relations, comment=(
                f"Seen again by automated testing (run {html.escape(draft['source_run_id'])}).<br>{draft['repro_steps']}"))
        except QAError as exc:
            if exc.error_code != "TFS_REQUEST_FAILED" or not relations:
                raise
            # the link may already exist; still record the occurrence as a comment
            self.tfs.add_links(tfs_id, [], comment=f"Seen again by automated testing (run {html.escape(draft['source_run_id'])}).")
        self.db.execute("UPDATE defect_drafts SET status = 'linked', tfs_id = ?, tfs_url = ?, updated_at = ? WHERE id = ?",
                        (tfs_id, self.web_url(tfs_id), now_iso(), draft_id))
        self._event(draft_id, (auth.current_user() or auth.LOCAL_PROGRAM).username, "linked",
                    f"Duplicate of #{tfs_id}; evidence added to it")
        self.agent.emit(names.AGENT_LOG, f"Failure linked to existing defect #{tfs_id}", run_id=draft["source_run_id"])
        return self.get(draft_id)

    def dismiss(self, draft_id: int, reason: str) -> dict[str, Any]:
        return self.reject(draft_id, reason or "Dismissed")

    def update(self, draft_id: int, values: dict[str, Any]) -> dict[str, Any]:
        draft = self.get(draft_id)
        if draft["status"] in ("created", "linked", "publishing"):
            raise QAError("DEFECT_LOCKED", "This draft is already in TFS (or being sent); edit the defect there.",
                          status=409)
        allowed = {k: v for k, v in values.items()
                   if k in ("title", "severity", "priority", "repro_steps", "assigned_to") and v is not None}
        if "assigned_to" in allowed:
            allowed["assigned_to"] = str(allowed["assigned_to"]).strip()[:256] or None
        if not allowed:
            return draft
        by = self._actor("edit")
        sets = ", ".join(f"{k} = ?" for k in allowed)
        self.db.execute(f"UPDATE defect_drafts SET {sets}, updated_at = ? WHERE id = ?", (*allowed.values(), now_iso(), draft_id))
        if draft["status"] == "approved":  # an approval covers what was approved: a change needs a new one
            self.db.execute("UPDATE defect_drafts SET status = 'needs_review', approved_by = NULL, approved_at = NULL"
                            " WHERE id = ?", (draft_id,))
        self._event(draft_id, by, "edited", ", ".join(allowed))
        return self.get(draft_id)

    # ---- queries -------------------------------------------------------------------------------

    def get(self, draft_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM defect_drafts WHERE id = ?", (draft_id,))
        if not row:
            raise QAError("DEFECT_DRAFT_NOT_FOUND", f"Defect draft {draft_id} does not exist.", status=404)
        for k in ("facts", "evidence", "occurrences"):
            row[k] = loads(row[k])
        row["duplicates"] = loads(row["duplicates"]) if row["duplicates"] else []
        row["product_defect"] = bool(row["product_defect"])
        cat = analysis.CATEGORIES.get(row["category"])
        row["finding"] = cat[2] if cat else (row["category"] or "").replace("_", " ")
        row["analysis"] = loads(row["analysis"]) if row.get("analysis") else None
        row["status_label"] = STATUS_LABELS.get(row["status"], row["status"])
        row["source_label"] = SOURCE_LABELS.get(row["source"], row["source"])
        row["classification_label"] = triage.CLASSES.get(row.get("classification") or "", None)
        return row

    def list(self, source: str | None = None, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses, params = [], []
        if source:
            clauses.append("source = ?")
            params.append(source)
        if status == "open":
            clauses.append(f"status IN ({','.join('?' * len(OPEN_STATUSES))})")
            params.extend(OPEN_STATUSES)
        elif status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT id, source, source_run_id, status, category, product_defect, title, severity, priority, duplicates,"
            f" occurrences, test_case_id, tfs_id, tfs_url, note, classification, confidence, source_ref, correlation_id,"
            f" approved_by, published_at, facts, created_at, updated_at FROM defect_drafts {where}"
            f" ORDER BY updated_at DESC LIMIT ?", [*params, limit])
        for r in rows:
            dups = loads(r.pop("duplicates")) if r.get("duplicates") else []
            r["top_duplicate"] = dups[0] if dups else None
            r["occurrences"] = len(loads(r["occurrences"]))
            r["product_defect"] = bool(r["product_defect"])
            facts = loads(r.pop("facts")) if r.get("facts") else {}
            r["failed_step"] = facts.get("failed_step_description") or facts.get("step") or facts.get("field")
            r["script_id"] = facts.get("script_id")
            r["status_label"] = STATUS_LABELS.get(r["status"], r["status"])
            r["source_label"] = SOURCE_LABELS.get(r["source"], r["source"])
            r["classification_label"] = triage.CLASSES.get(r.get("classification") or "", None)
        return rows
