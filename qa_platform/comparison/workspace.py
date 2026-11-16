"""Integration Comparison Workspace (23.8.0): one place to compare two or more applications.

The tester only: adds applications (A, B, C ...), uploads screenshots or captures a page / imports an export,
clicks ANALYZE, approves the suggested field mapping, clicks RUN COMPARISON and reads one results screen.

Behind the scenes this reuses the existing Data Comparison Agent (``service.ComparisonService``): its projects and
applications (with passwords in Windows Credential Manager), the browser extraction framework, file import, the
evidence store (validated, metadata-stripped, read-only screenshots), the audit trail, defects and TFS, and the
deterministic normalization rules. New here:

* ``screen_reader``: a screenshot -> tables, labelled values (KPI cards, forms) and chart values;
* one common data structure per application (records, values, charts), whatever the source;
* a multi-application field map (business fields with one member per application), status value groups,
  calculation rules (``formula``) and chart rules, suggested automatically and saved once approved;
* the comparison of every pair (A vs B, A vs C, B vs C) plus every approved calculation, with PASS / FAIL /
  WARNING per value. A value read from a screenshot with low confidence, a chart without readable values, a record
  not visible on screenshots that may not show everything: all WARNING, never a PASS or a false FAIL.

The applications are only read. Nothing here writes to them.
"""

from __future__ import annotations

import hashlib
import html
import itertools
import json
import re
import threading
import time
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any

from .. import aura, auth, branding
from ..clock import now_iso
from ..errors import QAError
from ..storage.db import dumps, loads
from . import filters as filt
from . import formula, investigate, mapping, ocr, reports, screen_reader, semantics
from .normalize import clean_text, equal, infer_type, is_null, normalize, parse_number, to_json

LOW = 0.80
LETTERS = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
DECISIONS = {"valid_business_difference": "Valid business difference",
             "expected_calculation_difference": "Expected calculation difference",
             "false_positive": "False positive", "actual_defect": "Actual defect",
             "investigate": "Requires further investigation"}
ACCEPTING = {"valid_business_difference", "expected_calculation_difference", "false_positive"}
ORDER = ["FAIL", "NOT_COMPARABLE", "NEEDS_REVIEW", "INCOMPLETE", "WARNING", "ACCEPTED", "PASS*", "PASS"]
OPEN_RESULTS = ("FAIL", "NOT_COMPARABLE", "NEEDS_REVIEW", "INCOMPLETE", "WARNING")
# 23.9.0: a check that is not a clear pass or fail says WHY it is not: the data was incomplete, a person must look
# (low OCR confidence, unapproved mapping, chart without labels), or the values cannot be compared (units, scope)
CATEGORY_RESULT = {
    "value_not_read": "INCOMPLETE", "source_missing": "INCOMPLETE", "partial_data": "INCOMPLETE",
    "not_on_screenshots": "INCOMPLETE", "field_not_found": "INCOMPLETE", "extraction_incomplete": "INCOMPLETE",
    "key_missing": "INCOMPLETE",
    "mapping_not_approved": "NEEDS_REVIEW", "rule_not_approved": "NEEDS_REVIEW", "status_not_mapped": "NEEDS_REVIEW",
    "ocr_uncertainty": "NEEDS_REVIEW", "visual_check": "NEEDS_REVIEW",
    "calculation_not_possible": "NOT_COMPARABLE", "unit_mismatch": "NOT_COMPARABLE", "scope_mismatch": "NOT_COMPARABLE",
}
# the tester's classification of a difference (Part 7)
CLASSIFICATION = {"actual_defect": "Genuine defect", "valid_business_difference": "Expected difference",
                  "expected_calculation_difference": "Expected difference", "false_positive": "False positive",
                  "investigate": "Further review"}
SUGGESTED = {"value_mismatch": "actual_defect", "date_mismatch": "actual_defect", "calculation_mismatch": "actual_defect",
             "chart_mismatch": "actual_defect", "status_mismatch": "actual_defect", "missing_record": "actual_defect",
             "duplicate_record": "actual_defect", "rounding_difference": "expected_calculation_difference",
             "approximate_match": "investigate", "ocr_uncertainty": "investigate", "visual_check": "investigate",
             "unit_mismatch": "investigate", "scope_mismatch": "investigate",
             "different_business_rules": "valid_business_difference"}
EXTRA_SYNONYMS = [
    {"sales", "revenue", "turnover", "income", "sale"},
    {"completed", "complete", "completion", "successful", "success", "fulfilled", "done", "closed"},
    {"pending", "waiting", "open", "outstanding", "awaiting"},
    {"cancelled", "canceled", "void", "rejected"},
    {"reference", "ref", "code", "id"},
]
STATUS_GROUPS = [
    {"completed", "complete", "successful", "success", "done", "fulfilled", "finished", "delivered", "paid",
     "approved"},
    {"pending", "waiting", "open", "in progress", "outstanding", "awaiting", "new", "processing", "on hold"},
    {"cancelled", "canceled", "void", "rejected", "aborted", "terminated", "closed - cancelled"},
    {"active", "enabled", "live"},
    {"inactive", "disabled", "suspended"},
]
MONTHS = {m[:3]: m for m in ("january", "february", "march", "april", "may", "june", "july", "august",
                             "september", "october", "november", "december")}


STATUS_RANK = {"FAILED": 0, "PARTIAL": 1, "NEEDS_REVIEW": 2, "COMPLETE": 3}


def _worst_status(items: list[str]) -> str:
    return min(items, key=lambda s: STATUS_RANK.get(s, 0)) if items else "FAILED"


def _legacy_status(parsed: dict[str, Any]) -> str:
    """A screenshot read by 23.8.0 (no status stored): COMPLETE only when everything was read with confidence."""
    if not (parsed.get("tables") or parsed.get("values") or parsed.get("charts")):
        return "FAILED"
    if parsed.get("low_confidence") or any(not c.get("extracted", True) for c in parsed.get("charts") or []):
        return "NEEDS_REVIEW"
    return "COMPLETE"


def extract_web_safe(url: str) -> str:
    from .extract_web import _safe_url

    return _safe_url(url)


def _id(prefix: str) -> str:
    import uuid

    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _user() -> Any:
    return auth.current_user() or auth.LOCAL_PROGRAM


def _family(ftype: str, label: str, samples: list[Any]) -> str:
    low = (label or "").lower()
    if re.search(r"\b(status|state|stage|condition)\b", low):
        return "status"
    if ftype == "percent":
        return "pct"
    if ftype in ("number", "currency"):
        return "num"
    if ftype in ("date", "datetime"):
        return "date"
    return "text"


def _ntype(family: str) -> str:
    return {"num": "number", "pct": "percent", "date": "date"}.get(family, "text")


def key_norm(value: Any) -> str | None:
    if value is None or is_null(value):
        return None
    t = clean_text(value).strip(" .,;:#")
    n = parse_number(t) if re.fullmatch(r"[\d,.]+", t) else None
    if n is not None and n == n.to_integral() and not t.startswith("0"):
        return str(int(n))
    return t.lower() or None


def cat_norm(value: Any) -> str:
    t = clean_text(value).lower().strip(" .,:")
    return MONTHS.get(t[:3], t) if t[:3] in MONTHS and (len(t) == 3 or MONTHS[t[:3]].startswith(t)) else t


def _cv(cell: Any) -> Any:
    return cell.get("value") if isinstance(cell, dict) else cell


def _label_score(a: str, b: str) -> float:
    def stem(s: str) -> str:
        return " ".join(formula._stem(w) for w in re.split(r"[^A-Za-z0-9#]+", s or "") if w)
    ta, tb = set(stem(a).lower().split()), set(stem(b).lower().split())
    contained = 0.85 if ta and tb and (ta <= tb or tb <= ta) else 0.0
    return max(mapping.label_score(a, b), mapping.label_score(stem(a), stem(b)), contained)


# extend the shared synonym list once (labels such as Revenue ~ Sales, Successful ~ Completed)
for _g in EXTRA_SYNONYMS:
    if _g not in mapping.SYNONYMS:
        mapping.SYNONYMS.append(_g)


class Workspace:
    def __init__(self, svc: Any) -> None:
        self.svc = svc
        self.db = svc.db
        self.rt = svc.rt

    # ---- comparisons and applications ----------------------------------------------------------------------
    def comparisons(self) -> list[dict[str, Any]]:
        out = []
        for p in self.svc.projects():
            last = p.get("last_execution")
            out.append({"id": p["id"], "name": p["name"], "applications": p["applications"],
                        "updated_at": p["updated_at"], "last_result": (last or {}).get("result"),
                        "workspace": bool((p["settings"] or {}).get("workspace"))})
        return out

    def create(self, name: str, applications: list[str] | None = None) -> dict[str, Any]:
        p = self.svc.create_project(name, "Integration comparison", settings={"workspace": True})
        for n in applications or ["Application A", "Application B"]:
            self.add_app(p["id"], {"name": n})
        return self.get(p["id"])

    def _apps(self, pid: str) -> list[dict[str, Any]]:
        apps = self.svc.applications(pid)
        used = {a["config"].get("letter") for a in apps if a["config"].get("letter")}
        for a in apps:  # applications made outside the workspace get the next free letter, once
            if not a["config"].get("letter"):
                letter = next(x for x in LETTERS + [f"A{x}" for x in LETTERS] if x not in used)
                used.add(letter)
                a["config"]["letter"] = letter
                self.db.execute("UPDATE cmp_applications SET config = ? WHERE id = ?", (dumps(a["config"]), a["id"]))
        apps.sort(key=lambda a: (len(a["config"]["letter"]), a["config"]["letter"]))
        for a in apps:
            a["letter"] = a["config"]["letter"]
            a["inputs"] = self._inputs(a)
            a["screens_complete"] = bool(a["config"].get("screens_complete"))
        return apps

    def _inputs(self, app: dict[str, Any]) -> dict[str, Any]:
        shots = self.db.query("SELECT e.id, e.original_name, e.width, e.height, e.captured_at, s.status, s.error "
                              "FROM cmp_evidence e LEFT JOIN cmp_ws_screens s ON s.evidence_id = e.id WHERE "
                              "e.application_id = ? AND e.source = 'workspace screenshot' ORDER BY e.captured_at",
                              (app["id"],))
        ds_ids = app["config"].get("ws_datasets") or []
        datasets = []
        for ds in ds_ids:
            row = self.db.query_one("SELECT id, kind, method, status, stats, created_at FROM cmp_datasets WHERE id = ?",
                                    (ds,))
            if row:
                st = loads(row["stats"]) or {}
                datasets.append({"id": row["id"], "kind": row["kind"], "method": row["method"],
                                 "status": row["status"], "records": st.get("records"), "file": st.get("file"),
                                 "reason": st.get("reason"), "created_at": row["created_at"]})
        return {"screenshots": shots, "datasets": datasets}

    def get(self, pid: str) -> dict[str, Any]:
        proj = self.svc.project(pid)
        state = self.db.query_one("SELECT analysis, updated_at FROM cmp_ws_state WHERE project_id = ?", (pid,))
        cfg = self.latest_config(pid)
        runs = self.db.query("SELECT id, result, summary, started_at, created_by FROM cmp_executions WHERE "
                             "project_id = ? AND mode = 'workspace' ORDER BY started_at DESC LIMIT 30", (pid,))
        for r in runs:
            r["summary"] = loads(r["summary"])
        return {"comparison": {"id": proj["id"], "name": proj["name"], "owner": proj["owner"]},
                "applications": [self._public_app(a) for a in self._apps(pid)],
                "analysis": loads(state["analysis"]) if state else None,
                "analyzed_at": state["updated_at"] if state else None,
                "saved_mapping": cfg, "runs": runs}

    @staticmethod
    def _public_app(a: dict[str, Any]) -> dict[str, Any]:
        c = a["config"]
        return {"id": a["id"], "letter": a["letter"], "name": a["name"], "url": a.get("base_url"),
                "auth_method": a["auth_method"], "username": c.get("username"), "has_password": a["has_password"],
                "inputs": a["inputs"], "screens_complete": a["screens_complete"],
                "connection": a.get("connection_status")}

    def add_app(self, pid: str, data: dict[str, Any]) -> dict[str, Any]:
        apps = self._apps(pid)
        used = {a["letter"] for a in apps}
        letter = next(x for x in LETTERS + [f"A{x}" for x in LETTERS] if x not in used)
        name = (data.get("name") or f"Application {letter}").strip()[:120]
        body = {"name": name, "app_type": "web" if data.get("url") else "screenshot",
                "base_url": data.get("url") or None, "auth_method": data.get("auth_method") or "none",
                "username": data.get("username"), "password": data.get("password"),
                "config": {"letter": letter}}
        app = self.svc.save_application(pid, body)
        return self._public_app(next(a for a in self._apps(pid) if a["id"] == app["id"]))

    def _app(self, aid: str) -> dict[str, Any]:
        row = self.svc._app_row(aid)
        return next(a for a in self._apps(row["project_id"]) if a["id"] == aid)

    def update_app(self, aid: str, data: dict[str, Any]) -> dict[str, Any]:
        app = self._app(aid)
        url = data.get("url", app.get("base_url"))
        body = {"name": (data.get("name") or app["name"]).strip()[:120], "app_type": "web" if url else "screenshot",
                "base_url": url or None, "auth_method": data.get("auth_method") or app["auth_method"],
                "username": data.get("username", app["config"].get("username")), "password": data.get("password"),
                "login_url": data.get("login_url"), "environment": app.get("environment")}
        self.svc.save_application(app["project_id"], body, aid)
        if "screens_complete" in data:
            self._set_config(aid, screens_complete=bool(data["screens_complete"]))
        return self._public_app(self._app(aid))

    def _set_config(self, aid: str, **values: Any) -> None:
        row = self.db.query_one("SELECT config FROM cmp_applications WHERE id = ?", (aid,))
        cfg = {**(loads(row["config"]) or {}), **values}
        self.db.execute("UPDATE cmp_applications SET config = ? WHERE id = ?", (dumps(cfg), aid))

    def remove_app(self, aid: str) -> None:
        self.svc.delete_application(aid)

    # ---- inputs: screenshots, captures, exported files ------------------------------------------------------
    def upload_screenshot(self, aid: str, data: bytes, name: str | None) -> dict[str, Any]:
        app = self._app(aid)
        ev = self.svc._save_evidence(app, data, source="workspace screenshot", original_name=(name or "")[:200])
        at = now_iso()
        self.db.execute("INSERT INTO cmp_ws_screens (evidence_id, project_id, application_id, status, created_at, "
                        "updated_at) VALUES (?,?,?,?,?,?)", (ev["id"], app["project_id"], aid, "new", at, at))
        self.svc.audit("workspace.screenshot", app["project_id"], ev["id"], application=aid, sha256=ev["sha256"])
        return {"id": ev["id"], "width": ev["width"], "height": ev["height"], "status": "new"}

    def remove_input(self, aid: str, input_id: str) -> None:
        app = self._app(aid)
        ds = [d for d in app["config"].get("ws_datasets") or [] if d != input_id]
        self._set_config(aid, ws_datasets=ds)
        # the screenshot file stays in the evidence store (evidence is never deleted); it no longer counts as input
        self.db.execute("UPDATE cmp_evidence SET source = 'workspace screenshot (removed)' WHERE id = ? AND "
                        "application_id = ?", (input_id, aid))
        self.db.execute("DELETE FROM cmp_ws_screens WHERE evidence_id = ?", (input_id,))
        self.svc.audit("workspace.input.remove", app["project_id"], input_id, application=aid)

    def import_file(self, aid: str, filename: str, content: bytes) -> dict[str, Any]:
        app = self._app(aid)
        ds = self.svc.import_file(aid, filename, content, {})
        self._set_config(aid, ws_datasets=(app["config"].get("ws_datasets") or []) + [ds["id"]])
        return {"id": ds["id"], "records": ds["stats"].get("records")}

    def capture(self, aid: str, data: dict[str, Any]) -> dict[str, Any]:
        """Read the application's page in the browser (existing extraction framework): the table (every page) and
        the KPI cards and charts. Read-only: the steps only navigate."""
        app = self._app(aid)
        if not app.get("base_url"):
            raise QAError("WS_URL_REQUIRED", "Enter the application's page address first.", status=422)
        steps = data.get("steps") or []
        # 23.9.0: one browser session reads the whole page (table, other tables, KPI cards, charts, OCR fallback)
        spec = {"kind": "page", "steps": steps, "screenshots": True,
                "screen_name": data.get("page_name") or app["name"], "target": data.get("target") or {}}
        if data.get("filters_spec"):
            spec.update(data["filters_spec"])
        jobs = [self.svc.start_extraction(aid, spec)]
        self._set_config(aid, ws_datasets=(app["config"].get("ws_datasets") or []) + [j["dataset_id"] for j in jobs])
        return {"jobs": jobs}

    # ---- 23.9.0 filters: discover, configure, apply & fetch, sync, filter sets -------------------------------
    def _sessions(self) -> dict[str, Any]:
        if not hasattr(self, "_filter_sessions"):
            self._filter_sessions: dict[str, Any] = {}
            self._filter_lock = threading.Lock()
        return self._filter_sessions

    def _session(self, aid: str, visible: bool | None = None, start: bool = True) -> Any:
        app = self._app(aid)
        if not app.get("base_url"):
            raise QAError("WS_URL_REQUIRED", "Enter the application's page address first.", status=422)
        sessions = self._sessions()
        with self._filter_lock:
            s = sessions.get(aid)
            if s is not None and (s.state in ("closed", "failed") or (visible is not None and s.visible != visible)):
                if s.state not in ("closed", "failed"):
                    s.close()
                s = None
            if s is None:
                if not start:
                    return None
                play = self.rt.settings.section("comparison") or {}
                show = bool(visible) if visible is not None else not bool(play.get("headless", True))
                row = self.svc._app_row(aid)

                def make() -> Any:
                    ex = self.svc._extractor(row, _id("NS"))
                    ex.headless = not show
                    return ex
                s = sessions[aid] = filt.FilterSession(make, aid, show)
        for _ in range(300):  # until the page is open (or the session asks for help)
            if s.state != "starting":
                break
            time.sleep(0.1)
        return s

    def filter_status(self, aid: str) -> dict[str, Any]:
        s = self._sessions().get(aid)
        app = self._app(aid)
        return {"session": s.status() if s else {"state": "none"}, "filters": app["config"].get("ws_filters"),
                "applied": app["config"].get("ws_applied_filters") or {},
                "runs": (app["config"].get("ws_filter_runs") or [])[-20:]}

    def discover_filters(self, aid: str, visible: bool | None = None) -> dict[str, Any]:
        """Find the filter controls of the application's page (Discover & Configure Filters)."""
        app = self._app(aid)
        s = self._session(aid, visible)

        def work(ex: Any) -> dict[str, Any]:
            raw = ex.page.evaluate(filt.FILTERS_JS)
            controls = filt.classify(raw)
            filt.dependencies(ex, controls)
            return {"controls": controls, "buttons": filt.buttons(raw), "rows": raw.get("rows"),
                    "url": extract_web_safe(ex.page.url)}
        found = s.call(work)
        old = {c["label"]: c for c in (app["config"].get("ws_filters") or {}).get("controls") or []}
        for c in found["controls"]:  # the tester's corrections survive a new discovery
            prev = old.get(c["label"])
            if prev and prev.get("edited"):
                c.update({k: prev[k] for k in ("is_filter", "default", "edited", "display") if k in prev})
        cfg = {**found, "discovered_at": now_iso()}
        self._set_config(aid, ws_filters=cfg)
        self.svc.audit("workspace.filters.discover", app["project_id"], aid,
                       filters=sum(1 for c in found["controls"] if c["is_filter"]), controls=len(found["controls"]))
        return cfg

    def save_filters(self, aid: str, controls: list[dict[str, Any]]) -> dict[str, Any]:
        """The tester's configuration: which controls are filters, their names and default values."""
        app = self._app(aid)
        cur = app["config"].get("ws_filters") or {}
        by_id = {c["id"]: c for c in cur.get("controls") or []}
        for c in controls:
            if c.get("id") in by_id:
                t = by_id[c["id"]]
                if "is_filter" in c:
                    t["is_filter"] = bool(c["is_filter"])
                if c.get("display"):
                    t["display"] = str(c["display"])[:80]
                if "default" in c:
                    t["default"] = c["default"]
                t["edited"] = True
        self._set_config(aid, ws_filters=cur)
        self.svc.audit("workspace.filters.save", app["project_id"], aid)
        return cur

    def reset_filters(self, aid: str) -> dict[str, Any]:
        """Clear the page's filters (its own Reset / Clear button) and forget the applied values."""
        app = self._app(aid)
        btn = ((app["config"].get("ws_filters") or {}).get("buttons") or {}).get("reset")
        s = self._session(aid)

        def work(ex: Any) -> None:
            if btn:
                ex.page.locator(btn["css"]).first.click()
            else:
                ex.page.reload()
            ex._settle()
            ex.wait_for_data(8)
        s.call(work)
        self._set_config(aid, ws_applied_filters={})
        return {"reset": True}

    def preview_filters(self, aid: str) -> dict[str, Any]:
        """A screenshot of the page as it is now in the session (after applying)."""
        app = self._app(aid)
        s = self._session(aid)
        png = s.call(lambda ex: ex.screenshot())
        ev = self.svc._save_evidence(app, png, source="filter preview", screen=app["name"])
        return {"evidence": ev["id"]}

    def resume_filters(self, aid: str) -> dict[str, Any]:
        s = self._sessions().get(aid)
        if s is None:
            raise QAError("FILTER_SESSION_CLOSED", "There is no browser session to resume.", status=409)
        s.resume()
        return s.status()

    def close_filters(self, aid: str) -> None:
        s = self._sessions().pop(aid, None)
        if s is not None:
            s.close()

    def apply_fetch(self, aid: str, values: dict[str, Any], *, set_name: str | None = None,
                    wait: bool = False) -> dict[str, Any]:
        """Apply Filters & Fetch Data: through the real page, in the application's own session. Runs in the
        background; the job reports progress, a pause for the tester, and the new dataset."""
        app = self._app(aid)
        conf = app["config"].get("ws_filters") or {}
        controls = [c for c in conf.get("controls") or [] if c.get("is_filter")]
        known = {c["id"] for c in controls}
        values = {k: v for k, v in (values or {}).items() if k in known or k.startswith("local:")}
        local = {k[6:]: v for k, v in values.items() if k.startswith("local:")}
        ui_values = {k: v for k, v in values.items() if not k.startswith("local:")}
        job = _id("JOB")
        state = {"id": job, "dataset_id": None, "status": "running", "progress": "opening the application",
                 "error": None, "set": set_name}
        self.svc.jobs[job] = state
        user = _user()

        def body() -> None:
            token = auth.set_current_user(user)
            try:
                s = self._session(aid)
                if s.state == "paused":
                    state["progress"] = "paused: " + ((s.paused or {}).get("message") or "the tester must help")
                labels = {c["id"]: c.get("display") or c["label"] for c in controls}

                def work(ex: Any) -> dict[str, Any]:
                    state["progress"] = "setting the filters"
                    res = filt.apply(ex, controls, ui_values, conf.get("buttons"),
                                     log=lambda m: state.__setitem__("progress", m))
                    applied = {labels.get(k, k): v for k, v in ui_values.items() if v not in (None, "", [])}
                    applied.update({f"local:{f}": v for f, v in local.items()})
                    ds = self.svc._new_dataset(self.svc._app_row(aid), "page", "dom", applied)
                    state["dataset_id"] = ds
                    spec = {"kind": "page", "screenshots": True, "screen_name": app["name"],
                            "target": conf.get("target") or {}}
                    self.svc._capture_page(self.svc._app_row(aid), ds, ex, spec, state, applied)
                    return {"dataset": ds, "applied": applied, "verified": res["verified"]}
                out = s.call(work, timeout=3600)
                d = self.svc.dataset(out["dataset"])
                st = d.get("stats") or {}
                run = {"at": now_iso(), "dataset": out["dataset"], "filters": out["applied"], "set": set_name,
                       "records": st.get("records"), "expected_total": st.get("expected_total"),
                       "complete": bool(st.get("complete")), "method": st.get("method"), "by": user.username}
                cur = self._app(aid)["config"]
                self._set_config(aid, ws_datasets=[out["dataset"]],
                                 ws_dataset_history=(cur.get("ws_dataset_history") or []) + (cur.get("ws_datasets") or []),
                                 ws_applied_filters=out["applied"],
                                 ws_filter_runs=(cur.get("ws_filter_runs") or [])[-49:] + [run])
                state.update(status="done", progress=f"{st.get('records') or 0} record(s) fetched", run=run,
                             verified=out["verified"])
                self.svc.audit("workspace.filters.fetch", app["project_id"], aid, dataset=out["dataset"],
                               filters=list(out["applied"]), records=st.get("records"), set=set_name)
            except QAError as exc:
                state.update(status="failed", error=exc.message, code=exc.code)
            except Exception as exc:
                state.update(status="failed", error=str(exc).splitlines()[0][:300])
            finally:
                auth.reset_current_user(token)

        if wait:
            body()
        else:
            threading.Thread(target=body, daemon=True, name=f"cmp-filter-fetch-{aid}").start()
        return {"job": job}

    # cross-application filter links and filter sets (kept in the comparison's settings)
    def _psettings(self, pid: str) -> dict[str, Any]:
        return self.svc.project(pid)["settings"] or {}

    def _save_psettings(self, pid: str, **values: Any) -> None:
        cur = self._psettings(pid)
        cur.update(values)
        self.db.execute("UPDATE cmp_projects SET settings = ?, updated_at = ? WHERE id = ?", (dumps(cur), now_iso(), pid))

    def filter_links(self, pid: str) -> dict[str, Any]:
        apps = self._apps(pid)
        saved = self._psettings(pid).get("ws_filter_links") or []
        info = [{"id": a["id"], "name": a["name"], "filters": (a["config"].get("ws_filters") or {}).get("controls") or []}
                for a in apps]
        suggested = filt.suggest_links(info)
        have = {l_["name"] for l_ in saved}
        return {"links": saved + [l_ for l_ in suggested if l_["name"] not in have], "saved": bool(saved),
                "fields": {a["id"]: [f["name"] for f in self.app_data(a)["fields"] if f["kind"] == "column"]
                           for a in apps}}

    def save_filter_links(self, pid: str, links: list[dict[str, Any]]) -> list[dict[str, Any]]:
        apps = {a["id"] for a in self._apps(pid)}
        clean = []
        for l_ in links:
            members = {aid: {k: str(v)[:120] for k, v in (m or {}).items() if k in ("filter", "label", "local_field")}
                       for aid, m in (l_.get("members") or {}).items() if aid in apps and m}
            if len(members) < 2:
                continue
            clean.append({"name": str(l_.get("name") or "Filter")[:80], "members": members,
                          "status": "approved" if l_.get("approve") or l_.get("status") == "approved" else "suggested",
                          "value_map": {aid: {str(a)[:120]: str(b)[:120] for a, b in (vm or {}).items()}
                                        for aid, vm in (l_.get("value_map") or {}).items() if aid in apps}})
        self._save_psettings(pid, ws_filter_links=clean)
        self.svc.audit("workspace.filters.links", pid, pid, approved=sum(1 for x in clean if x["status"] == "approved"))
        return clean

    def sync_filters(self, pid: str, source_app: str, values: dict[str, Any]) -> dict[str, Any]:
        """Sync Equivalent Filters: the values for the other applications, for the tester to check and apply."""
        apps = {a["id"]: {"name": a["name"], "filters": (a["config"].get("ws_filters") or {}).get("controls") or []}
                for a in self._apps(pid)}
        if source_app not in apps:
            raise QAError("WS_APP_NOT_FOUND", "Unknown application.", status=404)
        links = self.filter_links(pid)["links"]
        return filt.sync_values(links, apps, source_app, values or {})

    def save_filter_sets(self, pid: str, sets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        apps = {a["id"] for a in self._apps(pid)}
        clean = [{"id": s_.get("id") or _id("FS"), "name": str(s_.get("name") or "Filter set")[:80],
                  "values": {aid: dict(v or {}) for aid, v in (s_.get("values") or {}).items() if aid in apps},
                  "approved": bool(s_.get("approved"))} for s_ in sets[:50]]
        self._save_psettings(pid, ws_filter_sets=clean)
        return clean

    def run_filter_sets(self, pid: str) -> dict[str, Any]:
        """Run All Filter Sets: only the scenarios the tester saved and approved, never every combination. Each set
        fetches every application with its values and compares the filtered data."""
        sets = [s_ for s_ in self._psettings(pid).get("ws_filter_sets") or [] if s_.get("approved")]
        if not sets:
            raise QAError("WS_NO_FILTER_SETS", "Save and approve at least one filter set first.", status=422)
        job = _id("JOB")
        state = {"id": job, "status": "running", "progress": "starting", "error": None, "results": []}
        self.svc.jobs[job] = state
        user = _user()

        def body() -> None:
            token = auth.set_current_user(user)
            try:
                for s_ in sets:
                    for aid, vals in s_["values"].items():
                        state["progress"] = f"{s_['name']}: fetching {self._app(aid)['name']}"
                        j = self.apply_fetch(aid, vals, set_name=s_["name"], wait=True)
                        r = self.svc.jobs[j["job"]]
                        if r["status"] != "done":
                            raise QAError("FILTER_SET_FAILED", f"{s_['name']}: {r.get('error')}", status=409)
                    state["progress"] = f"{s_['name']}: comparing"
                    run_ = self.run(pid, label=s_["name"])
                    state["results"].append({"set": s_["name"], "run": run_["id"], "result": run_["result"]})
                state.update(status="done", progress=f"{len(sets)} filter set(s) compared")
            except QAError as exc:
                state.update(status="failed", error=exc.message)
            except Exception as exc:
                state.update(status="failed", error=str(exc).splitlines()[0][:300])
            finally:
                auth.reset_current_user(token)
        threading.Thread(target=body, daemon=True, name=f"cmp-filter-sets-{pid}").start()
        return {"job": job, "sets": len(sets)}

    def correct(self, pid: str, evidence_id: str, where: dict[str, Any], value: str) -> dict[str, Any]:
        """A tester corrects a value read from a screenshot. The OCR reading is kept next to it."""
        row = self.db.query_one("SELECT * FROM cmp_ws_screens WHERE evidence_id = ? AND project_id = ?",
                                (evidence_id, pid))
        if row is None or not row["parsed"]:
            raise QAError("WS_SCREEN_NOT_FOUND", "Analyze the screenshot first.", status=404)
        parsed = loads(row["parsed"])
        kind = where.get("kind")
        try:
            if kind == "table":
                cell = parsed["tables"][int(where["table"])]["rows"][int(where["row"])].setdefault(
                    where["field"], {"value": "", "confidence": 0.0, "method": "ocr"})
            elif kind == "value":
                cell = parsed["values"][int(where["index"])]
            elif kind == "chart":
                cell = parsed["charts"][int(where["chart"])]["points"].setdefault(
                    where["category"], {"value": "", "confidence": 0.0, "method": "ocr"})
            elif kind == "label":
                parsed["values"][int(where["index"])]["label"] = str(value)[:120]
                cell = None
            elif kind == "header":
                t = parsed["tables"][int(where["table"])]
                old = t["headers"][int(where["column"])]
                t["headers"][int(where["column"])] = str(value)[:120]
                for r in t["rows"]:
                    if old in r:
                        r[str(value)[:120]] = r.pop(old)
                cell = None
            else:
                raise KeyError(kind)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise QAError("WS_CORRECTION_INVALID", "That value is not on this screenshot.", status=422) from exc
        if cell is not None:
            if "ocr_value" not in cell:
                cell["ocr_value"] = cell.get("value")
            cell.update(value=str(value)[:200], confidence=1.0, method="tester")
        if kind == "chart":
            ch = parsed["charts"][int(where["chart"])]
            ch["extracted"] = all(c in ch["points"] and ch["points"][c].get("value") not in (None, "")
                                  for c in ch.get("categories") or [])
        self.db.execute("UPDATE cmp_ws_screens SET parsed = ?, updated_at = ? WHERE evidence_id = ?",
                        (dumps(parsed), now_iso(), evidence_id))
        self.svc.audit("workspace.correct", pid, evidence_id, where=where, value=str(value)[:200])
        return parsed

    # ---- analysis ---------------------------------------------------------------------------------------------
    def _read_screens(self, app: dict[str, Any], reread: bool = False) -> None:
        for shot in app["inputs"]["screenshots"]:
            row = self.db.query_one("SELECT * FROM cmp_ws_screens WHERE evidence_id = ?", (shot["id"],))
            if row and row["parsed"] and not reread:
                continue
            ev = self.db.query_one("SELECT * FROM cmp_evidence WHERE id = ?", (shot["id"],))
            try:
                path = self.svc.evidence.path(app["project_id"], ev["file"])
                res = ocr.read_for_layout(path, self.rt.settings)
                g = res.pop("graphics", None)
                if g is None:  # Windows OCR / Tesseract: chart shapes from the bundled runtime when it is there
                    g = ocr.graphics(path, self.rt.settings)
                parsed = screen_reader.read_screen(res, g)
                parsed["engine"] = res.get("engine")
                parsed["attempts"] = res.get("attempts") or []
                status, error = parsed["status"], None
                if status == "FAILED":
                    error = "; ".join(parsed.get("issues") or []) or "nothing could be read"
                self.db.execute("UPDATE cmp_evidence SET ocr = ? WHERE id = ?", (dumps(res), shot["id"]))
            except QAError as exc:
                parsed, status, error = None, "FAILED", exc.message
            at = now_iso()
            if row:
                self.db.execute("UPDATE cmp_ws_screens SET parsed = ?, status = ?, error = ?, updated_at = ? WHERE "
                                "evidence_id = ?", (dumps(parsed) if parsed else None, status, error, at, shot["id"]))
            else:
                self.db.execute("INSERT INTO cmp_ws_screens (evidence_id, project_id, application_id, parsed, status,"
                                " error, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                                (shot["id"], app["project_id"], app["id"], dumps(parsed) if parsed else None, status,
                                 error, at, at))

    def app_data(self, app: dict[str, Any]) -> dict[str, Any]:
        """The common structure every comparison works on, whatever the source of the data."""
        try:
            semantics.configure((self.rt.settings.section("comparison") or {}).get("concept_words"))
        except Exception:  # a broken setting never stops the comparison: the built-in vocabulary is used
            semantics.configure(None)
        tables: list[dict[str, Any]] = []
        values: dict[str, dict[str, Any]] = {}
        charts: list[dict[str, Any]] = []
        notes: list[str] = []
        sources = Counter()
        low = 0
        complete_live = []

        def add_table(headers: list[str], recs: list[dict[str, Any]], source: str, evidence: str | None,
                      complete: bool, origin: dict[str, Any] | None = None, units: dict[str, str] | None = None,
                      status: str | None = None) -> None:
            sig = tuple(h.strip().lower() for h in headers)
            same = next((t for t in tables if t["sig"] == sig and t["source"] == source), None)
            if same is None:
                same = {"id": f"T{len(tables) + 1}", "sig": sig, "headers": list(headers), "records": [],
                        "source": source, "evidence": [], "complete": complete, "origins": [],
                        "units": dict(units or {}), "statuses": []}
                tables.append(same)
            if status:
                same["statuses"].append(status)
            seen = {json.dumps({k: str(_cv(v)) for k, v in r["fields"].items()}, sort_keys=True)
                    for r in same["records"]}
            for r in recs:
                sig_r = json.dumps({k: str(_cv(v)) for k, v in r["fields"].items()}, sort_keys=True)
                if source == "screenshot" and sig_r in seen:
                    continue  # the same row on two overlapping screenshots
                seen.add(sig_r)
                same["records"].append(r)
            if evidence:
                same["evidence"].append(evidence)
            if origin:
                same["origins"].append(origin)

        inputs: list[dict[str, Any]] = []
        for shot in app["inputs"]["screenshots"]:
            row = self.db.query_one("SELECT parsed, status, error FROM cmp_ws_screens WHERE evidence_id = ?",
                                    (shot["id"],))
            label = shot.get("original_name") or shot["id"]
            if not row or not row["parsed"]:
                if row and row["status"] in ("unreadable", "FAILED"):
                    notes.append(f"Screenshot {label} could not be read: {row['error']}")
                    inputs.append({"id": shot["id"], "kind": "screenshot", "name": label, "status": "FAILED",
                                   "issues": [row["error"]]})
                continue
            sources["screenshot"] += 1
            p = loads(row["parsed"])
            inputs.append({"id": shot["id"], "kind": "screenshot", "name": label,
                           "status": p.get("status") or _legacy_status(p), "issues": p.get("issues") or [],
                           "engine": p.get("engine")})
            for ti, t in enumerate(p["tables"]):
                recs = [{"fields": {h: {**c, "source": "screenshot", "evidence": shot["id"],
                                        "where": {"kind": "table", "table": ti, "row": ri, "field": h}}
                                    for h, c in r.items()}, "source": "screenshot", "evidence": shot["id"]}
                        for ri, r in enumerate(t["rows"])]
                low += sum(1 for r in recs for c in r["fields"].values() if c.get("confidence", 1) < LOW)
                add_table(t["headers"], recs, "screenshot", shot["id"], False, {"evidence": shot["id"], "table": ti},
                          units=t.get("units"), status=t.get("status"))
                for tot in t.get("totals") or []:  # TOTAL / GRAND TOTAL rows: summary values, never records
                    kind = tot.get("kind", "total").replace("_", " ")
                    for h, c in tot["values"].items():
                        if h == next(iter(tot["values"])) or not screen_reader.is_number(str(c.get("value") or "")):
                            continue
                        check = next((x for x in t.get("total_checks") or [] if x["column"] == h and
                                      x["row"] == tot["label"]), None)
                        values.setdefault(f"{h} ({kind})", {**c, "source": "screenshot", "evidence": shot["id"],
                                                            "summary": True, "row_label": tot["label"],
                                                            "total_check": check, "unit": (t.get("units") or {}).get(h)})
            for vi, v in enumerate(p["values"]):
                label = v["label"].strip()
                cell = {**v, "source": "screenshot", "evidence": shot["id"], "where": {"kind": "value", "index": vi}}
                if label in values and str(values[label]["value"]) != str(v["value"]):
                    notes.append(f"'{label}' shows {values[label]['value']} on one screenshot and {v['value']} on "
                                 "another; the first is used.")
                    continue
                values.setdefault(label, cell)
                low += 1 if v.get("confidence", 1) < LOW else 0
            for ci, c in enumerate(p["charts"]):
                charts.append({"title": c["title"], "categories": c.get("categories") or list(c["points"]),
                               "points": {k: {**pt, "where": {"kind": "chart", "chart": ci, "category": k}}
                                          for k, pt in c["points"].items()},
                               "percentages": c.get("percentages") or {}, "type": c.get("type") or "bar",
                               "value_kind": c.get("value_kind") or "amount", "approximate": bool(c.get("approximate")),
                               "status": c.get("status") or ("COMPLETE" if c["extracted"] else "PARTIAL"),
                               "unit": c.get("unit"), "series": c.get("series"),
                               "extracted": c["extracted"], "note": c.get("note"), "source": "screenshot",
                               "evidence": shot["id"], "box": c.get("box")})
        for ds in app["inputs"]["datasets"]:
            kind_label = "file" if ds.get("method") == "file" else "live capture"
            if ds["status"] in ("running",):
                notes.append("A capture is still running; analyze again when it has finished.")
                inputs.append({"id": ds["id"], "kind": kind_label, "status": "PARTIAL",
                               "issues": ["still running"]})
                continue
            if ds["status"] in ("failed", "needs_manual"):
                reason = ds.get("reason") or ds["status"]
                notes.append(f"A {kind_label} failed: {reason}.")
                inputs.append({"id": ds["id"], "kind": kind_label, "status": "FAILED", "issues": [reason]})
                continue
            d = self.svc.dataset(ds["id"], True)
            source = "file" if d["method"] == "file" else "live"
            sources[source] += 1
            st = d.get("stats") or {}
            agg = d.get("aggregates") or {}
            got_any = bool(d["records"]) or bool(agg.get("kpis")) or bool(agg.get("charts")) or bool(agg.get("tables"))
            complete = bool(st.get("complete", True)) and d["status"] == "complete"
            inputs.append({"id": ds["id"], "kind": kind_label, "status": "FAILED" if not got_any else
                           "COMPLETE" if complete else "PARTIAL",
                           "issues": ([st.get("reason")] if st.get("reason") else []) + list(st.get("warnings") or []),
                           "records": len(d["records"]), "expected_total": st.get("expected_total"),
                           "method": st.get("method"), "filters": d.get("filters") or {},
                           "captured_at": d.get("updated_at")})
            if not got_any:
                notes.append(f"The {kind_label} found no data: {st.get('reason') or 'nothing was detected'}.")
            if d["records"]:
                headers = st.get("headers") or list(dict.fromkeys(k for r in d["records"] for k in r["fields"]))
                recs = [{"fields": {h: {**(c if isinstance(c, dict) else {"value": c}), "source": source}
                                    for h, c in r["fields"].items()}, "source": source, "page": r.get("page")}
                        for r in d["records"]]
                complete_live.append(complete)
                if not complete:
                    notes.append(f"The {source} table was not read completely: "
                                 f"{st.get('reason') or 'not every page was read'}.")
                add_table(headers, recs, source, None, complete, units=st.get("units"))
                for tot in st.get("totals") or []:
                    for h, v in (tot.get("values") or {}).items():
                        if screen_reader.is_number(str(v)):
                            values.setdefault(f"{h} ({tot.get('kind', 'total').replace('_', ' ')})",
                                              {"value": v, "confidence": 1.0, "method": "dom", "source": source,
                                               "summary": True, "row_label": tot.get("label")})
            for extra in agg.get("tables") or []:  # further tables of the same page (summary tables)
                if extra.get("rows"):
                    recs = [{"fields": {h: {"value": v, "method": extra.get("method") or "dom", "confidence": 1.0,
                                            "source": source} for h, v in zip(extra["headers"], row)},
                             "source": source} for row in extra["rows"]]
                    add_table(extra["headers"], recs, source, None, complete, units=extra.get("units"))
            for k in agg.get("kpis") or []:
                label = str(k.get("label") or "").strip()
                if label:
                    values.setdefault(label, {"value": k.get("value"), "confidence": float(k.get("confidence") or 1),
                                              "method": k.get("method") or "dom", "source": source,
                                              "evidence": agg.get("screenshot")})
            for ch in agg.get("charts") or []:
                multi = len(ch.get("series_values") or []) > 1
                for s_ in ch.get("series_values") or []:
                    pts = {str(lbl): {"value": v, "confidence": float(ch.get("confidence") or 1),
                                      "method": ch.get("method") or "dom"}
                           for lbl, v in (s_.get("values") or {}).items() if v not in (None, "")}
                    title = str(ch.get("title") or "Chart") + (f" — {s_['name']}" if s_.get("name") and multi else "")
                    ctype = str(s_.get("type") or ch.get("type") or "bar")
                    pie = ctype in ("pie", "doughnut", "donut")
                    pct = {}
                    if pie and pts:  # a chart library gives the amounts; the shares follow from them exactly
                        tot_ = sum((formula.to_decimal(p_["value"]) or Decimal(0)) for p_ in pts.values())
                        if tot_:
                            pct = {k: {"value": f"{(formula.to_decimal(p_['value']) or Decimal(0)) / tot_ * 100:.2f}%",
                                       "confidence": 1.0, "method": "computed from the chart data", "derived": True}
                                   for k, p_ in pts.items()}
                    charts.append({"title": title, "categories": list(pts), "points": pts, "extracted": bool(pts),
                                   "percentages": pct, "type": ctype, "value_kind": "amount",
                                   "approximate": False, "status": "COMPLETE" if pts else "FAILED",
                                   "unit": None, "series": s_.get("name") if multi else None,
                                   "note": None if pts else "The chart's values could not be read.",
                                   "source": source, "evidence": agg.get("screenshot")})
        # field list (unique names within the application)
        fields: list[dict[str, Any]] = []
        names: set[str] = set()

        def uniq(name: str, suffix: str) -> str:
            n = name or "Column"
            if n in names:
                n = f"{name} ({suffix})"
            i = 2
            base = n
            while n in names:
                n, i = f"{base} {i}", i + 1
            names.add(n)
            return n

        for t in tables:
            rename = {}
            for h in t["headers"]:
                nm = uniq(h, f"table {t['id'][1:]}")
                rename[h] = nm
                vals = [_cv(r["fields"].get(h)) for r in t["records"]]
                ft = infer_type([v for v in vals if v is not None])
                unit = (t.get("units") or {}).get(h) or semantics.unit_of(h, vals)
                fields.append({"name": nm, "kind": "column", "table": t["id"], "type": ft,
                               "family": _family(ft, h, vals), "samples": [v for v in vals if v not in (None, "")][:50],
                               "source": t["source"], "unit": unit,
                               "concept": semantics.concept(h, ft, vals, unit)})
            if any(k != v for k, v in rename.items()):
                for r in t["records"]:
                    r["fields"] = {rename.get(k, k): v for k, v in r["fields"].items()}
                t["headers"] = [rename[h] for h in t["headers"]]
        renamed_values = {}
        for label, cell in values.items():
            nm = uniq(label, "summary")
            renamed_values[nm] = cell
            ft = infer_type([cell.get("value")])
            unit = cell.get("unit") or semantics.unit_of(label, [cell.get("value")])
            fields.append({"name": nm, "kind": "value", "type": ft, "family": _family(ft, label, [cell.get("value")]),
                           "samples": [cell.get("value")], "source": cell.get("source"), "unit": unit,
                           "summary": bool(cell.get("summary")),
                           "concept": semantics.concept(label, ft, [cell.get("value")], unit)})
        for i, ch in enumerate(charts):
            ch["name"] = uniq(ch["title"], f"chart {i + 1}")
            fields.append({"name": ch["name"], "kind": "chart", "type": "number", "family": "chart",
                           "samples": [p.get("value") for p in ch["points"].values()][:20], "source": ch["source"],
                           "unit": ch.get("unit"), "concept": semantics.concept(ch["title"], "number", [], ch.get("unit"))})
        only_screens = bool(sources["screenshot"]) and not (sources["live"] or sources["file"])
        complete = (all(complete_live) and bool(complete_live)) or app["screens_complete"]
        if only_screens and not app["screens_complete"]:
            notes.append("Data comes from screenshots only: records that are not on the screenshots cannot be "
                         "checked. Tick 'Screenshots show all records' if they do.")
        for ch in charts:
            if not ch["extracted"]:
                notes.append(f"Chart '{ch['title']}': {ch.get('note') or 'values could not be read'}")
        extraction = _worst_status([i["status"] for i in inputs]) if inputs else "FAILED"
        if complete_live and all(complete_live):
            scope = "verified"  # a live capture read every page and its row count matched the application's total
        elif app["screens_complete"] and only_screens:
            scope = "declared"  # the tester declared that the screenshots show every record
        else:
            scope = "visible"
        return {"app_id": app["id"], "letter": app["letter"], "name": app["name"],
                "tables": [{k: v for k, v in t.items() if k != "sig"} for t in tables], "values": renamed_values,
                "charts": charts, "fields": fields, "filters": app["config"].get("ws_applied_filters") or {},
                "coverage": {"screenshots": sources["screenshot"], "live": sources["live"], "files": sources["file"],
                             "complete": complete, "only_screenshots": only_screens, "low_confidence": low,
                             "notes": notes, "extraction": extraction, "scope": scope, "inputs": inputs}}

    def analyze(self, pid: str, use_ai: bool = False, reread: bool = False) -> dict[str, Any]:
        apps = self._apps(pid)
        if len(apps) < 2:
            raise QAError("WS_TWO_APPS", "Add at least two applications.", status=422)
        for a in apps:
            self._read_screens(a, reread)
        apps = self._apps(pid)
        data = [self.app_data(a) for a in apps]
        empty = [d["name"] for d in data if not d["fields"]]
        prev = self.latest_config(pid)
        cfg = suggest_config(data, (prev or {}).get("config"))
        if use_ai:
            cfg["ai"] = self._ai_refine(data, cfg)
        analysis = {"apps": [_preview(d) for d in data], "config": cfg, "empty": empty,
                    "saved_version": (prev or {}).get("version"), "analyzed_at": now_iso()}
        self.db.execute("INSERT INTO cmp_ws_state (project_id, analysis, updated_at) VALUES (?,?,?) ON CONFLICT"
                        "(project_id) DO UPDATE SET analysis = excluded.analysis, updated_at = excluded.updated_at",
                        (pid, dumps(analysis), analysis["analyzed_at"]))
        self.svc.audit("workspace.analyze", pid, pid, applications=len(apps), fields=len(cfg["fields"]),
                       ai=use_ai)
        return analysis

    def _ai_refine(self, data: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
        """The local AI looks at fields the rules could not map. Every AI answer needs the tester's approval."""
        ai = getattr(self.rt, "ai", None)
        if ai is None:
            return {"used": False, "note": "The local AI is not available."}
        anchor = data[0]
        added = 0
        log: list[dict[str, Any]] = []
        try:
            for other in data[1:]:
                sp = {f["name"]: {"type": f["type"], "samples": f["samples"]} for f in anchor["fields"]
                      if f["kind"] != "chart"}
                tp = {f["name"]: {"type": f["type"], "samples": f["samples"]} for f in other["fields"]
                      if f["kind"] != "chart"}
                rows = [r for r in cfg["fields"] if r["status"] == "unmapped" and anchor["app_id"] in r["members"]
                        and other["app_id"] not in r["members"]]
                if not rows:
                    continue
                sug = [{"source_field": r["members"][anchor["app_id"]]["field"], "target_field": None,
                        "confidence": 0.0, "status": "unmapped"} for r in rows]
                sug = mapping.refine_with_ai(ai, sp, tp, sug, context="integration comparison", log=log)
                for s in sug:
                    if s.get("origin") != "ai" or not s.get("target_field"):
                        continue
                    row = next(r for r in rows if r["members"][anchor["app_id"]]["field"] == s["source_field"])
                    orphan = next((r for r in cfg["fields"] if list(r["members"]) == [other["app_id"]]
                                   and r["members"][other["app_id"]]["field"] == s["target_field"]), None)
                    if orphan is None:
                        continue
                    row["members"][other["app_id"]] = {**orphan["members"][other["app_id"]],
                                                       "confidence": s["confidence"], "origin": "ai"}
                    row["status"] = "needs_review"
                    row["evidence"] = row.get("evidence", []) + (s.get("evidence") or [])[-1:]
                    cfg["fields"].remove(orphan)
                    added += 1
        except Exception as exc:  # the rules' suggestions stand without the AI
            return {"used": False, "note": f"The local AI could not help: {str(exc)[:160]}", "log": log}
        failed = [x for x in log if x.get("error")]
        if failed and len(failed) == len(log):
            return {"used": False, "added": 0, "log": log,
                    "note": "The local AI did not return a usable answer (" + failed[0]["error"] + "). The mapping "
                            "below comes from the deterministic rules; review the unmapped fields yourself."}
        note = None
        if failed:
            note = f"{len(failed)} of {len(log)} AI request(s) gave no usable answer; their fields keep the rule-based " \
                   "suggestion."
        return {"used": True, "added": added, "log": log, "note": note}

    # ---- saved mapping configuration --------------------------------------------------------------------------
    def latest_config(self, pid: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM cmp_ws_configs WHERE project_id = ? ORDER BY version DESC LIMIT 1",
                                (pid,))
        if row:
            row["config"] = loads(row["config"])
        return row

    def config_history(self, pid: str) -> list[dict[str, Any]]:
        self.svc.project(pid)
        return self.db.query("SELECT id, version, approved_by, created_at, note FROM cmp_ws_configs WHERE "
                             "project_id = ? ORDER BY version DESC", (pid,))

    def save_config(self, pid: str, config: dict[str, Any], note: str | None = None) -> dict[str, Any]:
        """The tester's approval. Everything kept is marked approved; rows the tester left undecided stay as they
        are and are not used in a comparison. Saved for the next run."""
        self.svc.project(pid)
        u = _user()
        if getattr(u, "view_only", False):
            raise QAError("CMP_FORBIDDEN", "Your role cannot approve mappings.", status=403)
        apps = {a["id"]: a for a in self._apps(pid)}
        letters = {a["letter"]: a["id"] for a in apps.values()}
        state = self.db.query_one("SELECT analysis FROM cmp_ws_state WHERE project_id = ?", (pid,))
        seen: dict[tuple[str, str], dict[str, Any]] = {}
        for ap in (loads(state["analysis"]) if state else {}).get("apps") or []:
            for f in ap.get("fields") or []:
                seen[(ap["app_id"], f["name"])] = _signature(f)
        clean_fields = []
        for r in config.get("fields") or []:
            members = {aid: {"field": str(m.get("field") or "")[:200], "kind": m.get("kind") or "column",
                             "confidence": float(m.get("confidence") or 0), "origin": m.get("origin") or "tester",
                             "signature": seen.get((aid, str(m.get("field") or "")[:200]))}
                       for aid, m in (r.get("members") or {}).items() if aid in apps and m and m.get("field")}
            st = r.get("status") or "needs_review"
            if st not in ("auto_mapped", "needs_review", "unmapped", "approved", "rejected"):
                raise QAError("WS_STATUS", f"Unknown mapping status '{st}'.", status=422)
            if r.get("approve") or st == "approved":
                st = "approved" if len(members) >= 2 or r.get("is_key") else "unmapped"
            clean_fields.append({"id": r.get("id") or _id("F"), "name": str(r.get("name") or "")[:120]
                                 or next(iter(members.values()), {}).get("field", "Field"),
                                 "family": r.get("family") or "text", "is_key": bool(r.get("is_key")),
                                 "members": members, "status": st, "evidence": (r.get("evidence") or [])[:8]})
        rules = []
        for r in config.get("rules") or []:
            try:
                used = formula.validate(r.get("expression") or "")
            except formula.FormulaError as exc:
                raise QAError("WS_FORMULA", f"Calculation for {r.get('field')}: {exc}", status=422) from exc
            for lt, _f in used:
                if lt not in letters:
                    raise QAError("WS_FORMULA", f"The calculation uses application {lt}, which is not in this "
                                  "comparison.", status=422)
            if r.get("app") not in apps:
                raise QAError("WS_FORMULA", "A calculation names an unknown application.", status=422)
            st = "approved" if r.get("approve") or r.get("status") == "approved" else r.get("status") or "suggested"
            rules.append({"id": r.get("id") or _id("R"), "app": r["app"], "field": str(r.get("field"))[:200],
                          "field_id": r.get("field_id"), "expression": r["expression"][:600],
                          "scope": r.get("scope") if r.get("scope") in ("record", "total") else "record",
                          "tolerance": r.get("tolerance"), "status": st,
                          "confidence": r.get("confidence"), "origin": r.get("origin") or "tester",
                          "explanation": str(r.get("explanation") or "")[:400]})
        statuses = []
        for s in config.get("statuses") or []:
            groups = []
            for g in s.get("groups") or []:
                vals = {aid: str(v)[:120] for aid, v in (g.get("values") or {}).items() if aid in apps and v not in
                        (None, "")}
                if not vals:
                    continue
                st = "approved" if g.get("approve") or g.get("status") == "approved" else g.get("status") or \
                    "needs_review"
                groups.append({"name": str(g.get("name") or next(iter(vals.values())))[:120], "values": vals,
                               "confidence": g.get("confidence"), "status": st})
            statuses.append({"field_id": s.get("field_id"), "groups": groups})
        charts = []
        for c in config.get("charts") or []:
            if c.get("app") not in apps or c.get("source_app") not in apps:
                continue
            st = "approved" if c.get("approve") or c.get("status") == "approved" else c.get("status") or "suggested"
            charts.append({"id": c.get("id") or _id("G"), "app": c["app"], "chart": str(c.get("chart"))[:200],
                           "source_app": c["source_app"], "category_field": c.get("category_field"),
                           "value_field": c.get("value_field"),
                           "aggregate": c.get("aggregate") if c.get("aggregate") in ("sum", "count", "avg", "share")
                           else "sum", "kind": c.get("kind") if c.get("kind") in ("chart", "table") else "chart",
                           "tolerance": c.get("tolerance"),
                           "status": st, "confidence": c.get("confidence")})
        clean = {"fields": clean_fields, "rules": rules, "statuses": statuses, "charts": charts}
        last = self.db.query_one("SELECT MAX(version) AS v FROM cmp_ws_configs WHERE project_id = ?", (pid,))
        version = (last["v"] or 0) + 1
        self.db.execute("INSERT INTO cmp_ws_configs (project_id, version, config, approved_by, created_at, note) "
                        "VALUES (?,?,?,?,?,?)", (pid, version, dumps(clean), u.username, now_iso(), note))
        for r in clean_fields:  # learning memory: approved mappings help the next suggestion
            if r["status"] == "approved":
                for a, b in itertools.combinations(r["members"].items(), 2):
                    aura.learn("field_mapping", a[0], f"{a[1]['field']}->{b[0]}", {"target_field": b[1]["field"]},
                               source="integration comparison", approved=True, agent="comparison")
        self.svc.audit("workspace.mapping.save", pid, pid, version=version,
                       approved=sum(1 for r in clean_fields if r["status"] == "approved"),
                       rules=sum(1 for r in rules if r["status"] == "approved"))
        return self.latest_config(pid) or {}

    # ---- run ----------------------------------------------------------------------------------------------------
    def run(self, pid: str, label: str | None = None) -> dict[str, Any]:
        proj = self.svc.project(pid)
        saved = self.latest_config(pid)
        if not saved:
            raise QAError("WS_MAPPING_REQUIRED", "Review and approve the field mapping first.", status=422)
        apps = self._apps(pid)
        for a in apps:
            self._read_screens(a)
        data = [self.app_data(a) for a in self._apps(pid)]
        # 23.9.0 Compare Filtered Data: local filters for applications without the filter, then the scope check
        links = [l_ for l_ in (proj["settings"] or {}).get("ws_filter_links") or [] if l_.get("status") == "approved"]
        by_id = {d["app_id"]: d for d in data}
        for l_ in links:
            src_val = None
            for aid, m in l_["members"].items():
                if not m.get("local_field") and aid in by_id:
                    v = filt._applied(by_id[aid]).get(m.get("label"))
                    if v not in (None, "", []):
                        src_val = v
                        break
            for aid, m in l_["members"].items():
                if m.get("local_field") and aid in by_id and src_val is not None:
                    kept = filt.local_filter(by_id[aid], m["local_field"], src_val)
                    by_id[aid]["filters"] = {**(by_id[aid].get("filters") or {}), f"local:{m['local_field']}": src_val}
                    by_id[aid]["coverage"]["notes"].append(f"Filtered locally on {m['local_field']} = {src_val} "
                                                           f"({kept} record(s) kept).")
        scope = filt.scope_check(data, links)
        decisions = self.decisions(pid)
        rules = (proj["settings"] or {}).get("rules") or {}
        out = compare(data, saved["config"], decisions=decisions, rules=rules, scope=scope)
        out["mapping_version"] = saved["version"]
        out["label"] = label
        out["filters"] = {d["app_id"]: filt._applied(d) for d in data}
        exe, at = _id("CX"), now_iso()
        self.db.execute("INSERT INTO cmp_executions (id, project_id, mode, status, result, config, summary, detail, "
                        "created_by, started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (exe, pid, "workspace", "done", out["result"], dumps({"mapping_version": saved["version"],
                                                                        "label": label, "filters": out["filters"]}),
                         dumps(out["summary"]), dumps(out), _user().username, at, now_iso()))
        for r in out["rows"]:
            if r["result"] == "FAIL":
                self.db.execute("INSERT OR IGNORE INTO cmp_discrepancies (id, execution_id, project_id, category, "
                                "record_key, source_field, target_field, data) VALUES (?,?,?,?,?,?,?,?)",
                                (r["id"], exe, pid, r["category"], r.get("record_key"), r["field"], None, dumps(r)))
        self.svc.audit("workspace.run", pid, exe, result=out["result"], failed=out["summary"]["failed"],
                       warnings=out["summary"]["warnings"], mapping_version=saved["version"])
        return self.execution(exe)

    def execution(self, exe: str) -> dict[str, Any]:
        e = self.svc.execution(exe)
        if e["mode"] != "workspace":
            raise QAError("WS_NOT_WORKSPACE", "This run was made in the advanced comparison tools.", status=422)
        e["defects"] = self.db.query("SELECT id, discrepancy_id, title, status, tfs_id FROM cmp_defects WHERE "
                                     "execution_id = ?", (exe,))
        return e

    # ---- decisions and defects --------------------------------------------------------------------------------
    def decisions(self, pid: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM cmp_ws_decisions WHERE project_id = ? ORDER BY decided_at DESC", (pid,))
        for r in rows:
            r["row"] = loads(r["row"])
        return rows

    def decide(self, exe: str, row_id: str, decision: str, note: str | None = None) -> dict[str, Any]:
        if decision not in DECISIONS:
            raise QAError("WS_DECISION", "Unknown decision.", status=422)
        e = self.execution(exe)
        row = next((r for r in e["detail"]["rows"] if r["id"] == row_id), None)
        if row is None:
            raise QAError("WS_ROW_NOT_FOUND", "Result not found.", status=404)
        did = _id("WD")
        self.db.execute("INSERT INTO cmp_ws_decisions (id, project_id, execution_id, signature, row, decision, note, "
                        "decided_by, decided_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (did, e["project_id"], exe, row["signature"], dumps(row), decision, (note or "")[:1000],
                         _user().username, now_iso()))
        self.svc.audit("workspace.decision", e["project_id"], row_id, decision=decision, execution=exe)
        return {"id": did, "decision": decision, "label": DECISIONS[decision]}

    def create_defect(self, exe: str, row_id: str) -> dict[str, Any]:
        e = self.execution(exe)
        detail = e["detail"]
        row = next((r for r in detail["rows"] if r["id"] == row_id), None)
        if row is None:
            raise QAError("WS_ROW_NOT_FOUND", "Result not found.", status=404)
        existing = self.db.query_one("SELECT id FROM cmp_defects WHERE execution_id = ? AND discrepancy_id = ?",
                                     (exe, row_id))
        if existing:
            return self.svc.defect(existing["id"])
        names = {a["app_id"]: a["name"] for a in detail["apps"]}
        draft = defect_draft(row, detail["apps"])
        did, at = _id("CD"), now_iso()
        sev = investigate.SEVERITY.get(row["category"], 3)
        self.db.execute("INSERT INTO cmp_defects (id, project_id, execution_id, discrepancy_id, status, title, "
                        "severity, priority, data, history, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (did, e["project_id"], exe, row_id, "new", draft["title"], sev, 2 if sev <= 2 else 3,
                         dumps({**draft, "row": row, "names": names, "category": row["category"],
                                "repro_steps": draft["steps"]}),
                         dumps([{"at": at, "by": _user().username, "action": "created", "status": "new"}]), at, at))
        self.decide(exe, row_id, "actual_defect", "defect created")
        self.svc.audit("workspace.defect", e["project_id"], did, execution=exe, row=row_id)
        return self.svc.defect(did)

    def explain(self, exe: str, row_id: str) -> dict[str, Any]:
        """A plain-language explanation; the local AI adds its view when it is running. It cannot change the
        result."""
        e = self.execution(exe)
        row = next((r for r in e["detail"]["rows"] if r["id"] == row_id), None)
        if row is None:
            raise QAError("WS_ROW_NOT_FOUND", "Result not found.", status=404)
        disc = {"category": row["category"], "field": row["field"], "record_key": row.get("record_key"),
                "expected": row.get("expected"), "displayed": row.get("actual"), "difference": row.get("difference"),
                "values": row.get("values"), "calculation": row.get("calculation"), "note": row.get("explanation")}
        out = investigate.investigate(getattr(self.rt, "ai", None), disc, e, row.get("evidence") or [])
        return {"rules": row.get("explanation"), "ai": out}

    # ---- reports ------------------------------------------------------------------------------------------------
    def report_html(self, exe: str) -> str:
        e = self.execution(exe)
        p = self.svc.project(e["project_id"])
        cfg = self.db.query_one("SELECT config, version, approved_by, created_at FROM cmp_ws_configs WHERE "
                                "project_id = ? AND version = ?", (e["project_id"], e["detail"].get("mapping_version")))
        defects = self.db.query("SELECT id, title, status, data, tfs_id FROM cmp_defects WHERE execution_id = ?", (exe,))
        decisions = [d for d in self.decisions(e["project_id"]) if d["execution_id"] == exe]
        shots = {}
        for r in e["detail"]["rows"]:
            if r["result"] in OPEN_RESULTS:
                for ev in r.get("evidence") or []:
                    if ev not in shots and len(shots) < 12:
                        shots[ev] = self._data_uri(e["project_id"], ev)
        return workspace_report(p, e, loads(cfg["config"]) if cfg else {}, cfg, defects, decisions, shots,
                                brand=branding.name())

    def _data_uri(self, pid: str, evidence_id: str) -> str | None:
        import base64

        row = self.db.query_one("SELECT file FROM cmp_evidence WHERE id = ? AND project_id = ?", (evidence_id, pid))
        if not row:
            return None
        try:
            data = self.svc.evidence.path(pid, row["file"]).read_bytes()
        except QAError:
            return None
        mime = "image/png" if row["file"].endswith(".png") else "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"

    def report_pdf(self, exe: str) -> Any:
        from ..reports.service import print_pdf

        target = self.rt.paths.reports / "comparison" / f"{exe}.pdf"
        print_pdf(self.report_html(exe), target)
        self.svc.audit("workspace.report.pdf", self.execution(exe)["project_id"], exe)
        return target

    def report_xlsx(self, exe: str) -> bytes:
        e = self.execution(exe)
        d = e["detail"]
        letters = [a["letter"] for a in d["apps"]]
        ids = [a["app_id"] for a in d["apps"]]
        res = [[r["field"], r.get("record_key") or ""] + [str((r["values"].get(i) or {}).get("value") or "")
                                                            for i in ids]
               + [str(r.get("expected") or ""), str(r.get("difference") or ""), r["result"], r["category"],
                  r.get("source_app") or "", r.get("target_app") or "", r.get("rule") or "",
                  r.get("suggested_label") or "",
                  ((r.get("decision") or {}).get("classification") or (r.get("decision") or {}).get("label") or ""),
                  r.get("calculation") or "", r.get("explanation") or ""] for r in d["rows"]]
        summ = [[k.replace("_", " ").capitalize(), v] for k, v in d["summary"].items()]
        apps_ = [[a["letter"], a["name"], ", ".join(f"{k}: {v}" for k, v in a["coverage"].items()
                                                    if k != "notes")] for a in d["apps"]]
        dec = [[x["decided_at"], x["decided_by"], (x["row"] or {}).get("field"), DECISIONS.get(x["decision"]),
                x["note"]] for x in self.decisions(e["project_id"]) if x["execution_id"] == exe]
        self.svc.audit("workspace.report.xlsx", e["project_id"], exe)
        return reports.xlsx([
            ("Summary", ["Item", "Value"], summ),
            ("Applications", ["Letter", "Application", "Sources"], apps_),
            ("Results", ["Field", "Record"] + [f"Application {x}" for x in letters]
             + ["Expected", "Difference", "Result", "Category", "Source application", "Target application", "Rule",
                "Suggested classification", "Tester decision", "Calculation", "Explanation"], res),
            ("Decisions", ["When", "Tester", "Field", "Decision", "Note"], dec),
        ])


# ======================================================================================================================
# suggestion of the mapping configuration (pure functions: tested without a database)
# ======================================================================================================================

def _preview(d: dict[str, Any]) -> dict[str, Any]:
    def cell(c: Any) -> dict[str, Any]:
        c = c if isinstance(c, dict) else {"value": c}
        return {k: c.get(k) for k in ("value", "confidence", "method", "source", "evidence", "where", "ocr_value",
                                      "box")}
    return {"app_id": d["app_id"], "letter": d["letter"], "name": d["name"], "coverage": d["coverage"],
            "tables": [{"id": t["id"], "headers": t["headers"], "source": t["source"], "evidence": t["evidence"],
                        "origins": t.get("origins") or [],
                        "rows": [{h: cell(r["fields"].get(h)) for h in t["headers"]} for r in t["records"][:200]],
                        "total_rows": len(t["records"])} for t in d["tables"]],
            "values": {k: cell(v) for k, v in d["values"].items()},
            "charts": [{"name": c["name"], "title": c["title"], "categories": c["categories"],
                        "points": {k: cell(v) for k, v in c["points"].items()}, "extracted": c["extracted"],
                        "note": c.get("note"), "source": c["source"], "evidence": c.get("evidence")}
                       for c in d["charts"]],
            "fields": [{**{k: f[k] for k in ("name", "kind", "type", "family", "source")},
                        "unit": f.get("unit"), "concept": (f.get("concept") or {}).get("concept"),
                        "role": (f.get("concept") or {}).get("role")} for f in d["fields"]]}


def _signature(f: dict[str, Any] | None) -> dict[str, Any]:
    """What a saved mapping remembers of a field, to notice when the application changed it (23.9.0)."""
    f = f or {}
    c = f.get("concept")
    if isinstance(c, dict):
        c = c.get("concept")
    return {"type": f.get("type"), "unit": f.get("unit"), "concept": c}


def _field(d: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((f for f in d["fields"] if f["name"] == name), None)


def _main_table(d: dict[str, Any], key_field: str | None) -> dict[str, Any] | None:
    for t in d["tables"]:
        if key_field in t["headers"]:
            return t
    return None


def _keys(d: dict[str, Any], key_field: str) -> dict[str, dict[str, Any]]:
    t = _main_table(d, key_field)
    out: dict[str, dict[str, Any]] = {}
    if t is None:
        cell = d["values"].get(key_field)
        k = key_norm(_cv(cell))
        if k:
            out[k] = {"fields": d["values"], "source": "values"}
        return out
    for r in t["records"]:
        k = key_norm(_cv(r["fields"].get(key_field)))
        if k and k not in out:
            out[k] = r
    return out


def _key_candidates(d: dict[str, Any]) -> list[str]:
    out = []
    for f in d["fields"]:
        if f["kind"] == "column":
            t = next(t for t in d["tables"] if t["id"] == f["table"])
            vals = [key_norm(_cv(r["fields"].get(f["name"]))) for r in t["records"]]
            vals = [v for v in vals if v]
            # nearly unique: a duplicated record must not hide the key (duplicates are reported, not guessed)
            if len(vals) >= 2 and len(set(vals)) >= max(2, 0.8 * len(vals)) and f["family"] in ("text", "num"):
                out.append(f["name"])
        elif f["kind"] == "value" and re.search(r"(?i)\b(id|code|ref|reference|no|number|key)\b", f["name"]):
            out.append(f["name"])
    return out


def _pick_key(data: list[dict[str, Any]]) -> dict[str, str]:
    """The business key of every application: the columns whose values overlap most across applications."""
    anchor = data[0]
    best: dict[str, str] = {}
    cands = {d["app_id"]: _key_candidates(d) for d in data}
    score_best = -1.0
    for ka in cands[anchor["app_id"]]:
        a_keys = set(_keys(anchor, ka))
        chosen = {anchor["app_id"]: ka}
        total = 0.0
        for d in data[1:]:
            top = None
            for kb in cands[d["app_id"]]:
                b_keys = set(_keys(d, kb))
                if not a_keys or not b_keys:
                    continue
                ov = len(a_keys & b_keys) / min(len(a_keys), len(b_keys))
                sc = ov + 0.2 * _label_score(ka, kb)
                if ov > 0 and (top is None or sc > top[0]):
                    top = (sc, kb)
            if top:
                chosen[d["app_id"]] = top[1]
                total += top[0]
        idish = 0.3 if re.search(r"(?i)\b(id|code|ref|no|number|key)\b", ka) else 0
        if total + idish > score_best and len(chosen) > 1:
            score_best, best = total + idish, chosen
    return best


def _pick_key_by_name(data: list[dict[str, Any]]) -> dict[str, str]:
    """Fallback when no identifier values overlap (the applications hold different records now, e.g. filtered on
    different scopes): an identifier-like, unique column with the same name in every application. The mapping is
    only suggested for review, never auto-approved."""
    cands = {d["app_id"]: _key_candidates(d) for d in data}
    for ka in cands[data[0]["app_id"]]:
        if not re.search(r"(?i)\b(id|code|ref|reference|no|number|key)\b|#", ka):
            continue
        chosen = {data[0]["app_id"]: ka}
        for d in data[1:]:
            kb = max(cands[d["app_id"]], key=lambda c: _label_score(ka, c), default=None)
            if kb and _label_score(ka, kb) >= 0.9:
                chosen[d["app_id"]] = kb
        if len(chosen) > 1:
            return chosen
    return {}


def _agreement(a: dict[str, Any], fa: dict[str, Any], b: dict[str, Any], fb: dict[str, Any],
               key: dict[str, str]) -> tuple[float | None, int]:
    """Share of key-aligned records where the two fields hold the same value (None: cannot tell)."""
    fam = fa["family"] if fa["family"] == fb["family"] else "text"
    if "status" in (fa["family"], fb["family"]) and {fa["family"], fb["family"]} <= {"status", "text"} and \
            fa["kind"] == fb["kind"] == "column":
        # status values may be written differently (01 / Pending): the same record must always pair the same
        # two values (a consistent translation), not show equal text
        ka, kb = key.get(a["app_id"]), key.get(b["app_id"])
        if not ka or not kb:
            return None, 0
        ra, rb = _keys(a, ka), _keys(b, kb)
        pairs = [(str(_cv(ra[k]["fields"].get(fa["name"]))).strip().lower(),
                  str(_cv(rb[k]["fields"].get(fb["name"]))).strip().lower()) for k in set(ra) & set(rb)]
        pairs = [p for p in pairs if p[0] not in ("", "none") and p[1] not in ("", "none")]
        if len(pairs) < 2:
            return None, 0
        fwd: dict[str, set[str]] = defaultdict(set)
        back: dict[str, set[str]] = defaultdict(set)
        for x, y in pairs:
            fwd[x].add(y)
            back[y].add(x)
        consistent = sum(1 for x, y in pairs if len(fwd[x]) == 1 and len(back[y]) == 1) / len(pairs)
        return consistent, len(pairs)
    if fam in ("status", "chart"):
        return None, 0
    nt = _ntype(fam)
    if fa["kind"] == "value" and fb["kind"] == "value":
        va, vb = _cv(a["values"].get(fa["name"])), _cv(b["values"].get(fb["name"]))
        if va in (None, "") or vb in (None, ""):
            return None, 0
        return (1.0 if equal(normalize(va, nt)[0], normalize(vb, nt)[0], nt)[0] else 0.0), 1
    ka, kb = key.get(a["app_id"]), key.get(b["app_id"])
    if not ka or not kb:
        return None, 0
    ra, rb = _keys(a, ka), _keys(b, kb)
    common = set(ra) & set(rb)
    n = same = 0
    for k in common:
        va, vb = _cv(ra[k]["fields"].get(fa["name"])), _cv(rb[k]["fields"].get(fb["name"]))
        if va in (None, "") or vb in (None, ""):
            continue
        n += 1
        same += 1 if equal(normalize(va, nt)[0], normalize(vb, nt)[0], nt)[0] else 0
    return (same / n if n else None), n


def _pair_score(a: dict[str, Any], fa: dict[str, Any], b: dict[str, Any], fb: dict[str, Any],
                key: dict[str, str]) -> tuple[float, list[str]]:
    if "chart" in (fa["family"], fb["family"]):
        return 0.0, []
    kinds = {fa["kind"], fb["kind"]}
    if kinds == {"column", "value"}:
        # a summary value is a calculation over a column (handled by calculation rules), unless the value
        # screen is one record (it shows the key)
        side = b if fb["kind"] == "value" else a
        if not any(key_norm(_cv(side["values"].get(k))) for k in [key.get(side["app_id"])] if k):
            return 0.0, []
    ls = _label_score(fa["name"], fb["name"])
    # 23.9.0: business meaning (City ~ Territory, Orders ~ Order Volume, Share % ~ Contribution %, a quantity is
    # never an amount, a unit price is not a final amount)
    sm, sm_why = semantics.same_meaning(fa.get("concept") or {}, fb.get("concept") or {})
    concept_a = (fa.get("concept") or {}).get("concept")
    if sm >= 1.0 and concept_a not in (None, "text", "number"):
        ls = max(ls, 0.72)
    elif sm >= 0.8:
        ls = max(ls, 0.6)
    fam_ok = fa["family"] == fb["family"] or {fa["family"], fb["family"]} <= {"text", "status"}
    agree, n = _agreement(a, fa, b, fb, key)
    ev = [f"names '{fa['name']}' / '{fb['name']}' similarity {ls:.2f}",
          f"kinds of data {fa['family']} / {fb['family']}" + ("" if fam_ok else " (different)"),
          f"meaning: {sm_why}"]
    if agree is None:
        vo = mapping.value_overlap(fa["samples"], fb["samples"], _ntype(fa["family"]))
        score = 0.70 * ls + 0.20 * vo + (0.10 if fam_ok else -0.3)
        ev.append(f"{int(vo * 100)}% of sample values in common")
    else:
        score = 0.55 * ls + 0.35 * agree + (0.10 if fam_ok else -0.3)
        ev.append(f"same value on {int(agree * 100)}% of {n} matching record(s)")
        if n >= 3 and agree >= 0.95 and fam_ok and ls >= 0.3 and "status" not in (fa["family"], fb["family"]):
            score = max(score, 0.85)  # different names, but the same value on every matching record
        if n >= 2 and agree == 0:
            score = min(score, 0.40)  # names alike but the values never match: not the same field
        elif n == 1 and agree == 0:
            score = min(score, 0.60)
    values_agree = agree is not None and n >= 3 and agree >= 0.95
    if sm == 0.0:
        score = min(score, 0.30)  # different business meaning
    elif sm <= 0.1:
        score = min(score, 0.35)  # different units (AED / USD, % / amount)
    elif sm <= 0.25 and not values_agree:
        score = min(score, 0.40)  # different kinds of amount (unit price / final amount): not suggested
    elif sm == 0.8 and not (agree is not None and n >= 2 and agree >= 0.95):
        score = min(score, 0.70)  # Total ~ Final Amount: only the same when the values agree (tax may differ)
        ev.append("needs review: one amount may include tax or discount and the other not")
    if {fa["family"], fb["family"]} == {"num", "pct"}:
        score = min(score, 0.35)  # a count is not a percentage
    if not fam_ok:
        score = min(score, 0.35)
    return round(max(0.0, min(score, 0.99)), 3), ev


def _status(conf: float) -> str:
    return "auto_mapped" if conf >= mapping.AUTO_MAP else "needs_review" if conf >= mapping.SUGGEST else "unmapped"


def suggest_config(data: list[dict[str, Any]], saved: dict[str, Any] | None = None) -> dict[str, Any]:
    """Business fields across all applications, status groups, calculation and chart rules. Approved items of the
    saved configuration are kept exactly as approved; only what is new is suggested."""
    saved = saved or {}
    by_id = {d["app_id"]: d for d in data}
    letters = {d["app_id"]: d["letter"] for d in data}
    fields: list[dict[str, Any]] = []
    taken: dict[str, set[str]] = defaultdict(set)
    notes: list[str] = []
    for r in saved.get("fields") or []:
        if r["status"] != "approved":
            continue
        members = {aid: m for aid, m in r["members"].items() if aid in by_id}
        missing = [f"{by_id[aid]['name']} › {m['field']}" for aid, m in members.items()
                   if _field(by_id[aid], m["field"]) is None]
        changed = []
        for aid, m in members.items():
            old_sig, cur = m.get("signature"), _field(by_id[aid], m["field"])
            if not old_sig or cur is None:
                continue
            now_sig = _signature(cur)
            diff = [f"{k} {old_sig.get(k) or '-'} → {now_sig.get(k) or '-'}" for k in ("type", "unit", "concept")
                    if old_sig.get(k) and now_sig.get(k) and old_sig.get(k) != now_sig.get(k)
                    and not (k == "type" and {old_sig[k], now_sig[k]} <= {"number", "currency"})]
            if diff:
                changed.append(f"{by_id[aid]['name']} › {m['field']} ({', '.join(diff)})")
        row = {**r, "members": {aid: {**m, "origin": "saved"} for aid, m in members.items()}}
        if missing:
            row["status"] = "needs_review"
            row["evidence"] = [f"saved mapping: not found in this analysis: {', '.join(missing)}"]
            notes.append(f"Saved mapping '{r['name']}' uses fields that were not found now: {', '.join(missing)}.")
        elif changed:
            row["status"] = "needs_review"
            row["evidence"] = [f"saved mapping: the field changed since it was approved: {'; '.join(changed)}"]
            notes.append(f"Saved mapping '{r['name']}' needs review: {'; '.join(changed)}.")
        else:
            row["evidence"] = ["approved earlier (saved mapping)"]
        for aid, m in row["members"].items():
            taken[aid].add(m["field"])
        fields.append(row)
    key = {}
    saved_key = next((r for r in fields if r.get("is_key")), None)
    if saved_key:
        key = {aid: m["field"] for aid, m in saved_key["members"].items()}
    else:
        key = _pick_key(data)
        by_name = False
        if not key:
            key, by_name = _pick_key_by_name(data), True
        if key:
            kname = key[data[0]["app_id"]] if data[0]["app_id"] in key else next(iter(key.values()))
            fields.insert(0, {"id": _id("F"), "name": kname, "family": "text", "is_key": True,
                              "members": {aid: {"field": f, "kind": _field(by_id[aid], f)["kind"],
                                                "confidence": 0.6 if by_name else 0.95,
                                                "origin": "rules"} for aid, f in key.items()},
                              "status": "auto_mapped" if len(key) == len(data) and not by_name else "needs_review",
                              "evidence": ["the record identifier by name: no identifier values overlap in the "
                                           "current data (different records or scopes), confirm it"] if by_name else
                                          ["the record identifier: values overlap between the applications"]})
            for aid, f in key.items():
                taken[aid].add(f)
    # every other field: greedy best matches, anchored on the first application, then the next ones
    for d in data:
        for f in d["fields"]:
            if f["name"] in taken[d["app_id"]] or f["kind"] == "chart":
                continue
            cands = []
            for row in fields:
                if row.get("is_key") or d["app_id"] in row["members"]:
                    continue
                if row["status"] == "approved" and row["members"] and \
                        all(m.get("origin") == "saved" for m in row["members"].values()):
                    continue  # never change an approved mapping
                best = None
                for aid, m in row["members"].items():
                    other = _field(by_id[aid], m["field"])
                    if other is None:
                        continue
                    sc, ev = _pair_score(by_id[aid], other, d, f, key)
                    if best is None or sc > best[0]:
                        best = (sc, ev)
                if best:
                    cands.append((best[0], row, best[1]))
            cands.sort(key=lambda x: -x[0])
            if cands and cands[0][0] >= mapping.SUGGEST:
                sc, row, ev = cands[0]
                row["members"][d["app_id"]] = {"field": f["name"], "kind": f["kind"], "confidence": sc,
                                               "origin": "rules"}
                confs = [m["confidence"] for m in row["members"].values() if m.get("origin") != "saved"]
                row["status"] = _status(min(confs)) if row["status"] != "approved" else "needs_review"
                row["evidence"] = (row.get("evidence") or []) + ev
                if f["family"] == "status":
                    row["family"] = "status"
            else:
                fields.append({"id": _id("F"), "name": f["name"], "family": f["family"], "is_key": False,
                               "members": {d["app_id"]: {"field": f["name"], "kind": f["kind"], "confidence": 1.0,
                                                         "origin": "rules"}},
                               "status": "unmapped",
                               "evidence": [f"no matching field in the other application(s)"
                                            + (f"; closest {cands[0][0]:.0%}" if cands else "")]})
            taken[d["app_id"]].add(f["name"])
    for row in fields:
        if len(row["members"]) < 2 and not row.get("is_key"):
            row["status"] = "unmapped"
    rules = _suggest_rules(data, fields, key, saved.get("rules") or [], letters)
    charts = _suggest_charts(data, saved.get("charts") or [], key)
    statuses = _suggest_statuses(data, fields, key, saved.get("statuses") or [])
    return {"fields": fields, "rules": rules, "charts": charts, "statuses": statuses, "notes": notes,
            "key": key, "letters": letters}


def _numeric_fields(d: dict[str, Any], kinds: tuple[str, ...]) -> list[dict[str, Any]]:
    return [f for f in d["fields"] if f["kind"] in kinds and f["family"] in ("num", "pct")]


def _suggest_rules(data: list[dict[str, Any]], fields: list[dict[str, Any]], key: dict[str, str],
                   saved: list[dict[str, Any]], letters: dict[str, str]) -> list[dict[str, Any]]:
    """For numbers shown by one application but not the first (percentages, totals, KPI cards): which calculation
    over another application's values produces them."""
    by_id = {d["app_id"]: d for d in data}
    out = [r for r in saved if r.get("status") == "approved" and r.get("app") in by_id]
    covered = {(r["app"], r["field"]) for r in out}
    anchor = data[0]["app_id"]
    for row in fields:
        if row.get("is_key"):
            continue
        if anchor in row["members"]:
            continue  # shown by the first application (the source) too: compared directly
        for aid, m in row["members"].items():
            if (aid, m["field"]) in covered:
                continue
            f = _field(by_id[aid], m["field"])
            if f is None or f["family"] not in ("num", "pct"):
                continue
            best: list[dict[str, Any]] = []
            derived = (f.get("concept") or {}).get("role") in ("final", "total", "subtotal", "sales") or \
                (f.get("concept") or {}).get("concept") == "percent"
            for src in data:
                same_app = src["app_id"] == aid  # 23.9.0: Final Amount = Subtotal - Discount + Tax on one screen
                if same_app and not derived:
                    continue  # a discount or a unit price is an input, not a calculation of the same screen
                lt = src["letter"]
                if f["kind"] == "column" and key.get(aid) and key.get(src["app_id"]):
                    tk, sk = _keys(by_id[aid], key[aid]), _keys(src, key[src["app_id"]])
                    cand = [x for x in _numeric_fields(src, ("column", "value")) if x["name"] != key[src["app_id"]]
                            and not (same_app and x["name"] == f["name"])]
                    samples = []
                    for k in set(tk) & set(sk):
                        shown = _cv(tk[k]["fields"].get(f["name"]))
                        if shown in (None, ""):
                            continue
                        vals = {(lt, x["name"]): _cv(sk[k]["fields"].get(x["name"]) if x["kind"] == "column"
                                                    else src["values"].get(x["name"])) for x in cand}
                        samples.append({"shown": shown, "values": vals})
                    found = formula.suggest(f["name"], samples, [(lt, x["name"]) for x in cand],
                                            concepts={(lt, x["name"]): x.get("concept") for x in cand},
                                            target=f.get("concept"))
                    for s in found:
                        s.update(scope="record", source=src["app_id"])
                    best += found
                elif f["kind"] == "value":
                    shown = _cv(by_id[aid]["values"].get(f["name"]))
                    vals = [x for x in _numeric_fields(src, ("value",)) if not (same_app and x["name"] == f["name"])
                            and not (same_app and x.get("summary"))]
                    if vals:
                        found = formula.suggest(f["name"], [{"shown": shown, "values": {
                            (lt, x["name"]): _cv(src["values"].get(x["name"])) for x in vals}}],
                            [(lt, x["name"]) for x in vals],
                            concepts={(lt, x["name"]): x.get("concept") for x in vals}, target=f.get("concept"))
                        for s in found:
                            s.update(scope="total", source=src["app_id"])
                        best += found
                    cols = {}
                    cons = {}
                    for x in src["fields"]:
                        if x["kind"] == "column":
                            t = next(t for t in src["tables"] if t["id"] == x["table"])
                            cols[(lt, x["name"])] = [_cv(r["fields"].get(x["name"])) for r in t["records"]]
                            cons[(lt, x["name"])] = x.get("concept")
                    found = formula.suggest_total(f["name"], shown, cols, cons)
                    for s in found:
                        s.update(scope="total", source=src["app_id"])
                    best += found
            if not best:
                continue
            best.sort(key=lambda x: (-x["confidence"], -x["matches"], len(x["expression"])))
            top = best[0]
            out.append({"id": _id("R"), "app": aid, "field": f["name"], "field_id": row["id"],
                        "expression": top["expression"], "scope": top["scope"], "status": "suggested",
                        "confidence": top["confidence"],
                        "review": "auto_mapped" if top["confidence"] >= mapping.AUTO_MAP else "needs_review",
                        "origin": "rules", "explanation": top["explanation"], "mismatches": top["mismatches"],
                        "alternatives": [{"expression": b["expression"], "confidence": b["confidence"],
                                          "explanation": b["explanation"]} for b in best[1:4]]})
            covered.add((aid, f["name"]))
    return out


def _series(d: dict[str, Any], key_field: str | None = None) -> list[dict[str, Any]]:
    """Everything of one application that shows a value per category (23.9.0): chart series, the shares of a pie
    chart and the numeric columns of an aggregated table (Territory / Sales Amount / Share %). The detailed table
    that holds the record key is not a series: its records are compared one by one."""
    out = []
    for ch in d["charts"]:
        pct_only = ch.get("value_kind") == "percent"
        concept = {"concept": "percent", "unit": "%"} if pct_only else \
            semantics.concept(ch["title"], "number", [], ch.get("unit"))
        out.append({"name": ch["name"], "kind": "chart", "title": ch["title"], "categories": ch["categories"],
                    "points": ch["points"], "concept": concept, "chart_type": ch.get("type") or "bar",
                    "approximate": bool(ch.get("approximate")), "source": ch["source"],
                    "evidence": ch.get("evidence"), "extracted": ch["extracted"], "note": ch.get("note")})
        if ch.get("percentages") and not pct_only:
            out.append({"name": f"{ch['name']} — share %", "kind": "chart", "title": f"{ch['title']} (share %)",
                        "categories": list(ch["percentages"]), "points": ch["percentages"],
                        "concept": {"concept": "percent", "unit": "%"}, "chart_type": ch.get("type") or "pie",
                        "approximate": False, "source": ch["source"], "evidence": ch.get("evidence"),
                        "extracted": True, "note": None})
    for t in d["tables"]:
        if key_field and key_field in t["headers"]:
            continue
        fl = {f["name"]: f for f in d["fields"] if f.get("table") == t["id"]}
        cat = None
        for h in t["headers"]:
            f = fl.get(h)
            vals = [cat_norm(_cv(r["fields"].get(h))) for r in t["records"]]
            vals = [v for v in vals if v]
            if f and f["family"] in ("text", "status") and len(vals) >= 2 and len(set(vals)) == len(vals):
                cat = h
                break
        if cat is None:
            continue
        cats = [str(_cv(r["fields"].get(cat))) for r in t["records"] if _cv(r["fields"].get(cat)) not in (None, "")]
        for h in t["headers"]:
            f = fl.get(h)
            if h == cat or f is None or f["family"] not in ("num", "pct"):
                continue
            pts = {str(_cv(r["fields"].get(cat))): {**(r["fields"].get(h) if isinstance(r["fields"].get(h), dict)
                                                       else {"value": r["fields"].get(h)})}
                   for r in t["records"] if _cv(r["fields"].get(cat)) not in (None, "")}
            out.append({"name": f"{h} by {cat}", "kind": "table", "title": f"{h} by {cat}", "categories": cats,
                        "points": pts, "concept": f.get("concept") or {}, "chart_type": "table",
                        "approximate": False, "source": t["source"], "evidence": t.get("evidence"),
                        "extracted": True, "note": None, "table": t["id"], "field": h})
    return out


def group_expected(records: list[dict[str, Any]], cat_field: str, value_field: str,
                   aggregate: str = "sum") -> dict[str, Decimal]:
    """The value per category: SUM / COUNT / AVG of a column, or its SHARE of the total in percent
    (city sales / total sales x 100). Decimal arithmetic; the comparison rounds like the application shows."""
    if aggregate != "share":
        return chart_expected(records, cat_field, value_field, aggregate)
    sums = chart_expected(records, cat_field, value_field, "sum")
    total = sum(sums.values(), Decimal(0))
    if not total:
        return {}
    return {k: v / total * 100 for k, v in sums.items()}


def _suggest_charts(data: list[dict[str, Any]], saved: list[dict[str, Any]],
                    key: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """A chart's bars or slices, or an aggregated table, against the detailed records of another application:
    which column gives the categories, which column and aggregate give the values (23.9.0: the value column must
    mean what the chart shows: sales amounts for a sales chart, never the quantity because some numbers agree;
    a percentage is the share of the total)."""
    key = key or {}
    by_id = {d["app_id"]: d for d in data}
    out = [c for c in saved if c.get("status") == "approved" and c.get("app") in by_id]
    done = {(c["app"], c["chart"]) for c in out}
    for d in data:
        for ch in _series(d, key.get(d["app_id"])):
            if (d["app_id"], ch["name"]) in done:
                continue
            cats = {cat_norm(c) for c in ch["categories"]}
            if not cats:
                continue
            want = (ch.get("concept") or {}).get("concept")
            best = None
            for src in data:
                if src["app_id"] == d["app_id"]:
                    continue
                for t in src["tables"]:
                    if ch["kind"] == "table" and (key.get(src["app_id"]) and key[src["app_id"]] not in t["headers"]
                                                  or len(t["records"]) < len(cats)):
                        continue  # an aggregated table is checked against detailed records
                    for cf in t["headers"]:
                        vals = [cat_norm(_cv(r["fields"].get(cf))) for r in t["records"]]
                        ov = len(cats & set(vals)) / max(1, len(cats))
                        if ov < 0.5:
                            continue
                        for vf in t["headers"]:
                            fld = _field(src, vf)
                            if vf == cf or fld is None or fld["family"] not in ("num", "pct"):
                                continue
                            vc = (fld.get("concept") or {}).get("concept")
                            if vc in ("id", "date", "percent"):
                                continue
                            aggs = ["share"] if want == "percent" else ["sum", "count"]
                            for agg in aggs:
                                exp = group_expected(t["records"], cf, vf, agg)
                                hits = sum(1 for c, p in ch["points"].items() if cat_norm(c) in exp and
                                           formula.same(exp[cat_norm(c)], p.get("value"),
                                                        tolerance=_approx_tol(p, exp[cat_norm(c)]))[0])
                                hint = _label_score(ch["title"], vf)
                                fit = hits / max(1, len(ch["points"])) if ch["points"] else 0
                                meaning = 0.0
                                if want and want not in ("number", "text", "percent") and vc:
                                    meaning = 0.15 if vc == want else -0.3
                                if agg == "count" and want in ("money",):
                                    meaning = -0.3
                                if agg == "count" and want == "quantity" and vc != "quantity":
                                    meaning = max(meaning, 0.0)
                                detail = 0.04 if len(t["records"]) > len(cats) else 0.0  # the records, not a summary
                                sc = 0.45 * ov + 0.35 * fit + 0.1 * hint + meaning + detail - \
                                    (0.05 if agg == "count" else 0)
                                if best is None or sc > best[0]:
                                    best = (sc, src["app_id"], cf, vf, agg, hits, fld)
            if best:
                sc, src_id, cf, vf, agg, hits, fld = best
                sc = max(0.0, sc)
                how = "share of the total of" if agg == "share" else f"{agg} of"
                expl = (f"categories found in {by_id[src_id]['name']} › {cf}; {hits} of {len(ch['points'])} value(s) "
                        f"equal the {how} {vf}")
                if want and (fld.get("concept") or {}).get("concept") not in (want, None) and want not in \
                        ("number", "text", "percent"):
                    expl += f" (note: {vf} is a {(fld.get('concept') or {}).get('concept')}, the chart shows {want})"
                out.append({"id": _id("G"), "app": d["app_id"], "chart": ch["name"], "kind": ch["kind"],
                            "source_app": src_id, "category_field": cf, "value_field": vf, "aggregate": agg,
                            "status": "suggested", "confidence": round(min(sc, 0.95), 3),
                            "review": "auto_mapped" if sc >= mapping.AUTO_MAP and not ch["approximate"]
                            else "needs_review", "explanation": expl,
                            "tolerance": None})
    return out


def _approx_tol(p: dict[str, Any] | None, expected: Decimal) -> Decimal | None:
    """A value estimated from the drawing (no label) agrees within 2 % of the expected value; anything else must
    match as the application rounds it."""
    if p and p.get("approximate"):
        return abs(expected) * Decimal("0.02")
    return None


def _suggest_statuses(data: list[dict[str, Any]], fields: list[dict[str, Any]], key: dict[str, str],
                      saved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Status values that mean the same thing (01 = Pending = Waiting): from records with the same key in both
    applications, else from common status words. Uncertain groups need review."""
    by_id = {d["app_id"]: d for d in data}
    out = []
    saved_by = {s["field_id"]: s for s in saved}
    for row in fields:
        if row.get("family") != "status" and not any(
                (_field(by_id[aid], m["field"]) or {}).get("family") == "status" for aid, m in row["members"].items()):
            continue
        if len(row["members"]) < 2:
            continue
        old = saved_by.get(row["id"])
        if old and all(g["status"] == "approved" for g in old["groups"]):
            out.append(old)
            continue
        apps = list(row["members"])
        base = apps[0]

        def values_of(aid: str) -> list[str]:
            f = row["members"][aid]["field"]
            d = by_id[aid]
            if row["members"][aid]["kind"] == "value":
                v = _cv(d["values"].get(f))
                return [str(v).strip()] if v not in (None, "") else []
            t = next((t for t in d["tables"] if f in t["headers"]), None)
            return sorted({str(_cv(r["fields"].get(f))).strip() for r in (t["records"] if t else [])
                           if _cv(r["fields"].get(f)) not in (None, "")})

        groups: dict[str, dict[str, Any]] = {}
        for v in values_of(base):
            groups[v] = {"name": v, "values": {base: v}, "confidence": 1.0}
        for aid in apps[1:]:
            pairs = Counter()
            ka, kb = key.get(base), key.get(aid)
            if ka and kb:
                ra, rb = _keys(by_id[base], ka), _keys(by_id[aid], kb)
                for k in set(ra) & set(rb):
                    va = _cv(ra[k]["fields"].get(row["members"][base]["field"]))
                    vb = _cv(rb[k]["fields"].get(row["members"][aid]["field"]))
                    if va not in (None, "") and vb not in (None, ""):
                        pairs[(str(va).strip(), str(vb).strip())] += 1
            for va, g in groups.items():
                hits = {vb: n for (a, vb), n in pairs.items() if a == va}
                if hits:
                    vb, n = max(hits.items(), key=lambda x: x[1])
                    total = sum(hits.values())
                    g["values"][aid] = vb
                    g["confidence"] = min(g["confidence"], round((n / total) * (0.95 if total >= 2 else 0.75), 3))
                else:
                    vb = _status_word(va, values_of(aid))
                    if vb:
                        g["values"][aid] = vb
                        g["confidence"] = min(g["confidence"], 0.7)
                    else:
                        g["confidence"] = min(g["confidence"], 0.0)
        res = []
        for g in groups.values():
            words = [v for v in g["values"].values() if not re.fullmatch(r"[\d\W]+", v)]
            if len(words) >= 2 and len(g["values"]) == len(apps) and g["confidence"] > 0 and \
                    any({w.strip().lower() for w in words} <= grp for grp in STATUS_GROUPS):
                g["confidence"] = max(g["confidence"], 0.9)  # the words agree too (Pending = Waiting)
            res.append({"name": words[0] if words else g["name"], "values": g["values"],
                        "confidence": g["confidence"], "status": _status(g["confidence"])})
        if old:  # approved groups stay as approved
            keep = [g for g in old["groups"] if g["status"] == "approved"]
            res = keep + [g for g in res if not any(g["values"].get(base) == k["values"].get(base) for k in keep)]
        unmatched = {aid: sorted(set(values_of(aid)) - {g["values"].get(aid) for g in res}) for aid in apps}
        out.append({"field_id": row["id"], "field": row["name"], "groups": res,
                    "unmatched": {k: v for k, v in unmatched.items() if v}})
    return out


def _status_word(value: str, options: list[str]) -> str | None:
    low = value.strip().lower()
    for g in STATUS_GROUPS:
        if low in g:
            hit = [o for o in options if o.strip().lower() in g]
            if len(hit) == 1:
                return hit[0]
    return None


def chart_expected(records: list[dict[str, Any]], cat_field: str, value_field: str,
                   aggregate: str = "sum") -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    cnt: Counter = Counter()
    for r in records:
        c = cat_norm(_cv(r["fields"].get(cat_field)))
        if not c:
            continue
        if aggregate == "count":
            out[c] = out.get(c, Decimal(0)) + 1
            continue
        v = formula.to_decimal(_cv(r["fields"].get(value_field)))
        if v is None:
            continue
        out[c] = out.get(c, Decimal(0)) + v
        cnt[c] += 1
    if aggregate == "avg":
        out = {k: v / cnt[k] for k, v in out.items() if cnt[k]}
    return out


# ======================================================================================================================
# the comparison (deterministic)
# ======================================================================================================================

def _fmt(v: Any) -> Any:
    if isinstance(v, Decimal):
        q = v.quantize(Decimal("0.01")) if v != v.to_integral() else v.quantize(Decimal(1))
        return format(q, "f")
    return v


def _fmt_like(v: Any, shown: Any) -> Any:
    """An expected value written with the decimals the application shows (9,450.00 next to 9,450.00)."""
    if isinstance(v, Decimal) and shown not in (None, ""):
        places = formula.decimals_of(shown)
        return f"{v.quantize(Decimal(1).scaleb(-places)):,.{places}f}"
    return _fmt(v)


def compare(data: list[dict[str, Any]], cfg: dict[str, Any], *, decisions: list[dict[str, Any]] | None = None,
            rules: dict[str, Any] | None = None, scope: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    by_id = {d["app_id"]: d for d in data}
    names = {d["app_id"]: d["name"] for d in data}
    letter = {d["app_id"]: d["letter"] for d in data}
    letter_to_id = {d["letter"]: d["app_id"] for d in data}
    rows: list[dict[str, Any]] = []
    accepted = {x["signature"]: x for x in reversed(decisions or []) if x["decision"] in ACCEPTING}
    flagged = {x["signature"]: x for x in reversed(decisions or [])}
    key_row = next((r for r in cfg.get("fields") or [] if r.get("is_key") and r["status"] in
                    ("approved", "auto_mapped")), None)
    key = {aid: m["field"] for aid, m in (key_row or {}).get("members", {}).items() if aid in by_id}
    recs = {aid: _keys(by_id[aid], f) for aid, f in key.items()}
    status_maps = {s["field_id"]: s for s in cfg.get("statuses") or []}

    def add(field: str, field_id: str | None, record_key: str | None, values: dict[str, Any], result: str,
            category: str, explanation: str, *, expected: Any = None, actual: Any = None, difference: Any = None,
            checks: list[dict[str, Any]] | None = None, calculation: str | None = None, mapping_used: str | None = None,
            pair: list[str] | None = None) -> None:
        if result == "WARNING":
            result = CATEGORY_RESULT.get(category, result)
        normalized = result == "PASS*"
        if normalized:
            result = "PASS"  # same value after normalization (AED 1,200.00 = 1200)
        apps_in = sorted(values)
        sig_src = json.dumps([field_id or field, record_key, apps_in,
                              [str((values[a] or {}).get("value")) for a in apps_in], str(expected)], default=str)
        sig = hashlib.sha1(sig_src.encode()).hexdigest()[:16]
        row = {"id": "WR-" + hashlib.sha1(f"{sig}|{len(rows)}".encode()).hexdigest()[:10].upper(),
               "signature": sig, "field": field, "field_id": field_id, "record_key": record_key,
               "values": values, "result": result, "category": category, "explanation": explanation,
               "expected": _fmt(expected), "actual": actual, "difference": _fmt(difference),
               "checks": checks or [], "calculation": calculation, "mapping": mapping_used,
               "pairs": pair or [], "evidence": sorted({(v or {}).get("evidence") for v in values.values()
                                                       if (v or {}).get("evidence")}),
               "sources": sorted({(v or {}).get("source") for v in values.values() if (v or {}).get("source")}),
               "normalized": normalized, "source_app": names.get((pair or apps_in or [None])[0]),
               "target_app": names.get((pair or apps_in or [None, None])[-1]) if len(pair or apps_in) > 1 else None,
               "rule": calculation or mapping_used,
               "suggested_classification": SUGGESTED.get(category) if result in OPEN_RESULTS else None}
        if row["suggested_classification"]:
            row["suggested_label"] = CLASSIFICATION[row["suggested_classification"]]
        if result in OPEN_RESULTS and sig in accepted:
            d = accepted[sig]
            row["decision"] = {"decision": d["decision"], "label": DECISIONS[d["decision"]], "by": d["decided_by"],
                               "at": d["decided_at"], "note": d.get("note")}
            row["original_result"] = result
            row["result"] = "ACCEPTED"
        elif sig in flagged:
            d = flagged[sig]
            row["decision"] = {"decision": d["decision"], "label": DECISIONS[d["decision"]], "by": d["decided_by"],
                               "at": d["decided_at"], "note": d.get("note")}
        if row.get("decision"):
            row["decision"]["classification"] = CLASSIFICATION.get(row["decision"]["decision"])
        rows.append(row)

    def vcell(c: Any, extra: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if c is None:
            return None
        c = c if isinstance(c, dict) else {"value": c}
        out = {k: c.get(k) for k in ("value", "confidence", "source", "evidence", "method", "box", "where")}
        out.update(extra or {})
        return out

    # 0. 23.9.0: filtered datasets must cover the same scope (Region = East in one, Territory = East in the other)
    for p_ in scope or []:
        add(f"Filter scope: {p_['filter']}", None, None,
            {aid: {"value": ", ".join(map(str, v)) if isinstance(v, list) else v} for aid, v in p_["values"].items()},
            "NOT_COMPARABLE", "scope_mismatch",
            f"The applications were not filtered the same way on '{p_['filter']}' ({p_['why']}): their data cover "
            "different scopes. Apply the equivalent filter (Sync Equivalent Filters) or a local filter, then compare "
            "again.", pair=list(p_["values"]))

    # 0b. no approved record key: record-level fields cannot be lined up; say so instead of an empty result
    record_fields = [r for r in cfg.get("fields") or [] if not r.get("is_key") and r["status"] in
                     ("approved", "auto_mapped") and sum(1 for m in r["members"].values()
                                                         if m.get("kind", "column") == "column") >= 2]
    if len(key) < 2 and record_fields:
        add("Record key", None, None, {d["app_id"]: {"value": key.get(d["app_id"])} for d in data},
            "INCOMPLETE", "key_missing",
            "No record identifier is approved for at least two applications, so the records and their fields "
            f"({', '.join(r['name'] for r in record_fields[:6])}) were not compared. Choose the identifier column "
            "(for example Invoice No) in the mapping review and save it.")

    # 1. records: missing and duplicate (each application against every other)
    unique_keys = set()
    for aid, f in key.items():
        t = _main_table(by_id[aid], f)
        if t is None:
            continue
        cnt = Counter(key_norm(_cv(r["fields"].get(f))) for r in t["records"])
        cnt.pop(None, None)
        unique_keys |= set(cnt)
        for k, n in cnt.items():
            if n > 1:
                add(key_row["name"], key_row["id"], k, {aid: {"value": f"{n} rows", "source": t["source"]}}, "FAIL",
                    "duplicate_record", f"Record {k} appears {n} times in {names[aid]}.", actual=n, expected=1)
    table_apps = [aid for aid in key if _main_table(by_id[aid], key[aid]) is not None]
    for x, y in itertools.permutations(table_apps, 2):
        for k in sorted(set(recs[x]) - set(recs[y])):
            complete = by_id[y]["coverage"]["complete"]
            add(key_row["name"], key_row["id"], k,
                {x: vcell(recs[x][k]["fields"].get(key[x])), y: None},
                "FAIL" if complete else "WARNING", "missing_record" if complete else "not_on_screenshots",
                f"Record {k} is in {names[x]} but not in {names[y]}." + (
                    "" if complete else f" {names[y]} was read from screenshots that may not show every record, "
                                        "so this is not reported as a defect."), pair=[x, y])

    # 2. mapped fields, value by value
    for row in cfg.get("fields") or []:
        if row.get("is_key") or len(row["members"]) < 2:
            continue
        members = {aid: m for aid, m in row["members"].items() if aid in by_id}
        if row["status"] not in ("approved", "auto_mapped"):
            add(row["name"], row["id"], None, {aid: None for aid in members}, "WARNING", "mapping_not_approved",
                "This field mapping was not approved, so it was not compared.")
            continue
        fam = row.get("family") or "text"
        units = {aid: (_field(by_id[aid], m["field"]) or {}).get("unit") for aid, m in members.items()}
        known_units = {u for u in units.values() if u}
        if len(known_units) > 1 and fam in ("num", "pct"):
            add(row["name"], row["id"], None, {aid: {"value": None, "unit": u} for aid, u in units.items()},
                "NOT_COMPARABLE", "unit_mismatch",
                "The applications show this field in different units (" + " / ".join(
                    f"{names[a]}: {u}" for a, u in units.items() if u) + "). Add a conversion rule or correct the "
                "mapping; the values were not compared.", pair=list(members))
            continue
        calc_apps = {r["app"] for r in cfg.get("rules") or [] if r.get("status") == "approved" and
                     r["field"] == members.get(r["app"], {}).get("field")}
        keys: list[str | None]
        column_apps = [a for a, m in members.items() if m.get("kind", "column") == "column"]
        if column_apps and any(a in recs for a in column_apps):
            keys = sorted(set().union(*[set(recs.get(a, {})) for a in members]))
        else:
            keys = [None]
        for k in keys:
            vals: dict[str, Any] = {}
            for aid, m in members.items():
                if k is None or m.get("kind") == "value" and aid not in recs:
                    vals[aid] = vcell(by_id[aid]["values"].get(m["field"]))
                else:
                    rec = recs.get(aid, {}).get(k)
                    vals[aid] = vcell(rec["fields"].get(m["field"])) if rec else None
            present = {a: v for a, v in vals.items() if v is not None}
            if len(present) < 2:
                continue  # a missing record is reported once, above
            checks = []
            for x, y in itertools.combinations(present, 2):
                checks.append(_check(x, y, present[x], present[y], fam, row, status_maps.get(row["id"]),
                                     calc_apps, names, rules or {}))
            worst = min(checks, key=lambda c: ORDER.index(c["result"]))
            cat = worst.get("category") or "match"
            mapping_used = " = ".join(f"{letter[a]} › {members[a]['field']}" for a in members)
            add(row["name"], row["id"], k, vals, worst["result"], cat, worst["why"],
                expected=worst.get("expected"), actual=worst.get("actual"), difference=worst.get("difference"),
                checks=checks, mapping_used=mapping_used, pair=list(present))

    # 3. calculation rules
    for rule in cfg.get("rules") or []:
        if rule["app"] not in by_id:
            continue
        t_app = by_id[rule["app"]]
        readable = formula.pretty(rule["expression"], {d["letter"]: d["name"] for d in data})
        label = f"{rule['field']} (calculated)"
        src_letters = {lt for lt, _ in formula.refs(rule["expression"])}
        if rule.get("status") != "approved":
            add(label, rule.get("field_id"), None, {rule["app"]: None}, "WARNING", "rule_not_approved",
                f"The calculation for {names[rule['app']]} › {rule['field']} was not approved, so it was not checked.",
                calculation=readable)
            continue
        fld = _field(t_app, rule["field"])
        if fld is None:
            add(label, rule.get("field_id"), None, {rule["app"]: None}, "WARNING", "field_not_found",
                f"{names[rule['app']]} › {rule['field']} was not found in this run.", calculation=readable)
            continue
        targets: list[tuple[str | None, Any]] = []
        if rule.get("scope") == "record" and fld["kind"] == "column" and rule["app"] in recs:
            targets = [(k, r["fields"].get(rule["field"])) for k, r in sorted(recs[rule["app"]].items())]
        else:
            targets = [(None, t_app["values"].get(rule["field"]))]
        for k, shown_cell in targets:
            shown = _cv(shown_cell)
            vals = {rule["app"]: vcell(shown_cell)}
            src_rec = {}
            missing_src = False
            for lt in src_letters:
                sid = letter_to_id.get(lt)
                if sid is None:
                    missing_src = True
                    continue
                if k is not None:
                    rec = recs.get(sid, {}).get(k)
                    if rec is None:
                        missing_src = True
                    src_rec[lt] = rec

            def scalar(lt: str, field: str, k=k, src_rec=src_rec) -> Any:
                sid = letter_to_id[lt]
                if k is not None and src_rec.get(lt) is not None:
                    c = src_rec[lt]["fields"].get(field)
                    vals.setdefault(sid, vcell(c, {"field": field}))
                    return _cv(c)
                c = by_id[sid]["values"].get(field)
                vals.setdefault(sid, vcell(c, {"field": field}))
                return _cv(c)

            def column(lt: str, field: str) -> list[Any]:
                d = by_id[letter_to_id[lt]]
                t = next((t for t in d["tables"] if field in t["headers"]), None)
                return [_cv(r["fields"].get(field)) for r in (t["records"] if t else [])]

            if missing_src:
                add(label, rule.get("field_id"), k, vals, "WARNING", "source_missing",
                    f"The values this calculation needs are not available for record {k}.", calculation=readable,
                    actual=shown)
                continue
            if shown in (None, ""):
                add(label, rule.get("field_id"), k, vals, "WARNING", "value_not_read",
                    f"{names[rule['app']]} › {rule['field']} could not be read.", calculation=readable)
                continue
            try:
                expected = formula.evaluate(rule["expression"], scalar, column)
            except formula.FormulaError as exc:
                add(label, rule.get("field_id"), k, vals, "WARNING", "calculation_not_possible",
                    f"The calculation could not be made: {exc}", calculation=readable, actual=shown)
                continue
            ok, diff = formula.same(expected, shown, tolerance=rule.get("tolerance"))
            conf = min([float((v or {}).get("confidence") or 1) for v in vals.values() if v] or [1])
            over_column = any(fn in rule["expression"].upper() for fn in ("SUM(", "COUNT(", "AVG(", "MIN(", "MAX("))
            partial = over_column and not all(by_id[letter_to_id[lt]]["coverage"]["complete"]
                                              for lt in src_letters if lt in letter_to_id)
            if ok:
                result, cat = "PASS", "match"
                why = f"{names[rule['app']]} shows {shown}; calculated {_fmt(expected)} ({readable})."
                if partial:
                    result, cat = "WARNING", "partial_data"
                    why += " The source was read from screenshots that may not show every record: not a full check."
            elif conf < LOW:
                result, cat = "WARNING", "ocr_uncertainty"
                why = (f"{names[rule['app']]} shows {shown} but the calculation gives {_fmt(expected)}. A value was "
                       "read from a screenshot with low confidence: check it before reporting a defect.")
            elif partial:
                result, cat = "WARNING", "partial_data"
                why = (f"{names[rule['app']]} shows {shown}; the calculation over the records that were read gives "
                       f"{_fmt(expected)}. The source was read from screenshots that may not show every record.")
            else:
                result, cat = "FAIL", "calculation_mismatch"
                why = (f"{names[rule['app']]} shows {shown} but {readable} = {_fmt(expected)}: a difference of "
                       f"{_fmt(diff)}.")
            add(label, rule.get("field_id"), k, vals, result, cat, why, expected=expected, actual=shown,
                difference=diff if not ok else None, calculation=readable,
                mapping_used=f"{letter[rule['app']]} › {rule['field']} = {rule['expression']}",
                pair=sorted({rule["app"], *[letter_to_id[x] for x in src_letters if x in letter_to_id]}))

    # 4. charts, pie shares and aggregated tables against the detailed records they summarize
    for ch_rule in cfg.get("charts") or []:
        if ch_rule["app"] not in by_id or ch_rule["source_app"] not in by_id:
            continue
        d = by_id[ch_rule["app"]]
        ch = next((c for c in _series(d, key.get(d["app_id"])) if c["name"] == ch_rule["chart"]), None)
        is_table = (ch or {}).get("kind") == "table" or ch_rule.get("kind") == "table"
        label = f"{ch_rule['chart']} ({'summary table' if is_table else 'chart'})"
        pair = [ch_rule["app"], ch_rule["source_app"]]
        if ch_rule.get("status") != "approved":
            add(label, None, None, {ch_rule["app"]: None}, "WARNING", "rule_not_approved",
                "The chart rule was not approved, so the chart was not checked.")
            continue
        if ch is None:
            add(label, None, None, {ch_rule["app"]: None}, "WARNING", "field_not_found",
                f"'{ch_rule['chart']}' was not found in {names[ch_rule['app']]} in this run.")
            continue
        src = by_id[ch_rule["source_app"]]
        t = next((t for t in src["tables"] if ch_rule["category_field"] in t["headers"]
                  and ch_rule["value_field"] in t["headers"]), None)
        agg = ch_rule.get("aggregate") or "sum"
        exp = group_expected(t["records"] if t else [], ch_rule["category_field"], ch_rule["value_field"], agg)
        vf = _field(src, ch_rule["value_field"]) or {}
        unit_s, unit_c = vf.get("unit"), (ch.get("concept") or {}).get("unit")
        if agg == "share":
            how = (f"{names[src['app_id']]} › SUM({ch_rule['value_field']}) per {ch_rule['category_field']} ÷ "
                   f"SUM({ch_rule['value_field']}) × 100")
        else:
            how = (f"{agg.upper()} of {names[src['app_id']]} › {ch_rule['value_field']} by "
                   f"{ch_rule['category_field']}")
        if unit_s and unit_c and unit_s != unit_c and "%" not in (unit_s, unit_c) and agg != "count":
            add(label, None, None, {ch_rule["app"]: {"value": None, "source": ch["source"]}}, "NOT_COMPARABLE",
                "unit_mismatch", f"The {'table' if is_table else 'chart'} shows {unit_c} and "
                f"{ch_rule['value_field']} is in {unit_s}: the values cannot be compared without a conversion rule.",
                calculation=how, pair=pair)
            continue
        noun = "table" if is_table else "slice" if ch.get("chart_type") in ("pie", "donut", "doughnut") else "bar"
        for cat in ch["categories"] or list(ch["points"]):
            p = ch["points"].get(cat)
            e = exp.get(cat_norm(cat))
            vals = {ch_rule["app"]: vcell(p, {"category": cat, "source": ch["source"],
                                               "evidence": (p or {}).get("evidence") or ch.get("evidence")})
                    if p else {"value": None, "source": ch["source"], "evidence": ch.get("evidence")},
                    src["app_id"]: {"value": _fmt(e) if e is not None else None, "source": t["source"] if t else None}}
            if p is None or p.get("value") in (None, ""):
                add(label, None, cat, vals, "WARNING", "visual_check",
                    f"The value of '{cat}' could not be read: verify it visually against "
                    f"{_fmt(e) if e is not None else 'the source'}.", expected=e, calculation=how, pair=pair)
                continue
            if e is None:
                add(label, None, cat, vals, "FAIL" if src["coverage"]["complete"] else "WARNING",
                    "missing_record" if src["coverage"]["complete"] else "not_on_screenshots",
                    f"'{cat}' is shown but not found in {names[src['app_id']]}.", actual=p.get("value"),
                    calculation=how, pair=pair)
                continue
            tol = ch_rule.get("tolerance") if ch_rule.get("tolerance") not in (None, "") else _approx_tol(p, e)
            ok, diff = formula.same(e, p.get("value"), tolerance=tol)
            low_c = float(p.get("confidence") or 1) < LOW and not p.get("approximate")
            partial = not src["coverage"]["complete"]
            if ok and p.get("approximate"):
                add(label, None, cat, vals, "WARNING", "approximate_match",
                    f"The {noun} has no value label; estimated {p.get('value')} from the drawing, which agrees with "
                    f"{how} = {_fmt_like(e, p.get('value') if p else None)} within 2 %. Not an exact check.", expected=e, actual=p.get("value"),
                    difference=diff, calculation=how, pair=pair)
            elif ok and partial:
                add(label, None, cat, vals, "WARNING", "partial_data",
                    f"The {noun} shows {p.get('value')}; {how} = {_fmt_like(e, p.get('value') if p else None)} over the records that were read. The "
                    "source may not show every record.", expected=e, actual=p.get("value"), calculation=how, pair=pair)
            elif ok:
                add(label, None, cat, vals, "PASS", "match", f"The {noun} shows {p.get('value')}; {how} = {_fmt_like(e, p.get('value') if p else None)}.",
                    expected=e, actual=p.get("value"), calculation=how, pair=pair)
            elif p.get("approximate"):
                add(label, None, cat, vals, "WARNING", "visual_check",
                    f"The {noun} has no value label; the drawing suggests about {p.get('value')} but {how} = "
                    f"{_fmt_like(e, p.get('value') if p else None)}. Check the chart visually.", expected=e, actual=p.get("value"), difference=diff,
                    calculation=how, pair=pair)
            elif _rounding_only(e, p.get("value")):
                add(label, None, cat, vals, "WARNING", "rounding_difference",
                    f"The {noun} shows {p.get('value')}; {how} = {_fmt_like(e, p.get('value') if p else None)}. The difference is within the last "
                    "displayed digit (rounding), not a defect.", expected=e, actual=p.get("value"), difference=diff,
                    calculation=how, pair=pair)
            elif partial:
                add(label, None, cat, vals, "WARNING", "partial_data",
                    f"The {noun} shows {p.get('value')} but {how} over the records that were read = {_fmt_like(e, p.get('value') if p else None)}. The "
                    "source may not show every record.", expected=e, actual=p.get("value"), difference=diff,
                    calculation=how, pair=pair)
            else:
                add(label, None, cat, vals, "WARNING" if low_c else "FAIL",
                    "ocr_uncertainty" if low_c else "chart_mismatch",
                    f"The {noun} shows {p.get('value')} but {how} = {_fmt_like(e, p.get('value') if p else None)}." + (
                        " The value was read with low confidence." if low_c else ""),
                    expected=e, actual=p.get("value"), difference=diff, calculation=how, pair=pair)
        shown_cats = {cat_norm(c) for c in (ch["categories"] or list(ch["points"]))}
        p = None
        for c_norm, e in exp.items():
            if c_norm not in shown_cats:
                add(label, None, c_norm, {src["app_id"]: {"value": _fmt(e)}, ch_rule["app"]: None}, "WARNING",
                    "visual_check", f"'{c_norm}' has data in {names[src['app_id']]} but was not found in "
                    f"'{ch['title']}'.", expected=e, calculation=how, pair=pair)
    # 5. charts nobody could read
    for d in data:
        for ch in d["charts"]:
            if not ch["extracted"] and not any(c["chart"] == ch["name"] and c["app"] == d["app_id"]
                                               for c in cfg.get("charts") or []):
                add(f"{ch['name']} (chart)", None, None, {d["app_id"]: {"value": None, "evidence": ch.get("evidence"),
                                                                       "source": ch["source"]}},
                    "WARNING", "visual_check", ch.get("note") or "The chart values could not be read.")
    return _summarize(rows, data, cfg)


def _check(x: str, y: str, cx: dict[str, Any], cy: dict[str, Any], fam: str, row: dict[str, Any],
           smap: dict[str, Any] | None, calc_apps: set[str], names: dict[str, str],
           rules: dict[str, Any]) -> dict[str, Any]:
    vx, vy = cx.get("value"), cy.get("value")
    base = {"pair": [x, y], "values": [vx, vy]}
    if vx in (None, "") or vy in (None, ""):
        who = names[x] if vx in (None, "") else names[y]
        return {**base, "result": "WARNING", "category": "value_not_read",
                "why": f"The value in {who} could not be read."}
    conf = min(float(cx.get("confidence") or 1), float(cy.get("confidence") or 1))
    if fam == "status":
        if not smap:
            return {**base, "result": "WARNING", "category": "status_not_mapped",
                    "why": "No approved status mapping for these values."}
        gx = next((g for g in smap["groups"] if str(g["values"].get(x, "")).strip().lower() == str(vx).strip().lower()),
                  None)
        gy = next((g for g in smap["groups"] if str(g["values"].get(y, "")).strip().lower() == str(vy).strip().lower()),
                  None)
        if gx is None or gy is None or gx.get("status") != "approved" or gy.get("status") != "approved":
            return {**base, "result": "WARNING", "category": "status_not_mapped",
                    "why": f"'{vx if gx is None else vy}' is not in the approved status mapping."}
        if gx is gy:
            res = "PASS" if str(vx).strip().lower() == str(vy).strip().lower() else "PASS*"
            return {**base, "result": res, "category": "match",
                    "why": f"{vx} and {vy} mean '{gx['name']}' in the approved status mapping."}
        return {**base, "result": "WARNING" if conf < LOW else "FAIL", "category": "status_mismatch",
                "expected": gx["name"], "actual": gy["name"],
                "why": f"{names[x]} shows {vx} ({gx['name']}) but {names[y]} shows {vy} ({gy['name']})."}
    nt = _ntype(fam)
    r = {**rules}
    if fam in ("num", "pct"):
        dx, dy = formula.decimals_of(vx), formula.decimals_of(vy)
        r["decimals"] = min(dx, dy) if (dx or dy) else None
    nx, ny = normalize(vx, nt, r)[0], normalize(vy, nt, r)[0]
    same, how = equal(nx, ny, nt, r)
    if same:
        raw = str(vx).strip() == str(vy).strip()
        return {**base, "result": "PASS" if raw else "PASS*", "category": "match",
                "why": "Same value." if raw else f"Same value after normalization ({vx} = {vy})."}
    if x in calc_apps and y in calc_apps:
        return {**base, "result": "ACCEPTED", "category": "different_business_rules",
                "why": f"{names[x]} and {names[y]} use different approved calculations; each is checked against "
                       "its source instead."}
    diff = None
    if fam in ("num", "pct") and nx is not None and ny is not None:
        try:
            diff = ny - nx
        except TypeError:
            diff = None
    if conf < LOW:
        return {**base, "result": "WARNING", "category": "ocr_uncertainty", "expected": vx, "actual": vy,
                "difference": diff, "why": f"{names[x]} shows {vx} and {names[y]} shows {vy}, but a value was read "
                                           "from a screenshot with low confidence. Check the screenshot."}
    cat = "value_mismatch" if fam != "date" else "date_mismatch"
    return {**base, "result": "FAIL", "category": cat, "expected": vx, "actual": vy, "difference": diff,
            "why": f"{names[x]} shows {vx} but {names[y]} shows {vy}" + (f" (difference {_fmt(diff)})." if diff
                                                                         is not None else ".")}


def _rounding_only(expected: Decimal, shown: Any) -> bool:
    """The shown value differs from the expected one by at most one unit of its last displayed digit (40.17 % shown
    as 40.2 % by one application and 40.1 % by another): a rounding difference, not a defect."""
    d = formula.to_decimal(shown)
    if d is None:
        return False
    unit = Decimal(1).scaleb(-formula.decimals_of(shown))
    return abs(d - expected) <= unit


def _summarize(rows: list[dict[str, Any]], data: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    c = Counter(r["result"] for r in rows)
    cats = Counter(r["category"] for r in rows if r["result"] in OPEN_RESULTS)
    names = {d["app_id"]: d["name"] for d in data}
    pairs = {}
    for x, y in itertools.combinations([d["app_id"] for d in data], 2):
        sel = [r for r in rows if x in r["pairs"] and y in r["pairs"]]
        cnt = Counter(r["result"] for r in sel)
        pairs[f"{x}|{y}"] = {"apps": [x, y], "label": f"{names[x]} vs {names[y]}", "total": len(sel),
                             "passed": cnt["PASS"] + cnt["ACCEPTED"], "failed": cnt["FAIL"],
                             "warnings": cnt["WARNING"], "needs_review": cnt["NEEDS_REVIEW"],
                             "not_comparable": cnt["NOT_COMPARABLE"], "incomplete": cnt["INCOMPLETE"]}
    anchor = data[0]["app_id"]
    consistency = {}
    for d in data[1:]:
        sel = [r for r in rows if anchor in r["pairs"] and d["app_id"] in r["pairs"]]
        bad = sum(1 for r in sel if r["result"] == "FAIL")
        warn = sum(1 for r in sel if r["result"] in ("WARNING", "NEEDS_REVIEW", "INCOMPLETE", "NOT_COMPARABLE"))
        consistency[d["app_id"]] = {"name": d["name"], "checked": len(sel), "failed": bad, "warnings": warn,
                                    "consistent": bad == 0 and len(sel) > 0}
    partial = [d["name"] for d in data if not d["coverage"]["complete"]]
    failed_extraction = [d["name"] for d in data if d["coverage"].get("extraction") == "FAILED"]
    summary = {
        "total_comparisons": len(rows),
        "passed": c["PASS"] + c["ACCEPTED"],
        "failed": c["FAIL"], "warnings": c["WARNING"], "needs_review": c["NEEDS_REVIEW"],
        "not_comparable": c["NOT_COMPARABLE"], "incomplete": c["INCOMPLETE"], "accepted": c["ACCEPTED"],
        "normalized_matches": sum(1 for r in rows if r.get("normalized")),
        "records_checked": len({r["record_key"] for r in rows if r.get("record_key")}),
        "missing_records": cats["missing_record"] + cats["not_on_screenshots"],
        "duplicate_records": cats["duplicate_record"],
        "field_mismatches": cats["value_mismatch"] + cats["date_mismatch"],
        "calculation_mismatches": cats["calculation_mismatch"] + cats["chart_mismatch"],
        "status_mismatches": cats["status_mismatch"],
        "unverified_visual": cats["visual_check"] + cats["approximate_match"],
        "low_confidence": cats["ocr_uncertainty"],
        "rounding_differences": cats["rounding_difference"],
    }
    if c["FAIL"]:
        result = "FAIL"
    elif c["INCOMPLETE"] or failed_extraction:
        result = "INCOMPLETE"
    elif c["NEEDS_REVIEW"] or c["NOT_COMPARABLE"]:
        result = "NEEDS_REVIEW"
    elif c["WARNING"] or partial:
        result = "WARNING"
    else:
        result = "PASS"
    if not rows:
        result = "INCOMPLETE"
    rows.sort(key=lambda r: (ORDER.index(r["result"]), r["field"], str(r.get("record_key") or "")))
    return {"result": result, "summary": summary, "rows": rows, "pairs": pairs, "consistency": consistency,
            "apps": [{"app_id": d["app_id"], "letter": d["letter"], "name": d["name"], "coverage": d["coverage"]}
                     for d in data],
            "coverage_note": (f"Partial check: {', '.join(partial)} " + ("was" if len(partial) == 1 else "were") +
                              " read from screenshots that may not show every record.") if partial else None}


# ======================================================================================================================
# defect draft and report
# ======================================================================================================================

def defect_draft(row: dict[str, Any], apps: list[dict[str, Any]]) -> dict[str, Any]:
    names = {a["app_id"]: a["name"] for a in apps}
    shown = "; ".join(f"{names.get(a, a)}: {(v or {}).get('value', '(not found)')}" for a, v in row["values"].items())
    where = f" for record {row['record_key']}" if row.get("record_key") else ""
    if row.get("expected") is not None and row.get("actual") is not None:
        title = f"[Integration] {row['field']}{where}: shows {row['actual']}, expected {row['expected']}"
    else:
        title = f"[Integration] {row['field']}{where}: values differ ({shown})"
    steps = []
    for a in apps:
        if a["app_id"] in row["values"]:
            steps.append(f"Open {a['name']} and find {row['field'].replace(' (calculated)', '')}{where}.")
    steps.append(f"Compare the values: {shown}.")
    if row.get("calculation"):
        steps.append(f"Expected calculation: {row['calculation']} = {row.get('expected')}.")
    return {"title": title[:250], "applications": [names.get(a, a) for a in row["values"]], "field": row["field"],
            "record_key": row.get("record_key"), "expected": row.get("expected"), "actual": row.get("actual"),
            "difference": row.get("difference"), "steps": steps, "screenshots": row.get("evidence") or [],
            "explanation": row.get("explanation"), "calculation": row.get("calculation")}


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v))


COLORS = {"PASS": "#1f9d55", "PASS*": "#2b8a3e", "ACCEPTED": "#3b82f6", "FAIL": "#d64545", "WARNING": "#d9822b",
          "NEEDS_REVIEW": "#7c3aed", "NOT_COMPARABLE": "#475569", "INCOMPLETE": "#b45309"}


def workspace_report(project: dict[str, Any], e: dict[str, Any], cfg: dict[str, Any], cfg_row: dict[str, Any] | None,
                     defects: list[dict[str, Any]], decisions: list[dict[str, Any]], shots: dict[str, str | None],
                     brand: str = "SkyGuard QA") -> str:
    d = e["detail"]
    apps = d["apps"]
    ids = [a["app_id"] for a in apps]
    names = {a["app_id"]: a["name"] for a in apps}

    def badge(r: str) -> str:
        return f'<span class="b" style="background:{COLORS.get(r, "#6b7280")}">{_e(r)}</span>'

    s = d["summary"]
    apps_rows = "".join(
        f"<tr><td>{_e(a['letter'])}</td><td>{_e(a['name'])}</td><td>{a['coverage']['screenshots']}</td>"
        f"<td>{a['coverage']['live']}</td><td>{a['coverage']['files']}</td>"
        f"<td>{'yes' if a['coverage']['complete'] else 'partial'}</td>"
        f"<td>{_e(a['coverage'].get('extraction') or '')}</td><td>{_e(a['coverage'].get('scope') or '')}</td>"
        f"<td>{_e('; '.join(a['coverage']['notes']))}</td></tr>" for a in apps)
    map_rows = "".join(
        f"<tr><td>{_e(r['name'])}{' (key)' if r.get('is_key') else ''}</td>"
        + "".join(f"<td>{_e((r['members'].get(i) or {}).get('field', '—'))}</td>" for i in ids)
        + f"<td>{_e(r['status'].replace('_', ' '))}</td></tr>" for r in cfg.get("fields") or [])
    rule_rows = "".join(
        f"<tr><td>{_e(names.get(r['app']))} › {_e(r['field'])}</td>"
        f"<td>{_e(formula.pretty(r['expression'], {a['letter']: a['name'] for a in apps}))}</td>"
        f"<td>{_e(r['status'])}</td></tr>" for r in cfg.get("rules") or [])
    status_rows = "".join(
        f"<tr><td>{_e(g['name'])}</td>" + "".join(f"<td>{_e(g['values'].get(i, '—'))}</td>" for i in ids)
        + f"<td>{_e(g['status'])}</td></tr>" for st in cfg.get("statuses") or [] for g in st["groups"])
    res_rows = "".join(
        f"<tr><td>{_e(r['field'])}</td><td>{_e(r.get('record_key'))}</td>"
        + "".join(f"<td>{_e((r['values'].get(i) or {}).get('value') if r['values'].get(i) else '—')}</td>"
                  for i in ids)
        + f"<td>{_e(r.get('expected'))}</td><td>{_e(r.get('difference'))}</td><td>{badge(r['result'])}</td>"
        f"<td>{_e(r['explanation'])}{'<br><small>Rule: ' + _e(r['rule']) + '</small>' if r.get('rule') else ''}"
        f"{'<br><small>Suggested: ' + _e(r['suggested_label']) + '</small>' if r.get('suggested_label') else ''}"
        f"{'<br><i>Tester: ' + _e(r['decision'].get('classification') or r['decision']['label']) + '</i>' if r.get('decision') else ''}</td></tr>"
        for r in d["rows"][:3000])
    calc_rows = "".join(
        f"<tr><td>{_e(r['field'])}</td><td>{_e(r.get('record_key'))}</td><td>{_e(r.get('calculation'))}</td>"
        f"<td>{_e(r.get('expected'))}</td><td>{_e(r.get('actual'))}</td><td>{_e(r.get('difference'))}</td>"
        f"<td>{badge(r['result'])}</td></tr>" for r in d["rows"] if r.get("calculation"))
    shot_html = "".join(f'<figure><img src="{uri}" alt=""><figcaption>{_e(eid)}</figcaption></figure>'
                        for eid, uri in shots.items() if uri)
    def_rows = "".join(f"<tr><td>{_e(x['id'])}</td><td>{_e(x['title'])}</td><td>{_e(x['status'])}</td>"
                       f"<td>{_e(x.get('tfs_id') or '')}</td></tr>" for x in defects)
    dec_rows = "".join(f"<tr><td>{_e(x['decided_at'])}</td><td>{_e(x['decided_by'])}</td>"
                       f"<td>{_e((x['row'] or {}).get('field'))} {_e((x['row'] or {}).get('record_key') or '')}</td>"
                       f"<td>{_e(DECISIONS.get(x['decision']))}</td><td>{_e(x.get('note'))}</td></tr>"
                       for x in decisions)
    head_apps = "".join(f"<th>{_e(a['letter'])}: {_e(a['name'])}</th>" for a in apps)
    tiles = "".join(f'<div class="t"><b>{_e(v)}</b><span>{_e(k.replace("_", " "))}</span></div>' for k, v in s.items())
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Integration comparison {_e(e['id'])}</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;color:#1f2937;margin:28px;font-size:12px}}h1{{font-size:20px}}
h2{{font-size:15px;margin-top:22px;border-bottom:1px solid #ddd;padding-bottom:4px}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #d1d5db;padding:4px 6px;text-align:left;vertical-align:top}}th{{background:#f3f4f6}}
.b{{color:#fff;border-radius:4px;padding:1px 6px;font-weight:600;font-size:11px}}.tiles{{display:flex;flex-wrap:wrap;gap:8px}}
.t{{border:1px solid #d1d5db;border-radius:6px;padding:6px 10px;min-width:110px}}.t b{{display:block;font-size:16px}}
.t span{{color:#6b7280}}figure{{display:inline-block;margin:6px;max-width:46%}}img{{max-width:100%;border:1px solid #ccc}}
.note{{background:#fff7ed;border:1px solid #fdba74;padding:8px;border-radius:6px}}</style></head><body>
<h1>{_e(brand)} · Integration comparison report</h1>
<p><b>{_e(project['name'])}</b> · run {_e(e['id'])} · {_e(e['started_at'])} · by {_e(e.get('created_by'))} ·
overall {badge(e['result'])} · mapping version {_e((cfg_row or {}).get('version'))} approved by
{_e((cfg_row or {}).get('approved_by'))}</p>
{f'<p class="note">{_e(d["coverage_note"])}</p>' if d.get("coverage_note") else ''}
<h2>1. Applications compared</h2><table><tr><th>Letter</th><th>Application</th><th>Screenshots</th><th>Live captures</th>
<th>Files</th><th>Complete</th><th>Extraction</th><th>Scope</th><th>Notes</th></tr>{apps_rows}</table>
<h2>2. Scope and summary</h2><div class="tiles">{tiles}</div>
<h2>3. Mapping configuration</h2><table><tr><th>Business field</th>{head_apps}<th>Status</th></tr>{map_rows}</table>
{f'<h3>Status values</h3><table><tr><th>Meaning</th>{head_apps}<th>Status</th></tr>{status_rows}</table>' if status_rows else ''}
{f'<h3>Calculations</h3><table><tr><th>Displayed value</th><th>Calculation</th><th>Status</th></tr>{rule_rows}</table>' if rule_rows else ''}
<h2>4. Results</h2><table><tr><th>Field</th><th>Record</th>{head_apps}<th>Expected</th><th>Difference</th><th>Result</th><th>Explanation</th></tr>
{res_rows}</table>
<h2>5. Calculation validation</h2>{f'<table><tr><th>Field</th><th>Record</th><th>Calculation</th><th>Expected</th><th>Shown</th><th>Difference</th><th>Result</th></tr>{calc_rows}</table>' if calc_rows else '<p>No calculations in this run.</p>'}
<h2>6. Screenshot evidence</h2>{shot_html or '<p>No screenshots behind open results.</p>'}
<h2>7. Defects</h2>{f'<table><tr><th>Id</th><th>Title</th><th>Status</th><th>TFS</th></tr>{def_rows}</table>' if def_rows else '<p>None created.</p>'}
<h2>8. Tester decisions</h2>{f'<table><tr><th>When</th><th>Tester</th><th>Result</th><th>Decision</th><th>Note</th></tr>{dec_rows}</table>' if dec_rows else '<p>None.</p>'}
<p style="color:#6b7280;margin-top:24px">Results were decided by deterministic comparison and calculation rules.
AI only suggested mappings (approved by a tester) and wrote explanations.</p></body></html>"""
