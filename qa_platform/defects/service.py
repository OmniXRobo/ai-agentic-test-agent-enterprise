"""Defect Intelligence: drafts, duplicate detection and TFS defect creation.

Creation modes (settings ``defects.creation_mode``):
  review     - failures become drafts; a person reviews and creates them (default)
  automatic  - product failures with no likely duplicate are created in TFS right away;
               anything else stays a draft with the reason
  disabled   - no drafts are made automatically; analysis can still be requested by hand

The same failure seen again (same script step/locator or same plan/SLA) is added
to the open draft as another occurrence instead of a new draft.
"""

from __future__ import annotations

import html
import re
import threading
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

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
from . import analysis

log = get_logger("defects")

LIKELY_DUPLICATE = 0.85
POSSIBLE_DUPLICATE = 0.6
SEVERITY_WORDS = {1: "critical", 2: "high", 3: "medium", 4: "low"}
OPEN_STATUSES = ("draft", "needs_review")


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
                f"SELECT * FROM defect_drafts WHERE signature = ? AND (status IN ({','.join('?' * len(OPEN_STATUSES))})"
                " OR (status = 'created' AND tfs_id IS NOT NULL)) ORDER BY id DESC LIMIT 1",
                (draft["signature"], *OPEN_STATUSES))
            if existing:
                occ = loads(existing["occurrences"])
                if draft["source_run_id"] not in [o["run_id"] for o in occ]:
                    occ.append({"run_id": draft["source_run_id"], "at": ts})
                    self.db.execute("UPDATE defect_drafts SET occurrences = ?, updated_at = ? WHERE id = ?",
                                    (dumps(occ), ts, existing["id"]))
                    if existing["tfs_id"]:
                        self._comment_recurrence(existing["tfs_id"], draft["source_run_id"])
                return self.get(existing["id"])
            cur = self.db.execute(
                "INSERT INTO defect_drafts (source, source_run_id, signature, status, category, product_defect, title,"
                " severity, priority, repro_steps, facts, evidence, occurrences, test_case_id, user_story_id,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (draft["source"], draft["source_run_id"], draft["signature"],
                 "draft" if draft["product_defect"] else "needs_review", draft["category"], int(draft["product_defect"]),
                 draft["title"], draft["severity"], draft["priority"], draft["repro_steps"], dumps(draft["facts"]),
                 dumps(draft["evidence"]), dumps([{"run_id": draft["source_run_id"], "at": ts}]),
                 draft.get("test_case_id"), draft.get("user_story_id"), ts, ts))
            return {**self.get(cur.lastrowid), "_new": True}

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
        draft = self.get(draft_id)
        reason = None
        if not draft["product_defect"]:
            reason = "Automatic mode only creates product failures; this one needs a person to review it."
        elif draft["duplicates"] and draft["duplicates"][0]["score"] >= LIKELY_DUPLICATE:
            reason = f"Not created: likely duplicate of #{draft['duplicates'][0]['id']}."
        elif draft["note"]:
            reason = f"Not created: {draft['note']}"
        if reason:
            self.db.execute("UPDATE defect_drafts SET note = ?, updated_at = ? WHERE id = ?", (reason, now_iso(), draft_id))
            return
        try:
            self.create(draft_id)
        except QAError as exc:
            log.info(f"Automatic defect creation failed: {exc.message}")

    def _severity_value(self, type_name: str, level: int) -> str | None:
        allowed = self.tfs.allowed_values(type_name, "Microsoft.VSTS.Common.Severity")
        for value in allowed:
            if value.strip().startswith(str(level)):
                return value
        word = SEVERITY_WORDS[level]
        return next((v for v in allowed if word in v.lower()), None)

    def create(self, draft_id: int, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        if overrides:
            self.update(draft_id, overrides)
        draft = self.get(draft_id)
        if draft["status"] == "created":
            raise QAError("DEFECT_ALREADY_CREATED", f"This draft is already defect #{draft['tfs_id']}.", status=409)
        with self.agent.activity(f"Creating defect in TFS: {draft['title'][:80]}", run_id=draft["source_run_id"],
                                 test_case_id=draft.get("test_case_id"), user_story_id=draft.get("user_story_id")) as act:
            try:
                type_name = self.tfs.default_type("bug")  # Bug, Defect or Issue, from the process
                values: dict[str, Any] = {
                    "System.Title": draft["title"],
                    "Microsoft.VSTS.TCM.ReproSteps": draft["repro_steps"],
                    "Microsoft.VSTS.Common.Priority": draft["priority"],
                    "Microsoft.VSTS.Common.Severity": self._severity_value(type_name, draft["severity"]),
                    "System.Tags": "QA-Automation; " + ("Performance" if draft["source"] == "performance" else "Functional"),
                }
                patch, skipped = self.tfs.build_patch(type_name, values)
                if values["Microsoft.VSTS.Common.Severity"] is None:
                    has_field = "Microsoft.VSTS.Common.Severity" in self.tfs.type_field_refs(type_name)
                    skipped.append("Microsoft.VSTS.Common.Severity" + ("" if not has_field else " (no matching allowed value)"))
                if "Microsoft.VSTS.TCM.ReproSteps" in skipped:  # e.g. an Issue type without repro steps
                    extra, _ = self.tfs.build_patch(type_name, {"System.Description": draft["repro_steps"]})
                    patch += extra
                for wid, rel in ((draft.get("test_case_id"), "test case"), (draft.get("user_story_id"), "user story")):
                    if wid:
                        patch.append({"op": "add", "path": "/relations/-", "value": {
                            "rel": "System.LinkTypes.Related", "url": self.tfs.work_item_url(int(wid)),
                            "attributes": {"comment": f"Failing {rel} (automated run {draft['source_run_id']})"}}})
                attached = []
                for ev in draft["evidence"]:
                    path = Path(ev["path"])
                    if path.is_file() and path.stat().st_size <= 20 * 1024 * 1024:
                        up = self.tfs.upload_attachment(path.name, path.read_bytes())
                        patch.append({"op": "add", "path": "/relations/-", "value": {
                            "rel": "AttachedFile", "url": up["url"], "attributes": {"comment": "Automated test evidence"}}})
                        attached.append(path.name)
                item = self.tfs.create_raw(type_name, patch)
            except QAError as exc:
                self.agent.emit(names.DEFECT_FAILED, f"Defect could not be created: {exc.message}",
                                run_id=draft["source_run_id"], details={"draft_id": draft_id})
                self.db.execute("UPDATE defect_drafts SET note = ?, updated_at = ? WHERE id = ?",
                                (f"Creation failed: {exc.message}", now_iso(), draft_id))
                raise
            self.db.execute("UPDATE defect_drafts SET status = 'created', tfs_id = ?, note = ?, updated_at = ? WHERE id = ?",
                            (item["id"], ("Skipped fields not in this process: " + ", ".join(skipped)) if skipped else None,
                             now_iso(), draft_id))
            act.event(names.DEFECT_CREATED, f"{type_name} #{item['id']}: {draft['title'][:120]}",
                      link=f"#/defects?id={item['id']}", tfs_id=item["id"], attachments=attached, skipped_fields=skipped)
            act.complete(f"{type_name} #{item['id']} created in TFS")
        return {**self.get(draft_id), "type": type_name, "skipped_fields": skipped, "attachments": attached}

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
        self.db.execute("UPDATE defect_drafts SET status = 'linked', tfs_id = ?, updated_at = ? WHERE id = ?",
                        (tfs_id, now_iso(), draft_id))
        self.agent.emit(names.AGENT_LOG, f"Failure linked to existing defect #{tfs_id}", run_id=draft["source_run_id"])
        return self.get(draft_id)

    def dismiss(self, draft_id: int, reason: str) -> dict[str, Any]:
        self.get(draft_id)
        self.db.execute("UPDATE defect_drafts SET status = 'dismissed', note = ?, updated_at = ? WHERE id = ?",
                        (reason or "Dismissed", now_iso(), draft_id))
        return self.get(draft_id)

    def update(self, draft_id: int, values: dict[str, Any]) -> dict[str, Any]:
        draft = self.get(draft_id)
        if draft["status"] in ("created", "linked"):
            raise QAError("DEFECT_LOCKED", "This draft is already in TFS; edit the defect there.", status=409)
        allowed = {k: v for k, v in values.items() if k in ("title", "severity", "priority", "repro_steps") and v is not None}
        if not allowed:
            return draft
        sets = ", ".join(f"{k} = ?" for k in allowed)
        self.db.execute(f"UPDATE defect_drafts SET {sets}, updated_at = ? WHERE id = ?", (*allowed.values(), now_iso(), draft_id))
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
        row["finding"] = analysis.CATEGORIES[row["category"]][2]
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
            f" occurrences, test_case_id, tfs_id, note, created_at, updated_at FROM defect_drafts {where}"
            f" ORDER BY updated_at DESC LIMIT ?", [*params, limit])
        for r in rows:
            dups = loads(r.pop("duplicates")) if r.get("duplicates") else []
            r["top_duplicate"] = dups[0] if dups else None
            r["occurrences"] = len(loads(r["occurrences"]))
            r["product_defect"] = bool(r["product_defect"])
        return rows
