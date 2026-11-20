// 23.13.0: "Generate AI result", "Create TFS defect" and "Send email" (with the PDF report) on every testing module.
// One bar above the page, the same on each module, so each page keeps its own layout. The report and the email use
// the module's recorded data (and the AI analysis when one was generated); the defect goes through Defect Review's
// approval and duplicate check like every other defect, or is sent at once when the person may approve and send.

import { api } from "./api.js";
import { esc, toast, openModal } from "./util.js";
import { mountAiReport, openEmail } from "./ai-report.js";
import { canEdit } from "./auth.js";

export const MODULE_SOURCES = {
  "ai-automation": "framework", "guided-automation": "functional", discovery: "functional", "hybrid-automation": "functional",
  "api-testing": "api", "scheduled-runs": "functional", "perf-load": "performance", "perf-stress": "performance",
  "perf-spike": "performance", "perf-soak": "performance", "perf-live": "performance", "perf-schedules": "performance",
  "perf-results": "performance", "integration-comparison": "comparison", analytics: "functional",
};

export function hasModuleActions(route) { return route in MODULE_SOURCES; }

function openAi(route, title) {
  const { el } = openModal(`${title}: AI result`, '<div data-ai></div>', { wide: true });
  mountAiReport(el.querySelector("[data-ai]"), { kind: "module", id: route, analyzeLabel: "Generate AI result",
    intro: `The On-Premise AI reads the recorded results of ${title} (runs, failures, defects, self-healing, timings) and writes what they show, the risks and the next steps. Edit it if needed, then make the PDF, download it or email it.` });
}

async function sendEmail(route) {
  // The PDF is made from the recorded data even before an AI analysis exists (the analysis is added when there is one).
  try {
    const st = await api(`/api/ai-reports/module/${encodeURIComponent(route)}`);
    if (!st.has_pdf) await api(`/api/ai-reports/module/${encodeURIComponent(route)}/pdf`, { method: "POST", body: {} });
  } catch (err) { toast(err.message, "error"); return; }
  openEmail("module", route);
}

let people = null;
async function openDefect(route, title) {
  const { el, close } = openModal(`${title}: create TFS defect`, `
    <form class="grid" id="defForm" style="gap:10px">
      <label>Title <input name="title" required minlength="3" maxlength="250" placeholder="What is wrong, where"></label>
      <div class="row" style="gap:10px;flex-wrap:wrap">
        <label>Severity <select name="severity"><option value="1">1 - Critical</option><option value="2" selected>2 - High</option><option value="3">3 - Medium</option><option value="4">4 - Low</option></select></label>
        <label>Priority <select name="priority"><option>1</option><option selected>2</option><option>3</option><option>4</option></select></label>
        <label>Assign to <select name="assigned_to"><option value="">Not assigned</option></select></label>
        <label>Run ID <input name="run_id" maxlength="80" placeholder="optional"></label>
      </div>
      <label>Steps to reproduce, expected and actual result <textarea name="repro_steps" rows="9" placeholder="1. Open ...&#10;2. ...&#10;Expected: ...&#10;Actual: ..."></textarea></label>
      <label><span><input type="checkbox" name="send_now"> Approve and send to TFS now (otherwise it waits in Defect Review)</span></label>
      <p class="muted small" data-note style="margin:0">Loading people from TFS…</p>
      <div class="row"><span class="spacer"></span><button type="button" class="btn" data-close>Cancel</button><button class="btn primary" type="submit">Create defect</button></div>
    </form>`, { wide: true });
  const sel = el.querySelector("[name=assigned_to]"), note = el.querySelector("[data-note]");
  try {
    people = people || (await api("/api/defects/assignees")).people || [];
    sel.innerHTML = '<option value="">Not assigned</option>' + people.map((p) => `<option value="${esc(p.unique_name || p.name)}">${esc(p.name)}</option>`).join("");
    note.textContent = `${people.length} people in the TFS project.`;
  } catch (err) { note.textContent = `People could not be loaded from TFS (${err.message}). The defect can still be drafted.`; }
  el.querySelector("#defForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const btn = ev.target.querySelector("[type=submit]");
    btn.disabled = true;
    try {
      const { draft } = await api("/api/defects/manual", { method: "POST", body: {
        source: MODULE_SOURCES[route], module: title, title: String(f.get("title")).trim(), severity: Number(f.get("severity")),
        priority: Number(f.get("priority")), repro_steps: String(f.get("repro_steps") || ""), assigned_to: f.get("assigned_to") || null,
        run_id: String(f.get("run_id") || "").trim() || null, send_now: !!f.get("send_now") } });
      toast(draft.tfs_id ? `Created in TFS as #${draft.tfs_id}` : `Defect draft #${draft.id} is waiting in Defect Review`, "success");
      close();
      if (!draft.tfs_id) location.hash = `#/defect-review?id=${draft.id}`;
    } catch (err) { toast(err.message, "error"); btn.disabled = false; }
  });
}

/** Fills `bar` for `route`, or empties and hides it for pages without these actions. */
export function mountModuleActions(bar, route, title) {
  if (!bar) return;
  if (!hasModuleActions(route)) { bar.innerHTML = ""; bar.hidden = true; return; }
  bar.hidden = false;
  bar.innerHTML = `<div class="module-actions row" role="toolbar" aria-label="${esc(title)} report actions">
    <span class="muted small">${esc(title)} results:</span><span class="spacer"></span>
    <button class="btn small" data-ma="ai" title="The On-Premise AI analyzes this module's results">Generate AI result</button>
    ${canEdit() ? '<button class="btn small" data-ma="defect" title="Write a defect; it goes to TFS after approval">Create TFS defect</button>' : ""}
    <button class="btn small" data-ma="email" title="Email the PDF report of this module through Outlook">Send email (PDF)</button></div>`;
  bar.querySelector('[data-ma="ai"]').onclick = () => openAi(route, title);
  bar.querySelector('[data-ma="defect"]')?.addEventListener("click", () => openDefect(route, title));
  bar.querySelector('[data-ma="email"]').onclick = () => sendEmail(route);
}
