// 23.10.0: the simple AI Agent. Replaces the eight-tab AI Application Learning & Operations Agent page.
//   CONNECT WEBSITE -> START LEARNING -> USE THE WEBSITE NORMALLY -> SAY OR TYPE WHAT YOU WANT -> ONE PREVIEW -> DONE
// Four tabs: AI Chat & Voice, Connect & Learn, Applications & Knowledge, Tasks & History. Technical settings (accounts
// editor, locators, audit chain, model and discovery limits) live in Advanced Administrator Settings (admins only).
// Passwords go only through the secure form (Windows Credential Manager); never through chat, learning or logs.
//
// 23.11.1:
// * Suggestions are built by the server from what was learned for the selected website, the page open in the
//   learning window, the tasks the configured accounts may do and the person's recent requests. There is no fixed
//   list, and no permanent password warning: a short security notice appears only when a sign-in is needed.
// * "navigate to ..." opens the page; nothing is prepared or confirmed for navigation, reading or questions.
// * The preview is short (Action, Application, the values, Role, Status). Confirm & Execute appears only when at
//   least one record can run; otherwise Provide missing data / Edit request / Cancel. The confirmation checkbox keeps
//   its state: the card is redrawn only when the preview itself changes (the old card was redrawn every two seconds,
//   which unticked the box, and "Tick the confirmation box first" appeared although the person had ticked it).
import { api } from "../api.js";
import { esc, toast, dateTimeOf } from "../util.js";
import { startRecording } from "../wav-recorder.js";
import { looksSecret, SECRET_WARNING } from "../secret-check.js";
import { isAdmin, canEdit } from "../auth.js";

const TABS = [["chat", "AI Chat & Voice"], ["connect", "Connect & Learn"], ["knowledge", "Applications & Knowledge"], ["tasks", "Tasks & History"]];
const OUTCOME = { confirmed_success: ["PASS", "Completed"], confirmed_failure: ["FAIL", "Failed"], skipped: ["WARN", "Skipped"], unknown: ["WARN", "Uncertain: check in the application"], pending: ["INFO", "Pending"], running: ["INFO", "Running"] };
const FINAL = ["completed", "partial", "failed", "stopped", "cancelled"];

const state = { tab: "chat", app: null, apps: [], conversation: null, runs: new Map(), poll: null, view: null, watching: [], lastRun: null, ack: new Map(), sig: new Map(), suggestAt: 0, input: null, log: null };
let alive = true;

const pill = (cls, text) => `<span class="pill ${cls}">${esc(text)}</span>`;
const table = (heads, rows) => rows.length ? `<div class="table-wrap"><table class="data"><thead><tr>${heads.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>` : '<p class="muted">Nothing yet.</p>';

export async function mount(view) {
  alive = true;
  state.view = view;
  try { state.tab = localStorage.getItem("aiagent.tab") || "chat"; state.conversation = sessionStorage.getItem("aiagent.conv") || null; } catch { /* storage blocked */ }
  view.innerHTML = `
    <div class="card ai-agent-head" style="margin-bottom:12px">
      <div class="row"><div><h2 style="margin:0">AI Agent</h2>
        <p class="muted" style="margin:4px 0 0">Connect a website, use it normally while I learn, then tell me what to do by text or voice. I show one preview and act only after you confirm.</p></div>
        <span class="spacer"></span><span id="aiStatus" class="muted"></span></div>
    </div>
    <nav class="tabs">${TABS.map(([k, t]) => `<a href="#" class="tab" data-tab="${k}">${esc(t)}</a>`).join("")}</nav>
    <div id="aiBody"></div>
    ${isAdmin() ? '<p class="muted" style="margin-top:16px"><a href="#ops-agent">Advanced Administrator Settings</a> (accounts editor, locators, audit chain, model and discovery limits)</p>' : ""}`;
  view.querySelectorAll("[data-tab]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); show(a.dataset.tab); }));
  await loadStatus();
  await show(state.tab);
  state.poll = setInterval(tick, 2000);
}

export function unmount() { alive = false; clearInterval(state.poll); }

async function loadStatus() {
  try {
    const st = await api("/api/ops/simple/status");
    state.apps = st.applications;
    state.watching = st.watching;
    if (!state.app || !state.apps.some((a) => a.id === state.app)) state.app = state.apps[0]?.id || null;
    const el = state.view.querySelector("#aiStatus");
    if (el) el.innerHTML = st.watching.length ? pill("INFO", `Learning in ${st.watching.length} browser window(s)`) : (st.agent?.llm && st.agent.llm !== "none" ? `Local AI: ${esc(st.agent.llm)}` : "");
  } catch (err) { toast(err.message, "error"); }
}

function appPicker() {
  if (!state.apps.length) return '<span class="muted">No website connected yet: use Connect & Learn.</span>';
  return `<label>Website <select id="appPick">${state.apps.map((a) => `<option value="${esc(a.id)}"${a.id === state.app ? " selected" : ""}>${esc(a.name)}${a.environment ? ` (${esc(a.environment)})` : ""}</option>`).join("")}</select></label>`;
}
function bindPicker(root, then) { root.querySelector("#appPick")?.addEventListener("change", (e) => { state.app = e.target.value; then(); }); }

async function show(tab) {
  state.tab = tab;
  try { localStorage.setItem("aiagent.tab", tab); } catch { /* ignore */ }
  state.view.querySelectorAll("[data-tab]").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  const body = state.view.querySelector("#aiBody");
  try {
    await ({ chat: chatTab, connect: connectTab, knowledge: knowledgeTab, tasks: tasksTab })[tab](body);
  } catch (err) {
    body.innerHTML = `<div class="card"><h3>This tab could not be loaded</h3><p>${esc(err.message)}</p><button class="btn" id="retry">Try again</button></div>`;
    body.querySelector("#retry").addEventListener("click", () => show(tab));
  }
}

async function tick() {
  if (!alive) return;
  for (const [id, el] of state.runs) {
    if (!document.body.contains(el)) { state.runs.delete(id); continue; }
    if (el.dataset.final === "1") continue;
    await renderRun(id, el).catch(() => {});
  }
  if (state.tab === "connect") {
    const box = state.view.querySelector("#watchBox");
    if (box) { await loadStatus(); box.innerHTML = watchingHtml(); bindWatching(box); }
  }
  if (state.tab === "chat" && Date.now() - state.suggestAt > 8000) loadSuggestions().catch(() => {});
}

// ---- 1. AI Chat & Voice -------------------------------------------------------------------------------------------
async function chatTab(body) {
  body.innerHTML = `
    <div class="card">
      <div class="row">${appPicker()}<span class="spacer"></span><button class="btn" id="newConv" title="Start a new conversation">New conversation</button></div>
      <div id="chatLog" class="ai-chat-log" aria-live="polite" style="max-height:52vh;overflow:auto;margin:10px 0"></div>
      <form id="chatForm" class="row" autocomplete="off">
        <input id="chatText" type="text" maxlength="4000" style="flex:1" placeholder="Say or type what you want me to do" aria-label="Command">
        <button type="button" class="btn" id="micBtn" title="Click to start and click again to stop">🎤 Speak</button>
        <button class="btn primary" type="submit">Send</button>
      </form>
      <div id="suggest" class="row" style="flex-wrap:wrap;gap:6px;margin-top:6px" aria-label="Suggestions"></div>
    </div>`;
  bindPicker(body, () => { state.suggestAt = 0; chatTab(body); });
  const log = body.querySelector("#chatLog");
  state.log = log;
  if (state.conversation) {
    try {
      const { messages } = await api(`/api/ops/chat/${encodeURIComponent(state.conversation)}`);
      for (const m of messages) bubble(log, m.speaker === "user" ? "user" : "agent", esc(m.text), m.data?.run_id);
    } catch { state.conversation = null; }
  }
  if (!log.children.length) bubble(log, "agent", state.apps.length ? "Hello. Tell me what to do in your website, or ask me about it." : "Hello. Connect your website first in <b>Connect & Learn</b>, then use it normally while I learn.");
  const input = body.querySelector("#chatText");
  state.input = input;
  body.querySelector("#chatForm").addEventListener("submit", (e) => { e.preventDefault(); send(input.value, "typed", log, input); });
  body.querySelector("#newConv").addEventListener("click", () => { state.conversation = null; try { sessionStorage.removeItem("aiagent.conv"); } catch { /* ignore */ } chatTab(body); });
  bindMic(body.querySelector("#micBtn"), log, input);
  state.suggestAt = 0;
  await loadSuggestions();
}

async function loadSuggestions() {
  const box = state.view.querySelector("#suggest");
  if (!box || !state.app) { if (box) box.innerHTML = ""; return; }
  state.suggestAt = Date.now();
  const q = `app_id=${encodeURIComponent(state.app)}${state.conversation ? `&conversation=${encodeURIComponent(state.conversation)}` : ""}`;
  const out = await api(`/api/ops/simple/suggestions?${q}`);
  const items = out.suggestions || [];
  const html = items.length ? items.map((s) => `<button type="button" class="btn small" data-sug="${esc(s.text)}" title="${esc(s.why || "")}">${esc(s.text)}</button>`).join("")
    : (out.learned ? "" : '<span class="muted">Suggestions appear here once I have learned this website (Connect & Learn).</span>');
  if (box.dataset.html === html) return; // unchanged: keep the buttons (and focus) as they are
  box.dataset.html = html;
  box.innerHTML = html;
  box.querySelectorAll("[data-sug]").forEach((b) => b.addEventListener("click", () => { state.input.value = b.dataset.sug; state.input.focus(); }));
}

function bubble(log, who, html, runId) {
  const div = document.createElement("div");
  div.className = `ai-msg ${who}`;
  div.style.cssText = `margin:6px 0;padding:8px 10px;border-radius:10px;max-width:92%;${who === "user" ? "margin-left:auto;background:var(--accent-weak, rgba(80,140,255,.15))" : "background:var(--panel-2, rgba(255,255,255,.05))"}`;
  div.innerHTML = html;
  log.appendChild(div);
  if (runId) attachRun(div, runId);
  log.scrollTop = log.scrollHeight;
  return div;
}

async function send(text, source, log, input) {
  text = (text || "").trim();
  if (!text) return;
  if (looksSecret(text)) { toast(SECRET_WARNING, "error", 8000); return; }
  if (input) input.value = "";
  bubble(log, "user", esc(text) + (source === "voice" ? ' <span class="muted">(voice)</span>' : ""));
  const wait = bubble(log, "agent", '<span class="muted">Working…</span>');
  try {
    const out = await api("/api/ops/simple/command", { method: "POST", body: { text, conversation: state.conversation, app_id: state.app, source } });
    state.conversation = out.conversation;
    try { sessionStorage.setItem("aiagent.conv", out.conversation); } catch { /* ignore */ }
    wait.remove();
    renderReply(log, out.reply);
    state.suggestAt = 0;
    loadSuggestions().catch(() => {});
  } catch (err) { wait.innerHTML = `${pill("FAIL", "Error")} ${esc(err.message)}`; }
}

function renderReply(log, r) {
  let html = esc(r.text || "");
  if (r.type === "navigate" && r.url) {
    const opened = r.opened || {};
    html += `<br><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.url)}</a>`;
    if (opened.ok === false) html += `<br>${pill("WARN", "Not opened")} ${esc(opened.message || "")}`;
    else if (opened.window) html += ` <span class="muted">(${opened.window === "learning" ? "in your learning window" : opened.window === "existing" ? "in the open window" : "in a new window"})</span>`;
  }
  if (r.type === "records" && r.rows?.length) {
    const cols = Object.keys(r.rows[0]).filter((k) => !k.startsWith("_") && k !== "key" && !/^col\d+$/.test(k)).slice(0, 8);
    html += table(cols, r.rows.slice(0, 50).map((row) => `<tr>${cols.map((c) => `<td>${esc(row[c] ?? "")}</td>`).join("")}</tr>`));
  }
  if (r.type === "clarify" && r.missing?.length) html = `${pill("WARN", "Missing information")} <b>${esc(r.missing.join(", "))}</b><br>${html}`;
  if (r.type === "run" && r.filled?.length) html += `<p class="muted">I filled in: ${r.filled.map(esc).join("; ")}.</p>`;
  const div = bubble(log, "agent", html, r.type === "run" ? r.run_id : null);
  if (r.type === "need_credential" && r.account_id) {
    if (r.security_notice) div.insertAdjacentHTML("beforeend", `<p class="muted">🔒 ${esc(r.security_notice)}</p>`);
    div.appendChild(credentialForm(r.account_id, r.account, () => send("again", "typed", log)));
  }
  if (r.type === "secret_intercepted") div.insertAdjacentHTML("afterbegin", pill("WARN", "Withheld") + " ");
}

function credentialForm(accountId, label, after) {
  const f = document.createElement("form");
  f.className = "settings";
  f.autocomplete = "off";
  f.innerHTML = `<p><b>Password for ${esc(label || "this account")}</b> (saved in Windows Credential Manager on this PC)</p>
    <input type="password" name="pw" maxlength="512" autocomplete="new-password" required aria-label="Password">
    <label><input type="checkbox" name="consent" required> I am allowed to let SkyGuard sign in with this account</label>
    <div class="row"><button class="btn primary" type="submit">Save securely and continue</button></div>`;
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api(`/api/ops/accounts/${encodeURIComponent(accountId)}/credential`, { method: "PUT", body: { password: f.pw.value, consent: f.consent.checked } });
      f.pw.value = "";
      f.replaceWith(Object.assign(document.createElement("p"), { innerHTML: pill("PASS", "Saved securely") }));
      after();
    } catch (err) { toast(err.message, "error"); }
  });
  return f;
}

function bindMic(btn, log, input) {
  let rec = null;
  const stop = async () => {
    if (!rec) return;
    const r = rec; rec = null;
    btn.textContent = "🎤 Speak"; btn.classList.remove("primary");
    try {
      const wav = await r.stop();
      const { text } = await api("/api/ops/simple/voice", { method: "POST", rawBody: wav, contentType: "audio/wav" });
      if (!text) { toast("I did not catch that. Try again a little closer to the microphone.", "info"); return; }
      send(text, "voice", log, input);
    } catch (err) { toast(err.message, "error"); }
  };
  btn.addEventListener("click", async () => {
    if (rec) return stop();
    try {
      rec = await startRecording({ maxMs: 20000 });
      btn.textContent = "■ Stop and send"; btn.classList.add("primary");
    } catch (err) { rec = null; toast(`The microphone could not be used: ${err.message}`, "error"); }
  });
}

// One live card per task: inspecting -> preview -> (confirm) -> running -> outcome
function attachRun(container, runId) {
  const el = document.createElement("div");
  el.className = "card ai-run";
  el.style.marginTop = "8px";
  el.innerHTML = '<p class="muted">Preparing the preview…</p>';
  container.appendChild(el);
  state.runs.set(runId, el);
  renderRun(runId, el);
}

function previewHtml(runId, s) {
  const rows = [["Action", s.action], ["Application", s.application]];
  const recs = s.records || [];
  if (recs.length === 1) for (const [k, v] of Object.entries(recs[0])) rows.push([k, v]);
  if (s.role || s.account) rows.push(["Role", `${s.role || ""}${s.account ? ` (account ${s.account})` : ""}`]);
  rows.push(["Status", s.status_text]);
  let html = `<table class="data ai-preview"><tbody>${rows.map(([k, v]) => `<tr><th style="text-align:left;width:30%">${esc(k)}</th><td>${esc(v ?? "")}</td></tr>`).join("")}</tbody></table>`;
  if (recs.length > 1) {
    const cols = Object.keys(recs[0]);
    html += `<p><b>${recs.length}</b> records:</p>` + table(cols, recs.map((r) => `<tr>${cols.map((c) => `<td>${esc(r[c] ?? "")}</td>`).join("")}</tr>`));
  }
  if (s.missing?.length) html += `<p>${pill("WARN", "Missing information")} <b>${esc(s.missing.join(", "))}</b></p>`;
  if (s.blocked_reasons?.length && !s.can_confirm) html += `<p>${pill("FAIL", "Cannot run")} ${s.blocked_reasons.map(esc).join("; ")}</p>`;
  else if (s.blocked) html += `<p>${pill("WARN", "Note")} ${s.blocked} of ${s.count} record(s) will not run: ${s.blocked_reasons.map(esc).join("; ")}</p>`;
  if (!canEdit()) return html + '<p class="muted">Your account is view only.</p>';
  if (s.can_confirm) {
    const checked = state.ack.get(`${runId}:${s.preview_hash}`) ? " checked" : "";
    html += (s.consequential ? `<label class="ai-ack"><input type="checkbox" data-ack${checked}> I have read this. ${esc(s.action)} changes records in ${esc(s.application)}.</label>` : "")
      + '<div class="row"><button class="btn primary" data-confirm>Confirm &amp; Execute</button><button class="btn" data-edit>Edit</button><button class="btn" data-stop>Cancel</button></div>';
  } else {
    html += `<div class="row">${s.missing?.length ? '<button class="btn primary" data-provide>Provide missing data</button>' : ""}<button class="btn" data-edit>Edit request</button><button class="btn" data-stop>Cancel</button></div>`;
  }
  return html;
}

async function renderRun(runId, el) {
  const data = await api(`/api/ops/simple/runs/${encodeURIComponent(runId)}`);
  const r = data.run, s = data.simple, stages = data.plan?.stages || [];
  // redraw only when something the person sees changed: a redraw would reset the confirmation checkbox
  const sig = JSON.stringify([r.status, r.preview_hash, data.items.map((i) => i.status), data.status?.pause?.code, r.summary?.message]);
  if (state.sig.get(runId) === sig && el.dataset.drawn === "1") return;
  state.sig.set(runId, sig);
  const counts = {};
  for (const i of data.items) { const o = (OUTCOME[i.status] || ["INFO", i.status])[1]; counts[o] = (counts[o] || 0) + 1; }
  const head = `<div class="row"><b>${esc(stages.map((x) => x.title).join(" → ") || (data.plan?.kind === "discovery" ? "Read-only exploration" : "Task"))}</b><span class="spacer"></span>${pill(r.status === "completed" ? "PASS" : r.status === "failed" ? "FAIL" : r.status === "partial" ? "WARN" : "INFO", s.status_text || r.status.replace(/_/g, " "))}</div>`;
  let mid = "";
  if (r.status === "inspecting" || r.status === "planning") mid = '<p class="muted">Checking the application (read-only) for duplicates, availability and current statuses…</p>';
  if (r.status === "awaiting_confirmation") mid = previewHtml(runId, s);
  if (r.status === "paused") {
    const pause = data.status?.pause || {};
    mid = `<p>${pill("WARN", "Waiting for you")} ${esc(pause.message || "Paused.")}</p>
      ${(pause.choices || []).length ? `<div class="row">${pause.choices.map((c, i) => `<button class="btn" data-choice="${i}">${esc(c.label || c.text || `Option ${i + 1}`)}</button>`).join("")}</div>` : ""}
      <div class="row"><button class="btn primary" data-resume>Resume</button><button class="btn" data-stop>Stop</button></div>`;
  }
  if (["running", ...FINAL].includes(r.status) || data.items.some((i) => i.status !== "pending")) {
    mid += `<p>${Object.entries(counts).map(([k, n]) => `${esc(k)}: <b>${n}</b>`).join(" · ")}</p>
      <details${FINAL.includes(r.status) ? "" : " open"}><summary>Records</summary>${table(["#", "Record", "Result", "Reason"], data.items.map((i) => `<tr><td>${i.seq}</td><td>${esc(Object.entries(i.record).filter(([k]) => !k.startsWith("_")).map(([k, v]) => `${k}=${v}`).join(", "))}</td><td>${pill(...(OUTCOME[i.status] || ["INFO", i.status]))}</td><td>${esc(i.result?.reason || "")}</td></tr>`))}</details>`;
  }
  if (s.result) mid += `<p><b>${esc(s.result)}</b></p>`;
  else if (r.summary?.message) mid += `<p><b>${esc(r.summary.message)}</b></p>`;
  el.innerHTML = head + mid;
  el.dataset.drawn = "1";
  el.dataset.final = FINAL.includes(r.status) ? "1" : "0";
  const ack = el.querySelector("[data-ack]");
  ack?.addEventListener("change", () => state.ack.set(`${runId}:${s.preview_hash}`, ack.checked));
  el.querySelector("[data-confirm]")?.addEventListener("click", async (e) => {
    if (ack && !ack.checked) { toast("Tick the box to confirm that you read the preview.", "info"); ack.focus(); return; }
    e.target.disabled = true; // one click, one submission
    try {
      await api(`/api/ops/simple/runs/${encodeURIComponent(runId)}/confirm`, { method: "POST", body: { preview_hash: s.preview_hash, acknowledge: !!ack?.checked, app_id: data.run.application_id } });
      state.sig.delete(runId); renderRun(runId, el);
    } catch (err) { e.target.disabled = false; toast(err.message, "error", 8000); state.sig.delete(runId); renderRun(runId, el); }
  });
  const cancel = async () => { try { await api(`/api/ops/simple/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }); state.sig.delete(runId); renderRun(runId, el); } catch (err) { toast(err.message, "error"); } };
  el.querySelectorAll("[data-stop]").forEach((b) => b.addEventListener("click", cancel));
  el.querySelector("[data-edit]")?.addEventListener("click", async () => {
    if (state.input) { state.input.value = s.request_text || (stages[0]?.title || ""); state.input.focus(); }
    await cancel();
  });
  el.querySelector("[data-provide]")?.addEventListener("click", () => {
    if (state.input) { state.input.value = (s.missing || []).map((m) => `${m}: `).join("; "); state.input.focus(); }
    cancel();
  });
  el.querySelector("[data-resume]")?.addEventListener("click", async () => { try { await api(`/api/ops/runs/${encodeURIComponent(runId)}/resume`, { method: "POST", body: {} }); state.sig.delete(runId); renderRun(runId, el); } catch (err) { toast(err.message, "error"); } });
  el.querySelectorAll("[data-choice]").forEach((b) => b.addEventListener("click", async () => { try { await api(`/api/ops/runs/${encodeURIComponent(runId)}/resume`, { method: "POST", body: { choice: Number(b.dataset.choice) } }); state.sig.delete(runId); renderRun(runId, el); } catch (err) { toast(err.message, "error"); } }));
}

// ---- 2. Connect & Learn -------------------------------------------------------------------------------------------
function watchingHtml() {
  if (!state.watching.length) return '<p class="muted">Not learning right now.</p>';
  return state.watching.map((w) => {
    const app = state.apps.find((a) => a.id === w.application_id);
    const s = w.stats || {};
    return `<div class="row" style="margin:6px 0">${pill("INFO", "Learning")} <b>${esc(app?.name || "")}</b>
      <span class="muted">${s.pages ?? s.snapshots ?? 0} page views, ${s.actions ?? s.events ?? 0} actions seen</span><span class="spacer"></span>
      <button class="btn primary" data-stopwatch="${esc(w.session_id)}">Stop and save learning</button></div>`;
  }).join("");
}
function bindWatching(root) {
  root.querySelectorAll("[data-stopwatch]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      const out = await api(`/api/ops/simple/learn/${encodeURIComponent(b.dataset.stopwatch)}/stop`, { method: "POST" });
      const v = out.verified || {};
      if (v.checked && v.ok) toast(`Learning saved and checked: ${v.tasks} task(s) and ${v.pages} page(s) can be used in commands.`, "success", 8000);
      else if (v.checked) toast(`Learning saved, but ${v.failed.length} item(s) could not be found again: see Applications & Knowledge.`, "error", 10000);
      else toast("Learning saved. I did not see a complete task yet: do one from start to finish (until the website confirms it).", "info", 10000);
      await loadStatus(); show("connect");
    } catch (err) { toast(err.message, "error"); b.disabled = false; }
  }));
}

async function connectTab(body) {
  body.innerHTML = `
    <form class="card settings" id="connForm" autocomplete="off"><h3>Connect a website</h3>
      <label>Website address <input id="cUrl" type="url" required maxlength="500" placeholder="http://transport.intranet.local/"></label>
      <label>Name <input id="cName" maxlength="80" placeholder="Suggested automatically"></label>
      <label>Environment <select id="cEnv"><option value="Test">Test (recommended for learning)</option><option value="Production">Production (real records)</option></select></label>
      <label>Browser <select id="cBrowser"><option value="msedge">Microsoft Edge</option><option value="chrome">Google Chrome</option></select></label>
      <p id="envWarn" class="muted">${pill("WARN", "Note")} What you do while I learn really happens in the website. Use a test environment where you can.</p>
      <div class="row"><button class="btn primary" type="submit">Connect and start learning</button></div>
      <p class="muted">A maximized browser window opens on the website. Sign in and work as usual: create, edit, approve, search. I only watch: I learn the pages, tasks, rules, roles and statuses by myself and never click or type there. Passwords and codes are never captured. Press Stop and save learning when you are done (closing the window also saves).</p>
    </form>
    <div class="card" style="margin-top:12px"><h3>Learning now</h3><div id="watchBox">${watchingHtml()}</div></div>
    <div class="card" style="margin-top:12px"><h3>Connected websites</h3>
      ${table(["Website", "Environment", "Address", ""], state.apps.map((a) => `<tr><td>${esc(a.name)}</td><td>${esc(a.environment || "")}</td><td>${esc(a.base_url)}</td><td>
        <button class="btn" data-learn="${esc(a.base_url)}">Start learning</button>
        <button class="btn" data-explore="${esc(a.id)}" title="Opens the website and looks at menus, pages, forms and tables without submitting anything">Explore automatically (read-only)</button></td></tr>`))}</div>`;
  const url = body.querySelector("#cUrl"), name = body.querySelector("#cName"), env = body.querySelector("#cEnv");
  env.addEventListener("change", () => { body.querySelector("#envWarn").innerHTML = env.value === "Production" ? `${pill("FAIL", "Production")} Everything you do while I learn changes real records. Prefer a test environment.` : `${pill("WARN", "Note")} What you do while I learn really happens in the website. Use a test environment where you can.`; });
  url.addEventListener("input", () => {
    try {
      const u = new URL(url.value);
      const parts = u.hostname.split(".").filter((p) => p && !["www", "intranet", "app", "apps", "portal", "web"].includes(p));
      name.placeholder = /^[\d.]+$/.test(u.hostname) || u.hostname === "localhost" ? "Suggested automatically" : (parts[0] || "").replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) || "Suggested automatically";
    } catch { name.placeholder = "Suggested automatically"; }
  });
  const go = async (u, n, browser, environment) => {
    try {
      const out = await api("/api/ops/simple/connect", { method: "POST", body: { url: u, name: n || null, learn: true, browser, environment } });
      state.app = out.application.id;
      toast(`${out.application.name}: ${out.session?.reused ? "already learning in an open window" : "a browser window is opening. Use the website normally."}`, "success", 7000);
      await loadStatus(); show("connect");
    } catch (err) { toast(err.message, "error"); }
  };
  body.querySelector("#connForm").addEventListener("submit", (e) => { e.preventDefault(); go(url.value.trim(), name.value.trim(), body.querySelector("#cBrowser").value, env.value); });
  body.querySelectorAll("[data-learn]").forEach((b) => b.addEventListener("click", () => go(b.dataset.learn, null, body.querySelector("#cBrowser").value, null)));
  body.querySelectorAll("[data-explore]").forEach((b) => b.addEventListener("click", async () => {
    try {
      const out = await api("/api/ops/simple/explore", { method: "POST", body: { application_id: b.dataset.explore } });
      toast("Exploring read-only. If the website asks you to sign in, sign in yourself in the window that opened.", "success", 8000);
      state.lastRun = out.run?.id;
    } catch (err) { toast(err.message, "error"); }
  }));
  bindWatching(body.querySelector("#watchBox"));
}

// ---- 3. Applications & Knowledge ----------------------------------------------------------------------------------
async function knowledgeTab(body) {
  if (!state.app) { body.innerHTML = `<div class="card"><p class="muted">Connect a website first (Connect & Learn).</p></div>`; return; }
  const k = await api(`/api/ops/simple/knowledge/${encodeURIComponent(state.app)}`);
  const certainty = (t) => t.usable ? (t.certainty === "verified" ? pill("PASS", `Verified · seen ${t.seen}×`) : pill("WARN", `Uncertain · seen ${t.seen}×`)) : pill("INFO", t.state);
  const rv = k.retrieval || {};
  const ls = k.last_session;
  body.innerHTML = `
    <div class="card"><div class="row">${appPicker()}<span class="spacer"></span><span class="muted">${k.journal?.observations ?? ""} observations · ${k.records} records seen</span></div>
      <p>${rv.checked ? (rv.ok ? pill("PASS", "Knowledge checked") : pill("FAIL", `${rv.failed.length} item(s) not found again`)) : pill("INFO", "Nothing learned yet")}
        ${rv.checked ? `${rv.tasks} task(s) and ${rv.pages} page(s) are found by commands.` : ""}
        ${ls ? `<span class="muted">Last learning session: ${esc(dateTimeOf(ls.started))} (${esc(ls.status)}${ls.by ? `, ${esc(ls.by)}` : ""})</span>` : ""}
        <button class="btn small" id="testKnow">Test learned knowledge</button></p>
      <div id="testOut"></div></div>
    <div class="card" style="margin-top:8px"><h3>Tasks I can do</h3>
      ${table(["Task", "Who does it", "Fields", "Status", ""], k.tasks.map((t) => `<tr><td><b>${esc(t.title)}</b>${t.consequential ? ' <span class="muted">(asks for confirmation)</span>' : ""}</td><td>${esc((t.roles || []).join(", "))}</td><td>${esc(t.fields.join(", "))}</td><td>${certainty(t)}</td>
        <td>${canEdit() && t.usable ? `<button class="link-btn" data-try="${esc(t.title)}" title="Prepares a preview in the chat; nothing runs without your confirmation">Test learned workflow</button> · ` : ""}${canEdit() ? `<button class="link-btn" data-forget="${t.id}">This is wrong</button>` : ""}</td></tr>`))}
      <p class="muted">Verified: I saw it done successfully at least twice. Uncertain: seen once; I still show the preview before doing it.</p></div>
    <div class="card" style="margin-top:8px"><h3>Pages learned</h3>
      ${table(["Page", "Address", "Kind", "Visits"], (k.pages || []).slice(0, 60).map((p) => `<tr><td>${esc(p.title)}</td><td>${esc(p.path)}</td><td>${esc(p.type || "")}</td><td>${p.visits ?? ""}</td></tr>`))}</div>
    <div class="card" style="margin-top:8px"><h3>Rules the application enforces</h3>
      ${table(["Rule", "Applies to", "Seen", "Verification", "Evidence"], k.rules.map((r) => `<tr><td>${esc(r.rule)}</td><td>${esc(r.applies_to || "")}</td><td>${r.observations}×</td><td>${esc(r.verification)}</td><td>${esc(typeof r.evidence === "string" ? r.evidence : JSON.stringify(r.evidence)).slice(0, 200)}</td></tr>`))}
      ${k.suggestions.length ? `<h4>Unverified rules (shown, never enforced until confirmed)</h4>${table(["Rule", "Seen", "Confidence", "Verification", ""], k.suggestions.map((s) => `<tr><td>${esc(s.rule)}</td><td>${s.observations}×</td><td>${Math.round((s.confidence || 0) * 100)}%</td><td>${esc(s.verification)}</td><td>${isAdmin() ? `<button class="link-btn" data-trust="${s.id}">Confirm</button> · <button class="link-btn" data-forget="${s.id}">Wrong</button>` : ""}</td></tr>`))}` : ""}</div>
    <div class="grid two" style="margin-top:8px">
      <div class="card"><h3>Roles</h3>${table(["Role", "Users seen", "Does"], (k.roles || []).map((r) => `<tr><td>${esc(r.name || r.key)}</td><td>${esc((r.users || []).join(", "))}</td><td>${esc((r.performed || []).map((p) => p.flow).join(", "))}</td></tr>`))}</div>
      <div class="card"><h3>Status flow</h3>${(k.lifecycles || []).map((lc) => `<p><b>${esc(lc.entity)}</b>: ${esc((lc.statuses || []).join(" → "))}</p>${(lc.transitions || []).map((t) => `<p class="muted">${esc(t.from)} → ${esc(t.to)} by ${esc(t.action)} (${esc((t.roles || []).join(", "))})</p>`).join("")}`).join("") || '<p class="muted">No status column seen yet.</p>'}</div>
    </div>
    ${(k.unverified || []).length ? `<div class="card" style="margin-top:8px"><h3>Not verified yet</h3><p class="muted">${k.unverified.map(esc).join(" · ")}</p></div>` : ""}
    <div class="card" style="margin-top:8px"><h3>Accounts</h3>
      <p class="muted">One account per role. Created from the sign-ins I saw, or added here. Passwords are kept in Windows Credential Manager on this PC, never in the learning data, chat or logs.</p>
      ${table(["Account", "Role", "Password", ""], k.accounts.map((a) => `<tr><td>${esc(a.label)}</td><td>${esc(a.verified_role || a.role_key || "not shown by the application")}${a.verified_role ? ` ${pill("PASS", "verified")}` : ""}</td><td>${a.has_credential ? pill("PASS", "Saved") : pill("INFO", "Not saved")}</td><td>${canEdit() ? `<button class="link-btn" data-cred="${esc(a.id)}" data-label="${esc(a.label)}">${a.has_credential ? "Replace" : "Save password"}</button>` : ""}</td></tr>`))}
      ${canEdit() ? `<form id="accForm" class="row" autocomplete="off" style="margin-top:8px"><input name="label" maxlength="80" placeholder="Account label, e.g. Manager (test)" required><input name="login" maxlength="120" placeholder="User name" required><input name="role" maxlength="60" placeholder="Role, e.g. Manager"><button class="btn" type="submit">Add account</button></form>` : ""}
      <div id="credBox"></div></div>`;
  bindPicker(body, () => knowledgeTab(body));
  body.querySelector("#testKnow").addEventListener("click", async () => {
    const box = body.querySelector("#testOut");
    try {
      const v = await api(`/api/ops/simple/verify/${encodeURIComponent(state.app)}`, { method: "POST" });
      box.innerHTML = table(["Kind", "Name", "Found by a command", ""], v.checks.map((c) => `<tr><td>${esc(c.kind)}</td><td>${esc(c.name)}</td><td>${esc(c.found || "")}</td><td>${c.ok ? pill("PASS", "OK") : pill("FAIL", c.problem || "not found")}</td></tr>`));
    } catch (err) { toast(err.message, "error"); }
  });
  body.querySelectorAll("[data-try]").forEach((b) => b.addEventListener("click", async () => {
    await show("chat");
    if (state.input) { state.input.value = b.dataset.try; state.input.focus(); }
  }));
  body.querySelectorAll("[data-forget]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/ops/simple/knowledge/${b.dataset.forget}/forget`, { method: "POST", body: {} }); knowledgeTab(body); } catch (err) { toast(err.message, "error"); }
  }));
  body.querySelectorAll("[data-trust]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/ops/simple/knowledge/${b.dataset.trust}/trust`, { method: "POST", body: {} }); knowledgeTab(body); } catch (err) { toast(err.message, "error"); }
  }));
  body.querySelector("#accForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api("/api/ops/accounts", { method: "POST", body: { application_id: state.app, label: f.label.value.trim(), login_id: f.login.value.trim(), role_key: f.role.value.trim() } });
      knowledgeTab(body);
    } catch (err) { toast(err.message, "error"); }
  });
  body.querySelectorAll("[data-cred]").forEach((b) => b.addEventListener("click", () => {
    const box = body.querySelector("#credBox");
    box.innerHTML = "";
    box.appendChild(credentialForm(b.dataset.cred, b.dataset.label, () => knowledgeTab(body)));
  }));
}

// ---- 4. Tasks & History -------------------------------------------------------------------------------------------
async function tasksTab(body) {
  const { tasks } = await api(`/api/ops/simple/tasks${state.app ? `?app_id=${encodeURIComponent(state.app)}` : ""}`);
  body.innerHTML = `
    <div class="card"><div class="row">${appPicker()}</div>
      ${table(["Task", "Status", "Result", "Completed", "Failed", "Skipped", "Uncertain", "By", "Started", ""], tasks.map((t) => `<tr><td>${esc(t.title)}</td><td>${esc((t.status || "").replace(/_/g, " "))}</td><td>${esc(t.result || "")}</td>
        <td>${t.counts.completed || 0}</td><td>${t.counts.failed || 0}</td><td>${t.counts.skipped || 0}</td><td>${t.counts.uncertain || 0}</td><td>${esc(t.created_by || "")}</td><td>${esc(dateTimeOf(t.created_at))}</td>
        <td><button class="link-btn" data-open="${esc(t.id)}">Open</button></td></tr>`))}
      <p class="muted">Uncertain means the application did not clearly confirm the result (for example after a time-out). I never repeat such a record automatically; check it in the application.</p></div>
    <div id="taskDetail"></div>`;
  bindPicker(body, () => tasksTab(body));
  body.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => {
    const box = body.querySelector("#taskDetail");
    box.innerHTML = "";
    attachRun(box, b.dataset.open);
  }));
}
