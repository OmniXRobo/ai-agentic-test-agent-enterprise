"""Reporting & Analytics Agent: QA reports as a self-contained HTML file and a PDF.

Every figure in a report comes from recorded runs, TFS (live) or stored drafts.
A section whose source is unavailable says why instead of showing numbers.
The PDF is printed from the same HTML by the bundled Chromium (Playwright), so
it needs no extra software and no Internet.
"""

from __future__ import annotations

import base64
import html
import json
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from ..agents.base import BaseAgent
from ..clock import now, now_iso, parse_iso
from ..config import Paths
from ..design.service import html_to_text, split_criteria
from ..errors import QAError
from ..events import names
from ..events.bus import EventBus
from ..logging_setup import get_logger
from ..storage.db import Database, dumps, loads
from ..tfs.service import TfsService
from . import charts
from .analytics import AnalyticsService, _day

log = get_logger("reports")

MAX_EVIDENCE_IMAGES = 8
MAX_IMAGE_BYTES = 1_500_000
MAX_PERF_RUNS = 5
SCOPES = ("functional", "performance", "both")


class ReportingAgent(BaseAgent):
    agent_id = "reporting"


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _ms(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.0f} ms" if v < 1000 else f"{v / 1000:.2f} s"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:g}%"


def _when(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        return parse_iso(ts).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts


class ReportService:
    def __init__(self, paths: Paths, bus: EventBus, db: Database, tfs: TfsService, automation: Any,
                 performance: Any, defects: Any, design: Any, analytics: AnalyticsService) -> None:
        self.paths = paths
        self.db = db
        self.tfs = tfs
        self.automation = automation
        self.performance = performance
        self.defects = defects
        self.design = design
        self.analytics = analytics
        self.agent = ReportingAgent(bus)
        # a report cannot still be running when the application starts
        db.execute("UPDATE reports SET status = 'failed', finished_at = ?, error = ? WHERE status = 'running'",
                   (now_iso(), "Interrupted because the application stopped while the report was being generated."))

    # ---- storage -------------------------------------------------------------------------------

    def _folder(self, report_id: str) -> Path:
        return self.paths.reports / report_id

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM reports ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._row(r) for r in rows]

    def get(self, report_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM reports WHERE id = ?", (report_id,))
        if not row:
            raise QAError("REPORT_NOT_FOUND", f"Report {report_id} does not exist.", status=404)
        return self._row(row)

    def _row(self, r: dict[str, Any]) -> dict[str, Any]:
        r = dict(r)
        r["params"] = loads(r["params"]) if r.get("params") else {}
        r["summary"] = loads(r["summary"]) if r.get("summary") else {}
        folder = self._folder(r["id"])
        r["has_html"] = (folder / "report.html").exists()
        r["has_pdf"] = (folder / "report.pdf").exists()
        return r

    def file(self, report_id: str, kind: str) -> Path:
        self.get(report_id)
        path = self._folder(report_id) / ("report.pdf" if kind == "pdf" else "report.html")
        if not path.exists():
            raise QAError("REPORT_FILE_MISSING", f"This report has no {kind.upper()} file.", status=404)
        return path

    def delete(self, report_id: str) -> None:
        report = self.get(report_id)
        if report["status"] == "running":
            raise QAError("REPORT_RUNNING", "The report is still being generated.", status=409)
        folder = self._folder(report_id)
        if folder.exists() and folder.resolve().parent == self.paths.reports.resolve():
            shutil.rmtree(folder)
        self.db.execute("DELETE FROM reports WHERE id = ?", (report_id,))

    # ---- generation ----------------------------------------------------------------------------

    def start(self, params: dict[str, Any], *, background: bool = True) -> dict[str, Any]:
        scope = params.get("scope") or "both"
        if scope not in SCOPES:
            raise QAError("VALIDATION_FAILED", "Scope must be functional, performance or both.", status=422)
        report_id = f"RPT-{now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
        title = params.get("title") or {"functional": "Functional QA report", "performance": "Performance QA report",
                                         "both": "QA report"}[scope]
        self.db.execute("INSERT INTO reports (id, title, scope, params, status, created_at) VALUES (?,?,?,?,?,?)",
                        (report_id, title, scope, dumps(params), "running", now_iso()))
        if background:
            threading.Thread(target=self._generate, args=(report_id, title, params), name=report_id, daemon=True).start()
        else:
            self._generate(report_id, title, params)
        return self.get(report_id)

    def _generate(self, report_id: str, title: str, params: dict[str, Any]) -> None:
        folder = self._folder(report_id)
        try:
            with self.agent.activity(f"Generating report: {title}", run_id=report_id,
                                     user_story_id=str(params["user_story_id"]) if params.get("user_story_id") else None) as act:
                act.event(names.REPORT_STARTED, f"Collecting results for {title}")
                data = self.collect(params)
                act.progress(60, "Rendering the HTML report")
                page = render(title, data)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "report.html").write_text(page, encoding="utf-8")
                act.progress(80, "Printing the PDF")
                pdf_error = None
                try:
                    print_pdf(page, folder / "report.pdf")
                except Exception as exc:  # the HTML report is still delivered
                    pdf_error = f"PDF not created: {exc}"
                    log.info(pdf_error, extra={"run_id": report_id})
                summary = data["summary"] | ({"pdf_error": pdf_error} if pdf_error else {})
                self.db.execute("UPDATE reports SET status = ?, finished_at = ?, summary = ? WHERE id = ?",
                                ("ready", now_iso(), dumps(summary), report_id))
                act.event(names.REPORT_COMPLETED, title, link=f"#/reports?id={report_id}")  # notification reads "Report ready: <title>"
                act.complete(f"Report ready: {title}")
        except Exception as exc:
            message = exc.message if isinstance(exc, QAError) else f"{type(exc).__name__}: {exc}"
            self.db.execute("UPDATE reports SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                            ("failed", now_iso(), message, report_id))
            self.agent.emit(names.REPORT_FAILED, f"{title}: {message}", run_id=report_id)

    # ---- data ----------------------------------------------------------------------------------

    def collect(self, params: dict[str, Any]) -> dict[str, Any]:
        days = int(params.get("days") or 30)
        scope = params.get("scope") or "both"
        data: dict[str, Any] = {"generated_at": now_iso(), "days": days, "scope": scope,
                                "project": self.tfs.profile().get("project") or "", "summary": {}}
        if scope in ("functional", "both"):
            data["functional"] = self._functional(days, params.get("user_story_id"))
            f = data["functional"]
            data["summary"].update({"executions": f["analytics"]["executions"], "pass_rate": f["analytics"]["pass_rate"],
                                    "failed": f["analytics"]["failed"]})
        if scope in ("performance", "both"):
            data["performance"] = self._performance(days, params.get("perf_run_ids"))
            p = data["performance"]
            data["summary"].update({"perf_runs": len(p["runs"]), "sla_pass": p["sla"]["pass"], "sla_fail": p["sla"]["fail"]})
        return data

    def _since(self, days: int):
        from datetime import timedelta
        return now().date() - timedelta(days=max(1, days) - 1)

    def _functional(self, days: int, story_id: int | None) -> dict[str, Any]:
        start = self._since(days)
        analytics = self.analytics.functional(days)
        out: dict[str, Any] = {"analytics": analytics, "story": None, "story_error": None}
        scripts = {s["id"]: s for s in self.automation.scripts.list()}
        story_tcs: set[str] | None = None
        if story_id:
            try:
                item = self.tfs.detail(int(story_id))
                criteria = split_criteria(html_to_text(item.get("acceptance_criteria")))
                criteria += [c["title"] for c in item["traceability"]["acceptance_criteria"] if c["title"] not in criteria]
                tcs = item["traceability"]["test_cases"]
                story_tcs = {str(t["id"]) for t in tcs}
                out["story"] = {"id": item["id"], "title": item["title"], "state": item["state"], "type": item["type"],
                                "description": html_to_text(item.get("description")), "criteria": criteria,
                                "test_cases": [{"id": t["id"], "title": t["title"], "state": t.get("state")} for t in tcs]}
            except QAError as exc:
                out["story_error"] = exc.message

        rows = self.db.query("SELECT * FROM execution_results WHERE status IN ('PASSED','FAILED') ORDER BY started_at DESC"
                             " LIMIT 5000")
        rows = [r for r in rows if (_day(r["started_at"]) or start) >= start]
        if story_tcs is not None:
            rows = [r for r in rows if str(r.get("test_case_id") or "") in story_tcs]
        latest: dict[str, dict[str, Any]] = {}
        for r in rows:
            latest.setdefault(r["script_id"], r)
        in_scope = [s for s in scripts.values() if story_tcs is None or str(s.get("test_case_id") or "") in story_tcs]
        results = []
        for s in in_scope:
            r = latest.get(s["id"])
            results.append({"script": s["name"], "script_id": s["id"], "test_case_id": s.get("test_case_id"),
                            "status": r["status"] if r else "NOT RUN", "run_id": r["run_id"] if r else None,
                            "started_at": r["started_at"] if r else None,
                            "failed_step": (r["failed_step"] + 1) if r and r["failed_step"] is not None else None,
                            "error": r["error"] if r else None})
        results.sort(key=lambda x: ({"FAILED": 0, "NOT RUN": 1, "PASSED": 2}[x["status"]], x["script"].lower()))
        out["results"] = results
        out["counts"] = {k: sum(r["status"] == k for r in results) for k in ("PASSED", "FAILED", "NOT RUN")}

        if out["story"]:
            automated = {str(s.get("test_case_id")) for s in in_scope}
            for t in out["story"]["test_cases"]:
                t["automated"] = str(t["id"]) in automated
                t["last"] = next((r["status"] for r in results if str(r["test_case_id"]) == str(t["id"])), None)
                t["datasets"] = len(self.design.list_test_data(tfs_test_case_id=t["id"]))
        out["test_data"] = self.db.query_one("SELECT COUNT(*) AS n FROM test_data" + (" WHERE user_story_id = ?" if story_id else ""),
                                             (str(story_id),) if story_id else ())["n"]

        drafts = [d for d in self.defects.list(source="functional", limit=500) if (_day(d["created_at"]) or start) >= start]
        if story_tcs is not None:
            drafts = [d for d in drafts if str(d.get("test_case_id") or "") in story_tcs]
        out["defects"] = drafts

        heal = self.db.query("SELECT * FROM healing_events ORDER BY id DESC LIMIT 500")
        heal = [h for h in heal if (_day(h["timestamp"]) or start) >= start and h["script_id"] in {s["id"] for s in in_scope}]
        for h in heal:
            h["script"] = scripts.get(h["script_id"], {}).get("name", h["script_id"])
        out["healing"] = heal

        evidence = []
        for r in latest.values():
            if r["status"] != "FAILED" or len(evidence) >= MAX_EVIDENCE_IMAGES:
                continue
            shots = (loads(r["evidence"]) or {}).get("screenshots", []) if r.get("evidence") else []
            image = _data_uri(Path(shots[-1]), self.paths.evidence) if shots else None
            evidence.append({"script": scripts.get(r["script_id"], {}).get("name", r["script_id"]), "run_id": r["run_id"],
                             "error": r["error"], "image": image})
        out["evidence"] = evidence
        return out

    def _performance(self, days: int, run_ids: list[str] | None) -> dict[str, Any]:
        start = self._since(days)
        analytics = self.analytics.performance(days)
        if run_ids:
            chosen = list(run_ids)[:MAX_PERF_RUNS]
        else:  # the latest finished run of each plan in the period
            seen: dict[str, str] = {}
            for r in reversed(analytics["runs"]):
                seen.setdefault(r["plan_id"], r["run_id"])
            chosen = list(seen.values())[:MAX_PERF_RUNS]
        runs = []
        for run_id in chosen:
            run = self.performance.get_run(run_id)
            if not run.get("metrics"):
                continue
            plan_file = Path(run["folder"]) / "plan.json"
            plan = json.loads(plan_file.read_text(encoding="utf-8")) if plan_file.exists() else None
            drafts = [d for d in self.defects.list(source="performance", limit=500) if run_id in json.dumps(d.get("occurrences"))
                      or d.get("source_run_id") == run_id]
            runs.append({"run": run, "plan": plan, "defects": drafts})
        return {"analytics": analytics, "runs": runs, "sla": analytics["sla"], "from": start.isoformat()}


def _data_uri(path: Path, root: Path) -> str | None:
    try:
        resolved = path.resolve()
        if root.resolve() not in resolved.parents or not resolved.is_file() or resolved.stat().st_size > MAX_IMAGE_BYTES:
            return None
        return "data:image/png;base64," + base64.b64encode(resolved.read_bytes()).decode()
    except OSError:
        return None


def print_pdf(page_html: str, target: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(page_html, wait_until="load")
            page.pdf(path=str(target), format="A4", print_background=True,
                     margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"},
                     display_header_footer=True, header_template="<span></span>",
                     footer_template='<div style="font-size:8px;width:100%;text-align:center;color:#667">'
                                     '<span class="pageNumber"></span> / <span class="totalPages"></span></div>')
        finally:
            browser.close()


# ---- HTML ------------------------------------------------------------------------------------------

CSS = """
:root { --text:#1b2433; --muted:#5b6780; --border:#dfe4ee; --surface:#fff; --surface-2:#f4f6fa; --accent:#2563eb;
  --pass:#15803d; --pass-soft:#dcfce7; --fail:#b91c1c; --fail-soft:#fee2e2; --warn:#b45309; --warn-soft:#fef3c7; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif; color: var(--text); margin: 0;
  background: var(--surface); font-size: 13px; line-height: 1.45; }
main { max-width: 1040px; margin: 0 auto; padding: 24px; }
header.cover { border-bottom: 3px solid var(--accent); padding-bottom: 14px; margin-bottom: 18px; }
header.cover h1 { margin: 0 0 4px; font-size: 24px; }
.muted { color: var(--muted); }
h2 { font-size: 18px; margin: 26px 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--border); break-after: avoid; }
h3 { font-size: 14px; margin: 18px 0 8px; break-after: avoid; }
.kpis { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
.kpi { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; background: var(--surface-2); break-inside: avoid; }
.kpi .l { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
.kpi .v { font-size: 20px; font-weight: 650; margin-top: 2px; }
.kpi .n { font-size: 11px; color: var(--muted); }
table { width: 100%; border-collapse: collapse; margin: 6px 0 10px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .03em; background: var(--surface-2); }
tr { break-inside: avoid; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; white-space: nowrap; }
.PASSED, .PASS { background: var(--pass-soft); color: var(--pass); }
.FAILED, .FAIL { background: var(--fail-soft); color: var(--fail); }
.NOT.RUN, .NOT_DEFINED, .WAITING { background: var(--surface-2); color: var(--muted); border: 1px solid var(--border); }
.WARN { background: var(--warn-soft); color: var(--warn); }
.note { border-left: 3px solid var(--warn); background: var(--warn-soft); padding: 8px 10px; border-radius: 6px; }
.charts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.chart { border: 1px solid var(--border); border-radius: 10px; padding: 8px 10px; break-inside: avoid; }
.chart-title { font-size: 12px; font-weight: 600; margin-bottom: 4px; }
.chart svg { width: 100%; height: auto; display: block; }
.chart .grid { stroke: var(--border); stroke-width: 1; }
.chart .axis { fill: var(--muted); font-size: 10px; }
.legend { display: flex; gap: 14px; font-size: 11px; color: var(--muted); margin-top: 4px; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
.shots { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.shot { border: 1px solid var(--border); border-radius: 10px; padding: 8px; break-inside: avoid; }
.shot img { width: 100%; border-radius: 6px; border: 1px solid var(--border); }
section.run { break-before: page; }
section.run:first-of-type { break-before: auto; }
.code { font-family: Consolas, "Cascadia Mono", monospace; font-size: 11px; overflow-wrap: anywhere; }
@media (max-width: 700px) { main { padding: 16px; } .charts, .shots { grid-template-columns: 1fr; } }
@media print { main { padding: 0; max-width: none; } a { color: inherit; text-decoration: none; } }
"""


def kpi(label: str, value: Any, note: str = "") -> str:
    return f'<div class="kpi"><div class="l">{e(label)}</div><div class="v">{e(value)}</div><div class="n">{e(note)}</div></div>'


def pill(status: str | None) -> str:
    s = status or "—"
    return f'<span class="pill {e(s)}">{e(s.replace("_", " "))}</span>'


def render(title: str, data: dict[str, Any]) -> str:
    parts = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(title)}</title><style>{CSS}</style></head><body><main>
<header class="cover"><h1>{e(title)}</h1>
<div class="muted">AI Agentic Test Agent Enterprise{' · ' + e(data['project']) if data.get('project') else ''} ·
generated {e(_when(data['generated_at']))} · period: last {data['days']} day{'s' if data['days'] != 1 else ''}</div></header>"""]
    if "functional" in data:
        parts.append(_functional_html(data["functional"]))
    if "performance" in data:
        parts.append(_performance_html(data["performance"]))
    parts.append('<p class="muted" style="margin-top:28px">All figures come from recorded runs and the connected TFS project. '
                 "Root causes are stated only where the evidence shows them.</p></main></body></html>")
    return "".join(parts)


def _functional_html(f: dict[str, Any]) -> str:
    a, cov, c = f["analytics"], f["analytics"]["coverage"], f["counts"]
    scope = f" (user story {f['story']['id']})" if f["story"] else ""
    out = ['<h2>Functional testing</h2><h3>Executive summary</h3><div class="kpis">',
           kpi("Executions", a["executions"], f"{a['from']} to {a['to']}"),
           kpi("Pass rate", _pct(a["pass_rate"]), f"{a['passed']} passed, {a['failed']} failed"),
           kpi("Passed", c["PASSED"], f"latest result per test{scope}"), kpi("Failed", c["FAILED"], f"latest result per test{scope}"),
           kpi("Not run", c["NOT RUN"], f"automated, no run in period{scope}"),
           kpi("Automation coverage", _pct(cov.get("automation_coverage")),
               f"{cov['automated_test_cases']} of {cov['tfs_test_cases']} TFS test cases" if "tfs_test_cases" in cov else "TFS not reachable"),
           kpi("Not automated", cov.get("not_automated", "—"), "TFS test cases without a script"),
           kpi("Defect drafts", len(f["defects"]), f"{sum(1 for d in f['defects'] if d.get('tfs_id'))} in TFS"),
           kpi("Self-healing events", len(f["healing"]), f"{sum(1 for h in f['healing'] if h['result'] in ('healed', 'validated'))} recovered"),
           kpi("Test data sets", f["test_data"]), "</div>"]
    if not cov["tfs_connected"]:
        out.append(f'<p class="note">Coverage against TFS is not shown: {e(cov.get("tfs_reason"))}</p>')
    trend, defects = a["trend"], a["defects"]["trend"]
    if any(t["passed"] or t["failed"] for t in trend) or any(d["functional"] or d["performance"] for d in defects):
        out.append('<h3>Trends</h3><div class="charts"><div class="chart"><div class="chart-title">Executions per day</div>'
                   + charts.stacked_bars(trend, [("passed", "Passed", "#16a34a"), ("failed", "Failed", "#dc2626")])
                   + '</div><div class="chart"><div class="chart-title">Defects drafted per day</div>'
                   + charts.stacked_bars(defects, [("functional", "Functional", "#2563eb"), ("performance", "Performance", "#db2777")])
                   + "</div></div>")
    if f["story_error"]:
        out.append(f'<h3>User story</h3><p class="note">The user story could not be read from TFS: {e(f["story_error"])}</p>')
    if f["story"]:
        s = f["story"]
        out.append(f'<h3>User story {e(s["id"])}: {e(s["title"])}</h3><p class="muted">{e(s["type"])} · {e(s["state"])}</p>')
        if s["description"] and s["description"] != "(none)":
            out.append(f"<p>{e(s['description'][:1200])}</p>")
        out.append("<h3>Acceptance criteria</h3>" + ("<ol>" + "".join(f"<li>{e(x)}</li>" for x in s["criteria"]) + "</ol>"
                                                     if s["criteria"] else '<p class="muted">None recorded in TFS.</p>'))
        out.append("<h3>Test cases</h3>")
        if s["test_cases"]:
            out.append("<table><tr><th>ID</th><th>Title</th><th>State</th><th>Automated</th><th class='num'>Test data</th><th>Last result</th></tr>"
                       + "".join(f"<tr><td>{e(t['id'])}</td><td>{e(t['title'])}</td><td>{e(t['state'])}</td>"
                                 f"<td>{'Yes' if t['automated'] else 'No'}</td><td class='num'>{t['datasets']}</td>"
                                 f"<td>{pill(t['last']) if t['last'] else '—'}</td></tr>" for t in s["test_cases"]) + "</table>")
        else:
            out.append('<p class="muted">No test cases are linked to this user story in TFS.</p>')
    out.append("<h3>Execution results</h3>")
    if f["results"]:
        out.append("<table><tr><th>Automated test</th><th>Test case</th><th>Result</th><th>Run</th><th>When</th><th>Failure</th></tr>"
                   + "".join(f"<tr><td>{e(r['script'])}</td><td>{e(r['test_case_id'] or '—')}</td><td>{pill(r['status'])}</td>"
                             f"<td class='code'>{e(r['run_id'] or '—')}</td><td>{e(_when(r['started_at']))}</td>"
                             f"<td>{('Step ' + str(r['failed_step']) + ': ') if r['failed_step'] else ''}{e(r['error'] or '')}</td></tr>"
                             for r in f["results"]) + "</table>")
    else:
        out.append('<p class="muted">There are no automated tests in this scope.</p>')
    out.append("<h3>Defects</h3>")
    if f["defects"]:
        out.append("<table><tr><th>Draft</th><th>Title</th><th>Category</th><th>Severity</th><th>Status</th><th>TFS</th><th class='num'>Seen</th></tr>"
                   + "".join(f"<tr><td>#{d['id']}</td><td>{e(d['title'])}</td><td>{e(d['category'].replace('_', ' '))}</td>"
                             f"<td>{e(d['severity'])}</td><td>{e(d['status'].replace('_', ' '))}</td><td>{e(d.get('tfs_id') or '—')}</td>"
                             f"<td class='num'>{d['occurrences']}</td></tr>" for d in f["defects"]) + "</table>")
    else:
        out.append('<p class="muted">No functional defects were drafted in this period.</p>')
    out.append("<h3>Self-healing</h3>")
    if f["healing"]:
        out.append("<table><tr><th>When</th><th>Test</th><th class='num'>Step</th><th>Original locator</th><th>Healed locator</th>"
                   "<th class='num'>Confidence</th><th>Result</th><th>Applied</th></tr>"
                   + "".join(f"<tr><td>{e(_when(h['timestamp']))}</td><td>{e(h['script'])}</td><td class='num'>{h['step_index'] + 1}</td>"
                             f"<td class='code'>{e(h['original_selector'])}</td><td class='code'>{e(h['healed_selector'] or '—')}</td>"
                             f"<td class='num'>{'' if h['confidence'] is None else format(h['confidence'] * 100, '.0f') + '%'}</td>"
                             f"<td>{e(h['result'])}</td><td>{'Yes' if h['applied'] else 'No'}</td></tr>" for h in f["healing"]) + "</table>")
    else:
        out.append('<p class="muted">No self-healing was needed in this period.</p>')
    if f["evidence"]:
        out.append('<h3>Evidence of failures</h3><div class="shots">' + "".join(
            f'<div class="shot"><strong>{e(x["script"])}</strong><div class="muted code">{e(x["run_id"])}</div>'
            f'<p>{e(x["error"] or "")}</p>' + (f'<img src="{x["image"]}" alt="Screenshot of the failure">' if x["image"]
                                              else '<p class="muted">No screenshot was recorded.</p>') + "</div>"
            for x in f["evidence"]) + "</div>")
    return "".join(out)


def _performance_html(p: dict[str, Any]) -> str:
    a = p["analytics"]
    out = ['<h2>Performance testing</h2><h3>Performance test summary</h3><div class="kpis">',
           kpi("Runs", len(a["runs"]), f"since {p['from']}"),
           kpi("SLA passed", a["sla"]["pass"]), kpi("SLA failed", a["sla"]["fail"]),
           kpi("SLA pass rate", _pct(a["sla"]["pass_rate"])),
           kpi("Max tested concurrency", a["max_users"] or "—", "virtual users"), "</div>"]
    if len(a["runs"]) > 1:
        out.append('<div class="charts">'
                   + charts.run_chart(a["runs"], "p95_ms", "95th percentile per run (ms)", "#2563eb")
                   + charts.run_chart(a["runs"], "error_pct", "Error rate per run (%)", "#dc2626") + "</div>")
    if not p["runs"]:
        out.append('<p class="muted">No performance runs with results in this period.</p>')
    for item in p["runs"]:
        run, plan = item["run"], item["plan"] or {}
        m, sla = run["metrics"], run.get("sla") or {}
        o, prof = m["overall"], plan.get("profile") or {}
        out.append(f'<section class="run"><h3>{e(run["kind"].title())} test: {e(run["name"])} {pill(sla.get("status") or run["status"])}</h3>'
                   f'<p class="muted code">{e(run["run_id"])} · {e(_when(run["started_at"]))}</p>')
        out.append("<h3>Load profile</h3><table><tr><th>Users</th><th>Ramp-up</th><th>Duration</th><th>Think time</th>"
                   "<th>Peak active users</th><th>Requests</th></tr>"
                   f"<tr><td>{e(prof.get('users', '—'))}</td><td>{e(prof.get('ramp_up_s', '—'))} s</td><td>{e(prof.get('duration_s', '—'))} s</td>"
                   f"<td>{e(prof.get('think_time_ms', '—'))} ms</td><td>{e(m.get('peak_threads'))}</td><td>{len(plan.get('requests') or [])}</td></tr></table>")
        out.append('<div class="kpis">' + kpi("Samples", f"{o['samples']:,}", f"{o['errors']:,} errors") + kpi("Average", _ms(o["avg_ms"]))
                   + kpi("P90", _ms(o["p90_ms"])) + kpi("P95", _ms(o["p95_ms"])) + kpi("P99", _ms(o["p99_ms"]))
                   + kpi("Throughput", f"{o['throughput']:.1f} req/s") + kpi("Error rate", _pct(o["error_pct"])) + "</div>")
        if m.get("timeline"):
            out.append('<h3>Graphs</h3><div class="charts">'
                       + charts.timeline_chart(m["timeline"], "avg_ms", "Average response time (ms)", "#2563eb")
                       + charts.timeline_chart(m["timeline"], "throughput", "Throughput (requests per second)", "#0d9488")
                       + charts.timeline_chart(m["timeline"], "errors", "Errors per interval", "#dc2626")
                       + charts.timeline_chart(m["timeline"], "threads", "Active users", "#7c3aed") + "</div>")
        out.append("<h3>Transaction results</h3><table><tr><th>Request</th><th class='num'>Samples</th><th class='num'>Error %</th>"
                   "<th class='num'>Avg</th><th class='num'>P90</th><th class='num'>P95</th><th class='num'>P99</th><th class='num'>Req/s</th></tr>"
                   + "".join(f"<tr><td>{e(r['label'])}</td><td class='num'>{r['samples']:,}</td><td class='num'>{r['error_pct']}</td>"
                             f"<td class='num'>{_ms(r['avg_ms'])}</td><td class='num'>{_ms(r['p90_ms'])}</td><td class='num'>{_ms(r['p95_ms'])}</td>"
                             f"<td class='num'>{_ms(r['p99_ms'])}</td><td class='num'>{r['throughput']}</td></tr>" for r in m["requests"])
                   + "</table>")
        out.append("<h3>SLA compliance</h3>")
        if sla.get("checks"):
            out.append("<table><tr><th>Check</th><th>Scope</th><th class='num'>Limit</th><th class='num'>Actual</th><th>Result</th></tr>"
                       + "".join(f"<tr><td>{e(c['name'])}</td><td>{e(c['scope'])}</td><td class='num'>{'≥' if c['name'] == 'Throughput' else '≤'} "
                                 f"{e(c['limit'])} {e(c['unit'])}</td><td class='num'>{e(c['actual'])} {e(c['unit'])}</td><td>{pill(c['status'])}</td></tr>"
                                 for c in sla["checks"]) + "</table>")
        else:
            out.append('<p class="muted">No SLA limits were set on this plan.</p>')
        out.append("<h3>Performance defects</h3>")
        if item["defects"]:
            out.append("<table><tr><th>Draft</th><th>Title</th><th>Status</th><th>TFS</th></tr>"
                       + "".join(f"<tr><td>#{d['id']}</td><td>{e(d['title'])}</td><td>{e(d['status'].replace('_', ' '))}</td>"
                                 f"<td>{e(d.get('tfs_id') or '—')}</td></tr>" for d in item["defects"]) + "</table>")
        else:
            out.append('<p class="muted">No performance defect was drafted for this run.</p>')
        an = run.get("analysis")
        out.append("<h3>AI analysis</h3>")
        if an:
            out.append(f"<p>{e(an.get('summary'))}</p>")
            for key, label in (("observations", "Observations"), ("suspected_bottlenecks", "Suspected bottlenecks"),
                               ("recommendations", "Recommendations"), ("undetermined", "Not determined from these results")):
                if an.get(key):
                    out.append(f"<p><strong>{label}</strong></p><ul>" + "".join(f"<li>{e(x)}</li>" for x in an[key]) + "</ul>")
            out.append('<p class="muted">Written by the On-Premise AI from the measured values above.</p>')
        else:
            out.append('<p class="muted">No AI analysis was requested for this run.</p>')
        out.append("</section>")
    return "".join(out)
