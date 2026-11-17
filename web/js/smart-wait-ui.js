// 23.11.0 Smart Wait in the step editor: Execution Wait Settings, Insert Wait After Steps (bulk) and "+ Wait".
// The same wait service runs in SkyGuard and in the downloaded Python test.

import { api } from "./api.js";
import { esc, openModal, toast } from "./util.js";

export const WAIT_KINDS = {
  page_load: "Page Load", ajax: "AJAX/Data Load", element_visible: "Element Visible", element_hidden: "Element Hidden",
  text_change: "Text/Value Change", fixed: "Fixed Delay", network_response: "Network Response", table_refresh: "Table Refresh",
};
const NEEDS_SELECTOR = new Set(["element_visible", "element_hidden", "text_change", "table_refresh"]);

export function waitLabel(w) {
  w = w || {};
  const kind = WAIT_KINDS[w.kind || "ajax"] || w.kind;
  if (w.kind === "fixed") return `Fixed Delay ${w.ms || 1000} ms`;
  const what = w.selector || w.url || "";
  return `${kind}${what ? ` (${what})` : ""}${w.value ? ` = '${w.value}'` : ""} · ${Math.round((w.timeout_ms || 30000) / 1000)} s${w.auto ? " · bulk" : ""}`;
}

// The settings of one wait step. Resolves with the wait object, or null when cancelled.
export function editWait(initial = {}) {
  const w = { kind: "ajax", timeout_ms: 30000, ...initial };
  const body = `<form id="waitForm" class="settings" style="max-width:none">
    <label>Wait for <select name="kind">${Object.entries(WAIT_KINDS).map(([k, v]) => `<option value="${k}" ${k === w.kind ? "selected" : ""}>${v}</option>`).join("")}</select></label>
    <label data-for="selector">Element (CSS selector) <input name="selector" value="${esc(w.selector || "")}" placeholder="#orders, .results table"></label>
    <label data-for="value">Expected text or value (optional; empty = wait until it changes) <input name="value" value="${esc(w.value || "")}"></label>
    <label data-for="url">Part of the request URL <input name="url" value="${esc(w.url || "")}" placeholder="/api/orders"></label>
    <label data-for="ms">Delay (ms) <input name="ms" type="number" min="50" max="120000" value="${esc(w.ms || 1000)}"></label>
    <label>Timeout (seconds) <input name="timeout" type="number" min="1" max="300" value="${Math.round((w.timeout_ms || 30000) / 1000)}"></label>
    <p class="muted">Smart waits check requests in flight, loaders, busy markers, DataTables processing and page changes, and confirm the result changed. A wait that runs out of time is reported on the step; it does not hide the problem.</p>
    <div class="row"><button class="btn primary" type="submit">OK</button><button class="btn" type="button" data-close>Cancel</button></div></form>`;
  const { el, close } = openModal("Smart Wait", body);
  const form = el.querySelector("#waitForm");
  const sync = () => {
    const k = form.kind.value;
    form.querySelector('[data-for="selector"]').hidden = !NEEDS_SELECTOR.has(k);
    form.querySelector('[data-for="value"]').hidden = k !== "text_change";
    form.querySelector('[data-for="url"]').hidden = k !== "network_response";
    form.querySelector('[data-for="ms"]').hidden = k !== "fixed";
  };
  sync();
  form.kind.addEventListener("change", sync);
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    new MutationObserver((_, obs) => { if (!el.isConnected) { obs.disconnect(); finish(null); } }).observe(document.body, { childList: true });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const k = form.kind.value;
      if (NEEDS_SELECTOR.has(k) && !form.selector.value.trim()) { toast(`${WAIT_KINDS[k]} needs the element`, "error"); return; }
      if (k === "network_response" && !form.url.value.trim()) { toast("Give part of the request URL", "error"); return; }
      const out = { kind: k, timeout_ms: Math.max(1, Number(form.timeout.value) || 30) * 1000 };
      if (NEEDS_SELECTOR.has(k)) out.selector = form.selector.value.trim();
      if (k === "text_change" && form.value.value) out.value = form.value.value;
      if (k === "network_response") out.url = form.url.value.trim();
      if (k === "fixed") out.ms = Number(form.ms.value) || 1000;
      if (initial.auto) out.auto = true;
      finish(out); close();
    });
  });
}

const steps1 = (txt) => String(txt || "").split(/[ ,;]+/).map((x) => parseInt(x, 10)).filter((n) => n > 0).map((n) => n - 1);

// Execution Wait Settings (saved with the script). onSaved(script) is called after saving.
export function waitSettings(script, onSaved) {
  const p = { enabled: false, mode: "smart", timeout_ms: 30000, minimum_ms: 500, fixed_ms: 1000, scope: "all", steps: [], include_navigation: true, ...(script.wait_policy || {}) };
  const body = `<form id="wpForm" class="settings" style="max-width:none">
    <label class="row" style="gap:6px"><input type="checkbox" name="enabled" ${p.enabled ? "checked" : ""}> Wait automatically after each step</label>
    <label>Mode <select name="mode">
      <option value="smart" ${p.mode === "smart" ? "selected" : ""}>Smart (until the page and its data are ready)</option>
      <option value="smart_min" ${p.mode === "smart_min" ? "selected" : ""}>Smart + Minimum</option>
      <option value="fixed" ${p.mode === "fixed" ? "selected" : ""}>Fixed delay</option></select></label>
    <div class="row"><label>Timeout (s) <input name="timeout" type="number" min="1" max="300" value="${Math.round(p.timeout_ms / 1000)}"></label>
      <label>Minimum (ms) <input name="minimum" type="number" min="0" max="60000" value="${p.minimum_ms}"></label>
      <label>Fixed delay (ms) <input name="fixed" type="number" min="50" max="120000" value="${p.fixed_ms}"></label></div>
    <label>Apply to <select name="scope"><option value="all" ${p.scope === "all" ? "selected" : ""}>All steps</option><option value="selected" ${p.scope === "selected" ? "selected" : ""}>Selected steps</option></select></label>
    <label>Selected steps (numbers) <input name="steps" value="${esc((p.steps || []).map((i) => i + 1).join(", "))}" placeholder="2, 4, 7"></label>
    <label class="row" style="gap:6px"><input type="checkbox" name="nav" ${p.include_navigation ? "checked" : ""}> Include navigation (Open URL) steps</label>
    <p class="muted">Saved with the script. The downloaded Python test uses the same settings and the same wait code. Checks (Verify ...) wait for their own condition and get no extra wait.</p>
    <div class="row"><button class="btn primary" type="submit">Save</button><button class="btn" type="button" data-close>Cancel</button></div></form>`;
  const { el, close } = openModal("Execution Wait Settings", body);
  el.querySelector("#wpForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      const { script: saved } = await api(`/api/automation/scripts/${encodeURIComponent(script.id)}/wait-policy`, { method: "PUT", body: {
        enabled: f.enabled.checked, mode: f.mode.value, timeout_ms: (Number(f.timeout.value) || 30) * 1000,
        minimum_ms: Number(f.minimum.value) || 0, fixed_ms: Number(f.fixed.value) || 1000, scope: f.scope.value,
        steps: steps1(f.steps.value), include_navigation: f.nav.checked } });
      toast("Wait settings saved with the script (new version)", "success");
      close(); onSaved(saved);
    } catch (err) { toast(err.message, "error"); }
  });
}

// Insert Wait After Steps / remove / update (bulk). saveFirst() saves unsaved step edits.
export function bulkWaits(script, saveFirst, onSaved) {
  const body = `<form id="bwForm" class="settings" style="max-width:none">
    <label>After <select name="where">
      <option value="all">every step</option><option value="selected">selected steps</option><option value="clicks">clicks</option>
      <option value="dropdowns">dropdown and filter changes</option><option value="navigation">navigation</option><option value="data">steps that load data (search, filter, next page)</option></select></label>
    <label>Selected steps (numbers) <input name="steps" placeholder="3, 5"></label>
    <label>Wait for <select name="kind">${Object.entries(WAIT_KINDS).filter(([k]) => !NEEDS_SELECTOR.has(k) && k !== "network_response").map(([k, v]) => `<option value="${k}" ${k === "ajax" ? "selected" : ""}>${v}</option>`).join("")}</select></label>
    <div class="row"><label>Timeout (s) <input name="timeout" type="number" min="1" max="300" value="30"></label><label>Delay (ms, Fixed) <input name="ms" type="number" min="50" value="1000"></label></div>
    <p class="muted">A wait is not added after a step that already has one. Remove takes out the waits added in bulk (waits you added one by one stay unless you tick the box). Update changes the timeout of the chosen waits.</p>
    <label class="row" style="gap:6px"><input type="checkbox" name="manual"> Remove also waits added one by one</label>
    <div class="row"><button class="btn primary" type="submit" data-op="insert">Insert</button><button class="btn" type="submit" data-op="update">Update</button>
      <button class="btn danger" type="submit" data-op="remove">Remove</button><button class="btn" type="button" data-close>Cancel</button></div></form>`;
  const { el, close } = openModal("Insert Wait After Steps", body);
  const form = el.querySelector("#bwForm");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const op = e.submitter?.dataset.op || "insert";
    const wait = { kind: form.kind.value, timeout_ms: (Number(form.timeout.value) || 30) * 1000 };
    if (wait.kind === "fixed") wait.ms = Number(form.ms.value) || 1000;
    if (op === "remove") wait.include_manual = form.manual.checked;
    if (op === "update") delete wait.kind;
    try {
      await saveFirst();
      const r = await api(`/api/automation/scripts/${encodeURIComponent(script.id)}/waits`, { method: "POST", body: {
        op, where: form.where.value, steps: steps1(form.steps.value), wait } });
      toast(op === "insert" ? `${r.added} wait(s) inserted${r.skipped_existing ? `, ${r.skipped_existing} step(s) already had one` : ""}`
        : op === "remove" ? `${r.removed} wait(s) removed` : `${r.updated} wait(s) updated`, "success");
      close(); onSaved(r.script);
    } catch (err) { toast(err.message, "error"); }
  });
}
