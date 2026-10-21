// Analytics: functional and performance trends computed from recorded runs. Nothing is estimated; empty periods say so.

import { api } from "../api.js";
import { subscribe } from "../store.js";
import { esc, dateTimeOf } from "../util.js";
import { lineChart, barChart } from "../charts.js";

let unsub = null;
let timer = null;
const pct = (v) => (v === null || v === undefined ? "—" : `${v}%`);
const ms = (v) => (v === null || v === undefined ? "—" : v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(2)} s`);
const day = (iso) => iso.slice(5);
const runDay = (iso) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });

function kpi(label, value, note = "") {
  return `<div class="card"><div class="kpi-label">${esc(label)}</div><div class="kpi-value">${esc(value)}</div><div class="kpi-note">${esc(note)}</div></div>`;
}

function functionalHtml(f) {
  const cov = f.coverage;
  const tfsNote = "tfs_test_cases" in cov ? `${cov.automated_test_cases} of ${cov.tfs_test_cases} TFS test cases` : (cov.tfs_reason || "TFS not connected");
  return `
    <div class="grid kpis">
      ${kpi("Automation Coverage", pct(cov.automation_coverage), tfsNote)}
      ${kpi("Not Automated", cov.not_automated ?? "—", "TFS test cases without a script")}
      ${kpi("Pass Rate", pct(f.pass_rate), `${f.passed} of ${f.executions} executions`)}
      ${kpi("Failure Rate", pct(f.failure_rate), `${f.failed} failed executions`)}
      ${kpi("Flaky Tests", f.flaky.length, "Passed and failed in this period")}
      ${kpi("Self-Healing Events", f.healing.total, `${f.healing.healed} recovered`)}
    </div>
    <div class="grid charts-grid" style="margin-top:12px">
      <div class="card" id="cExec"></div><div class="card" id="cDefects"></div><div class="card" id="cHeal"></div>
    </div>
    <div class="grid split-2" style="margin-top:12px">
      <div class="card"><h3 style="margin-top:0">Flaky tests</h3>${f.flaky.length ? `<div style="overflow-x:auto"><table class="data"><thead><tr><th>Test</th><th class="num">Runs</th><th class="num">Passed</th><th class="num">Failed</th><th class="num">Flip rate</th></tr></thead><tbody>
        ${f.flaky.map((x) => `<tr><td>${esc(x.name)}</td><td class="num">${x.runs}</td><td class="num">${x.passed}</td><td class="num">${x.failed}</td><td class="num">${x.flip_rate}%</td></tr>`).join("")}</tbody></table></div>
        <p class="muted">Flip rate is how often the result changed between consecutive runs.</p>` : '<p class="muted">No test both passed and failed in this period.</p>'}</div>
      <div class="card"><h3 style="margin-top:0">Self-healing by test</h3>${f.healing.by_script.length ? `<div style="overflow-x:auto"><table class="data"><thead><tr><th>Test</th><th class="num">Recovered</th><th class="num">Not recovered</th><th class="num">Saved to script</th></tr></thead><tbody>
        ${f.healing.by_script.map((x) => `<tr><td>${esc(x.name)}</td><td class="num">${x.healed || 0}</td><td class="num">${x.failed || 0}</td><td class="num">${x.applied || 0}</td></tr>`).join("")}</tbody></table></div>` : '<p class="muted">No self-healing was needed in this period.</p>'}</div>
    </div>`;
}

function performanceHtml(p, planId) {
  return `
    <div class="grid kpis">
      ${kpi("Performance Runs", p.runs.length, "With results in this period")}
      ${kpi("SLA Pass Rate", pct(p.sla.pass_rate), `${p.sla.pass} passed, ${p.sla.fail} failed`)}
      ${kpi("No SLA Set", p.sla.not_defined, "Runs without limits")}
      ${kpi("Max Tested Concurrency", p.max_users || "—", "Peak active virtual users")}
    </div>
    <div class="row" style="margin:12px 0"><label>Plan <select id="plan"><option value="">All plans</option>
      ${p.plans.map((x) => `<option value="${esc(x.plan_id)}" ${x.plan_id === planId ? "selected" : ""}>${esc(x.name)} (${x.runs} run${x.runs === 1 ? "" : "s"})</option>`).join("")}</select></label></div>
    ${p.runs.length ? `<div class="grid charts-grid">
      <div class="card" id="cP95"></div><div class="card" id="cP99"></div><div class="card" id="cTput"></div><div class="card" id="cErr"></div>
    </div>` : '<p class="muted">There are no performance runs with results in this period.</p>'}
    <div class="grid split-2" style="margin-top:12px">
      <div class="card"><h3 style="margin-top:0">SLA results per run</h3>${p.runs.length ? `<div style="overflow-x:auto"><table class="data"><thead><tr><th>Run</th><th>When</th><th class="num">P95</th><th class="num">Users</th><th>SLA</th></tr></thead><tbody>
        ${p.runs.slice().reverse().slice(0, 15).map((r) => `<tr class="clickable" data-run="${esc(r.run_id)}"><td>${esc(r.name)} <span class="muted">${esc(r.kind)}</span></td><td>${esc(dateTimeOf(r.started_at))}</td>
          <td class="num">${ms(r.p95_ms)}</td><td class="num">${r.peak_users ?? "—"}</td><td><span class="pill ${r.sla === "PASS" ? "PASS" : r.sla === "FAIL" ? "FAIL" : "WAITING"}">${esc((r.sla || "—").replace("_", " "))}</span></td></tr>`).join("")}</tbody></table></div>` : '<p class="muted">No runs.</p>'}</div>
      <div class="card"><h3 style="margin-top:0">Transaction performance</h3>${!planId ? '<p class="muted">Choose a plan to compare its latest run with the previous one, request by request.</p>'
        : p.transactions.length ? `<div style="overflow-x:auto"><table class="data"><thead><tr><th>Request</th><th class="num">P95</th><th class="num">Previous P95</th><th class="num">Change</th><th class="num">P99</th><th class="num">Error %</th></tr></thead><tbody>
        ${p.transactions.map((t) => {
          const change = t.previous_p95_ms ? Math.round(((t.p95_ms - t.previous_p95_ms) / t.previous_p95_ms) * 100) : null;
          return `<tr><td>${esc(t.label)}</td><td class="num">${ms(t.p95_ms)}</td><td class="num">${ms(t.previous_p95_ms)}</td>
            <td class="num">${change === null ? "—" : `${change > 0 ? "+" : ""}${change}%`}</td><td class="num">${ms(t.p99_ms)}</td><td class="num">${t.error_pct}</td></tr>`;
        }).join("")}</tbody></table></div>` : '<p class="muted">The latest run of this plan has no request results.</p>'}</div>
    </div>`;
}

async function render(view) {
  const days = view.querySelector("#days").value;
  const planId = view.querySelector("#plan")?.value || "";
  const fBox = view.querySelector("#functional"), pBox = view.querySelector("#performance");
  const [f, p] = await Promise.allSettled([
    api(`/api/analytics/functional?days=${days}`),
    api(`/api/analytics/performance?days=${days}${planId ? `&plan_id=${encodeURIComponent(planId)}` : ""}`),
  ]);
  if (f.status === "fulfilled") {
    const d = f.value;
    fBox.innerHTML = functionalHtml(d);
    barChart(fBox.querySelector("#cExec"), d.trend, { title: "Executions per day", label: (r) => day(r.date),
      series: [{ key: "passed", label: "Passed", color: "var(--ok)" }, { key: "failed", label: "Failed", color: "var(--fail)" }] });
    barChart(fBox.querySelector("#cDefects"), d.defects.trend, { title: "Defects drafted per day", label: (r) => day(r.date),
      series: [{ key: "functional", label: "Functional", color: "var(--cat-1)" }, { key: "performance", label: "Performance", color: "var(--cat-2)" }] });
    barChart(fBox.querySelector("#cHeal"), d.healing.trend, { title: "Self-healing per day", label: (r) => day(r.date),
      series: [{ key: "healed", label: "Recovered", color: "var(--ok)" }, { key: "failed", label: "Not recovered", color: "var(--fail)" }] });
  } else fBox.innerHTML = `<p class="muted">${esc(f.reason.message)}</p>`;
  if (p.status === "fulfilled") {
    const d = p.value;
    pBox.innerHTML = performanceHtml(d, planId);
    if (d.runs.length) {
      const chart = (id, key, title, unit, color) => {
        const runs = d.runs.filter((r) => r[key] !== null && r[key] !== undefined);
        lineChart(pBox.querySelector(id), runs.map((r, i) => ({ ...r, t: i })), {
          key, title, unit, color, xLabel: (t) => (runs[t] ? runDay(runs[t].started_at) : ""),
          tipLabel: (i) => `${runs[i].name}, ${dateTimeOf(runs[i].started_at)}`,
        });
      };
      chart("#cP95", "p95_ms", "95th percentile per run (ms)", "ms", "var(--cat-1)");
      chart("#cP99", "p99_ms", "99th percentile per run (ms)", "ms", "var(--cat-1)");
      chart("#cTput", "throughput", "Throughput per run (requests/s)", "req/s", "var(--cat-2)");
      chart("#cErr", "error_pct", "Error rate per run (%)", "%", "var(--fail)");
    }
    pBox.querySelector("#plan").addEventListener("change", () => render(view));
    pBox.querySelectorAll("tr[data-run]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/perf-results?run=${tr.dataset.run}`; }));
  } else pBox.innerHTML = `<p class="muted">${esc(p.reason.message)}</p>`;
}

export async function mount(view) {
  view.innerHTML = `
    <div class="row form-row" style="margin-bottom:12px"><label>Period <select id="days"><option value="7">Last 7 days</option><option value="14">Last 14 days</option>
      <option value="30" selected>Last 30 days</option><option value="90">Last 90 days</option></select></label>
      <a class="btn" href="#/reports">Generate a report</a></div>
    <h3>Functional testing</h3><div id="functional"><p class="muted">Loading…</p></div>
    <h3 style="margin-top:24px">Performance testing</h3><div id="performance"><p class="muted">Loading…</p></div>`;
  view.querySelector("#days").addEventListener("change", () => render(view));
  unsub = subscribe((c) => {
    if (c.type !== "event" || !["agent.completed", "agent.failed"].includes(c.event.name)) return;
    clearTimeout(timer);
    timer = setTimeout(() => render(view), 800);
  });
  await render(view);
}

export function unmount() {
  unsub?.();
  clearTimeout(timer);
  unsub = null;
}
