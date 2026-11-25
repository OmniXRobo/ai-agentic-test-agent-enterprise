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
from collections import Counter
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .. import branding
from ..clock import now, now_iso, parse_iso
from ..ai.gateway import capped_schema
from ..errors import QAError
from ..logging_setup import get_logger
from .service import CSS, _data_uri, e, kpi, pill, print_pdf

log = get_logger("ai_reports")

KINDS = ("run", "perf", "dashboard", "module")

# 23.13.0: "Generate AI result" and "Send email" on the module pages and on Analytics. Each module has a title and the
# recorded data its report is made from (runs, results, defect drafts, healing ...); nothing is estimated.
MODULES: dict[str, dict[str, Any]] = {
    "analytics": {"title": "Analytics", "sources": None},
    "ai-automation": {"title": "AI-Driven Automation", "sources": ("framework",), "kinds": ("automation",),
                      "prefixes": ("GEN", "FW", "RUN")},
    "guided-automation": {"title": "Guided Browser Automation", "sources": ("functional", "python"),
                          "kinds": ("functional",), "executions": True},
    "discovery": {"title": "Application Discovery & Update", "sources": ("functional",), "prefixes": ("DISC",),
                  "discoveries": True},
    "hybrid-automation": {"title": "Hybrid Test Automation", "sources": ("functional", "python"),
                          "kinds": ("functional",), "executions": True},
    "api-testing": {"title": "API Testing", "sources": ("api",), "api": True},
    "scheduled-runs": {"title": "Scheduled Runs", "sources": ("functional", "framework"), "kinds": ("functional",),
                       "executions": True, "schedules": True},
    "perf-load": {"title": "Load Tests", "sources": ("performance",), "perf": ("load",)},
    "perf-stress": {"title": "Stress Tests", "sources": ("performance",), "perf": ("stress",)},
    "perf-spike": {"title": "Spike Tests", "sources": ("performance",), "perf": ("spike",)},
    "perf-soak": {"title": "Soak Tests", "sources": ("performance",), "perf": ("soak",)},
    "perf-live": {"title": "Performance Live Dashboard", "sources": ("performance",), "perf": None},
    "perf-schedules": {"title": "Performance Schedules", "sources": ("performance",), "perf": None,
                       "perf_schedules": True},
    "perf-results": {"title": "Performance Results", "sources": ("performance",), "perf": None},
    "integration-comparison": {"title": "Integration Comparison", "sources": ("comparison",), "comparison": True},
}


class AiAnalysis(BaseModel):
    summary: str = Field(description="Two to four sentences: what was tested and the outcome")
    observations: list[str] = Field(description="Facts from the data, each citing the values that support it")
    issues: list[str] = Field(default=[], description="Failures, risks or weak spots, each with its evidence")
    recommendations: list[str] = []
    undetermined: list[str] = Field(default=[], description="Questions this data cannot answer")


ANSWER_TOKENS = 1400
ERROR_CHARS = 400
CAPS = {"observations": 6, "issues": 5, "recommendations": 5, "undetermined": 4}
LENGTH_RULE = (" Keep it short: one sentence per point, at most 6 observations, 5 issues, 5 recommendations and 4 "
               "open questions.")


RUN_PROMPT = (
    "You are a senior QA test engineer reviewing one automated browser test run (Playwright). Use only the data "
    "provided. Summarise what the test did and its result. Observations must cite step numbers, results and times. "
    "For a failed step, say what failed and quote the error; give a likely cause only when the error or the steps "
    "show it, otherwise put the question under 'undetermined'. Mention self-healed locators and slow steps (much "
    "slower than the others). Use the earlier results of the same script to say whether it is stable or flaky. "
    "Recommendations must be specific to this script. Never invent steps, pages or numbers." + LENGTH_RULE)

DASHBOARD_PROMPT = (
    "You are a QA manager reviewing a QA dashboard. Use only the figures provided; a missing figure means it is not "
    "available, never zero. Summarise the state of quality and test progress. Observations must cite the figures "
    "(counts and percentages). Issues are gaps and risks: user stories without acceptance criteria, test cases not "
    "automated or without data, failing tests, open defects, performance SLA failures. Recommendations must be "
    "concrete next steps in priority order. Never invent numbers." + LENGTH_RULE)

MODULE_PROMPT = (
    "You are a senior QA lead reviewing one module of a QA platform (named in the data). Use only the data provided; "
    "a missing figure means it is not available, never zero. Summarise the state of this module's testing. "
    "Observations must cite counts, results, dates and run ids. Issues are failures, flaky results, open defects, "
    "missed SLAs and gaps. Recommendations must be concrete next steps for this module in priority order. Never "
    "invent numbers, runs or pages." + LENGTH_RULE)
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
MAX_STEP_IMAGES = 80
LIST_KEYS = ("observations", "issues", "recommendations", "undetermined")


def _short(text: Any, n: int) -> Any:
    """The first ``n`` characters of a long text (a Playwright error with its call log), on one line."""
    if not isinstance(text, str):
        return text
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _healed(h: Any) -> Any:
    """Only what the analysis needs of a self-healing record: the locator that failed and the one that worked."""
    if not isinstance(h, dict):
        return _short(str(h), 200)
    keep = {k: _short(h.get(k), 160) for k in ("original", "healed", "confidence") if h.get(k) is not None}
    return keep or _short(json.dumps(h, ensure_ascii=False, default=str), 200)


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
            raise QAError("VALIDATION_FAILED", "The report kind must be run, perf, dashboard or module.", status=422)
        if not ID_RE.match(item_id or ""):
            raise QAError("VALIDATION_FAILED", "That id is not valid.", status=422)
        if kind == "module" and item_id not in MODULES:
            raise QAError("VALIDATION_FAILED", "That module has no AI report.", status=422)

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
        # 23.3.34: kept short (a long Playwright error with its call log, or the whole healing record, made the prompt
        # slow to read for a local model on a PC without a GPU). Passwords are never in a step's description.
        steps = []
        for i, s in enumerate(r["steps"] or []):
            desc = s.get("description") or s.get("action")
            step = {"step": s.get("index", i) + 1, "description": _short(desc, 160), "result": s.get("status"),
                    "time_ms": s.get("duration_ms")}
            if s.get("action") and s.get("action") != desc:
                step["action"] = s.get("action")
            if s.get("error"):
                step["error"] = _short(s["error"], ERROR_CHARS)
            if s.get("healed"):
                step["self_healed"] = _healed(s["healed"])
            steps.append(step)
        return {
            "script": self._script_name(r.get("script_id")) or r.get("script_id"), "run_id": run_id,
            "result": r["status"], "browser": r.get("browser"), "start_url": r.get("base_url"),
            "test_case_id": r.get("test_case_id"), "started": r.get("started_at"), "finished": r.get("finished_at"),
            "failed_step": r["failed_step"] + 1 if r.get("failed_step") is not None else None,
            "error": _short(r.get("error"), ERROR_CHARS),
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

    def module_facts(self, module: str) -> dict[str, Any]:
        """The recorded data of one module page (23.13.0)."""
        m = MODULES[module]
        db = self.analytics.db
        out: dict[str, Any] = {"module": m["title"], "as_of": now_iso()}

        def safe(key: str, fn: Any) -> None:
            try:
                value = fn()
                if value not in (None, [], {}):
                    out[key] = value
            except Exception as exc:  # an older database layout must not stop the report
                log.info(f"module facts {module}/{key} skipped: {exc!r}")

        if module == "analytics":
            safe("functional_30_days", lambda: {k: v for k, v in self.analytics.functional(30).items()
                                                if k not in ("trend",)})
            safe("performance_30_days", lambda: {k: v for k, v in self.analytics.performance(30).items()
                                                 if k in ("sla", "max_users", "plans")})
            safe("dashboard", self.dashboard_facts)
            return out
        if m.get("kinds") or m.get("prefixes"):
            def runs() -> dict[str, Any]:
                rows = db.query("SELECT run_id, kind, title, status, started_at, finished_at FROM runs "
                                "ORDER BY started_at DESC LIMIT 400")
                keep = [r for r in rows if r["kind"] in (m.get("kinds") or ()) or
                        str(r["run_id"]).split("-", 1)[0] in (m.get("prefixes") or ())]
                return {"total": len(keep), "by_status": dict(Counter(r["status"] for r in keep)),
                        "latest": [{"run_id": r["run_id"], "title": _short(r["title"], 100), "status": r["status"],
                                    "started": r["started_at"]} for r in keep[:15]]}
            safe("runs", runs)
        if m.get("executions"):
            def executions() -> dict[str, Any]:
                rows = db.query("SELECT run_id, script_id, status, started_at, failed_step, error, browser "
                                "FROM execution_results ORDER BY started_at DESC LIMIT 300")
                return {"total": len(rows), "by_status": dict(Counter(r["status"] for r in rows)),
                        "browsers": dict(Counter(r["browser"] or "unknown" for r in rows)),
                        "latest_failures": [{"run_id": r["run_id"], "test": self._script_name(r["script_id"]),
                                             "failed_step": (r["failed_step"] + 1) if r["failed_step"] is not None else None,
                                             "error": _short(r["error"], 200), "started": r["started_at"]}
                                            for r in rows if r["status"] == "FAILED"][:8]}
            safe("executions", executions)
            safe("self_healing", lambda: dict(Counter(r["result"] for r in db.query(
                "SELECT result FROM healing_events ORDER BY id DESC LIMIT 500"))))
        if m.get("discoveries"):
            safe("discoveries", lambda: [{"url": r["base_url"], "at": r["created_at"]} for r in db.query(
                "SELECT base_url, created_at FROM discoveries ORDER BY created_at DESC LIMIT 10")])
        if m.get("api"):
            safe("api_runs", lambda: [{"run_id": r["id"], "label": r["label"], "status": r["status"],
                                       "counts": json.loads(r["counts"] or "{}"), "started": r["started_at"],
                                       "environment": r["environment"]} for r in db.query(
                "SELECT id, label, status, counts, started_at, environment FROM api_runs ORDER BY started_at DESC LIMIT 12")])
            safe("api_failures", lambda: [{"test": r["test_name"], "status_code": r["status_code"],
                                           "error": _short(r["error"], 200), "class": r["classification"]}
                                          for r in db.query("SELECT test_name, status_code, error, classification "
                                                            "FROM api_results WHERE status = 'FAILED' ORDER BY id DESC LIMIT 10")])
        if m.get("schedules"):
            def schedules() -> Any:
                path = self.paths.root / "schedules.json"
                data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
                items = data.get("schedules", data if isinstance(data, list) else [])
                return [{"name": x.get("name"), "enabled": x.get("enabled"), "next_run": x.get("next_run_at"),
                         "last_status": x.get("last_status")} for x in items[:20] if isinstance(x, dict)]
            safe("schedules", schedules)
        if "perf" in m:
            def perf() -> dict[str, Any]:
                rows = db.query("SELECT run_id, kind, name, status, started_at, metrics, sla FROM perf_runs "
                                "ORDER BY started_at DESC LIMIT 200")
                if m["perf"]:
                    rows = [r for r in rows if (r["kind"] or "").lower() in m["perf"]]
                latest = []
                for r in rows[:12]:
                    o = (json.loads(r["metrics"] or "{}") or {}).get("overall") or {}
                    sla = json.loads(r["sla"] or "null")
                    latest.append({"run_id": r["run_id"], "name": r["name"], "type": r["kind"], "status": r["status"],
                                   "started": r["started_at"], "p95_ms": o.get("p95_ms"), "avg_ms": o.get("avg_ms"),
                                   "error_pct": o.get("error_pct"), "throughput": o.get("throughput"),
                                   "sla": (sla or {}).get("verdict") if isinstance(sla, dict) else sla})
                return {"total": len(rows), "by_status": dict(Counter(r["status"] for r in rows)), "latest": latest}
            safe("performance_runs", perf)
        if m.get("perf_schedules"):
            safe("performance_schedules", lambda: [{"name": r["name"], "enabled": bool(r["enabled"]),
                                                    "next_run": r["next_run_at"], "last_run": r["last_run_id"]}
                                                   for r in db.query("SELECT name, enabled, next_run_at, last_run_id "
                                                                     "FROM perf_schedules LIMIT 30")])
        if m.get("comparison"):
            safe("comparison_runs", lambda: [{"id": r["id"], "status": r["status"], "result": r["result"],
                                              "summary": json.loads(r["summary"] or "{}"), "started": r["started_at"]}
                                             for r in db.query("SELECT id, status, result, summary, started_at FROM "
                                                               "cmp_executions ORDER BY started_at DESC LIMIT 10")])
            safe("open_discrepancies", lambda: (db.query_one("SELECT COUNT(*) AS n FROM cmp_discrepancies") or {}).get("n"))
        if m.get("sources"):
            marks = ",".join("?" * len(m["sources"]))
            safe("defect_drafts", lambda: dict(Counter(r["status"] for r in db.query(
                f"SELECT status FROM defect_drafts WHERE source IN ({marks})", m["sources"]))))
        return out

    def analyze(self, kind: str, item_id: str) -> dict[str, Any]:
        self._check(kind, item_id)
        if kind == "perf":
            self.performance.analyze(item_id)
            return self.get(kind, item_id)
        if kind == "module":
            facts = self.module_facts(item_id)
            prompt = MODULE_PROMPT
            label = f"Analyzing {MODULES[item_id]['title']} with the On-Premise AI"
        else:
            facts = self.run_facts(item_id) if kind == "run" else self.dashboard_facts()
            prompt = RUN_PROMPT if kind == "run" else DASHBOARD_PROMPT
            label = (f"Analyzing run {item_id} with the On-Premise AI" if kind == "run"
                     else "Analyzing the dashboard with the On-Premise AI")

        def call() -> AiAnalysis:
            # 23.3.34: the long-answer time limit (generation_timeout_seconds, 10 minutes by default) instead of the
            # 2-minute one for short requests, and a grammar that keeps the answer short so it also finishes sooner.
            sent = {k: v for k, v in facts.items() if v not in (None, [], {})} if kind == "run" else facts
            facts_json = json.dumps(sent,
                                    ensure_ascii=False, default=str, separators=(",", ":"))
            return self.ai.complete_json(prompt, facts_json, AiAnalysis, max_tokens=ANSWER_TOKENS,
                                         timeout=self.ai.long_timeout(), enforce_schema=capped_schema(AiAnalysis, CAPS))

        if self.agent is not None:
            with self.agent.activity(label, run_id=item_id if kind == "run" else None) as act:
                result = call()
                act.complete("AI analysis ready")
        else:
            result = call()
        analysis = {**result.model_dump(), "created_at": now_iso(), "model": self.ai.config().get("model"),
                    "facts": facts if kind in ("dashboard", "module") else None, "edited": False}
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
        if not analysis and kind != "module":
            raise QAError("AI_REPORT_EMPTY", "Analyze with the AI first; the PDF is made from the analysis.", status=409)
        title, body = {"run": self._run_html, "perf": self._perf_html, "dashboard": self._dashboard_html,
                       "module": self._module_html}[kind](item_id, analysis or {})
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
                "dashboard": f"dashboard-ai-analysis-{now().strftime('%Y%m%d')}.pdf",
                "module": f"{item_id}-report-{now().strftime('%Y%m%d')}.pdf"}[kind]

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
                + ("<div class='kpis'>"
                   + kpi("Samples", f"{o.get('samples', 0):,}", f"{o.get('errors', 0):,} errors")
                   + kpi("Error rate", f"{o.get('error_pct')}%") + kpi("Average", _ms(o.get("avg_ms")))
                   + kpi("95th percentile", _ms(o.get("p95_ms")))
                   + kpi("Throughput", f"{float(o.get('throughput') or 0):.1f} req/s") + "</div>" if o else "")
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

    def _module_html(self, module: str, a: dict[str, Any]) -> tuple[str, str]:
        facts = (a or {}).get("facts") or self.module_facts(module)
        title = f"{MODULES[module]['title']} report"

        def table(value: Any) -> str:
            if isinstance(value, dict):
                return "<table>" + "".join(f"<tr><th style='width:220px'>{e(str(k).replace('_', ' '))}</th>"
                                           f"<td>{table(v)}</td></tr>" for k, v in value.items()) + "</table>"
            if isinstance(value, list):
                if value and all(isinstance(x, dict) for x in value):
                    keys = list(dict.fromkeys(k for x in value for k in x))[:8]
                    return ("<table><thead><tr>" + "".join(f"<th>{e(k.replace('_', ' '))}</th>" for k in keys)
                            + "</tr></thead><tbody>" + "".join(
                                "<tr>" + "".join(f"<td>{e(_short(x.get(k), 160) if x.get(k) is not None else '—')}</td>"
                                                 for k in keys) + "</tr>" for x in value) + "</tbody></table>")
                return ", ".join(e(str(x)) for x in value) or "—"
            return e("—" if value is None else str(value))

        sections = "".join(f"<h2>{e(str(k).replace('_', ' ').capitalize())}</h2>{table(v)}"
                           for k, v in facts.items() if k not in ("module", "as_of"))
        body = (self._cover(title, f"Data as of {_when(facts.get('as_of'))}")
                + (self.analysis_html(a) if a and a.get("summary") else
                   "<p class='ai-note'>No AI analysis was generated for this report; it shows the recorded data only.</p>")
                + sections)
        return title, body

    # ---- the email -----------------------------------------------------------------------------

    def email_draft(self, kind: str, item_id: str) -> dict[str, Any]:
        """The subject and body written from the analysis (no AI call, so the form opens at once)."""
        self._check(kind, item_id)
        a = self._load(kind, item_id)
        if not a and kind != "module":
            raise QAError("AI_REPORT_EMPTY", "Analyze with the AI first; the email is written from the analysis.", status=409)
        a = a or {}
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
        elif kind == "module":
            title = MODULES[item_id]["title"]
            subject = f"{title} report ({day})"
            facts = "The attached PDF has the recorded results" + (" and the AI analysis." if a.get("summary") else ".")
            intro = f"Please find attached the {title} report."
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
