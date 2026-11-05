"""23.3.28: performance test reports: one run, a comparison of runs, and a consolidated report across test types.

Every number comes from the run's own files (metrics.json, results.jtl, config.json, generator.json) or from stored
drafts. A section whose evidence is missing says so (see "Limitations"); nothing is estimated or filled in. Findings
are measurements, not causes: a cause such as a memory leak is never stated as confirmed, because this application
measures only the load generator, not the servers under test. AI text is shown only where the user asked for an
analysis, and it is labelled as analysis.

The PDF is printed from the HTML by the bundled Chromium, like the other QA reports.
"""

from __future__ import annotations

import csv
import html
import json
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from ..clock import parse_iso
from ..logging_setup import redact_text
from ..performance.jtl import percentile
from . import charts

KIND_TITLES = {"load": "Load test", "stress": "Stress test", "spike": "Spike test", "soak": "Soak test"}
STATE_TEXT = {"COMPLETED": "Completed", "FAILED": "Failed", "CANCELLED": "Stopped", "INTERRUPTED": "Interrupted",
              "SKIPPED": "Skipped", "QUEUED": "Queued", "VALIDATING": "Pre-run checks", "RUNNING": "Running",
              "STOPPING": "Stopping"}
VERDICT_TEXT = {"PASS": "PASS", "FAIL": "FAIL", "NOT_SPECIFIED": "Target not specified",
                "INCONCLUSIVE": "Inconclusive (partial run)", None: "No verdict"}
MAX_COMPARE = 6
COLORS = ["#2563eb", "#db2777", "#0d9488", "#7c3aed", "#ea580c", "#475569"]


def e(value: Any) -> str:
    """Escape for HTML, with secrets redacted (an error message can quote a header or a token)."""
    return html.escape(redact_text("" if value is None else str(value)))


def _ms(v: Any) -> str:
    return "—" if v is None else f"{float(v):,.0f} ms" if float(v) >= 10 else f"{float(v):.1f} ms"


def _num(v: Any, d: int = 1) -> str:
    return "—" if v is None else f"{float(v):,.{d}f}"


def _dur(s: Any) -> str:
    if s is None:
        return "—"
    s = int(round(float(s)))
    if s < 60:
        return f"{s} s"
    m, r = divmod(s, 60)
    if m < 60:
        return f"{m} min {r} s" if r else f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m} min"


def local_time(ts: str | None) -> str:
    """Date and time in this PC's time zone, with the zone named."""
    if not ts:
        return "—"
    try:
        d = parse_iso(ts).astimezone()
    except ValueError:
        return ts
    off = d.strftime("%z")
    return f"{d.strftime('%Y-%m-%d %H:%M:%S')} {d.tzname() or ''} (UTC{off[:3]}:{off[3:]})".replace("  ", " ")


def time_zone() -> str:
    d = datetime.now().astimezone()
    off = d.strftime("%z")
    return f"{d.tzname() or 'local'} (UTC{off[:3]}:{off[3:]})"


def kpi(label: str, value: Any, note: str = "") -> str:
    return f'<div class="kpi"><div class="l">{e(label)}</div><div class="v">{e(value)}</div><div class="n">{e(note)}</div></div>'


def pill(text: str | None, cls: str | None = None) -> str:
    t = text or "—"
    return f'<span class="pill {e(cls or t)}">{e(t.replace("_", " "))}</span>'


def _table(head: list[str], rows: list[list[Any]], num_from: int = 1) -> str:
    """Cells are escaped unless they are already markup (wrapped in Markup)."""
    def cell(i: int, v: Any, tag: str) -> str:
        cls = ' class="num"' if i >= num_from and tag == "td" and not isinstance(v, Markup) else ""
        return f"<{tag}{cls}>{v if isinstance(v, Markup) else e(v)}</{tag}>"
    return ("<table><thead><tr>" + "".join(cell(i, Markup(e(h)), "th") for i, h in enumerate(head)) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(cell(i, v, "td") for i, v in enumerate(r)) + "</tr>" for r in rows)
            + "</tbody></table>")


class Markup(str):
    """Text that is already HTML."""


def _kv(rows: list[list[Any]]) -> str:
    """A two-column table of names and values, without a header row."""
    return ('<table class="kv"><tbody>' + "".join(
        f"<tr><th>{e(k)}</th><td>{v if isinstance(v, Markup) else e(v)}</td></tr>" for k, v in rows) + "</tbody></table>")


# ---- reading a run --------------------------------------------------------------------------------------------

def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        return None


def transactions_of(plan: dict[str, Any] | None) -> set[str]:
    script = (plan or {}).get("script")
    if not script:
        return set()
    try:
        from ..performance import script as jscript
        return {r["transaction"] for r in jscript.analyze_cached(script)["summary"]["requests"] if r["transaction"]}
    except Exception:
        return set()


def _users_at(groups: list[dict[str, Any]], t: float) -> float:
    total = 0.0
    for g in groups:
        start, end = g["delay_s"], g["delay_s"] + g["duration_s"]
        if start <= t < end:
            ramp = g["ramp_up_s"]
            total += g["users"] if ramp <= 0 or t >= start + ramp else g["users"] * (t - start) / ramp
    return round(total, 1)


def planned_windows(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The planned load as a list of periods with constant (or ramping) users, from the thread groups."""
    marks = sorted({0.0} | {float(m) for g in groups for m in (g["delay_s"], g["delay_s"] + g["ramp_up_s"],
                                                                  g["delay_s"] + g["duration_s"])})
    out = []
    for a, b in zip(marks, marks[1:]):
        if b - a < 1:
            continue
        u0, u1 = _users_at(groups, a), _users_at(groups, b - 1e-6)
        out.append({"start_s": a, "end_s": b, "users_from": u0, "users_to": u1, "ramp": abs(u1 - u0) >= 0.5,
                    "name": (f"Ramp {u0:g} → {u1:g} users" if abs(u1 - u0) >= 0.5 else
                             f"{u1:g} user" + ("" if u1 == 1 else "s"))})
    return out


def phase_stats(jtl_path: Path, windows: list[dict[str, Any]], transactions: set[str]) -> dict[str, Any]:
    """Requests per window, by start time from the first sample: samples, errors, average, 95th percentile and
    requests per second. Transaction samples are left out (they are not requests)."""
    if not jtl_path.is_file():
        return {"available": False, "windows": []}
    t0 = None
    last = 0
    with jtl_path.open(newline="", encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh):
            try:
                ts, el = int(float(r.get("timeStamp") or 0)), int(float(r.get("elapsed") or 0))
            except ValueError:
                continue
            if ts <= 0:
                continue
            t0 = ts if t0 is None else min(t0, ts)
            last = max(last, ts + el)
    if t0 is None:
        return {"available": False, "windows": []}
    observed_s = (last - t0) / 1000
    buckets: list[dict[str, Any]] = [{"values": [], "err": 0} for _ in windows]
    with jtl_path.open(newline="", encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh):
            label = r.get("label") or ""
            if label in transactions:
                continue
            try:
                ts, el = int(float(r.get("timeStamp") or 0)), int(float(r.get("elapsed") or 0))
            except ValueError:
                continue
            if ts <= 0:
                continue
            t = (ts - t0) / 1000
            ok = (r.get("success") or "").strip().lower() == "true"
            for i, w in enumerate(windows):
                if w["start_s"] <= t < w["end_s"]:
                    buckets[i]["values"].append(el)
                    buckets[i]["err"] += 0 if ok else 1
    out = []
    for w, b in zip(windows, buckets):
        vals = sorted(b["values"])
        n = len(vals)
        span = max(0.0, min(w["end_s"], observed_s) - w["start_s"])
        out.append({**w, "samples": n, "errors": b["err"],
                    "error_pct": round(b["err"] * 100 / n, 2) if n else None,
                    "avg_ms": round(sum(vals) / n, 1) if n else None,
                    "p95_ms": percentile(vals, 95) if n else None,
                    "throughput": round(n / span, 2) if n and span >= 1 else None,
                    "covered_s": round(span, 1)})
    return {"available": True, "windows": out, "observed_s": round(observed_s, 1)}


def gather(perf: Any, defects: Any, run_id: str) -> dict[str, Any]:
    """Everything a run report shows, read from the run's record and folder."""
    run = perf.get_run(run_id)
    folder = Path(run["folder"])
    cfg = _load_json(folder / "config.json") or run.get("config") or {}
    plan = cfg.get("plan") or {}
    m = run.get("metrics") or {}
    workload = cfg.get("workload") or {}
    groups = workload.get("thread_groups") or []
    validation = plan.get("mode") == "validation" or cfg.get("validation_run")
    known = bool(groups) and all(g.get("duration_known", True) and g.get("duration_s") for g in groups) and not validation
    tx = transactions_of(plan)
    windows: list[dict[str, Any]] = []
    phase_source = None
    if m.get("overall", {}).get("samples"):
        if known:
            windows = planned_windows(groups)
            phase_source = "planned"
        elif m.get("duration_s"):
            third = float(m["duration_s"]) / 3
            windows = [{"start_s": i * third, "end_s": (i + 1) * third + (0.001 if i == 2 else 0), "ramp": False,
                        "users_from": None, "users_to": None, "name": n}
                       for i, n in enumerate(("First third", "Middle third", "Last third"))]
            phase_source = "thirds"
    phases = phase_stats(folder / "results.jtl", windows, tx) if windows else {"available": False, "windows": []}
    trend = None
    if run["kind"] == "soak" and phase_source == "planned":
        hold = [w for w in phases["windows"] if not w["ramp"]]
        if hold:
            a, b = hold[0]["start_s"], hold[-1]["end_s"]
            third = (b - a) / 3
            trend = phase_stats(folder / "results.jtl", [
                {"start_s": a + i * third, "end_s": a + (i + 1) * third, "ramp": False, "users_from": None,
                 "users_to": None, "name": n} for i, n in enumerate(("First third of the hold", "Middle third",
                                                                     "Last third"))], tx)
    drafts = []
    if defects is not None:
        try:
            drafts = [d for d in defects.list(source="performance", limit=500)
                      if d.get("source_run_id") == run_id or run_id in json.dumps(d.get("occurrences") or [])]
        except Exception:
            drafts = []
    ctx = {"run": run, "config": cfg, "plan": plan, "metrics": m, "workload": workload, "transactions": sorted(tx),
           "phases": phases, "phase_source": phase_source, "trend": trend, "defects": drafts,
           "generator": run.get("generator") or _load_json(folder / "generator.json"),
           "validation_run": bool(validation), "build": run.get("build") or cfg.get("build") or "",
           "files": run.get("files") or [], "has_jmeter_report": run.get("has_report")}
    ctx["baseline"] = find_baseline(perf, run) if not validation else None
    if ctx["baseline"]:
        b = perf.get_run(ctx["baseline"])
        bcfg = _load_json(Path(b["folder"]) / "config.json") or b.get("config") or {}
        ctx["baseline_cmp"] = {"run": b, "differences": comparability(ctx, {"run": b, "config": bcfg,
                                                                            "build": b.get("build") or bcfg.get("build") or "",
                                                                            "generator": b.get("generator")})}
    return ctx


def find_baseline(perf: Any, run: dict[str, Any]) -> str | None:
    """The latest earlier COMPLETED run of the same scenario (or plan) and test type that has results."""
    col, val = ("scenario_id", run["scenario_id"]) if run.get("scenario_id") else ("plan_id", run["plan_id"])
    rows = perf.db.query(f"SELECT run_id, config FROM perf_runs WHERE {col} = ? AND kind = ? AND state = 'COMPLETED'"
                         " AND metrics IS NOT NULL AND run_id != ? AND COALESCE(started_at, queued_at) < ?"
                         " ORDER BY COALESCE(started_at, queued_at) DESC LIMIT 20",
                         (val, run["kind"], run["run_id"], run.get("started_at") or run.get("queued_at") or "9999"))
    for r in rows:
        try:
            if json.loads(r["config"] or "{}").get("plan", {}).get("mode") == "validation":
                continue
        except ValueError:
            continue
        return r["run_id"]
    return None


# ---- comparability --------------------------------------------------------------------------------------------

def _facts(item: dict[str, Any]) -> dict[str, Any]:
    run, cfg = item["run"], item.get("config") or {}
    wl = cfg.get("workload") or {}
    shape = wl.get("shape") or {}
    m = run.get("metrics") or {}
    gen = item.get("generator") or run.get("generator") or {}
    return {
        "kind": run.get("kind"), "plan_id": run.get("plan_id"),
        "script_version": (cfg.get("script") or {}).get("version"), "jmx": cfg.get("jmx_sha256"),
        "environment": (cfg.get("environment") or {}).get("name"), "build": item.get("build") or "",
        "peak_users": wl.get("peak_concurrent_users"), "duration_s": wl.get("total_duration_s"),
        "shape": shape.get("shape"), "model": wl.get("load_model"),
        "target_rps": shape.get("target_rps") if shape.get("model") == "rate" else None,
        "data": {d.get("id"): d.get("version") for d in cfg.get("test_data") or []},
        "app": cfg.get("application_version"), "jmeter": (cfg.get("tools") or {}).get("jmeter_version"),
        "state": run.get("state"), "partial": bool(run.get("partial")), "actual_s": m.get("duration_s"),
        "saturated": bool(gen.get("saturated")), "targets": cfg.get("targets"),
    }


def comparability(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, str]]:
    """What differs between two runs in a way that affects comparing their numbers. level "blocks": the numbers do
    not measure the same thing; "caution": they can be compared with care; "info": expected or minor."""
    fa, fb = _facts(a), _facts(b)
    ra, rb = a["run"]["run_id"], b["run"]["run_id"]
    out: list[dict[str, str]] = []

    def add(level: str, text: str) -> None:
        out.append({"level": level, "text": text})

    if fa["kind"] != fb["kind"]:
        add("blocks", f"Different test types ({fa['kind']} and {fb['kind']}): the load differs by design.")
    if fa["plan_id"] != fb["plan_id"] and fa["jmx"] and fa["jmx"] == fb["jmx"]:
        add("caution", "Different script records, but the JMX executed was identical.")
    elif fa["plan_id"] != fb["plan_id"]:
        add("blocks", "Different scripts: the requests sent are not the same.")
    elif fa["jmx"] and fb["jmx"] and fa["jmx"] != fb["jmx"]:
        sv = (f" (script version {fa['script_version']} and {fb['script_version']})"
              if fa["script_version"] != fb["script_version"] else " (same script version, different run settings)")
        add("caution", f"The executed JMX differs{sv}: requests, timers or settings changed.")
    if fa["environment"] != fb["environment"]:
        add("blocks", f"Different environments ({fa['environment'] or 'none'} and {fb['environment'] or 'none'}).")
    if fa["peak_users"] != fb["peak_users"]:
        add("caution", f"Different peak users ({fa['peak_users']} and {fb['peak_users']}).")
    if fa["shape"] != fb["shape"] or fa["model"] != fb["model"] or fa["target_rps"] != fb["target_rps"]:
        add("caution", "Different workload shape or load model.")
    if fa["duration_s"] != fb["duration_s"]:
        add("caution", f"Different planned durations ({_dur(fa['duration_s'])} and {_dur(fb['duration_s'])}).")
    if fa["data"] != fb["data"]:
        add("caution", "Different test data sets or data set versions.")
    if fa["build"] != fb["build"]:
        add("info", f"Different application builds ({fa['build'] or 'not recorded'} and {fb['build'] or 'not recorded'})"
                    ": expected when comparing builds; other differences listed here still matter.")
    for f, rid in ((fa, ra), (fb, rb)):
        if f["state"] != "COMPLETED" or f["partial"]:
            add("blocks", f"{rid} did not complete ({STATE_TEXT.get(f['state'], f['state'])}): its numbers cover only "
                          "part of the planned load.")
        if f["saturated"]:
            add("caution", f"The load generator was saturated during {rid}: its response times can be too high.")
    if fa["app"] != fb["app"] or fa["jmeter"] != fb["jmeter"]:
        add("info", "Different versions of this application or of JMeter ran the tests.")
    if fa["actual_s"] and fb["actual_s"] and abs(fa["actual_s"] - fb["actual_s"]) > 0.2 * max(fa["actual_s"], fb["actual_s"]):
        add("caution", f"Actual durations differ by more than 20% ({_dur(fa['actual_s'])} and {_dur(fb['actual_s'])}).")
    if fa["targets"] != fb["targets"]:
        add("info", "Different performance targets: the verdicts are not judged against the same limits.")
    return out


# ---- HTML pieces --------------------------------------------------------------------------------------------------

def _chart(points: list[dict[str, Any]], key: str, title: str, color: str, legend: str) -> str:
    c = charts.timeline_chart(points, key, title, color)
    tail = (f'<div class="legend"><span><i style="background:{color}"></i>{e(legend)}</span>'
            f'<span>x axis: elapsed time</span></div>')
    return c[:-6] + tail + "</div>" if c.endswith("</div>") else c


def _interval(timeline: list[dict[str, Any]]) -> int:
    return int(timeline[1]["t"] - timeline[0]["t"]) if len(timeline) > 1 else 1


SHAPE_FIELDS = {"load": ("users", "ramp_up_s", "hold_s"), "soak": ("users", "ramp_up_s", "hold_s"),
                "stress": ("users", "steps", "step_s", "ramp_up_s"),
                "spike": ("users", "ramp_up_s", "hold_s", "spike_users", "spike_at_s", "spike_s", "spike_ramp_s")}
SHAPE_NAMES = {"users": "users", "ramp_up_s": "ramp-up", "hold_s": "hold", "steps": "steps", "step_s": "step length",
               "spike_users": "spike users", "spike_at_s": "spike at", "spike_s": "spike length",
               "spike_ramp_s": "spike ramp-up"}


def shape_text(shape: dict[str, Any]) -> str:
    kind = shape.get("shape")
    parts = [f"{SHAPE_NAMES[k]} {_dur(shape[k]) if k.endswith('_s') else shape[k]}"
             for k in SHAPE_FIELDS.get(kind, ()) if shape.get(k) is not None]
    if shape.get("model") == "rate" and shape.get("target_rps"):
        parts.append(f"request-rate target {shape['target_rps']} req/s (users are the pool)")
    return f"{kind}: " + ", ".join(parts)


def cover(title: str, subtitle_rows: list[tuple[str, str]], generated: str) -> str:
    rows = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in subtitle_rows)
    return (f'<header class="cover"><h1>{e(title)}</h1><div class="muted">QA Enterprise AI · performance report · '
            f'generated {e(generated)} · all times in {e(time_zone())}</div></header>'
            f'<table class="facts">{rows}</table>')


def executive_summary(ctx: dict[str, Any]) -> list[str]:
    run, m = ctx["run"], ctx["metrics"]
    o = m.get("overall") or {}
    sla = run.get("sla") or {}
    env = (ctx["config"].get("environment") or {}).get("name")
    lines = [f"{KIND_TITLES.get(run['kind'], run['kind'])} '{run['name']}'"
             + (f" against {env}" if env else "") + (f", build {ctx['build']}" if ctx["build"] else "")
             + f": execution {STATE_TEXT.get(run['state'], run['state']).lower()}, performance verdict "
             + f"{VERDICT_TEXT.get(run.get('verdict'), run.get('verdict'))}."]
    if o.get("samples"):
        lines.append(f"Peak {m.get('peak_threads')} concurrent users over {_dur(m.get('duration_s'))}: "
                     f"{o['samples']:,} samples, average {_ms(o.get('avg_ms'))}, 95th percentile {_ms(o.get('p95_ms'))}, "
                     f"errors {o.get('error_pct')}%, {o.get('throughput')} samples per second.")
    else:
        lines.append("No samples were recorded, so there are no performance results for this run.")
    checks = sla.get("checks") or []
    if checks and run.get("verdict") in ("PASS", "FAIL"):
        failed = [c for c in checks if c["status"] == "FAIL"]
        lines.append(f"{len(checks) - len(failed)} of {len(checks)} targets met."
                     + (" Not met: " + "; ".join(f"{c['name']} ({c['scope']}) {c['actual']} {c['unit']} against "
                                                 f"{c['limit']} {c['unit']}" for c in failed[:4]) + "." if failed else ""))
    elif checks:
        lines.append("Targets were checked for reference only: a run that did not complete cannot pass or fail.")
    elif not ctx["validation_run"]:
        lines.append("No performance targets were set, so the results are reported without PASS or FAIL.")
    if run.get("partial"):
        lines.append("The run did not complete: the results cover only part of the planned load.")
    if (ctx.get("generator") or {}).get("saturated"):
        lines.append("The load generator (this PC) was saturated during the run; response times may be overstated.")
    bc = ctx.get("baseline_cmp")
    if bc and o.get("p95_ms") is not None:
        bo = (bc["run"].get("metrics") or {}).get("overall") or {}
        if bo.get("p95_ms"):
            ch = (o["p95_ms"] - bo["p95_ms"]) * 100 / bo["p95_ms"]
            lines.append(f"Compared with the previous comparable run {bc['run']['run_id']}, the 95th percentile "
                         f"changed by {ch:+.0f}% ({_ms(bo['p95_ms'])} → {_ms(o['p95_ms'])})"
                         + (" (see the comparability notes)." if bc["differences"] else "."))
    return lines


def _requests_table(m: dict[str, Any], tx: set[str]) -> str:
    rows = []
    for r in m.get("requests") or []:
        rows.append([Markup(e(r["label"]) + (' <span class="muted">(transaction)</span>' if r["label"] in tx else "")),
                     f"{r['samples']:,}", f"{r['errors']:,}", f"{r['error_pct']}%", _ms(r["avg_ms"]), _ms(r["p50_ms"]),
                     _ms(r["p90_ms"]), _ms(r["p95_ms"]), _ms(r["p99_ms"]), _ms(r["max_ms"]), _num(r["throughput"], 2)])
    o = m.get("overall")
    if o:
        rows.append([Markup("<strong>All samples</strong>"), f"{o['samples']:,}", f"{o['errors']:,}", f"{o['error_pct']}%",
                     _ms(o["avg_ms"]), _ms(o["p50_ms"]), _ms(o["p90_ms"]), _ms(o["p95_ms"]), _ms(o["p99_ms"]),
                     _ms(o["max_ms"]), _num(o["throughput"], 2)])
    return _table(["Request", "Samples", "Errors", "Error %", "Avg", "50th", "90th", "95th", "99th", "Max", "Per s"],
                  rows).replace("<table>", '<table class="dense">', 1)


def _phase_table(ph: dict[str, Any]) -> str:
    return _table(["Period", "From", "To", "Samples", "Errors", "Avg", "95th", "Req/s"],
                  [[w["name"], _dur(w["start_s"]), _dur(w["end_s"]), f"{w['samples']:,}",
                    "—" if w["error_pct"] is None else f"{w['error_pct']}%", _ms(w["avg_ms"]), _ms(w["p95_ms"]),
                    _num(w["throughput"], 2)] for w in ph["windows"]])


def test_specific(ctx: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """(section html, findings, limitations) for the test type."""
    kind, ph = ctx["run"]["kind"], ctx["phases"]
    wins = [w for w in ph.get("windows", []) if w["samples"]]
    findings: list[str] = []
    limits: list[str] = []
    title = {"load": "Expected-workload behaviour", "stress": "Saturation and recovery",
             "spike": "Response to workload transitions", "soak": "Long-term trend and degradation"}.get(kind, "Phases")
    if not ph.get("available") or not wins:
        return (f"<h2>{e(title)}</h2><p class='muted'>Not available: the run has no samples to split by period.</p>",
                [], ["Test-specific analysis is missing because the run has no samples."])
    out = [f"<h2>{e(title)}</h2>"]
    if ctx["phase_source"] == "thirds":
        out.append("<p class='muted'>The script's duration depends on loop counts, so the run is split into thirds of "
                   "the observed time instead of planned periods.</p>")
        limits.append("Periods are thirds of the observed run, not planned load levels (the duration depends on "
                      "loop counts).")
    else:
        out.append("<p class='muted'>Periods follow the planned load, measured from the first sample. Requests are "
                   "counted at their start time; transaction samples are left out.</p>")
    out.append(_phase_table(ph))
    steady = [w for w in wins if not w["ramp"]]
    if kind == "load":
        if steady:
            s = max(steady, key=lambda w: w["covered_s"])
            out.append(f"<p>At the expected workload ({e(s['name'])}, {_dur(s['covered_s'])}): 95th percentile "
                       f"{_ms(s['p95_ms'])}, average {_ms(s['avg_ms'])}, errors {s['error_pct']}%, "
                       f"{_num(s['throughput'], 2)} requests per second.</p>")
    elif kind == "stress":
        levels = [w for w in steady if w["users_to"]]
        eff_rows = []
        sat = None
        for prev, cur in zip(levels, levels[1:]):
            if not prev["throughput"] or not cur["throughput"] or not prev["users_to"]:
                continue
            eff = (cur["throughput"] / prev["throughput"]) / (cur["users_to"] / prev["users_to"])
            p95_up = (cur["p95_ms"] or 0) > 1.5 * (prev["p95_ms"] or 0) and (cur["p95_ms"] or 0) - (prev["p95_ms"] or 0) >= 20
            eff_rows.append([f"{prev['users_to']:g} → {cur['users_to']:g} users", f"{eff:.2f}",
                             f"{_ms(prev['p95_ms'])} → {_ms(cur['p95_ms'])}",
                             f"{prev['error_pct']}% → {cur['error_pct']}%"])
            if sat is None and (eff < 0.5 or p95_up or (cur["error_pct"] or 0) > (prev["error_pct"] or 0) + 1):
                sat = (prev, cur, eff)
        if eff_rows:
            out.append("<h3>Scaling between steps</h3><p class='muted'>Scaling efficiency = (throughput ratio) ÷ "
                       "(users ratio); 1.00 means throughput grew in step with the users.</p>"
                       + _table(["Step", "Efficiency", "95th percentile", "Errors"], eff_rows))
        if sat:
            prev, cur, eff = sat
            text = (f"Signs of saturation between {prev['users_to']:g} and {cur['users_to']:g} users: scaling efficiency "
                    f"{eff:.2f}, 95th percentile {_ms(prev['p95_ms'])} → {_ms(cur['p95_ms'])}, errors "
                    f"{prev['error_pct']}% → {cur['error_pct']}%. Where the limit is (application, database, network "
                    "or this PC) is not determined from these results.")
            out.append(f"<p>{e(text)}</p>")
            findings.append(text)
        elif len(levels) > 1:
            text = (f"No saturation observed up to {levels[-1]['users_to']:g} users: throughput grew with the users and "
                    "response times stayed level.")
            out.append(f"<p>{e(text)}</p>")
            findings.append(text)
        out.append("<p class='muted'>Recovery after the load was not tested: the stress shape has no step down.</p>")
        limits.append("Recovery after stress was not tested (the shape only steps up).")
    elif kind == "spike":
        spike = max(steady, key=lambda w: w["users_to"] or 0) if steady else None
        before = [w for w in steady if w["end_s"] <= (spike or {}).get("start_s", 0) and w is not spike]
        after = [w for w in steady if spike and w["start_s"] >= spike["end_s"]]
        if spike and before:
            b = before[-1]
            text = (f"From {b['users_to']:g} to {spike['users_to']:g} users: 95th percentile {_ms(b['p95_ms'])} → "
                    f"{_ms(spike['p95_ms'])}, errors {b['error_pct']}% → {spike['error_pct']}%.")
            out.append(f"<p>{e(text)}</p>")
            findings.append(text)
        if spike and after and before:
            a, b = after[0], before[-1]
            recovered = (a["p95_ms"] or 0) <= 1.2 * (b["p95_ms"] or 0) + 5 and (a["error_pct"] or 0) <= (b["error_pct"] or 0) + 1
            text = (f"After the spike ({a['users_to']:g} users): 95th percentile {_ms(a['p95_ms'])}, errors "
                    f"{a['error_pct']}%: " + ("back to the level before the spike." if recovered
                                             else "not back to the level before the spike within the run."))
            out.append(f"<p>{e(text)}</p>")
            findings.append(text)
        elif spike:
            out.append("<p class='muted'>Recovery not observed: the run ended during or right after the spike.</p>")
            limits.append("Recovery after the spike was not observed.")
    elif kind == "soak":
        tr = ctx.get("trend")
        if tr and tr.get("available") and all(w["samples"] for w in tr["windows"]):
            first, last = tr["windows"][0], tr["windows"][-1]
            out.append("<h3>Trend over the hold</h3>" + _phase_table(tr))
            rise = (last["p95_ms"] - first["p95_ms"]) * 100 / first["p95_ms"] if first["p95_ms"] else 0
            err_rise = (last["error_pct"] or 0) - (first["error_pct"] or 0)
            if rise > 20 or err_rise > 1:
                text = (f"Degradation over the hold: 95th percentile {_ms(first['p95_ms'])} → {_ms(last['p95_ms'])} "
                        f"({rise:+.0f}%), errors {first['error_pct']}% → {last['error_pct']}%. A memory leak or other "
                        "cause is not confirmed: this needs memory and resource measurements from the servers.")
            else:
                text = (f"No degradation over the hold: 95th percentile {_ms(first['p95_ms'])} → {_ms(last['p95_ms'])} "
                        f"({rise:+.0f}%), errors {first['error_pct']}% → {last['error_pct']}%.")
            out.append(f"<p>{e(text)}</p>")
            findings.append(text)
            if (ctx["workload"].get("total_duration_s") or 0) < 3600:
                limits.append("The soak lasted less than an hour: slow degradation may not show yet.")
        else:
            out.append("<p class='muted'>The trend over the hold could not be measured (no planned hold, or a third of "
                       "it has no samples).</p>")
            limits.append("Soak trend not measured.")
    return "".join(out), findings, limits


def run_body(ctx: dict[str, Any], *, section: bool = False, generated: str = "") -> str:
    """The report of one run: every section in the order the report template asks for."""
    run, cfg, m, plan = ctx["run"], ctx["config"], ctx["metrics"], ctx["plan"]
    o = m.get("overall") or {}
    sla = run.get("sla") or {}
    tx = set(ctx["transactions"])
    env = cfg.get("environment") or {}
    sc = cfg.get("scenario") or {}
    wl = ctx["workload"]
    gen = ctx.get("generator") or {}
    kind_title = ("One-user validation run" if ctx["validation_run"] else KIND_TITLES.get(run["kind"], run["kind"]))
    title = f"{kind_title}: {run['name']}"
    facts = [("Run ID", run["run_id"]), ("Test type", kind_title),
             ("Started", local_time(run.get("started_at"))), ("Ended", local_time(run.get("finished_at"))),
             ("Execution state", STATE_TEXT.get(run["state"], run["state"]) + (" (partial results)" if run.get("partial") else "")),
             ("Performance verdict", VERDICT_TEXT.get(run.get("verdict"), run.get("verdict") or "—")),
             ("Started by", {"schedule": "Schedule", "voice": "Voice command"}.get(run.get("trigger") or "", "Manual"))]
    out = [f'<section class="{"run" if section else "single"}">']
    out.append(cover(title, facts, generated) if not section else
               f"<h1 class='run-title'>{e(title)}</h1>" + _kv([[k, v] for k, v in facts]))
    findings: list[str] = []
    limits: list[str] = []

    out.append("<h2>Executive summary</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in executive_summary(ctx)) + "</ul>")

    out.append("<h2>Objective and scope</h2>")
    objective = sc.get("description") or ""
    ac = sc.get("acceptance_criteria") or ""
    out.append(f"<p>{e(objective)}</p>" if objective else "<p class='muted'>No objective was written on the scenario.</p>")
    if ac:
        out.append(f"<p><strong>Acceptance criteria (scenario):</strong> {e(ac)}</p>")
    req_labels = [r["label"] for r in m.get("requests") or [] if r["label"] not in tx]
    out.append(f"<p>Scope: {len(req_labels)} request(s)" + (f" in {len(tx)} transaction(s)" if tx else "")
               + (f", scenario {e(sc.get('name'))}" if sc.get("name") else "")
               + (f", user story {e(sc.get('user_story_id'))}" if sc.get("user_story_id") else "") + ".</p>")

    out.append("<h2>Target environment and application build</h2>")
    out.append(_kv([["Environment", env.get("name") or "Not set"],
                    ["Base URL", env.get("base_url") or "—"],
                    ["Allowed hosts", ", ".join(env.get("allowed_hosts") or []) or "Any"],
                    ["Application build", ctx["build"] or "Not recorded"],
                    ["Load generator", "This PC (one JMeter instance)"]]))
    if not ctx["build"]:
        limits.append("The application build under test was not recorded for this run.")

    out.append("<h2>Script, data and workload</h2>")
    script = cfg.get("script") or {}
    data = cfg.get("test_data") or []
    tools = cfg.get("tools") or {}
    rows = [["Script", f"{plan.get('name') or run['name']} · version {script.get('version', '—')}"
                       + (f" · SHA-256 {script['sha256'][:12]}…" if script.get("sha256") else "")],
            ["Executed JMX", f"plan.jmx · SHA-256 {(cfg.get('jmx_sha256') or '')[:12]}…" if cfg.get("jmx_sha256") else "—"],
            ["Test data", "; ".join(f"{d.get('name')} ({d.get('id')}) version {d.get('version')}, {d.get('rows')} rows"
                                   for d in data) or "None"],
            ["Workload", wl.get("load_model") or "—"],
            ["Planned peak users", wl.get("peak_concurrent_users", "—")],
            ["Planned duration", _dur(wl.get("total_duration_s")) if wl.get("total_duration_s") else "Depends on loop counts"],
            ["Tools", f"JMeter {tools.get('jmeter_version', '—')} · {tools.get('java_version', 'Java —')} · "
                      f"QA Enterprise AI {cfg.get('application_version', '—')}"]]
    shape = wl.get("shape") or {}
    if shape:
        rows.insert(4, ["Workload shape", shape_text(shape)])
    out.append(_kv(rows))

    out.append("<h2>Actual load and duration</h2>")
    if o.get("samples"):
        rate = m.get("rate")
        out.append('<div class="kpis">' + kpi("Peak active users", m.get("peak_threads"), "concurrent threads JMeter reported")
                   + kpi("Duration", _dur(m.get("duration_s")), "first sample to last")
                   + kpi("Samples", f"{o['samples']:,}", f"{o['errors']:,} errors")
                   + kpi("Throughput", f"{o['throughput']} /s", "all samples")
                   + (kpi("Request rate", f"{rate['achieved_rps']} req/s", f"target {rate['target_rps']} req/s")
                      if rate and rate.get("achieved_rps") is not None else "") + "</div>")
        if tx:
            out.append(f"<p class='muted'>The overall figures include {len(tx)} transaction label(s) "
                       f"({e(', '.join(sorted(tx)))}): JMeter records a transaction as a sample too.</p>")
    else:
        out.append("<p class='muted'>No samples were recorded.</p>")
        limits.append("No samples: there are no measured results.")

    out.append("<h2>Performance targets and acceptance criteria</h2>")
    checks = sla.get("checks") or []
    if checks:
        out.append(_table(["Target", "Scope", "Limit", "Actual", "Result"],
                          [[c["name"], c["scope"], f"{'≥' if c['name'] == 'Throughput' else '≤'} {c['limit']} {c['unit']}",
                            f"{c['actual']} {c['unit']}", Markup(pill(c["status"]))] for c in checks]))
        if run.get("verdict") == "INCONCLUSIVE":
            out.append("<p class='note'>Shown for reference: the run did not complete, so it cannot pass or fail.</p>")
        for c in checks:
            if c["status"] == "FAIL":
                findings.append(f"Target not met: {c['name']} ({c['scope']}) {c['actual']} {c['unit']} against a limit of "
                                f"{c['limit']} {c['unit']}.")
    else:
        targets = {k: v for k, v in (cfg.get("targets") or {}).items() if v is not None}
        out.append("<p class='muted'>" + ("No targets were evaluated." if targets else
                                          "Target not specified: no limits were set, so there is no PASS or FAIL.") + "</p>")
        if not ctx["validation_run"]:
            limits.append("No performance targets were set, so the run has no pass or fail verdict.")

    out.append("<h2>Transaction and request results</h2>")
    if m.get("requests"):
        out.append(_requests_table(m, tx))
        errs = [(r, r.get("top_errors") or []) for r in m["requests"] if r["errors"]]
        if errs:
            out.append("<h3>Errors and assertion failures</h3>"
                       + _table(["Request", "Error or assertion message", "Count"],
                                [[r["label"], x["error"], x["count"]] for r, xs in errs for x in xs[:5]], num_from=2))
            worst = max(errs, key=lambda p: p[0]["errors"])[0]
            findings.append(f"Most errors on '{worst['label']}': {worst['errors']:,} of {worst['samples']:,} "
                            f"({worst['error_pct']}%)" + (f", most often '{worst['top_errors'][0]['error']}'."
                                                         if worst.get("top_errors") else "."))
        slow = max((r for r in m["requests"] if r["label"] not in tx), key=lambda r: r["p95_ms"], default=None)
        if slow:
            findings.append(f"Slowest request at the 95th percentile: '{slow['label']}' {_ms(slow['p95_ms'])} "
                            f"(average {_ms(slow['avg_ms'])}, {slow['samples']:,} samples).")
    else:
        out.append("<p class='muted'>No request results.</p>")

    tl = m.get("timeline") or []
    if tl:
        step = _interval(tl)
        out.append(f"<h2>Charts</h2><p class='muted'>One point per {step} s of the run.</p><div class='charts'>"
                   + _chart(tl, "avg_ms", "Average response time (ms)", "#2563eb", "average response time, ms")
                   + _chart(tl, "throughput", "Throughput (samples per second)", "#0d9488", "samples per second")
                   + _chart(tl, "errors", f"Errors per {step} s", "#dc2626", f"failed samples per {step} s")
                   + _chart(tl, "threads", "Active users", "#7c3aed", "concurrent users (threads)") + "</div>")
        first_err = next((p for p in tl if p.get("errors")), None)
        if first_err:
            findings.append(f"Errors first appeared {_dur(first_err['t'])} into the run, at "
                            f"{first_err.get('threads')} active users.")

    out.append("<h2>Telemetry</h2>")
    if gen.get("available"):
        st = gen.get("stats") or {}
        out.append("<p><strong>Load generator (this PC)</strong></p>" + _kv(
            [["CPU average / maximum", f"{st.get('cpu_avg_pct', '—')}% / {st.get('cpu_max_pct', '—')}%"],
             ["Memory maximum", f"{st.get('mem_max_pct', '—')}%"],
             ["JMeter memory maximum", f"{st.get('jmeter_mem_max_mb', '—')} MB"],
             ["Saturated", "Yes" if gen.get("saturated") else "No"]]))
        if gen.get("samples") and len(gen["samples"]) > 1:
            out.append("<div class='charts'>"
                       + _chart(gen["samples"], "cpu_pct", "Generator CPU (% of all cores)", "#ea580c", "CPU %")
                       + _chart(gen["samples"], "mem_pct", "Generator memory (% used)", "#475569", "memory %") + "</div>")
        for f in gen.get("flags") or []:
            findings.append(f"Load generator: {f.get('message')}.")
            limits.append("The load generator was saturated during part of the run; response times measured then "
                          "can be too high.")
    else:
        out.append(f"<p class='muted'>{e(gen.get('reason') or 'The load generator was not monitored.')}</p>")
        limits.append("The load generator was not monitored.")
    out.append("<p class='muted'>Target telemetry (server CPU, memory, database) is not collected by this application."
               "</p>")
    limits.append("No server-side telemetry: causes on the servers under test cannot be confirmed from these results.")

    spec_html, spec_findings, spec_limits = test_specific(ctx) if not ctx["validation_run"] else ("", [], [])
    out.append(spec_html)
    findings += spec_findings
    limits += spec_limits

    out.append("<h2>Baseline comparison</h2>")
    bc = ctx.get("baseline_cmp")
    if bc:
        b = bc["run"]
        bo, bm = (b.get("metrics") or {}).get("overall") or {}, b.get("metrics") or {}
        rows = []
        for label, key, fmt in (("Samples", "samples", lambda v: f"{v:,}"), ("Average", "avg_ms", _ms),
                                ("95th percentile", "p95_ms", _ms), ("99th percentile", "p99_ms", _ms),
                                ("Error %", "error_pct", lambda v: f"{v}%"), ("Throughput /s", "throughput", lambda v: _num(v, 2))):
            av, bv = o.get(key), bo.get(key)
            ch = f"{(av - bv) * 100 / bv:+.0f}%" if av is not None and bv else "—"
            rows.append([label, fmt(bv) if bv is not None else "—", fmt(av) if av is not None else "—", ch])
        rows.append(["Peak users", bm.get("peak_threads", "—"), m.get("peak_threads", "—"), ""])
        out.append(f"<p>Previous comparable run: {e(b['run_id'])}, {e(local_time(b.get('started_at')))}, verdict "
                   f"{e(VERDICT_TEXT.get(b.get('verdict'), b.get('verdict')))}.</p>"
                   + _table(["Measure", "Baseline", "This run", "Change"], rows))
        if bc["differences"]:
            out.append("<p><strong>Comparability notes</strong></p><ul>" + "".join(
                f"<li>{pill(d['level'].upper(), {'blocks': 'FAIL', 'caution': 'WARN'}.get(d['level'], 'WAITING'))} {e(d['text'])}</li>"
                for d in bc["differences"]) + "</ul>")
    else:
        out.append("<p class='muted'>No earlier completed run of the same scenario and test type to compare with.</p>")

    out.append("<h2>Findings</h2>")
    out.append("<p class='muted'>Each finding states the measurement it rests on. Causes are not stated unless measured."
               "</p>" + ("<ol>" + "".join(f"<li>{e(f)}</li>" for f in findings) + "</ol>" if findings
                         else "<p>No findings: no target failed, no errors and no saturation were measured.</p>"))

    out.append("<h2>AI recommendations (analysis, not measurements)</h2>")
    an = run.get("analysis")
    if an:
        out.append(f"<p>{e(an.get('summary'))}</p>")
        for key, label in (("observations", "Observations"), ("suspected_bottlenecks", "Suspected bottlenecks (not confirmed)"),
                           ("recommendations", "Recommendations"), ("undetermined", "Not determined from these results")):
            if an.get(key):
                out.append(f"<p><strong>{label}</strong></p><ul>" + "".join(f"<li>{e(x)}</li>" for x in an[key]) + "</ul>")
        out.append(f"<p class='muted'>Written by the On-Premise AI ({e(an.get('model') or 'local model')}) from the "
                   f"measured values on {e(local_time(an.get('created_at')))}. It is analysis, not measurement.</p>")
    else:
        out.append("<p class='muted'>No AI analysis was requested for this run.</p>")

    out.append("<h2>Defect references</h2>")
    if ctx["defects"]:
        out.append(_table(["Draft", "Title", "Severity", "Status", "TFS"],
                          [[f"#{d['id']}", d["title"], d.get("severity") or "—", (d.get("status") or "").replace("_", " "),
                            d.get("tfs_id") or "—"] for d in ctx["defects"]], num_from=99))
    else:
        out.append("<p class='muted'>No performance defect was drafted for this run.</p>")

    out.append("<h2>Result files</h2>")
    files = ctx["files"]
    named = [(f, d) for f, d in (("results.jtl", "every sample (JMeter results)"), ("plan.jmx", "the JMX JMeter executed"),
                                 ("script.jmx", "the script as saved"), ("config.json", "the exact configuration"),
                                 ("metrics.json", "computed metrics"), ("jmeter.log", "JMeter log"),
                                 ("console.log", "JMeter console output"), ("generator.json", "generator telemetry"))
             if f in files]
    out.append(_table(["File in the run folder", "Content"], [[f, d] for f, d in named]
                      + ([["report/index.html", "JMeter HTML dashboard"]] if ctx["has_jmeter_report"] else []),
                      num_from=99))

    out.append("<h2>Limitations and missing information</h2>")
    if ctx["validation_run"]:
        limits.insert(0, "This is a one-user validation run: it checks that the script works, not performance.")
    if run.get("partial") or run["state"] != "COMPLETED":
        limits.insert(0, f"Execution {STATE_TEXT.get(run['state'], run['state']).lower()}"
                         + (f": {run.get('state_reason') or run.get('error')}" if run.get("state_reason") or run.get("error") else "")
                         + ". Evidence is incomplete.")
    if not an:
        limits.append("No AI analysis was requested.")
    seen: list[str] = []
    for x in limits:
        if x not in seen:
            seen.append(x)
    out.append("<ul>" + "".join(f"<li>{e(x)}</li>" for x in seen) + "</ul>")

    out.append("<h2>Execution state and performance verdict</h2>")
    out.append(_kv([["Execution state", Markup(pill(STATE_TEXT.get(run['state'], run['state']),
                                                                 {'COMPLETED': 'PASS', 'FAILED': 'FAIL'}.get(run['state'], 'WARN')))],
                                 ["Performance verdict", Markup(pill(VERDICT_TEXT.get(run.get('verdict'), '—'),
                                                                     {'PASS': 'PASS', 'FAIL': 'FAIL'}.get(run.get('verdict') or '', 'WAITING')))],
                                 ["Why", run.get("state_reason") or run.get("error") or
                                  ("All targets met." if run.get("verdict") == "PASS" else
                                   "At least one target not met." if run.get("verdict") == "FAIL" else
                                   "No targets set." if run.get("verdict") == "NOT_SPECIFIED" else "—")]]))
    out.append("<p class='muted'>The execution state says how the run ended; the verdict says whether the measured "
               "results met the targets. A run that did not complete has no pass or fail verdict.</p>")
    out.append("</section>")
    return "".join(out)


EXTRA_CSS = """
body, table, td, th { font-variant-ligatures: none; font-feature-settings: "liga" 0, "clig" 0; }
table.kv th { width: 30%; text-transform: none; letter-spacing: 0; font-size: 12px; font-weight: 600; color: var(--text); }
table.dense th, table.dense td { padding: 5px 5px; font-size: 11.5px; }
td.num { white-space: nowrap; }
table.facts { width: auto; min-width: 60%; }
table.facts th { width: 180px; text-transform: none; letter-spacing: 0; font-size: 12px; }
h1.run-title { font-size: 20px; margin: 0 0 8px; }
section.run { break-before: page; }
section.single h2:first-of-type { margin-top: 18px; }
ol li, ul li { margin: 3px 0; }
"""


def page(title: str, body: str) -> str:
    from .service import CSS
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width, initial-scale=1"><title>{e(title)}</title><style>{CSS}{EXTRA_CSS}</style>'
            f'</head><body><main>{body}</main></body></html>')


def run_report_html(ctx: dict[str, Any], generated: str) -> str:
    run = ctx["run"]
    return page(f"Performance report {run['run_id']}", run_body(ctx, generated=generated))


# ---- comparison and consolidated reports ---------------------------------------------------------------------------

def compare_data(ctxs: list[dict[str, Any]]) -> dict[str, Any]:
    """Side by side: the first run is the baseline. Differences that affect comparability are listed per pair."""
    base = ctxs[0]
    runs = []
    for c in ctxs:
        r, m = c["run"], c["metrics"]
        o = m.get("overall") or {}
        runs.append({"run_id": r["run_id"], "name": r["name"], "kind": r["kind"], "state": r["state"],
                     "verdict": r.get("verdict"), "partial": bool(r.get("partial")), "started_at": r.get("started_at"),
                     "build": c["build"], "environment": (c["config"].get("environment") or {}).get("name"),
                     "peak_users": m.get("peak_threads"), "duration_s": m.get("duration_s"),
                     "samples": o.get("samples"), "avg_ms": o.get("avg_ms"), "p90_ms": o.get("p90_ms"),
                     "p95_ms": o.get("p95_ms"), "p99_ms": o.get("p99_ms"), "error_pct": o.get("error_pct"),
                     "throughput": o.get("throughput"),
                     "targets": {f"{ch['name']} ({ch['scope']})": ch["status"] for ch in (r.get("sla") or {}).get("checks") or []}})
    labels: list[str] = []
    for c in ctxs:
        for rq in c["metrics"].get("requests") or []:
            if rq["label"] not in labels:
                labels.append(rq["label"])
    per_request = []
    for lb in labels:
        row = {"label": lb, "values": []}
        for c in ctxs:
            rq = next((x for x in c["metrics"].get("requests") or [] if x["label"] == lb), None)
            row["values"].append({"p95_ms": rq["p95_ms"], "error_pct": rq["error_pct"], "samples": rq["samples"]}
                                 if rq else None)
        per_request.append(row)
    pairs = []
    for c in ctxs[1:]:
        pairs.append({"run_id": c["run"]["run_id"], "baseline": base["run"]["run_id"],
                      "differences": comparability(base, c)})
    blocking = any(d["level"] == "blocks" for p in pairs for d in p["differences"])
    return {"runs": runs, "per_request": per_request, "pairs": pairs, "comparable": not blocking}


def _cmp_cover(title: str, ctxs: list[dict[str, Any]], generated: str, what: str) -> str:
    rows = [(c["run"]["run_id"], f"{KIND_TITLES.get(c['run']['kind'], c['run']['kind'])} · {c['run']['name']} · "
                                  f"{local_time(c['run'].get('started_at'))}") for c in ctxs]
    return cover(title, [("Report", what)] + rows, generated)


def _overview_table(data: dict[str, Any]) -> str:
    runs = data["runs"]
    head = ["Measure"] + [r["run_id"] for r in runs]
    base = runs[0]

    def delta(r: dict[str, Any], key: str) -> str:
        if r is base or r.get(key) is None or not base.get(key):
            return ""
        return f" ({(r[key] - base[key]) * 100 / base[key]:+.0f}%)"
    rows = [["Test type"] + [KIND_TITLES.get(r["kind"], r["kind"]) for r in runs],
            ["Started"] + [local_time(r["started_at"]) for r in runs],
            ["Environment"] + [r["environment"] or "—" for r in runs],
            ["Application build"] + [r["build"] or "Not recorded" for r in runs],
            ["Execution state"] + [STATE_TEXT.get(r["state"], r["state"]) + (" (partial)" if r["partial"] else "") for r in runs],
            ["Verdict"] + [VERDICT_TEXT.get(r["verdict"], r["verdict"] or "—") for r in runs],
            ["Peak users"] + [r["peak_users"] if r["peak_users"] is not None else "—" for r in runs],
            ["Duration"] + [_dur(r["duration_s"]) for r in runs],
            ["Samples"] + [f"{r['samples']:,}" if r["samples"] is not None else "—" for r in runs],
            ["Average"] + [_ms(r["avg_ms"]) + delta(r, "avg_ms") for r in runs],
            ["90th percentile"] + [_ms(r["p90_ms"]) + delta(r, "p90_ms") for r in runs],
            ["95th percentile"] + [_ms(r["p95_ms"]) + delta(r, "p95_ms") for r in runs],
            ["99th percentile"] + [_ms(r["p99_ms"]) + delta(r, "p99_ms") for r in runs],
            ["Error %"] + [("—" if r["error_pct"] is None else f"{r['error_pct']}%") for r in runs],
            ["Throughput /s"] + [_num(r["throughput"], 2) + delta(r, "throughput") for r in runs]]
    return _table(head, rows)


def _per_run_chart(runs: list[dict[str, Any]], key: str, title: str, color: str, legend: str) -> str:
    pts = [(i, float(r[key])) for i, r in enumerate(runs) if r.get(key) is not None]
    if not pts:
        return f'<div class="chart"><div class="chart-title">{e(title)}</div><p class="muted">No data.</p></div>'
    labels = [f"Run {i + 1}" for i, _ in pts]
    tips = [f"{runs[i]['run_id']}: {v:g}" for i, v in pts]
    c = charts._line([(n, v) for n, (_i, v) in enumerate(pts)], labels, title, color, max(1, len(pts) - 1), tips)
    return c[:-6] + f'<div class="legend"><span><i style="background:{color}"></i>{e(legend)}</span></div></div>'


def _run_charts(data: dict[str, Any]) -> str:
    runs = data["runs"]
    return ("<div class='charts'>" + _per_run_chart(runs, "p95_ms", "95th percentile per run (ms)", COLORS[0], "ms")
            + _per_run_chart(runs, "error_pct", "Error rate per run (%)", "#dc2626", "% of samples failed")
            + _per_run_chart(runs, "throughput", "Throughput per run (samples per second)", COLORS[2], "samples per s")
            + _per_run_chart(runs, "avg_ms", "Average response time per run (ms)", COLORS[3], "ms") + "</div>"
            + "<p class='muted'>x axis: " + e("; ".join(f"Run {i + 1} = {r['run_id']}" for i, r in enumerate(runs)))
            + ".</p>")


def comparison_html(ctxs: list[dict[str, Any]], title: str, generated: str) -> str:
    data = compare_data(ctxs)
    out = [_cmp_cover(title, ctxs, generated, f"Comparison of {len(ctxs)} runs; {ctxs[0]['run']['run_id']} is the baseline")]
    out.append("<h2>Executive summary</h2><ul>")
    base, runs = data["runs"][0], data["runs"]
    for r in runs[1:]:
        if base["p95_ms"] and r["p95_ms"] is not None:
            out.append(f"<li>{e(r['run_id'])}: 95th percentile {_ms(base['p95_ms'])} → {_ms(r['p95_ms'])} "
                       f"({(r['p95_ms'] - base['p95_ms']) * 100 / base['p95_ms']:+.0f}%), errors {base['error_pct']}% → "
                       f"{r['error_pct']}%, throughput {_num(base['throughput'], 2)} → {_num(r['throughput'], 2)} per s.</li>")
    out.append(f"<li>{'The runs are comparable, with the cautions listed below.' if data['comparable'] else 'Some runs are not comparable: see the differences marked BLOCKS before drawing conclusions.'}</li></ul>")
    out.append("<h2>Comparability</h2>")
    for p in data["pairs"]:
        out.append(f"<h3>{e(p['run_id'])} against {e(p['baseline'])}</h3>")
        out.append("<ul>" + "".join(
            f"<li>{pill(d['level'].upper(), {'blocks': 'FAIL', 'caution': 'WARN'}.get(d['level'], 'WAITING'))} {e(d['text'])}</li>"
            for d in p["differences"]) + "</ul>" if p["differences"] else
            "<p>Same script, JMX, environment, workload, data and targets; both completed.</p>")
    out.append("<h2>Results side by side</h2>" + _overview_table(data))
    if len(runs) > 1:
        out.append("<h2>Charts</h2>" + _run_charts(data))
    out.append("<h2>Per request, 95th percentile</h2>")
    rows = []
    for pr in data["per_request"]:
        cells = []
        for v in pr["values"]:
            cells.append("—" if v is None else f"{_ms(v['p95_ms'])} · {v['error_pct']}%")
        rows.append([pr["label"]] + cells)
    out.append(_table(["Request"] + [r["run_id"] for r in runs], rows) + "<p class='muted'>Each cell: 95th percentile · error %. "
               "— means the request is not in that run.</p>")
    targets = sorted({k for r in runs for k in r["targets"]})
    if targets:
        out.append("<h2>Targets</h2>" + _table(["Target"] + [r["run_id"] for r in runs],
                                               [[t] + [Markup(pill(r["targets"].get(t))) if r["targets"].get(t) else "—"
                                                       for r in runs] for t in targets]))
    out.append("<h2>Limitations</h2><ul><li>The comparison uses each run's own results; it does not re-run anything."
               "</li><li>Differences in server-side conditions (other load, data volume, deployments) are not measured "
               "by this application.</li>" + "".join(f"<li>{e(r['run_id'])} did not complete: its numbers are partial.</li>"
                                                      for r in runs if r["state"] != "COMPLETED" or r["partial"]) + "</ul>")
    return page(title, "".join(out))


def consolidated_html(ctxs: list[dict[str, Any]], title: str, generated: str) -> str:
    data = compare_data(ctxs)
    kinds = sorted({c["run"]["kind"] for c in ctxs}, key=lambda k: list(KIND_TITLES).index(k) if k in KIND_TITLES else 9)
    out = [_cmp_cover(title, ctxs, generated, "Consolidated report: " + ", ".join(KIND_TITLES.get(k, k) for k in kinds))]
    out.append("<h2>Executive summary</h2><ul>")
    for c in ctxs:
        out.append("<li>" + e(executive_summary(c)[0]) + "</li>")
    failed = [r for r in data["runs"] if r["verdict"] == "FAIL"]
    out.append(f"<li>{len(failed)} of {len(ctxs)} runs failed their targets." if failed else
               f"<li>No run failed its targets ({len(ctxs)} runs).")
    out.append("</li></ul>")
    out.append("<h2>Overview</h2>" + _overview_table(data))
    if len(ctxs) > 1:
        out.append(_run_charts(data))
    out.append("<p class='muted'>Different test types put different loads on purpose; compare each run against its own "
               "targets. The full report of each run follows.</p>")
    for c in ctxs:
        out.append(run_body(c, section=True, generated=generated))
    return page(title, "".join(out))


def pdf_text(path: Path) -> str:
    """The text of a PDF (for the secret scan and the tests)."""
    reader = _reader(path)
    return "\n".join(p.extract_text() or "" for p in reader.pages)


def pdf_pages(path: Path) -> int:
    return len(_reader(path).pages)


def _reader(path: Path) -> Any:
    import sys
    from ..automation.files import VENDOR
    if str(VENDOR) not in sys.path:
        sys.path.append(str(VENDOR))  # a pypdf installed on the PC wins; the shipped one is the fallback
    from pypdf import PdfReader
    return PdfReader(str(path))


def plain(text: str) -> str:
    """Letters and digits only. Text read back from a PDF can map punctuation to other characters (it depends on
    the font), and some fonts join letters such as "fi" into one ligature character, so the text is normalised
    (NFKC turns a ligature back into its letters) and comparisons are made on letters and digits."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if ch.isascii() and ch.isalnum()).lower()


def secret_scan(text: str, secrets: list[str]) -> list[str]:
    """Secrets (stored values) that appear in the text, ignoring punctuation and spaces. Values with fewer than six
    letters and digits are skipped: they match ordinary words."""
    flat = plain(text)
    return [s for s in secrets if s and len(plain(s)) >= 6 and plain(s) in flat]

