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
    await ({ chat, run, apps, learn, understand, knowledge, audit }[tab])();
  } catch (e) {
    main.innerHTML = `<div class="card"><p class="err">${esc(e.message)}</p></div>`;
  }
}

// ---- chat and voice ----------------------------------------------------------------------------------------------
async function chat() {
  main.innerHTML = `<div class="card"><div class="row"><label style="flex:1">Application ${appSelect()}</label></div>
    <div class="chat" id="chatLog"></div>
    <div class="suggest" id="suggest"></div>
    <label>Request<textarea id="say" placeholder="Type or speak a request"></textarea></label>
    <div class="row"><button class="btn primary" id="sendBtn">Send</button><button class="btn" id="micBtn">Speak</button></div>
    <p class="err" id="chatErr"></p></div>`;
  $("#appSel").onchange = (e) => { S.appId = e.target.value; suggestions(); };
  renderChat();
  suggestions();
  S.poll = setInterval(suggestions, 8000);
  $("#sendBtn").onclick = sendChat;
  $("#say").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) sendChat(); });
  $("#micBtn").onclick = voice;
}

// 1.3.0: suggestions come from what was learned for this application, the page open in the active tab, records
// waiting for a step and recent requests; view-only accounts get reading and navigation only. No fixed list.
async function suggestions() {
  const box = $("#suggest");
  if (!box || !S.appId) { if (box) box.innerHTML = ""; return; }
  let out;
  try {
    const { url } = await send({ type: "active-url" });
    out = await api.get(`/v1/simple/suggestions?app_id=${encodeURIComponent(S.appId)}${url ? `&page=${encodeURIComponent(url)}` : ""}${S.conversation ? `&conversation=${encodeURIComponent(S.conversation)}` : ""}`);
  } catch (e) { return; }
  const items = out.suggestions || [];
  const html = items.length ? items.map((s) => `<button class="chip" data-say="${esc(s.text)}" title="${esc(s.why || "")}">${esc(s.text)}</button>`).join("")
    : `<span class="muted">${out.learned ? "Nothing to suggest right now." : "Nothing learned for this application yet: open Learn and press Start learning."}</span>`;
  if (box.dataset.sig === html) return;
  box.dataset.sig = html;
  box.innerHTML = html;
  box.querySelectorAll("[data-say]").forEach((b) => b.onclick = () => { $("#say").value = b.dataset.say; $("#say").focus(); });
}

function renderChat() {
  const log = $("#chatLog");
  if (!log) return;
  log.innerHTML = (S.messages.length ? S.messages : [{ who: "agent", text: "Hello. Choose the application, then tell me what to do in it. Every change shows a short preview first and runs only after you confirm." }])
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
    const spoken = S.voiceSource;
    const out = await api.post("/v1/chat", { text, conversation: S.conversation || undefined, app_id: S.appId || undefined, source: S.voiceSource ? "voice" : "typed" });
    S.voiceSource = false;
    S.conversation = out.conversation;
    const r = out.reply;
    let actions = "";
    if (r.type === "secret_intercepted") actions = `<div class="row" style="margin-top:6px"><button class="btn" data-act="apps">Open Applications &amp; Accounts</button></div>`;
    if (r.type === "suggest_discovery" || r.type === "suggest_watch") actions = `<div class="row" style="margin-top:6px"><button class="btn" data-act="learn">Open Learn</button></div>`;
    if (r.type === "need_credential" && r.account_id) actions = `<div class="row" style="margin-top:6px"><button class="btn primary" data-act="credential" data-account="${esc(r.account_id)}">Save the password securely</button></div>`;
    if (r.type === "records" && (r.keys || []).length) actions = `<div class="muted" style="margin-top:4px">${esc(r.keys.slice(0, 50).join(", "))}</div>`;
    if (r.type === "run" && r.consequential) actions = `<div class="muted" style="margin-top:4px">This changes records: you will tick a confirmation box under the preview.</div>`;
    if (r.type === "needs_approval") { S.pendingApproval = { id: r.entry_id, text }; actions = `<div class="row" style="margin-top:6px"><button class="btn primary" data-act="approve-run">Approve and run</button><button class="btn" data-act="knowledge">Review it first</button></div>`; }
    if (r.type === "clarify" && (r.missing || []).length) actions = `<div class="muted" style="margin-top:4px">Missing information: ${esc(r.missing.join(", "))}</div>`;
    if (r.type === "need_credential" && r.security_notice) actions += `<div class="muted" style="margin-top:4px">${esc(r.security_notice)}</div>`;
    if (r.type === "clarify" && (r.options || []).length) actions += `<div class="row" style="margin-top:6px">${r.options.slice(0, 7).map((o) => `<button class="chip" data-act="say" data-say="${esc(o)}">${esc(o)}</button>`).join("")}</div>`;  // 1.4.3: the choices as buttons
    if (out.translated_to) actions += `<div class="muted" style="margin-top:4px">Understood as: ${esc(out.translated_to)}</div>`;  // SkyGuard 23.13.0: Arabic commands
    if (r.type === "navigate" && r.url) {
      const opened = await send({ type: "open-url", url: r.url });
      actions += `<div class="muted" style="margin-top:4px">${opened.ok ? "Opened in the browser." : esc(opened.error || "Could not open it.")} ${esc(r.url)}</div>`;
    }
    S.messages.push({ who: "agent", text: r.text, warn: r.type === "secret_intercepted" || r.type === "error", actions });
    if (spoken) sayReply(r);
    if (r.type === "run" && r.run_id && !r.duplicate) { S.runId = r.run_id; await send({ type: "drive", runId: r.run_id }); setTimeout(() => show("run"), 600); }
    else if (r.type === "run" && r.run_id) { S.runId = r.run_id; setTimeout(() => show("run"), 600); }
    suggestions();
  } catch (e) {
    S.messages.push({ who: "agent", warn: true, text: e.message });
  }
  renderChat();
}

// ---- spoken replies (1.4.2): English with the PC's installed voices, Arabic with the offline Arabic voice ----
const ARABIC = /[\u0600-\u06FF]/;
const MALE = /david|mark|guy|george|james|richard|ryan|daniel|naayf|hamed|male\b/i;
const FEMALE = /zira|hazel|susan|aria|jenny|eva|hoda|salma|zariyah|female/i;
let playing = null;

function localVoice(lang, gender) {
  const all = ("speechSynthesis" in window ? speechSynthesis.getVoices() : []).filter((v) => v.localService && v.lang.toLowerCase().startsWith(lang));
  const want = gender === "female" ? FEMALE : MALE;
  return all.find((v) => want.test(v.name)) || (lang === "ar" ? all[0] : all.find((v) => !(gender === "female" ? MALE : FEMALE).test(v.name))) || null;
}

function browserSay(text, lang, gender) {
  const v = localVoice(lang, gender);
  if (!v) return false; // never an English voice for Arabic text
  const u = new SpeechSynthesisUtterance(text);
  u.voice = v; u.lang = v.lang;
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
  return true;
}

async function sayReply(r) {
  const text = (r.speak || r.text || "").trim();
  if (!text) return;
  const { speak_replies, reply_gender } = await chrome.storage.local.get({ speak_replies: true, reply_gender: "male" });
  if (speak_replies === false) return;
  const gender = reply_gender === "female" ? "female" : "male";
  if (!ARABIC.test(text)) { browserSay(text, "en", gender); return; }
  try {
    const blob = await api.speakArabic(text, gender);
    playing?.pause();
    const url = URL.createObjectURL(blob);
    playing = new Audio(url);
    playing.onended = () => URL.revokeObjectURL(url);
    await playing.play();
  } catch (e) {
    if (!browserSay(text, "ar", gender)) toast($("#chatErr"), e.message || "No Arabic voice is installed; the reply is on screen.");
  }
}

async function chatAction(act, data) {
  if (act === "credential") { chrome.windows.create({ url: chrome.runtime.getURL(`credential.html?account=${encodeURIComponent(data.account)}`), type: "popup", width: 460, height: 560 }); return; }
  if (act === "say") { $("#say").value = data.say; return sendChat(); }
  if (act !== "approve-run") return show(act);
  const p = S.pendingApproval;
  if (!p) return;
  S.pendingApproval = null;
  try {
    await api.post(`/v1/knowledge/${p.id}/approve`, { note: "approved from chat" });
    $("#say").value = p.text;
    await sendChat();
  } catch (e) { S.messages.push({ who: "agent", warn: true, text: e.message }); renderChat(); }
}

// ---- understanding (23.7.0) ----------------------------------------------------------------------------------------
async function understand() {
  if (!S.appId) { main.innerHTML = `<div class="card"><p class="muted">Register an application first.</p></div>`; return; }
  const v = await api.get(`/v1/understanding?app_id=${encodeURIComponent(S.appId)}`);
  const { auto_learn } = await api.settings();
  const m = v.model, j = v.journal || {};
  const list = (items) => items.length ? `<div class="list">${items.join("")}</div>` : "<p class='muted'>Nothing yet.</p>";
  main.innerHTML = `<div class="card"><label>Application ${appSelect()}</label>
    <p class="muted">${auto_learn ? "Learning automatically while you browse." : "Automatic learning is off: turn it on in Options."} ${j.pages || 0} pages and ${j.events || 0} actions observed${v.pending ? `, ${v.pending} not analysed yet` : ""}.</p>
    <div class="row"><button class="btn" id="uNow">Analyse now</button></div><p class="err" id="uErr"></p></div>
    ${m ? `<div class="card"><h2>Tasks</h2>${list((m.flows || []).map((f) => `<div><strong>${esc(f.title)}</strong> <span class="muted">${esc(f.screen_name || "")} · ${f.successes}✔${f.failures ? ` ${f.failures}✖` : ""}${(f.roles || []).length ? " · " + esc(f.roles.join(", ")) : ""}</span><br><span class="muted">${esc((f.parameters || []).map((p) => p.field).join(", "))}</span></div>`))}</div>
    <div class="card"><h2>Statuses</h2>${list((m.lifecycles || []).map((lc) => `<div><strong>${esc(lc.entity)}</strong>: ${lc.statuses.map(esc).join(" → ")}<br><span class="muted">${lc.transitions.map((t) => `${esc(t.from)}→${esc(t.to)}: ${esc(t.action || "?")}${t.roles.length ? " (" + esc(t.roles.join(", ")) + ")" : ""}`).join("; ")}</span></div>`))}</div>
    <div class="card"><h2>Rules</h2>${list((m.rules || []).map((r) => `<div>${esc(r.text)} <span class="muted">${esc(r.flow)} · ${esc(r.source)}</span></div>`))}</div>
    <div class="card"><h2>Roles</h2>${list((m.roles || []).map((r) => `<div><strong>${esc(r.name)}</strong> <span class="muted">${esc(r.performed.map((p) => p.flow).join(", ") || "viewing")}${r.refused.length ? " · refused: " + esc(r.refused.join("; ")) : ""}</span></div>`))}</div>
    <div class="card"><h2>Screens</h2>${list((m.screens || []).map((s) => `<div>${esc(s.name)} <span class="muted">${esc(s.path)} · ${esc(s.type)}</span></div>`))}</div>` : ""}`;
  $("#appSel").onchange = (e) => { S.appId = e.target.value; understand(); };
  $("#uNow").onclick = async () => { try { await api.post(`/v1/understanding/${encodeURIComponent(S.appId)}/digest`, {}); understand(); } catch (e) { toast($("#uErr"), e.message); } };
}

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
  let v, ev, sv;
  try { [v, ev, sv] = await Promise.all([api.get(`/v1/runs/${S.runId}`), api.get(`/v1/runs/${S.runId}/events`), api.get(`/v1/simple/runs/${S.runId}`).then((x) => x.simple).catch(() => null)]); }
  catch (e) { if (!quiet) toast($("#runErr"), e.message); return; }
  const r = v.run, st = v.status, p = r.preview || {};
  // re-render only when something changed, so buttons stay put while the person reaches for them; the
  // confirmation box keeps its tick across re-renders (1.3.0)
  const ackKey = `${r.id}:${r.preview_hash || ""}`;
  S.ack = S.ack || {};
  const box = $("#ackBox");
  if (box && box.dataset.key) S.ack[box.dataset.key] = box.checked;
  const sig = JSON.stringify([r.id, r.status, r.preview_hash, v.items.map((i) => i.status), ev.events.length, st.pause]);
  if (quiet && sig === S.runSig) return;
  S.runSig = sig;
  const pause = st.pause;
  const final = ["completed", "partial", "failed", "stopped", "cancelled"].includes(r.status);
  statusBar(st, r);
  const s = sv || {};
  const rows = (s.records || []).map((rec) => Object.entries(rec).map(([k, val]) => `<tr><th>${esc(k)}</th><td>${esc(val)}</td></tr>`).join("")).join("<tr><td colspan='2'><hr></td></tr>");
  const preview = r.status === "awaiting_confirmation" && p.records ? `<h2>Preview (nothing has been submitted)</h2>
    <table><tr><th>Action</th><td>${esc(s.action || v.plan.stages.map((x) => x.title).join(" → "))}</td></tr><tr><th>Application</th><td>${esc(s.application || "")}</td></tr>
    ${rows}<tr><th>Role</th><td>${esc(s.role || "-")}${s.account ? ` (${esc(s.account)})` : ""}</td></tr><tr><th>Status</th><td>${esc(s.status_text || r.status)}</td></tr></table>
    ${(s.missing || []).length ? `<p class="err">Missing information: ${esc(s.missing.join(", "))}</p>` : ""}
    ${!s.can_confirm && (s.blocked_reasons || []).length ? `<p class="err">${esc(s.blocked_reasons.join("; "))}</p>` : ""}
    ${s.can_confirm ? `${s.consequential ? `<label style="display:block;margin-top:6px"><input type="checkbox" id="ackBox" data-key="${esc(ackKey)}"${S.ack[ackKey] ? " checked" : ""}> I have read the preview. These ${esc(s.ready)} record(s) will be changed in the application.</label>` : ""}
      <div class="row" style="margin-top:8px"><button class="btn primary" id="confirmBtn">Confirm &amp; Execute</button><button class="btn" id="editBtn">Edit</button><button class="btn danger" id="cancelBtn">Cancel</button></div>`
    : `<div class="row" style="margin-top:8px"><button class="btn primary" id="editBtn">Provide missing data</button><button class="btn" id="editBtn2">Edit request</button><button class="btn danger" id="cancelBtn">Cancel</button></div>`}` : "";
  const controls = final ? "" : `<div class="row">${r.status === "paused" ? `<button class="btn primary" id="resumeBtn">Resume</button>` : `<button class="btn" id="pauseBtn">Pause</button>`}
    <button class="btn danger" id="stopBtn">Stop</button><button class="btn" id="cancelRestBtn">Cancel remaining</button>${pause ? `<button class="btn" id="ackBtn">Acknowledge</button>` : ""}</div>`;
  body.innerHTML = `${pause ? `<div class="card" style="border-color:var(--warn)"><strong>Paused:</strong> ${esc(pause.message)}<br><span class="muted">The browser stays open. Complete the step yourself, then press Resume; I will verify and continue.</span></div>` : ""}
    ${preview}${controls}
    ${final ? `<p><strong>${esc(r.status)}</strong>: ${esc((r.summary || {}).message || "")}</p>` : ""}
    <h2>Records</h2><table><tr><th>#</th><th>Result</th><th>Reason</th></tr>${v.items.map((i) => `<tr><td>${i.seq}</td><td>${esc(STATUS_TEXT[i.status] || i.status)}</td><td>${esc((i.result || {}).reason || "")}</td></tr>`).join("")}</table>
    <h2>Activity</h2><div class="list">${ev.events.slice(-40).reverse().map((e) => `<div><span class="muted">${esc(e.at.slice(11, 19))}</span> ${esc(e.message)}</div>`).join("")}</div>`;
  const on = (id, fn) => { const b = $(id); if (b) b.onclick = async () => { try { await fn(); await renderRun(); } catch (e) { toast($("#runErr"), e.message); } }; };
  on("#confirmBtn", async () => {
    const consequential = s.consequential ?? v.consequential;
    if (consequential && !$("#ackBox")?.checked) throw new Error("Tick the confirmation box after reading the preview.");
    $("#confirmBtn").disabled = true; // one press, one execution
    await api.post(`/v1/runs/${r.id}/confirm`, { preview_hash: r.preview_hash, acknowledge: consequential ? true : undefined, app_id: r.application_id });
    await send({ type: "drive", runId: r.id });
  });
  const edit = async () => {
    await api.post(`/v1/runs/${r.id}/stop`);
    S.appId = r.application_id;
    await show("chat");
    $("#say").value = (s.request_text || "") + ((s.missing || []).length ? `; ${s.missing[0]}: ` : "");
    $("#say").focus();
  };
  on("#editBtn", edit);
  on("#editBtn2", edit);
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
  if ((msg.type === "learned" || msg.type === "learning-status") && S.tab === "learn" && !$("#lwStop:disabled")) {
    clearTimeout(S.learnRefresh);
    S.learnRefresh = setTimeout(() => { if (S.tab === "learn") learn().catch(() => {}); }, 1500);
  }
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
  const { auto_learn } = await api.settings();
  const cmp = await send({ type: "cmp-record-status" });
  const ls = S.appId ? await send({ type: "learning-status", appId: S.appId }) : { learning: false };
  if (S.tab !== "learn") return; // the person moved to another tab while this was loading
  main.innerHTML = `<div class="card"><h2>Learn while I browse</h2><label>Application ${appSelect("learnApp")}</label>
    ${ls.learning ? `<p><span class="pill ${ls.offline ? "warn" : "ok"}">${ls.offline ? "Companion unreachable: keeping your actions and retrying" : "Learning"}</span> since ${esc(new Date(ls.since).toLocaleTimeString())}: ${esc(ls.events)} action(s) and ${esc(ls.pages)} page(s) sent${ls.queued ? `, ${esc(ls.queued)} waiting` : ""}.</p>
      <p class="muted">Use the application normally in its tab. Passwords and codes are never recorded.</p>
      <div class="row"><button class="btn primary" id="lwStop">Stop &amp; save learning</button></div>`
    : `<p>Press Start, then use the application normally: I watch only, never click or type, and work out pages, tasks, statuses, rules and roles. ${auto_learn ? "(Automatic learning is also on in Options.)" : ""}</p>
      <div class="row"><button class="btn primary" id="lwStart"${S.appId ? "" : " disabled"}>Start learning</button><button class="btn" data-goto="understand">What I understood</button><button class="btn" id="openOpts">Options</button></div>`}
    <p class="err" id="lwErr"></p></div>
    <div class="card"><h2>Connect an application</h2><label>Address<input id="cUrl" placeholder="https://trips.example.local/"></label><label>Name (optional)<input id="cName" placeholder="suggested from the address"></label>
    <button class="btn" id="cBtn">Connect</button><p class="muted">Then open it and use it normally; I learn while you browse.</p></div>
    ${cmpCard(cmp)}
    <div class="card"><h2>Guided learning</h2><label>Application ${appSelect()}</label>
    ${active ? `<p>Recording <strong>${esc(active.title)}</strong> (${esc(active.events)} actions so far). Perform the task normally in the application tab.</p>
      <div class="row"><button class="btn primary" id="finish">Finish and review</button><button class="btn" id="cancelL">Cancel</button></div>`
    : `<label>Account ${accSel}</label><label>What are you teaching?<input id="lTitle" placeholder="Create trip"></label><button class="btn primary" id="startL">Start recording</button>`}
    <p class="muted">Passwords and codes you type are never recorded. What I learn is saved as a draft for your approval.</p><p class="err" id="lErr"></p></div>
    <div class="card"><h2>Automatic discovery (read-only)</h2><label>Account ${accSel.replace('id="lAcc"', 'id="dAcc"')}</label><label>Up to screens<input id="dMax" type="number" min="1" max="200" value="25"></label>
    <button class="btn" id="discBtn">Start discovery</button><p class="muted">Opens screens and reads them; it never submits anything.</p></div>
    <div class="card"><h2>Recent sessions</h2><div class="list">${sessions.slice(0, 10).map((s) => `<div>${esc(s.title)} - ${esc(s.status)}</div>`).join("") || "<p class='muted'>None.</p>"}</div></div>`;
  $("#appSel").onchange = (e) => { S.appId = e.target.value; learn(); };
  $("#learnApp").onchange = (e) => { S.appId = e.target.value; learn(); };
  if ($("#openOpts")) $("#openOpts").onclick = () => chrome.runtime.openOptionsPage();
  if (main.querySelector("[data-goto]")) main.querySelector("[data-goto]").onclick = () => show("understand");
  const err = (e) => toast($("#lErr"), e.message);
  const lwErr = (e) => toast($("#lwErr"), e.message || e);
  if ($("#lwStart")) $("#lwStart").onclick = async () => {
    // the site permission can only be asked for in this click
    const app = S.apps.find((a) => a.id === S.appId);
    if (app && !(await chrome.permissions.contains({ origins: app.origins.map((o) => o + "/*") }))
        && !(await chrome.permissions.request({ origins: app.origins.map((o) => o + "/*") }).catch(() => false))) return lwErr("Learning needs access to this application's site.");
    const r = await send({ type: "learning-start", appId: S.appId });
    if (!r.ok) return lwErr(r.error);
    learn();
  };
  if ($("#lwStop")) $("#lwStop").onclick = async () => {
    try {
      $("#lwStop").disabled = true;
      const r = await send({ type: "learning-stop", appId: S.appId });
      const out = await api.post(`/v1/simple/learn/${encodeURIComponent(S.appId)}/save`, {});
      const vf = out.verified || {};
      await learn();
      main.insertAdjacentHTML("afterbegin", `<div class="card"><h2>${vf.ok ? "Learning saved and tested" : "Learning saved, not all of it can be used yet"}</h2>
        <p>${esc(r.events || 0)} action(s) observed in this session. ${esc(vf.tasks || 0)} task(s) and ${esc(vf.pages || 0)} page(s) can be retrieved by a command${vf.checked ? ` (${esc(vf.checked - (vf.failed || []).length)} of ${esc(vf.checked)} checks passed)` : ""}.</p>
        ${(vf.failed || []).length ? `<p class="err">Not retrievable yet: ${esc(vf.failed.map((f) => f.name).join(", "))}</p>` : ""}</div>`);
    } catch (e) { lwErr(e); }
  };
  if ($("#startL")) $("#startL").onclick = async () => { try { const { session } = await api.post("/v1/learning", { application_id: S.appId, account_id: $("#lAcc").value || undefined, title: $("#lTitle").value.trim() || "Workflow" }); await send({ type: "learn-start", appId: S.appId, sessionId: session.id }); learn(); } catch (e) { err(e); } };
  if ($("#finish")) $("#finish").onclick = async () => { try { await send({ type: "learn-stop", sessionId: active.id }); const out = await api.post(`/v1/learning/${active.id}/finish`); main.insertAdjacentHTML("afterbegin", `<div class="card"><h2>Learned (awaiting your approval)</h2><p>Workflow "${esc(out.workflow.key)}" with ${esc((out.workflow.data.steps || []).length)} step(s) and ${esc((out.rules || []).length)} suggested rule(s) were added to Knowledge for your review.${out.profile_learned ? " The sign-in screen was learned too." : ""}</p></div>`); setTimeout(() => show("knowledge"), 1500); } catch (e) { err(e); } };
  if ($("#cancelL")) $("#cancelL").onclick = async () => { await send({ type: "learn-stop", sessionId: active.id }); await api.post(`/v1/learning/${active.id}/cancel`); learn(); };
  $("#cBtn").onclick = async () => {
    try {
      const out = await api.post("/v1/simple/connect", { url: $("#cUrl").value.trim(), name: $("#cName").value.trim() || undefined });
      S.appId = out.application.id;
      main.insertAdjacentHTML("afterbegin", `<div class="card"><p>${out.created ? "Connected" : "Already connected"}: <strong>${esc(out.application.name)}</strong>. Grant access to its site under Applications, then use it normally.</p></div>`);
      setTimeout(() => show("apps"), 1500);
    } catch (e) { err(e); }
  };
  bindCmp(err);
  $("#discBtn").onclick = async () => { try { const v = await api.post("/v1/discovery", { application_id: S.appId, account_id: $("#dAcc").value || undefined, max_pages: Number($("#dMax").value) || 25 }); S.runId = v.run.id; await send({ type: "drive", runId: v.run.id }); show("run"); } catch (e) { err(e); } };
}

// ---- recording for SkyGuard's Integration Comparison (read-only: the way to a report, saved as a file) -----------
function cmpCard(cmp) {
  const on = cmp && cmp.recording;
  return `<div class="card"><h2>Record for Integration Comparison</h2>
    ${on ? `<p>Recording in this tab: ${esc(cmp.events)} action(s). Sign in, open the menus and the report, set its filters, go to a next page and open one detail record if you need them.</p>
      <div class="row"><button class="btn primary" id="cmpStop">Stop and save file</button><button class="btn" id="cmpCancel">Cancel</button></div>`
    : `<p>Records the way to a report in the active tab, for SkyGuard's Integration Comparison (Connect to Application, <em>Import from browser extension</em>).</p>
      <button class="btn" id="cmpStart">Start recording in the active tab</button>`}
    <p class="muted">Only reads: clicks that change data (Save, Delete, Approve...) and sign-in steps are left out; passwords are never recorded.</p><p class="err" id="cmpErr"></p></div>`;
}

function bindCmp() {
  const err = (e) => toast($("#cmpErr"), e.message || e);
  if ($("#cmpStart")) $("#cmpStart").onclick = async () => {
    // asked here, in the click: a site permission can only be requested from a user gesture
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    const origin = tab ? originOf(tab.url || "") : "";
    if (origin && /^https?:/.test(origin) && !(await chrome.permissions.request({ origins: [origin + "/*"] }).catch(() => false))) return err("Recording needs access to this site.");
    const r = await send({ type: "cmp-record-start" });
    if (!r.ok) return err(r.error);
    learn();
  };
  if ($("#cmpCancel")) $("#cmpCancel").onclick = async () => { await send({ type: "cmp-record-stop" }); learn(); };
  if ($("#cmpStop")) $("#cmpStop").onclick = async () => {
    try {
      const r = await send({ type: "cmp-record-stop" });
      if (!r.ok) throw new Error(r.error);
      const out = await api.post("/v1/comparison/recording", { events: r.events, start_url: r.start_url, final_url: r.final_url, tables: r.tables });
      const rec = out.recording;
      const blob = new Blob([JSON.stringify(rec, null, 1)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      let host = "report"; try { host = new URL(r.start_url).hostname.replace(/[^\w.-]/g, "_"); } catch (e) { /* keep */ }
      a.download = `skyguard-navigation-${host}.json`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      await learn();
      main.insertAdjacentHTML("afterbegin", `<div class="card"><h2>Recording saved</h2><p>${rec.steps.length} step(s), ${rec.filters.length} filter(s)${rec.pagination_seen ? ", pagination noticed (SkyGuard reads every page)" : ""}${rec.detail ? `, detail page ${esc(rec.detail.url_template)}` : ""}. ${rec.dropped.length} action(s) left out.</p>
        <p class="muted">In SkyGuard: Integration Comparison, the application's Connect dialog, <em>Import from browser extension</em>, choose ${esc(a.download)}.</p></div>`);
    } catch (e) { err(e); }
  };
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
