// Defect Review: drafts from failed runs, duplicate check against open TFS defects,
// and Review Before Create / Automatic / Disabled creation modes.

import { api } from "../api.js";
import { esc, toast, dateTimeOf, safeHtml } from "../util.js";
import { evidenceUrl } from "./automation.js";

const MODES = {
  review: ["Review Before Create", "Failures become drafts here; you decide what goes to TFS."],
  automatic: ["Automatic", "Product failures with no likely duplicate are created in TFS right away. Test setup and locator problems still wait for review."],
  disabled: ["Disabled", "No drafts are made automatically. You can still analyze a failed run from Execution History."],
};
const STATUS_PILL = { draft: "WAITING", needs_review: "WARN", created: "PASS", linked: "PASS", dismissed: "WAITING" };
const STATUS_TEXT = { draft: "Draft", needs_review: "Needs review", created: "Created", linked: "Linked", dismissed: "Dismissed" };
const SEVERITY = { 1: "1 - Critical", 2: "2 - High", 3: "3 - Medium", 4: "4 - Low" };

const params = () => new URLSearchParams(location.hash.split("?")[1] || "");
const routeName = () => location.hash.replace(/^#\/?/, "").split("?")[0];

// Facts are recorded data; show them as short readable lines instead of raw JSON.
function fmtFact(v) {
  if (v === null || v === undefined) return "";
  if (Array.isArray(v)) return v.length ? v.map(fmtFact).join("\n") : "none";
  if (typeof v === "object") {
    if ("actual" in v && "limit" in v) return `${v.name} (${v.scope}): ${v.actual} ${v.unit}, limit ${v.limit} ${v.unit}`;
    return Object.entries(v).map(([k, x]) => `${k.replace(/_/g, " ")}: ${x ?? ""}`).join(", ");
  }
  return String(v);
}

function sourceLink(d) {
  return d.source === "performance" ? `#/perf-results?run=${encodeURIComponent(d.source_run_id)}` : `#/history?run=${encodeURIComponent(d.source_run_id)}`;
}

async function detail(view, id) {
  const { draft: d } = await api(`/api/defects/drafts/${encodeURIComponent(id)}`);
  const editable = !["created", "linked"].includes(d.status);
  const shots = d.evidence.filter((e) => e.kind === "screenshot");
  view.innerHTML = `
    <p><a href="#/${routeName()}">← ${esc(document.getElementById("pageTitle").textContent)}</a></p>
    <div class="card">
      <div class="row"><span class="pill ${STATUS_PILL[d.status]}">${esc(STATUS_TEXT[d.status])}</span>
        <span class="muted">Draft #${d.id} · ${esc(d.source)} · ${esc(d.category.replace(/_/g, " "))} · from <a href="${sourceLink(d)}">${esc(d.source_run_id)}</a>
        · seen ${d.occurrences.length} time${d.occurrences.length === 1 ? "" : "s"}</span><span class="spacer"></span>
        ${d.tfs_id ? `<a class="btn" href="#/defects?id=${d.tfs_id}">Open defect #${d.tfs_id}</a>` : ""}</div>
      <p><strong>Finding:</strong> ${esc(d.finding)}</p>
      ${d.product_defect ? "" : '<p class="pill WARN">This may not be a product defect. Check the evidence before creating it.</p>'}
      ${d.note ? `<p class="muted">${esc(d.note)}</p>` : ""}
    </div>
    <div class="grid split-2" style="margin-top:12px">
      <form class="card settings" id="form" style="max-width:none">
        <h3>Defect</h3>
        <label>Title <input name="title" value="${esc(d.title)}" ${editable ? "" : "disabled"} required></label>
        <div class="row">
          <label>Severity <select name="severity" ${editable ? "" : "disabled"}>${Object.entries(SEVERITY).map(([k, v]) => `<option value="${k}" ${Number(k) === d.severity ? "selected" : ""}>${v}</option>`).join("")}</select></label>
          <label>Priority <select name="priority" ${editable ? "" : "disabled"}>${[1, 2, 3, 4].map((p) => `<option ${p === d.priority ? "selected" : ""}>${p}</option>`).join("")}</select></label>
        </div>
        <label ${editable ? "" : "hidden"}>Repro steps (HTML) <textarea name="repro_steps" rows="10" ${editable ? "" : "disabled"}>${esc(d.repro_steps)}</textarea></label>
        ${editable ? "" : "<strong>Repro steps</strong>"}
        <div class="rich card" style="background:var(--surface-2)" id="preview"></div>
        ${editable ? `<div class="row"><button class="btn" type="button" id="save">Save draft</button>
          <button class="btn primary" type="submit">Create in TFS</button><span class="spacer"></span>
          <button class="link-btn" type="button" id="dismiss">Dismiss</button></div>` : ""}
        <p class="muted">The work item type (Bug, Defect or Issue) comes from your process. Fields your process does not have are skipped and listed after creation.${shots.length ? " Screenshots are attached to the defect." : ""}</p>
      </form>
      <div>
        <div class="card"><div class="row"><h3 style="margin:0">Possible duplicates in TFS</h3><span class="spacer"></span>
          <button class="btn" id="recheck">Check again</button></div>
          ${d.duplicates.length ? `<table class="data"><tbody>${d.duplicates.map((x) => `<tr><td><a href="#/defects?id=${x.id}">#${x.id}</a> ${esc(x.title)}
            <div class="muted">${esc(x.state)} · ${esc(x.reasons.join(", ") || "similar wording")}</div></td><td><strong>${Math.round(x.score * 100)}%</strong></td>
            <td>${editable ? `<button class="btn" data-link="${x.id}">Link to this</button>` : ""}</td></tr>`).join("")}</tbody></table>`
            : '<p class="muted">No open defect looks like this one.</p>'}
          ${editable ? `<form class="row" id="linkForm" style="margin-top:8px"><input name="tfs_id" inputmode="numeric" placeholder="Defect ID" style="width:120px" required><button class="btn" type="submit">Link to another defect</button></form>` : ""}
        </div>
        <div class="card" style="margin-top:12px"><h3>Evidence</h3>
          ${shots.length ? `<div class="shots">${shots.map((e) => `<a href="${evidenceUrl(e.path)}" target="_blank" rel="noopener"><img src="${evidenceUrl(e.path)}" alt="Screenshot"><span>${esc(e.path.split(/[\\/]/).pop())}</span></a>`).join("")}</div>` : ""}
          <table class="data"><tbody>${Object.entries(d.facts).filter(([k]) => k !== "finding").map(([k, v]) => [k, k === "failed_step" && v !== null ? v + 1 : v]).map(([k, v]) => `<tr><td class="muted">${esc(k.replace(/_/g, " "))}</td><td><code style="white-space:pre-wrap">${esc(fmtFact(v))}</code></td></tr>`).join("")}</tbody></table>
          <p class="muted">Occurrences: ${d.occurrences.map((o) => esc(o.run_id)).join(", ")}</p>
        </div>
      </div>
    </div>`;
  const form = view.querySelector("#form");
  const preview = view.querySelector("#preview");
  const render = () => { preview.innerHTML = safeHtml(form.repro_steps.value); };
  render();
  form.repro_steps.addEventListener("input", render);
  const values = () => ({ title: form.title.value, severity: Number(form.severity.value), priority: Number(form.priority.value), repro_steps: form.repro_steps.value });
  const reload = () => detail(view, id);
  view.querySelector("#save")?.addEventListener("click", async () => {
    try { await api(`/api/defects/drafts/${d.id}`, { method: "PATCH", body: values() }); toast("Draft saved", "success"); }
    catch (err) { toast(err.message, "error"); }
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const { draft } = await api(`/api/defects/drafts/${d.id}/create`, { method: "POST", body: values() });
      toast(`Defect #${draft.tfs_id} created in TFS`, "success");
      reload();
    } catch (err) { toast(err.message, "error"); btn.disabled = false; }
  });
  view.querySelector("#dismiss")?.addEventListener("click", async () => {
    const reason = prompt("Why is this not a defect? (kept with the draft)", d.product_defect ? "" : "Test maintenance");
    if (reason === null) return;
    try { await api(`/api/defects/drafts/${d.id}/dismiss`, { method: "POST", body: { reason } }); reload(); }
    catch (err) { toast(err.message, "error"); }
  });
  const link = async (tfsId) => {
    try { await api(`/api/defects/drafts/${d.id}/link`, { method: "POST", body: { tfs_id: Number(tfsId) } }); toast(`Linked to defect #${tfsId}`, "success"); reload(); }
    catch (err) { toast(err.message, "error"); }
  };
  view.querySelectorAll("[data-link]").forEach((b) => b.addEventListener("click", () => link(b.dataset.link)));
  view.querySelector("#linkForm")?.addEventListener("submit", (e) => { e.preventDefault(); link(new FormData(e.target).get("tfs_id")); });
  view.querySelector("#recheck").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { await api(`/api/defects/drafts/${d.id}/duplicates`, { method: "POST", body: {} }); reload(); }
    catch (err) { toast(err.message, "error"); e.target.disabled = false; }
  });
}

async function list(view, source) {
  const status = params().get("status") || "open";
  const q = new URLSearchParams({ status: status === "all" ? "" : status });
  if (source) q.set("source", source);
  const { items, creation_mode } = await api(`/api/defects/drafts?${q}`);
  view.innerHTML = `
    <div class="card"><div class="row"><h3 style="margin:0">Defect creation</h3>
      <select id="mode">${Object.entries(MODES).map(([k, [label]]) => `<option value="${k}" ${k === creation_mode ? "selected" : ""}>${label}</option>`).join("")}</select>
      <span class="muted" id="modeHelp">${esc(MODES[creation_mode][1])}</span></div></div>
    <div class="row" style="margin:12px 0">
      ${[["open", "Open"], ["created", "Created"], ["linked", "Linked"], ["dismissed", "Dismissed"], ["all", "All"]].map(([k, v]) => `<a class="btn ${k === status ? "primary" : ""}" href="#/${routeName()}?status=${k}">${v}</a>`).join("")}
    </div>
    <div class="card" style="overflow-x:auto">${items.length ? `<table class="data"><thead><tr><th>Draft</th><th>Category</th><th>Severity</th><th>Seen</th><th>Possible duplicate</th><th>Status</th><th>Updated</th></tr></thead><tbody>
      ${items.map((d) => `<tr class="clickable" data-id="${d.id}"><td>${esc(d.title)}<div class="muted">#${d.id} · ${esc(d.source)}${d.test_case_id ? ` · Test Case #${esc(d.test_case_id)}` : ""}</div></td>
        <td>${esc(d.category.replace(/_/g, " "))}${d.product_defect ? "" : ' <span class="pill WARN">review</span>'}</td><td>${esc(SEVERITY[d.severity])}</td><td>${d.occurrences}</td>
        <td>${d.top_duplicate ? `#${d.top_duplicate.id} (${Math.round(d.top_duplicate.score * 100)}%)` : ""}</td>
        <td><span class="pill ${STATUS_PILL[d.status]}">${esc(STATUS_TEXT[d.status])}</span>${d.tfs_id ? ` <a href="#/defects?id=${d.tfs_id}">#${d.tfs_id}</a>` : ""}</td>
        <td>${esc(dateTimeOf(d.updated_at))}</td></tr>`).join("")}
      </tbody></table>` : `<p class="muted">No ${status === "all" ? "" : esc(status) + " "}defect drafts. Drafts appear when ${source === "performance" ? "a performance run fails its SLAs" : "an automated test or performance run fails"}.</p>`}</div>`;
  view.querySelector("#mode").addEventListener("change", async (e) => {
    try {
      await api("/api/defects/mode", { method: "PUT", body: { creation_mode: e.target.value } });
      view.querySelector("#modeHelp").textContent = MODES[e.target.value][1];
      toast(`Defect creation: ${MODES[e.target.value][0]}`, "success");
    } catch (err) { toast(err.message, "error"); }
  });
  view.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest("a")) return;
    location.hash = `#/${routeName()}?id=${tr.dataset.id}`;
  }));
}

function page(source) {
  return {
    async mount(view) {
      const id = params().get("id");
      if (id) await detail(view, id); else await list(view, source);
    },
  };
}

export const defectReview = page(null);
export const perfDefects = page("performance");

// Shared "Analyze failure" action for Execution History and Performance Results.
export async function analyzeRun(kind, runId) {
  const { draft } = await api("/api/defects/analyze", { method: "POST", body: { kind, run_id: runId } });
  location.hash = `#/${kind === "performance" ? "perf-defects" : "defect-review"}?id=${draft.id}`;
}
