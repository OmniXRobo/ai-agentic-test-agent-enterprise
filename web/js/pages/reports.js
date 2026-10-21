// Reports: generate functional and performance reports (HTML and PDF) from recorded results, then view, download or delete them.

import { api } from "../api.js";
import { subscribe } from "../store.js";
import { esc, toast, dateTimeOf } from "../util.js";

const SCOPES = { both: "Functional and performance", functional: "Functional", performance: "Performance" };
const STATUS = { ready: "PASS", failed: "FAIL", running: "RUNNING" };
let unsub = null;

const params = () => new URLSearchParams(location.hash.split("?")[1] || "");

function summaryText(r) {
  const s = r.summary || {};
  const parts = [];
  if (s.executions !== undefined) parts.push(`${s.executions} executions${s.pass_rate !== null && s.pass_rate !== undefined ? `, ${s.pass_rate}% passed` : ""}`);
  if (s.perf_runs !== undefined) parts.push(`${s.perf_runs} performance run${s.perf_runs === 1 ? "" : "s"}, SLA ${s.sla_pass} passed / ${s.sla_fail} failed`);
  return parts.join(" · ");
}

async function renderList(view) {
  const box = view.querySelector("#list");
  let items;
  try { ({ items } = await api("/api/reports")); }
  catch (err) { box.innerHTML = `<p class="muted">${esc(err.message)}</p>`; return; }
  const focus = params().get("id");
  box.innerHTML = items.length ? `<div style="overflow-x:auto"><table class="data"><thead><tr><th>Report</th><th>Scope</th><th>Created</th><th>Status</th><th>Contents</th><th></th></tr></thead><tbody>
    ${items.map((r) => `<tr${r.id === focus ? ' class="selected"' : ""}>
      <td><strong>${esc(r.title)}</strong><div class="muted" style="font-size:12px">${esc(r.id)}${r.params?.user_story_id ? ` · user story ${esc(r.params.user_story_id)}` : ""} · last ${esc(r.params?.days ?? 30)} days</div></td>
      <td>${esc(SCOPES[r.scope] || r.scope)}</td><td>${esc(dateTimeOf(r.created_at))}</td>
      <td><span class="pill ${STATUS[r.status] || "WAITING"}">${esc(r.status.toUpperCase())}</span></td>
      <td>${r.status === "failed" ? `<span class="muted">${esc(r.error || "")}</span>` : esc(summaryText(r))}${r.summary?.pdf_error ? `<div class="muted">${esc(r.summary.pdf_error)}</div>` : ""}</td>
      <td style="white-space:nowrap">${r.has_html ? `<a class="btn" href="/api/reports/${esc(r.id)}/html" target="_blank" rel="noopener">View</a>
        <a class="btn" href="/api/reports/${esc(r.id)}/html?download=true">HTML</a>` : ""}
        ${r.has_pdf ? `<a class="btn" href="/api/reports/${esc(r.id)}/pdf?download=true">PDF</a>` : ""}
        ${r.status !== "running" ? `<button class="link-btn" data-del="${esc(r.id)}">Delete</button>` : ""}</td></tr>`).join("")}
    </tbody></table></div>` : '<p class="muted">No reports yet. Choose what to include above and press Generate report.</p>';
  box.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`Delete report ${b.dataset.del} and its files?`)) return;
    try { await api(`/api/reports/${b.dataset.del}`, { method: "DELETE" }); renderList(view); }
    catch (err) { toast(err.message, "error"); }
  }));
}

async function renderRuns(view) {
  const form = view.querySelector("#gen");
  const box = view.querySelector("#perfRuns");
  if (form.scope.value === "functional") { box.innerHTML = ""; return; }
  try {
    const { runs } = await api(`/api/analytics/performance?days=${form.days.value}`);
    box.innerHTML = runs.length ? `<p class="muted" style="margin:4px 0">Performance runs to include (up to 5). Leave all unticked to include the latest run of each plan.</p>
      <div class="chips">${runs.slice(-20).reverse().map((r) => `<label class="chip"><input type="checkbox" name="perf_run" value="${esc(r.run_id)}">
        ${esc(r.name)} · ${esc(dateTimeOf(r.started_at))}${r.sla ? ` · SLA ${esc(r.sla.replace("_", " ").toLowerCase())}` : ""}</label>`).join("")}</div>`
      : '<p class="muted" style="margin:4px 0">There are no performance runs with results in this period.</p>';
  } catch (err) { box.innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
}

export async function mount(view) {
  view.innerHTML = `
    <form class="card" id="gen" style="margin-bottom:12px">
      <h3 style="margin-top:0">Generate a report</h3>
      <div class="row form-row">
        <label>Include <select name="scope">${Object.entries(SCOPES).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></label>
        <label>Period <select name="days"><option value="7">Last 7 days</option><option value="14">Last 14 days</option>
          <option value="30" selected>Last 30 days</option><option value="90">Last 90 days</option></select></label>
        <label>User story (optional) <input name="user_story_id" inputmode="numeric" placeholder="e.g. 118" style="width:120px"></label>
        <label style="flex:1;min-width:200px">Title (optional) <input name="title" maxlength="150" placeholder="QA report" style="width:100%"></label>
      </div>
      <div id="perfRuns"></div>
      <div class="row" style="margin-top:8px"><button class="btn primary" type="submit">Generate report</button>
        <span class="muted">Built from recorded runs and your TFS project only. PDF files use the same layout as the HTML report.</span></div>
    </form>
    <div class="card" id="list"><p class="muted">Loading…</p></div>`;
  const form = view.querySelector("#gen");
  form.scope.addEventListener("change", () => renderRuns(view));
  form.days.addEventListener("change", () => renderRuns(view));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const story = form.user_story_id.value.trim();
    if (story && !/^\d+$/.test(story)) { toast("The user story must be a number.", "error"); return; }
    const runs = [...form.querySelectorAll("[name=perf_run]:checked")].map((c) => c.value);
    if (runs.length > 5) { toast("Choose up to 5 performance runs.", "error"); return; }
    const body = { scope: form.scope.value, days: Number(form.days.value) };
    if (story) body.user_story_id = Number(story);
    if (form.title.value.trim()) body.title = form.title.value.trim();
    if (runs.length) body.perf_run_ids = runs;
    try {
      const { report } = await api("/api/reports", { method: "POST", body });
      toast(`Generating ${report.title}…`, "info");
      renderList(view);
    } catch (err) { toast(err.message, "error"); }
  });
  unsub = subscribe((c) => {
    if (c.type === "event" && ["report.completed", "report.failed"].includes(c.event.name)) renderList(view);
  });
  await Promise.all([renderList(view), renderRuns(view)]);
}

export function unmount() {
  unsub?.();
  unsub = null;
}
