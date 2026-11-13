// 23.5.0: Adaptive AI Application Learning & Operations Agent (SkyGuard integrated module).
// Uses the SkyGuard sign-in (no second login), the local Qwen model through the AI gateway, the local speech
// recognizer, and a SkyGuard-controlled browser. Every business operation: preview -> explicit Confirm -> execute ->
// verify in the application. Passwords only through the secure credential form, never through chat.
import { api } from "../api.js";
import { esc, toast, openModal, dateTimeOf } from "../util.js";
import { startRecording } from "../wav-recorder.js";
import { looksSecret, SECRET_WARNING } from "../secret-check.js";

const TABS = [["chat", "Chat & Voice"], ["apps", "Applications & Accounts"], ["learn", "Learning"],
  ["knowledge", "Knowledge"], ["runs", "Executions"], ["audit", "Audit & Security"], ["config", "Agent Configuration"]];
const STATES = { discovered: "Discovered", ai_suggested: "AI Suggested", awaiting_approval: "Awaiting Approval", approved: "Approved", rejected: "Rejected", superseded: "Superseded", needs_revalidation: "Needs Revalidation" };
const ITEM_PILL = { confirmed_success: "PASS", confirmed_failure: "FAIL", skipped: "WARN", pending: "INFO", unknown: "WARN", running: "INFO" };
const ITEM_TEXT = { confirmed_success: "Confirmed success", confirmed_failure: "Confirmed failure", skipped: "Skipped", pending: "Pending", unknown: "Unknown / unverified", running: "Running", manual_required: "Manual intervention required" };

const state = { tab: "chat", app: null, apps: [], conversation: null, run: null, poll: null, view: null };
let alive = true;

const table = (heads, rows) => rows.length ? `<div class="table-wrap"><table class="data"><thead><tr>${heads.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>` : '<p class="muted">Nothing yet.</p>';
const pill = (cls, text) => `<span class="pill ${cls}">${esc(text)}</span>`;

export async function mount(view) {
  alive = true;
  state.view = view;
  try { state.tab = localStorage.getItem("ops.tab") || "chat"; } catch { /* storage blocked */ }
  view.innerHTML = `
    <div class="card" id="opsStatus" style="margin-bottom:12px"><p class="muted">Loading the agent…</p></div>
    <nav class="tabs">${TABS.map(([k, t]) => `<a href="#" class="tab" data-tab="${k}">${esc(t)}</a>`).join("")}</nav>
    <div id="opsBody"></div>`;
  view.querySelectorAll("[data-tab]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); show(a.dataset.tab); }));
  await loadApps();
  await show(state.tab);
  state.poll = setInterval(refreshRun, 2000);
}

export function unmount() { alive = false; clearInterval(state.poll); }

async function loadApps() {
  const { applications } = await api("/api/ops/applications");
  state.apps = applications;
  if (!state.app || !applications.some((a) => a.id === state.app)) state.app = applications[0]?.id || null;
}

function appPicker(id = "appPick") {
  return `<label>Application <select id="${id}">${state.apps.map((a) => `<option value="${esc(a.id)}"${a.id === state.app ? " selected" : ""}>${esc(a.name)}</option>`).join("") || "<option value=''>Register an application first</option>"}</select></label>`;
}
function bindPicker(root, id = "appPick", then = () => show(state.tab)) {
  root.querySelector(`#${id}`)?.addEventListener("change", (e) => { state.app = e.target.value; then(); });
}

async function show(tab) {
  state.tab = tab;
  try { localStorage.setItem("ops.tab", tab); } catch { /* ignore */ }
  state.view.querySelectorAll("[data-tab]").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  const body = state.view.querySelector("#opsBody");
  try {
    await ({ chat: chatTab, apps: appsTab, learn: learnTab, knowledge: knowledgeTab, runs: runsTab, audit: auditTab, config: configTab })[tab](body);
  } catch (err) { body.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`; }
  renderStatus();
}

// ---- status bar (always visible) ----------------------------------------------------------------------------------
async function refreshRun() {
  if (!alive || !state.run) return;
  try {
    const data = await api(`/api/ops/runs/${encodeURIComponent(state.run)}`);
    state.runData = data;
    renderStatus();
    if (state.tab === "runs" && state.view.querySelector("#runDetail")) renderRunDetail(state.view.querySelector("#runDetail"), data);
  } catch { /* the next poll tries again */ }
}

function renderStatus() {
  // 23.6.0: a short status line: application, state, progress and the pause / resume / stop controls. Details stay
  // on the Executions tab.
  const el = state.view?.querySelector("#opsStatus");
  if (!el) return;
  const d = state.runData;
  if (!d) { el.innerHTML = ""; el.hidden = true; return; }
  el.hidden = false;
  const s = d.status, c = s.counts || {};
  const manual = s.manual && !s.pause?.acknowledged;
  const active = !["completed", "partial", "failed", "stopped", "cancelled"].includes(s.state);
  const choices = (s.pause?.choices || []);
  el.innerHTML = `
    <div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
      <b>${esc(s.application)}</b>${pill(s.state === "paused" ? "WARN" : ["completed"].includes(s.state) ? "PASS" : ["failed", "stopped"].includes(s.state) ? "FAIL" : "INFO", s.state)}
      <progress max="100" value="${s.progress}" style="width:140px" aria-label="Progress"></progress><span>${s.progress}%</span>
      <span class="muted">Record ${esc(s.record)} · ✔ ${c.success || 0} ✖ ${c.failed || 0}</span>
      <span class="spacer"></span>
      ${["running", "inspecting"].includes(s.state) ? '<button class="btn" data-run-act="pause">Pause</button>' : ""}
      ${s.state === "paused" ? '<button class="btn primary" data-run-act="resume">Resume</button>' : ""}
      ${active ? '<button class="btn danger" data-run-act="stop">Stop</button>' : ""}
      <button class="link-btn" data-open-run>Details</button>
    </div>
    ${s.pause ? `<div class="card ${manual ? "ops-attention" : ""}" style="margin-top:8px"><b id="pauseMsg"></b>
      ${choices.length ? `<div class="row" style="margin-top:6px;flex-wrap:wrap;gap:6px">${choices.map((ch) => `<button class="btn" data-choice="${Number(ch.index)}">${esc(ch.label)}</button>`).join("")}</div>` : ""}</div>` : ""}`;
  const msg = el.querySelector("#pauseMsg");
  if (msg) msg.textContent = s.pause.message; // text only
  el.querySelectorAll("[data-run-act]").forEach((b) => b.addEventListener("click", () => runAction(b.dataset.runAct)));
  el.querySelectorAll("[data-choice]").forEach((b) => b.addEventListener("click", () => runAction("resume", { choice: Number(b.dataset.choice) })));
  el.querySelector("[data-open-run]")?.addEventListener("click", () => show("runs"));
}

async function runAction(act, body = {}) {
  try {
    if (act === "stop" && !confirm("Stop this execution? No further actions are taken; finished records stay as they are.")) return;
    await api(`/api/ops/runs/${encodeURIComponent(state.run)}/${act}`, { method: "POST", body });
    await refreshRun();
  } catch (err) { toast(err.message, "error"); }
}

// ---- chat & voice -------------------------------------------------------------------------------------------------
async function chatTab(body) {
  body.innerHTML = `
    <div class="card"><div class="row">${appPicker()}</div>
      <div id="chatLog" class="ops-chat" aria-live="polite"></div>
      <form id="chatForm" class="row" style="gap:6px;margin-top:8px" autocomplete="off">
        <textarea id="chatText" rows="2" style="flex:1" aria-label="Message"></textarea>
        <button class="btn" type="button" id="micBtn" aria-pressed="false" title="Speak (press again to stop)">🎙</button>
        <button class="btn primary" type="submit">Send</button>
      </form>
      <p class="muted" id="micState" aria-live="polite"></p>
    </div>`;
  bindPicker(body);
  const log = body.querySelector("#chatLog");
  if (state.conversation) {
    const { messages } = await api(`/api/ops/chat/${encodeURIComponent(state.conversation)}`);
    messages.forEach((m) => addMsg(log, m.speaker, m.text, m.data));
  } else addMsg(log, "agent", "Hello. I learn your applications and operate them through their normal screens. Say 'help' for examples.");
  const form = body.querySelector("#chatForm"), text = body.querySelector("#chatText");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const t = text.value.trim();
    if (!t) return;
    if (looksSecret(t)) { addMsg(log, "agent", SECRET_WARNING); text.value = ""; return; }
    text.value = "";
    addMsg(log, "user", t);
    try {
      const out = await api("/api/ops/chat", { method: "POST", body: { text: t, conversation: state.conversation, app_id: state.app, source: form.dataset.voice ? "voice" : "typed" } });
      delete form.dataset.voice;
      state.conversation = out.conversation;
      handleReply(log, out.reply);
    } catch (err) { addMsg(log, "agent", err.message); }
  });
  let rec = null;
  const mic = body.querySelector("#micBtn"), micState = body.querySelector("#micState");
  mic.addEventListener("click", async () => {
    if (rec) {
      mic.textContent = "🎙"; mic.setAttribute("aria-pressed", "false");
      micState.textContent = "Transcribing locally…";
      const wav = await rec.stop(); rec = null;
      try {
        const { text: heard } = await api("/api/ops/voice/transcribe", { method: "POST", rawBody: wav, contentType: "audio/wav" });
        text.value = heard; form.dataset.voice = "1"; text.focus();
        micState.textContent = "";
      } catch (err) { micState.textContent = err.message; }
      return;
    }
    try {
      rec = await startRecording({ onLevel: (l) => { micState.textContent = `● Recording… ${"▮".repeat(Math.round(l * 10))}`; } });
      mic.textContent = "■ Stop"; mic.setAttribute("aria-pressed", "true");
    } catch (err) { micState.textContent = `Microphone not available: ${err.message}`; }
  });
}

function addMsg(log, who, text, data = {}) {
  const div = document.createElement("div");
  div.className = `ops-msg ${who === "user" ? "me" : "agent"}`;
  div.textContent = text; // text only: chat content is never rendered as HTML
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function handleReply(log, reply) {
  const div = addMsg(log, "agent", reply.text);
  if (reply.type === "run" && reply.run_id) {
    state.run = reply.run_id;
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<button class="btn primary">Open the preview / start the browser</button>`;
    row.querySelector("button").addEventListener("click", async () => {
      try { if (reply.status === "inspecting") await api(`/api/ops/runs/${encodeURIComponent(reply.run_id)}/start`, { method: "POST" }); } catch (err) { toast(err.message, "error"); }
      show("runs");
    });
    div.appendChild(row);
    refreshRun();
  }
  if (reply.type === "secret_intercepted" || reply.open === "accounts") {
    const b = document.createElement("button"); b.className = "btn"; b.textContent = "Open the secure credential form";
    b.addEventListener("click", () => show("apps")); div.appendChild(b);
  }
  if (reply.type === "suggest_discovery") { const b = document.createElement("button"); b.className = "btn"; b.textContent = "Go to Learning"; b.addEventListener("click", () => show("learn")); div.appendChild(b); }
  if (reply.type === "suggest_guided") { const b = document.createElement("button"); b.className = "btn"; b.textContent = "Start guided learning"; b.addEventListener("click", () => { state.guidedTitle = reply.title; show("learn"); }); div.appendChild(b); }
}

// ---- applications & accounts --------------------------------------------------------------------------------------
async function appsTab(body) {
  const { accounts, vault, vault_available } = state.app ? await api(`/api/ops/accounts?app_id=${encodeURIComponent(state.app)}`) : { accounts: [], vault: "", vault_available: true };
  const app = state.apps.find((a) => a.id === state.app);
  body.innerHTML = `
    <div class="grid two">
    <form class="card settings" id="appForm" autocomplete="off"><h3>Register an application</h3>
      <label>Name <input name="name" required maxlength="80" placeholder="e.g. FleetOps Transport"></label>
      <label>Application URL <input name="base_url" required placeholder="https://fleet.example.local/"></label>
      <label>Environment <input name="environment" maxlength="60" placeholder="UAT / Production"></label>
      <label>Mode <select name="mode"><option value="testing">Testing (any account, incl. administrator)</option><option value="role_testing">Role-based testing (one account per role)</option><option value="production">Production (each user their own identity)</option></select></label>
      <label>Description <textarea name="description" rows="2" maxlength="2000"></textarea></label>
      <button class="btn primary">Register</button>
      <p class="muted">The agent only ever navigates inside the registered address (origin allowlist).</p></form>
    <div class="card"><div class="row">${appPicker()}<span class="spacer"></span>${app ? '<button class="btn danger" id="delApp">Delete application</button>' : ""}</div>
      ${app ? `<p class="muted">${esc(app.base_url)} · mode ${esc(app.mode)} · ${esc(app.environment || "")}</p>
      <details><summary>Sign-in, sign-out and identity knowledge</summary>
        <form id="profForm" class="settings" style="max-width:none">
          <label>Sign-in page URL <input name="url" value="${esc(app.profile?.login?.url || "")}"></label>
          <label>User field label <input name="username" value="${esc(app.profile?.login?.username || "")}" placeholder="User ID"></label>
          <label>Password field label <input name="password" value="${esc(app.profile?.login?.password || "")}" placeholder="Password"></label>
          <label>Sign-in button label <input name="submit" value="${esc(app.profile?.login?.submit || "")}" placeholder="Sign in"></label>
          <label><input type="checkbox" name="manual" ${app.profile?.login?.manual ? "checked" : ""}> Sign-in is always done by a person (SSO / smart card)</label>
          <label>Sign-out control label <input name="logout" value="${esc(app.profile?.logout?.control || "")}" placeholder="Logout"></label>
          <label>Identity pattern (regex with (?P&lt;user&gt;…) and (?P&lt;role&gt;…)) <input name="pattern" value="${esc(app.profile?.identity?.pattern || "")}" placeholder="Logged in as (?P<user>\\S+) \\((?P<role>[^)]+)\\)"></label>
          <button class="btn">Save sign-in knowledge</button></form></details>` : '<p class="muted">Register an application first.</p>'}
    </div></div>
    <div class="card" style="margin-top:12px"><h3>Accounts ${app ? "for " + esc(app.name) : ""}</h3>
      ${vault_available ? `<p class="muted">Passwords are kept in ${esc(vault)}; the database holds only a reference. Roles are verified from the application itself, never from this description.</p>` : '<p class="pill WARN">The Windows Credential Manager is not available, so credentials cannot be saved.</p>'}
      ${table(["Label", "Login ID", "Role (claimed)", "Verified role", "Status", "Credential", ""], accounts.map((a) => `<tr><td>${esc(a.label)}</td><td>${esc(a.login_id)}</td><td>${esc(a.role_key || a.role_description)}</td>
        <td>${esc(a.verified_role || "-")}</td><td>${esc(a.auth_status)}</td><td>${a.has_credential ? (a.credential_expired ? pill("WARN", "Expired") : pill("PASS", "Saved")) : a.revoked ? pill("FAIL", "Revoked") : pill("WARN", "Not set")}</td>
        <td><button class="link-btn" data-cred="${esc(a.id)}">Add / replace credential</button> · <button class="link-btn" data-revoke="${esc(a.id)}">Revoke</button> · <button class="link-btn" data-delacc="${esc(a.id)}">Delete</button></td></tr>`))}
      ${app ? `<form id="accForm" class="row" style="gap:6px;flex-wrap:wrap;margin-top:8px" autocomplete="off">
        <input name="label" required placeholder="Label, e.g. Entry Operator (test)"><input name="login_id" required placeholder="Login ID">
        <input name="role_key" placeholder="Role, e.g. Entry Operator"><input name="environment" placeholder="Environment">
        <button class="btn">Add account</button></form>` : ""}
    </div>`;
  bindPicker(body);
  body.querySelector("#appForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try { const { application } = await api("/api/ops/applications", { method: "POST", body: f }); state.app = application.id; await loadApps(); show("apps"); toast("Application registered", "success"); }
    catch (err) { toast(err.message, "error"); }
  });
  body.querySelector("#delApp")?.addEventListener("click", async () => {
    if (!confirm(`Delete ${app.name}, its accounts (credentials are removed from the vault) and all its knowledge?`)) return;
    try { await api(`/api/ops/applications/${encodeURIComponent(app.id)}`, { method: "DELETE" }); state.app = null; await loadApps(); show("apps"); } catch (err) { toast(err.message, "error"); }
  });
  body.querySelector("#profForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target.elements;
    const profile = { ...(app.profile || {}), login: { url: f.url.value, username: f.username.value, password: f.password.value, submit: f.submit.value, manual: f.manual.checked },
      logout: { ...(app.profile?.logout || {}), control: f.logout.value }, identity: { ...(app.profile?.identity || {}), pattern: f.pattern.value } };
    try { await api(`/api/ops/applications/${encodeURIComponent(app.id)}/profile`, { method: "PUT", body: { profile } }); await loadApps(); toast("Saved", "success"); } catch (err) { toast(err.message, "error"); }
  });
  body.querySelector("#accForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try { await api("/api/ops/accounts", { method: "POST", body: { ...f, application_id: state.app } }); show("apps"); } catch (err) { toast(err.message, "error"); }
  });
  body.querySelectorAll("[data-cred]").forEach((b) => b.addEventListener("click", () => credentialDialog(b.dataset.cred)));
  body.querySelectorAll("[data-revoke]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Revoke this credential? It is deleted from the vault.")) return;
    try { await api(`/api/ops/accounts/${encodeURIComponent(b.dataset.revoke)}/revoke`, { method: "POST" }); show("apps"); } catch (err) { toast(err.message, "error"); }
  }));
  body.querySelectorAll("[data-delacc]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Delete this account and its saved credential?")) return;
    try { await api(`/api/ops/accounts/${encodeURIComponent(b.dataset.delacc)}`, { method: "DELETE" }); show("apps"); } catch (err) { toast(err.message, "error"); }
  }));
}

function credentialDialog(accId) {
  const { el, close } = openModal("Secure credential", `<form id="credForm" class="settings" style="max-width:none" autocomplete="off">
    <label>Password <input type="password" name="password" required autocomplete="new-password" maxlength="512"></label>
    <label>Expires (optional) <input type="date" name="expires"></label>
    <label><input type="checkbox" name="consent" required> I agree that this password is stored encrypted in the Windows credential vault for this account and used only to sign in to this application.</label>
    <p class="muted">The password goes directly to the vault. It is never shown again, never sent to the AI model, and never written to chat, logs or audit.</p>
    <div class="row"><button class="btn primary">Save to vault</button><button class="btn" type="button" data-close>Cancel</button></div></form>`);
  el.querySelector("#credForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target.elements;
    try {
      await api(`/api/ops/accounts/${encodeURIComponent(accId)}/credential`, { method: "PUT", body: { password: f.password.value, consent: f.consent.checked, expires_at: f.expires.value ? `${f.expires.value}T23:59:59` : null } });
      f.password.value = ""; close(); toast("Credential saved in the vault", "success"); show("apps");
    } catch (err) { toast(err.message, "error"); }
  });
}

// ---- learning -----------------------------------------------------------------------------------------------------
async function learnTab(body) {
  const accounts = state.app ? (await api(`/api/ops/accounts?app_id=${encodeURIComponent(state.app)}`)).accounts : [];
  const { sessions } = state.app ? await api(`/api/ops/learning?app_id=${encodeURIComponent(state.app)}`) : { sessions: [] };
  const accOpts = `<option value="">No sign-in (public pages only)</option>` + accounts.map((a) => `<option value="${esc(a.id)}">${esc(a.label)}</option>`).join("");
  body.innerHTML = `
    <div class="row">${appPicker()}</div>
    <div class="grid two" style="margin-top:8px">
      <form class="card settings" id="discForm"><h3>Automatic discovery (read-only)</h3>
        <p class="muted">Signs in normally (pauses for MFA / one-time codes), then only opens links inside the application and inspects each screen: menus, forms, fields, dropdowns, buttons, tables, pagination and messages. It never clicks buttons or submits forms.</p>
        <label>Account <select name="account_id">${accOpts}</select></label>
        <label>Maximum screens <input type="number" name="max_pages" value="25" min="1" max="200"></label>
        <button class="btn primary">Start discovery</button></form>
      <form class="card settings" id="guidedForm"><h3>Guided learning (demonstration)</h3>
        <p class="muted">A browser opens on the application. Perform the workflow yourself; the agent watches your clicks, entries and choices (passwords and codes arrive only as "[secret]") and turns them into a workflow for you to review and approve. Whatever you submit during the demonstration is real: use a training record or test data.</p>
        <label>Workflow name <input name="title" required maxlength="80" value="${esc(state.guidedTitle || "")}" placeholder="Create trip"></label>
        <label>Demonstrating account (its role is suggested for the workflow) <select name="account_id">${accOpts}</select></label>
        <button class="btn primary">Start guided learning</button></form>
    </div>
    <div class="card" style="margin-top:12px"><h3>Learning sessions</h3>
      ${table(["Started", "Workflow", "Events", "Status", ""], sessions.map((s) => `<tr><td>${esc(dateTimeOf(s.created_at))}</td><td>${esc(s.title || "")}</td><td>${s.events}</td><td>${esc(s.status)}</td>
        <td>${s.status === "recording" ? `<button class="btn primary" data-finish="${esc(s.id)}">Finish & build workflow</button> <button class="btn" data-cancel="${esc(s.id)}">Cancel</button>` : ""}</td></tr>`))}
    </div>`;
  bindPicker(body);
  body.querySelector("#discForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try {
      const out = await api("/api/ops/discovery", { method: "POST", body: { application_id: state.app, account_id: f.account_id || null, max_pages: Number(f.max_pages) } });
      state.run = out.run.id; toast("Discovery started in a SkyGuard browser", "success"); refreshRun();
    } catch (err) { toast(err.message, "error"); }
  });
  body.querySelector("#guidedForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try { await api("/api/ops/learning", { method: "POST", body: { application_id: state.app, account_id: f.account_id || null, title: f.title } }); toast("A browser opened: perform the workflow, then press Finish.", "success"); show("learn"); }
    catch (err) { toast(err.message, "error"); }
  });
  body.querySelectorAll("[data-finish]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try { const out = await api(`/api/ops/learning/${encodeURIComponent(b.dataset.finish)}/finish`, { method: "POST" }); toast(`Workflow '${out.workflow.key}' built: review and approve it in Knowledge.`, "success"); show("knowledge"); }
    catch (err) { toast(err.message, "error"); b.disabled = false; }
  }));
  body.querySelectorAll("[data-cancel]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/ops/learning/${encodeURIComponent(b.dataset.cancel)}/cancel`, { method: "POST" }); show("learn"); } catch (err) { toast(err.message, "error"); }
  }));
}

// ---- knowledge ----------------------------------------------------------------------------------------------------
async function knowledgeTab(body) {
  if (!state.app) { body.innerHTML = '<p class="muted">Register an application first.</p>'; return; }
  const kind = state.kKind || "workflow";
  const { items } = await api(`/api/ops/knowledge?app_id=${encodeURIComponent(state.app)}&kind=${kind}`);
  const { backups } = await api("/api/ops/knowledge-backups");
  body.innerHTML = `
    <div class="row" style="gap:8px;flex-wrap:wrap">${appPicker()}
      <label>Kind <select id="kKind">${["workflow", "rule", "screen", "relationship", "role"].map((k) => `<option${k === kind ? " selected" : ""}>${k}</option>`).join("")}</select></label>
      <span class="spacer"></span><button class="btn" id="kNew">New ${esc(kind)}</button><button class="btn" id="kExport">Export</button><button class="btn" id="kBackup">Back up</button></div>
    <div class="card" style="margin-top:8px">
      ${table(["Key", "Version", "State", "Source", "Confidence", "Approver", "Updated", ""], items.map((e) => `<tr><td>${esc(e.key)}</td><td>${e.version}</td>
        <td>${pill(e.state === "approved" ? "PASS" : e.state === "rejected" ? "FAIL" : "WARN", STATES[e.state] || e.state)}</td><td>${esc(e.source)}</td><td>${Math.round(e.confidence * 100)}%</td>
        <td>${esc(e.approver || "")}</td><td>${esc(dateTimeOf(e.updated_at))}</td>
        <td><button class="link-btn" data-open="${e.id}">Review</button>${e.state !== "approved" && e.state !== "rejected" && e.state !== "superseded" ? ` · <button class="link-btn" data-approve="${e.id}">Approve</button> · <button class="link-btn" data-reject="${e.id}">Reject</button>` : ""}</td></tr>`))}
    </div>
    <div class="card" style="margin-top:8px"><h3>Backups</h3>${table(["File", "Size", ""], backups.map((b) => `<tr><td>${esc(b.name)}</td><td>${Math.round(b.size / 1024)} KB</td><td><button class="link-btn" data-restore="${esc(b.name)}">Restore (admin)</button></td></tr>`))}</div>`;
  bindPicker(body);
  body.querySelector("#kKind").addEventListener("change", (e) => { state.kKind = e.target.value; show("knowledge"); });
  body.querySelectorAll("[data-approve]").forEach((b) => b.addEventListener("click", () => decide(b.dataset.approve, "approve")));
  body.querySelectorAll("[data-reject]").forEach((b) => b.addEventListener("click", () => decide(b.dataset.reject, "reject")));
  body.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => reviewEntry(b.dataset.open)));
  body.querySelectorAll("[data-restore]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`Restore knowledge from ${b.dataset.restore}? The current knowledge is backed up first.`)) return;
    try { await api("/api/ops/admin/knowledge-restore", { method: "POST", body: { name: b.dataset.restore } }); toast("Restored", "success"); show("knowledge"); } catch (err) { toast(err.message, "error"); }
  }));
  body.querySelector("#kBackup").addEventListener("click", async () => { try { await api("/api/ops/knowledge-backup", { method: "POST" }); show("knowledge"); } catch (err) { toast(err.message, "error"); } });
  body.querySelector("#kExport").addEventListener("click", async () => {
    const { export: data } = await api(`/api/ops/knowledge-export?app_id=${encodeURIComponent(state.app)}`);
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 1)], { type: "application/json" }));
    a.download = "skyguard-agent-knowledge.json"; a.click();
  });
  body.querySelector("#kNew").addEventListener("click", () => editEntry(null, kind, ""));
}

async function decide(id, what) {
  const note = what === "reject" ? prompt("Why is it rejected? (optional)") : null;
  try { await api(`/api/ops/knowledge/${id}/${what}`, { method: "POST", body: { note } }); show("knowledge"); } catch (err) { toast(err.message, "error"); }
}

async function reviewEntry(id) {
  const { entry, history } = await api(`/api/ops/knowledge/${id}`);
  const { el, close } = openModal(`${entry.kind}: ${entry.key}`, `
    <p class="muted">Version ${entry.version} · ${esc(STATES[entry.state] || entry.state)} · source ${esc(entry.source)} · confidence ${Math.round(entry.confidence * 100)}% · app version ${esc(entry.app_version || "-")} · created ${esc(dateTimeOf(entry.created_at))} by ${esc(entry.created_by || "-")}${entry.approver ? ` · approved by ${esc(entry.approver)}` : ""}</p>
    ${entry.note ? `<p>${esc(entry.note)}</p>` : ""}
    <label>Content (edit to correct; saving creates a new version awaiting approval)<textarea id="kData" rows="18" style="width:100%;font-family:monospace">${esc(JSON.stringify(entry.data, null, 2))}</textarea></label>
    <div class="row"><button class="btn primary" id="kSave">Save correction</button><span class="spacer"></span>
      <label>Roll back to <select id="kVer">${history.map((h) => `<option value="${h.version}">v${h.version} (${esc(STATES[h.state] || h.state)})</option>`).join("")}</select></label><button class="btn" id="kRoll">Roll back</button></div>`, { wide: true });
  el.querySelector("#kSave").addEventListener("click", async () => {
    let data; try { data = JSON.parse(el.querySelector("#kData").value); } catch { toast("The content is not valid JSON.", "error"); return; }
    try { await api(`/api/ops/knowledge/${id}/correct`, { method: "POST", body: { data } }); close(); show("knowledge"); } catch (err) { toast(err.message, "error"); }
  });
  el.querySelector("#kRoll").addEventListener("click", async () => {
    try { await api("/api/ops/knowledge-rollback", { method: "POST", body: { application_id: entry.application_id, kind: entry.kind, key: entry.key, version: Number(el.querySelector("#kVer").value) } }); close(); show("knowledge"); } catch (err) { toast(err.message, "error"); }
  });
}

function editEntry(_id, kind) {
  const sample = { rule: { type: "unique", field: "trip_no", applies_to: "Create trip" },
    relationship: { from: "Trip management: trip", to: "Driver management: driver", type: "assigns", via: "driver" },
    role: { name: "manager", screens: {} }, screen: { url_path: "/", fields: [], buttons: [], tables: [] },
    workflow: { title: "", operation: "create", entity: "", commits: true, roles: [], parameters: [], steps: [], verify: {} } }[kind] || {};
  const { el, close } = openModal(`New ${kind}`, `<label>Key <input id="nKey" maxlength="160"></label>
    <label>Content<textarea id="nData" rows="14" style="width:100%;font-family:monospace">${esc(JSON.stringify(sample, null, 2))}</textarea></label>
    <button class="btn primary" id="nSave">Save (awaiting approval)</button>`, { wide: true });
  el.querySelector("#nSave").addEventListener("click", async () => {
    let data; try { data = JSON.parse(el.querySelector("#nData").value); } catch { toast("The content is not valid JSON.", "error"); return; }
    try { await api("/api/ops/knowledge", { method: "POST", body: { application_id: state.app, kind, key: el.querySelector("#nKey").value, data } }); close(); show("knowledge"); } catch (err) { toast(err.message, "error"); }
  });
}

// ---- executions ---------------------------------------------------------------------------------------------------
async function runsTab(body) {
  const { runs } = await api(`/api/ops/runs${state.app ? `?app_id=${encodeURIComponent(state.app)}` : ""}`);
  body.innerHTML = `<div class="row">${appPicker()}</div>
    <div class="card" style="margin-top:8px" id="runDetail">${state.run ? "<p class='muted'>Loading…</p>" : "<p class='muted'>Choose an execution below, or ask in Chat.</p>"}</div>
    <div class="card" style="margin-top:8px"><h3>Execution history</h3>
    ${table(["Execution", "Status", "Result", "By", "Started", ""], runs.map((r) => `<tr><td>${esc(r.id)}</td><td>${esc(r.status)}</td><td>${esc(r.summary?.message || "")}</td><td>${esc(r.created_by || "")}</td><td>${esc(dateTimeOf(r.created_at))}</td><td><button class="link-btn" data-run="${esc(r.id)}">Open</button></td></tr>`))}</div>`;
  bindPicker(body);
  body.querySelectorAll("[data-run]").forEach((b) => b.addEventListener("click", () => { state.run = b.dataset.run; refreshRun(); }));
  if (state.run) refreshRun();
}

async function renderRunDetail(el, data) {
  const r = data.run, p = r.preview || {};
  const evts = (await api(`/api/ops/runs/${encodeURIComponent(r.id)}/events`)).events;
  const canStart = ["inspecting", "running", "paused"].includes(r.status) && !data.browser?.open;
  el.innerHTML = `
    <div class="row"><h3 style="margin:0">${esc(r.id)}</h3><span class="spacer"></span>${canStart ? '<button class="btn" id="openBrowser">Open browser and continue</button>' : ""}
      ${["running", "inspecting", "paused"].includes(r.status) ? '<button class="btn" id="cancelRest">Cancel remaining</button>' : ""}</div>
    ${r.status === "awaiting_confirmation" ? `<div class="card ops-attention" style="margin-top:8px"><h3>Preview: confirm to execute</h3>
      <p><b>${esc(p.application)}</b> ${esc(p.environment || "")} · mode ${esc(p.mode || "")}</p>
      ${table(["Stage", "Account", "Verified role", "Page", "Expected status"], (p.stages || []).map((s) => `<tr><td>${esc(s.title)} (v${s.workflow_version})</td><td>${esc(s.account)} (${esc(s.login_id)})</td><td>${esc(s.role_verified || "verified at sign-in")}</td><td>${esc(s.page || "")}</td><td>${esc(s.expected_status || "")}</td></tr>`))}
      ${table(["#", "Values", "Validation", "Status"], (p.records || []).map((x) => `<tr><td>${x.seq}</td><td>${Object.entries(x.values).map(([k, v]) => `${esc(k)}: <b>${esc(v)}</b>${x.auto_assigned.includes(k) ? " (auto)" : ""}`).join("<br>")}</td><td>${x.findings.map((f) => `${f.severity === "error" ? "✖" : "⚠"} ${esc(f.message)}`).join("<br>") || "✔"}</td><td>${pill(x.status === "blocked" ? "FAIL" : x.status === "warning" ? "WARN" : "PASS", x.status)}</td></tr>`))}
      <p>Business rules: ${(p.rules || []).map((x) => `${esc(x.name)}${x.enforced ? "" : " (not yet approved: warning only)"}`).join(", ") || "none"}</p>
      ${(p.warnings || []).map((w) => `<p class="pill WARN">${esc(w)}</p>`).join("")}
      <p><b>${p.totals?.operations || 0} operation(s)</b> on ${p.totals?.ready || 0} record(s) will be performed; ${p.totals?.blocked || 0} blocked record(s) are not executed.</p>
      <div class="row"><button class="btn primary" id="confirmBtn">Confirm and execute exactly this</button><button class="btn" id="rejectBtn">Do not run</button></div></div>` : ""}
    ${table(["#", "Record", "Status", "Reason / evidence"], data.items.map((i) => `<tr><td>${i.seq}</td><td>${esc(Object.entries(i.record).filter(([k]) => !k.startsWith("_")).map(([k, v]) => `${k}=${v}`).join(", "))}</td><td>${pill(ITEM_PILL[i.status] || "INFO", ITEM_TEXT[i.status] || i.status)}</td><td>${esc(i.result?.reason || "")}${i.result?.evidence ? `<details><summary>evidence</summary><pre>${esc(JSON.stringify(i.result.evidence, null, 1))}</pre></details>` : ""}</td></tr>`))}
    ${r.summary?.message ? `<p><b>${esc(r.summary.message)}</b></p>` : ""}
    <details open><summary>Activity (${evts.length})</summary><div class="ops-events">${evts.slice(-80).map((e) => `<div><span class="muted">${esc(dateTimeOf(e.at))}</span> [${esc(e.kind)}] ${esc(e.message)}</div>`).join("")}</div></details>`;
  el.querySelector("#confirmBtn")?.addEventListener("click", async () => {
    try { await api(`/api/ops/runs/${encodeURIComponent(r.id)}/confirm`, { method: "POST", body: { preview_hash: r.preview_hash } }); refreshRun(); } catch (err) { toast(err.message, "error"); refreshRun(); }
  });
  el.querySelector("#rejectBtn")?.addEventListener("click", async () => { try { await api(`/api/ops/runs/${encodeURIComponent(r.id)}/stop`, { method: "POST" }); refreshRun(); } catch (err) { toast(err.message, "error"); } });
  el.querySelector("#cancelRest")?.addEventListener("click", () => runAction("cancel-remaining"));
  el.querySelector("#openBrowser")?.addEventListener("click", async () => { try { await api(`/api/ops/runs/${encodeURIComponent(r.id)}/start`, { method: "POST" }); refreshRun(); } catch (err) { toast(err.message, "error"); } });
}

// ---- audit & config ---------------------------------------------------------------------------------------------
async function auditTab(body) {
  const [{ audit }, chain] = await Promise.all([api("/api/ops/audit?limit=300"), api("/api/ops/audit/verify")]);
  body.innerHTML = `<div class="card"><p>${chain.valid ? pill("PASS", `Audit chain intact (${chain.checked} records)`) : pill("FAIL", `Audit chain broken at record ${chain.broken_at}`)}</p>
    <p class="muted">Audit records are hash-chained and cannot be edited or deleted. They never contain passwords, codes or session tokens.</p>
    ${table(["Time", "Actor", "Interface", "Action", "Target", "Details"], audit.map((a) => `<tr><td>${esc(dateTimeOf(a.at))}</td><td>${esc(a.actor || "")}</td><td>${esc(a.interface || "")}</td><td>${esc(a.action)}</td><td>${esc(a.target || "")}</td><td><code>${esc(JSON.stringify(a.details)).slice(0, 300)}</code></td></tr>`))}</div>`;
}

async function configTab(body) {
  const info = await api("/api/ops/info");
  const c = info.config;
  body.innerHTML = `<div class="grid two"><div class="card"><h3>Agent</h3>
    <p>${esc(info.agent)} ${esc(info.version)}</p><p class="muted">Knowledge database: ${esc(info.knowledge_db)} (schema ${info.schema})<br>Credential vault: ${esc(info.vault)}<br>Language model: ${esc(info.llm)} (learning is never stored in the model; it can be replaced at any time)</p></div>
    <form class="card settings" id="cfgForm"><h3>Configuration (admin)</h3>
      <label>Browser <select name="browser"><option value="">As in Preferences (Browser Manager)</option>${["chromium", "msedge", "chrome"].map((b) => `<option${c.browser === b ? " selected" : ""}>${b}</option>`).join("")}</select></label>
      <label><input type="checkbox" name="headless" ${c.headless ? "checked" : ""}> Hidden browser (no window; manual intervention then needs a visible browser)</label>
      <label><input type="checkbox" name="use_ai" ${c.use_ai ? "checked" : ""}> Let the local model suggest business rules after guided learning (always review before approval)</label>
      <label>Discovery screens limit <input type="number" name="max_discovery_pages" value="${c.max_discovery_pages}" min="1" max="200"></label>
      <button class="btn primary">Save</button></form></div>`;
  body.querySelector("#cfgForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target.elements;
    try { await api("/api/ops/config", { method: "PUT", body: { browser: f.browser.value || null, headless: f.headless.checked, use_ai: f.use_ai.checked, max_discovery_pages: Number(f.max_discovery_pages.value) } }); toast("Saved", "success"); }
    catch (err) { toast(err.message, "error"); }
  });
}
