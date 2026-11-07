"""23.3.31: AI analysis reports with a PDF and an Outlook email.

Three subjects, one flow: the On-Premise AI writes an analysis, the tester reads it, edits it or asks again, and when
satisfied makes a PDF, downloads it, or emails it with a subject and body written from the analysis (the PDF is
attached). Nothing is sent without the tester pressing Send.

* ``run``: one functional run (AI-Driven Automation and Guided Browser Automation > Recent Runs). The AI is given the
  steps, their results, times, errors and self-healing, and the script's earlier results. The PDF shows every step
  with the screenshot taken after it.
* ``perf``: one performance run (Performance Results). The analysis is the run's existing AI performance analysis.
* ``dashboard``: the Dashboard figures (TFS, coverage, pass / fail, test data, defects, performance).

Files are kept under ``<data>/reports/ai/<kind>/<id>/``: ``analysis.json`` (run and dashboard), ``report.pdf`` and
``report.html``. Everything works offline: the local AI writes the analysis and the bundled Chromium prints the PDF.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .. import branding
from ..clock import now, now_iso, parse_iso
from ..errors import QAError
from ..logging_setup import get_logger
from .service import CSS, _data_uri, e, kpi, pill, print_pdf

log = get_logger("ai_reports")

KINDS = ("run", "perf", "dashboard")
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
MAX_STEP_IMAGES = 80
LIST_KEYS = ("observations", "issues", "recommendations", "undetermined")


class AiAnalysis(BaseModel):
    summary: str = Field(description="Two to four sentences: what was tested and the outcome")
    observations: list[str] = Field(description="Facts from the data, each citing the values that support it")
    issues: list[str] = Field(default=[], description="Failures, risks or weak spots, each with its evidence")
    recommendations: list[str] = []
    undetermined: list[str] = Field(default=[], description="Questions this data cannot answer")


RUN_PROMPT = (
    "You are a senior QA test engineer reviewing one automated browser test run (Playwright). Use only the data "
    "provided. Summarise what the test did and its result. Observations must cite step numbers, results and times. "
    "For a failed step, say what failed and quote the error; give a likely cause only when the error or the steps "
    "show it, otherwise put the question under 'undetermined'. Mention self-healed locators and slow steps (much "
    "slower than the others). Use the earlier results of the same script to say whether it is stable or flaky. "
    "Recommendations must be specific to this script. Never invent steps, pages or numbers.")

DASHBOARD_PROMPT = (
    "You are a QA manager reviewing a QA dashboard. Use only the figures provided; a missing figure means it is not "
    "available, never zero. Summarise the state of quality and test progress. Observations must cite the figures "
    "(counts and percentages). Issues are gaps and risks: user stories without acceptance criteria, test cases not "
    "automated or without data, failing tests, open defects, performance SLA failures. Recommendations must be "
    "concrete next steps in priority order. Never invent numbers.")


def _when(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        return parse_iso(ts).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts


def _ms(v: Any) -> str:
    if v is None:
        return "—"
    v = float(v)
    return f"{v:.0f} ms" if v < 1000 else f"{v / 1000:.2f} s"


def _lines(text: str) -> str:
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in re.split(r"\n\s*\n", text.strip()) if p.strip())


class AiReportService:
    def __init__(self, paths: Any, ai: Any, automation: Any, performance: Any, analytics: Any, mailer: Any,
                 agent: Any = None) -> None:
        self.paths = paths
        self.ai = ai
        self.automation = automation
        self.performance = performance
        self.analytics = analytics
        self.mailer = mailer
        self.agent = agent

    # ---- storage -------------------------------------------------------------------------------

    def _check(self, kind: str, item_id: str) -> None:
        if kind not in KINDS:
            raise QAError("VALIDATION_FAILED", "The report kind must be run, perf or dashboard.", status=422)
        if not ID_RE.match(item_id or ""):
            raise QAError("VALIDATION_FAILED", "That id is not valid.", status=422)

    def folder(self, kind: str, item_id: str) -> Path:
        self._check(kind, item_id)
        return self.paths.reports / "ai" / kind / item_id

    def _load(self, kind: str, item_id: str) -> dict[str, Any] | None:
        if kind == "perf":
            run = self.performance.get_run(item_id)
            return run["analysis"]
        path = self.folder(kind, item_id) / "analysis.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _store(self, kind: str, item_id: str, analysis: dict[str, Any]) -> None:
        if kind == "perf":
            from ..storage.db import dumps
            self.performance.db.execute("UPDATE perf_runs SET analysis = ? WHERE run_id = ?", (dumps(analysis), item_id))
            return
        folder = self.folder(kind, item_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "analysis.json").write_text(json.dumps(analysis, indent=1, ensure_ascii=False), encoding="utf-8")

    def get(self, kind: str, item_id: str) -> dict[str, Any]:
        self._check(kind, item_id)
        if kind == "run":
            self._run(item_id)  # 404 for an unknown run
        analysis = self._load(kind, item_id)
        folder = self.folder(kind, item_id)
        pdf = folder / "report.pdf"
        made = pdf.stat().st_mtime if pdf.exists() else None
        try:  # the analysis the PDF was made from; an edit or a new analysis after it makes the PDF out of date
            made_from = json.loads((folder / "pdf.json").read_text(encoding="utf-8")).get("stamp")
        except (OSError, ValueError):
            made_from = None
        stale = bool(made and made_from != self._stamp(analysis))
        return {"kind": kind, "id": item_id, "analysis": analysis, "has_pdf": pdf.exists() and not stale,
                "pdf_made_at": now().fromtimestamp(made).astimezone().isoformat(timespec="seconds") if made else None,
                "email_configured": bool(self.mailer and self.mailer.configured())}

    @staticmethod
    def _stamp(analysis: dict[str, Any] | None) -> str | None:
        if not analysis:
            return None
        return f"{analysis.get('created_at')}|{analysis.get('updated_at')}"

    # ---- the AI analysis -----------------------------------------------------------------------

    def _run(self, run_id: str) -> dict[str, Any]:
        rows = self.automation.runner.results(run_id=run_id, limit=1)
        if not rows:
            raise QAError("RUN_NOT_FOUND", f"Run {run_id} has no recorded result.", status=404)
        return rows[0]

    def _script_name(self, script_id: str | None) -> str | None:
        if not script_id:
            return None
        try:
            return self.automation.scripts.get(script_id).name
        except Exception:
            return None

    def run_facts(self, run_id: str) -> dict[str, Any]:
        r = self._run(run_id)
        history = self.automation.runner.results(script_id=r["script_id"], limit=11) if r.get("script_id") else []
        steps = [{"step": s.get("index", i) + 1, "description": s.get("description") or s.get("action"),
                  "action": s.get("action"), "result": s.get("status"), "time_ms": s.get("duration_ms"),
                  **({"error": s["error"]} if s.get("error") else {}),
                  **({"self_healed": s["healed"]} if s.get("healed") else {})} for i, s in enumerate(r["steps"] or [])]
        return {
            "script": self._script_name(r.get("script_id")) or r.get("script_id"), "run_id": run_id,
            "result": r["status"], "browser": r.get("browser"), "start_url": r.get("base_url"),
            "test_case_id": r.get("test_case_id"), "started": r.get("started_at"), "finished": r.get("finished_at"),
            "failed_step": r["failed_step"] + 1 if r.get("failed_step") is not None else None, "error": r.get("error"),
            "steps": steps,
            "total_time_ms": sum(int(s.get("duration_ms") or 0) for s in r["steps"] or []),
            "earlier_results_of_this_script": [{"run_id": h["run_id"], "result": h["status"], "started": h["started_at"],
                                                "failed_step": h["failed_step"] + 1 if h.get("failed_step") is not None else None}
                                               for h in history if h["run_id"] != run_id][:10],
        }

    def dashboard_facts(self) -> dict[str, Any]:
        a = self.analytics.summary()
        tfs = a.get("tfs") or {}
        ins = a.get("insights") or {}
        return {
            "tfs": {"connected": tfs.get("connected"), "project": tfs.get("project"),
                    "user_stories": tfs.get("requirement"), "test_cases": tfs.get("testcase"),
                    "open_defects": tfs.get("open_defects"), "reason": None if tfs.get("connected") else tfs.get("reason")},
            "acceptance_criteria": a.get("acceptance_criteria"),
            "automation_coverage": a.get("coverage"),
            "test_data_rows": a.get("test_data"),
            "test_data": {k: v for k, v in (ins.get("test_data") or {}).items() if k != "top"},
            "functional_latest_results": a.get("functional"),
            "executions": ins.get("executions"),
            "defects": ins.get("defects"),
            "defect_drafts_to_review": a.get("open_defect_drafts"),
            "performance": a.get("performance"),
        }

    def analyze(self, kind: str, item_id: str) -> dict[str, Any]:
        self._check(kind, item_id)
        if kind == "perf":
            self.performance.analyze(item_id)
            return self.get(kind, item_id)
        facts = self.run_facts(item_id) if kind == "run" else self.dashboard_facts()
        prompt = RUN_PROMPT if kind == "run" else DASHBOARD_PROMPT
        label = f"Analyzing run {item_id} with the On-Premise AI" if kind == "run" else "Analyzing the dashboard with the On-Premise AI"

        def call() -> AiAnalysis:
            return self.ai.complete_json(prompt, json.dumps(facts, ensure_ascii=False, default=str), AiAnalysis,
                                         max_tokens=2500)

        if self.agent is not None:
            with self.agent.activity(label, run_id=item_id if kind == "run" else None) as act:
                result = call()
                act.complete("AI analysis ready")
        else:
            result = call()
        analysis = {**result.model_dump(), "created_at": now_iso(), "model": self.ai.config().get("model"),
                    "facts": facts if kind == "dashboard" else None, "edited": False}
        self._store(kind, item_id, analysis)
        return self.get(kind, item_id)

    def save(self, kind: str, item_id: str, values: dict[str, Any]) -> dict[str, Any]:
        """The tester's edits to the analysis (kept; the PDF is made again from them)."""
        self._check(kind, item_id)
        current = self._load(kind, item_id)
        if not current:
            raise QAError("AI_REPORT_EMPTY", "Analyze with the AI first, then edit the text.", status=409)
        if "summary" in values:
            current["summary"] = str(values["summary"] or "").strip()[:4000]
        for key in LIST_KEYS:
            if key in values:
                items = values[key] if isinstance(values[key], list) else str(values[key] or "").splitlines()
                current[key] = [str(x).strip()[:1000] for x in items if str(x).strip()][:40]
        if kind == "perf" and "suspected_bottlenecks" in values:
            out = []
            for line in values["suspected_bottlenecks"] or []:
                text = str(line).strip()
                if not text:
                    continue
                m = re.match(r"^(.+?)\s*\((low|medium|high)(?: confidence)?\)\s*:\s*(.*)$", text, re.I)
                out.append({"area": m.group(1), "confidence": m.group(2).lower(), "evidence": m.group(3)} if m
                           else {"area": text.split(":")[0][:120], "confidence": "low",
                                 "evidence": text.split(":", 1)[1].strip() if ":" in text else ""})
            current["suspected_bottlenecks"] = out[:20]
        current["edited"] = True
        current["updated_at"] = now_iso()
        self._store(kind, item_id, current)
        return self.get(kind, item_id)

    # ---- the PDF -------------------------------------------------------------------------------

    def make_pdf(self, kind: str, item_id: str) -> dict[str, Any]:
        self._check(kind, item_id)
        analysis = self._load(kind, item_id)
        if not analysis:
            raise QAError("AI_REPORT_EMPTY", "Analyze with the AI first; the PDF is made from the analysis.", status=409)
        title, body = {"run": self._run_html, "perf": self._perf_html, "dashboard": self._dashboard_html}[kind](
            item_id, analysis)
        page = self.page(title, body)
        folder = self.folder(kind, item_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "report.html").write_text(page, encoding="utf-8")
        print_pdf(page, folder / "report.pdf")
        (folder / "pdf.json").write_text(json.dumps({"stamp": self._stamp(analysis), "made_at": now_iso()}), encoding="utf-8")
        return {**self.get(kind, item_id), "title": title}

    def pdf_file(self, kind: str, item_id: str) -> Path:
        path = self.folder(kind, item_id) / "report.pdf"
        if not path.exists():
            raise QAError("AI_REPORT_NO_PDF", "Make the PDF first.", status=404)
        return path

    def file_name(self, kind: str, item_id: str) -> str:
        return {"run": f"{item_id}-ai-analysis.pdf", "perf": f"{item_id}-ai-performance-analysis.pdf",
                "dashboard": f"dashboard-ai-analysis-{now().strftime('%Y%m%d')}.pdf"}[kind]

    @staticmethod
    def page(title: str, body: str) -> str:
        extra = """
.ai-note { border-left: 3px solid var(--accent); background: #eef4ff; padding: 8px 12px; border-radius: 6px; }
.steps td.shot { width: 46%; }
.steps td.shot img { width: 100%; border: 1px solid var(--border); border-radius: 6px; display: block; }
ul { margin: 4px 0 10px; padding-left: 20px; } li { margin: 2px 0; }
"""
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
                f'content="width=device-width, initial-scale=1"><title>{e(title)}</title><style>{CSS}{extra}'
                f'{branding.REPORT_CSS}</style></head><body><main>{body}'
                '<p class="muted" style="margin-top:28px">The analysis was written by the On-Premise AI from the recorded '
                "data shown in this report and reviewed by the tester. Treat suspected causes as hypotheses to verify."
                "</p></main></body></html>")

    @staticmethod
    def _cover(title: str, subtitle: str) -> str:
        return (f'<header class="cover">{branding.report_header_html()}<h1>{e(title)}</h1>'
                f'<div class="muted">{e(branding.name())} · {e(subtitle)} · generated {e(_when(now_iso()))}</div></header>')

    @staticmethod
    def analysis_html(a: dict[str, Any], heading: str = "AI analysis") -> str:
        def section(title: str, items: list[Any] | None) -> str:
            if not items:
                return ""
            return f"<h3>{e(title)}</h3><ul>{''.join(f'<li>{e(x)}</li>' for x in items)}</ul>"

        bottlenecks = [f"{b.get('area')} ({b.get('confidence')} confidence): {b.get('evidence')}"
                       for b in a.get("suspected_bottlenecks") or []]
        who = f"{a.get('model') or 'the On-Premise AI'} on {_when(a.get('created_at'))}"
        if a.get("edited"):
            who += f", edited by the tester on {_when(a.get('updated_at'))}"
        return (f"<h2>{e(heading)}</h2><div class='ai-note'><p>{e(a.get('summary'))}</p></div>"
                + section("Observations", a.get("observations")) + section("Issues and risks", a.get("issues"))
                + section("Suspected bottlenecks", bottlenecks) + section("Recommendations", a.get("recommendations"))
                + section("Not determinable from this data", a.get("undetermined"))
                + f"<p class='muted'>AI analysis by {e(who)}.</p>")

    def _run_html(self, run_id: str, a: dict[str, Any]) -> tuple[str, str]:
        r = self._run(run_id)
        name = self._script_name(r.get("script_id")) or r.get("script_id") or run_id
        steps = r["steps"] or []
        passed = sum(s.get("status") == "PASSED" for s in steps)
        failed = sum(s.get("status") == "FAILED" for s in steps)
        total = sum(int(s.get("duration_ms") or 0) for s in steps)
        title = f"Test run analysis: {name}"
        root = self.paths.evidence
        rows, images = [], 0
        for s in steps:
            img = ""
            if s.get("screenshot") and images < MAX_STEP_IMAGES:
                uri = _data_uri(Path(s["screenshot"]), root)
                if uri:
                    images += 1
                    img = f'<img src="{uri}" alt="Screenshot after step {s.get("index", 0) + 1}">'
            detail = e(s.get("description") or s.get("action"))
            if s.get("healed"):
                h = s["healed"]
                detail += f"<div class='pill WARN'>Self-healed: {e(h.get('original'))} → {e(h.get('healed'))}</div>"
            if s.get("error"):
                detail += f"<div class='muted'>{e(s['error'])}</div>"
            rows.append(f"<tr><td class='num'>{s.get('index', 0) + 1}</td><td>{detail}<div class='muted'>"
                        f"{_ms(s.get('duration_ms'))}</div></td><td>{pill(s.get('status'))}</td>"
                        f"<td class='shot'>{img or '<span class=muted>No screenshot</span>'}</td></tr>")
        others = [p for p in (r.get("evidence") or {}).get("screenshots") or []
                  if p not in {s.get("screenshot") for s in steps}]
        extra = ""
        for p in others[:4]:
            uri = _data_uri(Path(p), root)
            if uri:
                extra += f'<div class="shot"><img src="{uri}" alt=""><div class="muted">{e(Path(p).name)}</div></div>'
        meta = [("Run", run_id), ("Script", name), ("TFS test case", r.get("test_case_id") or "—"),
                ("Browser", r.get("browser") or "—"), ("Start URL", r.get("base_url") or "—"),
                ("Started", _when(r.get("started_at"))), ("Finished", _when(r.get("finished_at")))]
        if r.get("data_row"):
            meta.append(("Test data row", r["data_row"]))
        body = (self._cover(title, "functional test run")
                + f"<div class='kpis'>{kpi('Result', r['status'])}{kpi('Steps', len(steps))}{kpi('Passed', passed)}"
                  f"{kpi('Failed', failed)}{kpi('Step time', _ms(total))}</div>"
                + "<table>" + "".join(f"<tr><th style='width:160px'>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in meta) + "</table>"
                + (f"<p class='note'>Failed at step {r['failed_step'] + 1}: {e(r.get('error'))}</p>"
                   if r.get("failed_step") is not None else (f"<p class='note'>{e(r['error'])}</p>" if r.get("error") else ""))
                + self.analysis_html(a)
                + "<h2>Steps and screenshots</h2><table class='steps'><thead><tr><th class='num'>#</th><th>Step</th>"
                  "<th>Result</th><th>Page after the step</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
                + (f"<h2>Other evidence</h2><div class='shots'>{extra}</div>" if extra else ""))
        return title, body

    def _perf_html(self, run_id: str, a: dict[str, Any]) -> tuple[str, str]:
        run = self.performance.get_run(run_id)
        m = run.get("metrics") or {}
        o = m.get("overall") or {}
        kinds = {"load": "Load test", "stress": "Stress test", "spike": "Spike test", "soak": "Soak test"}
        title = f"AI performance analysis: {run.get('name') or run_id}"
        meta = [("Run", run_id), ("Test type", kinds.get(run.get("kind"), run.get("kind"))),
                ("Started", _when(run.get("started_at"))), ("Ended", _when(run.get("finished_at"))),
                ("Duration", f"{round(m.get('duration_s') or 0)} s"), ("Peak users", m.get("peak_threads", "—")),
                ("Environment", ((run.get("config") or {}).get("environment") or {}).get("name") or "—"),
                ("Build", run.get("build") or "—"), ("Verdict", run.get("verdict") or "—")]
        reqs = "".join(
            f"<tr><td>{e(r.get('label'))}</td><td class='num'>{r.get('samples')}</td><td class='num'>{r.get('error_pct')}%</td>"
            f"<td class='num'>{_ms(r.get('avg_ms'))}</td><td class='num'>{_ms(r.get('p90_ms'))}</td>"
            f"<td class='num'>{_ms(r.get('p95_ms'))}</td><td class='num'>{_ms(r.get('p99_ms'))}</td>"
            f"<td class='num'>{_ms(r.get('max_ms'))}</td><td class='num'>{r.get('throughput')}</td></tr>"
            for r in [*(m.get("requests") or []), *([o] if o else [])])
        sla = (run.get("sla") or {}).get("checks") or []
        sla_html = ("<h2>SLA checks</h2><table><thead><tr><th>Check</th><th>Scope</th><th>Limit</th><th>Actual</th><th>Result</th>"
                    "</tr></thead><tbody>" + "".join(
                        f"<tr><td>{e(c.get('name'))}</td><td>{e(c.get('scope'))}</td><td>{e(c.get('limit'))} {e(c.get('unit'))}</td>"
                        f"<td>{e(c.get('actual'))} {e(c.get('unit'))}</td><td>{pill(c.get('status'))}</td></tr>" for c in sla)
                    + "</tbody></table>") if sla else ""
        body = (self._cover(title, "performance test run")
                + (f"<div class='kpis'>{kpi('Samples', f'{o.get('samples', 0):,}', f'{o.get('errors', 0):,} errors')}"
                   f"{kpi('Error rate', f'{o.get('error_pct')}%')}{kpi('Average', _ms(o.get('avg_ms')))}"
                   f"{kpi('95th percentile', _ms(o.get('p95_ms')))}"
                   f"{kpi('Throughput', f'{float(o.get('throughput') or 0):.1f} req/s')}</div>" if o else "")
                + "<table>" + "".join(f"<tr><th style='width:160px'>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in meta) + "</table>"
                + self.analysis_html(a, "AI performance analysis")
                + ("<h2>Requests</h2><table><thead><tr><th>Request</th><th class='num'>Samples</th><th class='num'>Errors</th>"
                   "<th class='num'>Avg</th><th class='num'>90th</th><th class='num'>95th</th><th class='num'>99th</th>"
                   "<th class='num'>Max</th><th class='num'>Req/s</th></tr></thead><tbody>" + reqs + "</tbody></table>" if reqs else "")
                + sla_html)
        return title, body

    def _dashboard_html(self, _id: str, a: dict[str, Any]) -> tuple[str, str]:
        f = a.get("facts") or self.dashboard_facts()
        tfs, ac, cov = f.get("tfs") or {}, f.get("acceptance_criteria") or {}, f.get("automation_coverage") or {}
        fn, perf = f.get("functional_latest_results") or {}, f.get("performance") or {}
        defects, td = f.get("defects") or {}, f.get("test_data") or {}
        dash = lambda v: "—" if v is None else v  # noqa: E731
        pct = lambda v: "—" if v is None else f"{v}%"  # noqa: E731
        title = "Dashboard analysis"
        figures = [
            ("User stories", dash(tfs.get("user_stories"))),
            ("Acceptance criteria", ac.get("criteria") if ac.get("available") else "—"),
            ("User stories with criteria", f"{ac.get('stories_with_criteria')} of {ac.get('stories')}" if ac.get("available") else "—"),
            ("Test cases (TFS)", dash(tfs.get("test_cases"))),
            ("Automated test cases", dash(cov.get("automated_test_cases"))),
            ("Not automated", dash(cov.get("not_automated"))),
            ("Automation coverage", pct(cov.get("automation_coverage"))),
            ("Test data rows", dash(f.get("test_data_rows"))),
            ("Test cases without data", dash(td.get("tfs_test_cases_without_data"))),
            ("Automated tests with results", dash(fn.get("scripts_run"))),
            ("Passing / failing (latest)", f"{dash(fn.get('passed'))} / {dash(fn.get('failed'))}"),
            ("Pass rate", pct(fn.get("pass_rate"))),
            ("Defects in TFS (open / closed)", f"{dash(defects.get('open'))} / {dash(defects.get('closed'))}" if defects else "—"),
            ("Defect drafts to review", dash(f.get("defect_drafts_to_review"))),
            ("Performance runs", dash(perf.get("runs"))),
            ("Performance failures", dash(perf.get("failed"))),
            ("Performance SLA pass rate", pct(perf.get("sla_pass_rate"))),
        ]
        project = tfs.get("project") or ("TFS not connected" if not tfs.get("connected") else "")
        body = (self._cover(title, project or "QA dashboard")
                + f"<div class='kpis'>{kpi('Automation coverage', pct(cov.get('automation_coverage')))}"
                  f"{kpi('Pass rate', pct(fn.get('pass_rate')))}{kpi('Open defects', dash(tfs.get('open_defects')))}"
                  f"{kpi('SLA pass rate', pct(perf.get('sla_pass_rate')))}</div>"
                + self.analysis_html(a)
                + "<h2>Dashboard figures the AI was given</h2><table>"
                + "".join(f"<tr><th style='width:260px'>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in figures) + "</table>"
                + (f"<p class='muted'>{e(tfs.get('reason'))}</p>" if tfs.get("reason") else "")
                + f"<p class='muted'>Figures as they were when the analysis was made ({e(_when(a.get('created_at')))}).</p>")
        return title, body

    # ---- the email -----------------------------------------------------------------------------

    def email_draft(self, kind: str, item_id: str) -> dict[str, Any]:
        """The subject and body written from the analysis (no AI call, so the form opens at once)."""
        self._check(kind, item_id)
        a = self._load(kind, item_id)
        if not a:
            raise QAError("AI_REPORT_EMPTY", "Analyze with the AI first; the email is written from the analysis.", status=409)
        name = branding.name()
        day = now().astimezone().strftime("%d %b %Y")
        if kind == "run":
            r = self._run(item_id)
            script = self._script_name(r.get("script_id")) or r.get("script_id")
            steps = r["steps"] or []
            passed = sum(s.get("status") == "PASSED" for s in steps)
            subject = f"[{r['status']}] Test run analysis: {script} ({item_id})"
            facts = (f"Result: {r['status']}, {passed} of {len(steps)} steps passed"
                     + (f", failed at step {r['failed_step'] + 1}" if r.get("failed_step") is not None else "")
                     + f". Browser: {r.get('browser') or '—'}. Run: {item_id}, {_when(r.get('started_at'))}.")
            intro = f"Please find attached the AI analysis report of the automated test run of \"{script}\"."
        elif kind == "perf":
            run = self.performance.get_run(item_id)
            o = (run.get("metrics") or {}).get("overall") or {}
            verdict = {"PASS": "Targets met", "FAIL": "Targets missed"}.get(run.get("verdict"), "Analysis")
            subject = f"[{verdict}] Performance analysis: {run.get('name') or item_id} ({item_id})"
            facts = (f"{o.get('samples', 0):,} samples, {o.get('error_pct', '—')}% errors, average {_ms(o.get('avg_ms'))}, "
                     f"95th percentile {_ms(o.get('p95_ms'))}, {float(o.get('throughput') or 0):.1f} requests per second. "
                     f"Run: {item_id}, {_when(run.get('started_at'))}.")
            intro = f"Please find attached the AI performance analysis of \"{run.get('name') or item_id}\"."
        else:
            f = a.get("facts") or {}
            cov, fn = f.get("automation_coverage") or {}, f.get("functional_latest_results") or {}
            project = (f.get("tfs") or {}).get("project")
            subject = f"QA dashboard analysis{': ' + project if project else ''} ({day})"
            facts = (f"Automation coverage: {cov.get('automation_coverage', '—')}%. Pass rate: {fn.get('pass_rate', '—')}%. "
                     f"Open defects: {(f.get('tfs') or {}).get('open_defects', '—')}.")
            intro = f"Please find attached the AI analysis of the QA dashboard{' for ' + project if project else ''}."
        points = (a.get("issues") or a.get("observations") or [])[:3]
        recs = (a.get("recommendations") or [])[:3]
        body = f"Hello,\n\n{intro}\n\n{(a.get('summary') or '').strip()}\n\n{facts}"
        if points:
            body += "\n\nKey points:\n" + "\n".join(f"- {p}" for p in points)
        if recs:
            body += "\n\nRecommended next steps:\n" + "\n".join(f"- {p}" for p in recs)
        body += f"\n\nThe full report is in the attached PDF.\n\nRegards,\n{name}"
        cfg = self.mailer.config() if self.mailer else {}
        return {"to": cfg.get("default_to") or "", "cc": "", "subject": subject[:200], "body": body,
                "attachment": self.file_name(kind, item_id), "email_configured": bool(cfg.get("configured"))}

    def send(self, kind: str, item_id: str, to: Any, subject: str, body: str, cc: Any = None) -> dict[str, Any]:
        self._check(kind, item_id)
        if not (subject or "").strip():
            raise QAError("VALIDATION_FAILED", "Write a subject.", status=422)
        state = self.get(kind, item_id)
        if not state["has_pdf"]:  # never made, or the analysis was edited after it
            self.make_pdf(kind, item_id)
        folder = self.folder(kind, item_id)
        attachment = folder / self.file_name(kind, item_id)
        attachment.write_bytes((folder / "report.pdf").read_bytes())
        try:
            text = (body or "").strip()
            mail_html = (f"<div style='font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#111827'>{_lines(text)}"
                         f"<p style='color:#6b7280;font-size:12px'>Sent by {html.escape(branding.name())}.</p></div>")
            out = self.mailer.send(to, subject.strip(), mail_html, text, cc=cc, attachments=[attachment])
        finally:
            attachment.unlink(missing_ok=True)
        log.info(f"AI report emailed: {kind} {item_id}")
        return {**out, "attachment": attachment.name}
