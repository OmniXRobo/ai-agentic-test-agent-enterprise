// 23.12.0: Automation Studio > API Testing.
// Applications (authorized targets only), authentication with secrets in the Windows Credential Manager, Test Connection
// with real diagnostics, OpenAPI / Swagger (file or intranet URL), cURL, Postman and Python imports (never executed),
// generated tests (expected results from the specification; anything undocumented waits for a person's approval),
// runs with a response viewer, suites and chains, the pytest + httpx script with its tabs, and Defect Review drafts
// for failures. Secrets are never shown: only whether one is stored.

import { api } from "../api.js";
import { esc, toast, dateTimeOf, openModal } from "../util.js";
import { canEdit } from "../auth.js";
import { confirmDelete } from "../confirm-delete.js";
import { highlightPython } from "./script-studio.js";
import { gutterHtml, sizeButtons, wireSize } from "../python-editor.js";

const BASE = "/api/apitest";
const ENVS = ["Development", "Test", "Staging", "Production"];
const AUTHS = [["none", "None"], ["basic", "Basic (user name + password)"], ["bearer", "Bearer token"], ["api_key", "API key"],
  ["oauth2", "OAuth 2.0 (token URL)"], ["client_cert", "Client certificate"], ["session", "Session sign-in (cookie)"], ["custom", "Custom headers"]];
const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];
const SCRIPT_TABS = ["View Script", "Edit Script", "Flow Logic", "Test Data", "Credentials", "Components", "History", "Result", "Evidence", "Logs"];
const STATUS_PILL = { PASSED: "PASS", FAILED: "FAIL", BLOCKED: "WARN", SKIPPED: "WAITING", RUNNING: "RUNNING", ERROR: "FAIL" };
const pill = (s) => s ? `<span class="pill ${esc(STATUS_PILL[s] || "WAITING")}">${esc(s)}</span>` : '<span class="muted">-</span>';
const lines = (src) => (src || "").split("\n").length;
const json = (v) => JSON.stringify(v ?? null, null, 2);

let view = null;
let timers = [];
const st = { apps: [], app: null, tab: "config", tests: [], suites: [], runs: [], selected: new Set(), script: null, scriptTab: "View Script",
  run: null, scriptErrors: [] };

function hashParams() { return new URLSearchParams((location.hash.split("?")[1]) || ""); }
function stopTimers() { timers.forEach(clearInterval); timers = []; }
async function guarded(btn, fn) {
  if (btn) btn.disabled = true;
  try { await fn(); } catch (err) { toast(err.message, "error", 8000); } finally { if (btn) btn.disabled = false; }
}

// ---- JSON tree (response viewer) ---------------------------------------------------------------------------------
function tree(v, key = null, depth = 0) {
  const k = key != null ? `<span class="jt-key">${esc(key)}</span>: ` : "";
  if (v === null || typeof v !== "object") {
    const cls = typeof v === "string" ? "jt-str" : typeof v === "number" ? "jt-num" : "jt-lit";
    return `<div class="jt-leaf">${k}<span class="${cls}">${esc(typeof v === "string" ? JSON.stringify(v) : String(v))}</span></div>`;
  }
  const entries = Array.isArray(v) ? v.map((x, i) => [i, x]) : Object.entries(v);
  const label = Array.isArray(v) ? `[${entries.length}]` : `{${entries.length}}`;
  return `<details class="jt" ${depth < 2 ? "open" : ""}><summary>${k}<span class="muted">${label}</span></summary>
    ${entries.slice(0, 500).map(([kk, x]) => tree(x, kk, depth + 1)).join("")}${entries.length > 500 ? `<div class="muted">… ${entries.length - 500} more</div>` : ""}</details>`;
}
function bodyView(body) {
  if (body == null || body === "") return '<p class="muted">No body.</p>';
  if (typeof body === "object") return `<div class="jtree">${tree(body)}</div>`;
  try { return `<div class="jtree">${tree(JSON.parse(body))}</div>`; } catch { return `<pre class="code">${esc(String(body))}</pre>`; }
}
function headersTable(h) {
  const rows = Array.isArray(h) ? h : Object.entries(h || {});
  return rows.length ? `<table class="data compact"><tbody>${rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">None.</p>';
}

// ---- page --------------------------------------------------------------------------------------------------------
async function loadApps() { st.apps = (await api(`${BASE}/apps`)).apps; }

async function selectApp(id) {
  st.app = id ? (await api(`${BASE}/apps/${encodeURIComponent(id)}`)).app : null;
  st.selected.clear();
  if (st.app) {
    const t = await api(`${BASE}/apps/${encodeURIComponent(st.app.id)}/tests`);
    st.tests = t.tests; st.suites = t.suites;
    st.runs = (await api(`${BASE}/runs?app_id=${encodeURIComponent(st.app.id)}&limit=30`)).runs;
  }
}

function render() {
  const a = st.app;
  view.innerHTML = `
    <div class="card"><div class="row" style="flex-wrap:wrap;gap:8px"><h3 style="margin:0">API Testing</h3>
      <select id="apSel" aria-label="Application"><option value="">${st.apps.length ? "Choose an application…" : "No applications yet"}</option>
        ${st.apps.map((x) => `<option value="${esc(x.id)}" ${a && a.id === x.id ? "selected" : ""}>${esc(x.name)} · ${esc(x.environment)}</option>`).join("")}</select>
      ${canEdit() ? '<button class="btn primary" id="apNew">New application</button>' : ""}<span class="spacer"></span>
      ${a ? `<span class="muted">${esc(a.base_url)}</span>${a.environment === "Production" ? ' <span class="pill FAIL">PRODUCTION</span>' : ""}` : ""}</div>
      <p class="muted" style="margin:6px 0 0">Only the hosts and addresses you authorize are contacted. Secrets are kept in the Windows Credential Manager and are never shown,
        written to scripts, reports or logs. Tests that change data (POST, PUT, PATCH, DELETE) run only after you confirm them; on Production they are off by default.</p></div>
    ${a ? `<div class="row tabs" style="margin-top:12px;gap:6px;flex-wrap:wrap">${[["config", "Configuration"], ["import", "Import & generate"], ["tests", `Tests (${st.tests.length})`],
      ["suites", `Suites & chains (${st.suites.length})`], ["runs", "Runs & results"], ["script", "Python script"], ["audit", "Audit"]]
      .map(([k, l]) => `<button class="chip ${st.tab === k ? "on" : ""}" data-tab="${k}">${esc(l)}</button>`).join("")}</div>
      <div id="apBody" style="margin-top:12px"></div>` : `<div class="card" style="margin-top:12px" id="apBody"></div>`}`;
  view.querySelector("#apSel").addEventListener("change", async (e) => { await selectApp(e.target.value); st.tab = st.app && !st.tests.length ? "import" : st.tab; render(); });
  view.querySelector("#apNew")?.addEventListener("click", () => { st.app = null; renderConfig(view.querySelector("#apBody"), true); });
  view.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { st.tab = b.dataset.tab; render(); }));
  const body = view.querySelector("#apBody");
  if (!a) { body.innerHTML = '<p class="muted">Choose an application, or create one with New application.</p>'; return; }
  ({ config: () => renderConfig(body, false), import: () => renderImport(body), tests: () => renderTests(body), suites: () => renderSuites(body),
    runs: () => renderRuns(body), script: () => renderScript(body), audit: () => renderAudit(body) })[st.tab]();
}

// ---- configuration ------------------------------------------------------------------------------------------------
function renderConfig(host, creating) {
  const a = creating ? { name: "", environment: "Test", description: "", targets: [], config: { protocol: "https", tls: { verify: true }, auth: { type: "none" }, state_changes: "confirm", timeout_ms: 30000, retries: 0, max_parallel: 2, rate_per_second: 10 } } : st.app;
  const c = a.config || {}, au = c.auth || { type: "none" }, cred = a.credentials || { app: {}, roles: {} };
  const stored = (f) => cred.app?.[f] ? '<span class="pill PASS">stored</span>' : '<span class="pill WAITING">not stored</span>';
  const ro = !canEdit();
  host.innerHTML = `<form class="card grid" id="apForm" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr))">
    <h3 style="grid-column:1/-1;margin:0">${creating ? "New API application" : `Configuration · ${esc(a.name)}`}</h3>
    <label>Name <input name="name" required maxlength="120" value="${esc(a.name)}"></label>
    <label>Environment <select name="environment">${ENVS.map((e) => `<option ${a.environment === e ? "selected" : ""}>${e}</option>`).join("")}</select></label>
    <label style="grid-column:span 2">Description <input name="description" value="${esc(a.description || "")}"></label>
    <label style="grid-column:1/-1">Complete base URL (or leave empty and fill protocol, host and port) <input name="base_url" value="${esc(c.base_url || "")}" placeholder="https://orders.intranet.local:8443/api/v1"></label>
    <label>Protocol <select name="protocol"><option ${c.protocol !== "http" ? "selected" : ""}>https</option><option ${c.protocol === "http" ? "selected" : ""}>http</option></select></label>
    <label>Hostname / IPv4 / IPv6 <input name="host" value="${esc(c.host || "")}" placeholder="10.20.30.40 or fd00::10 or api.local"></label>
    <label>Port <input name="port" type="number" min="1" max="65535" value="${esc(c.port || "")}"></label>
    <label>Base path <input name="base_path" value="${esc(c.base_path || "")}" placeholder="/api/v1"></label>
    <label style="grid-column:1/-1">Authorized targets (one per line: host, host:port, *.domain.local or a CIDR such as 10.20.0.0/24). The base URL's host is added automatically.
      <textarea name="targets" rows="3">${esc((a.targets || []).join("\n"))}</textarea></label>
    <label style="grid-column:1/-1">Default headers (one per line, Name: value; Authorization and Cookie are set by Authentication)
      <textarea name="headers" rows="2">${esc((c.headers || []).map((h) => `${h.name}: ${h.value}`).join("\n"))}</textarea></label>
    <label>Timeout (ms) <input name="timeout_ms" type="number" min="500" max="600000" value="${esc(c.timeout_ms || 30000)}"></label>
    <label>Retries (GET/HEAD/OPTIONS only) <input name="retries" type="number" min="0" max="3" value="${esc(c.retries || 0)}"></label>
    <label>Requests per second (limit) <input name="rate_per_second" type="number" step="0.1" min="0.2" max="50" value="${esc(c.rate_per_second || 10)}"></label>
    <label>Tests at the same time (max) <input name="max_parallel" type="number" min="1" max="4" value="${esc(c.max_parallel || 2)}"></label>
    <label>Response-time target (ms, 0 = none) <input name="response_time_ms" type="number" min="0" value="${esc(c.response_time_ms || 0)}"></label>
    <label>Largest response (bytes) <input name="max_response_bytes" type="number" min="10000" value="${esc(c.max_response_bytes || 5242880)}"></label>
    <label>Tests that change data <select name="state_changes">${[["confirm", "Run after I confirm them"], ["allowed", "Allowed (also from schedules, never on Production)"], ["never", "Never run"]]
      .map(([v, l]) => `<option value="${v}" ${c.state_changes === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    <label>Proxy (empty = direct) <input name="proxy" value="${esc(c.proxy || "")}" placeholder="http://proxy.local:8080"></label>
    <label class="row" style="gap:6px"><input type="checkbox" name="use_system_proxy" style="width:auto;margin:0" ${c.use_system_proxy ? "checked" : ""}>Use the Windows proxy settings</label>
    <label class="row" style="gap:6px"><input type="checkbox" name="follow_redirects" style="width:auto;margin:0" ${c.follow_redirects !== false ? "checked" : ""}>Follow redirects (each hop is checked)</label>
    <label class="row" style="gap:6px"><input type="checkbox" name="tls_verify" style="width:auto;margin:0" ${(c.tls || {}).verify !== false ? "checked" : ""}>Verify TLS certificates (recommended; always on for Production)</label>
    <fieldset style="grid-column:1/-1;border:1px solid var(--border);border-radius:8px;padding:10px 12px"><legend>Authentication</legend>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
      <label>Type <select name="auth_type">${AUTHS.map(([v, l]) => `<option value="${v}" ${au.type === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label data-auth="basic session oauth2">User name <input name="auth_username" autocomplete="off" value="${esc(au.username || "")}"></label>
      <label data-auth="api_key">Key name <input name="auth_name" value="${esc(au.name || "X-API-Key")}"></label>
      <label data-auth="api_key">Sent in <select name="auth_in"><option value="header" ${au.in !== "query" ? "selected" : ""}>Header</option><option value="query" ${au.in === "query" ? "selected" : ""}>Query parameter</option></select></label>
      <label data-auth="oauth2">Token URL <input name="auth_token_url" value="${esc(au.token_url || "")}"></label>
      <label data-auth="oauth2">Client ID <input name="auth_client_id" value="${esc(au.client_id || "")}"></label>
      <label data-auth="oauth2">Scope <input name="auth_scope" value="${esc(au.scope || "")}"></label>
      <label data-auth="oauth2">Grant <select name="auth_grant"><option value="client_credentials" ${au.grant !== "password" ? "selected" : ""}>Client credentials</option><option value="password" ${au.grant === "password" ? "selected" : ""}>Password</option></select></label>
      <label data-auth="session">Sign-in path <input name="auth_login_path" value="${esc(au.login_path || "login")}"></label>
      <label data-auth="custom" style="grid-column:1/-1">Custom headers (one per line, Name: value; write Name: [secret] for a value kept in the Credential Manager)
        <textarea name="auth_headers" rows="2">${esc((au.headers || []).map((h) => `${h.name}: ${h.secret ? "[secret]" : h.value || ""}`).join("\n"))}</textarea></label>
      </div>
      ${creating ? '<p class="muted">Save the application first; then enter its password, token, key or client secret here. They go straight to the Windows Credential Manager.</p>' : `
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr));margin-top:8px">
        ${[["password", "Password"], ["token", "Bearer token"], ["api_key", "API key"], ["client_secret", "OAuth client secret"]].map(([f, l]) => `
        <label data-secret="${f}">${l} ${stored(f)}<span class="row" style="gap:6px"><input type="password" autocomplete="new-password" data-sec="${f}" placeholder="enter to replace">
          <button class="btn small" type="button" data-savesec="${f}" ${ro ? "disabled" : ""}>Save</button>${cred.app?.[f] ? `<button class="btn small" type="button" data-clrsec="${f}" ${ro ? "disabled" : ""}>Remove</button>` : ""}</span></label>`).join("")}
        ${Object.keys(cred.app || {}).filter((k) => k.startsWith("custom:")).map((k) => `<label>${esc(k.slice(7))} ${stored(k)}<span class="row" style="gap:6px"><input type="password" autocomplete="new-password" data-sec="${esc(k)}"><button class="btn small" type="button" data-savesec="${esc(k)}" ${ro ? "disabled" : ""}>Save</button></span></label>`).join("")}
      </div>
      <div class="row" style="gap:8px;flex-wrap:wrap;margin-top:8px">
        <span class="muted">Certificates (PEM, kept in the SkyGuard data folder):</span>
        <label class="btn small">Trusted CA ${c.tls?.ca_file ? "✓" : ""}<input type="file" accept=".pem,.crt,.cer" data-cert="ca_file" hidden></label>
        <label class="btn small">Client certificate ${au.cert_file ? "✓" : ""}<input type="file" accept=".pem,.crt,.cer" data-cert="cert_file" hidden></label>
        <label class="btn small">Client key ${au.key_file ? "✓" : ""}<input type="file" accept=".pem,.key" data-cert="key_file" hidden></label></div>`}
      ${cred.store_available === false ? '<p class="pill FAIL" style="white-space:normal">The Windows Credential Manager is not available, so secrets cannot be saved on this PC.</p>' : ""}
    </fieldset>
    <fieldset style="grid-column:1/-1;border:1px solid var(--border);border-radius:8px;padding:10px 12px"><legend>Roles for authorization tests (optional)</legend>
      <p class="muted" style="margin:0 0 6px">One per line: role name | auth type | user name | operations it may call (comma-separated operation ids, empty = not restricted). Secrets of a role are saved with its Credential Manager entry below.</p>
      <textarea name="roles" rows="3" placeholder="viewer | basic | viewer.user | listOrders,getOrder">${esc((c.roles || []).map((r) => [r.name, r.auth?.type || "none", r.auth?.username || "", (r.allowed_operations || []).join(",")].join(" | ")).join("\n"))}</textarea>
      ${creating ? "" : (c.roles || []).map((r) => `<div class="row" style="gap:6px;margin-top:6px"><span>${esc(r.name)}</span>
        <select data-rolefield="${esc(r.name)}"><option value="password">password</option><option value="token">token</option><option value="api_key">api key</option><option value="client_secret">client secret</option></select>
        <input type="password" autocomplete="new-password" data-rolesec="${esc(r.name)}" placeholder="secret for this role"><button class="btn small" type="button" data-saverole="${esc(r.name)}" ${ro ? "disabled" : ""}>Save</button>
        <span class="muted">${Object.entries(cred.roles?.[r.name] || {}).filter(([, v]) => v).map(([k]) => `${esc(k)} stored`).join(", ")}</span></div>`).join("")}
    </fieldset>
    ${creating ? `<label style="grid-column:1/-1;display:flex;flex-direction:row;gap:8px;align-items:center"><input type="checkbox" name="authorized" style="width:auto;margin:0" required>
      I am authorized to test this API and its hosts (SkyGuard sends requests only to the authorized targets above; it never scans networks).</label>` : ""}
    <div class="row" style="grid-column:1/-1;gap:8px;flex-wrap:wrap">
      ${creating ? "" : '<input id="apConnPath" placeholder="Path to try (optional, e.g. /api/orders)" style="max-width:260px"><button class="btn" type="button" id="apConn">Test Connection</button>'}
      <span class="spacer"></span>${creating || ro ? "" : '<button class="btn" type="button" id="apDel">Delete application</button>'}
      ${ro ? "" : `<button class="btn primary" type="submit">${creating ? "Create application" : "Save"}</button>`}</div>
    <div id="apConnOut" style="grid-column:1/-1"></div>
  </form>`;
  const f = host.querySelector("#apForm");
  const syncAuth = () => f.querySelectorAll("[data-auth]").forEach((x) => { x.hidden = !x.dataset.auth.split(" ").includes(f.auth_type.value); });
  f.auth_type.addEventListener("change", syncAuth); syncAuth();
  f.addEventListener("submit", (e) => { e.preventDefault(); guarded(f.querySelector("[type=submit]"), async () => {
    const body = readConfig(f, a);
    if (creating) {
      const { app } = await api(`${BASE}/apps`, { method: "POST", body });
      await loadApps(); await selectApp(app.id); st.tab = "config";
      toast("Application created. Enter its secrets, then Test Connection.", "success");
    } else {
      await api(`${BASE}/apps/${encodeURIComponent(a.id)}`, { method: "PUT", body });
      await loadApps(); await selectApp(a.id); toast("Saved.", "success");
    }
    render();
  }); });
  host.querySelector("#apConn")?.addEventListener("click", (e) => guarded(e.target, async () => {
    const p = host.querySelector("#apConnPath").value.trim();
    const r = await api(`${BASE}/apps/${encodeURIComponent(a.id)}/test-connection${p ? `?path=${encodeURIComponent(p)}` : ""}`, { method: "POST" });
    host.querySelector("#apConnOut").innerHTML = `<div class="card" style="padding:10px"><strong>${r.ok ? "Reachable" : "Not reachable"}</strong> <span class="muted">${esc(r.url)}</span>
      <table class="data compact" style="margin-top:6px"><tbody>${r.steps.map((s) => `<tr><td>${esc(s.step)}</td><td><span class="pill ${s.status === "PASS" ? "PASS" : s.status === "WARN" ? "WARN" : "FAIL"}">${esc(s.status)}</span></td><td>${esc(s.detail)}</td></tr>`).join("")}</tbody></table></div>`;
  }));
  host.querySelector("#apDel")?.addEventListener("click", async () => {
    if (!(await confirmDelete({ title: "Delete API application?", name: a.name, body: "Its tests, suites, scripts and run history are deleted. Its stored secrets are removed from the Credential Manager." }))) return;
    guarded(null, async () => { await api(`${BASE}/apps/${encodeURIComponent(a.id)}`, { method: "DELETE" }); await loadApps(); st.app = null; render(); });
  });
  const saveSecret = (field, value, role) => guarded(null, async () => {
    await api(`${BASE}/apps/${encodeURIComponent(a.id)}/credentials`, { method: "POST", body: { field, value, role } });
    await selectApp(a.id); render(); toast(value ? "Stored in the Credential Manager." : "Removed.", "success");
  });
  host.querySelectorAll("[data-savesec]").forEach((b) => b.addEventListener("click", () => {
    const inp = host.querySelector(`[data-sec="${CSS.escape(b.dataset.savesec)}"]`);
    if (!inp.value) { toast("Enter the value first.", "error"); return; }
    saveSecret(b.dataset.savesec, inp.value); inp.value = "";
  }));
  host.querySelectorAll("[data-clrsec]").forEach((b) => b.addEventListener("click", () => saveSecret(b.dataset.clrsec, null)));
  host.querySelectorAll("[data-saverole]").forEach((b) => b.addEventListener("click", () => {
    const name = b.dataset.saverole;
    const inp = host.querySelector(`[data-rolesec="${CSS.escape(name)}"]`), fld = host.querySelector(`[data-rolefield="${CSS.escape(name)}"]`);
    if (!inp.value) { toast("Enter the value first.", "error"); return; }
    saveSecret(fld.value, inp.value, name); inp.value = "";
  }));
  host.querySelectorAll("[data-cert]").forEach((inp) => inp.addEventListener("change", () => guarded(null, async () => {
    const file = inp.files[0]; if (!file) return;
    const q = new URLSearchParams({ kind: inp.dataset.cert, name: file.name });
    const r = await fetch(`${BASE}/apps/${encodeURIComponent(a.id)}/certificates?${q}`,
      { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.message || `HTTP ${r.status}`);
    await selectApp(a.id); render(); toast("Certificate saved.", "success");
  })));
}

function readConfig(f, a) {
  const fd = new FormData(f);
  const kv = (text) => String(text || "").split("\n").map((l) => l.trim()).filter(Boolean).map((l) => { const i = l.indexOf(":"); return i < 0 ? null : [l.slice(0, i).trim(), l.slice(i + 1).trim()]; }).filter(Boolean);
  const type = fd.get("auth_type");
  const auth = { ...((a.config || {}).auth || {}), type };
  Object.assign(auth, { username: fd.get("auth_username") || undefined, name: fd.get("auth_name") || undefined, in: fd.get("auth_in"),
    token_url: fd.get("auth_token_url") || undefined, client_id: fd.get("auth_client_id") || undefined, scope: fd.get("auth_scope") || undefined,
    grant: fd.get("auth_grant"), login_path: fd.get("auth_login_path") || undefined });
  auth.headers = kv(fd.get("auth_headers")).map(([name, value]) => (value === "[secret]" ? { name, secret: true } : { name, value }));
  const roles = String(fd.get("roles") || "").split("\n").map((l) => l.split("|").map((x) => x.trim())).filter((p) => p[0])
    .map(([name, t, user, ops]) => ({ name, auth: { ...(((a.config || {}).roles || []).find((r) => r.name === name)?.auth || {}), type: t || "none", username: user || undefined },
      allowed_operations: ops ? ops.split(",").map((x) => x.trim()).filter(Boolean) : null }));
  const config = { ...(a.config || {}), base_url: fd.get("base_url").trim(), protocol: fd.get("protocol"), host: fd.get("host").trim(), port: fd.get("port") || null,
    base_path: fd.get("base_path").trim(), headers: kv(fd.get("headers")).map(([name, value]) => ({ name, value })),
    timeout_ms: Number(fd.get("timeout_ms")), retries: Number(fd.get("retries")), rate_per_second: Number(fd.get("rate_per_second")),
    max_parallel: Number(fd.get("max_parallel")), response_time_ms: Number(fd.get("response_time_ms")), max_response_bytes: Number(fd.get("max_response_bytes")),
    state_changes: fd.get("state_changes"), proxy: fd.get("proxy").trim(), use_system_proxy: fd.get("use_system_proxy") === "on",
    follow_redirects: fd.get("follow_redirects") === "on", tls: { ...((a.config || {}).tls || {}), verify: fd.get("tls_verify") === "on" }, auth, roles };
  return { name: fd.get("name").trim(), environment: fd.get("environment"), description: fd.get("description"), config,
    targets: String(fd.get("targets") || "").split("\n").map((x) => x.trim()).filter(Boolean), authorized: fd.get("authorized") === "on" || !!a.id };
}

// ---- import & generate --------------------------------------------------------------------------------------------
function renderImport(host) {
  const a = st.app, sp = a.spec_summary;
  host.innerHTML = `<div class="card"><h3 style="margin-top:0">OpenAPI / Swagger</h3>
      ${sp ? `<p>${esc(sp.kind)} ${esc(sp.version)} · <strong>${esc(sp.title || "")}</strong> ${esc(sp.api_version || "")} · ${a.endpoint_count} operation(s) · imported ${esc(dateTimeOf(sp.imported_at))}</p>
        ${(sp.unresolved || []).length ? `<p class="pill WARN" style="white-space:normal">References that could not be read offline (upload those files too): ${esc(sp.unresolved.join(", "))}</p>` : ""}
        ${(sp.warnings || []).length ? `<details><summary>${sp.warnings.length} warning(s)</summary><ul>${sp.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>` : ""}` : '<p class="muted">No specification imported yet.</p>'}
      <p class="muted">Swagger 2.0 and OpenAPI 3.x, JSON or YAML, read completely offline. Upload the main file and every file it references; references to other web sites are reported, never downloaded.</p>
      <div class="row" style="gap:8px;flex-wrap:wrap"><input type="file" id="spFiles" multiple accept=".json,.yaml,.yml"><select id="spMain" hidden></select>
        <label class="row" style="gap:6px"><input type="checkbox" id="spGen" checked style="width:auto;margin:0">Generate tests</label>
        <button class="btn primary" id="spUp" ${canEdit() ? "" : "disabled"}>Import files</button></div>
      <div class="row" style="gap:8px;flex-wrap:wrap;margin-top:8px"><input id="spUrl" placeholder="Intranet URL of the specification (must be an authorized target)" style="min-width:360px">
        <button class="btn" id="spUrlBtn" ${canEdit() ? "" : "disabled"}>Import from URL</button></div></div>
    <div class="card" style="margin-top:12px"><h3 style="margin-top:0">Generate tests</h3>
      <p class="muted">Expected results come from the documented responses. Where the specification documents no result, the test is marked <strong>Needs review</strong> and does not run until a person approves its expected result.</p>
      <div class="chips">${[["positive", "Positive (status, schema, content type, response time)"], ["negative", "Negative (required, type, invalid, missing parameter, error responses)"],
        ["auth", "Authentication and authorization by role"], ["boundary", "Boundary values"], ["pagination", "Pagination"], ["filtering", "Filtering"], ["sorting", "Sorting"]].map(([k, l]) =>
        `<label class="chip" style="display:inline-flex;gap:6px;align-items:center"><input type="checkbox" data-opt="${k}" checked style="width:auto;margin:0">${l}</label>`).join("")}</div>
      <div class="row" style="margin-top:8px"><button class="btn primary" id="genBtn" ${a.endpoint_count && canEdit() ? "" : "disabled"}>Generate from the specification</button>
        ${a.endpoint_count ? "" : '<span class="muted">Import a specification first, or add tests by hand or from cURL / Postman / Python.</span>'}</div>
      <div class="row" style="gap:8px;margin-top:10px;flex-wrap:wrap"><textarea id="aiText" rows="2" placeholder="Describe a behaviour to test, e.g. 'an order with quantity 0 is refused'" style="flex:1;min-width:300px"></textarea>
        <button class="btn" id="aiBtn" ${canEdit() ? "" : "disabled"}>Suggest with local AI</button></div>
      <p class="muted">AI suggestions are always saved as Needs review.</p></div>
    <div class="card" style="margin-top:12px"><h3 style="margin-top:0">Import other definitions (never executed)</h3>
      <div class="row" style="gap:8px;flex-wrap:wrap"><select id="impKind"><option value="curl">cURL command(s)</option><option value="postman">Postman collection (JSON)</option>
        <option value="python">Python requests / httpx script (literal URLs are read, nothing runs)</option><option value="skyguard">SkyGuard API tests export (JSON)</option></select>
        <input type="file" id="impFile" accept=".json,.txt,.py,.sh"></div>
      <textarea id="impText" rows="5" style="width:100%;margin-top:6px" placeholder="curl -X GET 'https://orders.local/api/orders' -H 'Accept: application/json'"></textarea>
      <p class="muted">Passwords, tokens, cookies and API keys found in the text are dropped; enter them under Configuration > Authentication.</p>
      <div class="row"><span class="spacer"></span><a class="btn" href="${BASE}/apps/${encodeURIComponent(a.id)}/export">Export tests (JSON)</a><button class="btn primary" id="impBtn" ${canEdit() ? "" : "disabled"}>Import</button></div>
      <div id="impOut"></div></div>`;
  const files = host.querySelector("#spFiles"), main = host.querySelector("#spMain");
  files.addEventListener("change", () => {
    main.hidden = files.files.length < 2;
    main.innerHTML = [...files.files].map((f) => `<option>${esc(f.name)}</option>`).join("");
  });
  const opts = () => Object.fromEntries([...host.querySelectorAll("[data-opt]")].map((c) => [c.dataset.opt, c.checked]));
  const after = async (r) => {
    await selectApp(a.id);
    const msg = r.created != null ? `${r.created} test(s) created${r.needs_review ? `, ${r.needs_review} need review` : ""}.` : "Imported.";
    toast(msg, "success", 6000); st.tab = "tests"; render();
  };
  host.querySelector("#spUp").addEventListener("click", (e) => guarded(e.target, async () => {
    if (!files.files.length) throw new Error("Choose the specification file(s).");
    const payload = {};
    for (const f of files.files) { if (f.size > 8 * 1024 * 1024) throw new Error(`${f.name} is larger than 8 MB.`); payload[f.name] = await f.text(); }
    const r = await api(`${BASE}/apps/${encodeURIComponent(a.id)}/spec`, { method: "POST", body: { files: payload, main: main.value || files.files[0].name, generate: host.querySelector("#spGen").checked, options: opts() } });
    await after({ created: r.tests_created });
  }));
  host.querySelector("#spUrlBtn").addEventListener("click", (e) => guarded(e.target, async () => {
    const url = host.querySelector("#spUrl").value.trim(); if (!url) throw new Error("Enter the URL.");
    const r = await api(`${BASE}/apps/${encodeURIComponent(a.id)}/spec`, { method: "POST", body: { url, generate: host.querySelector("#spGen").checked, options: opts() } });
    await after({ created: r.tests_created });
  }));
  host.querySelector("#genBtn").addEventListener("click", (e) => guarded(e.target, async () => {
    const r = await api(`${BASE}/apps/${encodeURIComponent(a.id)}/generate`, { method: "POST", body: { options: opts() } });
    if (r.note) toast(r.note, "info", 8000);
    await after(r);
  }));
  host.querySelector("#aiBtn").addEventListener("click", (e) => guarded(e.target, async () => {
    const r = await api(`${BASE}/apps/${encodeURIComponent(a.id)}/ai-suggest`, { method: "POST", body: { behaviour: host.querySelector("#aiText").value } });
    if ((r.refused || []).length) toast(`${r.refused.length} suggestion(s) refused: ${r.refused.slice(0, 2).join("; ")}`, "info", 8000);
    await after({ created: r.created, needs_review: r.created });
  }));
  host.querySelector("#impFile").addEventListener("change", async (e) => { const f = e.target.files[0]; if (f) host.querySelector("#impText").value = await f.text(); });
  host.querySelector("#impBtn").addEventListener("click", (e) => guarded(e.target, async () => {
    const r = await api(`${BASE}/apps/${encodeURIComponent(a.id)}/import`, { method: "POST", body: { kind: host.querySelector("#impKind").value, text: host.querySelector("#impText").value } });
    if ((r.warnings || []).length) host.querySelector("#impOut").innerHTML = `<ul class="muted">${r.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`;
    await after({ created: r.created, needs_review: r.created });
  }));
}

// ---- tests --------------------------------------------------------------------------------------------------------
function renderTests(host) {
  const a = st.app, ed = canEdit();
  const review = st.tests.filter((t) => t.status === "needs_review").length;
  host.innerHTML = `<div class="card"><div class="row" style="gap:8px;flex-wrap:wrap">
      <button class="btn primary" id="tRunSel" disabled>Run selected</button><button class="btn" id="tRunAll" ${st.tests.length ? "" : "disabled"}>Run all</button>
      <label class="row" style="gap:6px">At the same time <input id="tPar" type="number" min="1" max="4" value="1" style="width:60px"></label>
      ${ed ? `<button class="btn" id="tNew">New test</button><button class="btn" id="tSuite" disabled>Suite from selected</button><button class="btn" id="tApprove" disabled>Approve selected</button>
        <button class="btn" id="tScript" ${st.tests.length ? "" : "disabled"}>Generate Script</button>` : ""}
      <a class="btn" href="#/scheduled-runs" title="API tests, suites and scripts are listed in Scheduled Runs (Page: API Testing)">Schedule…</a>
      <span class="spacer"></span><input id="tFilter" placeholder="Filter" style="width:200px"></div>
      ${review ? `<p class="pill WARN" style="white-space:normal;margin-top:8px">${review} test(s) need review: their expected result is not documented. Open one, check it, then Approve.</p>` : ""}
      <div id="tList" style="margin-top:8px"></div></div><div id="tRun"></div>`;
  const list = () => {
    const q = (host.querySelector("#tFilter").value || "").toLowerCase();
    const shown = st.tests.filter((t) => !q || `${t.name} ${t.method} ${t.path} ${t.kind}`.toLowerCase().includes(q));
    host.querySelector("#tList").innerHTML = shown.length ? `<div class="table-wrap"><table class="data"><thead><tr><th><input type="checkbox" id="tAll" style="width:auto;margin:0"></th><th>Test</th><th>Request</th><th>Kind</th><th>Status</th><th>Last result</th><th></th></tr></thead><tbody>
      ${shown.map((t) => `<tr><td><input type="checkbox" data-t="${esc(t.id)}" style="width:auto;margin:0" ${st.selected.has(t.id) ? "checked" : ""}></td>
        <td><strong>${esc(t.name)}</strong><div class="muted" style="font-size:12px">${esc(t.id)} · v${esc(t.version)} · ${esc(t.origin || "")}</div></td>
        <td><code>${esc(t.method)}</code> ${esc(t.path)}${t.destructive ? ' <span class="pill WARN" title="Changes data on the server">changes data</span>' : ""}</td>
        <td>${esc(t.kind || "")}</td><td>${t.status === "needs_review" ? `<span class="pill WARN" title="${esc(t.review_reason || "")}">Needs review</span>` : t.enabled ? '<span class="pill PASS">Ready</span>' : '<span class="pill WAITING">Disabled</span>'}</td>
        <td>${pill(t.last_status)}</td><td><button class="btn small" data-open="${esc(t.id)}">Open</button></td></tr>`).join("")}</tbody></table></div>`
      : `<p class="muted">${st.tests.length ? "No test matches the filter." : "No tests yet. Use Import & generate, or New test."}</p>`;
    host.querySelectorAll("[data-t]").forEach((c) => c.addEventListener("change", () => { c.checked ? st.selected.add(c.dataset.t) : st.selected.delete(c.dataset.t); sync(); }));
    host.querySelector("#tAll")?.addEventListener("change", (e) => { shown.forEach((t) => (e.target.checked ? st.selected.add(t.id) : st.selected.delete(t.id))); list(); sync(); });
    host.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => openTest(b.dataset.open)));
  };
  const sync = () => { const n = st.selected.size; ["#tRunSel", "#tSuite", "#tApprove"].forEach((s) => { const b = host.querySelector(s); if (b) b.disabled = !n; }); };
  host.querySelector("#tFilter").addEventListener("input", list);
  list(); sync();
  const par = () => Number(host.querySelector("#tPar").value) || 1;
  host.querySelector("#tRunSel").addEventListener("click", (e) => guarded(e.target, () => startRun({ test_ids: [...st.selected], parallel: par() })));
  host.querySelector("#tRunAll").addEventListener("click", (e) => guarded(e.target, () => startRun({ parallel: par() })));
  host.querySelector("#tNew")?.addEventListener("click", () => openTest(null));
  host.querySelector("#tApprove")?.addEventListener("click", (e) => guarded(e.target, async () => {
    const ids = [...st.selected].filter((id) => st.tests.find((t) => t.id === id)?.status === "needs_review");
    if (!ids.length) throw new Error("None of the selected tests needs review.");
    if (!confirm(`Approve the expected results of ${ids.length} test(s)? Check each one first: approved tests run in every run and schedule.`)) return;
    for (const id of ids) await api(`${BASE}/tests/${encodeURIComponent(id)}/approve`, { method: "POST", body: {} });
    await selectApp(a.id); render(); toast(`${ids.length} approved.`, "success");
  }));
  host.querySelector("#tSuite")?.addEventListener("click", () => suiteForm(null, [...st.selected]));
  host.querySelector("#tScript")?.addEventListener("click", (e) => guarded(e.target, async () => {
    st.script = await api(`${BASE}/apps/${encodeURIComponent(a.id)}/script/generate`, { method: "POST", body: st.selected.size ? { test_ids: [...st.selected] } : {} });
    st.tab = "script"; st.scriptTab = "View Script"; render(); toast(`Script v${st.script.current.version} generated.`, "success");
  }));
}

async function startRun(body, rerunOf = null) {
  const a = st.app;
  const ids = body.test_ids || (body.suite_id ? (st.suites.find((s) => s.id === body.suite_id)?.test_ids || []) : st.tests.map((t) => t.id));
  const changing = st.tests.filter((t) => ids.includes(t.id) && t.destructive && t.enabled && t.status !== "needs_review");
  let confirmChanges = false;
  if (changing.length) {
    const prod = a.environment === "Production";
    confirmChanges = confirm(`${changing.length} test(s) change data on ${a.name}${prod ? " (PRODUCTION)" : ""}:\n\n${changing.slice(0, 8).map((t) => `${t.method} ${t.path}`).join("\n")}${changing.length > 8 ? "\n…" : ""}\n\nOK = run them too. Cancel = run only the tests that do not change data (the others are reported as BLOCKED).`);
  }
  const r = rerunOf != null ? await api(`${BASE}/results/${rerunOf}/rerun`, { method: "POST", body: { confirm_state_changes: confirmChanges } })
    : await api(`${BASE}/apps/${encodeURIComponent(a.id)}/run`, { method: "POST", body: { ...body, confirm_state_changes: confirmChanges } });
  toast(`Run ${r.run_id} started.`, "success");
  st.tab = "runs"; render();
  await showRun(r.run_id, true);
}

function testForm(t) {
  const ro = !canEdit();
  const roles = (st.app.config.roles || []).map((r) => r.name);
  const area = (name, label, value, rows = 4) => `<label style="grid-column:1/-1">${label}<textarea name="${name}" rows="${rows}" spellcheck="false" class="mono" ${ro ? "readonly" : ""}>${esc(value)}</textarea></label>`;
  return `<form class="grid" id="tForm" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr))">
    ${t?.status === "needs_review" ? `<p class="pill WARN" style="grid-column:1/-1;white-space:normal">Needs review: ${esc(t.review_reason || "the expected result is not documented")}. It does not run until approved.</p>` : ""}
    <label style="grid-column:span 2">Name <input name="name" required value="${esc(t?.name || "")}" ${ro ? "readonly" : ""}></label>
    <label>Method <select name="method" ${ro ? "disabled" : ""}>${METHODS.map((m) => `<option ${(t?.method || "GET") === m ? "selected" : ""}>${m}</option>`).join("")}</select></label>
    <label>Authentication <select name="auth" ${ro ? "disabled" : ""}>${[["app", "Application's"], ["none", "None (no credentials)"], ...roles.map((r) => [`role:${r}`, `Role: ${r}`])]
      .map(([v, l]) => `<option value="${esc(v)}" ${(t?.auth || "app") === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
    <label style="grid-column:1/-1">Path (relative to the base URL; {{variable}} uses an extracted or data value) <input name="path" required value="${esc(t?.path || "/")}" ${ro ? "readonly" : ""}></label>
    <label>Content type <input name="content_type" value="${esc(t?.content_type || "")}" placeholder="application/json" ${ro ? "readonly" : ""}></label>
    <label>Timeout (ms, empty = application) <input name="timeout_ms" type="number" min="100" value="${esc(t?.timeout_ms || "")}" ${ro ? "readonly" : ""}></label>
    <label class="row" style="gap:6px"><input type="checkbox" name="enabled" style="width:auto;margin:0" ${t?.enabled !== false ? "checked" : ""} ${ro ? "disabled" : ""}>Enabled</label>
    ${area("path_params", "Path parameters (JSON object)", json(t?.path_params || {}), 2)}
    ${area("query", 'Query parameters (JSON list of {"name": ..., "value": ...})', json(t?.query || []), 3)}
    ${area("headers", 'Headers (JSON list of {"name": ..., "value": ...}; credentials come from Authentication)', json(t?.headers || []), 3)}
    ${area("body", "Body (JSON, or a string)", json(t?.body ?? null), 5)}
    ${area("assertions", 'Assertions (JSON list: {"type": "status", "expected": ["200"]}, {"type": "jsonpath", "path": "$.id", "op": "exists"}, {"type": "schema"}, {"type": "response_time", "max_ms": 800} …)', json(t?.assertions || [{ type: "status", expected: ["2xx"] }]), 7)}
    ${area("extract", 'Extract variables for later tests (JSON list of {"name": "orderId", "from": "body", "path": "$.id"})', json(t?.extract || []), 3)}
    ${area("data_rows", "Test data rows (JSON list of objects; the test runs once per row, {{column}} uses a value)", json(t?.data_rows || []), 3)}
    <label style="grid-column:1/-1">What changed (optional) <input name="note" maxlength="300" ${ro ? "readonly" : ""}></label>
    <div id="tErr" style="grid-column:1/-1"></div>
    <div class="row" style="grid-column:1/-1;gap:8px;flex-wrap:wrap">
      ${t ? `<button class="btn" type="button" data-x="history">Versions (${(t.versions || []).length})</button>` : ""}
      ${t && !ro ? '<button class="btn" type="button" data-x="delete">Delete</button>' : ""}
      <span class="spacer"></span>
      ${ro ? "" : '<button class="btn primary" type="submit">Save</button>'}<button class="btn" type="button" data-x="validate">Validate</button>
      ${t ? '<button class="btn" type="button" data-x="run">Run</button>' : ""}${t && !ro ? '<button class="btn" type="button" data-x="script">Generate Script</button>' : ""}
      ${t?.status === "needs_review" && !ro ? '<button class="btn" type="button" data-x="approve">Approve expected result</button>' : ""}
      <a class="btn" href="#/scheduled-runs">Schedule…</a></div>
    ${t && (t.recent_results || []).length ? `<div style="grid-column:1/-1"><h4>Recent results</h4><table class="data compact"><tbody>${t.recent_results.map((r) => `<tr><td>${esc(dateTimeOf(r.at))}</td><td>${pill(r.status)}</td><td>${esc(r.status_code ?? "")}</td><td>${r.time_ms != null ? `${r.time_ms} ms` : ""}</td><td>${r.draft_id ? `<a href="#/defect-review?id=${r.draft_id}">Draft #${r.draft_id}</a>` : ""}</td><td><button class="btn small" type="button" data-res="${r.id}">View</button></td></tr>`).join("")}</tbody></table></div>` : ""}
  </form>`;
}

function readTest(f) {
  const fd = new FormData(f);
  const parse = (k, fallback) => { const v = String(fd.get(k) || "").trim(); if (!v) return fallback; try { return JSON.parse(v); } catch { throw new Error(`${k.replace("_", " ")}: not valid JSON.`); } };
  return { name: fd.get("name"), method: f.method.value, auth: f.auth.value, path: fd.get("path"), content_type: fd.get("content_type") || null,
    timeout_ms: fd.get("timeout_ms") ? Number(fd.get("timeout_ms")) : null, enabled: f.enabled.checked, path_params: parse("path_params", {}), query: parse("query", []),
    headers: parse("headers", []), body: parse("body", null), assertions: parse("assertions", []), extract: parse("extract", []), data_rows: parse("data_rows", []), note: fd.get("note") || null };
}

async function openTest(id) {
  const t = id ? (await api(`${BASE}/tests/${encodeURIComponent(id)}`)).test : null;
  const m = openModal(t ? `${t.name} (v${t.version})` : "New API test", testForm(t), { wide: true });
  const f = m.el.querySelector("#tForm");
  const showErr = (r) => { m.el.querySelector("#tErr").innerHTML = (r.errors || []).length || (r.warnings || []).length ? `<div class="pill ${(r.errors || []).length ? "FAIL" : "WARN"}" style="white-space:normal;display:block">${[...(r.errors || []), ...(r.warnings || [])].map(esc).join("<br>")}</div>` : '<span class="pill PASS">No problems found.</span>'; };
  const done = async () => { m.close(); await selectApp(st.app.id); render(); };
  f.addEventListener("submit", (e) => { e.preventDefault(); guarded(f.querySelector("[type=submit]"), async () => {
    const body = readTest(f);
    if (t) await api(`${BASE}/tests/${encodeURIComponent(t.id)}`, { method: "PUT", body });
    else await api(`${BASE}/apps/${encodeURIComponent(st.app.id)}/tests`, { method: "POST", body });
    toast("Saved (a new version).", "success"); await done();
  }); });
  m.el.querySelectorAll("[data-x]").forEach((b) => b.addEventListener("click", () => guarded(b, async () => {
    const x = b.dataset.x;
    if (x === "validate") showErr(await api(`${BASE}/tests/validate`, { method: "POST", body: readTest(f) }));
    else if (x === "run") { m.close(); await startRun({ test_ids: [t.id] }); }
    else if (x === "approve") { await api(`${BASE}/tests/${encodeURIComponent(t.id)}/approve`, { method: "POST", body: {} }); toast("Approved.", "success"); await done(); }
    else if (x === "script") { st.script = await api(`${BASE}/apps/${encodeURIComponent(st.app.id)}/script/generate`, { method: "POST", body: { test_ids: [t.id] } }); m.close(); st.tab = "script"; st.scriptTab = "View Script"; render(); }
    else if (x === "delete") {
      if (!(await confirmDelete({ title: "Delete API test?", name: t.name, body: "Its versions are deleted too. Run history is kept." }))) return;
      await api(`${BASE}/tests/${encodeURIComponent(t.id)}`, { method: "DELETE" }); await done();
    } else if (x === "history") {
      const h = openModal(`Versions of ${t.name}`, `<table class="data"><thead><tr><th>Version</th><th>Note</th><th>By</th><th>When</th><th></th></tr></thead><tbody>${t.versions.map((v) => `<tr><td>v${v.version}</td><td>${esc(v.note || "")}</td><td>${esc(v.created_by || "")}</td><td>${esc(dateTimeOf(v.created_at))}</td><td>${canEdit() && v.version !== t.version ? `<button class="btn small" data-rv="${v.version}">Restore</button>` : ""}</td></tr>`).join("")}</tbody></table>`);
      h.el.querySelectorAll("[data-rv]").forEach((rb) => rb.addEventListener("click", () => guarded(rb, async () => {
        await api(`${BASE}/tests/${encodeURIComponent(t.id)}/restore/${rb.dataset.rv}`, { method: "POST", body: {} }); h.close(); toast("Restored as a new version.", "success"); await done();
      })));
    }
  })));
  m.el.querySelectorAll("[data-res]").forEach((b) => b.addEventListener("click", () => guarded(b, () => showResult(Number(b.dataset.res)))));
}

// ---- suites -------------------------------------------------------------------------------------------------------
function suiteForm(s, ids) {
  const m = openModal(s ? `Suite ${s.name}` : "New suite", `<form class="grid" id="sForm"><label>Name <input name="name" required maxlength="120" value="${esc(s?.name || "")}"></label>
    <label class="row" style="gap:6px"><input type="checkbox" name="chain" style="width:auto;margin:0" ${s?.chain ? "checked" : ""}>Chain: run in this order and pass extracted values ({{name}}) to the next tests; a test whose input is missing is BLOCKED</label>
    <label>Tests at the same time (not for chains) <input name="parallel" type="number" min="1" max="4" value="${esc(s?.parallel || 1)}"></label>
    <p class="muted">${ids.length} test(s), in this order:</p><ol>${ids.map((id) => `<li>${esc(st.tests.find((t) => t.id === id)?.name || id)}</li>`).join("")}</ol>
    <div class="row"><span class="spacer"></span><button class="btn primary" type="submit">Save suite</button></div></form>`);
  const f = m.el.querySelector("#sForm");
  f.addEventListener("submit", (e) => { e.preventDefault(); guarded(f.querySelector("[type=submit]"), async () => {
    const body = { name: f.name.value, chain: f.chain.checked, parallel: Number(f.parallel.value) || 1, test_ids: ids };
    if (s) await api(`${BASE}/suites/${encodeURIComponent(s.id)}`, { method: "PUT", body });
    else await api(`${BASE}/apps/${encodeURIComponent(st.app.id)}/suites`, { method: "POST", body });
    m.close(); await selectApp(st.app.id); st.tab = "suites"; render(); toast("Suite saved.", "success");
  }); });
}

function renderSuites(host) {
  host.innerHTML = `<div class="card">${st.suites.length ? `<table class="data"><thead><tr><th>Suite</th><th>Tests</th><th>Kind</th><th></th></tr></thead><tbody>
    ${st.suites.map((s) => `<tr><td><strong>${esc(s.name)}</strong><div class="muted" style="font-size:12px">${esc(s.id)}</div></td><td>${s.test_ids.map((id) => esc(st.tests.find((t) => t.id === id)?.name || id)).join("<br>")}</td>
      <td>${s.chain ? "Chain (sequential, variables passed on)" : s.parallel > 1 ? `${s.parallel} at the same time` : "Sequential"}</td>
      <td class="row" style="gap:6px;flex-wrap:wrap"><button class="btn small" data-run="${esc(s.id)}">Run</button>${canEdit() ? `<button class="btn small" data-edit="${esc(s.id)}">Edit</button><button class="btn small" data-script="${esc(s.id)}">Generate Script</button><button class="btn small" data-del="${esc(s.id)}">Delete</button>` : ""}</td></tr>`).join("")}
    </tbody></table>` : '<p class="muted">No suites yet. In Tests, tick tests and choose Suite from selected.</p>'}</div>`;
  host.querySelectorAll("[data-run]").forEach((b) => b.addEventListener("click", () => guarded(b, () => startRun({ suite_id: b.dataset.run }))));
  host.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => { const s = st.suites.find((x) => x.id === b.dataset.edit); suiteForm(s, s.test_ids); }));
  host.querySelectorAll("[data-script]").forEach((b) => b.addEventListener("click", () => guarded(b, async () => {
    st.script = await api(`${BASE}/apps/${encodeURIComponent(st.app.id)}/script/generate`, { method: "POST", body: { suite_id: b.dataset.script } });
    st.tab = "script"; st.scriptTab = "View Script"; render();
  })));
  host.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    const s = st.suites.find((x) => x.id === b.dataset.del);
    if (!(await confirmDelete({ title: "Delete suite?", name: s.name, body: "Its tests are kept." }))) return;
    guarded(b, async () => { await api(`${BASE}/suites/${encodeURIComponent(s.id)}`, { method: "DELETE" }); await selectApp(st.app.id); render(); });
  }));
}

// ---- runs and the response viewer --------------------------------------------------------------------------------
function countsText(c) {
  if (!c) return "";
  return `${c.total ?? 0} total · ${c.PASSED || 0} passed · ${c.FAILED || 0} failed · ${c.SKIPPED || 0} skipped · ${c.BLOCKED || 0} blocked`;
}

function renderRuns(host) {
  host.innerHTML = `<div class="card"><div class="row"><h3 style="margin:0">Runs</h3><span class="spacer"></span><button class="btn small" id="rRef">Refresh</button></div>
    ${st.runs.length ? `<table class="data" style="margin-top:8px"><thead><tr><th>Run</th><th>What</th><th>Trigger</th><th>Result</th><th>Counts</th><th>Started</th><th></th></tr></thead><tbody>
      ${st.runs.map((r) => `<tr><td>${esc(r.id)}</td><td>${esc(r.label || "")}<div class="muted" style="font-size:12px">${esc(r.mode_reason || "")}</div></td><td>${esc(r.trigger)}</td><td>${pill(r.status)}</td>
        <td>${esc(countsText(r.counts))}</td><td>${esc(dateTimeOf(r.started_at))}</td>
        <td class="row" style="gap:6px"><button class="btn small" data-show="${esc(r.id)}">Open</button><a class="btn small" href="${BASE}/runs/${esc(r.id)}/report.html" target="_blank" rel="noopener">Report</a></td></tr>`).join("")}</tbody></table>`
      : '<p class="muted">No runs yet.</p>'}</div><div id="runDetail"></div>`;
  host.querySelector("#rRef").addEventListener("click", async () => { st.runs = (await api(`${BASE}/runs?app_id=${encodeURIComponent(st.app.id)}&limit=30`)).runs; render(); });
  host.querySelectorAll("[data-show]").forEach((b) => b.addEventListener("click", () => guarded(b, () => showRun(b.dataset.show, false))));
  if (st.run) showRun(st.run, false).catch(() => {});
}

async function showRun(runId, poll) {
  st.run = runId;
  stopTimers();
  const draw = async () => {
    const box = view.querySelector("#runDetail");
    if (!box) { stopTimers(); return null; }
    const { run: r } = await api(`${BASE}/runs/${encodeURIComponent(runId)}`);
    box.innerHTML = `<div class="card" style="margin-top:12px"><div class="row" style="gap:8px;flex-wrap:wrap"><h3 style="margin:0">Run ${esc(r.id)}</h3>${pill(r.status)}
        <span class="muted">${esc(countsText(r.counts))}${r.duration_ms ? ` · ${(r.duration_ms / 1000).toFixed(1)} s` : ""}</span><span class="spacer"></span>
        ${r.status === "RUNNING" ? '<button class="btn small" id="rCancel">Stop</button>' : ""}<a class="btn small" href="${BASE}/runs/${esc(r.id)}/report.html" target="_blank" rel="noopener">HTML report</a></div>
      <p class="muted" style="margin:4px 0">Execution mode: ${esc(r.mode_reason || "Authorized API/HTTP")}</p>
      ${r.summary?.output_tail ? `<details><summary>pytest output</summary><pre class="code">${esc(r.summary.output_tail)}</pre></details>` : ""}
      <table class="data" style="margin-top:8px"><thead><tr><th>Test</th><th>Result</th><th>HTTP</th><th>Time</th><th>Failure</th><th>Classification</th><th>Defect draft</th><th></th></tr></thead><tbody>
      ${r.results.map((x) => `<tr><td>${esc(x.test_name)}${x.row_label ? `<div class="muted">${esc(x.row_label)}</div>` : ""}</td><td>${pill(x.status)}</td><td>${esc(x.status_code ?? "")}</td>
        <td>${x.time_ms != null ? `${x.time_ms} ms` : ""}</td><td style="max-width:380px">${esc(x.error || (x.assertions || []).filter((y) => y.passed === false).map((y) => y.message).join("; "))}</td>
        <td>${esc(x.classification?.label || "")}</td><td>${x.draft_id ? `<a href="#/defect-review?id=${x.draft_id}">Draft #${x.draft_id}</a>` : ""}</td>
        <td>${x.test_id ? `<button class="btn small" data-res="${x.id}">View</button>` : ""}</td></tr>`).join("") || '<tr><td colspan="8" class="muted">Starting…</td></tr>'}</tbody></table></div>`;
    box.querySelector("#rCancel")?.addEventListener("click", (e) => guarded(e.target, () => api(`${BASE}/runs/${encodeURIComponent(r.id)}/cancel`, { method: "POST", body: {} })));
    box.querySelectorAll("[data-res]").forEach((b) => b.addEventListener("click", () => guarded(b, () => showResult(Number(b.dataset.res)))));
    return r;
  };
  const r = await draw();
  if (poll || r?.status === "RUNNING") {
    timers.push(setInterval(async () => {
      try {
        const x = await draw();
        if (!x || x.status !== "RUNNING") {
          stopTimers();
          if (x) { st.runs = (await api(`${BASE}/runs?app_id=${encodeURIComponent(st.app.id)}&limit=30`)).runs; const t = await api(`${BASE}/apps/${encodeURIComponent(st.app.id)}/tests`); st.tests = t.tests; if (st.tab === "runs") render(); }
        }
      } catch { stopTimers(); }
    }, 1500));
  }
}

async function showResult(id) {
  const { result: x } = await api(`${BASE}/results/${id}`);
  const req = x.request || {}, res = x.response || {};
  const m = openModal(`${x.test_name} · ${x.status}`, `
    <div class="row" style="gap:8px;flex-wrap:wrap">${pill(x.status)}<span>HTTP ${esc(res.status ?? "-")} ${esc(res.reason || "")}</span><span class="muted">${res.time_ms != null ? `${res.time_ms} ms` : ""} · ${esc(res.size != null ? `${res.size} bytes` : "")}</span>
      <span class="spacer"></span>${x.draft_id ? `<a class="btn small" href="#/defect-review?id=${x.draft_id}">Defect draft #${x.draft_id}</a>` : ""}${x.test_id ? '<button class="btn small" id="reRun">Rerun</button>' : ""}</div>
    ${x.error ? `<p class="pill FAIL" style="white-space:normal">${esc(x.error)}</p>` : ""}
    ${x.classification ? `<p><strong>${esc(x.classification.label || x.classification.classification)}</strong> ${x.classification.confidence != null ? `(${Math.round(x.classification.confidence * 100)}%)` : ""}</p>${(x.classification.evidence || []).length ? `<ul>${x.classification.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}` : ""}
    <h4>Assertions</h4>${(x.assertions || []).length ? `<table class="data compact"><tbody>${x.assertions.map((y) => `<tr><td>${y.passed === true ? '<span class="pill PASS">PASS</span>' : y.passed === false ? '<span class="pill FAIL">FAIL</span>' : '<span class="pill WAITING">-</span>'}</td><td>${esc(y.description || y.type)}</td><td>${esc(y.message || "")}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">None.</p>'}
    <h4>Request</h4><p><code>${esc(req.method || "")}</code> ${esc(req.url || "")}</p>${headersTable(req.headers)}
    <details><summary>Request body</summary>${bodyView(req.body)}</details>
    <h4>Response</h4>${headersTable(res.headers)}<div class="row" style="gap:6px;margin:6px 0"><button class="btn small" data-v="tree">JSON tree</button><button class="btn small" data-v="raw">Raw</button></div>
    <div id="resBody">${bodyView(res.body)}</div>
    ${res.extracted ? `<h4>Extracted variables</h4>${headersTable(Object.entries(res.extracted))}` : ""}
    ${res.redirects?.length ? `<h4>Redirects</h4><ol>${res.redirects.map((u) => `<li>${esc(u)}</li>`).join("")}</ol>` : ""}
    <p class="muted">Passwords, tokens, keys and cookies are shown as [redacted].${res.truncated ? " The body was cut at the size limit." : ""}</p>`, { wide: true });
  m.el.querySelectorAll("[data-v]").forEach((b) => b.addEventListener("click", () => {
    m.el.querySelector("#resBody").innerHTML = b.dataset.v === "raw" ? `<pre class="code">${esc(typeof res.body === "string" ? res.body : json(res.body))}</pre>` : bodyView(res.body);
  }));
  m.el.querySelector("#reRun")?.addEventListener("click", (e) => guarded(e.target, async () => { m.close(); await startRun({}, x.id); }));
}

// ---- Python script (pytest + httpx) -------------------------------------------------------------------------------
async function renderScript(host) {
  const a = st.app;
  if (!st.script || st.script.app_id !== a.id) st.script = await api(`${BASE}/apps/${encodeURIComponent(a.id)}/script`);
  const s = st.script, cur = s.current;
  host.innerHTML = `<div class="card" id="scCard"><div class="row" style="gap:6px;flex-wrap:wrap"><h3 style="margin:0">Python API test (pytest + httpx)</h3>
      ${cur ? `<span class="pill ${cur.edited ? "WARN" : "WAITING"}">v${esc(cur.version)} · ${cur.edited ? "edited" : "generated"}</span>` : ""}<span class="spacer"></span>
      ${canEdit() ? `<button class="btn" data-s="generate">${cur ? "Regenerate from all tests" : "Generate Script"}</button>` : ""}
      ${cur ? `<button class="btn" data-s="run">Run</button><a class="btn" href="${BASE}/apps/${encodeURIComponent(a.id)}/script/download">Download ${esc(s.file_name)}</a><a class="btn" href="#/scheduled-runs">Schedule…</a>` : ""}</div>
    ${cur ? `<div class="row tabs" style="gap:6px;flex-wrap:wrap;margin:10px 0">${SCRIPT_TABS.map((t) => `<button class="chip ${st.scriptTab === t ? "on" : ""}" data-st="${esc(t)}">${esc(t)}</button>`).join("")}</div><div id="scBody"></div>`
      : '<p class="muted">No script yet. Generate Script writes one test function per API test; secrets are read from environment variables (SKYGUARD_API_PASSWORD, SKYGUARD_API_TOKEN, SKYGUARD_API_KEY, SKYGUARD_API_CLIENT_SECRET), never stored in the file.</p>'}</div>`;
  host.querySelectorAll("[data-st]").forEach((b) => b.addEventListener("click", () => { st.scriptTab = b.dataset.st; renderScript(host); }));
  host.querySelectorAll("[data-s]").forEach((b) => b.addEventListener("click", () => guarded(b, async () => {
    if (b.dataset.s === "generate") {
      if (cur?.edited && !confirm("The current version was edited by hand. Regenerating creates a new version from the tests (your edit stays in History). Continue?")) return;
      st.script = await api(`${BASE}/apps/${encodeURIComponent(a.id)}/script/generate`, { method: "POST", body: {} });
      toast(`Script v${st.script.current.version} generated.`, "success");
    } else {
      toast("Running the script with pytest…", "info");
      const { run } = await api(`${BASE}/apps/${encodeURIComponent(a.id)}/script/run`, { method: "POST" });
      st.lastScriptRun = run; st.scriptTab = "Result";
      toast(`Script ${run.status}: ${countsText(run.counts)}`, run.status === "PASSED" ? "success" : "error", 8000);
    }
    renderScript(host);
  })));
  if (!cur) return;
  const body = host.querySelector("#scBody");
  const src = cur.source || "";
  const tests = st.tests;
  const tab = st.scriptTab;
  if (tab === "View Script") {
    body.innerHTML = `<div class="row" style="gap:6px;margin-bottom:6px">${sizeButtons()}</div><div class="pyed"><pre class="gutter" aria-hidden="true">${gutterHtml(lines(src))}</pre><div class="pyed-stack"><pre class="code py pyed-hl">${highlightPython(src)}\n</pre></div></div>`;
    wireSize(host.querySelector("#scCard"));
  } else if (tab === "Edit Script") {
    if (!canEdit()) { body.innerHTML = '<p class="muted">A view-only account cannot edit scripts.</p>'; return; }
    body.innerHTML = `<div class="row" style="gap:6px;flex-wrap:wrap;margin-bottom:6px"><input id="scFind" placeholder="Find" style="width:160px"><input id="scRepl" placeholder="Replace with" style="width:160px">
        <button class="btn small" data-e="find">Find next</button><button class="btn small" data-e="replaceAll">Replace all</button><span class="spacer"></span>${sizeButtons()}</div>
      <div id="scErr"></div>
      <div class="pyed"><pre class="gutter" aria-hidden="true" id="scGut">${gutterHtml(lines(src), st.scriptErrors)}</pre><div class="pyed-stack"><pre class="code py pyed-hl" aria-hidden="true" id="scHl">${highlightPython(src)}\n</pre>
        <textarea id="scSrc" class="pyed-ta" spellcheck="false" wrap="off" aria-label="Python API test">${esc(src)}</textarea></div></div>
      <div class="row" style="gap:8px;margin-top:8px"><input id="scNote" placeholder="What changed (optional)" style="width:260px"><span class="spacer"></span>
        <button class="btn" data-e="validate">Validate</button><button class="btn primary" data-e="save">Save</button></div>`;
    wireSize(host.querySelector("#scCard"));
    const ta = body.querySelector("#scSrc");
    const refresh = () => {
      body.querySelector("#scHl").innerHTML = highlightPython(ta.value) + "\n";
      body.querySelector("#scGut").innerHTML = gutterHtml(lines(ta.value), st.scriptErrors);
      ta.style.height = "0px"; ta.style.height = `${ta.scrollHeight}px`;
      ta.style.width = "100%"; ta.style.width = `${Math.max(ta.scrollWidth, ta.parentElement.clientWidth)}px`;
    };
    const showErr = (errs) => {
      st.scriptErrors = errs || [];
      body.querySelector("#scErr").innerHTML = st.scriptErrors.length ? `<div class="pill FAIL" style="white-space:normal;display:block">${st.scriptErrors.map((e) => `Line ${esc(e.line)}, column ${esc(e.column)}: ${esc(e.message)}`).join("<br>")}</div>` : "";
      refresh();
    };
    const insert = (text) => { ta.focus(); if (!document.execCommand("insertText", false, text)) ta.setRangeText(text, ta.selectionStart, ta.selectionEnd, "end"); refresh(); };
    ta.addEventListener("input", refresh);
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Tab" && !e.ctrlKey && !e.altKey) { e.preventDefault(); if (!e.shiftKey) insert("    "); }
      else if (e.key === "Enter" && !e.ctrlKey) {
        e.preventDefault();
        const s0 = ta.value.lastIndexOf("\n", ta.selectionStart - 1) + 1, line = ta.value.slice(s0, ta.selectionStart);
        insert("\n" + (line.match(/^\s*/) || [""])[0] + (/:\s*(#.*)?$/.test(line) ? "    " : ""));
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); body.querySelector('[data-e="save"]').click(); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") { e.preventDefault(); body.querySelector("#scFind").focus(); }
    });
    requestAnimationFrame(refresh);
    body.querySelectorAll("[data-e]").forEach((b) => b.addEventListener("click", () => guarded(b, async () => {
      const q = body.querySelector("#scFind").value;
      if (b.dataset.e === "find") {
        if (!q) return; let i = ta.value.indexOf(q, ta.selectionEnd); if (i < 0) i = ta.value.indexOf(q);
        if (i < 0) { toast("Not found", "info"); return; } ta.focus(); ta.setSelectionRange(i, i + q.length);
      } else if (b.dataset.e === "replaceAll") {
        if (!q) return; const n = ta.value.split(q).length - 1; if (!n) { toast("Not found", "info"); return; }
        ta.focus(); ta.select(); insert(ta.value.split(q).join(body.querySelector("#scRepl").value)); toast(`${n} replaced`, "success");
      } else if (b.dataset.e === "validate") {
        const r = await api(`${BASE}/scripts/validate`, { method: "POST", body: { source: ta.value } });
        showErr(r.errors); toast(r.ok ? "No errors found" : `${r.errors.length} problem(s) found`, r.ok ? "success" : "error");
      } else {
        try {
          st.script = await api(`${BASE}/apps/${encodeURIComponent(a.id)}/script`, { method: "PUT", body: { source: ta.value, note: body.querySelector("#scNote").value } });
          st.scriptErrors = []; st.scriptTab = "View Script"; toast(`Saved as v${st.script.current.version}.`, "success"); renderScript(host);
        } catch (err) { if (err.details?.errors) showErr(err.details.errors); throw err; }
      }
    })));
  } else if (tab === "Flow Logic") {
    body.innerHTML = tests.length ? `<ol>${tests.map((t) => `<li><code>${esc(t.method)}</code> ${esc(t.path)} — ${esc(t.name)}
      <div class="muted">Checks: ${esc((t.assertions || []).map((x) => x.type === "status" ? `status ${[].concat(x.expected).join("/")}` : x.type === "jsonpath" ? `${x.path} ${x.op || "exists"}${x.value != null ? ` ${JSON.stringify(x.value)}` : ""}` : x.type === "response_time" ? `≤ ${x.max_ms} ms` : x.type).join(", ") || "none")}
      ${(t.extract || []).length ? ` · extracts ${esc(t.extract.map((x) => x.name).join(", "))}` : ""}${t.status === "needs_review" ? " · skipped until reviewed" : ""}</div></li>`).join("")}</ol>` : '<p class="muted">No tests.</p>';
  } else if (tab === "Test Data") {
    const withRows = tests.filter((t) => (t.data_rows || []).length);
    body.innerHTML = withRows.length ? withRows.map((t) => { const cols = [...new Set(t.data_rows.flatMap((r) => Object.keys(r)))]; return `<h4>${esc(t.name)}</h4><table class="data compact"><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${t.data_rows.map((r) => `<tr>${cols.map((c) => `<td>${esc(r[c] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table>`; }).join("")
      : '<p class="muted">No test uses data rows. Add them in a test (Test data rows) to run it once per row.</p>';
  } else if (tab === "Credentials") {
    const c = a.credentials || { app: {} };
    body.innerHTML = `<p>Authentication: <strong>${esc(AUTHS.find(([v]) => v === (a.config.auth || {}).type)?.[1] || "None")}</strong>${a.config.auth?.username ? ` · user ${esc(a.config.auth.username)}` : ""}</p>
      <table class="data compact"><thead><tr><th>Secret</th><th>Stored in the Credential Manager</th><th>Environment variable the script reads</th></tr></thead><tbody>
      ${[["password", "SKYGUARD_API_PASSWORD"], ["token", "SKYGUARD_API_TOKEN"], ["api_key", "SKYGUARD_API_KEY"], ["client_secret", "SKYGUARD_API_CLIENT_SECRET"]].map(([f, v]) => `<tr><td>${esc(f)}</td><td>${c.app?.[f] ? '<span class="pill PASS">yes</span>' : '<span class="pill WAITING">no</span>'}</td><td><code>${v}</code></td></tr>`).join("")}</tbody></table>
      <p class="muted">When SkyGuard runs the script it passes the stored values in these variables for that run only. The file never contains them; saving a script that contains a stored secret is refused.</p>`;
  } else if (tab === "Components") {
    const defs = [...src.matchAll(/^def\s+(\w+)\s*\(/gm)].map((m) => m[1]);
    const imports = [...src.matchAll(/^(?:from\s+\S+\s+)?import\s+.+$/gm)].map((m) => m[0]);
    const cases = (src.match(/^\s*\{"id":/gm) || []).length;
    body.innerHTML = `<h4>Imports</h4><ul>${imports.map((x) => `<li><code>${esc(x)}</code></li>`).join("")}</ul><h4>Functions</h4><ul>${defs.map((d) => `<li><code>${esc(d)}()</code></li>`).join("")}</ul>
      <p class="muted">${cases || tests.length} test case(s) in CASES; helper functions check status codes, JSONPath values and response schemas offline.</p>`;
  } else if (tab === "History") {
    body.innerHTML = `<table class="data"><thead><tr><th>Version</th><th>Kind</th><th>Note</th><th>By</th><th>When</th><th>SHA-256</th><th></th></tr></thead><tbody>${s.versions.map((v) => `<tr><td>v${v.version}${v.version === cur.version ? ' <span class="pill PASS">current</span>' : ""}</td><td>${v.edited ? "edited" : "generated"}</td><td>${esc(v.note || "")}</td><td>${esc(v.created_by || "")}</td><td>${esc(dateTimeOf(v.created_at))}</td><td><code>${esc((v.sha256 || "").slice(0, 12))}</code></td>
      <td>${canEdit() && v.version !== cur.version ? `<button class="btn small" data-rv="${v.version}">Restore</button>` : ""} <a class="btn small" href="${BASE}/apps/${encodeURIComponent(a.id)}/script/download?version=${v.version}">Download</a></td></tr>`).join("")}</tbody></table>`;
    body.querySelectorAll("[data-rv]").forEach((b) => b.addEventListener("click", () => guarded(b, async () => {
      st.script = await api(`${BASE}/apps/${encodeURIComponent(a.id)}/script/restore/${b.dataset.rv}`, { method: "POST", body: {} }); toast("Restored as a new version.", "success"); renderScript(host);
    })));
  } else {
    const runs = (await api(`${BASE}/runs?app_id=${encodeURIComponent(a.id)}&limit=50`)).runs.filter((r) => (r.label || "").startsWith("Python script"));
    const last = st.lastScriptRun || (runs[0] ? (await api(`${BASE}/runs/${encodeURIComponent(runs[0].id)}`)).run : null);
    if (!last) { body.innerHTML = '<p class="muted">The script has not run yet. Choose Run.</p>'; return; }
    if (tab === "Result") {
      body.innerHTML = `<p>${pill(last.status)} ${esc(last.label || "")} · ${esc(countsText(last.counts))} · ${esc(dateTimeOf(last.started_at))}</p>
        <table class="data compact"><tbody>${last.results.map((x) => `<tr><td>${esc(x.test_name)}</td><td>${pill(x.status)}</td><td>${esc(x.error || "")}</td><td>${x.draft_id ? `<a href="#/defect-review?id=${x.draft_id}">Draft #${x.draft_id}</a>` : ""}</td></tr>`).join("")}</tbody></table>`;
    } else if (tab === "Evidence") {
      body.innerHTML = `<ul>${runs.slice(0, 20).map((r) => `<li>${pill(r.status)} <a href="${BASE}/runs/${esc(r.id)}/report.html" target="_blank" rel="noopener">${esc(r.id)}</a> · ${esc(r.label)} · ${esc(dateTimeOf(r.started_at))}</li>`).join("")}</ul>
        <p class="muted">Each run keeps the script version it ran, its JUnit XML and the redacted pytest output in the SkyGuard data folder (api-testing evidence).</p>`;
    } else {
      body.innerHTML = `<pre class="code" style="max-height:520px;overflow:auto">${esc(last.summary?.output_tail || "No output recorded.")}</pre><p class="muted">Secrets are replaced by [redacted].</p>`;
    }
  }
}

async function renderAudit(host) {
  const { audit } = await api(`${BASE}/apps/${encodeURIComponent(st.app.id)}/audit`);
  host.innerHTML = `<div class="card"><h3 style="margin-top:0">Audit</h3>${audit.length ? `<table class="data compact"><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead><tbody>
    ${audit.map((x) => `<tr><td>${esc(dateTimeOf(x.at))}</td><td>${esc(x.by || "")}</td><td>${esc(x.action)}</td><td>${esc(x.target || "")}</td><td><code>${esc(typeof x.detail === "string" ? x.detail : JSON.stringify(x.detail || {}))}</code></td></tr>`).join("")}</tbody></table>`
    : '<p class="muted">Nothing recorded yet.</p>'}</div>`;
}

export const apiTesting = {
  async mount(el) {
    view = el; stopTimers();
    const p = hashParams();
    await loadApps();
    const want = p.get("app") || (st.app && st.apps.some((x) => x.id === st.app.id) ? st.app.id : st.apps[0]?.id);
    await selectApp(want || null);
    if (p.get("run") && st.app) { st.tab = "runs"; st.run = p.get("run"); }
    render();
  },
  unmount() { stopTimers(); },
};
