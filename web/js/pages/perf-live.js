// 23.3.27: live dashboard of a running performance test. Every figure is read from the results JMeter has written
// so far (refreshed every 2 seconds); nothing is estimated. The verdict comes only from the full results after the
// run, so targets show here as "now within / above", not as pass or fail.

import { api } from "../api.js";
import { esc, toast, dateTimeOf } from "../util.js";
import { fmtDuration, lineChart } from "../charts.js";

const ACTIVE = ["VALIDATING", "RUNNING", "STOPPING"];
const FINAL = ["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "SKIPPED"];
const STATE_PILL = { QUEUED: "WAITING", VALIDATING: "RUNNING", RUNNING: "RUNNING", STOPPING: "RUNNING", COMPLETED: "PASS",
  FAILED: "FAIL", CANCELLED: "WARN", INTERRUPTED: "WARN", SKIPPED: "WARN" };
const STATE_LABEL = { QUEUED: "Queued", VALIDATING: "Pre-run checks", RUNNING: "Running", STOPPING: "Stopping", COMPLETED: "Completed",
  FAILED: "Failed", CANCELLED: "Cancelled", INTERRUPTED: "Interrupted", SKIPPED: "Skipped" };
const params = () => new URLSearchParams(location.hash.split("?")[1] || "");
const ms = (v) => (v === null || v === undefined ? "–" : `${Math.round(v).toLocaleString()} ms`);
const num = (v, d = 1) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));

let timer = null;
let generation = 0;  // a newer mount ends the refresh loop of an older one
let lastDash = null;  // the last figures seen stay on the page after the run ends

function tile(label, value, note = "", cls = "") {
  return `<div class="card live-tile ${cls}"><div class="kpi-label">${esc(label)}</div><div class="kpi-value">${value}</div>${note ? `<div class="kpi-note">${note}</div>` : ""}</div>`;
}

async function pickRun(view) {
  const { items } = await api("/api/performance/queue");
  const active = items.find((r) => ACTIVE.includes(r.state));
  if (active || items.length) return (active || items[0]).run_id;
  const { items: recent } = await api("/api/performance/runs?limit=8");
  view.innerHTML = `<div class="card"><h3 style="margin-top:0">No test is running</h3>
    <p class="muted">Start a test from a scenario or the script editor; its live view opens here as soon as it is queued. Recent runs:</p>
    ${recent.length ? `<table class="data"><tbody>${recent.map((r) => `<tr><td><a href="#/perf-results?run=${encodeURIComponent(r.run_id)}">${esc(r.name)}</a></td><td>${esc(STATE_LABEL[r.state] || r.state)}</td><td>${esc(dateTimeOf(r.started_at))}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">None yet.</p>'}</div>`;
  return null;
}

function render(view, data, firstPaint) {
  const { run, generator: g } = data;
  if (data.dashboard) lastDash = data.dashboard;
  const d = data.dashboard || (FINAL.includes(run.state) ? lastDash : null);
  const title = run.validation_run ? `One-user validation: ${esc(run.name.replace(/ \(one-user validation\)$/, ""))}` : esc(run.name);
  if (firstPaint) {
    view.innerHTML = `
      <p><a href="#/perf-results">← Performance Results</a></p>
      <div class="card"><div class="row" style="flex-wrap:wrap"><h2 style="margin:0">${title}</h2><span id="lvState"></span>
          <span class="spacer"></span>
          <a class="btn" href="#/perf-results?run=${encodeURIComponent(run.run_id)}">Run details</a>
          <button class="btn" id="lvStop" hidden title="JMeter stops cleanly and writes its results and report">Stop</button>
          <button class="btn" id="lvKill" hidden title="Ends JMeter at once; the HTML report may be missing">Emergency stop</button></div>
        <p class="muted" id="lvMeta"></p>
        <div class="live-progress" id="lvProg" hidden><span></span></div>
        <p id="lvNote"></p></div>
      <div class="grid kpis" id="lvTiles" style="margin-top:12px"></div>
      <div class="card" id="lvWatch" style="margin-top:12px" hidden></div>
      <div class="grid" id="lvCharts" style="grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr));margin-top:12px">
        <div class="card" id="cUsers"></div><div class="card" id="cTput"></div>
        <div class="card" id="cP95"></div><div class="card" id="cErr"></div></div>
      <div class="grid" style="grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);margin-top:12px;align-items:start" id="lvLower">
        <div class="card"><h3 style="margin-top:0">Requests</h3><div id="lvReqs" style="overflow-x:auto"></div></div>
        <div class="card"><h3 style="margin-top:0">Errors</h3><div id="lvErrs"></div></div></div>
      <div class="card" style="margin-top:12px" id="lvGen"></div>`;
    const act = (mode, msg) => api(`/api/performance/runs/${encodeURIComponent(run.run_id)}/stop`, { method: "POST", body: { mode } })
      .then(() => toast(msg, "info")).catch((err) => toast(err.message, "error"));
    view.querySelector("#lvStop").onclick = () => act("graceful", "Stopping JMeter; results so far are kept");
    view.querySelector("#lvKill").onclick = () => { if (confirm("End JMeter at once? Results so far are kept, but the HTML report may be missing.")) act("emergency", "JMeter is being ended"); };
  }
  view.querySelector("#lvState").innerHTML = `<span class="pill ${STATE_PILL[run.state] || "WAITING"}">${esc(STATE_LABEL[run.state] || run.state)}</span>${ACTIVE.includes(run.state) ? ' <span class="live-dot" title="Refreshed every 2 seconds"></span> <span class="muted">live</span>' : ""}`;
  view.querySelector("#lvStop").hidden = !["VALIDATING", "RUNNING"].includes(run.state);
  view.querySelector("#lvKill").hidden = !ACTIVE.includes(run.state);
  view.querySelector("#lvMeta").textContent = `${run.run_id}${run.environment ? ` · environment ${run.environment}` : ""} · ${run.state === "QUEUED" ? `queued ${dateTimeOf(run.queued_at)}` : `started ${dateTimeOf(run.started_at)}`}${run.finished_at ? ` · ended ${dateTimeOf(run.finished_at)}` : ""}`;
  const note = view.querySelector("#lvNote");
  if (run.state === "QUEUED") note.innerHTML = `Waiting in the queue${run.position ? ` (position ${run.position})` : ""}. Tests run one at a time on this PC.`;
  else if (run.state === "VALIDATING") note.innerHTML = "Pre-run checks are running; no request has been sent yet.";
  else if (FINAL.includes(run.state)) note.innerHTML = `<b>The test has ended.</b> ${esc(run.state_reason || run.error || "")} ${d ? "The figures below are the last ones read while it ran. " : ""}The verdict, percentiles and the JMeter report are on the <a href="#/perf-results?run=${encodeURIComponent(run.run_id)}">run details</a>.`;
  else note.innerHTML = '<span class="muted">Figures are read from the results JMeter has written so far, every 2 seconds. Samples count at their start time, so the last few seconds still fill up. The verdict is decided from the full results after the run.</span>';
  const prog = view.querySelector("#lvProg");
  if (d && d.progress_pct !== null) { prog.hidden = false; prog.querySelector("span").style.width = `${d.progress_pct}%`; } else prog.hidden = true;
  const show = !!d;
  for (const id of ["#lvTiles", "#lvCharts", "#lvLower"]) view.querySelector(id).hidden = !show;
  if (!d) { view.querySelector("#lvWatch").hidden = true; renderGen(view, g); return; }
  const w = d.window;
  view.querySelector("#lvTiles").innerHTML = [
    tile("Elapsed", fmtDuration(Math.round(d.elapsed_s)), d.planned_duration_s ? `of ${fmtDuration(d.planned_duration_s)} planned (${num(d.progress_pct, 0)}%)` : "duration depends on loop counts"),
    tile("Active users", d.active_users, d.planned_users_now !== null ? `planned now ${num(d.planned_users_now, 0)}` : "concurrent threads"),
    tile(`Requests/s (last ${w.seconds}s)`, num(w.throughput, 1), d.rate_target ? `target ${d.rate_target} req/s` : `${d.samples.toLocaleString()} requests so far`),
    tile(`95th pct (last ${w.seconds}s)`, ms(w.p95_ms), `avg ${ms(w.avg_ms)} · 99th ${ms(w.p99_ms)}`),
    tile(`Errors (last ${w.seconds}s)`, w.error_pct === null ? "–" : `${num(w.error_pct, 2)}%`, `${d.errors.toLocaleString()} of ${d.samples.toLocaleString()} so far (${num(d.error_pct, 2)}%)`, w.error_pct > 0 ? "bad" : ""),
  ].join("");
  const watch = view.querySelector("#lvWatch");
  watch.hidden = !d.watch.length;
  watch.innerHTML = `<h3 style="margin-top:0">Targets, last ${w.seconds} seconds</h3><div class="row" style="flex-wrap:wrap;gap:8px">${d.watch.map((x) => `<span class="pill ${x.ok ? "PASS" : "WARN"}">${esc(x.name)}: ${esc(num(x.actual, x.unit === "ms" ? 0 : 2))} ${esc(x.unit)} (target ${x.name === "Throughput" || x.name === "Request rate target" ? "≥" : "≤"} ${esc(x.limit)})</span>`).join("")}</div>
    <p class="muted">A live indication only. Pass or fail is decided from the complete results when the run ends.</p>`;
  const pts = d.timeline;
  const hint = d.planned_duration_s || 0;
  lineChart(view.querySelector("#cUsers"), pts, { key: "threads", title: "Active users", unit: "users", integer: true, xMaxHint: hint,
    overlay: d.planned_curve.length ? { key: "planned", label: "planned" } : null });
  lineChart(view.querySelector("#cTput"), pts, { key: "throughput", title: "Requests per second", unit: "req/s", xMaxHint: hint,
    ref: d.rate_target ? { value: d.rate_target, label: "target" } : null });
  const p95t = d.watch.find((x) => x.name === "95th percentile");
  lineChart(view.querySelector("#cP95"), pts, { key: "p95_ms", title: "95th percentile response time per second (ms)", unit: "ms", xMaxHint: hint,
    ref: p95t ? { value: p95t.limit, label: "target" } : null });
  lineChart(view.querySelector("#cErr"), pts, { key: "errors", title: "Errors per second", unit: "errors", integer: true, color: "var(--fail)", xMaxHint: hint });
  view.querySelector("#lvReqs").innerHTML = d.requests.length ? `<table class="data"><thead><tr><th>Request</th><th>Samples</th><th>Errors</th><th>Avg</th><th>Max</th><th>Req/s now</th><th>95th now</th></tr></thead><tbody>
    ${d.requests.map((r) => `<tr${r.transaction ? ' class="muted"' : ""}><td>${esc(r.label)}${r.transaction ? " (transaction)" : ""}</td><td>${r.samples.toLocaleString()}</td>
      <td>${r.errors ? `<span class="pill FAIL">${num(r.error_pct, 2)}%</span>` : "0%"}</td><td>${ms(r.avg_ms)}</td><td>${ms(r.max_ms)}</td>
      <td>${r.transaction ? "–" : num(r.window_throughput, 2)}</td><td>${r.transaction ? "–" : ms(r.window_p95_ms)}</td></tr>`).join("")}</tbody></table>`
    : '<p class="muted">No samples yet. JMeter writes a sample when its request finishes.</p>';
  view.querySelector("#lvErrs").innerHTML = d.top_errors.length ? `<table class="data"><tbody>${d.top_errors.map((e) => `<tr><td>${esc(e.error)}</td><td>${e.count}</td></tr>`).join("")}</tbody></table>
    <h4>Latest</h4><ul class="live-errors">${d.recent_errors.slice(0, 8).map((e) => `<li><span class="muted">${fmtDuration(Math.round(e.t))}</span> ${esc(e.label)}: ${esc(e.code)} ${esc(e.message)}</li>`).join("")}</ul>`
    : '<p class="muted">No errors so far.</p>';
  renderGen(view, g);
}

function renderGen(view, g) {
  const box = view.querySelector("#lvGen");
  if (!g) { box.hidden = true; return; }
  box.hidden = false;
  if (!g.available) { box.innerHTML = '<h3 style="margin-top:0">Load generator (this PC)</h3><p class="muted">psutil is not installed, so CPU and memory of this PC are not recorded.</p>'; return; }
  const last = g.samples[g.samples.length - 1];
  box.innerHTML = `<div class="row"><h3 style="margin:0">Load generator (this PC)</h3>${g.flags.length ? '<span class="pill WARN">SATURATED</span>' : '<span class="pill PASS">OK</span>'}</div>
    ${g.flags.length ? `<p>${g.flags.map((f) => esc(f.message)).join("; ")}. Response times measured while this PC is overloaded can be too high.</p>` : ""}
    <p class="muted">${last ? `Now: CPU ${last.cpu_pct}%, memory ${last.mem_pct}%, JMeter ${last.jmeter_mem_mb} MB, free disk ${Number(last.disk_free_mb).toLocaleString()} MB.` : "Waiting for the first sample."}</p>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr))"><div id="gCpu"></div><div id="gMem"></div></div>`;
  if (g.samples.length > 1) {
    lineChart(box.querySelector("#gCpu"), g.samples, { key: "cpu_pct", title: "Generator CPU (% of all cores)", unit: "%" });
    lineChart(box.querySelector("#gMem"), g.samples, { key: "mem_pct", title: "Generator memory (% used)", unit: "%" });
  }
}

export const perfLive = {
  async mount(view) {
    const gen = ++generation;
    lastDash = null;
    let runId = params().get("run");
    if (!runId) {
      runId = await pickRun(view);
      if (gen !== generation) return;
      if (!runId) {  // nothing running: look again, and open the live view as soon as a test is queued
        timer = setTimeout(() => { if (gen === generation) perfLive.mount(view).catch(() => {}); }, 3000);
        return;
      }
    }
    let first = true;
    const tick = async () => {
      if (gen !== generation) return;
      try {
        const data = await api(`/api/performance/runs/${encodeURIComponent(runId)}/live`);
        if (gen !== generation) return;
        render(view, data, first);
        first = false;
        if (FINAL.includes(data.run.state)) return;  // ended: nothing more to refresh
      } catch (err) {
        if (first) { view.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`; return; }
      }
      if (gen === generation) timer = setTimeout(tick, 2000);
    };
    await tick();
  },
  unmount() { generation += 1; clearTimeout(timer); },
};
