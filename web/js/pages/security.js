// 23.14.0: Security Testing (AI Security & Vulnerability Testing). Eight screens share this module:
// AI Security Assessment, Application Discovery, Vulnerability Scanning, Security Test Scenarios, Security Findings,
// Security Regression, Security Reports and Security Preferences. Every rule that decides what may be sent is enforced
// on the server (scope, authorization record, Production, state-changing permission, rate and request budget).

import { api } from "../api.js";
import { esc, toast, dateTimeOf, openModal } from "../util.js";
import { canEdit, isAdmin } from "../auth.js";
import { actionButtons } from "../result-actions.js";
import { enc, pill, short, guarded, stored, targetPicker, envBadge, authorizationHtml, authorizationFields, runsTable,
  wireRuns, runDetail, wireResultActions, lines } from "./itest-ui.js";

const B = "/api/security";
let view = null;
let stopDetail = null;
let meta = null;
const st = { apps: [], app: null };

async function load(screen) {
  meta = meta || await api(`${B}/meta`);
  st.apps = (await api(`${B}/apps`)).apps;
  const want = new URLSearchParams(location.hash.split("?")[1] || "").get("app") || stored("sg.sec.app");
  st.app = st.apps.find((a) => a.id === want) || st.apps[0] || null;
  render(screen);
}

function header(screen, title, intro) {
  return `<div class="card"><div class="row" style="gap:8px;flex-wrap:wrap"><h3 style="margin:0">${esc(title)}</h3>
    ${screen !== "preferences" ? targetPicker(st.apps, st.app?.id, { label: "Application", id: "secApp", empty: "No application yet" }) : ""}
    ${st.app && screen !== "preferences" ? envBadge(st.app) : ""}<span class="spacer"></span>
    ${st.app && screen !== "preferences" ? `<span class="muted small">${esc(st.app.config.base_url)} · ${st.app.open_findings} open finding(s)</span>` : ""}</div>
    <p class="muted" style="margin:6px 0 0">${intro}</p>
    ${st.app && screen !== "preferences" ? authorizationHtml(st.app.config) : ""}</div><div id="secBody" style="margin-top:12px"></div>`;
}

function render(screen) {
  if (stopDetail) { stopDetail(); stopDetail = null; }
  const S = SCREENS[screen];
  view.innerHTML = header(screen, S.title, S.intro);
  view.querySelector("#secApp")?.addEventListener("change", (e) => { st.app = st.apps.find((a) => a.id === e.target.value) || null; stored("sg.sec.app", st.app?.id || ""); render(screen); });
  const body = view.querySelector("#secBody");
  if (!st.app && !["assessment", "preferences"].includes(screen)) { body.innerHTML = '<div class="card"><p class="muted">Add an application in AI Security Assessment first.</p></div>'; return; }
  S.draw(body).catch((err) => { body.innerHTML = `<div class="card"><p class="pill FAIL">${esc(err.message)}</p></div>`; });
}

// ---- AI Security Assessment: applications, scope, accounts, intensity, limits, authorization ---------------------------
function configForm(a) {
  const c = a?.config || {};
  const cats = meta.categories;
  return `<form class="card grid" id="secForm" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px">
    <h3 style="grid-column:1/-1;margin:0">${a ? `Application ${esc(a.name)}` : "New application"}</h3>
    <label>Name <input name="name" required minlength="2" maxlength="120" value="${esc(a?.name || "")}"></label>
    <label>Environment <select name="environment">${meta.environments.map((e) => `<option ${a?.environment === e || (!a && e === "Test") ? "selected" : ""}>${e}</option>`).join("")}</select></label>
    <label style="grid-column:span 2">Application URL <input name="base_url" required value="${esc(c.base_url || "")}" placeholder="https://orders.intranet.company.local/"></label>
    <label>Authorized hosts / networks (one per line) <textarea name="targets" rows="3" placeholder="orders.intranet.company.local&#10;10.20.30.0/24">${esc((c.targets || []).join("\n"))}</textarea></label>
    <label>Allowed URLs (prefixes or re:pattern) <textarea name="allowed" rows="3">${esc((c.allowed || []).join("\n"))}</textarea></label>
    <label>Excluded URLs <textarea name="excluded" rows="3" placeholder="https://orders…/admin/delete">${esc((c.excluded || []).join("\n"))}</textarea></label>
    <label>Intensity <select name="intensity">${Object.keys(meta.intensity).map((k) => `<option ${c.intensity === k ? "selected" : ""}>${k}</option>`).join("")}</select></label>
    <label>Requests per second <input name="rate" type="number" step="0.5" min="0.2" max="50" value="${esc(c.rate ?? "")}"></label>
    <label>Concurrency <input name="concurrency" type="number" min="1" max="4" value="${esc(c.concurrency ?? 1)}"></label>
    <label>Timeout (s) <input name="timeout" type="number" min="2" max="120" value="${esc(c.timeout ?? 15)}"></label>
    <label>Maximum duration (min) <input name="duration_minutes" type="number" min="1" max="240" value="${esc(c.duration_minutes ?? 20)}"></label>
    <label>Request budget <input name="max_requests" type="number" min="20" max="20000" value="${esc(c.max_requests ?? "")}"></label>
    <label>Maximum pages <input name="max_pages" type="number" min="1" max="500" value="${esc(c.max_pages ?? 60)}"></label>
    <fieldset style="grid-column:1/-1"><legend>Test categories</legend><div class="row" style="flex-wrap:wrap;gap:10px">
      ${Object.entries(cats).map(([k, l]) => `<label class="row" style="gap:4px"><input type="checkbox" name="cat" value="${k}" ${(c.categories || []).includes(k) ? "checked" : ""}> ${esc(l)}${meta.state_changing.includes(k) ? ' <span class="muted small">(sends POST)</span>' : ""}</label>`).join("")}</div></fieldset>
    <label class="row" style="gap:6px;grid-column:1/-1"><input type="checkbox" name="allow_state_changing" ${c.allow_state_changing ? "checked" : ""}> This is an isolated test environment: allow state-changing tests (never allowed on Production)</label>
    <fieldset style="grid-column:1/-1"><legend>Test accounts (one per role; passwords and tokens are entered separately and kept in the Windows Credential Manager)</legend>
      <textarea name="accounts" rows="4" placeholder="role | auth (none, basic, bearer, header, cookie, form) | user name | login URL&#10;user | form | qa.user | https://orders…/login&#10;admin | form | qa.admin | https://orders…/login">${esc((c.accounts || []).map((x) => [x.role, x.auth, x.username || "", x.login_url || ""].join(" | ")).join("\n"))}</textarea></fieldset>
    <label style="grid-column:1/-1">Restricted pages (URL | roles allowed), for authorization tests <textarea name="restricted" rows="2" placeholder="https://orders…/admin | admin">${esc((c.restricted || []).map((r) => `${r.url} | ${r.allowed_roles.join(", ")}`).join("\n"))}</textarea></label>
    <fieldset style="grid-column:1/-1"><legend>OWASP ZAP (installed on this PC, offline)</legend><div class="row" style="gap:10px;flex-wrap:wrap">
      <label class="row" style="gap:4px"><input type="checkbox" name="zap_use" ${c.zap?.use ? "checked" : ""}> Use ZAP</label>
      <label>ZAP API address <input name="zap_url" value="${esc(c.zap?.url || "http://127.0.0.1:8090")}"></label>
      <label class="row" style="gap:4px"><input type="checkbox" name="zap_active" ${c.zap?.active ? "checked" : ""}> Active scanning approved</label>
      <label>Scan policy <input name="zap_policy" value="${esc(c.zap?.policy || "")}" maxlength="80"></label></div></fieldset>
    <label class="row" style="gap:6px"><input type="checkbox" name="verify_tls" ${c.tls?.verify === false ? "" : "checked"}> Verify TLS certificates</label>
    <label>Company CA certificate file <input name="ca_file" value="${esc(c.tls?.ca_file || "")}" placeholder="C:\\Certs\\company-ca.pem"></label>
    ${authorizationFields()}
    ${a?.environment === "Production" && isAdmin() ? `<label style="grid-column:1/-1">Admin approval for active testing on Production <input name="production_approval" maxlength="300" placeholder="Change ticket and window"></label>` : ""}
    <div class="row" style="grid-column:1/-1;gap:8px"><span class="spacer"></span>${a ? '<button class="btn" type="button" id="secDel">Delete</button>' : ""}<button class="btn primary">Save</button></div></form>`;
}

function readConfig(f) {
  const fd = new FormData(f);
  const before = Object.fromEntries((st.app?.config.accounts || []).map((x) => [x.role, x]));
  const accounts = f.accounts.value.split(/\r?\n/).map((l) => l.split("|").map((x) => x.trim())).filter((p) => p[0])
    .map(([role, auth, username, login_url]) => ({ ...(before[role] || {}), role, auth: auth || "form", username, login_url }));
  const restricted = f.restricted.value.split(/\r?\n/).map((l) => l.split("|").map((x) => x.trim())).filter((p) => p[0])
    .map(([url, roles]) => ({ url, allowed_roles: (roles || "").split(",").map((x) => x.trim()).filter(Boolean) }));
  const num = (k) => (fd.get(k) === "" || fd.get(k) == null ? undefined : Number(fd.get(k)));
  return { name: fd.get("name"), environment: fd.get("environment"), config: {
    base_url: fd.get("base_url"), targets: lines(fd.get("targets")), allowed: f.allowed.value.split(/\r?\n/).map((x) => x.trim()).filter(Boolean),
    excluded: f.excluded.value.split(/\r?\n/).map((x) => x.trim()).filter(Boolean), intensity: fd.get("intensity"),
    rate: num("rate"), concurrency: num("concurrency"), timeout: num("timeout"), duration_minutes: num("duration_minutes"), max_requests: num("max_requests"),
    max_pages: num("max_pages"), categories: fd.getAll("cat"), allow_state_changing: !!fd.get("allow_state_changing"), accounts, restricted,
    zap: { use: !!fd.get("zap_use"), url: fd.get("zap_url"), active: !!fd.get("zap_active"), policy: fd.get("zap_policy") },
    tls: { verify: !!fd.get("verify_tls"), ca_file: fd.get("ca_file") },
    authorization_confirmed: !!fd.get("authorization_confirmed"), authorization_statement: fd.get("authorization_statement"),
    authorization_reference: fd.get("authorization_reference"), production_approval: fd.get("production_approval") || undefined,
    objects: st.app?.config.objects, fuzz_operations: st.app?.config.fuzz_operations, lockout_account: st.app?.config.lockout_account } };
}

async function drawAssessment(body) {
  const a = st.app;
  body.innerHTML = `${canEdit() ? '<div class="row" style="margin-bottom:8px"><button class="btn" id="secNew">New application</button></div>' : ""}
    <div id="secCfg">${canEdit() ? configForm(a) : `<div class="card"><pre class="code">${esc(JSON.stringify(a?.config || {}, null, 2))}</pre></div>`}</div>
    ${a ? `<div class="card" style="margin-top:12px"><h3 style="margin:0 0 6px">Credentials</h3>
      <p class="muted small">Kept in the Windows Credential Manager; never shown, sent to the AI, written into reports, emails or logs.</p>
      <table class="data compact"><tbody>${Object.entries(a.secrets).map(([n, ok]) => `<tr><td>${esc(n)}</td><td>${ok ? '<span class="pill PASS">saved</span>' : '<span class="pill WAITING">not saved</span>'}</td>
        <td>${canEdit() ? `<input type="password" data-sec="${esc(n)}" autocomplete="new-password" placeholder="new value"><button class="btn small" data-save-sec="${esc(n)}">Save</button>${ok ? `<button class="btn small" data-clear-sec="${esc(n)}">Remove</button>` : ""}` : ""}</td></tr>`).join("")}</tbody></table></div>
      <div class="card" style="margin-top:12px"><h3 style="margin:0 0 6px">Run the assessment</h3>
      <p class="muted small">Configure → Discover → approve the application map → generate scenarios → review → approve → execute → verify → analyze → report.</p>
      <div class="row" style="gap:8px;flex-wrap:wrap">${["discovery", "passive", "vulnerability", "zap", "scenarios"].map((k) => `<button class="btn" data-start="${k}" ${canEdit() ? "" : "disabled"}>${esc(k === "zap" ? "OWASP ZAP scan" : k[0].toUpperCase() + k.slice(1))}</button>`).join("")}</div></div>` : ""}`;
  body.querySelector("#secNew")?.addEventListener("click", () => { st.app = null; body.querySelector("#secCfg").innerHTML = configForm(null); wireForm(body); });
  wireForm(body);
  body.querySelectorAll("[data-save-sec]").forEach((b) => b.addEventListener("click", () => guarded(b, async () => {
    const v = body.querySelector(`[data-sec="${b.dataset.saveSec}"]`).value; if (!v) return;
    await api(`${B}/apps/${enc(a.id)}/credentials`, { method: "POST", body: { name: b.dataset.saveSec, value: v } }); toast("Saved in the Credential Manager.", "success"); await load("assessment"); })));
  body.querySelectorAll("[data-clear-sec]").forEach((b) => b.addEventListener("click", () => guarded(b, async () => {
    await api(`${B}/apps/${enc(a.id)}/credentials`, { method: "POST", body: { name: b.dataset.clearSec, value: null } }); await load("assessment"); })));
  body.querySelectorAll("[data-start]").forEach((b) => b.addEventListener("click", () => startRun(b, b.dataset.start)));
}

function wireForm(body) {
  const f = body.querySelector("#secForm");
  if (!f) return;
  f.addEventListener("submit", (ev) => { ev.preventDefault(); guarded(f.querySelector("button.primary"), async () => {
    const r = st.app ? await api(`${B}/apps/${enc(st.app.id)}`, { method: "PUT", body: readConfig(f) }) : await api(`${B}/apps`, { method: "POST", body: readConfig(f) });
    stored("sg.sec.app", r.app.id); toast("Saved.", "success"); await load("assessment"); }); });
  f.querySelector("#secDel")?.addEventListener("click", (e) => guarded(e.target, async () => {
    if (!window.confirm(`Delete ${st.app.name}, its credentials and its configuration? Findings and results stay in history.`)) return;
    await api(`${B}/apps/${enc(st.app.id)}`, { method: "DELETE" }); stored("sg.sec.app", ""); await load("assessment"); }));
}

async function startRun(btn, kind, options = {}) {
  await guarded(btn, async () => {
    const r = await api(`${B}/apps/${enc(st.app.id)}/runs`, { method: "POST", body: { kind, options } });
    toast(`${kind} started (${r.run.id}).`, "success");
    location.hash = `#/sec-scanning?app=${enc(st.app.id)}&run=${enc(r.run.id)}`;
  });
}

// ---- Application Discovery: maps, access matrix, approval -------------------------------------------------------------
async function drawDiscovery(body) {
  const maps = (await api(`${B}/apps/${enc(st.app.id)}/maps`)).maps;
  body.innerHTML = `<div class="card"><div class="row" style="gap:8px"><button class="btn primary" id="disc" ${canEdit() ? "" : "disabled"}>Run read-only discovery</button>
    <span class="muted small">Only GET requests; links that look state-changing (logout, delete, approve, pay…) are listed, not opened.</span></div>
    <table class="data compact" style="margin-top:8px"><thead><tr><th>Map</th><th>Run</th><th>Created</th><th>Approved</th><th></th></tr></thead><tbody>
    ${maps.map((m) => `<tr><td>#${m.id}</td><td>${esc(m.run_id)}</td><td>${esc(dateTimeOf(m.created_at))}</td><td>${m.approved ? `${pill("PASSED")} ${esc(m.approved_by || "")}` : '<span class="muted">no</span>'}</td>
      <td><button class="btn small" data-map="${m.id}">Open</button>${!m.approved && canEdit() ? `<button class="btn small" data-approve="${m.id}">Approve for active testing</button>` : ""}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">No map yet.</td></tr>'}</tbody></table></div><div id="mapBox" style="margin-top:12px"></div>`;
  body.querySelector("#disc").addEventListener("click", (e) => startRun(e.target, "discovery"));
  body.querySelectorAll("[data-approve]").forEach((b) => b.addEventListener("click", () => guarded(b, async () => { await api(`${B}/apps/${enc(st.app.id)}/maps/${b.dataset.approve}/approve`, { method: "POST" }); toast("Map approved: active tests use it.", "success"); drawDiscovery(body); })));
  body.querySelectorAll("[data-map]").forEach((b) => b.addEventListener("click", () => guarded(b, async () => {
    const m = (await api(`${B}/apps/${enc(st.app.id)}/maps/${b.dataset.map}`)).map.map;
    const roles = m.roles || [];
    body.querySelector("#mapBox").innerHTML = `<div class="card"><h3 style="margin:0">Application map #${b.dataset.map}</h3>
      <p class="small">${m.pages.length} pages · ${m.endpoints.length} endpoints · ${m.api_docs.length} API descriptions · ${m.login_forms.length} sign-in forms · ${m.uploads.length} upload forms · ${m.requests} requests</p>
      <table class="data compact"><thead><tr><th>Page</th>${roles.map((r) => `<th>${esc(r)}</th>`).join("")}<th>Forms</th></tr></thead><tbody>
      ${m.pages.map((p) => `<tr><td>${esc(p.url)}<div class="muted small">${esc(p.title || "")}</div></td>${roles.map((r) => `<td>${esc(p.access[r] ?? "-")}</td>`).join("")}
        <td class="small">${(p.forms || []).map((f) => `${esc(f.method)} ${esc(short(f.action, 60))} (${(f.tags || []).join(", ")})`).join("<br>")}</td></tr>`).join("")}</tbody></table>
      ${m.not_visited.length ? `<h4>Not opened (state-changing)</h4><ul>${m.not_visited.map((n) => `<li>${esc(n.url)} <span class="muted small">${esc(n.reason)}</span></li>`).join("")}</ul>` : ""}
      ${m.endpoints.length ? `<h4>Endpoints</h4><ul>${m.endpoints.slice(0, 100).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</div>`;
  })));
}

// ---- Vulnerability Scanning: runs, live progress, results with actions ---------------------------------------------
async function drawScanning(body) {
  const runs = (await api(`/api/itest/runs?module=security&target_id=${enc(st.app.id)}`)).runs;
  const want = new URLSearchParams(location.hash.split("?")[1] || "").get("run");
  body.innerHTML = `<div class="card"><div class="row" style="gap:8px;flex-wrap:wrap">
    ${[["passive", "Passive checks"], ["vulnerability", "Vulnerability scan (approved categories)"], ["zap", "OWASP ZAP scan"]].map(([k, l]) => `<button class="btn ${k === "vulnerability" ? "primary" : ""}" data-start="${k}" ${canEdit() ? "" : "disabled"}>${l}</button>`).join("")}
    <span class="muted small">Categories: ${esc((st.app.config.categories || []).join(", ") || "none approved")} · ${esc(st.app.config.rate)} req/s · budget ${esc(st.app.config.max_requests)}</span></div>
    <div id="runsBox" style="margin-top:8px">${runsTable(runs, "security")}</div></div><div id="runBox" style="margin-top:12px"></div>`;
  body.querySelectorAll("[data-start]").forEach((b) => b.addEventListener("click", () => startRun(b, b.dataset.start)));
  const open = async (id) => { if (stopDetail) stopDetail(); stopDetail = await runDetail(body.querySelector("#runBox"), id, { onDone: () => drawScanning(body) }); };
  wireRuns(body.querySelector("#runsBox"), open, () => drawScanning(body));
  wireResultActions(body, () => drawScanning(body));
  if (want || runs[0]) await open(want || runs[0].id);
}

// ---- Security Test Scenarios: business logic, AI drafts, review and approval ----------------------------------------
async function drawScenarios(body) {
  const cases = (await api(`/api/itest/cases?module=security&target_id=${enc(st.app.id)}`)).cases.filter((c) => c.definition.kind === "scenario");
  body.innerHTML = `<div class="card"><div class="row" style="gap:8px;flex-wrap:wrap">
    ${canEdit() ? '<button class="btn" id="scDraft">Draft scenarios with AI (from the approved map)</button><button class="btn" id="scNew">New scenario</button><button class="btn primary" id="scRun">Run approved scenarios</button>' : ""}
    <span class="muted small">Types: duplicate, replay, sequence, status change, ownership, concurrent. They change data: they run only when approved, on a non-production target that allows state-changing tests.</span></div>
    <table class="data compact" style="margin-top:8px"><thead><tr><th>Scenario</th><th>Type</th><th>Steps</th><th>Status</th><th>Origin</th><th></th></tr></thead><tbody>
    ${cases.map((c) => { const s = c.definition.scenario; return `<tr><td>${esc(c.name)}<div class="muted small">${esc(s.description || "")}</div></td><td>${esc(s.type)}</td>
      <td class="small">${s.steps.map((x) => `${esc(x.method)} ${esc(short(x.url, 60))} as ${esc(x.role || "anonymous")}`).join("<br>")}</td><td>${c.status === "approved" ? pill("PASSED").replace("PASSED", "approved") : '<span class="pill WAITING">draft</span>'}</td>
      <td>${esc(c.origin || "")}</td><td>${canEdit() ? `<button class="btn small" data-edit="${esc(c.id)}">Edit</button>${c.status !== "approved" ? `<button class="btn small" data-ok="${esc(c.id)}">Approve</button>` : ""}<button class="btn small" data-del="${esc(c.id)}">Delete</button>` : ""}</td></tr>`; }).join("") || '<tr><td colspan="6" class="muted">No scenario yet.</td></tr>'}</tbody></table></div>`;
  const edit = (c) => {
    const def = c?.definition.scenario || { type: "duplicate", name: "", description: "", steps: [{ method: "POST", url: st.app.config.base_url, role: st.app.config.primary_role || "", json: {} }] };
    const { el, close } = openModal(c ? `Edit ${c.name}` : "New scenario", `<p class="muted small">JSON: type, name, description, steps [{method, url, role, json}], skip_step (sequence), id_path (duplicate), parallel (concurrent). Saving an edited scenario makes it a draft again.</p>
      <textarea id="scJson" rows="18" style="width:100%;font-family:monospace">${esc(JSON.stringify(def, null, 2))}</textarea>
      <div class="row"><span class="spacer"></span><button class="btn primary" id="scSave">Save</button></div>`, { wide: true });
    el.querySelector("#scSave").addEventListener("click", (e) => guarded(e.target, async () => {
      let v; try { v = JSON.parse(el.querySelector("#scJson").value); } catch { throw new Error("The scenario is not valid JSON."); }
      if (c) await api(`${B}/apps/${enc(st.app.id)}/scenarios/${enc(c.id)}`, { method: "PUT", body: v }); else await api(`${B}/apps/${enc(st.app.id)}/scenarios`, { method: "POST", body: v });
      close(); drawScenarios(body); }));
  };
  body.querySelector("#scNew")?.addEventListener("click", () => edit(null));
  body.querySelector("#scDraft")?.addEventListener("click", (e) => guarded(e.target, async () => { e.target.textContent = "Drafting… (local AI)"; const r = await api(`${B}/apps/${enc(st.app.id)}/scenarios/draft`, { method: "POST" }); toast(`${r.cases.length} draft(s) added; review and approve them.`, "success"); drawScenarios(body); }));
  body.querySelector("#scRun")?.addEventListener("click", (e) => startRun(e.target, "scenarios"));
  body.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => edit(cases.find((c) => c.id === b.dataset.edit))));
  body.querySelectorAll("[data-ok]").forEach((b) => b.addEventListener("click", () => guarded(b, async () => { await api(`/api/itest/cases/${enc(b.dataset.ok)}/approve`, { method: "POST" }); drawScenarios(body); })));
  body.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => guarded(b, async () => { if (!window.confirm("Delete this scenario?")) return; await api(`/api/itest/cases/${enc(b.dataset.del)}`, { method: "DELETE" }); drawScenarios(body); })));
}

// ---- Security Findings ---------------------------------------------------------------------------------------------
async function drawFindings(body, { regression = false } = {}) {
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  const status = body.querySelector("#fStatus")?.value ?? (q.get("status") || "open,confirmed");
  const sev = body.querySelector("#fSev")?.value ?? (q.get("severity") || "");
  const r = await api(`${B}/findings?app_id=${enc(st.app.id)}&status=${enc(status)}&severity=${enc(sev)}`);
  const c = r.counts;
  body.innerHTML = `<div class="card"><div class="row" style="gap:8px;flex-wrap:wrap">
    <select id="fStatus"><option value="open,confirmed">Open and confirmed</option><option value="open">Pending review</option><option value="fixed">Fixed</option><option value="false_positive">False positives</option><option value="accepted_risk">Accepted risk</option><option value="">All</option></select>
    <select id="fSev"><option value="">All severities</option>${["critical", "high", "medium", "low", "info"].map((s) => `<option>${s}</option>`).join("")}</select>
    <span class="spacer"></span><span class="small">${Object.entries(c.by_severity).map(([k, v]) => `${pill(k)} ${v}`).join(" ")}</span>
    ${regression && canEdit() ? '<button class="btn primary" id="regRun">Run security regression</button>' : ""}</div>
    <table class="data compact" style="margin-top:8px"><thead><tr><th>Finding</th><th>Severity</th><th>OWASP</th><th>Status</th><th>Seen</th><th>Actions</th></tr></thead><tbody>
    ${r.findings.map((f) => `<tr><td>${esc(f.title)}<div class="muted small">${esc(short(f.url, 90))}${f.param ? ` · ${esc(f.param)}` : ""} · ${esc(f.id)} · ${esc(f.source)}</div>
      ${f.review_note ? `<div class="small">${esc(f.review_note)}</div>` : ""}${f.draft_id ? `<div class="small">Defect draft #${esc(f.draft_id)}</div>` : ""}</td>
      <td>${pill(f.severity)}<div class="muted small">${esc(f.confidence || "")} confidence</div></td><td class="small">${esc(f.owasp || "")}</td>
      <td>${pill(f.status)}${f.retest_status ? `<div class="small">retest: ${esc(f.retest_status)}</div>` : ""}
        ${canEdit() ? `<select data-review="${esc(f.id)}" aria-label="Review"><option value="">Review…</option>${meta.finding_statuses.map((s) => `<option value="${s}">${s.replace("_", " ")}</option>`).join("")}</select>` : ""}</td>
      <td class="small">${f.occurrences}× · ${esc(dateTimeOf(f.updated_at))}</td><td>${actionButtons("finding", f.id, f.status === "fixed" ? "fixed" : "FAILED")}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">No findings.</td></tr>'}</tbody></table></div><div id="regBox" style="margin-top:12px"></div>`;
  body.querySelector("#fStatus").value = status; body.querySelector("#fSev").value = sev;
  body.querySelector("#fStatus").addEventListener("change", () => drawFindings(body, { regression }));
  body.querySelector("#fSev").addEventListener("change", () => drawFindings(body, { regression }));
  body.querySelectorAll("[data-review]").forEach((s) => s.addEventListener("change", () => guarded(s, async () => {
    if (!s.value) return;
    const note = window.prompt(`Note for marking ${s.dataset.review} as ${s.value.replace("_", " ")} (optional):`, "") ?? null;
    if (note === null) { s.value = ""; return; }
    await api(`${B}/findings/${enc(s.dataset.review)}/review`, { method: "POST", body: { status: s.value, note } }); drawFindings(body, { regression }); })));
  body.querySelector("#regRun")?.addEventListener("click", (e) => startRun(e.target, "regression"));
  wireResultActions(body, () => drawFindings(body, { regression }));
  if (regression) {
    const runs = (await api(`/api/itest/runs?module=security&target_id=${enc(st.app.id)}`)).runs.filter((x) => ["regression", "retest"].includes(x.kind));
    body.querySelector("#regBox").innerHTML = `<div class="card"><h3 style="margin:0 0 6px">Regression and retest runs</h3>${runsTable(runs, "security")}
      <p class="muted small">A regression run repeats discovery, passive checks and the approved categories: findings not seen again in a covered category are marked fixed; a fixed finding that comes back is reopened.</p></div>`;
  }
}

// ---- Security Reports -----------------------------------------------------------------------------------------------
async function drawReports(body) {
  const runs = (await api(`/api/itest/runs?module=security&target_id=${enc(st.app.id)}`)).runs.filter((r) => !["RUNNING", "QUEUED", "PAUSED"].includes(r.status));
  body.innerHTML = `<div class="card"><p class="muted small">Offline PDF reports (made on this PC, no Internet): the suite summary of a run, or the Security Vulnerability Report / AI Bug Analysis Report / Defect Evidence Report of one finding from Security Findings.</p>
    <table class="data compact"><thead><tr><th>Run</th><th>Type</th><th>Status</th><th>Finished</th><th>Reports</th></tr></thead><tbody>
    ${runs.map((r) => `<tr><td>${esc(r.id)}<div class="muted small">${esc(r.label || "")}</div></td><td>${esc(r.kind)}</td><td>${pill(r.status)}</td><td>${esc(dateTimeOf(r.finished_at))}</td>
      <td><button class="btn small" data-pdf="${esc(r.id)}" data-type="suite">Suite summary PDF</button><button class="btn small" data-pdf="${esc(r.id)}" data-type="security">Security report PDF</button></td></tr>`).join("") || '<tr><td colspan="5" class="muted">No finished run yet.</td></tr>'}</tbody></table></div>`;
  body.querySelectorAll("[data-pdf]").forEach((b) => b.addEventListener("click", () => guarded(b, async () => {
    const r = (await api(`/api/test-actions/run/${enc(b.dataset.pdf)}/pdf?type=${b.dataset.type}`, { method: "POST" })).pdf;
    window.open(r.url, "_blank", "noopener"); })));
}

// ---- Security Preferences -------------------------------------------------------------------------------------------
async function drawPreferences(body) {
  const rules = meta.rules;
  body.innerHTML = `<div class="card"><h3 style="margin:0 0 6px">How Security Testing protects the applications it tests</h3>
    <ul class="small"><li>Only authorized hosts and approved URLs are contacted; SkyGuard's own port and cloud metadata addresses never.</li>
    <li>Every run needs the recorded authorization; Production gets discovery and passive checks only, unless an admin records a production approval. State-changing tests are never run on Production.</li>
    <li>Rate, concurrency, timeouts, maximum duration and a request budget are applied to every run; runs can be paused, resumed and cancelled (partial results are kept).</li>
    <li>Passwords and tokens live in the Windows Credential Manager. Evidence is sanitized before it is stored, shown, sent to the local AI, put in a PDF or an email.</li>
    <li>The AI drafts scenarios and analyses results; it never decides what may be sent.</li>
    <li>OWASP ZAP is used through its local API (127.0.0.1 only) with the same scope; its add-ons must be installed offline.</li></ul>
    <p class="small">Per-application limits, categories, accounts and the ZAP address are set in <a href="#/sec-assessment">AI Security Assessment</a>.</p></div>
    <div class="card" style="margin-top:12px"><h3 style="margin:0 0 6px">Checks and their OWASP mapping (${Object.keys(rules).length})</h3>
    <table class="data compact"><thead><tr><th>Rule</th><th>Check</th><th>Category</th><th>OWASP / WSTG</th><th>Default severity</th></tr></thead><tbody>
    ${Object.entries(rules).sort().map(([k, r]) => `<tr><td>${esc(k)}</td><td>${esc(r.title)}</td><td>${esc(r.category)}</td><td class="small">${esc(r.owasp)}</td><td>${pill(r.severity)}</td></tr>`).join("")}</tbody></table></div>`;
}

const SCREENS = {
  assessment: { title: "AI Security Assessment", intro: "Applications under authorized security testing: scope, test accounts and roles, intensity, rate, concurrency, timeouts, duration and categories.", draw: drawAssessment },
  discovery: { title: "Application Discovery", intro: "Read-only discovery per role: pages, menus, forms, APIs, cookies and the role access matrix. Approve the map before active testing uses it.", draw: drawDiscovery },
  scanning: { title: "Vulnerability Scanning", intro: "OWASP WSTG, Top 10 and API Top 10 checks, controlled fuzzing and the offline OWASP ZAP scan, inside the approved scope and limits.", draw: drawScanning },
  scenarios: { title: "Security Test Scenarios", intro: "Business-logic scenarios: duplicate transactions, replay, skipped approval steps, unauthorized status changes, record ownership and concurrent updates.", draw: drawScenarios },
  findings: { title: "Security Findings", intro: "Deduplicated findings with evidence, OWASP mapping, review status and the result actions.", draw: (b) => drawFindings(b) },
  regression: { title: "Security Regression", intro: "Repeat the approved checks after a fix: fixed findings close, returning ones reopen.", draw: (b) => drawFindings(b, { regression: true }) },
  reports: { title: "Security Reports", intro: "Offline PDF reports of runs and findings.", draw: drawReports },
  preferences: { title: "Security Preferences", intro: "The safety rules and the checks with their OWASP mapping.", draw: drawPreferences },
};

function page(screen) {
  return {
    async mount(v) { view = v; await load(screen); },
    unmount() { if (stopDetail) stopDetail(); stopDetail = null; },
  };
}

export const secAssessment = page("assessment");
export const secDiscovery = page("discovery");
export const secScanning = page("scanning");
export const secScenarios = page("scenarios");
export const secFindings = page("findings");
export const secRegression = page("regression");
export const secReports = page("reports");
export const secPreferences = page("preferences");
