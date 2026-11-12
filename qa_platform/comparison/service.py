"""Application Data Comparison & Defect Investigation Agent: the service behind the API.

Projects hold applications (web, desktop, remote desktop, screenshots/files), their screens, extracted datasets,
versioned field mappings, aggregate definitions, comparison executions, discrepancies and defects. Every change is
written to an append-only audit trail (hash chain; the database refuses UPDATE and DELETE on it).

Access: a project is visible to its owner, its members, administrators and programs on this PC.
Passwords: kept in Windows Credential Manager (namespace ``cmp_app``), never in the database, never returned.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from .. import aura, auth, branding
from ..clock import now_iso
from ..errors import QAError
from ..storage.db import dumps, loads
from . import aggregate, investigate, mapping, ocr, reconcile, reports
from .evidence import EvidenceStore

SECRET_NS = "cmp_app"
APP_TYPES = ("web", "desktop", "rdp", "screenshot", "file")
AUTH_METHODS = ("none", "form", "basic", "windows", "ntlm", "manual")
DEFECT_STATUSES = ("new", "under_review", "confirmed", "not_a_defect", "duplicate", "created_in_tfs", "closed")
OFFLINE_CODES = {"TFS_UNREACHABLE", "TFS_TIMEOUT", "TFS_REQUEST_FAILED", "TFS_RATE_LIMITED"}
MAPPING_STATUSES = ("auto_mapped", "needs_review", "approved", "manually_mapped", "rejected", "unmapped")


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _user() -> Any:
    return auth.current_user() or auth.LOCAL_PROGRAM


class ComparisonService:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self.db = rt.db
        self.secrets = rt.secrets
        self.evidence = EvidenceStore(rt.paths.evidence / "comparison")
        self._audit_lock = threading.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self._cancel: set[str] = set()

    def close(self) -> None:
        for jid in list(self.jobs):
            self._cancel.add(jid)

    # ---- audit -------------------------------------------------------------------------------------------------
    def audit(self, action: str, project_id: str | None, target: str | None = None, **details: Any) -> None:
        user = _user()
        with self._audit_lock:
            last = self.db.query_one("SELECT hash FROM cmp_audit ORDER BY id DESC LIMIT 1")
            prev = last["hash"] if last else ""
            at = now_iso()
            body = dumps(investigate.redact(details))
            digest = hashlib.sha256(f"{prev}|{at}|{project_id}|{user.username}|{action}|{target}|{body}".encode()).hexdigest()
            self.db.execute("INSERT INTO cmp_audit (at, project_id, actor, action, target, details, prev_hash, hash)"
                            " VALUES (?,?,?,?,?,?,?,?)", (at, project_id, user.username, action, target, body, prev,
                                                          digest))

    def audit_trail(self, project_id: str, limit: int = 500) -> list[dict[str, Any]]:
        self.project(project_id)
        rows = self.db.query("SELECT * FROM cmp_audit WHERE project_id = ? ORDER BY id DESC LIMIT ?",
                             (project_id, limit))
        for r in rows:
            r["details"] = loads(r["details"])
        return rows

    def verify_audit(self) -> dict[str, Any]:
        prev, n = "", 0
        for r in self.db.query("SELECT * FROM cmp_audit ORDER BY id"):
            expect = hashlib.sha256(f"{prev}|{r['at']}|{r['project_id']}|{r['actor']}|{r['action']}|{r['target']}|"
                                    f"{r['details']}".encode()).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != expect:
                return {"ok": False, "broken_at": r["id"], "checked": n}
            prev, n = r["hash"], n + 1
        return {"ok": True, "checked": n}

    # ---- projects ----------------------------------------------------------------------------------------------
    def _can(self, row: dict[str, Any]) -> bool:
        u = _user()
        if u.local or u.role == "admin":
            return True
        return row["owner"] == u.username or u.username in (loads(row["members"]) or [])

    def _project_row(self, project_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM cmp_projects WHERE id = ?", (project_id,))
        if row is None or not self._can(row):
            raise QAError("CMP_PROJECT_NOT_FOUND", "Comparison project not found.", status=404)
        return row

    def project(self, project_id: str) -> dict[str, Any]:
        row = self._project_row(project_id)
        row["members"] = loads(row["members"]) or []
        row["settings"] = loads(row["settings"])
        return row

    def projects(self) -> list[dict[str, Any]]:
        out = []
        for r in self.db.query("SELECT * FROM cmp_projects WHERE archived = 0 ORDER BY updated_at DESC"):
            if self._can(r):
                r["members"] = loads(r["members"]) or []
                r["settings"] = loads(r["settings"])
                r["applications"] = self.db.query_one("SELECT COUNT(*) AS n FROM cmp_applications WHERE project_id = ?",
                                                      (r["id"],))["n"]
                last = self.db.query_one("SELECT id, result, started_at FROM cmp_executions WHERE project_id = ? "
                                         "ORDER BY started_at DESC LIMIT 1", (r["id"],))
                r["last_execution"] = last
                out.append(r)
        return out

    def create_project(self, name: str, description: str = "", members: list[str] | None = None,
                       settings: dict[str, Any] | None = None) -> dict[str, Any]:
        pid, at = _id("CP"), now_iso()
        self.db.execute("INSERT INTO cmp_projects (id, name, description, owner, members, settings, created_at, "
                        "updated_at) VALUES (?,?,?,?,?,?,?,?)",
                        (pid, name.strip(), description, _user().username, dumps(members or []),
                         dumps(settings or {}), at, at))
        self.audit("project.create", pid, pid, name=name)
        return self.project(pid)

    def update_project(self, project_id: str, **changes: Any) -> dict[str, Any]:
        cur = self.project(project_id)
        u = _user()
        if "members" in changes and not (u.local or u.role == "admin" or cur["owner"] == u.username):
            raise QAError("CMP_FORBIDDEN", "Only the owner or an administrator can change members.", status=403)
        name = changes.get("name") or cur["name"]
        desc = changes.get("description") if changes.get("description") is not None else cur["description"]
        members = changes.get("members") if changes.get("members") is not None else cur["members"]
        settings = {**cur["settings"], **(changes.get("settings") or {})}
        self.db.execute("UPDATE cmp_projects SET name=?, description=?, members=?, settings=?, archived=?, "
                        "updated_at=? WHERE id=?", (name, desc, dumps(members), dumps(settings),
                                                    1 if changes.get("archived") else 0, now_iso(), project_id))
        self.audit("project.update", project_id, project_id, changed=sorted(k for k, v in changes.items()
                                                                            if v is not None))
        return self.project(project_id) if not changes.get("archived") else {**cur, "archived": 1}

    # ---- applications ------------------------------------------------------------------------------------------
    def _app_row(self, app_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM cmp_applications WHERE id = ?", (app_id,))
        if row is None:
            raise QAError("CMP_APP_NOT_FOUND", "Application not found.", status=404)
        self._project_row(row["project_id"])
        row["config"] = loads(row["config"])
        row["has_password"] = self.secrets.has(SECRET_NS, app_id) if self.secrets.available else False
        return row

    def applications(self, project_id: str) -> list[dict[str, Any]]:
        self.project(project_id)
        rows = self.db.query("SELECT * FROM cmp_applications WHERE project_id = ? ORDER BY created_at", (project_id,))
        for r in rows:
            r["config"] = loads(r["config"])
            r["has_password"] = self.secrets.has(SECRET_NS, r["id"]) if self.secrets.available else False
        return rows

    def save_application(self, project_id: str, data: dict[str, Any], app_id: str | None = None) -> dict[str, Any]:
        self.project(project_id)
        if data.get("app_type") not in APP_TYPES:
            raise QAError("CMP_APP_TYPE", f"Application type must be one of {', '.join(APP_TYPES)}.", status=422)
        if (data.get("auth_method") or "none") not in AUTH_METHODS:
            raise QAError("CMP_AUTH_METHOD", "Unknown sign-in method.", status=422)
        for key in ("base_url", "login_url"):
            url = data.get(key)
            if url and not re.match(r"^https?://[^\s/]+", str(url)):
                raise QAError("CMP_URL_INVALID", f"{key.replace('_', ' ')} must start with http:// or https://.",
                              status=422)
        if data.get("app_type") == "web" and not data.get("base_url"):
            raise QAError("CMP_URL_REQUIRED", "A web application needs its URL.", status=422)
        config = dict(data.get("config") or {})
        for k in ("allowed_hosts", "username_selector", "password_selector", "submit_selector", "process",
                  "title_pattern", "timeout_ms"):
            if data.get(k) is not None:
                config[k] = data[k]
        if data.get("username") is not None:
            config["username"] = str(data["username"]).strip()
        password = data.get("password")
        if password and not self.secrets.available:  # checked before anything is saved
            raise QAError("CREDENTIAL_STORE_UNAVAILABLE", "The Windows Credential Manager is not available, so "
                          "the password cannot be saved.", status=503)
        at = now_iso()
        if app_id:
            cur = self._app_row(app_id)
            config = {**cur["config"], **config}
            self.db.execute("UPDATE cmp_applications SET name=?, app_type=?, base_url=?, config=?, environment=?, "
                            "auth_method=?, description=?, updated_at=?, profile_version = profile_version + 1 "
                            "WHERE id=?", (data["name"], data["app_type"], data.get("base_url"), dumps(config),
                                           data.get("environment"), data.get("auth_method") or "none",
                                           data.get("description"), at, app_id))
        else:
            app_id = _id("CA")
            self.db.execute("INSERT INTO cmp_applications (id, project_id, name, app_type, base_url, config, "
                            "environment, auth_method, description, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (app_id, project_id, data["name"], data["app_type"], data.get("base_url"), dumps(config),
                             data.get("environment"), data.get("auth_method") or "none", data.get("description"), at,
                             at))
        if data.get("login_url") is not None:
            config["login_url"] = data["login_url"]
            self.db.execute("UPDATE cmp_applications SET config = ? WHERE id = ?", (dumps(config), app_id))
        if password:
            self.secrets.set(SECRET_NS, app_id, password)
        self.audit("application.save", project_id, app_id, name=data["name"], type=data["app_type"],
                   password_changed=bool(password))
        return self._app_row(app_id)

    def delete_application(self, app_id: str) -> None:
        row = self._app_row(app_id)
        self.db.execute("DELETE FROM cmp_applications WHERE id = ?", (app_id,))
        if self.secrets.available and row["has_password"]:
            self.secrets.delete(SECRET_NS, app_id)
        self.audit("application.delete", row["project_id"], app_id, name=row["name"])

    def _web_config(self, app: dict[str, Any]) -> dict[str, Any]:
        return {**app["config"], "base_url": app["base_url"], "auth_method": app["auth_method"]}

    def _extractor(self, app: dict[str, Any], session_id: str, job: str | None = None) -> Any:
        from .extract_web import WebExtractor

        password = self.secrets.get(SECRET_NS, app["id"]) if self.secrets.available else None

        def log(step: str, details: dict[str, Any]) -> None:
            self.db.execute("INSERT INTO cmp_navigation (project_id, application_id, session_id, at, step, details) "
                            "VALUES (?,?,?,?,?,?)", (app["project_id"], app["id"], session_id, now_iso(), step,
                                                     dumps(details)))

        play = self.rt.settings.section("comparison") or {}
        return WebExtractor(self._web_config(app), username=app["config"].get("username"), password=password, log=log,
                            headless=bool(play.get("headless", True)), browser=play.get("browser"),
                            cancel=(lambda: job in self._cancel) if job else None)

    def test_connection(self, app_id: str) -> dict[str, Any]:
        app = self._app_row(app_id)
        status, detail = "ok", "connected"
        try:
            if app["app_type"] == "web":
                with self._extractor(app, _id("NS")) as ex:
                    ex.login()
                    detail = f"signed in ({app['auth_method']})"
            elif app["app_type"] in ("desktop", "rdp"):
                from . import desktop

                w = desktop.find_window(app["config"])
                detail = f"window '{w['title']}' ({w.get('process')})"
            else:
                detail = "screenshots and files are uploaded; no connection needed"
        except QAError as exc:
            status, detail = "failed", exc.message
        except Exception as exc:  # browser/network errors arrive as plain exceptions
            status, detail = "failed", str(exc).splitlines()[0][:300]
        self.db.execute("UPDATE cmp_applications SET connection_status=?, connection_detail=?, last_connected_at=? "
                        "WHERE id=?", (status, detail, now_iso(), app_id))
        self.audit("application.test", app["project_id"], app_id, status=status)
        return {"status": status, "detail": detail}

    # ---- screens (discovery) -----------------------------------------------------------------------------------
    def discover(self, app_id: str, steps: list[dict[str, Any]] | None = None, name: str | None = None) -> dict[str, Any]:
        app = self._app_row(app_id)
        if app["app_type"] == "web":
            sid = _id("NS")
            with self._extractor(app, sid) as ex:
                ex.login()
                ex.run_steps(steps or [])
                info = ex.discover()
                info["kpis"] = ex.kpis()
                info["charts"] = [{k: c.get(k) for k in ("lib", "type", "title")} for c in ex.charts()]
        elif app["app_type"] in ("desktop", "rdp"):
            from . import desktop

            w = desktop.find_window(app["config"])
            info = {"title": w["title"], "remote": w["remote"]}
            if not w["remote"]:
                data = desktop.read_window(w)
                info["tables"] = [{"name": t.get("name"), "headers": (t.get("records") or [{}])[0].get("fields", {}).keys()
                                   and list(t["records"][0]["fields"].keys()), "rows": len(t.get("records") or [])}
                                  for t in data.get("tables") or []]
                info["fields"] = [{k: f.get(k) for k in ("name", "type")} for f in data.get("fields") or []]
        else:
            raise QAError("CMP_DISCOVERY_NA", "Screenshots and files have no screens to discover.", status=422)
        scr = _id("CS")
        self.db.execute("INSERT INTO cmp_screens (id, project_id, application_id, name, location, discovery, "
                        "fingerprint, created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (scr, app["project_id"], app_id, name or info.get("title") or "Screen", dumps(steps or []),
                         dumps(info), hashlib.sha1(dumps(info.get("tables")).encode()).hexdigest()[:16], now_iso()))
        aura.learn("page_structure", app["name"], name or info.get("title") or scr,
                   {"headings": info.get("headings"), "tables": info.get("tables"), "tabs": info.get("tabs")},
                   source="data comparison discovery", agent="comparison")
        self.audit("screen.discover", app["project_id"], scr, application=app_id)
        return {"id": scr, **info}

    def screens(self, app_id: str) -> list[dict[str, Any]]:
        self._app_row(app_id)
        rows = self.db.query("SELECT * FROM cmp_screens WHERE application_id = ? ORDER BY created_at DESC", (app_id,))
        for r in rows:
            r["discovery"], r["location"] = loads(r["discovery"]), loads(r["location"])
        return rows

    # ---- datasets ----------------------------------------------------------------------------------------------
    def _new_dataset(self, app: dict[str, Any], kind: str, method: str, filters: dict[str, Any] | None = None,
                     screen_id: str | None = None) -> str:
        ds, at = _id("DS"), now_iso()
        self.db.execute("INSERT INTO cmp_datasets (id, project_id, application_id, screen_id, kind, method, status, "
                        "filters, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (ds, app["project_id"], app["id"], screen_id, kind, method, "running", dumps(filters or {}),
                         _user().username, at, at))
        return ds

    def _store_records(self, ds: str, records: list[dict[str, Any]], start: int = 0) -> None:
        for i, r in enumerate(records, start=start):
            self.db.execute("INSERT INTO cmp_records (dataset_id, seq, record_key, page, fields) VALUES (?,?,?,?,?)",
                            (ds, i, r.get("key"), r.get("page"), dumps(r.get("fields") or {})))

    def _finish_dataset(self, ds: str, status: str, stats: dict[str, Any], aggregates: dict[str, Any] | None = None,
                        filters: dict[str, Any] | None = None) -> None:
        sets = "status=?, stats=?, updated_at=?" + (", aggregates=?" if aggregates is not None else "") + \
               (", filters=?" if filters is not None else "")
        params: list[Any] = [status, dumps(stats), now_iso()]
        if aggregates is not None:
            params.append(dumps(aggregates))
        if filters is not None:
            params.append(dumps(filters))
        self.db.execute(f"UPDATE cmp_datasets SET {sets} WHERE id = ?", (*params, ds))

    def dataset(self, ds: str, with_records: bool = False, limit: int = 200000) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM cmp_datasets WHERE id = ?", (ds,))
        if row is None:
            raise QAError("CMP_DATASET_NOT_FOUND", "Dataset not found.", status=404)
        self._project_row(row["project_id"])
        for k in ("filters", "stats", "aggregates"):
            row[k] = loads(row[k])
        if with_records:
            row["records"] = [{"key": r["record_key"], "page": r["page"], "fields": loads(r["fields"])}
                              for r in self.db.query("SELECT * FROM cmp_records WHERE dataset_id = ? ORDER BY seq "
                                                     "LIMIT ?", (ds, limit))]
        return row

    def datasets(self, project_id: str) -> list[dict[str, Any]]:
        self.project(project_id)
        rows = self.db.query("SELECT d.*, a.name AS application FROM cmp_datasets d JOIN cmp_applications a ON "
                             "a.id = d.application_id WHERE d.project_id = ? ORDER BY d.created_at DESC", (project_id,))
        for r in rows:
            for k in ("filters", "stats", "aggregates"):
                r[k] = loads(r[k])
        return rows

    def records_page(self, ds: str, page: int = 1, size: int = 100, search: str | None = None) -> dict[str, Any]:
        self.dataset(ds)
        size = max(1, min(size, 1000))
        where, params = "dataset_id = ?", [ds]
        if search:
            where += " AND fields LIKE ?"
            params.append(f"%{search[:80]}%")
        total = self.db.query_one(f"SELECT COUNT(*) AS n FROM cmp_records WHERE {where}", params)["n"]
        rows = self.db.query(f"SELECT * FROM cmp_records WHERE {where} ORDER BY seq LIMIT ? OFFSET ?",
                             (*params, size, (max(page, 1) - 1) * size))
        return {"total": total, "page": page, "size": size,
                "records": [{"key": r["record_key"], "page": r["page"], "fields": loads(r["fields"])} for r in rows]}

    def _save_evidence(self, app: dict[str, Any], data: bytes, *, source: str, screen: str | None = None,
                       sequence: int | None = None, original_name: str | None = None,
                       ocr_result: dict[str, Any] | None = None) -> dict[str, Any]:
        saved = self.evidence.save(app["project_id"], data)
        self.db.execute("INSERT INTO cmp_evidence (id, project_id, application_id, screen, kind, file, sha256, width, "
                        "height, source, sequence, ocr, original_name, captured_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (saved["id"], app["project_id"], app["id"], screen, saved["kind"], saved["file"],
                         saved["sha256"], saved["width"], saved["height"], source, sequence,
                         dumps(ocr_result) if ocr_result else None, original_name, now_iso()))
        return saved

    def evidence_list(self, project_id: str) -> list[dict[str, Any]]:
        self.project(project_id)
        rows = self.db.query("SELECT id, application_id, screen, kind, file, sha256, width, height, source, sequence, "
                             "original_name, captured_at, (ocr IS NOT NULL) AS has_ocr FROM cmp_evidence WHERE "
                             "project_id = ? ORDER BY captured_at DESC", (project_id,))
        return rows

    def evidence_file(self, evidence_id: str, annotated: bool = False) -> Path:
        row = self.db.query_one("SELECT * FROM cmp_evidence WHERE id = ?", (evidence_id,))
        if row is None:
            raise QAError("EVIDENCE_NOT_FOUND", "Evidence not found.", status=404)
        self._project_row(row["project_id"])
        if annotated:
            return self.evidence.path(row["project_id"], f"{evidence_id}.annotated.svg")
        if not self.evidence.verify(row["project_id"], row["file"], row["sha256"]):
            raise QAError("EVIDENCE_TAMPERED", "This evidence file was changed after it was captured (checksum "
                          "differs).", status=409)
        return self.evidence.path(row["project_id"], row["file"])

    def annotate(self, evidence_id: str, boxes: list[dict[str, Any]]) -> str:
        row = self.db.query_one("SELECT * FROM cmp_evidence WHERE id = ?", (evidence_id,))
        if row is None:
            raise QAError("EVIDENCE_NOT_FOUND", "Evidence not found.", status=404)
        self._project_row(row["project_id"])
        name = self.evidence.annotate(row["project_id"], row["file"], row["width"], row["height"], boxes)
        self.audit("evidence.annotate", row["project_id"], evidence_id, boxes=len(boxes))
        return name

    # web / desktop extraction runs in the background ------------------------------------------------------------
    def start_extraction(self, app_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        app = self._app_row(app_id)
        kind = spec.get("kind") or "table"
        if kind not in ("table", "kpis", "charts", "dashboard"):
            raise QAError("CMP_KIND", "Extraction kind must be table, kpis, charts or dashboard.", status=422)
        ds = self._new_dataset(app, kind, {"web": "dom", "desktop": "uia", "rdp": "ocr"}.get(app["app_type"], "file"),
                               screen_id=spec.get("screen_id"))
        job = _id("JOB")
        self.jobs[job] = {"id": job, "dataset_id": ds, "status": "running", "progress": "starting", "error": None}
        t = threading.Thread(target=self._extract, args=(app, ds, job, spec, _user()), daemon=True,
                             name=f"cmp-extract-{ds}")
        t.start()
        self.audit("dataset.extract", app["project_id"], ds, application=app_id, kind=kind)
        return {"job": job, "dataset_id": ds}

    def cancel(self, job: str) -> None:
        self._cancel.add(job)

    def _extract(self, app: dict[str, Any], ds: str, job: str, spec: dict[str, Any], user: Any) -> None:
        token = auth.set_current_user(user)
        state = self.jobs[job]
        try:
            if app["app_type"] == "web":
                self._extract_web(app, ds, job, spec, state)
            elif app["app_type"] in ("desktop", "rdp"):
                self._extract_desktop(app, ds, spec, state)
            else:
                raise QAError("CMP_UPLOAD_ONLY", "Upload screenshots or files for this application.", status=422)
            state["status"] = "done"
        except Exception as exc:
            msg = exc.message if isinstance(exc, QAError) else str(exc).splitlines()[0][:300]
            state.update(status="failed", error=msg)
            cur = self.db.query_one("SELECT stats FROM cmp_datasets WHERE id = ?", (ds,))
            stats = loads(cur["stats"]) if cur else {}
            self._finish_dataset(ds, "failed", {**stats, "complete": False, "reason": msg})
        finally:
            self._cancel.discard(job)
            auth.reset_current_user(token)

    def _extract_web(self, app: dict[str, Any], ds: str, job: str, spec: dict[str, Any], state: dict[str, Any]) -> None:
        with self._extractor(app, ds, job) as ex:
            state["progress"] = "signing in"
            ex.login()
            filters = ex.run_steps(spec.get("steps") or [])
            aggregates: dict[str, Any] = {}
            stats: dict[str, Any] = {"complete": True, "method": "dom"}
            kind = spec.get("kind") or "table"
            if kind in ("kpis", "dashboard"):
                aggregates["kpis"] = ex.kpis()
            if kind in ("charts", "dashboard"):
                aggregates["charts"] = ex.charts()
            if kind in ("kpis", "charts", "dashboard"):
                png = ex.screenshot()
                ev = self._save_evidence(app, png, source="web capture", screen=spec.get("screen_name"))
                aggregates["screenshot"] = ev["id"]
            if kind == "table":
                seq = [0]
                screenshots = spec.get("screenshots", True)

                def on_page(n: int, data: dict[str, Any], png: bytes | None) -> None:
                    state["progress"] = f"page {n}: {len(data['rows'])} rows"
                    ev_id = None
                    if png:
                        ev_id = self._save_evidence(app, png, source="web capture", screen=spec.get("screen_name"),
                                                    sequence=n)["id"]
                    recs = []
                    for row in data["rows"]:
                        fields = {h or f"Column {j + 1}": {"value": v, "method": "dom", "confidence": 1.0}
                                  for j, (h, v) in enumerate(zip(data["headers"], row))}
                        recs.append({"page": n, "fields": fields, "screenshot": ev_id})
                    key_field = spec.get("key_field")
                    for r in recs:
                        if key_field and key_field in r["fields"]:
                            r["key"] = r["fields"][key_field]["value"]
                        if ev_id:
                            for c in r["fields"].values():
                                c["screenshot"] = ev_id
                    self._store_records(ds, recs, seq[0])
                    seq[0] += len(recs)

                result = ex.table(spec.get("target") or {}, on_page=on_page, screenshots=screenshots)
                stats = result["stats"]
            stats["blocked_requests"] = sorted(set(ex.blocked))[:20]
            self._finish_dataset(ds, "complete" if stats.get("complete", True) else "incomplete", stats,
                                 aggregates or None, filters)

    def _extract_desktop(self, app: dict[str, Any], ds: str, spec: dict[str, Any], state: dict[str, Any]) -> None:
        from . import desktop

        w = desktop.find_window(app["config"])
        if not w["remote"] and app["app_type"] == "desktop":
            state["progress"] = "reading the window (UI Automation)"
            data = desktop.read_window(w)
            tables = data.get("tables") or []
            idx = int(spec.get("table_index") or 0)
            recs = tables[idx]["records"] if idx < len(tables) else []
            self._store_records(ds, recs)
            self._finish_dataset(ds, "complete", {"complete": True, "method": "uia", "records": len(recs),
                                                  "pages_visited": 1, "tables_found": len(tables)})
            return
        state["progress"] = "capturing the remote screen"
        tmp = self.rt.paths.root / "tmp" / f"{ds}.png"
        desktop.capture(w, tmp)
        try:
            self._ocr_into(app, ds, tmp.read_bytes(), spec, source="remote desktop capture")
        finally:
            tmp.unlink(missing_ok=True)

    def _ocr_into(self, app: dict[str, Any], ds: str, data: bytes, spec: dict[str, Any], *, source: str,
                  original_name: str | None = None) -> dict[str, Any]:
        tmp_ev = self._save_evidence(app, data, source=source, screen=spec.get("screen_name"),
                                     original_name=original_name)
        path = self.evidence.path(app["project_id"], tmp_ev["file"])
        try:
            res = ocr.read(path, self.rt.settings)
        except QAError as exc:
            self._finish_dataset(ds, "needs_manual", {"complete": False, "method": "ocr", "reason": exc.message,
                                                      "evidence": tmp_ev["id"]})
            return {"evidence": tmp_ev, "ocr": None, "error": exc.message}
        self.db.execute("UPDATE cmp_evidence SET ocr = ? WHERE id = ?", (dumps(res), tmp_ev["id"]))
        kind = spec.get("kind") or "table"
        if kind == "table":
            table = ocr.table_from_words(ocr.words_of(res), spec.get("headers"), int(spec.get("header_row") or 0))
            recs = [{"page": spec.get("sequence") or 1, "fields": {k: {**v, "screenshot": tmp_ev["id"]} for k, v in row.items()}}
                    for row in table["rows"]]
            key_field = spec.get("key_field")
            for r in recs:
                if key_field and key_field in r["fields"]:
                    r["key"] = r["fields"][key_field]["value"]
            start = self.db.query_one("SELECT COUNT(*) AS n FROM cmp_records WHERE dataset_id = ?", (ds,))["n"]
            self._store_records(ds, recs, start)
            low = sum(1 for r in recs for c in r["fields"].values() if c.get("confidence", 1) < 0.8)
            self._finish_dataset(ds, "complete", {"complete": True, "method": "ocr", "records": start + len(recs),
                                                  "headers": table["headers"], "low_confidence_cells": low,
                                                  "engine": res.get("engine"), "pages_visited": 1})
        else:
            kp = ocr.kpis_from_lines(res.get("lines") or [])
            self._finish_dataset(ds, "complete", {"complete": True, "method": "ocr", "engine": res.get("engine")},
                                 {"kpis": kp, "screenshot": tmp_ev["id"]})
        return {"evidence": tmp_ev, "ocr": {"engine": res.get("engine"), "lines": len(res.get("lines") or [])}}

    def upload_screenshot(self, app_id: str, data: bytes, spec: dict[str, Any], original_name: str | None,
                          dataset_id: str | None = None) -> dict[str, Any]:
        """A screenshot (or the next one of a multi-screenshot table) read with offline OCR."""
        app = self._app_row(app_id)
        ds = dataset_id or self._new_dataset(app, spec.get("kind") or "table", "ocr")
        if dataset_id:
            self.dataset(dataset_id)
        out = self._ocr_into(app, ds, data, spec, source="uploaded screenshot", original_name=original_name)
        self.audit("evidence.upload", app["project_id"], out["evidence"]["id"], dataset=ds,
                   sha256=out["evidence"]["sha256"])
        return {"dataset_id": ds, **out}

    def import_file(self, app_id: str, filename: str, content: bytes, spec: dict[str, Any]) -> dict[str, Any]:
        """An export from the application (CSV, XLSX or JSON) as a dataset."""
        app = self._app_row(app_id)
        if len(content) > 50 * 1024 * 1024:
            raise QAError("CMP_FILE_TOO_LARGE", "Files can be at most 50 MB.", status=413)
        low = filename.lower()
        if low.endswith(".csv"):
            text = content.decode("utf-8-sig", errors="replace")
            rows = list(csv.reader(io.StringIO(text)))
        elif low.endswith((".xlsx", ".xlsm")):
            from ..automation.files import read_xlsx

            book = read_xlsx(content)
            sheet = spec.get("sheet") or next(iter(book), None)
            rows = book.get(sheet) or []
        elif low.endswith(".json"):
            data = json.loads(content.decode("utf-8"))
            items = data if isinstance(data, list) else data.get("records") or data.get("items") or []
            headers = list(dict.fromkeys(k for it in items for k in it))
            rows = [headers] + [[it.get(h) for h in headers] for it in items]
        else:
            raise QAError("CMP_FILE_TYPE", "Import CSV, XLSX or JSON files.", status=415)
        if not rows:
            raise QAError("CMP_FILE_EMPTY", "The file has no rows.", status=422)
        headers = [str(h or f"Column {i + 1}").strip() for i, h in enumerate(rows[0])]
        key_field = spec.get("key_field")
        recs = []
        for row in rows[1:]:
            if not any(str(c or "").strip() for c in row):
                continue
            fields = {h: {"value": "" if v is None else str(v), "method": "file", "confidence": 1.0}
                      for h, v in zip(headers, row)}
            recs.append({"page": 1, "fields": fields,
                         "key": fields.get(key_field, {}).get("value") if key_field else None})
        ds = self._new_dataset(app, "table", "file", spec.get("filters"))
        self._store_records(ds, recs)
        self._finish_dataset(ds, "complete", {"complete": True, "method": "file", "records": len(recs),
                                              "headers": headers, "file": Path(filename).name[:120],
                                              "pages_visited": 1})
        self.audit("dataset.import", app["project_id"], ds, file=Path(filename).name[:120], records=len(recs),
                   sha256=hashlib.sha256(content).hexdigest())
        return self.dataset(ds)

    def set_dataset_aggregates(self, ds: str, values: dict[str, Any]) -> dict[str, Any]:
        """Displayed KPI/chart values typed or corrected by a tester (kept as such: method 'manual')."""
        cur = self.dataset(ds)
        agg = {**cur["aggregates"]}
        for k in ("kpis", "charts"):
            if k in values:
                agg[k] = [{**x, "method": x.get("method") or "manual"} for x in values[k] or []]
        self._finish_dataset(ds, cur["status"] if cur["status"] != "running" else "complete", cur["stats"], agg)
        self.audit("dataset.aggregates", cur["project_id"], ds)
        return self.dataset(ds)

    def set_dataset_filters(self, ds: str, filters: dict[str, Any]) -> dict[str, Any]:
        cur = self.dataset(ds)
        self._finish_dataset(ds, cur["status"], cur["stats"], None, filters)
        self.audit("dataset.filters", cur["project_id"], ds, filters=filters)
        return self.dataset(ds)

    # ---- mappings ----------------------------------------------------------------------------------------------
    def _columns(self, ds: dict[str, Any]) -> dict[str, list[Any]]:
        cols: dict[str, list[Any]] = {}
        for r in ds.get("records") or []:
            for f, c in (r.get("fields") or {}).items():
                cols.setdefault(f, []).append(c.get("value") if isinstance(c, dict) else c)
        return cols

    def latest_mapping(self, project_id: str, source_app: str, target_app: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM cmp_mappings WHERE project_id = ? AND source_app = ? AND target_app = ?"
                                " ORDER BY version DESC LIMIT 1", (project_id, source_app, target_app))
        if row:
            for k in ("mappings", "key_fields", "filter_map"):
                row[k] = loads(row[k]) if row[k] else ([] if k != "key_fields" else {})
        return row

    def mapping_history(self, project_id: str) -> list[dict[str, Any]]:
        self.project(project_id)
        rows = self.db.query("SELECT id, version, source_app, target_app, note, created_by, created_at, "
                             "mappings FROM cmp_mappings WHERE project_id = ? ORDER BY created_at DESC", (project_id,))
        for r in rows:
            m = loads(r.pop("mappings")) or []
            r["fields"] = len(m)
            r["approved"] = sum(1 for x in m if x.get("status") in ("approved", "manually_mapped"))
        return rows

    def suggest_mapping(self, source_ds: str, target_ds: str, use_ai: bool = False) -> dict[str, Any]:
        s, t = self.dataset(source_ds, True, 2000), self.dataset(target_ds, True, 2000)
        if s["project_id"] != t["project_id"]:
            raise QAError("CMP_PROJECT_MISMATCH", "Both datasets must belong to the same project.", status=422)
        proj = self.project(s["project_id"])
        sp, tp = mapping.profile(self._columns(s)), mapping.profile(self._columns(t))
        prev = self.latest_mapping(s["project_id"], s["application_id"], t["application_id"])
        reference = (proj["settings"].get("reference") or {})
        sug = mapping.suggest(sp, tp, approved=(prev or {}).get("mappings") or [], reference=reference,
                              rules=proj["settings"].get("rules") or {})
        ai_used = False
        if use_ai:
            try:
                sug = mapping.refine_with_ai(self.rt.ai, sp, tp, sug, context=proj["name"])
                ai_used = True
            except Exception as exc:
                for x in sug:
                    x.setdefault("evidence", []).append(f"local AI not used: {str(exc)[:120]}")
        for x in sug:
            if x["target_field"] and x["source_field"] in reference:
                x.setdefault("rules", {})["reference"] = reference[x["source_field"]]
        key = (prev or {}).get("key_fields") or self._guess_key(sp, tp, sug)
        return {"source_profile": sp, "target_profile": tp, "suggestions": sug, "key_fields": key,
                "previous_version": (prev or {}).get("version"), "ai_used": ai_used,
                "filter_map": (prev or {}).get("filter_map") or []}

    @staticmethod
    def _guess_key(sp: dict[str, Any], tp: dict[str, Any], sug: list[dict[str, Any]]) -> dict[str, str]:
        best = None
        for x in sug:
            if not x.get("target_field"):
                continue
            vals = [str(v) for v in sp[x["source_field"]]["samples"]]
            unique = len(set(vals)) == len(vals) and len(vals) > 1
            idish = any(w in mapping.tokens(x["source_field"]) for w in ("id", "no", "number", "code", "ref", "key", "#"))
            score = (2 if unique else 0) + (1 if idish else 0) + x.get("confidence", 0)
            if unique and (best is None or score > best[0]):
                best = (score, x["source_field"], x["target_field"])
        return {"source": best[1], "target": best[2]} if best else {}

    def save_mapping(self, project_id: str, source_app: str, target_app: str, mappings: list[dict[str, Any]],
                     key_fields: dict[str, str], filter_map: list[dict[str, Any]] | None = None,
                     note: str | None = None) -> dict[str, Any]:
        self.project(project_id)
        clean = []
        for m in mappings:
            st = m.get("status")
            if st not in MAPPING_STATUSES:
                raise QAError("CMP_MAPPING_STATUS", f"Unknown mapping status '{st}'.", status=422)
            # never auto-approve: an automatic mapping keeps its status only above the auto-map threshold
            if st == "auto_mapped" and (float(m.get("confidence") or 0) < mapping.AUTO_MAP or m.get("origin") == "ai"):
                st = "needs_review"
            clean.append({k: m.get(k) for k in ("source_field", "target_field", "type", "confidence", "origin",
                                                 "transformation", "rules", "evidence")} | {"status": st})
        last = self.db.query_one("SELECT MAX(version) AS v FROM cmp_mappings WHERE project_id = ? AND source_app = ? "
                                 "AND target_app = ?", (project_id, source_app, target_app))
        version = (last["v"] or 0) + 1
        self.db.execute("INSERT INTO cmp_mappings (project_id, version, source_app, target_app, mappings, key_fields, "
                        "filter_map, note, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (project_id, version, source_app, target_app, dumps(clean), dumps(key_fields or {}),
                         dumps(filter_map or []), note, _user().username, now_iso()))
        for m in clean:
            if m["status"] in ("approved", "manually_mapped"):
                aura.learn("field_mapping", source_app, f"{m['source_field']}->{target_app}",
                           {"target_field": m["target_field"], "type": m.get("type")}, source="data comparison",
                           approved=True, agent="comparison")
        self.audit("mapping.save", project_id, f"{source_app}->{target_app}", version=version,
                   approved=sum(1 for m in clean if m["status"] in ("approved", "manually_mapped")))
        return self.latest_mapping(project_id, source_app, target_app) or {}

    # ---- aggregate definitions ---------------------------------------------------------------------------------
    def definitions(self, project_id: str) -> list[dict[str, Any]]:
        self.project(project_id)
        rows = self.db.query("SELECT * FROM cmp_aggregate_defs WHERE project_id = ? ORDER BY name", (project_id,))
        for r in rows:
            r["definition"] = loads(r["definition"])
        return rows

    def save_definition(self, project_id: str, definition: dict[str, Any], def_id: str | None = None) -> dict[str, Any]:
        self.project(project_id)
        fn = definition.get("function")
        if fn not in aggregate.FUNCTIONS:
            raise QAError("CMP_FUNCTION", f"Function must be one of {', '.join(aggregate.FUNCTIONS)}.", status=422)
        for cond in definition.get("where") or []:
            if cond.get("op", "eq") not in aggregate.OPS:
                raise QAError("CMP_OPERATOR", f"Unknown operator '{cond.get('op')}'.", status=422)
        at = now_iso()
        definition = {k: v for k, v in definition.items() if k not in ("approved", "approved_by")}
        if def_id:
            cur = self.db.query_one("SELECT * FROM cmp_aggregate_defs WHERE id = ? AND project_id = ?",
                                    (def_id, project_id))
            if cur is None:
                raise QAError("CMP_DEF_NOT_FOUND", "Definition not found.", status=404)
            # a changed definition must be approved again
            self.db.execute("UPDATE cmp_aggregate_defs SET name=?, definition=?, approved=0, approved_by=NULL, "
                            "version=version+1, updated_at=? WHERE id=?",
                            (definition.get("name") or fn, dumps(definition), at, def_id))
        else:
            def_id = _id("AD")
            self.db.execute("INSERT INTO cmp_aggregate_defs (id, project_id, name, definition, created_at, updated_at)"
                            " VALUES (?,?,?,?,?,?)", (def_id, project_id, definition.get("name") or fn,
                                                     dumps(definition), at, at))
        self.audit("definition.save", project_id, def_id, name=definition.get("name"))
        return next(d for d in self.definitions(project_id) if d["id"] == def_id)

    def approve_definition(self, project_id: str, def_id: str, approved: bool = True) -> dict[str, Any]:
        self.project(project_id)
        u = _user()
        if u.view_only:
            raise QAError("CMP_FORBIDDEN", "Your role cannot approve calculation definitions.", status=403)
        self.db.execute("UPDATE cmp_aggregate_defs SET approved=?, approved_by=?, updated_at=? WHERE id=? AND "
                        "project_id=?", (1 if approved else 0, u.username if approved else None, now_iso(), def_id,
                                         project_id))
        self.audit("definition.approve" if approved else "definition.unapprove", project_id, def_id)
        return next(d for d in self.definitions(project_id) if d["id"] == def_id)

    def suggest_definitions(self, source_ds: str, target_ds: str) -> list[dict[str, Any]]:
        s, t = self.dataset(source_ds, True), self.dataset(target_ds)
        types = {f: p["type"] for f, p in mapping.profile(self._columns(s)).items()}
        targets = [{**k, "application": t["application_id"]} for k in (t["aggregates"].get("kpis") or [])]
        return aggregate.suggest_definitions(s["records"], targets, types)

    @staticmethod
    def _resolve_target(d: dict[str, Any], tgt: dict[str, Any]) -> dict[str, Any]:
        """A definition's displayed value: literal, a KPI of the target dataset (by label) or a chart series."""
        ref = d.get("target") or {}
        agg = tgt.get("aggregates") or {}
        if ref.get("kpi"):
            hit = next((k for k in agg.get("kpis") or [] if str(k.get("label", "")).strip().lower()
                        == str(ref["kpi"]).strip().lower()), None)
            if hit:
                return {"value": hit.get("value"), "confidence": hit.get("confidence", 1.0),
                        "method": hit.get("method"), "screenshot": agg.get("screenshot")}
            return {"value": None, "missing": f"KPI '{ref['kpi']}' was not found on the target screen"}
        if ref.get("chart"):
            chart = next((c for c in agg.get("charts") or [] if str(c.get("title") or "").strip().lower()
                          == str(ref["chart"]).strip().lower()), None)
            if chart:
                series = chart.get("series_values") or []
                s = next((x for x in series if ref.get("series") in (None, x.get("name"))), series[0] if series else None)
                if s:
                    return {"values": s["values"], "confidence": chart.get("confidence", 1.0),
                            "method": chart.get("method"), "screenshot": agg.get("screenshot")}
            return {"values": {}, "missing": f"chart '{ref['chart']}' was not found on the target screen"}
        return ref

    # ---- executions --------------------------------------------------------------------------------------------
    def _names(self, s: dict[str, Any], t: dict[str, Any]) -> dict[str, str]:
        an = {r["id"]: r["name"] for r in self.db.query("SELECT id, name FROM cmp_applications WHERE id IN (?,?)",
                                                        (s["application_id"], t["application_id"]))}
        return {"source": an.get(s["application_id"], "Application A"), "target": an.get(t["application_id"],
                                                                                           "Application B")}

    def run(self, project_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        """Run a comparison (record, aggregate or hybrid). Deterministic: the result is decided here, not by AI."""
        proj = self.project(project_id)
        mode = spec.get("mode") or "record"
        if mode not in ("record", "aggregate", "hybrid"):
            raise QAError("CMP_MODE", "Mode must be record, aggregate or hybrid.", status=422)
        s = self.dataset(spec["source_dataset"], True)
        t = self.dataset(spec["target_dataset"], True)
        if s["project_id"] != project_id or t["project_id"] != project_id:
            raise QAError("CMP_PROJECT_MISMATCH", "Both datasets must belong to this project.", status=422)
        if "running" in (s["status"], t["status"]):
            raise QAError("CMP_DATASET_RUNNING", "A dataset is still being extracted.", status=409)
        names = self._names(s, t)
        rules = proj["settings"].get("rules") or {}
        exe, started = _id("CX"), now_iso()
        self.db.execute("INSERT INTO cmp_executions (id, project_id, mode, status, config, created_by, started_at) "
                        "VALUES (?,?,?,?,?,?,?)", (exe, project_id, mode, "running", dumps(spec), _user().username,
                                                   started))
        detail: dict[str, Any] = {"names": names}
        results = []
        if mode in ("record", "hybrid"):
            m = self.latest_mapping(project_id, s["application_id"], t["application_id"])
            if spec.get("mappings") is not None:
                maps, key, fmap = spec["mappings"], spec.get("key_fields") or {}, spec.get("filter_map") or []
            elif m:
                maps, key, fmap = m["mappings"], m["key_fields"], m["filter_map"]
            else:
                raise QAError("CMP_MAPPING_REQUIRED", "Save a field mapping for these applications first.", status=422)
            fmap = [{**f, "source_value": s["filters"].get(f.get("source_filter")) if f.get("source_filter") else None,
                     "target_value": t["filters"].get(f.get("target_filter")) if f.get("target_filter") else None}
                    for f in fmap]
            mapped_s = {f.get("source_filter") for f in fmap}
            mapped_t = {f.get("target_filter") for f in fmap}
            fmap += [{"source_filter": k, "target_filter": None, "source_value": v} for k, v in s["filters"].items()
                     if k not in mapped_s]
            fmap += [{"source_filter": None, "target_filter": k, "target_value": v} for k, v in t["filters"].items()
                     if k not in mapped_t]
            out = reconcile.compare(s, t, maps, key=key, rules=rules, filter_map=fmap, names=names)
            detail.update(out)
            detail["mapping_version"] = (m or {}).get("version")
            results.append(out["result"])
        if mode in ("aggregate", "hybrid"):
            ids = spec.get("definitions")
            defs = [d for d in self.definitions(project_id) if not ids or d["id"] in ids]
            prepared = []
            fmap_pairs = {f["source_filter"]: f["target_filter"] for f in
                          ((self.latest_mapping(project_id, s["application_id"], t["application_id"]) or {}).get("filter_map") or [])
                          if f.get("source_filter") and f.get("target_filter")}
            for d in defs:
                body = dict(d["definition"])
                body.update({"id": d["id"], "approved": bool(d["approved"]), "name": d["name"]})
                body["target"] = self._resolve_target(body, t)
                body.setdefault("filters", {"source": s["filters"], "target": t["filters"], "mapping": fmap_pairs})
                prepared.append(body)
            agg = aggregate.reconcile(s["records"], prepared, rules=rules,
                                      source_complete=bool(s["stats"].get("complete", True)))
            for r in agg["rows"]:
                miss = (r.get("target") or {}).get("missing")
                if miss and r["result"] in ("FAIL", "PASS"):
                    r["result"], r["reason"] = "REVIEW", miss
            detail["aggregates"] = agg
            results.append(agg["result"])
            discs = detail.setdefault("discrepancies", [])
            for r in agg["rows"]:
                if r["result"] in ("FAIL", "REVIEW", "CONFIG_MISMATCH"):
                    cat = {"FAIL": "aggregate_mismatch", "CONFIG_MISMATCH": "filter_mismatch"}.get(r["result"],
                                                                                                 "aggregate_review")
                    discs.append({"id": "D-" + hashlib.sha1(f"agg|{r['metric']}".encode()).hexdigest()[:10].upper(),
                                  "category": cat, "metric": r["metric"], "expected": r.get("expected"),
                                  "displayed": r.get("displayed"), "difference": r.get("difference"),
                                  "note": r.get("reason"), "records_used": r.get("records_used")})
        order = ["INCOMPLETE", "FAIL", "CONFIG_MISMATCH", "REVIEW", "PASS_EQUIVALENT", "PASS"]
        result = min(results, key=lambda r: order.index(r) if r in order else 0) if results else "REVIEW"
        detail["coverage"] = detail.get("coverage") or {"source": s["stats"], "target": t["stats"]}
        execution = {"id": exe, "result": result, "detail": detail}
        if result in ("PASS", "PASS_EQUIVALENT"):
            detail["no_defect"] = investigate.no_defect(self.rt.ai if spec.get("use_ai") else None, execution)
        self.db.execute("UPDATE cmp_executions SET status='done', result=?, summary=?, detail=?, finished_at=? "
                        "WHERE id=?", (result, dumps(detail.get("summary") or detail.get("aggregates", {}).get("counts")),
                                       dumps(detail), now_iso(), exe))
        for dsc in detail.get("discrepancies") or []:
            self.db.execute("INSERT OR IGNORE INTO cmp_discrepancies (id, execution_id, project_id, category, "
                            "record_key, source_field, target_field, data) VALUES (?,?,?,?,?,?,?,?)",
                            (dsc["id"], exe, project_id, dsc["category"], dsc.get("record_key"),
                             dsc.get("source_field"), dsc.get("target_field"), dumps(dsc)))
        self._draft_defects(project_id, exe, detail.get("discrepancies") or [], names)
        self.audit("execution.run", project_id, exe, mode=mode, result=result,
                   discrepancies=len(detail.get("discrepancies") or []))
        return self.execution(exe)

    def execution(self, exe: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM cmp_executions WHERE id = ?", (exe,))
        if row is None:
            raise QAError("CMP_EXECUTION_NOT_FOUND", "Execution not found.", status=404)
        self._project_row(row["project_id"])
        for k in ("config", "summary", "detail"):
            row[k] = loads(row[k])
        return row

    def executions(self, project_id: str) -> list[dict[str, Any]]:
        self.project(project_id)
        rows = self.db.query("SELECT id, mode, status, result, summary, created_by, started_at, finished_at FROM "
                             "cmp_executions WHERE project_id = ? ORDER BY started_at DESC LIMIT 200", (project_id,))
        for r in rows:
            r["summary"] = loads(r["summary"])
        return rows

    def dashboard(self, project_id: str) -> dict[str, Any]:
        """BI view: results over time, discrepancies by category, defects by status."""
        self.project(project_id)
        runs = self.db.query("SELECT result, started_at FROM cmp_executions WHERE project_id = ? ORDER BY started_at",
                             (project_id,))
        cats = self.db.query("SELECT category, COUNT(*) AS n FROM cmp_discrepancies WHERE project_id = ? GROUP BY "
                             "category ORDER BY n DESC", (project_id,))
        defects = self.db.query("SELECT status, COUNT(*) AS n FROM cmp_defects WHERE project_id = ? GROUP BY status",
                                (project_id,))
        by_result: dict[str, int] = {}
        for r in runs:
            by_result[r["result"] or "running"] = by_result.get(r["result"] or "running", 0) + 1
        return {"executions": len(runs), "by_result": by_result, "trend": runs[-50:], "by_category": cats,
                "defects": defects}

    # ---- defect review center ----------------------------------------------------------------------------------
    def _draft_defects(self, project_id: str, exe: str, discs: list[dict[str, Any]], names: dict[str, str]) -> None:
        cfg = loads(self.db.query_one("SELECT config FROM cmp_executions WHERE id = ?", (exe,))["config"]) or {}
        nav = self.db.query("SELECT n.step, n.details, a.name AS application FROM cmp_navigation n JOIN "
                            "cmp_applications a ON a.id = n.application_id WHERE n.session_id IN (?, ?) ORDER BY n.id",
                            (cfg.get("source_dataset"), cfg.get("target_dataset")))
        for n in nav:
            n["details"] = loads(n["details"])
        for dsc in discs:
            if dsc["category"] not in investigate.PRODUCT:
                continue
            title = self._title(dsc, names)
            existing = self.db.query_one("SELECT id FROM cmp_defects WHERE project_id = ? AND title = ? AND status NOT IN"
                                         " ('closed', 'not_a_defect')", (project_id, title))
            if existing:
                self._history(existing["id"], "seen again", {"execution": exe})
                continue
            did, at = _id("CD"), now_iso()
            self.db.execute("INSERT INTO cmp_defects (id, project_id, execution_id, discrepancy_id, status, title, "
                            "severity, priority, data, history, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (did, project_id, exe, dsc["id"], "new", title, investigate.SEVERITY.get(dsc["category"], 3),
                             2 if dsc["category"] in ("missing_record", "currency_mismatch", "aggregate_mismatch") else 3,
                             dumps({**dsc, "repro_steps": investigate.repro_steps(nav, dsc, names), "names": names}),
                             dumps([{"at": at, "by": _user().username, "action": "created", "status": "new"}]), at, at))

    @staticmethod
    def _title(dsc: dict[str, Any], names: dict[str, str]) -> str:
        cat = dsc["category"].replace("_", " ")
        if dsc.get("metric"):
            return f"[Data] {dsc['metric']}: {names['target']} shows {dsc.get('displayed')} but {names['source']} " \
                   f"records give {dsc.get('expected')}"[:250]
        if dsc.get("source_field"):
            return f"[Data] {cat}: {dsc['source_field']} of {dsc.get('record_key')} differs between " \
                   f"{names['source']} and {names['target']}"[:250]
        return f"[Data] {cat}: record {dsc.get('record_key')} ({names['source']} vs {names['target']})"[:250]

    def _history(self, did: str, action: str, extra: dict[str, Any] | None = None) -> None:
        row = self.db.query_one("SELECT history FROM cmp_defects WHERE id = ?", (did,))
        hist = (loads(row["history"]) or []) if row else []
        hist.append({"at": now_iso(), "by": _user().username, "action": action, **(extra or {})})
        self.db.execute("UPDATE cmp_defects SET history = ?, updated_at = ? WHERE id = ?", (dumps(hist[-200:]),
                                                                                           now_iso(), did))

    def defects(self, project_id: str, status: str | None = None) -> list[dict[str, Any]]:
        self.project(project_id)
        sql, params = "SELECT * FROM cmp_defects WHERE project_id = ?", [project_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        rows = self.db.query(sql + " ORDER BY created_at DESC", params)
        for r in rows:
            for k in ("data", "investigation", "comments", "history", "tfs_sync"):
                r[k] = loads(r[k])
        return rows

    def defect(self, did: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM cmp_defects WHERE id = ?", (did,))
        if row is None:
            raise QAError("CMP_DEFECT_NOT_FOUND", "Defect not found.", status=404)
        self._project_row(row["project_id"])
        for k in ("data", "investigation", "comments", "history", "tfs_sync"):
            row[k] = loads(row[k])
        row["repro_steps"] = row["data"].get("repro_steps") or []
        return row

    def update_defect(self, did: str, status: str | None = None, comment: str | None = None,
                      severity: int | None = None, priority: int | None = None, title: str | None = None) -> dict[str, Any]:
        cur = self.defect(did)
        if status and status not in DEFECT_STATUSES:
            raise QAError("CMP_DEFECT_STATUS", "Unknown status.", status=422)
        if status == "created_in_tfs" and not cur.get("tfs_id"):
            raise QAError("CMP_DEFECT_STATUS", "Create the defect in TFS to set this status.", status=422)
        if comment:
            comments = cur["comments"] or []
            comments.append({"at": now_iso(), "by": _user().username, "text": comment[:4000]})
            self.db.execute("UPDATE cmp_defects SET comments = ? WHERE id = ?", (dumps(comments), did))
        self.db.execute("UPDATE cmp_defects SET status = COALESCE(?, status), severity = COALESCE(?, severity), "
                        "priority = COALESCE(?, priority), title = COALESCE(?, title), updated_at = ? WHERE id = ?",
                        (status, severity, priority, title, now_iso(), did))
        if status and status != cur["status"]:
            self._history(did, "status", {"from": cur["status"], "status": status})
            if status == "not_a_defect":
                aura.learn("approved_correction", cur["data"].get("names", {}).get("target"), f"not-a-defect:{did}",
                           {"category": cur["data"].get("category"), "field": cur["data"].get("source_field"),
                            "reason": (comment or "")[:300]}, source="data comparison review", approved=True,
                           agent="comparison")
        self.audit("defect.update", cur["project_id"], did, status=status, commented=bool(comment))
        return self.defect(did)

    def investigate(self, did: str) -> dict[str, Any]:
        cur = self.defect(did)
        exe = self.execution(cur["execution_id"]) if cur.get("execution_id") else {}
        ev = [e["id"] for e in self.evidence_list(cur["project_id"])][:20]
        ai = self.rt.ai if getattr(self.rt, "ai", None) is not None else None
        out = investigate.investigate(ai, cur["data"], exe, ev)
        self.db.execute("UPDATE cmp_defects SET investigation = ?, updated_at = ? WHERE id = ?",
                        (dumps(out), now_iso(), did))
        self._history(did, "investigated", {"origin": out.get("origin")})
        self.audit("defect.investigate", cur["project_id"], did, origin=out.get("origin"))
        return self.defect(did)

    def create_in_tfs(self, did: str, area_path: str | None = None, iteration_path: str | None = None,
                      assigned_to: str | None = None, force: bool = False) -> dict[str, Any]:
        cur = self.defect(did)
        if cur.get("tfs_id"):
            raise QAError("CMP_DEFECT_IN_TFS", f"Already created as #{cur['tfs_id']}.", status=409)
        if cur["status"] not in ("confirmed", "under_review", "new"):
            raise QAError("CMP_DEFECT_STATUS", "Only open defects can be created in TFS.", status=422)
        names = cur["data"].get("names") or {"source": "A", "target": "B"}
        draft = {"title": cur["title"], "repro_steps": "\n".join(cur["repro_steps"]), "facts": {}}
        if not force:
            try:
                from ..defects.service import POSSIBLE_DUPLICATE, similarity

                dups = []
                for bug in self.rt.defects._open_bugs():
                    score, reasons = similarity(draft, bug)
                    if score >= POSSIBLE_DUPLICATE:
                        dups.append({"id": bug["id"], "title": bug["title"], "score": round(score, 2), "reasons": reasons})
                if dups:
                    dups.sort(key=lambda d: -d["score"])
                    self.db.execute("UPDATE cmp_defects SET tfs_sync = ? WHERE id = ?",
                                    (dumps({"duplicates": dups[:5], "at": now_iso()}), did))
                    raise QAError("CMP_POSSIBLE_DUPLICATE", f"A similar open defect exists (#{dups[0]['id']}). "
                                  "Review it, or create anyway.", status=409, details={"duplicates": dups[:5]})
            except QAError as exc:
                if exc.code == "CMP_POSSIBLE_DUPLICATE":
                    raise
                if exc.code in OFFLINE_CODES:
                    return self._queue_tfs(did, exc.message)
                raise
        bug_type = (self.rt.tfs.types_for("bug") or ["Bug"])[0]
        values = {"title": cur["title"], "repro_steps": investigate.bug_html(cur, names),
                  "priority": cur["priority"], "area_path": area_path,
                  "iteration_path": iteration_path, "assigned_to": assigned_to,
                  "tags": "DataComparison; " + str(cur["data"].get("category") or ""),
                  "Microsoft.VSTS.TCM.SystemInfo": f"{branding.name()} Data Comparison Agent; execution "
                                                   f"{cur.get('execution_id')}; {names['source']} vs {names['target']}"}
        try:
            created = self.rt.tfs.create(bug_type, values)
        except QAError as exc:
            if exc.code in OFFLINE_CODES:
                return self._queue_tfs(did, exc.message)
            raise
        tfs_id = created["item"]["id"]
        self.db.execute("UPDATE cmp_defects SET tfs_id = ?, status = 'created_in_tfs', tfs_sync = ?, updated_at = ? "
                        "WHERE id = ?", (tfs_id, dumps({"created_at": now_iso(), "fields": created.get("fields_report")}),
                                         now_iso(), did))
        self._history(did, "created in TFS", {"tfs_id": tfs_id, "status": "created_in_tfs"})
        aura.learn("known_defect", names.get("target"), f"tfs:{tfs_id}", {"title": cur["title"],
                   "category": cur["data"].get("category")}, source="data comparison", agent="comparison")
        self.audit("defect.tfs", cur["project_id"], did, tfs_id=tfs_id)
        return self.defect(did)

    def _queue_tfs(self, did: str, reason: str) -> dict[str, Any]:
        """TFS cannot be reached: kept in the sync queue and sent by ``sync_queue`` later (nothing is lost)."""
        self.db.execute("UPDATE cmp_defects SET tfs_sync = ?, updated_at = ? WHERE id = ?",
                        (dumps({"queued": True, "reason": reason, "at": now_iso()}), now_iso(), did))
        self._history(did, "queued for TFS", {"reason": reason[:200]})
        return {**self.defect(did), "queued": True}

    def sync_queue(self) -> dict[str, Any]:
        done, failed = [], []
        for r in self.db.query("SELECT id FROM cmp_defects WHERE tfs_id IS NULL AND tfs_sync LIKE '%\"queued\": true%'"):
            try:
                out = self.create_in_tfs(r["id"], force=True)
                (done if out.get("tfs_id") else failed).append(r["id"])
            except QAError as exc:
                failed.append({"id": r["id"], "error": exc.message})
        return {"created": done, "failed": failed}

    # ---- reports -----------------------------------------------------------------------------------------------
    def report_html(self, exe: str) -> str:
        e = self.execution(exe)
        p = self.project(e["project_id"])
        defects = [d for d in self.defects(e["project_id"]) if d.get("execution_id") == exe]
        return reports.html_report(p, e, defects=defects, names=(e["detail"] or {}).get("names"), brand=branding.name())

    def report_pdf(self, exe: str) -> Path:
        from ..reports.service import print_pdf

        target = self.rt.paths.reports / "comparison" / f"{exe}.pdf"
        print_pdf(self.report_html(exe), target)
        self.audit("report.pdf", self.execution(exe)["project_id"], exe)
        return target

    def report_xlsx(self, exe: str) -> bytes:
        e = self.execution(exe)
        self.audit("report.xlsx", e["project_id"], exe)
        return reports.excel_report(e, (e["detail"] or {}).get("names") or {})

    def evidence_package(self, exe: str) -> bytes:
        e = self.execution(exe)
        cfg = e["config"] or {}
        ids = [cfg.get("source_dataset"), cfg.get("target_dataset")]
        rows = self.db.query("SELECT DISTINCT ev.* FROM cmp_evidence ev WHERE ev.project_id = ? AND (ev.id IN "
                             "(SELECT json_extract(aggregates, '$.screenshot') FROM cmp_datasets WHERE id IN (?,?)) OR "
                             "ev.application_id IN (SELECT application_id FROM cmp_datasets WHERE id IN (?,?)))",
                             (e["project_id"], *ids, *ids))
        files = []
        for r in rows[:500]:
            try:
                files.append((r["file"], self.evidence.path(e["project_id"], r["file"]).read_bytes()))
            except QAError:
                continue
        audit = [a for a in self.audit_trail(e["project_id"], 2000)]
        self.audit("report.package", e["project_id"], exe, files=len(files))
        return reports.evidence_package(self.report_html(exe), e, files, audit)
