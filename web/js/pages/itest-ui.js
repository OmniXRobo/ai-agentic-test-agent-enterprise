// 23.14.0: shared pieces of the Security, Red Hat Fuse and ActiveMQ testing pages: target picker, run list with
// pause / resume / cancel, results with the unified result actions, live progress and the authorization record.

import { api } from "../api.js";
import { esc, toast, dateTimeOf } from "../util.js";
import { canEdit } from "../auth.js";
import { actionButtons, wireActions } from "../result-actions.js";

export const enc = encodeURIComponent;
const PILL = { PASSED: "PASS", FAILED: "FAIL", ERROR: "FAIL", BLOCKED: "WARN", SKIPPED: "WAITING", UNVERIFIED: "WARN",
  RUNNING: "RUNNING", COMPLETED: "PASS", CANCELLED: "WARN", PAUSED: "WARN", INTERRUPTED: "WARN", QUEUED: "WAITING",
  CANCELLING: "WARN", open: "FAIL", confirmed: "FAIL", false_positive: "WAITING", accepted_risk: "WARN", fixed: "PASS",
  critical: "FAIL", high: "FAIL", medium: "WARN", low: "WAITING", info: "WAITING" };
export const pill = (s) => s ? `<span class="pill ${esc(PILL[s] || "WAITING")}">${esc(String(s).replace("_", " "))}</span>` : '<span class="muted">-</span>';
export const short = (v, n = 160) => { const t = typeof v === "string" ? v : v == null ? "" : JSON.stringify(v); return t.length > n ? `${t.slice(0, n)}…` : t; };

export async function guarded(btn, fn) {
  if (btn) btn.disabled = true;
  try { return await fn(); } catch (err) { toast(err.message, "error", 9000); return null; } finally { if (btn) btn.disabled = false; }
}

export function stored(key, value) {
  try { if (value === undefined) return localStorage.getItem(key) || ""; localStorage.setItem(key, value); } catch { /* private mode */ }
  return value || "";
}

export function targetPicker(targets, selected, { label = "Target", id = "tgtSel", empty = "None yet" } = {}) {
  return `<select id="${id}" aria-label="${esc(label)}"><option value="">${targets.length ? `Choose ${esc(label.toLowerCase())}…` : esc(empty)}</option>
    ${targets.map((t) => `<option value="${esc(t.id)}" ${selected === t.id ? "selected" : ""}>${esc(t.name)} · ${esc(t.environment)}</option>`).join("")}</select>`;
}

export function envBadge(t) {
  if (!t) return "";
  return t.environment === "Production" ? '<span class="pill FAIL">PRODUCTION</span>' : `<span class="pill WAITING">${esc(t.environment)}</span>`;
}

export function authorizationHtml(cfg) {
  const a = cfg?.authorization || {};
  return a.confirmed ? `<p class="small" style="margin:4px 0"><span class="pill PASS">Authorized</span> ${esc(a.statement)} ${a.reference ? `(${esc(a.reference)})` : ""} · recorded by ${esc(a.by)} ${esc(dateTimeOf ? dateTimeOf(a.at) : a.at)}</p>`
    : '<p class="small" style="margin:4px 0"><span class="pill FAIL">Not authorized yet</span> Record who approved testing this target before any run.</p>';
}

export function authorizationFields() {
  return `<fieldset style="grid-column:1/-1"><legend>Authorization to test</legend>
    <label class="row" style="gap:6px"><input type="checkbox" name="authorization_confirmed"> I am authorized to test this target and record the approval below</label>
    <label>Approval (who approved, scope, period) <input name="authorization_statement" maxlength="500" placeholder="Approved by the application owner for the QA environment, 1-31 Dec"></label>
    <label>Reference (ticket / email) <input name="authorization_reference" maxlength="120"></label></fieldset>`;
}

export function runsTable(runs, module) {
  if (!runs.length) return '<p class="muted">No runs yet.</p>';
  return `<table class="data compact"><thead><tr><th>Run</th><th>Type</th><th>Status</th><th>Results</th><th>Started</th><th></th></tr></thead><tbody>
    ${runs.map((r) => { const c = r.counts || {}; const live = ["RUNNING", "PAUSED", "QUEUED", "CANCELLING"].includes(r.status);
      return `<tr><td><a href="#" data-run="${esc(r.id)}">${esc(r.id)}</a><div class="muted small">${esc(r.label || "")}</div></td><td>${esc(r.kind)}</td><td>${pill(r.status)}</td>
      <td class="small">${c.PASSED ?? 0} passed · ${(c.FAILED ?? 0) + (c.ERROR ?? 0)} failed · ${c.BLOCKED ?? 0} blocked${c.UNVERIFIED ? ` · ${c.UNVERIFIED} unverified` : ""}</td>
      <td class="small">${esc(dateTimeOf(r.started_at))}</td>
      <td>${live && canEdit() ? `${r.status === "PAUSED" ? `<button class="btn small" data-ctl="resume" data-rid="${esc(r.id)}" data-mod="${module}">Resume</button>` : `<button class="btn small" data-ctl="pause" data-rid="${esc(r.id)}" data-mod="${module}">Pause</button>`}
        <button class="btn small" data-ctl="cancel" data-rid="${esc(r.id)}" data-mod="${module}">Cancel</button>` : !live ? actionButtons("run", r.id, r.status === "COMPLETED" && !((c.FAILED || 0) + (c.ERROR || 0)) ? "PASSED" : "FAILED", { retest: false }) : ""}</td></tr>`; }).join("")}</tbody></table>`;
}

const CONTROL_BASE = { security: "/api/security/runs", fuse: "/api/fuse/runs", activemq: "/api/activemq/runs" };

export function wireRuns(root, onOpen, refresh) {
  root.addEventListener("click", async (e) => {
    const a = e.target.closest("[data-run]");
    if (a) { e.preventDefault(); onOpen(a.dataset.run); return; }
    const b = e.target.closest("[data-ctl]");
    if (b) await guarded(b, async () => { await api(`${CONTROL_BASE[b.dataset.mod]}/${enc(b.dataset.rid)}/control`, { method: "POST", body: { action: b.dataset.ctl } }); refresh?.(); });
  });
}

export function resultsTable(results, { showCategory = true } = {}) {
  if (!results.length) return '<p class="muted">No results.</p>';
  return `<table class="data compact"><thead><tr><th>Test</th>${showCategory ? "<th>Category</th>" : ""}<th>Status</th><th>Expected / actual</th><th>Actions</th></tr></thead><tbody>
    ${results.map((r) => `<tr><td>${esc(r.test_name)}${r.correlation_id ? `<div class="muted small">Correlation ID ${esc(r.correlation_id)}</div>` : ""}${r.draft_id ? `<div class="small">Defect draft #${esc(r.draft_id)}</div>` : ""}</td>
      ${showCategory ? `<td>${esc(r.category || "")}${r.severity ? ` ${pill(r.severity)}` : ""}</td>` : ""}<td>${pill(r.status)}</td>
      <td class="small">${r.expected ? `<div><b>Expected:</b> ${esc(short(r.expected))}</div>` : ""}${r.actual ? `<div><b>Actual:</b> ${esc(short(r.actual, 240))}</div>` : ""}</td>
      <td>${r.finding_id ? actionButtons("finding", r.finding_id, r.status) : actionButtons("result", r.id, r.status)}</td></tr>`).join("")}</tbody></table>`;
}

export function statusFilter(id = "stFilter") {
  return `<select id="${id}" aria-label="Status"><option value="">All results</option><option value="FAILED,ERROR">Failed</option><option value="PASSED">Passed</option>
    <option value="BLOCKED">Blocked</option><option value="UNVERIFIED">Unverified</option></select>`;
}

// Shows one run with its results and refreshes while it is running.
export async function runDetail(box, runId, { onDone } = {}) {
  let timer = null;
  const draw = async () => {
    const r = await api(`/api/itest/runs/${enc(runId)}`);
    const status = box.querySelector("#rdStatus")?.value || "";
    const res = (await api(`/api/itest/results?run_id=${enc(runId)}${status ? `&status=${enc(status)}` : ""}`)).results;
    const live = ["RUNNING", "PAUSED", "QUEUED", "CANCELLING"].includes(r.run.status);
    box.innerHTML = `<div class="card"><div class="row" style="gap:8px;flex-wrap:wrap"><h3 style="margin:0">${esc(r.run.label || r.run.id)}</h3>${pill(r.run.status)}
      <span class="muted small">${esc(r.run.id)} · ${r.counts.PASSED} passed · ${r.counts.FAILED + r.counts.ERROR} failed · ${r.counts.BLOCKED} blocked · ${r.counts.UNVERIFIED || 0} unverified</span>
      <span class="spacer"></span>${statusFilter("rdStatus")}</div>
      ${r.run.summary?.error ? `<p class="pill FAIL">${esc(r.run.summary.error)}</p>` : ""}${r.run.summary?.note ? `<p class="muted">${esc(r.run.summary.note)}</p>` : ""}
      ${live ? '<p class="muted small">Running… this list refreshes every 3 seconds. Results already recorded are kept if the run is cancelled.</p>' : ""}
      <div style="margin-top:8px">${resultsTable(res)}</div></div>`;
    box.querySelector("#rdStatus").value = status;
    box.querySelector("#rdStatus").addEventListener("change", draw);
    if (!live && timer) { clearInterval(timer); timer = null; onDone?.(r.run); }
    return live;
  };
  const live = await draw();
  if (live) timer = setInterval(() => draw().catch(() => {}), 3000);
  return () => timer && clearInterval(timer);
}

// Wired once per container (screens redraw their body in place); the latest refresh callback is used.
const WIRED = new WeakMap();
export function wireResultActions(root, refresh) {
  if (WIRED.has(root)) { WIRED.set(root, refresh); return; }
  WIRED.set(root, refresh);
  wireActions(root, { onChange: () => WIRED.get(root)?.() });
}

export function lines(v) { return String(v || "").split(/\r?\n|,|;/).map((x) => x.trim()).filter(Boolean); }
