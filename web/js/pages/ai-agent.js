// 23.10.0: the simple AI Agent. Replaces the eight-tab AI Application Learning & Operations Agent page.
//   CONNECT WEBSITE -> START LEARNING -> USE THE WEBSITE NORMALLY -> SAY OR TYPE WHAT YOU WANT -> ONE PREVIEW -> DONE
// Four tabs: AI Chat & Voice, Connect & Learn, Applications & Knowledge, Tasks & History. Technical settings (accounts
// editor, locators, audit chain, model and discovery limits) live in Advanced Administrator Settings (admins only).
// Passwords go only through the secure form (Windows Credential Manager); never through chat, learning or logs.
import { api } from "../api.js";
import { esc, toast, dateTimeOf } from "../util.js";
import { startRecording } from "../wav-recorder.js";
import { looksSecret, SECRET_WARNING } from "../secret-check.js";
import { isAdmin, canEdit } from "../auth.js";

const TABS = [["chat", "AI Chat & Voice"], ["connect", "Connect & Learn"], ["knowledge", "Applications & Knowledge"], ["tasks", "Tasks & History"]];
const OUTCOME = { confirmed_success: ["PASS", "Completed"], confirmed_failure: ["FAIL", "Failed"], skipped: ["WARN", "Skipped"], unknown: ["WARN", "Uncertain: check in the application"], pending: ["INFO", "Pending"], running: ["INFO", "Running"] };
const EXAMPLES = ["Create two trips for tomorrow for driver John", "Create Aslam's trip from Dubai to Abu Dhabi", "Show pending trips", "Approve the selected trips", "Authorize all approved trips", "What happens after a trip is approved?"];

const state = { tab: "chat", app: null, apps: [], conversation: null, runs: new Map(), poll: null, view: null, watching: [], lastRun: null };
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
  return `<label>Website <select id="appPick">${state.apps.map((a) => `<option value="${esc(a.id)}"${a.id === state.app ? " selected" : ""}>${esc(a.name)}</option>`).join("")}</select></label>`;
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
}

// ---- 1. AI Chat & Voice -------------------------------------------------------------------------------------------
async function chatTab(body) {
  body.innerHTML = `
    <div class="card">
      <div class="row">${appPicker()}<span class="spacer"></span><button class="btn" id="newConv" title="Start a new conversation">New conversation</button></div>
      <div id="chatLog" class="ai-chat-log" aria-live="polite" style="max-height:52vh;overflow:auto;margin:10px 0"></div>
      <form id="chatForm" class="row" autocomplete="off">
        <input id="chatText" type="text" maxlength="4000" style="flex:1" placeholder="Say or type what you want, e.g. Create two trips for tomorrow for driver John" aria-label="Command">
        <button type="button" class="btn" id="micBtn" title="Hold to talk, or click to start and click again to stop">🎤 Speak</button>
        <button class="btn primary" type="submit">Send</button>
      </form>
      <p class="muted" style="margin:6px 0 0">Try: ${EXAMPLES.map((e) => `<a href="#" class="link-btn" data-ex="${esc(e)}">${esc(e)}</a>`).join(" · ")}</p>
      <p class="muted" style="margin:4px 0 0">Never type passwords or codes here. When I need to sign in, I ask for the password in a secure form.</p>
    </div>`;
  bindPicker(body, () => chatTab(body));
  const log = body.querySelector("#chatLog");
  if (state.conversation) {
    try {
      const { messages } = await api(`/api/ops/chat/${encodeURIComponent(state.conversation)}`);
      for (const m of messages) bubble(log, m.speaker === "user" ? "user" : "agent", esc(m.text), m.data?.run_id);
    } catch { state.conversation = null; }
  }
  if (!log.children.length) bubble(log, "agent", "Hello. Tell me what to do in your website, or ask me about it. If I have not learned it yet, open <b>Connect & Learn</b> first.");
  const input = body.querySelector("#chatText");
  body.querySelector("#chatForm").addEventListener("submit", (e) => { e.preventDefault(); send(input.value, "typed", log, input); });
  body.querySelectorAll("[data-ex]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); input.value = a.dataset.ex; input.focus(); }));
  body.querySelector("#newConv").addEventListener("click", () => { state.conversation = null; try { sessionStorage.removeItem("aiagent.conv"); } catch { /* ignore */ } chatTab(body); });
  bindMic(body.querySelector("#micBtn"), log, input);
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
  } catch (err) { wait.innerHTML = `${pill("FAIL", "Error")} ${esc(err.message)}`; }
}

function renderReply(log, r) {
  let html = esc(r.text || "");
  if (r.type === "records" && r.rows?.length) {
    const cols = Object.keys(r.rows[0]).filter((k) => !k.startsWith("_") && k !== "key" && !/^col\d+$/.test(k)).slice(0, 8);
    html += table(cols, r.rows.slice(0, 50).map((row) => `<tr>${cols.map((c) => `<td>${esc(row[c] ?? "")}</td>`).join("")}</tr>`));
  }
  if (r.type === "run") {
    if (r.filled?.length) html += `<p class="muted">I filled in: ${r.filled.map(esc).join("; ")}.</p>`;
  }
  const div = bubble(log, "agent", html, r.type === "run" ? r.run_id : null);
  if (r.type === "need_credential" && r.account_id) div.appendChild(credentialForm(r.account_id, r.account, () => send("again", "typed", log)));
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

async function renderRun(runId, el) {
  const data = await api(`/api/ops/runs/${encodeURIComponent(runId)}`);
  const r = data.run, p = r.preview || {}, stages = data.plan?.stages || [];
  const cons = stages.some((s) => /(approv|authori[sz]|reject|delet|remov|cancel|void|record|submit|post|pay|close|accept|confirm|finali|complet|dispatch|release)/i.test(`${s.workflow} ${s.title}`));
  const counts = {};
  for (const i of data.items) { const o = (OUTCOME[i.status] || ["INFO", i.status])[1]; counts[o] = (counts[o] || 0) + 1; }
  const head = `<div class="row"><b>${esc(stages.map((s) => s.title).join(" → ") || (data.plan?.kind === "discovery" ? "Read-only exploration" : "Task"))}</b><span class="spacer"></span>${pill(r.status === "completed" ? "PASS" : r.status === "failed" ? "FAIL" : r.status === "partial" ? "WARN" : "INFO", r.status.replace(/_/g, " "))}</div>`;
  let mid = "";
  if (r.status === "inspecting") mid = '<p class="muted">Checking the application (read-only) for duplicates, availability and current statuses…</p>';
  if (r.status === "awaiting_confirmation") {
    const stage = (p.stages || [])[0] || {};
    mid = `<p>As <b>${esc(stage.account || "")}</b>${stage.role_verified ? ` (${esc(stage.role_verified)})` : ""} in <b>${esc(p.application || "")}</b>:</p>
      ${table(["#", "Values", "Checks", ""], (p.records || []).map((x) => `<tr><td>${x.seq}</td><td>${Object.entries(x.values).map(([k, v]) => `${esc(k.replace(/_/g, " "))}: <b>${esc(v)}</b>${(x.auto_assigned || []).includes(k) ? ' <span class="muted">(chosen for you)</span>' : ""}`).join("<br>")}</td><td>${(x.findings || []).map((f) => `${f.severity === "error" ? "✖" : "⚠"} ${esc(f.message)}`).join("<br>") || "✔"}</td><td>${pill(x.status === "blocked" ? "FAIL" : x.status === "warning" ? "WARN" : "PASS", x.status === "blocked" ? "will not run" : "ready")}</td></tr>`))}
      ${(p.warnings || []).map((w) => `<p>${pill("WARN", "Note")} ${esc(w)}</p>`).join("")}
      <p><b>${p.totals?.ready || 0}</b> record(s) will be processed${p.totals?.blocked ? `, <b>${p.totals.blocked}</b> blocked by rules will not` : ""}.</p>
      ${cons ? `<label class="ai-ack"><input type="checkbox" id="ack-${esc(runId)}"> I have read this preview. This ${esc(stages[0]?.title || "operation")} changes records in the application.</label>` : ""}
      ${canEdit() ? `<div class="row"><button class="btn primary" data-confirm>Confirm</button><button class="btn" data-stop>Cancel</button></div>` : '<p class="muted">Your account is view only.</p>'}`;
  }
  if (r.status === "paused") {
    const pause = data.status?.pause || {};
    mid = `<p>${pill("WARN", "Waiting for you")} ${esc(pause.message || "Paused.")}</p>
      ${(pause.choices || []).length ? `<div class="row">${pause.choices.map((c, i) => `<button class="btn" data-choice="${i}">${esc(c.label || c.text || `Option ${i + 1}`)}</button>`).join("")}</div>` : ""}
      <div class="row"><button class="btn primary" data-resume>Resume</button><button class="btn" data-stop>Stop</button></div>`;
  }
  if (["running", "completed", "partial", "failed", "stopped", "cancelled"].includes(r.status) || data.items.some((i) => i.status !== "pending")) {
    mid += `<p>${Object.entries(counts).map(([k, n]) => `${esc(k)}: <b>${n}</b>`).join(" · ")}</p>
      <details${["completed", "partial", "failed"].includes(r.status) ? "" : " open"}><summary>Records</summary>${table(["#", "Record", "Result", "Reason"], data.items.map((i) => `<tr><td>${i.seq}</td><td>${esc(Object.entries(i.record).filter(([k]) => !k.startsWith("_")).map(([k, v]) => `${k}=${v}`).join(", "))}</td><td>${pill(...(OUTCOME[i.status] || ["INFO", i.status]))}</td><td>${esc(i.result?.reason || "")}</td></tr>`))}</details>`;
  }
  if (r.summary?.message) mid += `<p><b>${esc(r.summary.message)}</b></p>`;
  el.innerHTML = head + mid;
  el.dataset.final = ["completed", "partial", "failed", "stopped", "cancelled"].includes(r.status) ? "1" : "0";
  el.querySelector("[data-confirm]")?.addEventListener("click", async () => {
    const ack = el.querySelector(`#ack-${CSS.escape(runId)}`);
    if (ack && !ack.checked) { toast("Tick the confirmation box first.", "info"); return; }
    try { await api(`/api/ops/simple/runs/${encodeURIComponent(runId)}/confirm`, { method: "POST", body: { preview_hash: r.preview_hash, acknowledge: !!ack?.checked } }); renderRun(runId, el); } catch (err) { toast(err.message, "error"); }
  });
  el.querySelector("[data-stop]")?.addEventListener("click", async () => { try { await api(`/api/ops/runs/${encodeURIComponent(runId)}/stop`, { method: "POST" }); renderRun(runId, el); } catch (err) { toast(err.message, "error"); } });
  el.querySelector("[data-resume]")?.addEventListener("click", async () => { try { await api(`/api/ops/runs/${encodeURIComponent(runId)}/resume`, { method: "POST", body: {} }); renderRun(runId, el); } catch (err) { toast(err.message, "error"); } });
  el.querySelectorAll("[data-choice]").forEach((b) => b.addEventListener("click", async () => { try { await api(`/api/ops/runs/${encodeURIComponent(runId)}/resume`, { method: "POST", body: { choice: Number(b.dataset.choice) } }); renderRun(runId, el); } catch (err) { toast(err.message, "error"); } }));
}

// ---- 2. Connect & Learn -------------------------------------------------------------------------------------------
function watchingHtml() {
  if (!state.watching.length) return '<p class="muted">Not learning right now.</p>';
  return state.watching.map((w) => {
    const app = state.apps.find((a) => a.id === w.application_id);
    const s = w.stats || {};
    return `<div class="row" style="margin:6px 0">${pill("INFO", "Learning")} <b>${esc(app?.name || "")}</b>
      <span class="muted">${s.pages ?? s.snapshots ?? 0} page views, ${s.actions ?? s.events ?? 0} actions seen</span><span class="spacer"></span>
      <button class="btn" data-stopwatch="${esc(w.session_id)}">Stop learning</button></div>`;
  }).join("");
}
function bindWatching(root) {
  root.querySelectorAll("[data-stopwatch]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      const out = await api(`/api/ops/simple/learn/${encodeURIComponent(b.dataset.stopwatch)}/stop`, { method: "POST" });
      const n = out.trusted?.promoted?.length || 0;
      toast(`Learning saved${n ? `: ${n} new things I can now use` : ""}.`, "success");
      await loadStatus(); show("connect");
    } catch (err) { toast(err.message, "error"); b.disabled = false; }
  }));
}

async function connectTab(body) {
  body.innerHTML = `
    <form class="card settings" id="connForm" autocomplete="off"><h3>Connect a website</h3>
      <label>Website address <input id="cUrl" type="url" required maxlength="500" placeholder="http://transport.intranet.local/"></label>
      <label>Name <input id="cName" maxlength="80" placeholder="Suggested automatically"></label>
      <label>Browser <select id="cBrowser"><option value="msedge">Microsoft Edge</option><option value="chrome">Google Chrome</option></select></label>
      <div class="row"><button class="btn primary" type="submit">Connect and start learning</button></div>
      <p class="muted">A maximized browser window opens on the website. Sign in and work as usual: create, edit, approve, search. I learn the screens, tasks, rules, roles and statuses by myself. Passwords and codes are never captured. Stop learning when you are done (closing the window also stops it).</p>
    </form>
    <div class="card" style="margin-top:12px"><h3>Learning now</h3><div id="watchBox">${watchingHtml()}</div></div>
    <div class="card" style="margin-top:12px"><h3>Connected websites</h3>
      ${table(["Website", "Address", ""], state.apps.map((a) => `<tr><td>${esc(a.name)}</td><td>${esc(a.base_url)}</td><td>
        <button class="btn" data-learn="${esc(a.base_url)}">Start learning</button>
        <button class="btn" data-explore="${esc(a.id)}" title="Opens the website and looks at menus, pages, forms and tables without submitting anything">Explore automatically (read-only)</button></td></tr>`))}</div>`;
  const url = body.querySelector("#cUrl"), name = body.querySelector("#cName");
  url.addEventListener("input", () => {
    try {
      const u = new URL(url.value);
      const parts = u.hostname.split(".").filter((p) => p && !["www", "intranet", "app", "apps", "portal", "web"].includes(p));
      name.placeholder = /^[\d.]+$/.test(u.hostname) || u.hostname === "localhost" ? "Suggested automatically" : (parts[0] || "").replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) || "Suggested automatically";
    } catch { name.placeholder = "Suggested automatically"; }
  });
  const go = async (u, n, browser) => {
    try {
      const out = await api("/api/ops/simple/connect", { method: "POST", body: { url: u, name: n || null, learn: true, browser } });
      state.app = out.application.id;
      toast(`${out.application.name}: ${out.session?.reused ? "already learning in an open window" : "a browser window is opening. Use the website normally."}`, "success", 7000);
      await loadStatus(); show("connect");
    } catch (err) { toast(err.message, "error"); }
  };
  body.querySelector("#connForm").addEventListener("submit", (e) => { e.preventDefault(); go(url.value.trim(), name.value.trim(), body.querySelector("#cBrowser").value); });
  body.querySelectorAll("[data-learn]").forEach((b) => b.addEventListener("click", () => go(b.dataset.learn, null, body.querySelector("#cBrowser").value)));
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
  body.innerHTML = `
    <div class="card"><div class="row">${appPicker()}<span class="spacer"></span><span class="muted">${k.journal?.observations ?? ""} observations · ${k.records} records seen</span></div></div>
    <div class="card" style="margin-top:8px"><h3>Tasks I can do</h3>
      ${table(["Task", "Who does it", "Fields", "Status", ""], k.tasks.map((t) => `<tr><td><b>${esc(t.title)}</b>${t.consequential ? ' <span class="muted">(asks for confirmation)</span>' : ""}</td><td>${esc((t.roles || []).join(", "))}</td><td>${esc(t.fields.join(", "))}</td><td>${certainty(t)}</td>
        <td>${canEdit() ? `<button class="link-btn" data-forget="${t.id}">This is wrong</button>` : ""}</td></tr>`))}
      <p class="muted">Verified: I saw it done successfully at least twice. Uncertain: seen once; I still show the preview before doing it.</p></div>
    <div class="card" style="margin-top:8px"><h3>Rules the application enforces</h3>
      ${table(["Rule", "Evidence"], k.rules.map((r) => `<tr><td>${esc(r.rule)}</td><td>${esc(typeof r.evidence === "string" ? r.evidence : JSON.stringify(r.evidence)).slice(0, 200)}</td></tr>`))}
      ${k.suggestions.length ? `<h4>Suggestions (not used until you confirm)</h4>${table(["Suggestion", "Confidence", ""], k.suggestions.map((s) => `<tr><td>${esc(s.rule)}</td><td>${Math.round((s.confidence || 0) * 100)}%</td><td>${canEdit() ? `<button class="link-btn" data-trust="${s.id}">Use it</button> · <button class="link-btn" data-forget="${s.id}">Wrong</button>` : ""}</td></tr>`))}` : ""}</div>
    <div class="grid two" style="margin-top:8px">
      <div class="card"><h3>Roles</h3>${table(["Role", "Users seen", "Does"], (k.roles || []).map((r) => `<tr><td>${esc(r.name || r.key)}</td><td>${esc((r.users || []).join(", "))}</td><td>${esc((r.performed || []).map((p) => p.flow).join(", "))}</td></tr>`))}</div>
      <div class="card"><h3>Status flow</h3>${(k.lifecycles || []).map((lc) => `<p><b>${esc(lc.entity)}</b>: ${esc((lc.statuses || []).join(" → "))}</p>${(lc.transitions || []).map((t) => `<p class="muted">${esc(t.from)} → ${esc(t.to)} by ${esc(t.action)} (${esc((t.roles || []).join(", "))})</p>`).join("")}`).join("") || '<p class="muted">No status column seen yet.</p>'}</div>
    </div>
    <div class="card" style="margin-top:8px"><h3>Sign-in accounts</h3>
      <p class="muted">Created from the sign-ins I saw. Passwords are kept in Windows Credential Manager on this PC, never in the learning data.</p>
      ${table(["Account", "Role", "Password", ""], k.accounts.map((a) => `<tr><td>${esc(a.label)}</td><td>${esc(a.verified_role || a.role_key || "")}</td><td>${a.has_credential ? pill("PASS", "Saved") : pill("INFO", "Not saved")}</td><td>${canEdit() ? `<button class="link-btn" data-cred="${esc(a.id)}" data-label="${esc(a.label)}">${a.has_credential ? "Replace" : "Save password"}</button>` : ""}</td></tr>`))}
      <div id="credBox"></div></div>`;
  bindPicker(body, () => knowledgeTab(body));
  body.querySelectorAll("[data-forget]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/ops/simple/knowledge/${b.dataset.forget}/forget`, { method: "POST", body: {} }); knowledgeTab(body); } catch (err) { toast(err.message, "error"); }
  }));
  body.querySelectorAll("[data-trust]").forEach((b) => b.addEventListener("click", async () => {
    try { await api(`/api/ops/simple/knowledge/${b.dataset.trust}/trust`, { method: "POST", body: {} }); knowledgeTab(body); } catch (err) { toast(err.message, "error"); }
  }));
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
      ${table(["Task", "Status", "Completed", "Failed", "Skipped", "Uncertain", "By", "Started", ""], tasks.map((t) => `<tr><td>${esc(t.title)}</td><td>${esc((t.status || "").replace(/_/g, " "))}</td>
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
