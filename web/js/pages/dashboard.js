import { api } from "../api.js";
import { state, subscribe } from "../store.js";
import { esc } from "../util.js";

let unsub = null;
let refreshTimer = null;

function kpi(label, value, note = "") {
  return `<div class="card"><div class="kpi-label">${esc(label)}</div><div class="kpi-value">${esc(value)}</div><div class="kpi-note">${esc(note)}</div></div>`;
}

const TFS_KPIS = ["User Stories", "Acceptance Criteria", "Test Cases", "Test Data", "Automated", "Not Automated",
  "Open Defects", "Automation Coverage", "Functional Pass Rate"];
const PERF_KPIS = ["Performance Tests", "Performance Failures", "Performance SLA Pass Rate"];

async function render(view) {
  let summary;
  try {
    summary = await api("/api/dashboard/summary");
  } catch (err) {
    view.innerHTML = `<div class="card"><h2>Dashboard unavailable</h2><p class="muted">${esc(err.message)}</p></div>`;
    return;
  }
  const exec = summary.runs.functional || {};
  const tfsNote = summary.tfs.connected ? "" : "Not connected";
  view.innerHTML = `
    <div class="grid kpis">
      ${kpi("Agents Running", summary.agents_running, "Live from runtime events")}
      ${kpi("Unread Notifications", summary.unread_notifications)}
      ${kpi("Passed", exec.PASSED ?? 0, "Functional runs")}
      ${kpi("Failed", exec.FAILED ?? 0, "Functional runs")}
    </div>
    <h3 style="margin:22px 0 10px">TFS / Azure DevOps</h3>
    ${summary.tfs.connected ? "" : `<p class="muted">${esc(summary.tfs.reason)} These figures will come from your configured TFS project; nothing is estimated.</p>`}
    <div class="grid kpis">${TFS_KPIS.map((k) => kpi(k, "—", tfsNote)).join("")}</div>
    <h3 style="margin:22px 0 10px">Performance</h3>
    <div class="grid kpis">${PERF_KPIS.map((k) => kpi(k, "—", "From JMeter runs (Stage 5)")).join("")}</div>
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
    if (!["agent.completed", "agent.failed", "agent.started"].includes(c.event.name)) return;
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => render(view), 600);
  });
}

export function unmount() {
  if (unsub) unsub();
  clearTimeout(refreshTimer);
  unsub = null;
}
