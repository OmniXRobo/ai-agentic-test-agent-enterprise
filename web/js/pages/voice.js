// Voice Commands: a cockpit-style comms console to talk to the QA runtime, see what was understood,
// and set up speech recognition and the radio voice.

import { api } from "../api.js";
import { esc, toast } from "../util.js";
import { voice, onVoice, conversationId, sendCommand, listen, loadSettings, localVoices, speak, prepare, ACK } from "../voice.js";

const EXAMPLES = [
  "Create the test case", "Create the automation test case", "Do JMeter performance test",
  "Open user story 118", "Generate automation for test case 1280", "Play test case 1280",
  "Run all automated tests", "Run a 100 user load test for checkout for five minutes", "Show failed tests",
  "Analyze the failure", "Create defects for failed tests", "Show performance report", "Stop the running test",
  "What is running", "Open TFS connection settings",
];
const ENGINES = {
  builtin: ["Built-in offline recognizer (recommended)", "Speech is recognized on this PC by the bundled offline model. No Internet and no speech server are needed."],
  local: ["Local speech server", "A whisper.cpp or OpenAI-compatible speech server on this PC or your network turns speech into text. Nothing leaves your network."],
  browser: ["Browser speech recognition", "Chrome and Edge send the audio to their online service, so this needs Internet. Do not use it on offline PCs."],
  off: ["Off (typed commands only)", "The microphone is not used. Type commands below."],
};
const KIND_CLASS = { failed: "FAIL", unsupported: "FAIL", needs_confirmation: "WARN", needs_input: "WARN", accepted: "RUNNING", progress: "RUNNING" };
const STATUS = {
  idle: ["STANDBY", "Ready to receive. Press the mic, then speak."],
  listening: ["RECEIVING", "Speak now. Press again to stop."],
  thinking: ["PROCESSING", "Analyzing your command…"],
  speaking: ["TRANSMITTING", "Agent on the radio."],
};
const BARS = 28;

let off = null;
let clock = null;

function logHtml() {
  if (!voice.log.length) return '<p class="ck-empty">No traffic yet. Press the mic and give an order, or type one below. Replies come back over the radio and are logged here.</p>';
  return voice.log.map((m) => `<div class="vmsg ${m.who}"><span class="vwho">${m.who === "you" ? "COMMAND" : "AGENT"}</span>
    <span class="vtext">${esc(m.text)}</span>${m.kind && KIND_CLASS[m.kind] ? `<span class="pill ${KIND_CLASS[m.kind]}">${esc(m.kind.replace("_", " "))}</span>` : ""}</div>`).join("");
}

function settingsHtml(s, radio) {
  const voices = localVoices();
  return `
    <form class="settings" id="vset" style="max-width:none">
      <h3>Speech recognition</h3>
      <label>Engine <select name="stt_engine">${Object.entries(ENGINES).map(([k, [l]]) => `<option value="${k}" ${k === s.stt_engine ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
      <p class="muted" id="engineHelp">${esc(ENGINES[s.stt_engine]?.[1] || "")}</p>
      <div data-builtin ${s.stt_engine === "builtin" ? "" : "hidden"}>
        <label>Speech model folder (optional) <input name="stt_model_dir" value="${esc(s.stt_model_dir || "")}" placeholder="bundled model in runtime\\speech"></label>
      </div>
      <div data-local ${s.stt_engine === "local" ? "" : "hidden"}>
        <label>Server type <select name="stt_api">
          <option value="whisper_cpp" ${s.stt_api === "whisper_cpp" ? "selected" : ""}>whisper.cpp server (/inference)</option>
          <option value="openai" ${s.stt_api === "openai" ? "selected" : ""}>OpenAI-compatible (/audio/transcriptions)</option></select></label>
        <label>Server URL <input name="stt_url" value="${esc(s.stt_url)}" placeholder="${s.stt_api === "openai" ? "http://127.0.0.1:8000/v1" : "http://127.0.0.1:8081"}"></label>
        <label data-openai ${s.stt_api === "openai" ? "" : "hidden"}>Model name <input name="stt_model" value="${esc(s.stt_model)}" placeholder="as the server lists it"></label>
      </div>
      <label>Language <input name="language" value="${esc(s.language)}" maxlength="5" style="width:90px"></label>
      <h3>Spoken replies</h3>
      <label><span><input type="checkbox" name="speak_replies" ${s.speak_replies ? "checked" : ""}> Speak replies aloud</span></label>
      <label>Reply style <select name="reply_style">
        <option value="radio" ${s.reply_style === "radio" ? "selected" : ""}>Pilot radio (squelch, static, roger beep)</option>
        <option value="plain" ${s.reply_style === "plain" ? "selected" : ""}>Plain voice</option></select></label>
      <label>Radio voice (Windows) <select name="radio_voice" ${radio.available ? "" : "disabled"}><option value="">Adult male voice (default)</option>
        ${radio.voices.map((v) => `<option value="${esc(v.name)}" ${v.name === s.radio_voice ? "selected" : ""}>${esc(v.name)} (${esc(v.gender)}, ${esc(v.lang)})</option>`).join("")}</select></label>
      <p class="muted">${radio.available ? `${radio.voices.length} Windows voices found. Radio replies are rendered with them on this PC, so they work offline.` : "No Windows voices here, so the browser voice below is used with radio static around it."}</p>
      <label>Browser voice (fallback) <select name="tts_voice"><option value="">Male voice for the language</option>
        ${voices.map((v) => `<option value="${esc(v.name)}" ${v.name === s.tts_voice ? "selected" : ""}>${esc(v.name)} (${esc(v.lang)})</option>`).join("")}</select></label>
      <label>Speaking rate <input type="number" name="tts_rate" min="0.5" max="2" step="0.1" value="${esc(s.tts_rate)}" style="width:90px"></label>
      <div class="row"><button class="btn primary" type="submit">Save</button><button class="btn" type="button" id="testVoice">Radio check</button>
        <button class="btn" type="button" id="diag">Check voice setup</button></div>
      <div id="diagOut"></div>
    </form>`;
}

function zulu() {
  const d = new Date();
  return `${String(d.getUTCHours()).padStart(2, "0")}${String(d.getUTCMinutes()).padStart(2, "0")}:${String(d.getUTCSeconds()).padStart(2, "0")} ZULU`;
}

async function render(view) {
  const s = await loadSettings(true);
  const radio = await api("/api/voice/radio-voices").catch(() => ({ available: false, voices: [] }));
  try {
    const { items, context } = await api(`/api/voice/commands?conversation_id=${encodeURIComponent(conversationId)}&limit=50`);
    if (!voice.log.length) {
      for (const it of items) {
        voice.log.push({ who: "you", text: it.text });
        if (it.reply) voice.log.push({ who: "agent", text: it.reply, kind: it.status });
        if (it.final_reply) voice.log.push({ who: "agent", text: it.final_reply, kind: it.status === "failed" ? "failed" : null });
      }
    }
    voice.pending = context?.pending || null;
  } catch { /* history is a convenience */ }
  const engine = ENGINES[s.stt_engine]?.[0] || "";
  view.innerHTML = `
    <div class="cockpit">
      <header class="ck-head">
        <div class="ck-callsign"><span class="ck-wing" aria-hidden="true">&#9650;</span> TACTICAL VOICE COMMAND</div>
        <div class="ck-meta"><span>CH 07</span><span>SECURE</span><span>${s.stt_engine === "builtin" || s.stt_engine === "local" ? "OFFLINE LINK" : s.stt_engine === "browser" ? "ONLINE LINK" : "KEYBOARD ONLY"}</span></div>
        <div class="ck-clock" id="ckClock">${zulu()}</div>
      </header>
      <div class="ck-grid">
        <section class="ck-panel ck-scope">
          <div class="radar" id="radar">
            <div class="radar-sweep" aria-hidden="true"></div>
            <button class="mic-btn" id="mic" aria-label="Speak a command"><svg viewBox="0 0 24 24" width="38" height="38" aria-hidden="true"><path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12Z"/></svg></button>
          </div>
          <div class="ck-status"><span class="ck-lamp" id="lamp"></span><strong id="vstatus"></strong></div>
          <div class="ck-hint" id="vhint"></div>
          <div class="ck-meter" id="meter" aria-hidden="true">${"<i></i>".repeat(BARS)}</div>
          <div class="ck-readout"><span>RECOGNITION</span><b>${esc(engine.replace(/ \(.*\)$/, "").toUpperCase())}</b></div>
          <div class="ck-readout"><span>REPLY</span><b>${s.speak_replies ? (s.reply_style === "radio" ? "PILOT RADIO" : "PLAIN VOICE") : "TEXT ONLY"}</b></div>
        </section>
        <section class="ck-panel ck-comms">
          <h3>COMMS LOG</h3>
          <div class="vlog" id="vlog" aria-live="polite"></div>
          <div class="row" id="confirmRow" hidden><button class="btn primary" id="yes">Confirm</button><button class="btn" id="no">Cancel</button></div>
          <form class="row ck-typed" id="typed"><span class="ck-prompt" aria-hidden="true">&gt;</span><input name="text" placeholder="Type an order, for example: create the test case" autocomplete="off" maxlength="500">
            <button class="btn primary" type="submit">Send</button></form>
        </section>
      </div>
      <section class="ck-panel">
        <h3>ORDERS YOU CAN GIVE</h3>
        <div class="chips">${EXAMPLES.map((e) => `<button class="chip" data-say="${esc(e)}">${esc(e)}</button>`).join("")}</div>
        <p class="muted">The agent answers "Roger, sir. Stand by." while it analyzes, then confirms the mission, for example "Copy, sir. Starting test case creation."
          Follow-ups use what you just opened or ran: "open user story 118", then "create the test case"; "run test case 1280", then "run it again". Creating defects in TFS asks for confirmation first.</p>
      </section>
      <details class="ck-panel ck-config"><summary>SYSTEMS CONFIGURATION</summary>${settingsHtml(s, radio)}</details>
    </div>`;

  const bars = [...view.querySelectorAll("#meter i")];
  let shown = { log: -1, status: "", error: null, pending: null };
  const update = () => {
    const state = voice.status !== "idle" ? voice.status : voice.speaking ? "speaking" : "idle";
    const level = state === "listening" ? voice.level : 0;
    bars.forEach((b, i) => {
      const center = 1 - Math.abs(i - (BARS - 1) / 2) / (BARS / 2);
      b.style.transform = `scaleY(${Math.max(0.08, Math.min(1, level * (0.5 + center) * (0.75 + Math.random() * 0.5)))})`;
    });
    const key = { log: voice.log.length, status: state, error: voice.error, pending: voice.pending?.kind || null };
    if (key.log === shown.log && key.status === shown.status && key.error === shown.error && key.pending === shown.pending) return;
    shown = key;
    const [label, hint] = STATUS[state];
    view.querySelector(".cockpit").dataset.state = state;
    view.querySelector("#vstatus").textContent = label;
    view.querySelector("#vhint").textContent = voice.error || (state === "idle" && s.stt_engine === "off" ? "Voice input is off; type your order." : hint);
    const mic = view.querySelector("#mic");
    mic.classList.toggle("on", state === "listening");
    mic.disabled = s.stt_engine === "off" || voice.status === "thinking";
    const log = view.querySelector("#vlog");
    log.innerHTML = logHtml();
    log.scrollTop = log.scrollHeight;
    view.querySelector("#confirmRow").hidden = voice.pending?.kind !== "confirm";
  };
  off?.();
  off = onVoice(update);
  update();
  clearInterval(clock);
  clock = setInterval(() => { const el = view.querySelector("#ckClock"); if (el) el.textContent = zulu(); }, 1000);
  prepare(ACK);

  const run = (fn) => fn().catch((err) => { update(); if (err?.message) toast(err.message, "error"); });
  view.querySelector("#mic").addEventListener("click", () => run(listen));
  view.querySelector("#typed").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = e.target.text.value;
    e.target.text.value = "";
    run(() => sendCommand(text, "typed"));
  });
  view.querySelectorAll("[data-say]").forEach((b) => b.addEventListener("click", () => run(() => sendCommand(b.dataset.say, "typed"))));
  view.querySelector("#yes").addEventListener("click", () => run(() => sendCommand("confirm")));
  view.querySelector("#no").addEventListener("click", () => run(() => sendCommand("cancel")));

  const form = view.querySelector("#vset");
  form.stt_engine.addEventListener("change", () => {
    view.querySelector("#engineHelp").textContent = ENGINES[form.stt_engine.value][1];
    view.querySelector("[data-local]").hidden = form.stt_engine.value !== "local";
    view.querySelector("[data-builtin]").hidden = form.stt_engine.value !== "builtin";
  });
  form.stt_api.addEventListener("change", () => { view.querySelector("[data-openai]").hidden = form.stt_api.value !== "openai"; });
  const formSettings = () => ({
    stt_engine: form.stt_engine.value, stt_api: form.stt_api.value, stt_url: form.stt_url.value, stt_model: form.stt_model.value,
    stt_model_dir: form.stt_model_dir.value.trim(), language: form.language.value.trim() || "en", speak_replies: form.speak_replies.checked,
    reply_style: form.reply_style.value, radio_voice: form.radio_voice.value, tts_voice: form.tts_voice.value,
    tts_rate: Number(form.tts_rate.value) || 1,
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { voice.settings = (({ ok, ...rest }) => rest)(await api("/api/voice/settings", { method: "PUT", body: formSettings() })); toast("Voice settings saved", "success"); render(view); }
    catch (err) { toast(err.message, "error"); }
  });
  view.querySelector("#testVoice").addEventListener("click", async () => {
    const was = voice.settings;
    const trial = { ...was, ...formSettings(), speak_replies: true };
    if (trial.reply_style !== "radio" && !localVoices().length) { toast("No installed voice was found in this browser.", "error"); return; }
    if (trial.radio_voice !== was.radio_voice || trial.tts_rate !== was.tts_rate) {
      toast("Save first to hear a different Windows voice or rate on the radio.", "info");
    }
    await speak("Radio check. Voice Command Agent, loud and clear. Ready for your orders, sir. Over.", trial);
  });
  view.querySelector("#diag").addEventListener("click", async () => {
    const out = view.querySelector("#diagOut");
    out.innerHTML = '<p class="muted">Checking…</p>';
    const checks = [];
    try { checks.push(...(await api("/api/voice/diagnostics", { method: "POST", body: {} })).checks); }
    catch (err) { checks.push({ name: "Voice service", status: "FAIL", detail: err.message }); }
    let mic = "NOT_CONFIGURED", micDetail = "Not needed while voice input is off.";
    if (form.stt_engine.value !== "off") {
      try {
        const p = await navigator.permissions?.query({ name: "microphone" });
        mic = p?.state === "denied" ? "FAIL" : p?.state === "granted" ? "PASS" : "WARN";
        micDetail = p ? `Microphone permission: ${p.state}.${p.state === "prompt" ? " The browser asks the first time you press the microphone." : ""}` : "The browser will ask when you press the microphone.";
      } catch { mic = "WARN"; micDetail = "The browser will ask when you press the microphone."; }
      if (!navigator.mediaDevices?.getUserMedia) { mic = "FAIL"; micDetail = "The microphone is not available on this address. Open the dashboard on http://127.0.0.1."; }
    }
    checks.push({ name: "Microphone", status: mic, detail: micDetail });
    const n = localVoices().length;
    checks.push({ name: "Browser voices", status: n ? "PASS" : "WARN", detail: n ? `${n} voices installed on this PC.` : "No installed browser voices." });
    out.innerHTML = `<table class="data"><tbody>${checks.map((c) => `<tr><td>${esc(c.name)}</td><td><span class="pill ${c.status === "NOT_CONFIGURED" ? "WAITING" : esc(c.status)}">${esc(c.status.replace("_", " "))}</span></td><td class="muted">${esc(c.detail)}</td></tr>`).join("")}</tbody></table>`;
  });
}

export async function mount(view) {
  if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = () => { const sel = view.querySelector("[name=tts_voice]"); if (sel && sel.options.length <= 1) render(view); };
  await render(view);
}

export function unmount() {
  off?.();
  off = null;
  clearInterval(clock);
  clock = null;
  if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = null;
}
