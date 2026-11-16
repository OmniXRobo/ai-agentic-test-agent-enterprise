// 23.8.0 Integration Comparison Workspace: one page, one flow.
// ADD APPLICATIONS -> UPLOAD SCREENSHOTS / CONNECT -> ANALYZE -> REVIEW MAPPING -> RUN COMPARISON -> VIEW RESULTS.
// Every PASS / FAIL / WARNING comes from the server's deterministic comparison and calculation rules.
// The AI only suggests mappings (a tester approves them) and writes explanations.

import { api } from "../api.js";
import { esc, toast, dateTimeOf, openModal } from "../util.js";
import { canEdit } from "../auth.js";

const BASE = "/api/comparison/workspace";
const KEY = "qa.ws.comparison";
const RESULT_CLASS = { PASS: "PASS", "PASS*": "PASS", ACCEPTED: "ACC", FAIL: "FAIL", WARNING: "WARN", NEEDS_REVIEW: "REVIEW", NOT_COMPARABLE: "NC", INCOMPLETE: "INC" };
const RESULT_TEXT = { PASS: "PASS", "PASS*": "PASS*", ACCEPTED: "EXPECTED", FAIL: "FAIL", WARNING: "WARNING", NEEDS_REVIEW: "NEEDS REVIEW", NOT_COMPARABLE: "NOT COMPARABLE", INCOMPLETE: "INCOMPLETE" };
const OPEN = ["FAIL", "WARNING", "NEEDS_REVIEW", "NOT_COMPARABLE", "INCOMPLETE"];
const EXTRACTION_TEXT = { COMPLETE: "Read completely", PARTIAL: "Partly read", FAILED: "Could not be read", NEEDS_REVIEW: "Read, needs review" };
const STATUS_TEXT = { auto_mapped: "Auto-mapped", needs_review: "Needs review", unmapped: "Unmapped", approved: "Approved", rejected: "Not the same" };
const DECISIONS = [["actual_defect", "Genuine defect"], ["valid_business_difference", "Expected difference"], ["expected_calculation_difference", "Expected calculation difference"],
  ["false_positive", "False positive"], ["investigate", "Further review"]];
const SOURCE_TEXT = { screenshot: "Screenshot", live: "Live application", file: "Exported file" };

let st = null; // { id, data, cfg, run, filter }
let root = null;

const remembered = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
const remember = (v) => { try { localStorage.setItem(KEY, v || ""); } catch { /* storage blocked */ } };
const pill = (r) => `<span class="pill ${RESULT_CLASS[r] || "WARN"}">${esc(RESULT_TEXT[r] || r)}</span>`;
const pct = (c) => (c === undefined || c === null) ? "" : `${Math.round(Number(c) * 100)}%`;
const appsOf = () => st.data.applications;
const appName = (id) => (appsOf().find((a) => a.id === id) || {}).name || id;
const letterOf = (id) => (appsOf().find((a) => a.id === id) || {}).letter || "?";

async function load(id) {
  st = { id, data: await api(`${BASE}/${encodeURIComponent(id)}`), cfg: null, run: null, filter: "all" };
  if (st.data.analysis) st.cfg = structuredClone(st.data.analysis.config);
  if (st.data.runs?.length) {
    try { st.run = (await api(`${BASE}/runs/${st.data.runs[0].id}`)).run; } catch { st.run = null; }
  }
}

async function reload() { await load(st.id); render(); }

export async function mount(view) {
  root = document.createElement("div");
  root.className = "ws";
  view.appendChild(root);
  root.innerHTML = '<p class="muted">Loading…</p>';
  const { items } = await api(BASE);
  let id = remembered();
  if (!items.some((x) => x.id === id)) id = (items.find((x) => x.workspace) || items[0] || {}).id;
  if (id) { await load(id); render(); }
  else renderEmpty();
}

export function unmount() { root = null; st = null; }

function renderEmpty() {
  root.innerHTML = `<div class="card ws-hero"><h2>Integration Comparison</h2>
    <p>Compare the same business data shown by two or more applications: tables, calculated values, KPI cards and charts.</p>
    ${canEdit() ? '<button class="btn primary" id="wsNew">Start a comparison</button>' : '<p class="muted">Ask an editor to create a comparison.</p>'}</div>`;
  root.querySelector("#wsNew")?.addEventListener("click", newComparison);
}

function newComparison() {
  const { el, close } = openModal("New comparison", `<form class="grid" id="nc">
    <label>Name <input name="name" required maxlength="120" placeholder="e.g. Orders: ERP vs Sales Portal vs Analytics"></label>
    <label>Applications (one per line) <textarea name="apps" rows="3">Application A\nApplication B</textarea></label>
    <div class="row"><button class="btn primary">Create</button></div></form>`);
  el.querySelector("#nc").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const names = String(f.get("apps") || "").split("\n").map((s) => s.trim()).filter(Boolean);
    try {
      const r = await api(BASE, { method: "POST", body: { name: f.get("name"), applications: names.length ? names : undefined } });
      remember(r.comparison.id); close(); await load(r.comparison.id); render();
    } catch (err) { toast(err.message, "error"); }
  });
}

// ---- page ----------------------------------------------------------------------------------------------------------

function render() {
  if (!root || !st) return;
  remember(st.id);
  const d = st.data;
  const saved = d.saved_mapping;
  const steps = [["Add applications", appsOf().some((a) => a.inputs.screenshots.length || a.inputs.datasets.length)],
    ["Analyze", !!d.analysis], ["Review mapping", !!saved], ["Run comparison", !!st.run], ["Results", !!st.run]];
  root.innerHTML = `
    <div class="card row ws-top">
      <label class="ws-pick">Comparison <select id="wsSel"></select></label>
      ${canEdit() ? '<button class="btn" id="wsNew">New comparison</button>' : ""}
      <span class="spacer"></span>
      ${saved ? `<span class="muted">Saved mapping v${esc(saved.version)} · ${esc(dateTimeOf(saved.created_at))}</span> <button class="link-btn" id="wsHist">Review saved mappings</button>` : '<span class="muted">No saved mapping yet</span>'}
    </div>
    <ol class="ws-steps">${steps.map(([t, done], i) => `<li class="${done ? "done" : ""}"><b>${i + 1}</b> ${esc(t)}</li>`).join("")}</ol>
    <section class="ws-sec"><h3>Step 1 · Add applications</h3><div class="ws-apps" id="wsApps"></div></section>
    <section class="ws-sec"><h3>Step 2 · Analyze applications</h3>
      <div class="row"><button class="btn primary ws-big" id="wsAnalyze" ${canEdit() ? "" : "disabled"}>ANALYZE &amp; DETECT DATA</button>
      <label class="ws-check"><input type="checkbox" id="wsAI"> Ask the local AI about field names it cannot match</label>
      ${d.analyzed_at ? `<span class="muted">Last analyzed ${esc(dateTimeOf(d.analyzed_at))}</span>` : ""}</div>
      <div id="wsPreview"></div></section>
    <section class="ws-sec"><h3>Step 3 · AI field mapping</h3><div id="wsMap"></div></section>
    <section class="ws-sec"><h3>Step 4 · Run comparison</h3>
      <div class="row"><button class="btn primary ws-big" id="wsRun" ${saved && canEdit() ? "" : "disabled"}>RUN COMPARISON</button>
      ${saved ? "" : '<span class="muted">Approve and save the mapping first.</span>'}
      ${d.runs.length ? `<label class="ws-check">Earlier runs <select id="wsRuns">${d.runs.map((r) => `<option value="${esc(r.id)}" ${st.run && st.run.id === r.id ? "selected" : ""}>${esc(dateTimeOf(r.started_at))} · ${esc(r.result)}</option>`).join("")}</select></label>` : ""}</div></section>
    <section class="ws-sec" id="wsResults"></section>`;
  api(BASE).then(({ items }) => {
    const sel = root?.querySelector("#wsSel");
    if (!sel) return;
    sel.innerHTML = items.map((x) => `<option value="${esc(x.id)}" ${x.id === st.id ? "selected" : ""}>${esc(x.name)}</option>`).join("");
  });
  root.querySelector("#wsSel").addEventListener("change", async (e) => { await load(e.target.value); render(); });
  root.querySelector("#wsNew")?.addEventListener("click", newComparison);
  root.querySelector("#wsHist")?.addEventListener("click", history);
  root.querySelector("#wsAnalyze").addEventListener("click", analyze);
  root.querySelector("#wsRun").addEventListener("click", runComparison);
  root.querySelector("#wsRuns")?.addEventListener("change", async (e) => { st.run = (await api(`${BASE}/runs/${e.target.value}`)).run; renderResults(); });
  renderApps();
  renderPreview();
  renderMapping();
  renderResults();
}

// ---- step 1: application cards --------------------------------------------------------------------------------------

function renderApps() {
  const host = root.querySelector("#wsApps");
  host.innerHTML = appsOf().map((a) => {
    const shots = a.inputs.screenshots.map((s) => `<figure class="ws-thumb" title="${esc(s.original_name || s.id)}">
        <img src="/api/comparison/evidence/${esc(s.id)}" alt="" loading="lazy">
        <figcaption>${s.status === "unreadable" ? '<span class="pill FAIL">not readable</span>' : s.status === "read" ? '<span class="pill PASS">read</span>' : s.status === "nothing_found" ? '<span class="pill WARN">no data found</span>' : '<span class="muted">new</span>'}
        ${canEdit() ? `<button class="icon-btn" data-rm="${esc(s.id)}" data-app="${esc(a.id)}" aria-label="Remove">✕</button>` : ""}</figcaption></figure>`).join("");
    const ds = a.inputs.datasets.map((x) => `<li>${x.method === "file" ? "Exported file" : "Live capture"}${x.file ? ` · ${esc(x.file)}` : ""} · ${x.status === "complete" ? `${esc(x.records ?? 0)} records` : x.status === "running" ? "running…" : `<span class="pill WARN">${esc(x.status)}</span> ${esc(x.reason || "")}`}
        ${canEdit() ? `<button class="icon-btn" data-rm="${esc(x.id)}" data-app="${esc(a.id)}" aria-label="Remove">✕</button>` : ""}</li>`).join("");
    return `<div class="card ws-app" data-id="${esc(a.id)}">
      <div class="row"><span class="ws-letter">${esc(a.letter)}</span>
        <input class="ws-name" value="${esc(a.name)}" maxlength="120" aria-label="Application name" ${canEdit() ? "" : "disabled"}>
        ${canEdit() && appsOf().length > 2 ? `<button class="icon-btn" data-del="${esc(a.id)}" aria-label="Remove application">✕</button>` : ""}</div>
      ${a.url ? `<p class="muted ws-url">${esc(a.url)}${a.username ? ` · ${esc(a.username)}` : ""}</p>` : ""}
      <div class="ws-shots">${shots || '<p class="muted">No screenshots yet.</p>'}</div>
      ${ds ? `<ul class="ws-ds">${ds}</ul>` : ""}
      ${canEdit() ? `<div class="row ws-actions">
        <label class="btn">Upload screenshots<input type="file" accept="image/png,image/jpeg" multiple hidden data-up="${esc(a.id)}"></label>
        <button class="btn" data-connect="${esc(a.id)}">Connect to application</button>
        <label class="btn">Upload export file<input type="file" accept=".csv,.xlsx,.json" hidden data-file="${esc(a.id)}"></label></div>
      <label class="ws-check"><input type="checkbox" data-complete="${esc(a.id)}" ${a.screens_complete ? "checked" : ""}> Screenshots show all records</label>` : ""}
    </div>`;
  }).join("") + (canEdit() ? '<button class="card ws-app ws-add" id="wsAddApp">+ Add another application</button>' : "");
  host.querySelector("#wsAddApp")?.addEventListener("click", async () => {
    try { await api(`${BASE}/${st.id}/applications`, { method: "POST", body: {} }); await reload(); } catch (err) { toast(err.message, "error"); }
  });
  host.querySelectorAll(".ws-name").forEach((inp) => inp.addEventListener("change", async () => {
    const id = inp.closest(".ws-app").dataset.id;
    try { await api(`${BASE}/applications/${id}`, { method: "PATCH", body: { name: inp.value } }); await reload(); } catch (err) { toast(err.message, "error"); }
  }));
  host.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Remove this application from the comparison?")) return;
    try { await api(`${BASE}/applications/${b.dataset.del}`, { method: "DELETE" }); await reload(); } catch (err) { toast(err.message, "error"); }
  }));
  host.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`${BASE}/applications/${b.dataset.app}/inputs/${b.dataset.rm}`, { method: "DELETE" }); await reload(); } catch (err) { toast(err.message, "error"); }
  }));
  host.querySelectorAll("[data-up]").forEach((inp) => inp.addEventListener("change", async () => {
    const files = [...inp.files];
    let ok = 0;
    for (const f of files) {
      try { await api(`${BASE}/applications/${inp.dataset.up}/screenshots?name=${encodeURIComponent(f.name)}`, { method: "POST", rawBody: f, contentType: f.type || "application/octet-stream" }); ok += 1; }
      catch (err) { toast(`${f.name}: ${err.message}`, "error"); }
    }
    if (ok) toast(`${ok} screenshot(s) added`, "success");
    await reload();
  }));
  host.querySelectorAll("[data-file]").forEach((inp) => inp.addEventListener("change", async () => {
    const f = inp.files[0];
    if (!f) return;
    try { const r = await api(`${BASE}/applications/${inp.dataset.file}/file?name=${encodeURIComponent(f.name)}`, { method: "POST", rawBody: f, contentType: "application/octet-stream" }); toast(`${r.dataset.records} records imported`, "success"); }
    catch (err) { toast(err.message, "error"); }
    await reload();
  }));
  host.querySelectorAll("[data-complete]").forEach((c) => c.addEventListener("change", async () => {
    try { await api(`${BASE}/applications/${c.dataset.complete}`, { method: "PATCH", body: { screens_complete: c.checked } }); } catch (err) { toast(err.message, "error"); }
  }));
  host.querySelectorAll("[data-connect]").forEach((b) => b.addEventListener("click", () => connect(appsOf().find((a) => a.id === b.dataset.connect))));
}

function connect(a) {
  const { el, close } = openModal(`Connect to ${a.name}`, `<form class="grid" id="cf" autocomplete="off">
    <p class="muted">The page is opened in the browser and only read. Nothing is changed in the application.</p>
    <label>Page address <input name="url" value="${esc(a.url || "")}" placeholder="https://reports.company.local/orders" required></label>
    <label>Sign-in <select name="auth_method">${[["none", "No sign-in"], ["form", "Login form (username and password)"], ["windows", "Windows sign-in"], ["basic", "Basic"], ["manual", "I sign in myself (SSO / MFA)"]]
      .map(([v, t]) => `<option value="${v}" ${v === a.auth_method ? "selected" : ""}>${t}</option>`).join("")}</select></label>
    <label>Username <input name="username" value="${esc(a.username || "")}" autocomplete="off"></label>
    <label>Password <input name="password" type="password" autocomplete="new-password" placeholder="${a.has_password ? "Saved. Leave blank to keep it" : "Kept in Windows Credential Manager"}"></label>
    <details><summary>Advanced: steps to reach the page</summary>
      <label>Steps (one per line: click Text, or fill Label = value) <textarea name="steps" rows="3" placeholder="click Reports\nclick Order Summary"></textarea></label></details>
    <div class="row"><button class="btn primary" name="go" value="capture">Save and capture data</button><button class="btn" name="go" value="save">Save only</button></div></form>`, { wide: true });
  el.querySelector("#cf").addEventListener("submit", async (e) => {
    e.preventDefault();
    const go = e.submitter?.value || "capture";
    const f = Object.fromEntries(new FormData(e.target));
    try {
      await api(`${BASE}/applications/${a.id}`, { method: "PATCH", body: { url: f.url, auth_method: f.auth_method, username: f.username || null, password: f.password || null } });
      if (go === "capture") {
        await api(`${BASE}/applications/${a.id}/capture`, { method: "POST", body: { steps: parseSteps(f.steps || "") } });
        toast("Capturing the page… Analyze when it has finished.", "success");
      }
      close(); await reload();
      if (go === "capture") setTimeout(() => st && reload(), 6000);
    } catch (err) { toast(err.message, "error"); }
  });
}

function parseSteps(text) {
  return text.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const fill = l.match(/^fill\s+(.+?)\s*=\s*(.*)$/i);
    if (fill) return { action: "fill", label: fill[1], value: fill[2] };
    const click = l.match(/^click\s+(.+)$/i);
    if (click) return { action: "click", text: click[1] };
    const open = l.match(/^(?:open|go to)\s+(https?:\/\/\S+)$/i);
    if (open) return { action: "goto", url: open[1] };
    return { action: "click", text: l };
  });
}

// ---- step 2: analysis preview ---------------------------------------------------------------------------------------

async function analyze() {
  const btn = root.querySelector("#wsAnalyze");
  btn.disabled = true; btn.textContent = "Analyzing…";
  try {
    await api(`${BASE}/${st.id}/analyze`, { method: "POST", body: { use_ai: root.querySelector("#wsAI").checked } });
    await reload();
    toast("Analysis finished. Review the mapping below.", "success");
  } catch (err) { toast(err.message, "error"); btn.disabled = false; btn.textContent = "ANALYZE & DETECT DATA"; }
}

function cellHtml(c, extra = "") {
  if (!c) return '<td class="ws-missing" title="Not found">—</td>';
  const low = c.confidence !== null && c.confidence !== undefined && Number(c.confidence) < 0.8;
  const fixed = c.method === "tester";
  const where = c.where && c.evidence ? ` data-ev="${esc(c.evidence)}" data-where='${esc(JSON.stringify(c.where))}'` : "";
  return `<td class="${low ? "ws-low" : ""} ${fixed ? "ws-fixed" : ""} ${where ? "ws-edit" : ""}"${where} ${extra} title="${low ? `Read with ${pct(c.confidence)} confidence: click to correct` : fixed ? `Corrected by a tester (read as ${esc(c.ocr_value ?? "")})` : ""}">${esc(c.value ?? "")}</td>`;
}

function renderPreview() {
  const host = root.querySelector("#wsPreview");
  const an = st.data.analysis;
  if (!an) { host.innerHTML = '<p class="muted">Upload screenshots or connect the applications, then click Analyze.</p>'; return; }
  host.innerHTML = an.apps.map((a) => {
    const cov = a.coverage;
    const src = [cov.screenshots ? `${cov.screenshots} screenshot(s)` : "", cov.live ? "live application" : "", cov.files ? "exported file" : ""].filter(Boolean).join(" + ") || "no data";
    const tables = a.tables.map((t) => `<div class="ws-tbl"><div class="muted">Table · ${esc(SOURCE_TEXT[t.source] || t.source)} · ${t.total_rows} row(s)</div>
      <div class="table-wrap"><table class="data compact"><thead><tr>${t.headers.map((h, i) => `<th ${t.source === "screenshot" && t.origins?.[0] ? `class="ws-edit" data-ev="${esc(t.origins[0].evidence)}" data-where='${esc(JSON.stringify({ kind: "header", table: t.origins[0].table, column: i }))}'` : ""}>${esc(h)}</th>`).join("")}</tr></thead>
      <tbody>${t.rows.slice(0, 12).map((r) => `<tr>${t.headers.map((h) => cellHtml(r[h])).join("")}</tr>`).join("")}</tbody></table></div>
      ${t.total_rows > 12 ? `<p class="muted">… and ${t.total_rows - 12} more</p>` : ""}</div>`).join("");
    const vals = Object.entries(a.values);
    const values = vals.length ? `<div class="ws-tbl"><div class="muted">Values and KPI cards</div><table class="data compact"><tbody>${vals.map(([k, c]) => `<tr><th>${esc(k)}</th>${cellHtml(c)}<td class="muted">${esc(SOURCE_TEXT[c.source] || c.source || "")}</td></tr>`).join("")}</tbody></table></div>` : "";
    const charts = a.charts.map((ch) => `<div class="ws-tbl"><div class="muted">Chart · ${esc(ch.title)} · ${esc(SOURCE_TEXT[ch.source] || ch.source)} ${ch.extracted ? "" : '<span class="pill WARN">visual check needed</span>'}</div>
      <table class="data compact"><thead><tr>${ch.categories.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody><tr>${ch.categories.map((c) => {
        const p = ch.points[c];
        if (p) return cellHtml(p);
        return ch.source === "screenshot" && ch.evidence ? `<td class="ws-low ws-edit" data-ev="${esc(ch.evidence)}" data-where='${esc(JSON.stringify({ kind: "chart", chart: an.apps.find((x) => x.app_id === a.app_id).charts.filter((y) => y.source === "screenshot" && y.evidence === ch.evidence).indexOf(ch), category: c }))}' title="Not readable: click to enter the value you see">?</td>` : "<td>?</td>";
      }).join("")}</tr></tbody></table>${ch.note ? `<p class="muted">${esc(ch.note)}</p>` : ""}</div>`).join("");
    return `<details class="card ws-prev" open><summary><span class="ws-letter">${esc(a.letter)}</span> <b>${esc(a.name)}</b> · ${esc(src)}
      ${cov.low_confidence ? `<span class="pill WARN">${cov.low_confidence} value(s) to check</span>` : ""} ${cov.complete ? '<span class="pill PASS">complete</span>' : '<span class="pill WARN">partial</span>'}
      ${cov.extraction ? `<span class="pill ${cov.extraction === "COMPLETE" ? "PASS" : cov.extraction === "FAILED" ? "FAIL" : "WARN"}" title="Extraction status">${esc(EXTRACTION_TEXT[cov.extraction] || cov.extraction)}</span>` : ""}
      ${cov.scope ? `<span class="muted" title="Scope of the data">${esc({ verified: "all records verified", declared: "all records (declared by you)", visible: "visible records only" }[cov.scope] || cov.scope)}</span>` : ""}</summary>
      ${(cov.inputs || []).filter((i) => i.status !== "COMPLETE" && (i.issues || []).length).map((i) => `<p class="muted">${esc(i.kind || "input")}: ${esc(EXTRACTION_TEXT[i.status] || i.status)}: ${esc((i.issues || []).join("; "))}</p>`).join("")}
      ${cov.notes.length ? `<ul class="ws-notes">${cov.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
      ${tables}${values}${charts || ""}${!tables && !values && !charts ? '<p class="muted">Nothing was detected.</p>' : ""}</details>`;
  }).join("") + (an.config.notes?.length ? `<div class="card ws-warn">${an.config.notes.map((n) => `<p>${esc(n)}</p>`).join("")}</div>` : "")
    + (an.config.ai ? `<p class="muted">Local AI: ${esc(an.config.ai.used ? `${an.config.ai.added} extra suggestion(s), all marked for review${an.config.ai.note ? `. ${an.config.ai.note}` : ""}` : an.config.ai.note)}</p>` : "");
  if (!canEdit()) return;
  host.querySelectorAll(".ws-edit").forEach((td) => td.addEventListener("click", () => correct(td)));
}

function correct(td) {
  const where = JSON.parse(td.dataset.where);
  const label = where.kind === "header" ? "Correct the column name" : "Correct the value";
  const { el, close } = openModal(label, `<form class="grid" id="cv"><p class="muted">Type exactly what the screenshot shows. The value that was read is kept in the history.</p>
    <img class="ws-ev-img" src="/api/comparison/evidence/${esc(td.dataset.ev)}" alt="">
    <label>Value <input name="v" value="${esc(td.textContent === "?" ? "" : td.textContent)}" required maxlength="200"></label>
    <div class="row"><button class="btn primary">Save correction</button></div></form>`, { wide: true });
  el.querySelector("#cv").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api(`${BASE}/${st.id}/correct`, { method: "POST", body: { evidence_id: td.dataset.ev, where, value: e.target.v.value } });
      close();
      await api(`${BASE}/${st.id}/analyze`, { method: "POST", body: { use_ai: false } });
      await reload();
    } catch (err) { toast(err.message, "error"); }
  });
}

// ---- step 3: mapping ---------------------------------------------------------------------------------------------

function fieldsOf(appId, kinds = ["column", "value"]) {
  const a = st.data.analysis?.apps.find((x) => x.app_id === appId);
  return (a?.fields || []).filter((f) => kinds.includes(f.kind));
}

function distinctValues(appId, field) {
  const a = st.data.analysis?.apps.find((x) => x.app_id === appId);
  if (!a) return [];
  const out = new Set();
  for (const t of a.tables) if (t.headers.includes(field)) for (const r of t.rows) if (r[field]?.value) out.add(String(r[field].value).trim());
  if (a.values[field]?.value) out.add(String(a.values[field].value).trim());
  return [...out];
}

function renderMapping() {
  const host = root.querySelector("#wsMap");
  const cfg = st.cfg;
  if (!cfg) { host.innerHTML = '<p class="muted">The mapping appears here after the analysis.</p>'; return; }
  const apps = appsOf();
  const edit = canEdit();
  const savedV = st.data.analysis?.saved_version;
  const rank = (r) => (r.is_key ? 0 : r.status === "needs_review" ? 1 : r.status === "unmapped" ? 3 : 2);
  const all = cfg.fields.filter((r) => r.status !== "rejected").sort((x, y) => rank(x) - rank(y));
  const hiddenCount = all.filter((r) => r.status === "unmapped").length;
  const rows = st.showUnmapped ? all : all.filter((r) => r.status !== "unmapped");
  all.forEach((r) => { if (r.approve === undefined) r.approve = r.status === "approved" || r.status === "auto_mapped" || !!r.is_key; });
  (cfg.rules || []).forEach((r) => { if (r.approve === undefined) r.approve = r.status === "approved" || r.review === "auto_mapped"; });
  (cfg.charts || []).forEach((c) => { if (c.approve === undefined) c.approve = c.status === "approved" || c.review === "auto_mapped"; });
  (cfg.statuses || []).forEach((s) => s.groups.forEach((g) => { if (g.approve === undefined) g.approve = g.status === "approved" || g.status === "auto_mapped"; }));
  const counts = { auto_mapped: 0, needs_review: 0, unmapped: 0, approved: 0 };
  all.forEach((r) => { counts[r.status] = (counts[r.status] || 0) + 1; });
  const conf = (r) => Math.min(...Object.values(r.members).map((m) => m.confidence ?? 1));
  const names = Object.fromEntries(apps.map((a) => [a.letter, a.name]));
  const pretty = (expr) => esc(expr.replaceAll("*", "×").replaceAll("/", "÷")).replace(/\[([A-Z]{1,2}):([^\]]+)\]/g, (_, l, f) => `<b>${esc(names[l] || l)}</b> › ${f}`);
  host.innerHTML = `
    ${savedV ? `<p class="ws-info">Your saved mapping (version ${esc(savedV)}) is reused: approved fields stay as you approved them. Only new or changed fields need review.</p>` : ""}
    <p class="muted">${counts.auto_mapped || 0} auto-mapped · ${counts.needs_review || 0} need review · ${counts.unmapped || 0} unmapped${counts.approved ? ` · ${counts.approved} approved earlier` : ""}. Tick <b>Same</b> for fields that hold the same business information.</p>
    <div class="table-wrap"><table class="data ws-map"><thead><tr><th>Same</th><th>Business field</th>${apps.map((a) => `<th>${esc(a.letter)} · ${esc(a.name)}</th>`).join("")}<th>AI confidence</th><th>Status</th></tr></thead>
    <tbody>${rows.map((r) => `<tr data-row="${esc(r.id)}" class="ws-st-${esc(r.status)}">
      <td><input type="checkbox" data-approve="${esc(r.id)}" ${r.approve ? "checked" : ""} ${edit && Object.keys(r.members).length > 1 ? "" : "disabled"} aria-label="Approve"></td>
      <td><input class="ws-fname" data-name="${esc(r.id)}" value="${esc(r.name)}" ${edit ? "" : "disabled"}>${r.is_key ? ' <span class="pill ACC" title="Records are matched on this field">record key</span>' : ""}</td>
      ${apps.map((a) => `<td><select data-member="${esc(r.id)}" data-app="${esc(a.id)}" ${edit ? "" : "disabled"}><option value="">— not shown —</option>${fieldsOf(a.id).map((f) => `<option value="${esc(f.name)}" ${(r.members[a.id] || {}).field === f.name ? "selected" : ""}>${esc(f.name)}${f.kind === "value" ? " (value)" : ""}</option>`).join("")}</select></td>`).join("")}
      <td title="${esc((r.evidence || []).join("\n"))}">${Object.keys(r.members).length > 1 ? pct(conf(r)) : "—"}</td>
      <td><span class="pill ${r.status === "auto_mapped" || r.status === "approved" ? "PASS" : r.status === "needs_review" ? "WARN" : "IDLE"}">${esc(STATUS_TEXT[r.status] || r.status)}</span></td></tr>`).join("")}</tbody></table></div>
    ${hiddenCount ? `<button class="link-btn" id="wsUnm">${st.showUnmapped ? "Hide" : "Show"} ${hiddenCount} unmapped field(s)</button> <span class="muted">Fields shown by one application only. Pick a match for any that should be compared.</span>` : ""}
    ${(cfg.statuses || []).filter((s) => s.groups.length).map((s) => `<h4>Status values · ${esc((cfg.fields.find((f) => f.id === s.field_id) || {}).name || s.field || "")}</h4>
      <p class="muted">Values that mean the same thing in each application. A comparison passes only when the approved mapping says they are equal.</p>
      <table class="data ws-map"><thead><tr><th>Same</th><th>Meaning</th>${apps.map((a) => `<th>${esc(a.letter)}</th>`).join("")}<th>Confidence</th></tr></thead><tbody>
      ${s.groups.map((g, gi) => `<tr><td><input type="checkbox" data-sg="${esc(s.field_id)}" data-gi="${gi}" ${g.approve ? "checked" : ""} ${edit ? "" : "disabled"}></td><td><input class="ws-fname" data-sgname="${esc(s.field_id)}" data-gi="${gi}" value="${esc(g.name)}" ${edit ? "" : "disabled"}></td>
        ${apps.map((a) => { const fr = cfg.fields.find((f) => f.id === s.field_id); const fm = fr?.members[a.id]; if (!fm) return "<td>—</td>";
          const opts = distinctValues(a.id, fm.field); return `<td><select data-sv="${esc(s.field_id)}" data-gi="${gi}" data-app="${esc(a.id)}" ${edit ? "" : "disabled"}><option value="">—</option>${opts.map((o) => `<option ${g.values[a.id] === o ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></td>`; }).join("")}
        <td>${pct(g.confidence)}</td></tr>`).join("")}</tbody></table>
      ${Object.keys(s.unmatched || {}).length ? `<p class="ws-warn">Not mapped yet: ${Object.entries(s.unmatched).map(([aid, v]) => `${esc(appName(aid))}: ${esc(v.join(", "))}`).join(" · ")}</p>` : ""}`).join("")}
    <h4>Calculations</h4><p class="muted">How a displayed value is calculated from another application. Checked with exact arithmetic, never by the AI.</p>
    ${(cfg.rules || []).length ? `<table class="data ws-map"><thead><tr><th>Approve</th><th>Displayed value</th><th>Calculation</th><th>Confidence</th><th></th></tr></thead><tbody>
      ${cfg.rules.map((r, i) => `<tr><td><input type="checkbox" data-rule="${i}" ${r.approve ? "checked" : ""} ${edit ? "" : "disabled"}></td>
        <td><b>${esc(appName(r.app))}</b> › ${esc(r.field)}</td><td>${pretty(r.expression)}<div class="muted">${esc(r.explanation || "")}</div>
        ${(r.mismatches || []).length ? `<div class="ws-warn">Does not match: ${r.mismatches.map((m) => `shown ${esc(m.shown)}, calculated ${esc(m.expected)}`).join("; ")}</div>` : ""}</td>
        <td>${pct(r.confidence)}</td><td>${edit ? `<button class="link-btn" data-editrule="${i}">Edit</button>` : ""}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">No calculated values were detected.</p>'}
    ${edit ? '<button class="btn" id="wsAddRule">Add a calculation</button>' : ""}
    ${(cfg.charts || []).length ? `<h4>Charts</h4><table class="data ws-map"><thead><tr><th>Approve</th><th>Chart</th><th>Compared with</th><th>Confidence</th></tr></thead><tbody>
      ${cfg.charts.map((c, i) => `<tr><td><input type="checkbox" data-chart="${i}" ${c.approve ? "checked" : ""} ${edit ? "" : "disabled"}></td><td><b>${esc(appName(c.app))}</b> › ${esc(c.chart)}</td>
        <td>${esc((c.aggregate || "sum").toUpperCase())} of <b>${esc(appName(c.source_app))}</b> › ${esc(c.value_field)} by ${esc(c.category_field)}<div class="muted">${esc(c.explanation || "")}</div></td><td>${pct(c.confidence)}</td></tr>`).join("")}</tbody></table>` : ""}
    ${edit ? '<div class="row"><button class="btn primary ws-big" id="wsSave">APPROVE &amp; SAVE MAPPING</button><span class="muted">Saved for the next run of this comparison.</span></div>' : ""}`;
  host.querySelector("#wsUnm")?.addEventListener("click", () => { st.showUnmapped = !st.showUnmapped; renderMapping(); });
  if (!edit) return;
  const rowOf = (id) => cfg.fields.find((r) => r.id === id);
  host.querySelectorAll("[data-approve]").forEach((c) => c.addEventListener("change", () => { rowOf(c.dataset.approve).approve = c.checked; }));
  host.querySelectorAll("[data-name]").forEach((i) => i.addEventListener("change", () => { rowOf(i.dataset.name).name = i.value; }));
  host.querySelectorAll("[data-member]").forEach((sel) => sel.addEventListener("change", () => {
    const r = rowOf(sel.dataset.member), app = sel.dataset.app;
    if (!sel.value) delete r.members[app];
    else {
      // a field belongs to one business field: take it out of the row it was in
      for (const other of cfg.fields) {
        if (other !== r && other.members[app]?.field === sel.value) delete other.members[app];
      }
      cfg.fields = cfg.fields.filter((x) => Object.keys(x.members).length || x === r);
      const f = fieldsOf(app).find((x) => x.name === sel.value);
      r.members[app] = { field: sel.value, kind: f?.kind || "column", confidence: 1, origin: "tester" };
    }
    r.status = Object.keys(r.members).length > 1 ? "needs_review" : "unmapped";
    r.approve = Object.keys(r.members).length > 1;
    renderMapping();
  }));
  host.querySelectorAll("[data-sg]").forEach((c) => c.addEventListener("change", () => { cfg.statuses.find((s) => s.field_id === c.dataset.sg).groups[c.dataset.gi].approve = c.checked; }));
  host.querySelectorAll("[data-sgname]").forEach((c) => c.addEventListener("change", () => { cfg.statuses.find((s) => s.field_id === c.dataset.sgname).groups[c.dataset.gi].name = c.value; }));
  host.querySelectorAll("[data-sv]").forEach((c) => c.addEventListener("change", () => {
    const g = cfg.statuses.find((s) => s.field_id === c.dataset.sv).groups[c.dataset.gi];
    if (c.value) g.values[c.dataset.app] = c.value; else delete g.values[c.dataset.app];
    g.approve = true;
  }));
  host.querySelectorAll("[data-rule]").forEach((c) => c.addEventListener("change", () => { cfg.rules[c.dataset.rule].approve = c.checked; }));
  host.querySelectorAll("[data-chart]").forEach((c) => c.addEventListener("change", () => { cfg.charts[c.dataset.chart].approve = c.checked; }));
  host.querySelectorAll("[data-editrule]").forEach((b) => b.addEventListener("click", () => editRule(cfg.rules[b.dataset.editrule])));
  host.querySelector("#wsAddRule")?.addEventListener("click", () => editRule(null));
  host.querySelector("#wsSave")?.addEventListener("click", saveMapping);
}

function editRule(rule) {
  const apps = appsOf();
  const r = rule || { app: apps[1]?.id, field: "", expression: "", scope: "record", origin: "tester" };
  const chips = apps.map((a) => `<div><b>${esc(a.letter)} · ${esc(a.name)}</b> ${fieldsOf(a.id).filter((f) => f.family === "num" || f.family === "pct").map((f) => `<button type="button" class="chip" data-ins="[${esc(a.letter)}:${esc(f.name)}]">${esc(f.name)}</button>`).join(" ")}</div>`).join("");
  const { el, close } = openModal(rule ? "Edit calculation" : "Add a calculation", `<form class="grid" id="rf">
    <label>Application that shows the value <select name="app">${apps.map((a) => `<option value="${esc(a.id)}" ${a.id === r.app ? "selected" : ""}>${esc(a.letter)} · ${esc(a.name)}</option>`).join("")}</select></label>
    <label>Value shown <select name="field"></select></label>
    <label>Calculation <textarea name="expression" rows="3" required placeholder="[A:Completed Orders] / ([A:Completed Orders] + [A:Pending Orders]) * 100">${esc(r.expression)}</textarea></label>
    <div class="muted">Click a field to insert it. Use + - * / ( ) and SUM, AVG, MIN, MAX, COUNT, ROUND.</div><div class="ws-chips">${chips}</div>
    ${(r.alternatives || []).length ? `<div><b>Other suggestions</b>${r.alternatives.map((x) => `<div><button type="button" class="link-btn" data-alt="${esc(x.expression)}">${esc(x.expression)}</button> <span class="muted">${pct(x.confidence)} · ${esc(x.explanation)}</span></div>`).join("")}</div>` : ""}
    <label>Applies to <select name="scope"><option value="record" ${r.scope === "record" ? "selected" : ""}>Each record (matched on the record key)</option><option value="total" ${r.scope === "total" ? "selected" : ""}>The whole screen (a total or KPI card)</option></select></label>
    <div class="row"><button class="btn primary">Use this calculation</button></div></form>`, { wide: true });
  const form = el.querySelector("#rf");
  const fillFields = () => { form.field.innerHTML = fieldsOf(form.app.value).map((f) => `<option ${f.name === r.field ? "selected" : ""}>${esc(f.name)}</option>`).join(""); };
  form.app.addEventListener("change", fillFields); fillFields();
  el.querySelectorAll("[data-ins]").forEach((b) => b.addEventListener("click", () => { const t = form.expression; const p = t.selectionStart ?? t.value.length; t.value = t.value.slice(0, p) + b.dataset.ins + t.value.slice(t.selectionEnd ?? p); t.focus(); }));
  el.querySelectorAll("[data-alt]").forEach((b) => b.addEventListener("click", () => { form.expression.value = b.dataset.alt; }));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const upd = { ...r, app: form.app.value, field: form.field.value, expression: form.expression.value.trim(), scope: form.scope.value, origin: "tester", approve: true, status: "suggested", confidence: 1, mismatches: [], explanation: "entered by a tester" };
    if (rule) Object.assign(rule, upd); else st.cfg.rules.push(upd);
    close(); renderMapping();
  });
}

async function saveMapping() {
  try {
    const r = await api(`${BASE}/${st.id}/mapping`, { method: "PUT", body: { config: st.cfg } });
    toast(`Mapping approved and saved (version ${r.saved.version})`, "success");
    await api(`${BASE}/${st.id}/analyze`, { method: "POST", body: { use_ai: false } });
    await reload();
  } catch (err) { toast(err.message, "error"); }
}

async function history() {
  const { items } = await api(`${BASE}/${st.id}/mapping/history`);
  openModal("Saved mappings", items.length ? `<table class="data"><thead><tr><th>Version</th><th>Approved by</th><th>When</th></tr></thead><tbody>${items.map((x) => `<tr><td>${esc(x.version)}</td><td>${esc(x.approved_by)}</td><td>${esc(dateTimeOf(x.created_at))}</td></tr>`).join("")}</tbody></table>
    <p class="muted">The latest version is used. To change it, edit the mapping in Step 3 and save again: a new version is kept, older versions stay in this history.</p>` : '<p class="muted">Nothing saved yet.</p>');
}

// ---- step 4 and 5: run and results ------------------------------------------------------------------------------------

async function runComparison() {
  const btn = root.querySelector("#wsRun");
  btn.disabled = true; btn.textContent = "Comparing…";
  try {
    const r = await api(`${BASE}/${st.id}/run`, { method: "POST" });
    st.run = r.run;
    st.data = await api(`${BASE}/${st.id}`);
    render();
    root.querySelector("#wsResults")?.scrollIntoView({ behavior: "smooth" });
  } catch (err) { toast(err.message, "error"); btn.disabled = false; btn.textContent = "RUN COMPARISON"; }
}

function renderResults() {
  const host = root.querySelector("#wsResults");
  if (!st.run) { host.innerHTML = ""; return; }
  const run = st.run, d = run.detail, s = d.summary;
  const apps = d.apps;
  const tiles = [["Total comparisons", s.total_comparisons], ["Passed", s.passed, "ok"], ["Failed", s.failed, "fail"], ["Warnings", s.warnings, "warn"],
    ["Needs review", s.needs_review ?? 0, "warn"], ["Not comparable", s.not_comparable ?? 0], ["Incomplete", s.incomplete ?? 0, "warn"],
    ["Records checked", s.records_checked], ["Missing records", s.missing_records], ["Field mismatches", s.field_mismatches],
    ["Calculation mismatches", s.calculation_mismatches], ["Status mismatches", s.status_mismatches], ["Unverified visual values", s.unverified_visual]];
  const filters = [["all", "All"], ["FAIL", "Failed"], ["WARNING", "Warnings"], ["NEEDS_REVIEW", "Needs review"], ["NOT_COMPARABLE", "Not comparable"], ["INCOMPLETE", "Incomplete"], ...Object.entries(d.pairs).map(([k, p]) => [k, `${letterOf(p.apps[0])} vs ${letterOf(p.apps[1])}`])];
  const rows = d.rows.filter((r) => st.filter === "all" ? true : OPEN.includes(st.filter) ? r.result === st.filter : (() => { const [x, y] = st.filter.split("|"); return r.pairs.includes(x) && r.pairs.includes(y); })());
  host.innerHTML = `<h3>Step 5 · Results ${pill(run.result)}</h3>
    ${d.coverage_note ? `<p class="ws-warn">${esc(d.coverage_note)}</p>` : ""}
    <div class="ws-tiles">${tiles.map(([t, v, k]) => `<div class="card ws-tile ${k || ""}"><b>${esc(v)}</b><span>${esc(t)}</span></div>`).join("")}</div>
    <div class="ws-cons">${Object.values(d.consistency).map((c) => `<span class="pill ${c.consistent ? "PASS" : c.failed ? "FAIL" : "WARN"}">${esc(c.name)} ${c.consistent ? "is consistent with" : c.failed ? `differs from` : "needs checks against"} ${esc(apps[0].name)}${c.failed ? ` (${c.failed})` : ""}</span>`).join(" ")}</div>
    <div class="row ws-filters">${filters.map(([k, t]) => `<button class="chip ${st.filter === k ? "on" : ""}" data-f="${esc(k)}">${esc(t)}${d.pairs[k] ? ` · ${d.pairs[k].failed} failed` : ""}</button>`).join("")}
      <span class="spacer"></span><a class="btn" href="${BASE}/runs/${esc(run.id)}/report.pdf" target="_blank" rel="noopener">PDF report</a>
      <a class="btn" href="${BASE}/runs/${esc(run.id)}/report.xlsx">Excel report</a>
      <a class="link-btn" href="${BASE}/runs/${esc(run.id)}/report.html" target="_blank" rel="noopener">Open report</a></div>
    <div class="table-wrap"><table class="data ws-res"><thead><tr><th>Field</th><th>Record</th>${apps.map((a) => `<th>${esc(a.letter)} · ${esc(a.name)}</th>`).join("")}<th>Expected</th><th>Result</th></tr></thead>
    <tbody>${rows.map((r) => `<tr class="clickable" data-r="${esc(r.id)}"><td>${esc(r.field)}</td><td>${esc(r.record_key ?? "")}</td>
      ${apps.map((a) => { const v = r.values[a.app_id]; return v ? `<td class="${v.confidence !== null && v.confidence !== undefined && v.confidence < 0.8 ? "ws-low" : ""}">${esc(v.value ?? "")}${v.source === "screenshot" ? ' <span class="ws-src" title="Read from a screenshot">📷</span>' : ""}</td>` : '<td class="muted">—</td>'; }).join("")}
      <td>${esc(r.expected ?? "")}</td><td>${pill(r.result)}${r.decision ? `<div class="muted">${esc(r.decision.label)}</div>` : ""}</td></tr>`).join("") || `<tr><td colspan="${apps.length + 4}" class="muted">Nothing here.</td></tr>`}</tbody></table></div>
    <details class="card ws-log" id="wsLog"><summary>Defects and tester decisions</summary><div id="wsLogBody" class="muted">Loading…</div></details>`;
  host.querySelector("#wsLog").addEventListener("toggle", (e) => { if (e.target.open) renderLog(); });
  host.querySelectorAll("[data-f]").forEach((b) => b.addEventListener("click", () => { st.filter = b.dataset.f; renderResults(); }));
  host.querySelectorAll("[data-r]").forEach((tr) => tr.addEventListener("click", () => detail(d.rows.find((r) => r.id === tr.dataset.r))));
}

function evidenceHtml(r) {
  const boxes = {};
  for (const v of Object.values(r.values)) if (v?.evidence && v.box) (boxes[v.evidence] = boxes[v.evidence] || []).push(v.box);
  return (r.evidence || []).map((id) => `<figure class="ws-ev"><div class="ws-ev-wrap"><img src="/api/comparison/evidence/${esc(id)}" alt="" data-boxes='${esc(JSON.stringify(boxes[id] || []))}'></div><figcaption>${esc(id)}</figcaption></figure>`).join("");
}

function placeBoxes(scope) {
  scope.querySelectorAll("img[data-boxes]").forEach((img) => {
    const draw = () => {
      const boxes = JSON.parse(img.dataset.boxes || "[]");
      const k = img.clientWidth / (img.naturalWidth || 1);
      img.parentElement.querySelectorAll(".ws-box").forEach((b) => b.remove());
      for (const [x, y, w, h] of boxes) {
        const b = document.createElement("div");
        b.className = "ws-box";
        Object.assign(b.style, { left: `${x * k - 3}px`, top: `${y * k - 3}px`, width: `${w * k + 6}px`, height: `${h * k + 6}px` });
        img.parentElement.appendChild(b);
      }
    };
    if (img.complete) draw(); else img.addEventListener("load", draw);
  });
}

function detail(r) {
  const run = st.run, apps = run.detail.apps;
  const edit = canEdit();
  const { el, close } = openModal(`${r.field}${r.record_key ? ` · ${r.record_key}` : ""}`, `
    <p>${pill(r.result)} ${r.original_result ? `<span class="muted">(was ${esc(r.original_result)}; accepted by ${esc(r.decision.by)}: ${esc(r.decision.label)})</span>` : ""}</p>
    <p>${esc(r.explanation)}</p>
    <table class="data compact"><tbody>
      ${apps.map((a) => { const v = r.values[a.app_id]; return `<tr><th>${esc(a.letter)} · ${esc(a.name)}</th><td>${v ? esc(v.value ?? "(not read)") : '<span class="muted">not in this comparison</span>'}</td>
        <td class="muted">${v ? `${esc(SOURCE_TEXT[v.source] || v.source || "")}${v.field ? ` · ${esc(v.field)}` : ""}${v.confidence !== null && v.confidence !== undefined && v.source === "screenshot" ? ` · read with ${pct(v.confidence)} confidence` : ""}` : ""}</td></tr>`; }).join("")}
      ${r.expected !== null && r.expected !== undefined ? `<tr><th>Expected</th><td>${esc(r.expected)}</td><td></td></tr>` : ""}
      ${r.actual !== null && r.actual !== undefined ? `<tr><th>Actual</th><td>${esc(r.actual)}</td><td></td></tr>` : ""}
      ${r.difference !== null && r.difference !== undefined ? `<tr><th>Difference</th><td>${esc(r.difference)}</td><td></td></tr>` : ""}
      ${r.source_app ? `<tr><th>Source / target application</th><td colspan="2">${esc(r.source_app)}${r.target_app ? ` → ${esc(r.target_app)}` : ""}</td></tr>` : ""}
      ${r.suggested_label ? `<tr><th>Suggested classification</th><td colspan="2">${esc(r.suggested_label)} <span class="muted">(your decision below is what counts)</span></td></tr>` : ""}
      ${r.normalized ? `<tr><th>Normalization</th><td colspan="2">Equal after normalization (currency, separators, case or date format)</td></tr>` : ""}
      ${r.mapping ? `<tr><th>Field mapping used</th><td colspan="2">${esc(r.mapping)}</td></tr>` : ""}
      ${r.calculation ? `<tr><th>Calculation used</th><td colspan="2">${esc(r.calculation)}</td></tr>` : ""}
    </tbody></table>
    ${r.checks?.length > 1 ? `<h4>Each pair</h4><ul>${r.checks.map((c) => `<li>${esc(letterOf(c.pair[0]))} vs ${esc(letterOf(c.pair[1]))}: ${pill(c.result)} ${esc(c.why)}</li>`).join("")}</ul>` : ""}
    ${r.evidence?.length ? `<h4>Evidence</h4><div class="ws-evs">${evidenceHtml(r)}</div>` : ""}
    <div id="wsAI"></div>
    ${r.decision && !r.original_result ? `<p class="muted">Decision: ${esc(r.decision.label)} by ${esc(r.decision.by)}, ${esc(dateTimeOf(r.decision.at))}</p>` : ""}
    ${edit ? `<h4>Your decision</h4><div class="row ws-dec">${DECISIONS.map(([k, t]) => `<button class="btn" data-dec="${k}">${esc(t)}</button>`).join("")}</div>
    <label class="grid">Note <input id="wsNote" maxlength="1000"></label>
    <div class="row">${OPEN.includes(r.result) ? '<button class="btn primary" id="wsDefect">CREATE DEFECT / REVIEW</button>' : ""}
      <button class="btn" id="wsExplain">Explain with AI</button></div>` : ""}`, { wide: true });
  placeBoxes(el);
  el.querySelectorAll("[data-dec]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`${BASE}/runs/${run.id}/rows/${r.id}/decision`, { method: "POST", body: { decision: b.dataset.dec, note: el.querySelector("#wsNote").value || null } }); toast("Decision saved. It applies from the next run.", "success"); close(); }
    catch (err) { toast(err.message, "error"); }
  }));
  el.querySelector("#wsDefect")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      const { defect } = await api(`${BASE}/runs/${run.id}/rows/${r.id}/defect`, { method: "POST" });
      close(); showDefect(defect);
      st.run = (await api(`${BASE}/runs/${run.id}`)).run; renderResults();
    } catch (err) { toast(err.message, "error"); e.target.disabled = false; }
  });
  el.querySelector("#wsExplain")?.addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Asking…";
    try {
      const x = await api(`${BASE}/runs/${run.id}/rows/${r.id}/explain`, { method: "POST" });
      const ai = x.ai || {};
      el.querySelector("#wsAI").innerHTML = `<div class="card ws-info"><b>Explanation</b> <span class="muted">(${esc(ai.origin || "rules")})</span><p>${esc(ai.summary || x.rules)}</p>${ai.likely_cause ? `<p><b>Likely cause:</b> ${esc(ai.likely_cause)}</p>` : ""}${(ai.checks || []).length ? `<ul>${ai.checks.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}</div>`;
    } catch (err) { toast(err.message, "error"); }
    e.target.textContent = "Explain with AI"; e.target.disabled = false;
  });
}

function showDefect(dft) {
  const d = dft.data || {};
  const { el } = openModal(`Defect ${dft.id}`, `<table class="data compact"><tbody>
    <tr><th>Title</th><td>${esc(dft.title)}</td></tr>
    <tr><th>Applications</th><td>${esc((d.applications || []).join(", "))}</td></tr>
    <tr><th>Field</th><td>${esc(d.field)}${d.record_key ? ` · record ${esc(d.record_key)}` : ""}</td></tr>
    <tr><th>Expected</th><td>${esc(d.expected ?? "")}</td></tr><tr><th>Actual</th><td>${esc(d.actual ?? "")}</td></tr>
    <tr><th>Difference</th><td>${esc(d.difference ?? "")}</td></tr>
    <tr><th>Steps to reproduce</th><td><ol>${(d.steps || []).map((s) => `<li>${esc(s)}</li>`).join("")}</ol></td></tr>
    <tr><th>Explanation</th><td>${esc(d.explanation || "")}</td></tr>
    <tr><th>Screenshots</th><td>${esc((d.screenshots || []).join(", ") || "none")}</td></tr></tbody></table>
    <div class="row"><button class="btn primary" id="wsTfs">Send to TFS</button><span class="muted">It stays listed under "Defects and tester decisions" below the results.</span></div>`, { wide: true });
  el.querySelector("#wsTfs").addEventListener("click", async (e) => {
    e.target.disabled = true;
    const send = (force) => api(`/api/comparison/defects/${dft.id}/tfs`, { method: "POST", body: { force } });
    try { const r = await send(false); toast(r.defect.tfs_id ? `Created TFS #${r.defect.tfs_id}` : "TFS is not reachable: queued", "success"); }
    catch (err) {
      if (err.code === "CMP_POSSIBLE_DUPLICATE" && confirm(`${err.message}\n\nCreate it anyway?`)) { try { await send(true); } catch (e2) { toast(e2.message, "error"); } }
      else toast(err.message, "error");
      e.target.disabled = false;
    }
  });
}

async function renderLog() {
  const host = root.querySelector("#wsLogBody");
  try {
    const [{ items: defects }, { items: decisions }] = await Promise.all([
      api(`/api/comparison/projects/${st.id}/defects`), api(`${BASE}/${st.id}/decisions`)]);
    const label = Object.fromEntries(DECISIONS);
    host.innerHTML = `<h4>Defects</h4>${defects.length ? `<table class="data compact"><thead><tr><th>Id</th><th>Title</th><th>Status</th><th>TFS</th><th></th></tr></thead><tbody>${defects.map((x) => `<tr><td>${esc(x.id)}</td><td>${esc(x.title)}</td><td>${esc(x.status.replaceAll("_", " "))}</td><td>${esc(x.tfs_id || "")}</td><td><button class="link-btn" data-open="${esc(x.id)}">Open</button></td></tr>`).join("")}</tbody></table>` : '<p class="muted">None yet.</p>'}
      <h4>Tester decisions</h4>${decisions.length ? `<table class="data compact"><thead><tr><th>When</th><th>Tester</th><th>Result</th><th>Decision</th><th>Note</th></tr></thead><tbody>${decisions.map((x) => `<tr><td>${esc(dateTimeOf(x.decided_at))}</td><td>${esc(x.decided_by)}</td><td>${esc(x.row?.field || "")} ${esc(x.row?.record_key || "")}</td><td>${esc(label[x.decision] || x.decision)}</td><td>${esc(x.note || "")}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">None yet.</p>'}
      <p class="muted">Decisions never change an approved mapping or calculation. A difference marked as valid, expected or a false positive is shown as EXPECTED on the next runs while the values stay the same.</p>`;
    host.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => showDefect(defects.find((x) => x.id === b.dataset.open))));
  } catch (err) { host.textContent = err.message; }
}
