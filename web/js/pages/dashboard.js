import { api } from "../api.js";
import { state, subscribe } from "../store.js";
import { esc } from "../util.js";

let unsub = null;
let refreshTimer = null;

function kpi(label, value, note = "") {
  return `<div class="card"><div class="kpi-label">${esc(label)}</div><div class="kpi-value">${esc(value)}</div><div class="kpi-note">${esc(note)}</div></div>`;
}

const pct = (v) => (v === null || v === undefined ? "—" : `${v}%`);
const link = (href, html) => `<a href="${href}" style="text-decoration:none;color:inherit">${html}</a>`;

async function render(view) {
  let summary, a;
  try {
    [summary, a] = await Promise.all([api("/api/dashboard/summary"), api("/api/analytics/summary")]);
  } catch (err) {
    view.innerHTML = `<div class="card"><h2>Dashboard unavailable</h2><p class="muted">${esc(err.message)}</p></div>`;
    return;
  }
  const tfs = summary.tfs;
  const t = a.tfs.connected ? a.tfs : {};
  const cov = a.coverage, fn = a.functional, perf = a.performance, ac = a.acceptance_criteria;
  const notConnected = "TFS not connected";
  const tfsNote = tfs.connected ? tfs.project : notConnected;
  const quality = [
    link("#/user-stories", kpi("User Stories", t.requirement ?? "—", tfsNote)),
    kpi("Acceptance Criteria", ac.available ? ac.criteria : "—",
      ac.available ? `on ${ac.stories_with_criteria} of ${ac.stories} user stories` : tfs.connected ? "Not available" : notConnected),
    link("#/test-cases", kpi("Test Cases", t.testcase ?? "—", tfsNote)),
    link("#/test-data", kpi("Test Data", a.test_data, "Saved data sets")),
    link("#/ai-automation", kpi("Automated", cov.automated_test_cases, `${cov.scripts} automated test${cov.scripts === 1 ? "" : "s"}`)),
    kpi("Not Automated", cov.not_automated ?? "—", "tfs_test_cases" in cov ? "TFS test cases without a script" : notConnected),
    kpi("Automation Coverage", pct(cov.automation_coverage), "tfs_test_cases" in cov ? `${cov.automated_test_cases} of ${cov.tfs_test_cases} test cases` : notConnected),
    link("#/defects", kpi("Open Defects", t.open_defects ?? "—", tfsNote)),
  ];
  const functional = [
    link("#/history", kpi("Passed", fn.passed, "Latest result per test")),
    link("#/history?status=FAILED", kpi("Failed", fn.failed, "Latest result per test")),
    kpi("Functional Pass Rate", pct(fn.pass_rate), fn.scripts_run ? `${fn.scripts_run} tests with results` : "No results yet"),
    link("#/defect-review", kpi("Defect Drafts", a.open_defect_drafts, "Waiting for review")),
  ];
  const performance = [
    link("#/perf-results", kpi("Performance Tests", perf.runs, "Finished JMeter runs")),
    kpi("Performance Failures", perf.failed, "Runs that failed or missed the SLA"),
    kpi("Performance SLA Pass Rate", pct(perf.sla_pass_rate), perf.sla_pass + perf.sla_fail ? `${perf.sla_pass} passed, ${perf.sla_fail} failed` : "No SLA results yet"),
  ];
  view.innerHTML = `
    <div class="grid kpis">
      ${kpi("Agents Running", summary.agents_running, "Live from runtime events")}
      ${kpi("Unread Notifications", summary.unread_notifications)}
      ${functional.join("")}
    </div>
    <h3 style="margin:22px 0 10px">${esc(tfs.label)}${tfs.project ? " · " + esc(tfs.project) : ""}</h3>
    ${tfs.connected ? "" : `<p class="muted">${esc(tfs.reason)} <a href="#/tfs-settings">TFS connection settings</a>. Figures come only from your configured project; nothing is estimated.</p>`}
    <div class="grid kpis">${quality.join("")}</div>
    <h3 style="margin:22px 0 10px">Performance</h3>
    <div class="grid kpis">${performance.join("")}</div>
    <h3 style="margin:22px 0 10px">Recent runs</h3>
    <div class="card" id="recentRuns"><p class="muted">Loading…</p></div>`;
  try {
    const { runs } = await api("/api/runs?limit=8");
    view.querySelector("#recentRuns").innerHTML = runs.length ? `
      <table class="data"><thead><tr><th>Run</th><th>Type</th><th>Status</th><th>Started</th></tr></thead><tbody>
      ${runs.map((r) => `<tr class="clickable" data-run="${esc(r.run_id)}"><td>${esc(r.run_id)}</td><td>${esc(r.kind)}</td>
        <td><span class="pill ${esc(r.status)}">${esc(r.status)}</span></td><td>${esc(new Date(r.started_at).toLocaleString())}</td></tr>`).join("")}
      </tbody></table>` : `<p class="muted">No runs yet.</p>`;
    view.querySelectorAll("tr[data-run]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/history?run=${tr.dataset.run}`; }));
  } catch (err) {
    view.querySelector("#recentRuns").innerHTML = `<p class="muted">${esc(err.message)}</p>`;
  }
}

export async function mount(view) {
  await render(view);
  // Re-render only when a run finishes, debounced, instead of on a timer.
  unsub = subscribe((c) => {
    if (c.type !== "event") return;
    if (!["agent.completed", "agent.failed", "agent.started", "defect.created"].includes(c.event.name)) return;
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => render(view), 600);
  });
}

export function unmount() {
  if (unsub) unsub();
  clearTimeout(refreshTimer);
  unsub = null;
}
