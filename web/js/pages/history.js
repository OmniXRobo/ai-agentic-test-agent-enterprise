import { api } from "../api.js";
import { esc, dateTimeOf, elapsed, timeOf } from "../util.js";

function runIdFromHash() {
  const q = location.hash.split("?")[1] || "";
  return new URLSearchParams(q).get("run");
}

async function renderDetail(view, runId) {
  const { run, events } = await api(`/api/runs/${encodeURIComponent(runId)}`);
  const checks = run.summary?.checks || [];
  view.innerHTML = `
    <p><a href="#/history">← Execution History</a></p>
    <div class="card">
      <div class="row"><h2 style="margin:0">${esc(run.title || run.run_id)}</h2><span class="pill ${esc(run.status)}">${esc(run.status)}</span></div>
      <p class="muted">${esc(run.run_id)} · ${esc(run.kind)} · started ${esc(dateTimeOf(run.started_at))}${run.finished_at ? " · took " + esc(elapsed(run.started_at, run.finished_at)) : ""}</p>
      ${run.summary?.error ? `<p class="pill FAIL">${esc(run.summary.error)}</p>` : ""}
      ${checks.length ? `<table class="data"><thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr></thead><tbody>
        ${checks.map((c) => `<tr><td>${esc(c.name)}</td><td><span class="pill ${esc(c.status)}">${esc(c.status)}</span></td><td>${esc(c.detail)}</td></tr>`).join("")}
      </tbody></table>` : ""}
    </div>
    <div class="card" style="margin-top:14px"><h3>Event log</h3>
      <div class="feed">${events.map((e) => `<div class="feed-item"><span class="feed-time">${esc(timeOf(e.timestamp))}</span><span>${esc((e.agent_name ? e.agent_name + " · " : "") + (e.activity || e.name))}</span></div>`).join("")}</div>
    </div>`;
}

async function renderList(view) {
  const { runs } = await api("/api/runs?limit=200");
  view.innerHTML = `<div class="card">${runs.length ? `
    <table class="data"><thead><tr><th>Run ID</th><th>Type</th><th>Title</th><th>Status</th><th>Started</th><th>Duration</th></tr></thead><tbody>
    ${runs.map((r) => `<tr class="clickable" data-run="${esc(r.run_id)}"><td>${esc(r.run_id)}</td><td>${esc(r.kind)}</td><td>${esc(r.title || "")}</td>
      <td><span class="pill ${esc(r.status)}">${esc(r.status)}</span></td><td>${esc(dateTimeOf(r.started_at))}</td>
      <td>${r.finished_at ? esc(elapsed(r.started_at, r.finished_at)) : ""}</td></tr>`).join("")}
    </tbody></table>` : `<p class="muted">No runs recorded yet.</p>`}</div>`;
  view.querySelectorAll("tr[data-run]").forEach((tr) => tr.addEventListener("click", () => { location.hash = `#/history?run=${encodeURIComponent(tr.dataset.run)}`; }));
}

export async function mount(view) {
  const runId = runIdFromHash();
  if (runId) await renderDetail(view, runId); else await renderList(view);
}
