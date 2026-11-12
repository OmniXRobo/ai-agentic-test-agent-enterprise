// Automation Studio: AI-Driven, Guided Browser, Discovery & Update and Hybrid automation,
// plus the shared script editor (steps, Playwright view, versions, self-healing, results).

import { loginBlock, bindLogin } from "../login-fields.js";
import { api, preselectBrowser } from "../api.js";
import { subscribe } from "../store.js";
import { esc, toast, timeOf, dateTimeOf, isRepeat } from "../util.js";
import { discoveryPanel as discoveryPanelImpl } from "./discovery.js";
import { confirmDelete } from "../confirm-delete.js";
import { mountAiReport, openShots } from "../ai-report.js";
import { hasRule, openRuleBuilder, previewRule, ruleLabel } from "../date-rules.js";

const ACTIONS = ["goto", "click", "dblclick", "fill", "type", "select", "pick_date", "check", "uncheck", "press", "hover", "wait",
  "expect_visible", "expect_hidden", "expect_text", "expect_value", "expect_url", "screenshot",
  "upload_file", "download_file", "expect_file_name", "expect_file_text", "expect_excel_cell"];
// 23.3.17: what the Value means for the file steps (shown as the field's placeholder).
const VALUE_HINT = { upload_file: "file name(s) from Files, e.g. invoice.pdf; photo.png", download_file: "save as (optional)",
  expect_file_name: "e.g. .pdf or Invoice", expect_file_text: "text in the file (|| for more)", expect_excel_cell: "B2=Total or Sheet1!C5=10" };
const RULE_ACTIONS = new Set(["fill", "type", "pick_date", "select", "expect_value", "expect_text", "expect_file_text", "expect_excel_cell"]);

// Read a picked file as base64 for the API.
function fileBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1] || "");
    r.onerror = () => reject(new Error(`Could not read ${file.name}`));
    r.readAsDataURL(file);
  });
}

function sizeOf(n) {
  return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`;
}
const MODE_NAMES = { ai: "AI-Driven", guided: "Guided", discovery: "Discovery", hybrid: "Hybrid", manual: "Manual" };
const ROUTE_FOR_MODE = { ai: "ai-automation-classic", guided: "guided-automation", discovery: "discovery", hybrid: "hybrid-automation", manual: "guided-automation" };

let cleanups = [];
const onCleanup = (fn) => cleanups.push(fn);

function params() {
  return new URLSearchParams(location.hash.split("?")[1] || "");
}

export function evidenceUrl(path) {
  return `/api/automation/evidence?path=${encodeURIComponent(path)}`;
}

// Download the pytest bundle (zip). api() only reads JSON, so this uses fetch directly.
export async function downloadTests(body, fileName) {
  try {
    const resp = await fetch("/api/automation/export-bundle", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.message || `Download failed (HTTP ${resp.status})`);
    }
    const url = URL.createObjectURL(await resp.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: fileName });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch (err) { toast(err.message, "error"); }
}

function routeName() {
  return location.hash.replace(/^#\/?/, "").split("?")[0];
}

// Live activity feed for one run id, fed by the event stream.
function liveFeed(container, getRunId, onEvent) {
  let last = null;
  const unsub = subscribe((c) => {
    if (c.type !== "event" || !getRunId() || c.event.run_id !== getRunId()) return;
    const repeat = isRepeat(last, c.event);
    last = c.event;
    if (!repeat) {
      const failed = c.event.name.endsWith(".failed");
      container.insertAdjacentHTML("beforeend", `<div class="feed-item ${failed ? "failed" : ""}"><span class="feed-time">${esc(timeOf(c.event.timestamp))}</span><span class="feed-text">${esc((c.event.agent_name ? c.event.agent_name + " · " : "") + (c.event.activity || c.event.name))}</span></div>`);
      container.scrollTop = container.scrollHeight;
    }
    if (onEvent) onEvent(c.event);
  });
  onCleanup(unsub);
}

function scriptTable(items, emptyText) {
  if (!items.length) return `<p class="muted">${esc(emptyText)}</p>`;
  return `<table class="data"><thead><tr><th>Script</th><th>Mode</th><th>Test Case</th><th>Steps</th><th>Version</th><th>Updated</th><th>Actions</th></tr></thead><tbody>
    ${items.map((s) => `<tr class="clickable" data-script="${esc(s.id)}"><td>${esc(s.name)}<div class="muted">${esc(s.id)}</div></td>
      <td>${esc(MODE_NAMES[s.mode] || s.mode)}</td><td>${s.test_case_id ? `<a href="#/test-cases?id=${esc(s.test_case_id)}">#${esc(s.test_case_id)}</a>` : ""}</td>
      <td>${s.steps}</td><td>v${s.version}</td><td>${esc(dateTimeOf(s.updated_at))}</td>
      <td><button class="link-btn danger" data-del="${esc(s.id)}" title="Delete this script">Delete</button></td></tr>`).join("")}
    </tbody></table>`;
}

function bindScriptRows(root) {
  root.querySelectorAll("tr[data-script]").forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest("a, button")) return;
    location.hash = `#/${routeName()}?script=${encodeURIComponent(tr.dataset.script)}`;
  }));
}

// 23.3.16: Delete for a recorded (Guided, Hybrid, Discovery) script, after the confirmation. Returns true when deleted.
async function deleteRecorded(s) {
  const ok = await confirmDelete({
    name: s.name || s.id,
    detail: [s.id, s.test_case_id ? `TC${s.test_case_id}` : "", `${s.steps} steps`].filter(Boolean).join(" - "),
    note: "Its versions and evidence are kept in the archive folder.",
  });
  if (!ok) return false;
  await api(`/api/automation/scripts/${encodeURIComponent(s.id)}`, { method: "DELETE" });
  toast(`Deleted ${s.name || s.id}.`, "success");
  return true;
}

async function renderScriptList(box, modes, emptyText, { onDeleted } = {}) {
  const { items } = await api("/api/automation/scripts");
  const mine = modes ? items.filter((s) => modes.includes(s.mode)) : items;
  box.innerHTML = `<div class="row"><h3 style="margin:0">Scripts</h3><span class="spacer"></span>
      ${mine.length ? `<button class="btn" id="zipAll" title="pytest + Playwright files, one per script">Download Python tests (.zip)</button>
        <button class="btn" id="runAll">Run all ${mine.length}</button>` : ""}</div>${scriptTable(mine, emptyText)}`;
  bindScriptRows(box);
  box.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async (e) => {
    e.stopPropagation();
    const s = mine.find((x) => x.id === b.dataset.del);
    try {
      if (!s || !(await deleteRecorded(s))) return;
      if (onDeleted) await onDeleted(s); else await renderScriptList(box, modes, emptyText);
    } catch (err) { toast(err.message, "error"); }
  }));
  box.querySelector("#zipAll")?.addEventListener("click", () => downloadTests({ script_ids: mine.map((s) => s.id) }, "automated-tests.zip"));
  box.querySelector("#runAll")?.addEventListener("click", async () => {
    try {
      const r = await api("/api/automation/run-many", { method: "POST", body: { script_ids: mine.map((s) => s.id) } });
      toast(`Queued ${r.run_ids.length} runs. Follow them on Agent Workflow.`, "success");
    } catch (err) { toast(err.message, "error"); }
  });
}

// 23.3.18: the Data button: pick a column of the imported Excel / CSV instead of typing {{DATA:column}}.
function dataMenu(anchor, onPick) {
  document.querySelectorAll(".data-menu").forEach((m) => m.remove());
  const menu = document.createElement("div");
  menu.className = "data-menu card";
  menu.innerHTML = dataCtx.columns.length
    ? `<div class="muted small">Take the value from column (each row is one run):</div>
       ${dataCtx.columns.map((c) => `<button type="button" class="data-opt" data-col="${esc(c)}"><strong>${esc(c)}</strong><span class="muted"> row 1: ${esc(dataCtx.row[c] ?? "")}</span></button>`).join("")}
       <button type="button" class="data-opt" data-clear><span class="muted">Clear the value</span></button>`
    : '<div class="muted">No test data yet. Use <strong>Import Excel / CSV</strong> in the Test data card below, then pick a column here.</div>';
  // 23.3.19: placed with inline fixed styles next to the button (the .card style made it position: relative, so the
  // menu ended up below the page, out of sight). It flips above the button near the bottom of the window.
  Object.assign(menu.style, { position: "fixed", zIndex: "1000", width: "260px", maxHeight: "320px", overflow: "auto", margin: "0",
    background: "var(--surface, #0b1626)", border: "1px solid var(--border, #334)", borderRadius: "10px" });
  menu.addEventListener("mousedown", (e) => e.preventDefault()); // keep the focus (and the typed value) in the step
  document.body.appendChild(menu);
  const place = () => {
    const r = anchor.getBoundingClientRect(), vw = document.documentElement.clientWidth, vh = window.innerHeight;
    const h = menu.offsetHeight;
    const below = r.bottom + 4 + h <= vh - 8 || r.top - 4 - h < 8;
    menu.style.top = `${Math.max(8, below ? r.bottom + 4 : r.top - 4 - h)}px`;
    menu.style.left = `${Math.max(8, Math.min(r.right - 260, vw - 268))}px`;
  };
  place();
  window.addEventListener("scroll", place, true);
  window.addEventListener("resize", place);
  const close = () => {
    menu.remove(); document.removeEventListener("click", outside, true); document.removeEventListener("keydown", esc_);
    window.removeEventListener("scroll", place, true); window.removeEventListener("resize", place);
  };
  const outside = (e) => { if (!menu.contains(e.target)) close(); };
  const esc_ = (e) => { if (e.key === "Escape") close(); };
  setTimeout(() => document.addEventListener("click", outside, true), 0);
  document.addEventListener("keydown", esc_);
  menu.querySelectorAll("[data-col]").forEach((b) => b.addEventListener("click", () => { close(); onPick(b.dataset.col); }));
  menu.querySelector("[data-clear]")?.addEventListener("click", () => { close(); onPick(null); });
  onCleanup(close);
}

// ---- 23.3.17: files for upload steps -----------------------------------------------------------

function renderFiles(card, script, items) {
  const sid = encodeURIComponent(script.id);
  card.innerHTML = `<div class="row"><h3 style="margin:0">Files</h3><span class="spacer"></span>
      <label class="btn">Attach files<input type="file" id="attach" multiple hidden></label></div>
    <p class="muted">Files an <code>upload_file</code> step uploads: put the file name in its Value (several: separate with ;). Files you pick while recording are attached here automatically. Up to 50 MB each.</p>
    ${items.length ? `<table class="data"><thead><tr><th>File</th><th>Size</th><th></th></tr></thead><tbody>
      ${items.map((f) => `<tr><td><code>${esc(f.name)}</code></td><td>${esc(sizeOf(f.size))}</td>
        <td><button class="link-btn danger" data-remove="${esc(f.name)}">Remove</button></td></tr>`).join("")}</tbody></table>`
      : '<p class="muted">No files attached.</p>'}`;
  card.querySelector("#attach").addEventListener("change", async (e) => {
    let list = items;
    try {
      for (const file of e.target.files) {
        if (file.size > 50 * 1024 * 1024) { toast(`${file.name} is larger than 50 MB.`, "error"); continue; }
        ({ items: list } = await api(`/api/automation/scripts/${sid}/files`, { method: "POST", body: { name: file.name, content_base64: await fileBase64(file) } }));
      }
      toast("Files attached", "success");
    } catch (err) { toast(err.message, "error"); }
    renderFiles(card, script, list);
  });
  card.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`Remove ${b.dataset.remove}? Upload steps that use it will fail.`)) return;
    try { const r = await api(`/api/automation/scripts/${sid}/files/${encodeURIComponent(b.dataset.remove)}`, { method: "DELETE" }); renderFiles(card, script, r.items); }
    catch (err) { toast(err.message, "error"); }
  }));
}

// ---- 23.3.17: test data from Excel / CSV, one run per row ---------------------------------------

function dataFieldsOf(steps) {
  const out = [];
  for (const s of steps) for (const m of String(s.value ?? "").matchAll(/\{\{\s*DATA:([^}]+?)\s*\}\}/g)) if (!out.includes(m[1])) out.push(m[1]);
  return out;
}

function importDialog(script, file, content, onDone) {
  const el = document.createElement("div");
  el.className = "modal-backdrop";
  document.body.appendChild(el);
  const close = () => el.remove();
  const load = async (sheet, dynamic) => {
    el.innerHTML = '<div class="modal card wide"><p class="muted">Reading the file…</p></div>';
    let r;
    try {
      r = await api(`/api/automation/scripts/${encodeURIComponent(script.id)}/data-table`, { method: "POST", body: { name: file.name, content_base64: content, sheet, dynamic_dates: dynamic, preview: true } });
    } catch (err) { toast(err.message, "error"); close(); return; }
    el.innerHTML = `<div class="modal card wide" role="dialog" aria-modal="true" aria-label="Import test data">
      <div class="row"><h3 style="margin:0">Import test data: ${esc(file.name)}</h3><span class="spacer"></span><button class="icon-btn" data-close aria-label="Close">✕</button></div>
      <div class="modal-body">
        <div class="row" style="flex-wrap:wrap">
          ${r.sheets.length > 1 ? `<label>Sheet <select id="sheet">${r.sheets.map((n) => `<option ${n === r.sheet ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label>` : ""}
          <label class="row" style="gap:4px"><input type="checkbox" id="dyn" ${dynamic ? "checked" : ""}> Make dates and times dynamic (Current system date ± days, time ± hours/minutes)</label></div>
        <p><strong>${r.row_count}</strong> row(s) = ${r.row_count} run(s). Columns: ${r.columns.map((c) => `<code>{{DATA:${esc(c)}}}</code>`).join(" ")}
          ${dynamic && r.dates_converted ? `<br>${r.dates_converted} date/time cell(s) become rules.` : ""}</p>
        ${r.fields_missing.length ? `<p class="pill FAIL" style="white-space:normal">The script uses ${r.fields_missing.map((f) => `{{DATA:${esc(f)}}}`).join(", ")}, which the file has no column for. Rename the column or the step value.</p>` : ""}
        ${!r.fields_used.length ? '<p class="pill WARN" style="white-space:normal">No step uses {{DATA:...}} yet. After the import, put {{DATA:column}} in the Value of the steps that should change per row.</p>' : ""}
        <div class="table-scroll"><table class="data"><thead><tr><th>Row</th>${r.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>
          ${r.rows.slice(0, 20).map((row, i) => `<tr><td>${i + 1}</td>${r.columns.map((c) => `<td>${esc(row[c])}${hasRule(row[c]) ? `<div class="muted small">now: ${esc(previewRule(row[c]))}</div>` : ""}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
        ${r.row_count > 20 ? `<p class="muted">First 20 of ${r.row_count} rows shown.</p>` : ""}
        <div class="row"><span class="spacer"></span><button class="btn" data-close>Cancel</button><button class="btn primary" id="doImport">Import ${r.row_count} rows</button></div>
      </div></div>`;
    el.querySelector("#sheet")?.addEventListener("change", (e) => load(e.target.value, el.querySelector("#dyn").checked));
    el.querySelector("#dyn").addEventListener("change", (e) => load(el.querySelector("#sheet")?.value || r.sheet, e.target.checked));
    el.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
    el.querySelector("#doImport").addEventListener("click", async () => {
      try {
        const done = await api(`/api/automation/scripts/${encodeURIComponent(script.id)}/data-table`, { method: "POST", body: { name: file.name, content_base64: content, sheet: el.querySelector("#sheet")?.value || r.sheet, dynamic_dates: el.querySelector("#dyn").checked } });
        toast(`Imported ${done.row_count} rows from ${file.name}`, "success");
        close(); onDone();
      } catch (err) { toast(err.message, "error"); }
    });
  };
  el.addEventListener("click", (e) => { if (e.target === el) close(); });
  load(null, true);
}

function dataRunsHtml(batches) {
  if (!batches.length) return "";
  return `<h4>Data-driven runs</h4>${batches.slice(0, 5).map((b) => `
    <details class="tc" ${b.active ? "open" : ""}><summary>${esc(dateTimeOf(b.started_at))} · ${esc(b.source || "")} ·
      ${Object.entries(b.counts).map(([k, n]) => `<span class="pill ${esc(k)}">${n} ${esc(k)}</span>`).join(" ")}
      ${b.active ? `<button class="btn small" data-stopbatch="${esc(b.batch_id)}">Stop</button>` : ""}</summary>
      <table class="data"><thead><tr><th>Row</th><th>Result</th><th>Run</th><th>Error</th></tr></thead><tbody>
        ${b.rows.map((r) => `<tr><td>${r.row + 1}</td><td><span class="pill ${esc(r.status)}">${esc(r.status)}</span></td>
          <td>${r.started_at ? `<a href="#/history?run=${esc(r.run_id)}">${esc(r.run_id)}</a>` : `<span class="muted">${esc(r.run_id)}</span>`}</td>
          <td class="muted">${esc(r.error || "")}${r.failed_step !== null && r.failed_step !== undefined ? ` (step ${r.failed_step + 1})` : ""}</td></tr>`).join("")}
      </tbody></table></details>`).join("")}`;
}

function renderDataTable(card, script, batches, headless, currentSteps, onTable) {
  const sid = encodeURIComponent(script.id);
  const t = script.data_table;
  const changed = (dataCtx.columns.join("\u0001") !== (t?.columns || []).join("\u0001"));
  dataCtx.columns = t ? t.columns : [];
  dataCtx.row = t && t.rows.length ? t.rows[0] : {};
  // 23.3.18: the warnings follow the steps as they are edited (not only after Save)
  const warnings = () => {
    if (!t) return "";
    const used = dataFieldsOf(currentSteps());
    const missing = used.filter((f) => !t.columns.includes(f));
    return (missing.length ? `<p class="pill FAIL" style="white-space:normal">No column for ${missing.map((f) => `{{DATA:${esc(f)}}}`).join(", ")}.</p>` : "")
      + (!used.length ? '<p class="pill WARN" style="white-space:normal">No step uses a column yet: press Data next to a step value and pick a column, otherwise every row runs the same values.</p>' : "");
  };
  card.innerHTML = `<div class="row"><h3 style="margin:0">Test data (Excel / CSV)</h3><span class="spacer"></span>
      <label class="btn">${t ? "Replace" : "Import"} Excel / CSV<input type="file" id="importData" accept=".xlsx,.xlsm,.csv,.txt" hidden></label>
      ${t ? '<button class="btn" id="removeData">Remove</button>' : ""}</div>
    ${t ? `<p class="muted">${esc(t.source || "")}${t.sheet ? ` · sheet ${esc(t.sheet)}` : ""} · ${t.rows.length} row(s) · imported ${esc(dateTimeOf(t.imported_at))}. Each row is one run. Use <strong>Data</strong> next to a step value to take it from a column (or click a column to copy its <code>{{DATA:...}}</code>).</p>
      <div class="chips">${t.columns.map((c) => `<button type="button" class="chip" data-copy="${esc(`{{DATA:${c}}}`)}">${esc(c)}</button>`).join("")}</div>
      <div id="dataWarn">${warnings()}</div>
      <div class="table-scroll" style="margin-top:8px"><table class="data"><thead><tr><th><input type="checkbox" id="allRows" checked title="All rows"></th><th>Row</th>${t.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>
        ${t.rows.map((row, i) => `<tr><td><input type="checkbox" data-row="${i}" checked></td><td>${i + 1}</td>${t.columns.map((c) => `<td>${esc(row[c])}${hasRule(row[c]) ? `<div class="muted small">now: ${esc(previewRule(row[c]))}</div>` : ""}</td>`).join("")}</tr>`).join("")}
      </tbody></table></div>
      <div class="row" style="margin-top:8px"><span class="spacer"></span><button class="btn primary" id="runRows">Run ${t.rows.length} row(s)</button></div>`
      : `<p class="muted">Import an Excel workbook (.xlsx) or a CSV file: the first row holds the column names, every other row is one run. Put <code>{{DATA:column}}</code> in the Value of the steps that change per row. A cell can also hold a rule such as <code>{{TODAY+1d:%d/%m/%Y}}</code>.</p>`}
    <div id="batches">${dataRunsHtml(batches)}</div>`;
  card.refresh = () => { const w = card.querySelector("#dataWarn"); if (w) w.innerHTML = warnings(); };
  if (changed && onTable) setTimeout(onTable, 0);  // the steps' Data hints show the new columns
  const reload = async () => {
    const [{ script: fresh }, { items }] = await Promise.all([api(`/api/automation/scripts/${sid}`), api(`/api/automation/scripts/${sid}/data-runs`)]);
    renderDataTable(card, fresh, items, headless, currentSteps, onTable);
  };
  card.querySelector("#importData").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try { importDialog(script, file, await fileBase64(file), reload); } catch (err) { toast(err.message, "error"); }
  });
  card.querySelector("#removeData")?.addEventListener("click", async () => {
    if (!confirm("Remove the imported test data? Steps with {{DATA:...}} then have no values.")) return;
    try { await api(`/api/automation/scripts/${sid}/data-table`, { method: "DELETE" }); await reload(); } catch (err) { toast(err.message, "error"); }
  });
  card.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); toast(`Copied ${b.dataset.copy}`, "success"); } catch { toast(b.dataset.copy, "info"); }
  }));
  const boxes = () => [...card.querySelectorAll("[data-row]")];
  const runBtn = card.querySelector("#runRows");
  const count = () => { if (runBtn) runBtn.textContent = `Run ${boxes().filter((b) => b.checked).length} row(s)`; };
  card.querySelector("#allRows")?.addEventListener("change", (e) => { boxes().forEach((b) => (b.checked = e.target.checked)); count(); });
  boxes().forEach((b) => b.addEventListener("change", count));
  runBtn?.addEventListener("click", async () => {
    const chosen = boxes().filter((b) => b.checked).map((b) => Number(b.dataset.row));
    if (!chosen.length) { toast("Pick at least one row.", "error"); return; }
    try {
      const r = await api(`/api/automation/scripts/${sid}/data-runs`, { method: "POST", body: { rows: chosen.length === boxes().length ? null : chosen, headless: headless() ? true : null } });
      toast(`Running ${r.rows.length} row(s), one after the other.`, "success");
      await reload();
    } catch (err) { toast(err.message, "error"); }
  });
  card.querySelectorAll("[data-stopbatch]").forEach((b) => b.addEventListener("click", async (e) => {
    e.preventDefault();
    try { await api(`/api/automation/data-runs/${encodeURIComponent(b.dataset.stopbatch)}/stop`, { method: "POST", body: {} }); setTimeout(reload, 800); } catch (err) { toast(err.message, "error"); }
  }));
  if (batches.some((b) => b.active)) {
    const timer = setTimeout(() => { if (card.isConnected) reload().catch(() => {}); }, 3000);
    onCleanup(() => clearTimeout(timer));
  }
}

// ---- script editor ------------------------------------------------------------------------

// The line under a Value: what a rule gives now, the recorded value, or a "make it dynamic" suggestion.
// 23.3.18: the imported Excel / CSV table the Data button offers (kept up to date by the Test data card).
const dataCtx = { columns: [], row: {} };
const DATA_ACTIONS = new Set(["fill", "type", "select", "pick_date", "press", "goto", "expect_text", "expect_value", "expect_url",
  "upload_file", "download_file", "expect_file_name", "expect_file_text", "expect_excel_cell"]);

// 23.3.18: older recordings of a date picker: a click that opened it, then "Click 2" (a day). Same test as the server.
function dayOf(s) {
  if (!s || s.action !== "click") return null;
  const t = String(s.target?.text ?? "").trim();
  const m = /^\d{1,2}$/.test(t) ? t : (/^\s*click\s+(\d{1,2})\s*$/i.exec(s.description || "") || [])[1];
  const n = Number(m);
  return m && n >= 1 && n <= 31 ? n : null;
}
function calendarPairs(steps) {
  const out = {};
  for (let i = 0; i < steps.length - 1; i += 1) if (steps[i].action === "click" && dayOf(steps[i]) === null && dayOf(steps[i + 1]) !== null) out[i] = dayOf(steps[i + 1]);
  return out;
}

function valueHint(s, i, suggestions, pairs) {
  const v = s.value ?? "";
  if (pairs && pairs[i] !== undefined) {
    return `<div class="rule-hint muted">Calendar clicks (steps ${i + 1}–${i + 2}, day ${pairs[i]}): played on another day they click the same day number. <button type="button" class="suggest" data-calpair="${i}">Replace with one date step</button></div>`;
  }
  const data = /^\s*\{\{\s*DATA:([^}]+?)\s*\}\}\s*$/.exec(v);
  if (data) {
    const col = data[1], known = dataCtx.columns.includes(col);
    return `<div class="rule-hint ${known ? "muted" : ""}">${known ? `Excel column <strong>${esc(col)}</strong> · row 1: ${esc(dataCtx.row[col] ?? "")}` : `<span class="pill FAIL">No column ${esc(col)} in the imported data</span>`}</div>`;
  }
  if (hasRule(v)) {
    const label = ruleLabel(v);
    return `<div class="rule-hint muted">${label ? `${esc(label)} · ` : ""}now: <strong>${esc(previewRule(v))}</strong>${s.original_value ? ` · recorded: ${esc(s.original_value)}` : ""}</div>`;
  }
  const sug = suggestions?.[String(i)];
  if (sug && sug.original === v) {
    return `<div class="rule-hint muted">Fixed ${/:/.test(v) && !/\d{4}/.test(v) ? "time" : "date"}. <button type="button" class="suggest" data-suggest="${i}" title="${esc(sug.rule)}">Make it dynamic: ${esc(sug.label)}</button></div>`;
  }
  return "";
}

function stepRow(s, i, suggestions, pairs) {
  const t = s.target || {};
  const ruleBtn = (RULE_ACTIONS.has(s.action) ? `<button type="button" class="btn small" data-rule="${i}" title="Current system date / time, plus or minus days, hours or minutes">Rule</button>` : "")
    + (DATA_ACTIONS.has(s.action) ? `<button type="button" class="btn small" data-data="${i}" title="Take this value from a column of the imported Excel / CSV">Data</button>` : "");
  return `<tr data-i="${i}">
    <td>${i + 1}</td>
    <td><select data-f="action">${ACTIONS.map((a) => `<option ${a === s.action ? "selected" : ""}>${a}</option>`).join("")}</select></td>
    <td><input data-f="selector" value="${esc(t.selector || "")}" placeholder="${["expect_file_name", "expect_file_text", "expect_excel_cell", "goto", "wait", "expect_url"].includes(s.action) ? "(no element)" : "selector"}" style="width:100%">
      ${t.alternates?.length ? `<div class="muted" title="${esc(t.alternates.join("\n"))}">${t.alternates.length} alternate locator(s)</div>` : ""}</td>
    <td><div class="value-cell"><input data-f="value" value="${esc(s.value ?? "")}" placeholder="${esc(VALUE_HINT[s.action] || "value")}">${ruleBtn}</div>${valueHint(s, i, suggestions, pairs)}</td>
    <td><input data-f="description" value="${esc(s.description || "")}" style="width:100%"></td>
    <td><input type="checkbox" data-f="optional" ${s.optional ? "checked" : ""} title="Optional step"></td>
    <td class="row" style="gap:4px;flex-wrap:nowrap">
      <button class="link-btn" data-move="-1" title="Move up">↑</button><button class="link-btn" data-move="1" title="Move down">↓</button>
      <button class="link-btn" data-del title="Delete step">✕</button>
      <a class="link-btn" data-rec-at="${i + 1}" title="Record new steps before this step (Hybrid: the browser plays the steps before it first)">⏺</a>
      <a class="link-btn" data-rerec="${i + 1}" title="Re-record this step (healing): the recorded steps replace it">⟲</a></td></tr>`;
}

function readSteps(table, original) {
  return [...table.querySelectorAll("tbody tr")].map((tr) => {
    const orig = original[Number(tr.dataset.i)] || {};
    const get = (f) => tr.querySelector(`[data-f="${f}"]`);
    const selector = get("selector").value.trim();
    const target = selector ? { ...(orig.target || {}), selector } : null;
    if (target && orig.target && orig.target.selector !== selector) {
      // A hand-edited selector no longer matches the recorded fingerprint's alternates.
      target.alternates = [];
    }
    return {
      ...orig,
      action: get("action").value,
      target,
      value: get("value").value === "" ? null : get("value").value,
      description: get("description").value || null,
      optional: get("optional").checked,
    };
  });
}

function resultsHtml(items) {
  if (!items.length) return '<p class="muted">This script has not run yet.</p>';
  return items.map((r) => `
    <details class="tc" data-run-details="${esc(r.run_id)}">
      <summary><span class="pill ${esc(r.status)}">${esc(r.status)}</span> <a href="#/history?run=${esc(r.run_id)}">${esc(r.run_id)}</a>
        <span class="muted">${esc(dateTimeOf(r.started_at))}${r.failed_step !== null && r.failed_step !== undefined ? ` · failed at step ${r.failed_step + 1}` : ""}</span></summary>
      ${r.error ? `<p class="pill FAIL">${esc(r.error)}</p>` : ""}
      ${executionStepsHtml(r)}
      ${r.status === "RUNNING" ? "" : `<div class="ai-run-report"><h4>AI analysis report</h4><div data-ai-run="${esc(r.run_id)}"></div></div>`}
    </details>`).join("");
}

// 23.3.31: the AI analysis report of a run is loaded when its row is opened (not for all 20 runs at once)
export function bindRunReports(container) {
  container.querySelectorAll("details[data-run-details]").forEach((d) => {
    const mount = () => {
      const box = d.querySelector("[data-ai-run]");
      if (!d.open || !box || box.dataset.mounted) return;
      box.dataset.mounted = "1";
      mountAiReport(box, { kind: "run", id: box.dataset.aiRun, analyzeLabel: "Generate AI analysis report",
        intro: "The On-Premise AI reviews this run: the steps, results, times, errors and self-healing, and the script's earlier results. Read it, edit it or ask again; when you are satisfied, generate the PDF (with every step's screenshot), download it or email it." });
    };
    d.addEventListener("toggle", mount);
    mount();
  });
}

// 23.3.31: the screenshot taken after each step, next to the step; a click opens it large with previous / next
const runShots = new Map();
const fileName = (p) => p.split(/[\\/]/).pop();

function shotsOf(r) {
  const steps = r.steps || [];
  const byStep = steps.filter((s) => s.screenshot).map((s) => ({ path: s.screenshot, url: evidenceUrl(s.screenshot),
    label: `Step ${s.index + 1}: ${s.description || s.action}`, status: s.status }));
  const used = new Set(byStep.map((s) => s.path));
  const others = (r.evidence?.screenshots || []).filter((p) => !used.has(p)).map((p) => ({ path: p, url: evidenceUrl(p),
    label: /final\.png$/i.test(p) ? "Final page" : fileName(p), status: "" }));
  return [...byStep, ...others];
}

document.addEventListener("click", (e) => {
  const b = e.target.closest?.("[data-step-shot]");
  if (!b) return;
  e.preventDefault();
  const shots = runShots.get(b.dataset.stepShot) || [];
  openShots(shots, Math.max(0, shots.findIndex((s) => s.path === b.dataset.path)));
});

export function executionStepsHtml(r) {
  const shots = shotsOf(r);
  runShots.set(r.run_id, shots);
  const used = new Set((r.steps || []).map((s) => s.screenshot).filter(Boolean));
  const others = (r.evidence?.screenshots || []).filter((p) => !used.has(p));
  const thumb = (s) => s.screenshot
    ? `<button type="button" class="shot-thumb" data-step-shot="${esc(r.run_id)}" data-path="${esc(s.screenshot)}" title="Page after step ${s.index + 1}: click to enlarge"><img src="${evidenceUrl(s.screenshot)}" alt="Screenshot after step ${s.index + 1}" loading="lazy"></button>`
    : '<span class="muted small">—</span>';
  return `<div style="overflow-x:auto"><table class="data run-steps"><thead><tr><th>#</th><th>Step</th><th>Result</th><th>Time</th><th>Screenshot</th></tr></thead><tbody>
    ${(r.steps || []).map((s) => `<tr><td>${s.index + 1}</td><td>${esc(s.description || s.action)}
      ${s.healed ? `<div class="pill WARN">Self-healed: ${esc(s.healed.original)} → ${esc(s.healed.healed)} (${Number(s.healed.confidence).toFixed(2)})</div>` : ""}
      ${s.error ? `<div class="muted">${esc(s.error)}</div>` : ""}</td>
      <td><span class="pill ${esc(s.status)}">${esc(s.status)}</span></td><td>${s.duration_ms ?? ""} ms</td><td>${thumb(s)}</td></tr>`).join("")}
    </tbody></table></div>
    ${(r.steps || []).length && !used.size ? '<p class="muted small">Step screenshots are taken from 23.3.31 on; this run has only the evidence below.</p>' : ""}
    ${others.length ? `<div class="shots">${others.map((p) => `<a href="${evidenceUrl(p)}" data-step-shot="${esc(r.run_id)}" data-path="${esc(p)}"><img src="${evidenceUrl(p)}" alt="${esc(fileName(p))}" loading="lazy"><span>${esc(fileName(p))}</span></a>`).join("")}</div>` : ""}
    ${r.evidence?.video ? `<p><a href="${evidenceUrl(r.evidence.video)}" target="_blank" rel="noopener">Video recording</a></p>` : ""}`;
}

function healingHtml(items) {
  if (!items.length) return '<p class="muted">No self-healing events for this script.</p>';
  return `<table class="data"><thead><tr><th>When</th><th>Step</th><th>Original</th><th>Healed</th><th>Confidence</th><th>Result</th><th>Evidence</th><th></th></tr></thead><tbody>
    ${items.map((h) => `<tr><td>${esc(dateTimeOf(h.timestamp))}</td><td>${h.step_index + 1}</td><td><code>${esc(h.original_selector)}</code></td>
      <td>${h.healed_selector ? `<code>${esc(h.healed_selector)}</code>` : '<span class="muted">none</span>'}</td>
      <td>${Number(h.confidence).toFixed(2)}</td><td><span class="pill ${["failed", "rejected"].includes(h.result) ? "FAIL" : ["validated", "confirmed"].includes(h.result) ? "PASS" : "WARN"}">${esc(h.result)}</span></td>
      <td>${h.screenshot ? `<a href="${evidenceUrl(h.screenshot)}" target="_blank" rel="noopener">Screenshot</a>` : ""}
        <details ${h.result === "needs_confirmation" ? "open" : ""}><summary class="muted">Candidates</summary>${(h.candidates || []).map((c) => `<div><code>${esc(c.selector)}</code> ${Number(c.confidence).toFixed(2)}${h.result === "needs_confirmation" ? ` <button class="btn" type="button" data-confirm="${h.id}" data-selector="${esc(c.selector)}">Use this</button>` : ""}</div>`).join("")}</details></td>
      <td>${h.applied ? '<span class="muted">Applied</span>' : h.result === "validated" ? `<button class="btn" data-apply="${h.id}">Update script</button>`
        : h.result === "needs_confirmation" ? `<button class="btn" type="button" data-reject="${h.id}">None of these</button>` : ""}</td></tr>`).join("")}
    </tbody></table>
    ${items.filter((h) => h.result === "validated" && !h.applied).length > 1 ? `<p><button class="btn primary" data-apply-all>Apply all healed steps (${items.filter((h) => h.result === "validated" && !h.applied).length})</button></p>` : ""}
    <p class="muted">A healed locator only updates the script after a run using it passed, and only when you choose to apply it. When confidence is low the run stops and asks: pick the right element under Candidates, or choose "None of these". The previous version is kept.</p>`;
}

async function renderEditor(view, scriptId) {
  dataCtx.columns = []; dataCtx.row = {};
  const sid = encodeURIComponent(scriptId);
  const [{ script, python, python_file, versions, files: attached = [], date_suggestions: suggestions = {}, converted = [] },
    { items: results }, { items: healing }, data, { items: dataRuns = [] }] = await Promise.all([
    api(`/api/automation/scripts/${sid}`),
    api(`/api/automation/results?script_id=${sid}&limit=20`),
    api(`/api/automation/healing?script_id=${sid}`),
    api("/api/test-data").catch(() => ({ items: [] })),
    api(`/api/automation/scripts/${sid}/data-runs`).catch(() => ({ items: [] })),
  ]);
  const datasets = (data.items || []).filter((d) => !script.test_case_id || String(d.tfs_test_case_id) === String(script.test_case_id));
  let steps = script.steps;
  let runId = null;
  view.innerHTML = `
    <p><a href="#/${routeName()}">← ${esc(document.getElementById("pageTitle").textContent)}</a></p>
    <div class="card">
      <div class="row"><h2 style="margin:0">${esc(script.name)}</h2><span class="pill WAITING">${esc(MODE_NAMES[script.mode] || script.mode)}</span>
        <span class="muted">${esc(script.id)} · v${script.version}</span><span class="spacer"></span>
        ${datasets.length ? `<select id="dataset"><option value="">No test data</option>${datasets.map((d) => `<option value="${d.id}">${esc(d.name)}</option>`).join("")}</select>` : ""}
        <label class="row" style="gap:4px"><input type="checkbox" id="headless"> Headless</label>
        <button class="btn primary" id="run">Run</button><button class="btn" id="stop" hidden>Stop</button></div>
      <form class="grid" id="meta" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr));margin-top:10px">
        <label>Name <input name="name" value="${esc(script.name)}" required></label>
        <label>Start URL <input name="base_url" value="${esc(script.base_url || "")}"></label>
        <label>TFS Test Case ID <input name="test_case_id" value="${esc(script.test_case_id || "")}" inputmode="numeric"></label>
        <label>Application <input name="application" value="${esc(script.application || "")}"></label>
        <label>Credential role <input name="credential_role" value="${esc(script.credential_role || "")}" placeholder="e.g. admin"></label>
        <p class="muted" style="align-self:end">Plays in the browser chosen under <a href="#/settings">Settings &gt; Preferences</a>.</p>
      </form>
      ${script.notes ? `<p class="pill WARN" style="white-space:pre-wrap">${esc(script.notes)}</p>` : ""}
    </div>
    <div class="card" id="runBox" style="margin-top:12px" hidden><h3>Live run</h3><div class="feed" id="feed"></div></div>
    <div class="card" style="margin-top:12px">
      <div class="row"><h3 style="margin:0">Steps</h3><span class="spacer"></span>
        <button class="btn" id="addStep">Add step</button>
        <a class="btn" href="#/hybrid-automation?append=${esc(script.id)}">Record more steps</a>
        <button class="btn primary" id="save">Save</button></div>
      ${converted.length ? `<p class="pill PASS" style="white-space:normal">Made dynamic while recording: ${converted.map((c) => `step ${c.index + 1} ${esc(c.original)} → ${esc(c.label)}`).join("; ")}. Change any of them with Rule.</p>` : ""}
      <p class="muted">Use <strong>Rule</strong> for a date or time that must follow the calendar: current system date, plus or minus days, current system time, plus or minus hours and minutes. It is worked out from the Windows system clock when the test runs.
        Other runtime values: <code>{{DATA:column}}</code> (imported test data), <code>{{CRED:role.username}}</code>, <code>{{CRED:role.password}}</code>, <code>{{RANDOM:6}}</code>, <code>{{UNIQUE}}</code>.
        File steps: <code>upload_file</code> (Value = file name from Files), <code>download_file</code> (click that downloads), then <code>expect_file_name</code>, <code>expect_file_text</code> (PDF, Excel, CSV, Word, text) or <code>expect_excel_cell</code>.</p>
      <div style="overflow-x:auto"><table class="data steps" id="steps"><thead><tr><th>#</th><th>Action</th><th>Locator</th><th>Value</th><th>Description</th><th>Opt.</th><th></th></tr></thead>
        <tbody>${steps.map((st, i) => stepRow(st, i, suggestions, calendarPairs(steps))).join("")}</tbody></table></div>
    </div>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(360px,1fr));margin-top:12px">
      <div class="card" id="dataCard"></div>
      <div class="card" id="filesCard"></div>
    </div>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(360px,1fr));margin-top:12px">
      <div class="card"><h3>Recent runs</h3><div id="results">${resultsHtml(results)}</div></div>
      <div class="card"><h3>Self-healing</h3><div id="healing">${healingHtml(healing)}</div></div>
    </div>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(360px,1fr));margin-top:12px">
      <div class="card"><div class="row"><h3 style="margin:0">Python test (pytest + Playwright)</h3><span class="spacer"></span>
        <a class="btn" href="/api/automation/scripts/${esc(script.id)}/export">Download ${esc(python_file)}</a>
        <button class="btn" id="zipOne" title="With pytest.ini, requirements.txt, README.txt and RUN_TESTS.cmd">Download ready-to-run .zip</button></div>
        <p class="muted">Runs with <code>pytest</code> (one test per data set of the Test Case) or with <code>python ${esc(python_file)}</code>. Passwords are read from environment variables, never stored in the file.</p>
        <pre class="code">${esc(python)}</pre></div>
      <div class="card"><h3>Version history</h3>
        <table class="data"><tbody>${versions.slice().reverse().map((v) => `<tr><td>v${v.version}</td><td>${esc(v.reason)}</td><td>${esc(dateTimeOf(v.timestamp))}</td>
          <td>${v.version < script.version ? `<button class="link-btn" data-restore="${v.version}">Restore</button>` : '<span class="muted">current</span>'}</td></tr>`).join("")}</tbody></table>
        <p><button class="btn danger" id="archive">Delete this script</button></p></div>
    </div>`;

  view.querySelector("#zipOne").addEventListener("click", () => downloadTests({ script_ids: [script.id] }, python_file.replace(/\.py$/, ".zip")));
  const table = view.querySelector("#steps");
  const dataCard = view.querySelector("#dataCard");
  renderFiles(view.querySelector("#filesCard"), script, attached);
  renderDataTable(dataCard, script, dataRuns, () => view.querySelector("#headless").checked, () => readSteps(table, steps), () => redraw());
  const rebind = () => {
    table.querySelectorAll("[data-rule]").forEach((b) => (b.onclick = () => {
      steps = readSteps(table, steps);
      const i = Number(b.dataset.rule), st = steps[i];
      openRuleBuilder(st.value, (rule) => {
        if (!hasRule(st.value) && st.value && !st.original_value) st.original_value = st.value;
        st.value = rule; redraw();
      }, { recorded: st.original_value || (hasRule(st.value) ? null : st.value) });
    }));
    table.querySelectorAll("[data-suggest]").forEach((b) => (b.onclick = () => {
      steps = readSteps(table, steps);
      const i = Number(b.dataset.suggest), sug = suggestions[String(i)];
      if (!sug) return;
      steps[i].original_value = steps[i].value; steps[i].value = sug.rule; redraw();
      toast(`Step ${i + 1} is now ${sug.label}. Save to keep it.`, "success");
    }));
    table.querySelectorAll('[data-f="action"]').forEach((sel) => (sel.onchange = () => { steps = readSteps(table, steps); redraw(); }));
    table.querySelectorAll('[data-f="value"]').forEach((inp) => (inp.onchange = () => { steps = readSteps(table, steps); redraw(); }));
    // 23.3.19: no blur on press, so a value being typed does not redraw the table under the click
    table.querySelectorAll("[data-data],[data-rule]").forEach((b) => (b.onmousedown = (e) => e.preventDefault()));
    table.querySelectorAll("[data-data]").forEach((b) => (b.onclick = (e) => {
      e.stopPropagation();
      steps = readSteps(table, steps);
      const i = Number(b.dataset.data);
      dataMenu(b, (col) => { steps[i].value = col === null ? null : `{{DATA:${col}}}`; redraw(); });
    }));
    table.querySelectorAll("[data-calpair]").forEach((b) => (b.onclick = () => {
      steps = readSteps(table, steps);
      const i = Number(b.dataset.calpair);
      openRuleBuilder("{{TODAY}}", (rule) => {
        const iso = rule.replace(/:[^}]*\}\}$/, "").replace(/\}\}$/, "") + ":%Y-%m-%d}}";  // pick_date reads ISO dates
        const label = ruleLabel(iso) || iso;
        steps.splice(i, 2, { ...steps[i], action: "pick_date", value: iso, original_value: `day ${dayOf(steps[i + 1])}`,
          description: `Pick date ${label}`, optional: false });
        redraw();
        toast(`Steps ${i + 1}–${i + 2} are now one date step: ${label}. Check its locator points at the date field or its calendar icon, then Save.`, "success");
      }, { title: "Date for the calendar", recorded: `day ${dayOf(steps[i + 1])} (month and year were not recorded)` });
    }));
    table.querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => {
      steps = readSteps(table, steps); steps.splice(Number(b.closest("tr").dataset.i), 1); redraw();
    }));
    // 23.3.21: record new steps before this step, or re-record it (Hybrid Test Automation, earlier steps played first)
    table.querySelectorAll("[data-rec-at],[data-rerec]").forEach((b) => (b.onclick = (e) => {
      e.preventDefault();
      const at = b.dataset.recAt || b.dataset.rerec;
      location.hash = `#/hybrid-automation?append=${encodeURIComponent(script.id)}&at=${at}&replay=1${b.dataset.rerec ? "&replace=1" : ""}`;
    }));
    table.querySelectorAll("[data-move]").forEach((b) => (b.onclick = () => {
      steps = readSteps(table, steps);
      const i = Number(b.closest("tr").dataset.i), j = i + Number(b.dataset.move);
      if (j < 0 || j >= steps.length) return;
      [steps[i], steps[j]] = [steps[j], steps[i]]; redraw();
    }));
  };
  const redraw = () => {
    const pairs = calendarPairs(steps);
    table.querySelector("tbody").innerHTML = steps.map((st, i) => stepRow(st, i, suggestions, pairs)).join("");
    rebind();
    dataCard.refresh?.();
  };
  rebind();
  view.querySelector("#addStep").addEventListener("click", () => {
    steps = readSteps(table, steps); steps.push({ action: "click", target: null, value: null, description: null, optional: false, timeout_ms: 10000 }); redraw();
  });
  const save = async () => {
    const f = new FormData(view.querySelector("#meta"));
    const body = { ...script, name: f.get("name"), base_url: f.get("base_url") || null, test_case_id: f.get("test_case_id") || null,
      application: f.get("application") || null, credential_role: f.get("credential_role") || null, browser: script.browser,
      steps: readSteps(table, steps) };
    for (const k of ["id", "version", "created_at", "updated_at"]) delete body[k];
    await api(`/api/automation/scripts/${encodeURIComponent(script.id)}`, { method: "PUT", body });
  };
  view.querySelector("#save").addEventListener("click", async () => {
    try { await save(); toast("Script saved as a new version", "success"); renderEditor(view, scriptId); }
    catch (err) { toast(err.message + (err.details ? ` (${JSON.stringify(err.details).slice(0, 200)})` : ""), "error"); }
  });
  const runBtn = view.querySelector("#run"), stopBtn = view.querySelector("#stop");
  runBtn.addEventListener("click", async () => {
    try {
      const ds = view.querySelector("#dataset")?.value;
      const r = await api(`/api/automation/scripts/${encodeURIComponent(script.id)}/run`, { method: "POST", body: {
        test_data_id: ds ? Number(ds) : null, headless: view.querySelector("#headless").checked ? true : null } });
      runId = r.run_id;
      view.querySelector("#runBox").hidden = false;
      view.querySelector("#feed").innerHTML = "";
      runBtn.disabled = true; stopBtn.hidden = false;
    } catch (err) { toast(err.message, "error"); }
  });
  stopBtn.addEventListener("click", () => runId && api(`/api/automation/runs/${encodeURIComponent(runId)}/stop`, { method: "POST", body: {} }).catch((err) => toast(err.message, "error")));
  liveFeed(view.querySelector("#feed"), () => runId, (ev) => {
    if (ev.name === "execution.completed" || ev.name === "execution.failed") {
      runBtn.disabled = false; stopBtn.hidden = true;
      setTimeout(async () => {
        const [{ items }, { items: heal }] = await Promise.all([
          api(`/api/automation/results?script_id=${sid}&limit=20`),
          api(`/api/automation/healing?script_id=${sid}`),
        ]);
        view.querySelector("#results").innerHTML = resultsHtml(items);
        bindRunReports(view.querySelector("#results"));
        view.querySelector("#healing").innerHTML = healingHtml(heal);
        bindHealing();
      }, 400);
    }
  });
  const bindHealing = () => {
    view.querySelectorAll("[data-apply]").forEach((b) => b.addEventListener("click", async () => {
      try { await api(`/api/automation/healing/${b.dataset.apply}/apply`, { method: "POST", body: {} }); toast("Script updated with the healed locator", "success"); renderEditor(view, scriptId); }
      catch (err) { toast(err.message, "error"); }
    }));
    view.querySelectorAll("[data-confirm], [data-reject]").forEach((b) => b.addEventListener("click", async () => {  // final patch, defect 7
      const id = b.dataset.confirm || b.dataset.reject;
      try {
        await api(`/api/automation/healing/${id}/confirm`, { method: "POST", body: { selector: b.dataset.selector || null } });
        toast(b.dataset.selector ? "Script updated with the element you picked" : "Candidates rejected", "success");
        renderEditor(view, scriptId);
      } catch (err) { toast(err.message, "error"); }
    }));
    view.querySelector("[data-apply-all]")?.addEventListener("click", async () => {  // 23.3.21
      try {
        const r = await api("/api/automation/healing/apply-all", { method: "POST", body: { script_id: scriptId } });
        toast(`${r.applied.length} healed step(s) applied` + (r.skipped.length ? `; ${r.skipped.length} skipped (${r.skipped.map((x) => `step ${x.step}: ${x.reason}`).join("; ")})` : ""), r.applied.length ? "success" : "error", 9000);
        renderEditor(view, scriptId);
      } catch (err) { toast(err.message, "error"); }
    });
  };
  bindHealing();
  bindRunReports(view.querySelector("#results"));
  view.querySelectorAll("[data-restore]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`Restore version ${b.dataset.restore}? The current version is kept in history.`)) return;
    try { await api(`/api/automation/scripts/${encodeURIComponent(script.id)}/restore/${b.dataset.restore}`, { method: "POST", body: {} }); renderEditor(view, scriptId); }
    catch (err) { toast(err.message, "error"); }
  }));
  view.querySelector("#archive").addEventListener("click", async () => {
    try {
      if (await deleteRecorded({ ...script, steps: script.steps.length })) location.hash = `#/${routeName()}`;
    } catch (err) { toast(err.message, "error"); }
  });
}

// ---- mode panels ------------------------------------------------------------------------------

async function aiPanel(view) {
  let discoveries = [];
  try { ({ items: discoveries } = await api("/api/automation/discoveries")); } catch { /* optional */ }
  const tc = params().get("tc") || "";
  view.innerHTML = `
    <form class="card grid" id="gen" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
      <label>TFS Test Case ID <input name="test_case_id" value="${esc(tc)}" inputmode="numeric"></label>
      <label>or User Story ID (all its Test Cases) <input name="user_story_id" value="${esc(params().get("story") || "")}" inputmode="numeric"></label>
      <label>Application URL <input name="base_url" required placeholder="https://your-app/"></label>
      <label>Credential role <input name="credential_role" placeholder="e.g. admin"></label>
      <label>Application name <input name="application" placeholder="as saved in Application Credentials"></label>
      ${loginBlock()}
      <label>Element inventory <select name="discovery_id"><option value="">Latest discovery for this URL (or discover now)</option>
        ${discoveries.map((d) => `<option value="${esc(d.id)}">${esc(d.base_url)} · ${esc(dateTimeOf(d.created_at))}</option>`).join("")}</select></label>
      <div class="row" style="align-self:end"><button class="btn primary" type="submit">Create automation</button></div>
      <p class="muted" style="grid-column:1/-1">The Automation Engineering Agent reads the test case steps from TFS and maps them to elements discovered on your application. It never invents selectors: anything it could not verify is listed for review on the script.</p>
    </form>
    <div class="card" id="progress" style="margin-top:12px" hidden><h3>Activity</h3><div class="feed" id="feed"></div><p id="done"></p></div>
    <div class="card" id="list" style="margin-top:12px"><p class="muted">Loading…</p></div>`;
  let runId = null, storyId = null, pending = 0;
  bindLogin(view.querySelector("#gen"));  // final patch: sign-in fields (defect 4)
  view.querySelector("#gen").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    if (!f.test_case_id === !f.user_story_id) { toast("Enter a Test Case ID or a User Story ID.", "warning"); return; }
    try {
      const r = await api("/api/automation/generate", { method: "POST", body: {
        test_case_id: f.test_case_id ? Number(f.test_case_id) : null, user_story_id: f.user_story_id ? Number(f.user_story_id) : null,
        base_url: f.base_url.trim(), credential_role: f.credential_role || null,
        application: f.application || null, discovery_id: f.discovery_id || null } });
      runId = r.run_id;
      storyId = f.user_story_id || null;
      pending = r.test_case_ids ? r.test_case_ids.length : 1;
      view.querySelector("#progress").hidden = false;
      view.querySelector("#feed").innerHTML = "";
      view.querySelector("#done").innerHTML = "";
    } catch (err) { toast(err.message, "error"); }
  });
  liveFeed(view.querySelector("#feed"), () => runId, async (ev) => {
    // Discovery runs under the same run id and can fail without ending generation; count only this agent's results.
    if (ev.agent_id !== "automation_engineering" || !["agent.completed", "agent.failed"].includes(ev.name)) return;
    pending -= 1;
    const s = await api(`/api/automation/generate/${encodeURIComponent(runId)}`);
    const done = view.querySelector("#done");
    if (storyId) {
      const ids = s.script_ids || [];
      done.innerHTML = `${ids.length} script(s) created${pending > 0 ? `, ${pending} test case(s) to go…` : "."}
        ${ids.length && pending <= 0 ? '<button class="btn primary" id="zipStory">Download the Python tests (.zip)</button>' : ""}`;
      const sid = storyId;
      done.querySelector("#zipStory")?.addEventListener("click", () => downloadTests({ script_ids: ids }, `automated-tests-US${sid}.zip`));
    } else {
      done.innerHTML = s.script_id
        ? `<a class="btn primary" href="#/ai-automation-classic?script=${esc(s.script_id)}">Open the generated script</a>`
        : '<span class="pill FAIL">No script was created. See the activity above.</span>';
    }
    renderScriptList(view.querySelector("#list"), ["ai"], "");
  });
  await renderScriptList(view.querySelector("#list"), ["ai"], "No AI-generated scripts yet.");
}

async function guidedPanel(view, { appendTo = null } = {}) {
  let creds = [];
  try { ({ items: creds } = await api("/api/automation/credentials")); } catch { /* optional */ }
  let target = null;
  if (appendTo) {
    try { target = (await api(`/api/automation/scripts/${encodeURIComponent(appendTo)}`)).script; } catch (err) { toast(err.message, "error"); }
  }
  const roles = [...new Set(creds.map((c) => c.role))];
  const n = target ? target.steps.length : 0;
  const atParam = Number(params().get("at")) || 0;
  const firstAt = target ? (atParam >= 1 && atParam <= n + 1 ? atParam : n + 1) : 0;
  const replaceParam = target && params().get("replace") === "1" && firstAt <= n ? firstAt : 0;  // 23.3.21: re-record a step
  const replayParam = params().get("replay") === "1";
  // 23.3.20: where the recorded steps go: insert before step N, or replace steps N to M
  const placeFields = (prefix, at, replaceTo) => `
      <label>Insert at step <input name="${prefix}insert_at" type="number" min="1" max="${n + 1}" value="${at}"></label>
      <label>Recorded steps <select name="${prefix}mode"><option value="insert">are inserted before this step</option>
        <option value="replace"${replaceTo ? " selected" : ""}>replace this step (healing)</option></select></label>
      <label data-replace-to ${replaceTo ? "" : "hidden"}>Replace up to step <input name="${prefix}replace_to" type="number" min="1" max="${n}" value="${replaceTo || Math.max(1, Math.min(at, n))}"></label>`;
  view.innerHTML = `
    <form class="card grid" id="rec" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
      ${target ? `<p style="grid-column:1/-1"><strong>Adding recorded steps to:</strong> <a href="#/hybrid-automation?script=${esc(target.id)}">${esc(target.name)}</a> (${n} steps)</p>` : ""}
      <label>Start URL <input name="start_url" required value="${esc(target?.base_url || "")}" placeholder="https://your-app/"></label>
      ${target ? placeFields("", firstAt, replaceParam)
        : `<label>Script name <input name="name" placeholder="e.g. Login and search"></label>
           <label>TFS Test Case ID <input name="test_case_id" inputmode="numeric"></label>`}
      <label>Credential role <input name="credential_role" list="roles" value="${esc(target?.credential_role || "")}" placeholder="passwords become {{CRED:role.password}}"></label>
      <datalist id="roles">${roles.map((r) => `<option value="${esc(r)}">`).join("")}</datalist>
      <label>Application <input name="application" value="${esc(target?.application || "")}"></label>
      ${loginBlock()}
      <label>Browser <select name="browser"><option value="chromium">Chromium (included)</option><option value="msedge">Microsoft Edge</option><option value="chrome">Google Chrome</option></select></label>
      ${target ? `<label style="grid-column:1/-1;display:flex;flex-direction:row;gap:8px;align-items:center"><input type="checkbox" name="replay" style="width:auto;margin:0" ${(replayParam || n) && firstAt > 1 ? "checked" : ""}>
        <span>Play the script's steps before this position first (<span id="replayRange"></span>), so the browser is already on the right screen</span></label>` : ""}
      <div class="row" style="align-self:end"><button class="btn primary" type="submit">${target ? "Open browser" : "Start recording"}</button></div>
      <p class="muted" style="grid-column:1/-1">${target
        ? "A browser window opens on this PC without recording. Browse to the screen you need, then press <strong>Record</strong> (here or on the bar in the browser window). <strong>Pause</strong> lets you browse freely again; nothing is recorded until you press Record or Resume. While paused you can change where the next steps go, so one session can add steps at several places or replace broken steps. Typed passwords are never stored."
        : "A browser window opens on this PC and records at once. Perform the test steps there; each action is recorded with a fingerprint for self-healing. Use <strong>Pause</strong> to browse without recording and <strong>Resume</strong> to carry on. Typed passwords are never stored."}</p>
    </form>
    <div class="card" id="live" style="margin-top:12px" hidden>
      <div class="row" style="flex-wrap:wrap;gap:8px"><h3 style="margin:0">Recording</h3><span class="pill WAITING" id="recState">OPENING</span>
        <span class="muted" id="recWhere"></span><span class="spacer"></span>
        <button class="btn primary" id="recBtn" disabled>Record</button>
        <button class="btn" id="pauseBtn" disabled>Pause</button>
        <button class="btn primary" id="stopRec">Stop and save</button></div>
      <p class="muted" id="recNote" style="margin:8px 0 0" hidden></p>
      ${target ? `<form class="grid" id="placeForm" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr));margin:10px 0">
        <p style="grid-column:1/-1;margin:0"><strong>Where the next recorded steps go</strong> <span class="muted">(change it while paused or before you press Record)</span></p>
        ${placeFields("p_", firstAt, replaceParam)}
        <div class="row" style="align-self:end"><button class="btn" type="submit">Set position</button></div></form>` : ""}
      <form class="row" id="assertForm" style="margin:10px 0">
        <strong>Add a check</strong>
        <button class="btn" type="button" id="pickText" title="Then click the element in the browser: its text is checked">Click element: check text</button>
        <button class="btn" type="button" id="pickVisible" title="Then click the element in the browser: it must be visible">Click element: check visible</button>
        <span class="muted">or</span><input name="selector" placeholder="CSS selector, e.g. #welcome" required><input name="text" placeholder="Expected text (optional)">
        <button class="btn" type="submit">Add check</button>
        <label class="row" style="gap:6px;margin-left:auto">Pop-ups <select id="dialogs"><option value="dialog_ok">OK</option><option value="dialog_cancel">Cancel</option></select></label></form>
      <table class="data"><thead><tr><th>#</th><th>Action</th><th>Element</th><th>Value</th></tr></thead><tbody id="recSteps"></tbody></table>
    </div>
    <div class="card" id="list" style="margin-top:12px"><p class="muted">Loading…</p></div>`;
  const rec = view.querySelector("#rec");
  bindLogin(rec);  // final patch: sign-in fields for hybrid recording (defect 9)
  preselectBrowser(rec);  // 23.3.33: starts on the browser chosen in Settings (Edge when installed)
  const wirePlace = (form, prefix) => {
    if (!form) return;
    const mode = form.querySelector(`[name=${prefix}mode]`), to = form.querySelector("[data-replace-to]");
    const at = form.querySelector(`[name=${prefix}insert_at]`), toInput = form.querySelector(`[name=${prefix}replace_to]`);
    const sync = () => {
      const a = Number(at.value) || 1;
      const replace = mode.value === "replace";
      to.hidden = !replace;
      toInput.disabled = !replace || mode.disabled; // a hidden field must not block the form
      if (replace && (Number(toInput.value) || 0) < a) toInput.value = Math.min(a, n);
      at.max = replace ? n : n + 1;
      const range = view.querySelector("#replayRange");
      if (range && form === rec) range.textContent = a > 1 ? `steps 1 to ${a - 1}` : "none: the position is step 1";
    };
    mode.addEventListener("change", sync); at.addEventListener("input", sync); sync();
  };
  const placeOf = (f, prefix) => {
    const at = Number(f[`${prefix}insert_at`]) || 1;
    const replace = f[`${prefix}mode`] === "replace" ? Math.max(1, (Number(f[`${prefix}replace_to`]) || at) - at + 1) : 0;
    return { insert_at: at - 1, replace };
  };
  wirePlace(target ? rec : null, "");
  wirePlace(view.querySelector("#placeForm"), "p_");

  let runId = null, timer = null, last = null;
  const stopPolling = () => { if (timer) clearInterval(timer); timer = null; };
  onCleanup(stopPolling);
  const STATE = { ready: ["NOT RECORDING", "WAITING"], recording: ["RECORDING", "FAIL"], paused: ["PAUSED", "WARN"], replaying: ["PLAYING EARLIER STEPS", "RUNNING"] };
  const placeLabel = (g) => g.at === null || g.at === undefined ? "" : g.replace
    ? (g.replace === 1 ? `Replaces step ${g.at + 1}` : `Replaces steps ${g.at + 1} to ${g.at + g.replace}`) : `Inserted at step ${g.at + 1}`;
  const render = (s) => {
    last = s;
    const segs = s.segments || [{ at: null, replace: 0, steps: s.steps }];
    let i = 0;
    view.querySelector("#recSteps").innerHTML = segs.map((g) => {
      const head = target ? `<tr><td colspan="4" class="muted"><strong>${esc(placeLabel(g))}</strong>${g.replace && !g.steps.length ? " (no steps recorded yet: the step(s) will be removed)" : ""}</td></tr>` : "";
      return head + g.steps.map((st) => `<tr><td>${++i}</td><td>${esc(st.action)}</td>
        <td>${esc(st.target?.description || st.target?.selector || "")}</td><td>${esc(st.value ?? "")}</td></tr>`).join("");
    }).join("") || `<tr><td colspan="4" class="muted">${s.state === "recording" ? "Recording: do the steps in the browser window." : "Nothing recorded yet. Press Record when the browser is on the right screen."}</td></tr>`;
    const pill = view.querySelector("#recState"), where = view.querySelector("#recWhere");
    const recBtn = view.querySelector("#recBtn"), pauseBtn = view.querySelector("#pauseBtn");
    const note = view.querySelector("#recNote");
    note.hidden = !s.note; note.textContent = s.note || "";
    if (!s.active) {
      pill.textContent = s.error ? "ERROR" : "BROWSER CLOSED"; pill.className = `pill ${s.error ? "FAIL" : "WARN"}`;
      recBtn.disabled = pauseBtn.disabled = true;
      view.querySelector("#placeForm")?.querySelectorAll("input,select,button").forEach((x) => { x.disabled = true; });
      where.textContent = `${s.count ?? 0} step(s) recorded. Stop and save to keep them.`;
      stopPolling(); if (s.error) toast(s.error, "error");
      return;
    }
    const [label, cls] = STATE[s.state] || [s.state, "WAITING"];
    pill.textContent = label; pill.className = `pill ${cls}`;
    where.textContent = target ? `Next steps: ${s.where} · ${s.count ?? 0} recorded` : `${s.count ?? 0} recorded`;
    recBtn.textContent = s.state === "paused" ? "Resume" : "Record";
    const pt = view.querySelector("#pickText"), pv = view.querySelector("#pickVisible");
    pt.disabled = pv.disabled = s.state !== "recording";
    pt.textContent = s.pick === "text" ? "Cancel: click an element in the browser" : "Click element: check text";
    pv.textContent = s.pick === "visible" ? "Cancel: click an element in the browser" : "Click element: check visible";
    if (document.activeElement !== view.querySelector("#dialogs")) view.querySelector("#dialogs").value = s.dialogs === "dismiss" ? "dialog_cancel" : "dialog_ok";
    recBtn.disabled = s.state === "recording" || s.state === "replaying";
    pauseBtn.disabled = s.state !== "recording";
    const pf = view.querySelector("#placeForm");
    if (pf) {
      const editing = pf.contains(document.activeElement);
      if (!editing && s.at !== null && s.at !== undefined) {
        pf.querySelector("[name=p_insert_at]").value = s.at + 1;
        pf.querySelector("[name=p_mode]").value = s.replace ? "replace" : "insert";
        pf.querySelector("[name=p_replace_to]").value = s.replace ? s.at + s.replace : Math.max(1, Math.min(s.at + 1, n));
        pf.querySelector("[data-replace-to]").hidden = !s.replace;
      }
      pf.querySelectorAll("input,select,button").forEach((x) => { x.disabled = s.state === "recording"; });
      if (pf.querySelector("[name=p_mode]").value !== "replace") pf.querySelector("[name=p_replace_to]").disabled = true;
    }
  };
  const poll = async () => {
    try { render(await api(`/api/automation/recordings/${encodeURIComponent(runId)}`)); }
    catch (err) { stopPolling(); toast(err.message, "error"); }
  };
  const control = async (body) => {
    try { render(await api(`/api/automation/recordings/${encodeURIComponent(runId)}/control`, { method: "POST", body })); }
    catch (err) { toast(err.message, "error"); }
  };
  rec.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const place = target ? placeOf(f, "") : { insert_at: null, replace: 0 };
    try {
      const r = await api("/api/automation/recordings", { method: "POST", body: {
        start_url: f.start_url.trim(), name: f.name || null, test_case_id: f.test_case_id || null, append_to: target?.id || null,
        insert_at: place.insert_at, replace: place.replace, replay: !!(target && f.replay && place.insert_at > 0),
        credential_role: f.credential_role || null, application: f.application || null, browser: f.browser } });
      runId = r.run_id;
      view.querySelector("#live").hidden = false;
      e.target.querySelectorAll("input,select,button").forEach((x) => { x.disabled = true; });
      timer = setInterval(poll, 1000);
      poll();
    } catch (err) { toast(err.message, "error"); }
  });
  view.querySelector("#recBtn").addEventListener("click", () => control({ action: "record" }));
  // 23.3.21: click-to-check in the browser, and how pop-ups are answered
  view.querySelector("#pickText").addEventListener("click", () => control({ action: last?.pick === "text" ? "pick_off" : "pick_text" }));
  view.querySelector("#pickVisible").addEventListener("click", () => control({ action: last?.pick === "visible" ? "pick_off" : "pick_visible" }));
  view.querySelector("#dialogs").addEventListener("change", (e) => control({ action: e.target.value }));
  view.querySelector("#pauseBtn").addEventListener("click", () => control({ action: "pause" }));
  view.querySelector("#placeForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    control({ action: "position", ...placeOf(Object.fromEntries(new FormData(e.target)), "p_") });
  });
  view.querySelector("#assertForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try { await api(`/api/automation/recordings/${encodeURIComponent(runId)}/assert`, { method: "POST", body: { selector: f.selector, text: f.text || null } }); e.target.reset(); poll(); }
    catch (err) { toast(err.message, "error"); }
  });
  view.querySelector("#stopRec").addEventListener("click", async () => {
    stopPolling();
    try {
      const { script, converted = [], summary } = await api(`/api/automation/recordings/${encodeURIComponent(runId)}/stop`, { method: "POST", body: {} });
      if (summary && !summary.length) toast("Nothing was recorded; the script is unchanged.", "info");
      else toast((summary ? `Saved: ${summary.join("; ")}` : `Saved ${script.steps.length} steps`)
        + (converted.length ? `; ${converted.length} date/time value(s) made dynamic` : ""), "success");
      location.hash = `#/${ROUTE_FOR_MODE[script.mode] || routeName()}?script=${encodeURIComponent(script.id)}`;
    } catch (err) { toast(err.message, "error"); if (last?.active) timer = setInterval(poll, 1000); }
  });
  await renderScriptList(view.querySelector("#list"), appendTo || routeName() === "hybrid-automation" ? ["hybrid"] : ["guided", "manual"],
    routeName() === "hybrid-automation" ? "No hybrid scripts yet. Open any script and choose Record more steps, or record extra steps into an AI-generated script." : "No recorded scripts yet.");
}

async function hybridPanel(view) {
  const appendTo = params().get("append");
  if (appendTo) return guidedPanel(view, { appendTo });
  const { items } = await api("/api/automation/scripts");
  view.innerHTML = `
    <div class="card"><h3>Combine AI-generated and recorded steps</h3>
      <p class="muted">Pick a script, then record additional steps in the browser and insert them at any position. AI-generated scripts become Hybrid once recorded steps are added; every change is kept as a version.</p>
      ${items.length ? `<form class="row" id="pick"><select name="sid" required>${items.map((s) => `<option value="${esc(s.id)}">${esc(s.name)} (${esc(MODE_NAMES[s.mode] || s.mode)}, ${s.steps} steps)</option>`).join("")}</select>
        <button class="btn primary" type="submit">Record more steps</button></form>` : '<p class="muted">Create a script first with AI-Driven or Guided Browser Automation.</p>'}
    </div>
    <div class="card" id="list" style="margin-top:12px"><p class="muted">Loading…</p></div>`;
  view.querySelector("#pick")?.addEventListener("submit", (e) => {
    e.preventDefault();
    location.hash = `#/hybrid-automation?append=${encodeURIComponent(new FormData(e.target).get("sid"))}`;
  });
  await renderScriptList(view.querySelector("#list"), ["hybrid"], "No hybrid scripts yet.",
    { onDeleted: () => hybridPanel(view) });   // the script picker above lists it too
}

function discoveryPanel(view) {
  return discoveryPanelImpl(view, { onCleanup, liveFeed, renderScriptList });
}

function page(panel) {
  return {
    async mount(view) {
      const scriptId = params().get("script");
      if (scriptId) await renderEditor(view, scriptId); else await panel(view);
    },
    unmount() {
      cleanups.forEach((fn) => fn());
      cleanups = [];
    },
  };
}

export const aiAutomation = page(aiPanel);
export const guidedAutomation = page((view) => guidedPanel(view));
export const hybridAutomation = page(hybridPanel);
export const discovery = page(discoveryPanel);
