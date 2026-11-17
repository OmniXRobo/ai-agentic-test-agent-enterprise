// Defect Review (23.11.0): one review for AI-Driven Automation, Guided Browser Automation (recorded scripts, edited
// Python, data-driven, bulk and scheduled runs) and Integration Comparison. A draft is classified from the run's
// evidence; a person approves it and sends it to TFS. Nothing is published without that approval, and success is
// shown only with the work item TFS returned.

import { api } from "../api.js";
import { esc, toast, dateTimeOf, safeHtml } from "../util.js";

const MODES = {
  review: ["Review Before Create", "Failures become drafts here; you approve what goes to TFS."],
  automatic: ["Automatic drafts", "Every failure becomes a draft right away and is marked \"Approval required\". Nothing is sent to TFS without a person's approval."],
  disabled: ["Disabled", "No drafts are made automatically. You can still analyze a failed run from Execution History."],
};
const STATUS_PILL = { draft: "WAITING", needs_review: "WARN", approved: "RUNNING", publishing: "RUNNING", created: "PASS",
  publication_failed: "FAIL", dismissed: "WAITING", linked: "PASS" };
const CLASS_PILL = { application_defect: "FAIL", automation_script: "WARN", test_data: "WARN", environment: "WARN",
  timing: "WARN", inconclusive: "WAITING" };
const SEVERITY = { 1: "1 - Critical", 2: "2 - High", 3: "3 - Medium", 4: "4 - Low" };
const SOURCES = [["", "All modules"], ["framework", "AI-Driven Automation"], ["functional", "Guided Browser"],
  ["python", "Guided Browser (Python)"], ["comparison", "Integration Comparison"], ["performance", "Performance"]];
const STATUSES = [["open", "Open"], ["approved", "Approved"], ["publication_failed", "Publication Failed"],
  ["created", "Published to TFS"], ["dismissed", "Rejected"], ["linked", "Duplicate"], ["all", "All"]];

const params = () => new URLSearchParams(location.hash.split("?")[1] || "");
const routeName = () => location.hash.replace(/^#\/?/, "").split("?")[0];

function fmtFact(v) {
  if (v === null || v === undefined || v === "") return "";
  if (Array.isArray(v)) return v.length ? v.map(fmtFact).join("\n") : "none";
  if (typeof v === "object") {
    if ("actual" in v && "limit" in v) return `${v.name} (${v.scope}): ${v.actual} ${v.unit}, limit ${v.limit} ${v.unit}`;
    return Object.entries(v).map(([k, x]) => `${k.replace(/_/g, " ")}: ${fmtFact(x)}`).join(", ");
  }
  return String(v);
}

function sourceLink(d) {
  if (d.source === "performance") return `#/perf-results?run=${encodeURIComponent(d.source_run_id)}`;
  if (d.source === "comparison") return "#/integration-comparison";
  return `#/history?run=${encodeURIComponent(d.source_run_id)}`;
}

const pct = (x) => (x === null || x === undefined ? "" : `${Math.round(Number(x) * 100)}%`);

function classification(d) {
  const a = d.analysis || {};
  if (!d.classification) return '<p class="muted">Not classified yet. Use AI Analyze.</p>';
  return `<div class="row"><span class="pill ${CLASS_PILL[d.classification] || "WAITING"}">${esc(d.classification_label || d.classification)}</span>
      <span class="muted">confidence ${pct(d.confidence)} · ${esc(a.origin || "rules")}${a.ai ? (a.ai.used ? (a.ai.agrees_with_rules ? " · agrees with the rules" : " · differs from the rules") : ` · ${esc(a.ai.reason || "")}`) : ""}</span></div>
    ${(a.evidence || []).length ? `<ul>${a.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
    ${a.recommendation && a.recommendation !== "none" ? `<p><strong>Recommendation:</strong> ${esc(a.recommendation_text || a.recommendation)}
      <span class="muted">A saved script is changed only when you apply a change yourself.</span></p>` : ""}
    ${a.rules && a.rules.classification !== d.classification ? `<p class="muted">Rules alone: ${esc(a.rules.classification.replace(/_/g, " "))} (${pct(a.rules.confidence)})</p>` : ""}`;
}

function evidence(d) {
  const items = (d.evidence || []).map((e, i) => ({ ...e, url: `/api/defects/drafts/${d.id}/evidence/${i}`, name: String(e.path || "").split(/[\\/]/).pop() }));
  const shots = items.filter((e) => e.kind === "screenshot");
  const files = items.filter((e) => e.kind !== "screenshot");
  return `${shots.length ? `<div class="shots">${shots.map((e) => `<a href="${e.url}" target="_blank" rel="noopener"><img src="${e.url}" alt="Screenshot"><span>${esc(e.name)}</span></a>`).join("")}</div>` : '<p class="muted">No screenshot was captured for this failure.</p>'}
    ${files.length ? `<ul>${files.map((e) => `<li><a href="${e.url}">${esc(e.kind === "trace" ? "Playwright trace" : e.kind)}: ${esc(e.name)}</a>${e.kind === "trace" ? ' <span class="muted">(open with "npx playwright show-trace" or trace.playwright.dev offline copy)</span>' : ""}</li>`).join("")}</ul>` : ""}
    ${d.facts?.trace_note ? `<p class="muted">${esc(d.facts.trace_note)}</p>` : ""}`;
}

const FAILURE_KEYS = ["failed_step_description", "failed_step", "action", "locator", "frame", "expected", "actual", "error",
  "traceback", "previous_step", "script_id", "script_version", "script_file", "run_id", "test_case_id", "dataset", "browser",
  "base_url", "environment", "applications", "record_key", "field", "difference", "calculation", "mapping", "rule_status",
  "result", "explanation", "observed"];

function failure(d) {
  const f = d.facts || {};
  const rows = FAILURE_KEYS.filter((k) => f[k] !== undefined && f[k] !== null && f[k] !== "" && !(Array.isArray(f[k]) && !f[k].length))
    .map((k) => [k, k === "failed_step" ? Number(f[k]) + 1 : f[k]]);
  return `<table class="data"><tbody>${rows.map(([k, v]) => `<tr><td class="muted">${esc(k.replace(/_/g, " "))}</td><td><code style="white-space:pre-wrap">${esc(fmtFact(v))}</code></td></tr>`).join("")}</tbody></table>`;
}

async function detail(view, id) {
  const [{ draft: d }, { events }] = await Promise.all([
    api(`/api/defects/drafts/${encodeURIComponent(id)}`), api(`/api/defects/drafts/${encodeURIComponent(id)}/events`)]);
  const editable = !["created", "linked", "publishing"].includes(d.status);
  const canApprove = ["draft", "needs_review", "publication_failed"].includes(d.status) && !(d.status === "publication_failed" && d.approved_by);
  const canSend = d.status === "approved";
  const canRetry = d.status === "publication_failed";
  const scriptId = d.facts?.script_id;
  const canRerun = scriptId && ["functional", "framework", "python"].includes(d.source);
  view.innerHTML = `
    <p><a href="#/${routeName()}">← ${esc(document.getElementById("pageTitle").textContent)}</a></p>
    <div class="card">
      <div class="row"><span class="pill ${STATUS_PILL[d.status] || "WAITING"}">${esc(d.status_label)}</span>
        <span class="muted">Draft #${d.id} · ${esc(d.source_label)} · from <a href="${sourceLink(d)}">${esc(d.source_ref || d.source_run_id)}</a>
        · seen ${d.occurrences.length} time${d.occurrences.length === 1 ? "" : "s"}</span><span class="spacer"></span>
        ${d.tfs_url ? `<a class="btn" href="${esc(d.tfs_url)}" target="_blank" rel="noopener">Open in TFS #${d.tfs_id}</a>` : ""}</div>
      ${d.tfs_id && d.status === "created" ? `<p>Published to TFS as <strong>#${d.tfs_id}</strong> on ${esc(dateTimeOf(d.published_at))}${d.approved_by ? `, approved by ${esc(d.approved_by)}` : ""}.</p>` : ""}
      ${d.note ? `<p class="muted">${esc(d.note)}</p>` : ""}
      ${d.correlation_id ? `<p class="muted">Correlation ID ${esc(d.correlation_id)} (also the TFS tag SkyGuard-${esc(d.correlation_id)})</p>` : ""}
      <div class="row" style="flex-wrap:wrap;gap:6px">
        <button class="btn" data-scroll="failure">View Failure</button><button class="btn" data-scroll="evidence">View Evidence</button>
        <button class="btn" id="ai">AI Analyze</button>
        ${canRerun ? '<button class="btn" id="rerun">Rerun</button>' : ""}
        ${canApprove ? '<button class="btn primary" id="approve">Approve</button>' : ""}
        ${editable && d.status !== "dismissed" ? '<button class="btn" id="reject">Reject</button>' : ""}
        ${canSend ? '<button class="btn primary" id="send">Send to TFS</button>' : ""}
        ${canRetry ? '<button class="btn primary" id="retry">Retry</button>' : ""}
      </div>
    </div>
    <div class="grid split-2" style="margin-top:12px">
      <div>
        <div class="card"><h3>Classification</h3>${classification(d)}</div>
        <form class="card settings" id="form" style="max-width:none;margin-top:12px">
          <h3>Defect</h3>
          <label>Title <input name="title" value="${esc(d.title)}" ${editable ? "" : "disabled"} required></label>
          <div class="row">
            <label>Severity <select name="severity" ${editable ? "" : "disabled"}>${Object.entries(SEVERITY).map(([k, v]) => `<option value="${k}" ${Number(k) === d.severity ? "selected" : ""}>${v}</option>`).join("")}</select></label>
            <label>Priority <select name="priority" ${editable ? "" : "disabled"}>${[1, 2, 3, 4].map((p) => `<option ${p === d.priority ? "selected" : ""}>${p}</option>`).join("")}</select></label>
          </div>
          <label ${editable ? "" : "hidden"}>Repro steps (HTML) <textarea name="repro_steps" rows="10" ${editable ? "" : "disabled"}>${esc(d.repro_steps)}</textarea></label>
          ${editable ? "" : "<strong>Repro steps</strong>"}
          <div class="rich card" style="background:var(--surface-2)" id="preview"></div>
          ${editable ? '<div class="row"><button class="btn" type="button" id="save">Save Draft</button></div>' : ""}
          <p class="muted">Mandatory fields come from Preferences &gt; TFS Integration. Editing an approved draft needs a new approval.</p>
        </form>
      </div>
      <div>
        <div class="card" id="failure"><h3>Failure</h3>${failure(d)}</div>
        <div class="card" id="evidence" style="margin-top:12px"><h3>Evidence</h3>${evidence(d)}
          <p class="muted">Occurrences: ${d.occurrences.map((o) => esc(o.run_id)).join(", ")}</p></div>
        <div class="card" style="margin-top:12px"><div class="row"><h3 style="margin:0">Possible duplicates in TFS</h3><span class="spacer"></span>
          <button class="btn" id="recheck">Check again</button></div>
          ${d.duplicates.length ? `<table class="data"><tbody>${d.duplicates.map((x) => `<tr><td>#${x.id} ${esc(x.title)}
            <div class="muted">${esc(x.state || "")} · ${esc((x.reasons || []).join(", ") || "similar wording")}</div></td><td><strong>${Math.round(x.score * 100)}%</strong></td>
            <td>${editable ? `<button class="btn" data-link="${x.id}">Mark duplicate, add evidence</button>` : ""}</td></tr>`).join("")}</tbody></table>`
            : '<p class="muted">No open defect looks like this one.</p>'}
          ${editable ? '<form class="row" id="linkForm" style="margin-top:8px"><input name="tfs_id" inputmode="numeric" placeholder="Defect ID" style="width:120px" required><button class="btn" type="submit">Duplicate of another defect</button></form>' : ""}
        </div>
        <div class="card" style="margin-top:12px"><h3>History</h3>
          <table class="data"><tbody>${events.map((e) => `<tr><td class="muted">${esc(dateTimeOf(e.at))}</td><td>${esc(e.by || "")}</td><td>${esc(e.action.replace(/_/g, " "))}</td><td>${esc(e.detail || "")}</td></tr>`).join("")}</tbody></table></div>
      </div>
    </div>`;
  view.querySelectorAll("[data-scroll]").forEach((b) => b.addEventListener("click", () => view.querySelector(`#${b.dataset.scroll}`).scrollIntoView({ behavior: "smooth" })));
  const form = view.querySelector("#form");
  const preview = view.querySelector("#preview");
  const render = () => { preview.innerHTML = safeHtml(form.repro_steps.value); };
  render();
  form.repro_steps.addEventListener("input", render);
  const values = () => ({ title: form.title.value, severity: Number(form.severity.value), priority: Number(form.priority.value), repro_steps: form.repro_steps.value });
  const reload = () => detail(view, id);
  const act = (sel, fn) => view.querySelector(sel)?.addEventListener("click", async (e) => {
    e.preventDefault();
    const btn = e.currentTarget;
    btn.disabled = true;
    try { await fn(); } catch (err) { toast(err.message, "error"); btn.disabled = false; return; }
    reload();
  });
  act("#save", async () => { await api(`/api/defects/drafts/${d.id}`, { method: "PATCH", body: values() }); toast("Draft saved", "success"); });
  act("#ai", async () => {
    const { draft } = await api(`/api/defects/drafts/${d.id}/ai-analyze`, { method: "POST", body: {} });
    toast(draft.analysis?.ai?.used ? `AI: ${draft.classification_label}` : (draft.analysis?.ai?.reason || "Rules only"), draft.analysis?.ai?.used ? "success" : "info");
  });
  act("#approve", async () => {
    if (editable) await api(`/api/defects/drafts/${d.id}`, { method: "PATCH", body: values() });
    await api(`/api/defects/drafts/${d.id}/approve`, { method: "POST", body: {} }); toast("Approved", "success");
  });
  act("#reject", async () => {
    const reason = prompt("Why is this not a defect? (kept with the draft)", d.classification === "application_defect" ? "" : "Test maintenance");
    if (reason === null) throw new Error("Not rejected");
    await api(`/api/defects/drafts/${d.id}/reject`, { method: "POST", body: { reason } });
  });
  const send = async (path) => {
    try {
      const { draft } = await api(path, { method: "POST", body: {} });
      toast(`Published to TFS as #${draft.tfs_id}`, "success");
    } catch (err) {
      if (err.code === "DEFECT_LIKELY_DUPLICATE" && confirm(`${err.message}\n\nSend it to TFS anyway?`)) {
        const { draft } = await api(path, { method: "POST", body: { allow_duplicate: true } });
        toast(`Published to TFS as #${draft.tfs_id}`, "success");
        return;
      }
      throw err;
    }
  };
  act("#send", () => send(`/api/defects/drafts/${d.id}/publish`));
  act("#retry", () => send(`/api/defects/drafts/${d.id}/retry`));
  act("#rerun", async () => {
    const path = d.source === "framework" ? `/api/automation/studio/scripts/${encodeURIComponent(scriptId)}/run` : `/api/automation/scripts/${encodeURIComponent(scriptId)}/run`;
    const r = await api(path, { method: "POST", body: {} });
    toast(`Rerun started${r.run_id ? ` (${r.run_id})` : ""}. This draft keeps the original failure.`, "success");
  });
  act("#recheck", async () => { await api(`/api/defects/drafts/${d.id}/duplicates`, { method: "POST", body: {} }); });
  const link = async (tfsId) => {
    try { await api(`/api/defects/drafts/${d.id}/link`, { method: "POST", body: { tfs_id: Number(tfsId) } }); toast(`Marked as duplicate of #${tfsId}; evidence added there`, "success"); reload(); }
    catch (err) { toast(err.message, "error"); }
  };
  view.querySelectorAll("[data-link]").forEach((b) => b.addEventListener("click", () => link(b.dataset.link)));
  view.querySelector("#linkForm")?.addEventListener("submit", (e) => { e.preventDefault(); link(new FormData(e.target).get("tfs_id")); });
}

async function list(view, fixedSource) {
  const status = params().get("status") || "open";
  const source = fixedSource || params().get("source") || "";
  const q = new URLSearchParams({ status: status === "all" ? "" : status });
  if (source) q.set("source", source);
  const { items, creation_mode } = await api(`/api/defects/drafts?${q}`);
  const href = (k, s) => `#/${routeName()}?status=${k}${s ? `&source=${s}` : ""}`;
  view.innerHTML = `
    <div class="card"><div class="row"><h3 style="margin:0">Defect drafts</h3>
      <select id="mode">${Object.entries(MODES).map(([k, [label]]) => `<option value="${k}" ${k === creation_mode ? "selected" : ""}>${label}</option>`).join("")}</select>
      <span class="muted" id="modeHelp">${esc(MODES[creation_mode][1])}</span></div></div>
    <div class="row" style="margin:12px 0;flex-wrap:wrap;gap:6px">
      ${fixedSource ? "" : `<select id="source">${SOURCES.map(([k, v]) => `<option value="${k}" ${k === source ? "selected" : ""}>${v}</option>`).join("")}</select>`}
      ${STATUSES.map(([k, v]) => `<a class="btn ${k === status ? "primary" : ""}" href="${href(k, source)}">${v}</a>`).join("")}
    </div>
    <div class="card" style="overflow-x:auto">${items.length ? `<table class="data"><thead><tr><th>Defect</th><th>Module</th><th>Script / run</th><th>Failed step</th><th>Classification</th><th>Severity</th><th>Status</th><th>TFS</th><th>Updated</th></tr></thead><tbody>
      ${items.map((d) => `<tr class="clickable" data-id="${d.id}"><td>${esc(d.title)}<div class="muted">#${d.id}${d.test_case_id ? ` · Test Case #${esc(d.test_case_id)}` : ""}${d.occurrences > 1 ? ` · seen ${d.occurrences} times` : ""}</div></td>
        <td>${esc(d.source_label)}</td><td>${esc(d.script_id || d.source_ref || "")}<div class="muted">${esc(d.source_run_id)}</div></td>
        <td>${esc(d.failed_step ?? "")}</td>
        <td>${d.classification ? `<span class="pill ${CLASS_PILL[d.classification] || "WAITING"}">${esc(d.classification_label)}</span> <span class="muted">${pct(d.confidence)}</span>` : ""}</td>
        <td>${esc(SEVERITY[d.severity] || "")}<div class="muted">P${esc(d.priority)}</div></td>
        <td><span class="pill ${STATUS_PILL[d.status] || "WAITING"}">${esc(d.status_label)}</span>${d.top_duplicate ? `<div class="muted">like #${d.top_duplicate.id} (${Math.round(d.top_duplicate.score * 100)}%)</div>` : ""}</td>
        <td>${d.tfs_url ? `<a href="${esc(d.tfs_url)}" target="_blank" rel="noopener">#${d.tfs_id}</a>` : ""}</td>
        <td>${esc(dateTimeOf(d.updated_at))}</td></tr>`).join("")}
      </tbody></table>` : `<p class="muted">No ${status === "all" ? "" : esc(STATUSES.find(([k]) => k === status)?.[1] || status) + " "}defect drafts. Drafts appear when an AI-Driven or Guided Browser test fails, or when you create a defect from an Integration Comparison result.</p>`}</div>`;
  view.querySelector("#mode").addEventListener("change", async (e) => {
    try {
      await api("/api/defects/mode", { method: "PUT", body: { creation_mode: e.target.value } });
      view.querySelector("#modeHelp").textContent = MODES[e.target.value][1];
      toast(`Defect drafts: ${MODES[e.target.value][0]}`, "success");
    } catch (err) { toast(err.message, "error"); }
  });
  view.querySelector("#source")?.addEventListener("change", (e) => { location.hash = href(status, e.target.value); });
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
