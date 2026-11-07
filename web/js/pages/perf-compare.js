// 23.3.28: performance reports from saved results: the run report (PDF), run-to-run comparison with what differs
// in a way that affects comparing, and consolidated reports across test types. Nothing here re-runs a test.

import { api } from "../api.js";
import { esc, toast, dateTimeOf } from "../util.js";

const FINAL = ["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "SKIPPED"];
const KINDS = { load: "Load", stress: "Stress", spike: "Spike", soak: "Soak" };
const STATE = { COMPLETED: "Completed", FAILED: "Failed", CANCELLED: "Stopped", INTERRUPTED: "Interrupted", SKIPPED: "Skipped" };
const VERDICT = { PASS: "Targets met", FAIL: "Targets missed", NOT_SPECIFIED: "Target not specified", INCONCLUSIVE: "Inconclusive" };
const LEVEL = { blocks: ["FAIL", "Not comparable"], caution: ["WARN", "Caution"], info: ["WAITING", "Note"] };
const ms = (v) => (v === null || v === undefined ? "–" : `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 })} ms`);
const pct = (a, b) => (a === null || a === undefined || !b ? "" : ` <span class="muted">(${a - b >= 0 ? "+" : ""}${Math.round(((a - b) * 100) / b)}%)</span>`);

export function reportButtons(run) {
  if (!FINAL.includes(run.state)) return "";
  const url = `/api/performance/runs/${encodeURIComponent(run.run_id)}/report`;
  return `<div class="row" style="margin-top:8px;flex-wrap:wrap"><strong>Run report</strong>
    <a class="btn" href="${url}" target="_blank" rel="noopener" title="All sections: summary, environment and build, workload, targets, results, charts, telemetry, test-specific analysis, baseline, findings, limitations">Open PDF</a>
    <a class="btn" href="${url}?format=html" target="_blank" rel="noopener">Open as a page</a>
    <a class="btn" href="${url}?download=true">Download PDF</a>
    <button class="btn" id="remakeReport" title="Made again from the saved results; nothing is re-run">Make again</button>
    <span class="muted" id="reportNote">${run.files.includes("qa-report.pdf") ? "Made after the run." : "Not made yet: it is made when you open it."}</span></div>`;
}

export function bindReportButtons(view, run) {
  view.querySelector("#remakeReport")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    const note = view.querySelector("#reportNote");
    note.textContent = "Making the report from the saved results…";
    try {
      const r = await api(`/api/performance/runs/${encodeURIComponent(run.run_id)}/report`, { method: "POST", body: {} });
      note.textContent = r.pdf ? `Made again: ${r.pages} page${r.pages === 1 ? "" : "s"}.` : `The page was made; the PDF was not: ${r.error}`;
      if (r.pdf) toast("Run report made again", "success"); else toast(r.error, "error");
    } catch (err) { note.textContent = err.message; toast(err.message, "error"); }
    finally { e.target.disabled = false; }
  });
}

export async function makePerfReport(btn, kind, runIds, title = "") {
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = "Making the PDF…";
  try {
    const { report } = await api("/api/performance/reports", { method: "POST", body: { kind, run_ids: runIds, title } });
    if (report.has_pdf) {
      window.open(`/api/reports/${encodeURIComponent(report.id)}/pdf`, "_blank", "noopener");
      toast(`${kind === "comparison" ? "Comparison" : "Consolidated"} report ready (also under Reports)`, "success");
    } else toast(report.summary?.pdf_error || "The PDF could not be made; the report page is under Reports.", "error");
  } catch (err) { toast(err.message, "error"); }
  finally { btn.disabled = false; btn.textContent = label; }
}

export async function compareView(view, ids) {
  const data = await api("/api/performance/compare", { method: "POST", body: { run_ids: ids } });
  const runs = data.runs, base = runs[0];
  const row = (label, fn) => `<tr><th>${esc(label)}</th>${runs.map((r) => `<td>${fn(r)}</td>`).join("")}</tr>`;
  const targets = [...new Set(runs.flatMap((r) => Object.keys(r.targets)))].sort();
  view.innerHTML = `
    <p><a href="#/perf-results">← Performance Results</a></p>
    <div class="card"><div class="row" style="flex-wrap:wrap"><h2 style="margin:0">Compare ${runs.length} runs</h2>
      ${data.comparable ? '<span class="pill PASS">Comparable</span>' : '<span class="pill FAIL">Not all comparable</span>'}<span class="spacer"></span>
      <button class="btn" id="cmpPdf">Comparison PDF</button><button class="btn" id="consPdf">Consolidated PDF</button></div>
      <p class="muted">${esc(base.run_id)} is the baseline; changes are against it. Figures come from each run's saved results.</p></div>
    <div class="card" style="margin-top:12px"><h3 style="margin-top:0">What differs</h3>
      ${data.pairs.map((p) => `<h4>${esc(p.run_id)} against ${esc(p.baseline)}</h4>${p.differences.length
        ? `<ul class="cmp-diffs">${p.differences.map((d) => `<li><span class="pill ${LEVEL[d.level][0]}">${LEVEL[d.level][1]}</span> ${esc(d.text)}</li>`).join("")}</ul>`
        : '<p class="muted">Same script, JMX, environment, workload, data and targets; both completed.</p>'}`).join("")}</div>
    <div class="card" style="margin-top:12px;overflow-x:auto"><h3 style="margin-top:0">Side by side</h3>
      <table class="data cmp-table"><thead><tr><th></th>${runs.map((r) => `<th><a href="#/perf-results?run=${encodeURIComponent(r.run_id)}">${esc(r.run_id)}</a></th>`).join("")}</tr></thead><tbody>
        ${row("Test type", (r) => esc(KINDS[r.kind] || r.kind))}
        ${row("Started", (r) => esc(dateTimeOf(r.started_at)))}
        ${row("Environment", (r) => esc(r.environment || "–"))}
        ${row("Build", (r) => esc(r.build || "not recorded"))}
        ${row("State", (r) => `${esc(STATE[r.state] || r.state)}${r.partial ? " (partial)" : ""}`)}
        ${row("Verdict", (r) => esc(VERDICT[r.verdict] || "–"))}
        ${row("Peak users", (r) => esc(r.peak_users ?? "–"))}
        ${row("Samples", (r) => (r.samples ?? 0).toLocaleString())}
        ${row("Average", (r) => ms(r.avg_ms) + (r === base ? "" : pct(r.avg_ms, base.avg_ms)))}
        ${row("95th percentile", (r) => ms(r.p95_ms) + (r === base ? "" : pct(r.p95_ms, base.p95_ms)))}
        ${row("99th percentile", (r) => ms(r.p99_ms) + (r === base ? "" : pct(r.p99_ms, base.p99_ms)))}
        ${row("Error rate", (r) => (r.error_pct === null ? "–" : `${r.error_pct}%`))}
        ${row("Throughput", (r) => `${r.throughput ?? "–"}/s` + (r === base ? "" : pct(r.throughput, base.throughput)))}
      </tbody></table></div>
    <div class="card" style="margin-top:12px;overflow-x:auto"><h3 style="margin-top:0">Per request: 95th percentile and errors</h3>
      <table class="data"><thead><tr><th>Request</th>${runs.map((r) => `<th>${esc(r.run_id)}</th>`).join("")}</tr></thead><tbody>
        ${data.per_request.map((pr) => `<tr><td>${esc(pr.label)}</td>${pr.values.map((v, i) => `<td>${v ? `${ms(v.p95_ms)}${i ? pct(v.p95_ms, pr.values[0]?.p95_ms) : ""} · ${v.error_pct}%` : '<span class="muted">not in this run</span>'}</td>`).join("")}</tr>`).join("")}
      </tbody></table></div>
    ${targets.length ? `<div class="card" style="margin-top:12px;overflow-x:auto"><h3 style="margin-top:0">Targets</h3><table class="data"><thead><tr><th>Target</th>${runs.map((r) => `<th>${esc(r.run_id)}</th>`).join("")}</tr></thead><tbody>
      ${targets.map((t) => `<tr><td>${esc(t)}</td>${runs.map((r) => `<td>${r.targets[t] ? `<span class="pill ${esc(r.targets[t])}">${esc(r.targets[t])}</span>` : "–"}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : ""}`;
  view.querySelector("#cmpPdf").addEventListener("click", (e) => makePerfReport(e.target, "comparison", ids));
  view.querySelector("#consPdf").addEventListener("click", (e) => makePerfReport(e.target, "consolidated", ids));
}
