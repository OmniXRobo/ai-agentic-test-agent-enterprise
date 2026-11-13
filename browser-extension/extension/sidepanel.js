// Side panel: chat and voice, executions (preview, confirm, pause, resume, stop), applications and accounts,
// learning, knowledge approval and audit. Everything goes to the local companion; nothing to the internet.
import * as api from "./lib/api.js";
import { looksSecret, SECRET_WARNING } from "./lib/secret-check.js";
import { startRecording } from "./lib/wav-recorder.js";

const $ = (sel, root = document) => root.querySelector(sel);
const main = $("#main");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const S = { tab: "chat", apps: [], appId: "", conversation: null, runId: null, user: null, poll: null, messages: [] };
const STATUS_TEXT = { confirmed_success: "Confirmed success", confirmed_failure: "Confirmed failure", pending: "Pending", skipped: "Skipped", unknown: "Unknown (check in the application)", running: "Running", manual_required: "Manual action required" };

function originOf(url) { try { const u = new URL(url); return `${u.protocol}//${u.host}`; } catch (e) { return ""; } }
function toast(el, text) { if (el) el.textContent = text || ""; }
const send = (msg) => chrome.runtime.sendMessage(msg);

// ---- connection --------------------------------------------------------------------------------------------------
async function connect() {
  const pill = $("#conn");
  try {
    await api.hello();
  } catch (e) {
    pill.textContent = "companion offline"; pill.className = "pill fail";
    main.innerHTML = `<div class="card"><h2>Companion not running</h2><p>${esc(e.message)}</p><button class="btn" id="retry">Try again</button></div>`;
    $("#retry").onclick = connect;
    return false;
  }
  const s = await api.settings();
  S.user = await api.currentUser();
  if (!s.device_id || !S.user) {
    pill.textContent = s.device_id ? "signed out" : "not paired"; pill.className = "pill warn";
    main.innerHTML = `<div class="card"><h2>${s.device_id ? "Sign in" : "Pair with the companion"}</h2>
      <p>${s.device_id ? "Sign in to the companion to use the agent." : "Pair this browser with the SkyGuard companion on this PC first."}</p>
      <button class="btn primary" id="opt">Open Options</button></div>`;
    $("#opt").onclick = () => chrome.runtime.openOptionsPage();
    return false;
  }
  pill.textContent = `${S.user.username} (${S.user.role})`; pill.className = "pill ok";
  return true;
}

async function loadApps() {
  S.apps = (await api.get("/v1/applications")).applications;
  if (!S.apps.some((a) => a.id === S.appId)) S.appId = S.apps[0]?.id || "";
}

function appSelect(id = "appSel") {
  return `<select id="${id}">${S.apps.length ? S.apps.map((a) => `<option value="${esc(a.id)}"${a.id === S.appId ? " selected" : ""}>${esc(a.name)}</option>`).join("") : "<option value=''>Register an application first</option>"}</select>`;
}

// ---- tabs --------------------------------------------------------------------------------------------------------
document.querySelectorAll("#tabs button").forEach((b) => b.onclick = () => show(b.dataset.tab));

async function show(tab) {
  S.tab = tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  clearInterval(S.poll);
  if (!(await connect())) return;
  try {
    await loadApps();
    await ({ chat, run, apps, learn, knowledge, audit }[tab])();
  } catch (e) {
    main.innerHTML = `<div class="card"><p class="err">${esc(e.message)}</p></div>`;
  }
}

// ---- chat and voice ----------------------------------------------------------------------------------------------
async function chat() {
  main.innerHTML = `<div class="card"><div class="row"><label style="flex:1">Application ${appSelect()}</label></div>
    <div class="chat" id="chatLog"></div>
    <label>Request<textarea id="say" placeholder="e.g. create 3 trips on 2026-12-01 from 06:00 every 30 minutes, route: Dubai to Abu Dhabi, bus: BUS-101, driver: available, trip numbers starting at DXB200 as Entry Operator (test)"></textarea></label>
    <div class="row"><button class="btn primary" id="sendBtn">Send</button><button class="btn" id="micBtn">Speak</button>
    <span class="muted">Never type passwords or codes here.</span></div><p class="err" id="chatErr"></p></div>`;
  $("#appSel").onchange = (e) => { S.appId = e.target.value; };
  renderChat();
  $("#sendBtn").onclick = sendChat;
  $("#say").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) sendChat(); });
  $("#micBtn").onclick = voice;
}

function renderChat() {
  const log = $("#chatLog");
  if (!log) return;
  log.innerHTML = (S.messages.length ? S.messages : [{ who: "agent", text: "Hello. I operate your registered applications through their normal screens. Every operation shows a preview first and runs only after you confirm. Say 'help' for examples." }])
    .map((m) => `<div class="msg ${m.who}${m.warn ? " warn" : ""}">${esc(m.text)}${m.actions || ""}</div>`).join("");
  log.scrollTop = log.scrollHeight;
  log.querySelectorAll("[data-act]").forEach((b) => b.onclick = () => chatAction(b.dataset.act, b.dataset));
}

async function sendChat() {
  const box = $("#say");
  const text = box.value.trim();
  if (!text) return;
  if (looksSecret(text)) { // first line of defence: never leaves the page; the companion checks again
    S.messages.push({ who: "agent", warn: true, text: SECRET_WARNING });
    box.value = "";
    renderChat();
    return;
  }
  S.messages.push({ who: "me", text });
  box.value = "";
  renderChat();
  try {
    const out = await api.post("/v1/chat", { text, conversation: S.conversation || undefined, app_id: S.appId || undefined, source: S.voiceSource ? "voice" : "typed" });
    S.voiceSource = false;
    S.conversation = out.conversation;
    const r = out.reply;
    let actions = "";
    if (r.type === "secret_intercepted") actions = `<div class="row" style="margin-top:6px"><button class="btn" data-act="apps">Open Applications &amp; Accounts</button></div>`;
    if (r.type === "suggest_discovery") actions = `<div class="row" style="margin-top:6px"><button class="btn" data-act="learn">Open Learn</button></div>`;
    S.messages.push({ who: "agent", text: r.text, warn: r.type === "secret_intercepted" || r.type === "error", actions });
    if (r.type === "run" && r.run_id) { S.runId = r.run_id; await send({ type: "drive", runId: r.run_id }); setTimeout(() => show("run"), 600); }
  } catch (e) {
    S.messages.push({ who: "agent", warn: true, text: e.message });
  }
  renderChat();
}

function chatAction(act) { show(act); }

let rec = null;
async function voice() {
  const btn = $("#micBtn");
  if (rec) { btn.disabled = true; btn.textContent = "Transcribing..."; const wav = await rec.stop(); rec = null;
    try { const { text } = await api.transcribe(wav); $("#say").value = text; S.voiceSource = true; $("#say").focus(); toast($("#chatErr"), "Check the text, then press Send. Nothing is run until you confirm the preview."); }
    catch (e) { toast($("#chatErr"), e.message); }
    btn.disabled = false; btn.textContent = "Speak"; return; }
  try {
    rec = await startRecording({ maxMs: 30000 });
    btn.textContent = "Stop";
    rec.done.then(() => { if (rec) btn.click(); });
  } catch (e) {
    toast($("#chatErr"), "Microphone not available. Allow it once on the microphone page.");
    chrome.tabs.create({ url: chrome.runtime.getURL("mic.html") });
  }
}

// ---- execution ---------------------------------------------------------------------------------------------------
async function run() {
  const { runs } = await api.get("/v1/runs?limit=20");
  if (!S.runId && runs.length) S.runId = runs[0].id;
  main.innerHTML = `<div class="card"><label>Execution <select id="runSel">${runs.map((r) => `<option value="${esc(r.id)}"${r.id === S.runId ? " selected" : ""}>${esc(r.id)} - ${esc(r.status)}</option>`).join("") || "<option value=''>No executions yet</option>"}</select></label>
    <div id="runBody"></div><p class="err" id="runErr"></p></div>`;
  $("#runSel").onchange = (e) => { S.runId = e.target.value; renderRun(); };
  await renderRun();
  S.poll = setInterval(() => renderRun(true), 1500);
}

async function renderRun(quiet) {
  const body = $("#runBody");
  if (!body || !S.runId) return;
  let v, ev;
  try { [v, ev] = await Promise.all([api.get(`/v1/runs/${S.runId}`), api.get(`/v1/runs/${S.runId}/events`)]); }
  catch (e) { if (!quiet) toast($("#runErr"), e.message); return; }
  const r = v.run, st = v.status, p = r.preview || {};
  // re-render only when something changed, so buttons stay put while the person reaches for them
  const sig = JSON.stringify([r.id, r.status, r.updated_at, v.items.map((i) => i.status), ev.events.length, st.pause]);
  if (quiet && sig === S.runSig) return;
  S.runSig = sig;
  const pause = st.pause;
  const final = ["completed", "partial", "failed", "stopped", "cancelled"].includes(r.status);
  statusBar(st, r);
  const preview = r.status === "awaiting_confirmation" && p.records ? `<h2>Preview (nothing has been submitted)</h2>
    <p>${esc(p.totals.ready)} of ${esc(p.totals.records)} record(s) ready, ${esc(p.totals.blocked)} blocked. Account(s): ${esc(v.plan.stages.map((s) => `${s.title}: ${s.account}`).join("; "))}.</p>
    <table><tr><th>#</th><th>Values</th><th>Checks</th></tr>${p.records.map((x) => `<tr><td>${x.seq}</td><td>${esc(Object.entries(x.values || {}).map(([k, val]) => `${k}: ${val}`).join(", "))}</td><td>${esc(x.status)} ${esc((x.findings || []).map((f) => f.message).join("; "))}</td></tr>`).join("")}</table>
    <div class="row" style="margin-top:8px"><button class="btn primary" id="confirmBtn"${p.totals.ready ? "" : " disabled"}>Confirm and run ${esc(p.totals.ready)} record(s)</button><button class="btn danger" id="cancelBtn">Cancel</button></div>` : "";
  const controls = final ? "" : `<div class="row">${r.status === "paused" ? `<button class="btn primary" id="resumeBtn">Resume</button>` : `<button class="btn" id="pauseBtn">Pause</button>`}
    <button class="btn danger" id="stopBtn">Stop</button><button class="btn" id="cancelRestBtn">Cancel remaining</button>${pause ? `<button class="btn" id="ackBtn">Acknowledge</button>` : ""}</div>`;
  body.innerHTML = `${pause ? `<div class="card" style="border-color:var(--warn)"><strong>Paused:</strong> ${esc(pause.message)}<br><span class="muted">The browser stays open. Complete the step yourself, then press Resume; I will verify and continue.</span></div>` : ""}
    ${preview}${controls}
    ${final ? `<p><strong>${esc(r.status)}</strong>: ${esc((r.summary || {}).message || "")}</p>` : ""}
    <h2>Records</h2><table><tr><th>#</th><th>Result</th><th>Reason</th></tr>${v.items.map((i) => `<tr><td>${i.seq}</td><td>${esc(STATUS_TEXT[i.status] || i.status)}</td><td>${esc((i.result || {}).reason || "")}</td></tr>`).join("")}</table>
    <h2>Activity</h2><div class="list">${ev.events.slice(-40).reverse().map((e) => `<div><span class="muted">${esc(e.at.slice(11, 19))}</span> ${esc(e.message)}</div>`).join("")}</div>`;
  const on = (id, fn) => { const b = $(id); if (b) b.onclick = async () => { try { await fn(); await renderRun(); } catch (e) { toast($("#runErr"), e.message); } }; };
  on("#confirmBtn", async () => { await api.post(`/v1/runs/${r.id}/confirm`, { preview_hash: r.preview_hash }); await send({ type: "drive", runId: r.id }); });
  on("#cancelBtn", () => api.post(`/v1/runs/${r.id}/stop`));
  on("#pauseBtn", () => api.post(`/v1/runs/${r.id}/pause`));
  on("#resumeBtn", async () => { const { events } = await send({ type: "manual-events", runId: r.id }); await api.post(`/v1/runs/${r.id}/resume`, { manual_events: events || [] }); await send({ type: "drive", runId: r.id }); });
  on("#stopBtn", async () => { await api.post(`/v1/runs/${r.id}/stop`); await send({ type: "stop-drive", runId: r.id }); });
  on("#cancelRestBtn", () => api.post(`/v1/runs/${r.id}/cancel-remaining`));
  on("#ackBtn", async () => { await api.post(`/v1/runs/${r.id}/acknowledge`); await send({ type: "acknowledge" }); });
}

function statusBar(st, r) {
  const bar = $("#status");
  if (!st || ["completed", "partial", "failed", "stopped", "cancelled"].includes(r.status)) { bar.className = "status"; return; }
  bar.className = "status on" + (r.status === "paused" || r.status === "awaiting_confirmation" ? " attention" : "");
  bar.innerHTML = `<strong>${esc(st.application)}</strong> - ${esc(r.status)} - ${esc(st.stage)} - record ${esc(st.record)} - ${esc(st.progress)}%<br>
    Account: ${esc(st.account || "-")} ${st.identity_verified ? "(identity verified" + (st.verified_role ? ", role " + esc(st.verified_role) : "") + ")" : ""} - success ${st.counts.success}, failed ${st.counts.failed}, skipped ${st.counts.skipped}, unknown ${st.counts.unknown}`;
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "run-paused" || msg.type === "run-done" || msg.type === "run-error") { if (S.tab === "run") renderRun(true); }
});

// ---- applications and accounts -----------------------------------------------------------------------------------
async function apps() {
  const accs = S.appId ? (await api.get(`/v1/accounts?app_id=${encodeURIComponent(S.appId)}`)) : { accounts: [], vault_available: true };
  const app = S.apps.find((a) => a.id === S.appId);
  const granted = app ? await chrome.permissions.contains({ origins: app.origins.map((o) => o + "/*") }) : false;
  main.innerHTML = `<div class="card"><h2>Applications</h2><label>${appSelect()}</label>
    ${app ? `<p class="muted">${esc(app.base_url)} - mode ${esc(app.mode)}</p><div class="row"><button class="btn${granted ? "" : " primary"}" id="grantBtn">${granted ? "Access granted" : "Grant access to this site"}</button></div>` : ""}</div>
    ${app ? `<div class="card"><h2>Accounts</h2>${accs.vault_available ? "" : `<p class="err">No secure credential store is available on this PC; passwords cannot be saved.</p>`}
      <div class="list">${accs.accounts.map((a) => `<div><strong>${esc(a.label)}</strong> (${esc(a.login_id)}) ${esc(a.role_key || "")}
        <span class="pill ${a.has_credential ? "ok" : "warn"}">${a.has_credential ? "credential saved" : "no credential"}</span>
        <div class="row" style="margin-top:4px"><button class="btn" data-cred="${esc(a.id)}">${a.has_credential ? "Replace" : "Add"} credential</button>${a.has_credential ? `<button class="btn" data-revoke="${esc(a.id)}">Revoke</button>` : ""}</div></div>`).join("") || "<p class='muted'>No accounts yet.</p>"}</div>
      <h2 style="margin-top:10px">Add account</h2><label>Label<input id="accLabel" placeholder="Entry Operator (test)"></label>
      <label>Login ID<input id="accLogin" autocomplete="off"></label><label>Role<input id="accRole" placeholder="Entry Operator"></label>
      <button class="btn" id="addAcc">Add account</button><p class="muted">Passwords are entered only on the separate credential page and saved in the Windows-protected vault after your consent.</p></div>` : ""}
    <div class="card"><h2>Register an application</h2><label>Name<input id="appName"></label><label>Application URL<input id="appUrl" placeholder="https://fleet.example.local/"></label>
      <label>Environment<input id="appEnv" placeholder="UAT"></label><label>Mode<select id="appMode"><option value="testing">Testing</option><option value="role_testing">Role testing</option><option value="production">Production</option></select></label>
      <button class="btn primary" id="regBtn">Register and grant access</button><p class="err" id="appErr"></p></div>`;
  $("#appSel").onchange = (e) => { S.appId = e.target.value; apps(); };
  const err = (e) => toast($("#appErr"), e.message);
  if ($("#grantBtn")) $("#grantBtn").onclick = async () => { const ok = await chrome.permissions.request({ origins: app.origins.map((o) => o + "/*") }); if (!ok) toast($("#appErr"), "Access was not granted. Your browser policy may block it; ask IT to allow this site for the extension."); apps(); };
  $("#regBtn").onclick = async () => {
    const url = $("#appUrl").value.trim();
    const origin = originOf(url);
    if (!origin.startsWith("http")) return err(new Error("Enter an http(s) address."));
    // the permission prompt must come from this click (a user gesture)
    const ok = await chrome.permissions.request({ origins: [origin + "/*"] });
    try {
      const out = await api.post("/v1/applications", { name: $("#appName").value.trim(), base_url: url, environment: $("#appEnv").value.trim(), mode: $("#appMode").value });
      S.appId = out.application.id;
      if (!ok) toast($("#appErr"), "Registered, but access to the site was not granted.");
      apps();
    } catch (e) { err(e); }
  };
  if ($("#addAcc")) $("#addAcc").onclick = async () => { try { await api.post("/v1/accounts", { application_id: S.appId, label: $("#accLabel").value.trim(), login_id: $("#accLogin").value.trim(), role_key: $("#accRole").value.trim() }); apps(); } catch (e) { err(e); } };
  main.querySelectorAll("[data-cred]").forEach((b) => b.onclick = () => chrome.windows.create({ url: chrome.runtime.getURL(`credential.html?account=${encodeURIComponent(b.dataset.cred)}`), type: "popup", width: 460, height: 560 }));
  main.querySelectorAll("[data-revoke]").forEach((b) => b.onclick = async () => { try { await api.post(`/v1/accounts/${b.dataset.revoke}/revoke`); apps(); } catch (e) { err(e); } });
}

// ---- learning ----------------------------------------------------------------------------------------------------
async function learn() {
  const accs = S.appId ? (await api.get(`/v1/accounts?app_id=${encodeURIComponent(S.appId)}`)).accounts : [];
  const { sessions } = S.appId ? await api.get(`/v1/learning?app_id=${encodeURIComponent(S.appId)}`) : { sessions: [] };
  const active = sessions.find((s) => s.status === "recording");
  const accSel = `<select id="lAcc"><option value="">(signed in already / none)</option>${accs.map((a) => `<option value="${esc(a.id)}">${esc(a.label)}</option>`).join("")}</select>`;
  main.innerHTML = `<div class="card"><h2>Guided learning</h2><label>Application ${appSelect()}</label>
    ${active ? `<p>Recording <strong>${esc(active.title)}</strong> (${esc(active.events)} actions so far). Perform the task normally in the application tab.</p>
      <div class="row"><button class="btn primary" id="finish">Finish and review</button><button class="btn" id="cancelL">Cancel</button></div>`
    : `<label>Account ${accSel}</label><label>What are you teaching?<input id="lTitle" placeholder="Create trip"></label><button class="btn primary" id="startL">Start recording</button>`}
    <p class="muted">Passwords and codes you type are never recorded. What I learn is saved as a draft for your approval.</p><p class="err" id="lErr"></p></div>
    <div class="card"><h2>Automatic discovery (read-only)</h2><label>Account ${accSel.replace('id="lAcc"', 'id="dAcc"')}</label><label>Up to screens<input id="dMax" type="number" min="1" max="200" value="25"></label>
    <button class="btn" id="discBtn">Start discovery</button><p class="muted">Opens screens and reads them; it never submits anything.</p></div>
    <div class="card"><h2>Recent sessions</h2><div class="list">${sessions.slice(0, 10).map((s) => `<div>${esc(s.title)} - ${esc(s.status)}</div>`).join("") || "<p class='muted'>None.</p>"}</div></div>`;
  $("#appSel").onchange = (e) => { S.appId = e.target.value; learn(); };
  const err = (e) => toast($("#lErr"), e.message);
  if ($("#startL")) $("#startL").onclick = async () => { try { const { session } = await api.post("/v1/learning", { application_id: S.appId, account_id: $("#lAcc").value || undefined, title: $("#lTitle").value.trim() || "Workflow" }); await send({ type: "learn-start", appId: S.appId, sessionId: session.id }); learn(); } catch (e) { err(e); } };
  if ($("#finish")) $("#finish").onclick = async () => { try { await send({ type: "learn-stop", sessionId: active.id }); const out = await api.post(`/v1/learning/${active.id}/finish`); main.insertAdjacentHTML("afterbegin", `<div class="card"><h2>Learned (awaiting your approval)</h2><p>Workflow "${esc(out.workflow.key)}" with ${esc((out.workflow.data.steps || []).length)} step(s) and ${esc((out.rules || []).length)} suggested rule(s) were added to Knowledge for your review.${out.profile_learned ? " The sign-in screen was learned too." : ""}</p></div>`); setTimeout(() => show("knowledge"), 1500); } catch (e) { err(e); } };
  if ($("#cancelL")) $("#cancelL").onclick = async () => { await send({ type: "learn-stop", sessionId: active.id }); await api.post(`/v1/learning/${active.id}/cancel`); learn(); };
  $("#discBtn").onclick = async () => { try { const v = await api.post("/v1/discovery", { application_id: S.appId, account_id: $("#dAcc").value || undefined, max_pages: Number($("#dMax").value) || 25 }); S.runId = v.run.id; await send({ type: "drive", runId: v.run.id }); show("run"); } catch (e) { err(e); } };
}

// ---- knowledge ---------------------------------------------------------------------------------------------------
async function knowledge() {
  const state = S.kState || "awaiting_approval";
  const { items } = await api.get(`/v1/knowledge?app_id=${encodeURIComponent(S.appId)}&state=${state}`);
  main.innerHTML = `<div class="card"><label>Application ${appSelect()}</label><label>Show <select id="kState">${["awaiting_approval", "ai_suggested", "discovered", "approved", "needs_revalidation", "rejected"].map((s) => `<option${s === state ? " selected" : ""}>${s}</option>`).join("")}</select></label>
    <div class="list">${items.map((e) => `<div><strong>${esc(e.kind)}</strong> ${esc(e.key)} <span class="pill">${esc(e.state)}</span> <span class="muted">v${e.version} from ${esc(e.source)}</span>
      <details><summary class="muted">Details</summary><pre style="white-space:pre-wrap;font-size:11px">${esc(JSON.stringify(e.data, null, 1)).slice(0, 4000)}</pre></details>
      ${["approved", "rejected", "superseded"].includes(e.state) ? "" : `<div class="row"><button class="btn primary" data-ok="${e.id}">Approve</button><button class="btn" data-no="${e.id}">Reject</button></div>`}</div>`).join("") || "<p class='muted'>Nothing here.</p>"}</div><p class="err" id="kErr"></p></div>`;
  $("#appSel").onchange = (e) => { S.appId = e.target.value; knowledge(); };
  $("#kState").onchange = (e) => { S.kState = e.target.value; knowledge(); };
  const act = (sel, path) => main.querySelectorAll(sel).forEach((b) => b.onclick = async () => { try { await api.post(`/v1/knowledge/${b.dataset.ok || b.dataset.no}/${path}`, {}); knowledge(); } catch (e) { toast($("#kErr"), e.message); } });
  act("[data-ok]", "approve");
  act("[data-no]", "reject");
}

// ---- audit -------------------------------------------------------------------------------------------------------
async function audit() {
  const [{ audit: rows }, chain] = await Promise.all([api.get("/v1/audit?limit=100"), api.get("/v1/audit/verify")]);
  main.innerHTML = `<div class="card"><p><span class="pill ${chain.valid ? "ok" : "fail"}">${chain.valid ? `Audit chain intact (${chain.checked} records)` : `Audit chain broken at record ${esc(chain.broken_at)}`}</span></p>
    <div class="list">${rows.map((r) => `<div><span class="muted">${esc(r.at.replace("T", " ").slice(0, 19))}</span> ${esc(r.action)} ${esc(r.actor || "")} ${esc(r.target || "")}</div>`).join("")}</div></div>`;
}

show("chat");
