// Voice Commands: talk to the QA runtime, see what was understood, and set up speech recognition and voices.

import { api } from "../api.js";
import { esc, toast } from "../util.js";
import { voice, onVoice, conversationId, sendCommand, listen, loadSettings, localVoices, speak } from "../voice.js";

const EXAMPLES = [
  "Open user story 118", "Generate test cases", "Generate automation for test case 1280", "Play test case 1280",
  "Run all automated tests", "Run a 100 user load test for checkout for five minutes", "Show failed tests",
  "Analyze the failure", "Create defects for failed tests", "Show performance report", "Stop the running test",
  "What is running", "Open TFS connection settings",
];
const ENGINES = {
  local: ["Local speech server (works offline)", "A whisper.cpp or OpenAI-compatible speech server on this PC or your network turns speech into text. Nothing leaves your network."],
  browser: ["Browser speech recognition", "Chrome and Edge send the audio to their online service, so this needs Internet. Do not use it on offline PCs."],
  off: ["Off (typed commands only)", "The microphone is not used. Type commands below."],
};
const KIND_CLASS = { failed: "FAIL", unsupported: "FAIL", needs_confirmation: "WARN", needs_input: "WARN", accepted: "RUNNING", progress: "RUNNING" };

let off = null;

function logHtml() {
  if (!voice.log.length) return '<p class="muted">Press the microphone and say a command, or type one below. Replies are spoken and shown here.</p>';
  return voice.log.map((m) => `<div class="vmsg ${m.who}"><span class="vwho">${m.who === "you" ? "You" : "Voice Agent"}</span>
    <span class="vtext">${esc(m.text)}</span>${m.kind && KIND_CLASS[m.kind] ? `<span class="pill ${KIND_CLASS[m.kind]}">${esc(m.kind.replace("_", " "))}</span>` : ""}</div>`).join("");
}

function settingsHtml(s) {
  const voices = localVoices();
  return `
    <form class="card settings" id="vset" style="max-width:none">
      <h3>Speech recognition</h3>
      <label>Engine <select name="stt_engine">${Object.entries(ENGINES).map(([k, [l]]) => `<option value="${k}" ${k === s.stt_engine ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
      <p class="muted" id="engineHelp">${esc(ENGINES[s.stt_engine]?.[1] || "")}</p>
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
      <label>Voice <select name="tts_voice"><option value="">Default voice for the language</option>
        ${voices.map((v) => `<option value="${esc(v.name)}" ${v.name === s.tts_voice ? "selected" : ""}>${esc(v.name)} (${esc(v.lang)})</option>`).join("")}</select></label>
      <p class="muted">${voices.length ? `Only voices installed on this PC are used (${voices.length} found), so replies work offline.` : "No installed voices were found in this browser. Replies are shown on screen only."}</p>
      <label>Speaking rate <input type="number" name="tts_rate" min="0.5" max="2" step="0.1" value="${esc(s.tts_rate)}" style="width:90px"></label>
      <div class="row"><button class="btn primary" type="submit">Save</button><button class="btn" type="button" id="testVoice">Test voice</button>
        <button class="btn" type="button" id="diag">Check voice setup</button></div>
      <div id="diagOut"></div>
    </form>`;
}

async function render(view) {
  const s = await loadSettings(true);
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
  view.innerHTML = `
    <div class="grid split-2">
      <div>
        <div class="card voice-card">
          <div class="row"><button class="mic-btn" id="mic" aria-label="Speak a command"><svg viewBox="0 0 24 24" width="30" height="30" aria-hidden="true"><path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12Z"/></svg></button>
            <div><strong id="vstatus"></strong><div class="muted" id="vhint"></div></div></div>
          <div class="vlog" id="vlog" aria-live="polite"></div>
          <div class="row" id="confirmRow" hidden><button class="btn primary" id="yes">Confirm</button><button class="btn" id="no">Cancel</button></div>
          <form class="row" id="typed"><input name="text" placeholder="Type a command, for example: run test case 1280" autocomplete="off" style="flex:1;min-width:0" maxlength="500">
            <button class="btn" type="submit">Send</button></form>
        </div>
        <div class="card" style="margin-top:12px"><h3>Things you can say</h3>
          <div class="chips">${EXAMPLES.map((e) => `<button class="chip" data-say="${esc(e)}">${esc(e)}</button>`).join("")}</div>
          <p class="muted">Follow-ups use what you just opened or ran: "open user story 118", then "generate test cases"; "run test case 1280", then "run it again". Creating defects in TFS asks for confirmation first.</p></div>
      </div>
      <div>${settingsHtml(s)}</div>
    </div>`;
  const update = () => {
    const status = { idle: "Ready", listening: "Listening…", thinking: "Working…" }[voice.status];
    view.querySelector("#vstatus").textContent = status;
    view.querySelector("#vhint").textContent = voice.error || (voice.status === "listening" ? "Speak now. Press again to stop." : s.stt_engine === "off" ? "Voice input is off; type a command." : "Press the microphone, then speak.");
    view.querySelector("#mic").classList.toggle("on", voice.status === "listening");
    view.querySelector("#mic").disabled = s.stt_engine === "off" || voice.status === "thinking";
    const log = view.querySelector("#vlog");
    log.innerHTML = logHtml();
    log.scrollTop = log.scrollHeight;
    view.querySelector("#confirmRow").hidden = voice.pending?.kind !== "confirm";
  };
  off?.();
  off = onVoice(update);
  update();
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
  });
  form.stt_api.addEventListener("change", () => { view.querySelector("[data-openai]").hidden = form.stt_api.value !== "openai"; });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      stt_engine: form.stt_engine.value, stt_api: form.stt_api.value, stt_url: form.stt_url.value, stt_model: form.stt_model.value,
      language: form.language.value.trim() || "en", speak_replies: form.speak_replies.checked, tts_voice: form.tts_voice.value,
      tts_rate: Number(form.tts_rate.value) || 1,
    };
    try { voice.settings = (({ ok, ...rest }) => rest)(await api("/api/voice/settings", { method: "PUT", body })); toast("Voice settings saved", "success"); render(view); }
    catch (err) { toast(err.message, "error"); }
  });
  view.querySelector("#testVoice").addEventListener("click", () => {
    if (!localVoices().length) { toast("No installed voice was found in this browser.", "error"); return; }
    const was = voice.settings;
    voice.settings = { ...was, speak_replies: true, tts_voice: form.tts_voice.value, tts_rate: Number(form.tts_rate.value) || 1 };
    speak("Voice Command Agent ready.");
    voice.settings = was;
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
    checks.push({ name: "Installed voices", status: n ? "PASS" : "WARN", detail: n ? `${n} voices installed on this PC.` : "No installed voices; replies are shown on screen only." });
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
  if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = null;
}
