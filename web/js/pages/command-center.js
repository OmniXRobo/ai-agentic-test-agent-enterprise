// AI Voice Command Center: talk to the agent, see what it heard and understood, which agent took the task,
// and how that agent is doing. Every state shown here comes from the voice client or from runtime events.

import { state, subscribe, onTick, loadHealth } from "../store.js";
import { cardHtml, refreshCards, updateCard } from "../agent-card.js";
import { agentState, activeAgent } from "../agent-state.js";
import { esc, toast } from "../util.js";
import {
  voice, onVoice, conversationId, sendCommand, listen, cancelListening, loadSettings, agentName, stopSpeaking,
  setWake, wakeActive, applyAmbience, speak,
} from "../voice.js";
import { api } from "../api.js";
import { testRadio } from "../radio.js";

const SQUADRON = ["voice_command", "requirements", "test_planning", "test_case_design", "test_data", "automation_engineering",
  "guided_browser", "test_execution", "jmeter_load", "reporting", "defect_intelligence"];

const EXAMPLES = [
  "Open user story 118", "Generate test cases for user story 118", "Automate user story 118", "Create the test plan", "Generate automation for test case 1280",
  "Run test case 1280", "Run it again", "Show failed tests", "Show me the evidence", "Start manual recording",
  "Open the calendar", "Select tomorrow", "Stop recording", "Test the TFS connection", "Check the local AI",
  "Generate the report", "What is running", "Go back", "Stop the current test",
];

const VOICE_STATE = {
  idle: ["IDLE", "READY", "Standing by. Press the mic, then speak."],
  listening: ["LISTENING", "LISTENING", "Speak now. It stops when you pause."],
  transcribing: ["TRANSCRIBING", "TRANSCRIBING", "Turning your words into text on this PC…"],
  thinking: ["UNDERSTANDING", "THINKING", "Working out what you asked…"],
  executing: ["EXECUTING", "EXECUTING", "A specialist agent is working on it."],
  speaking: ["REPORTING", "SPEAKING", "Agent on the radio."],
  completed: ["COMPLETED", "COMPLETED", "Task complete."],
  failed: ["FAILED", "FAILED", "The last command did not complete."],
};
const KIND_CLASS = { failed: "FAIL", unsupported: "FAIL", unclear: "WARN", needs_confirmation: "WARN", needs_input: "WARN", accepted: "RUNNING", progress: "RUNNING", completed: "PASS" };
const BARS = 36;

let cleanups = [];
let lastOwner = null; // the agent the last command was handed to

function voiceState() { return voice.speaking ? "speaking" : voice.status; }

function logHtml() {
  if (!voice.log.length) return `<p class="ck-empty">No traffic yet. Press the mic and give an order, or type one below. Replies come back over the radio and are logged here.</p>`;
  return voice.log.slice(-60).map((m) => `<div class="vmsg ${m.who}"><span class="vwho">${m.who === "you" ? "YOU" : esc(agentName())}</span>
    <span class="vtext" dir="auto">${esc(m.text)}</span>${m.kind && KIND_CLASS[m.kind] ? `<span class="pill ${KIND_CLASS[m.kind]}">${esc(m.kind.replace("_", " "))}</span>` : ""}</div>`).join("");
}

// Voice LISTENING → UNDERSTANDING → <Agent> RUNNING → COMPLETED → Voice REPORTING
// Voice LISTENING → UNDERSTANDING → each agent that worked on the task (e.g. Requirements → Application Discovery →
// Automation Engineering), in the order they really started → Voice REPORTING.
function taskChain() {
  if (!voice.task) return [];
  return [...state.agents.values()]
    .filter((a) => a.id !== "voice_command" && a.started_at && new Date(a.started_at).getTime() >= voice.task.since)
    .sort((a, b) => new Date(a.started_at) - new Date(b.started_at));
}

function handoffHtml() {
  const vs = voiceState();
  let chain = taskChain();
  if (!chain.length) {
    const owner = (voice.executing?.owner && state.agents.get(voice.executing.owner)) || activeAgent(state.agents)
      || (lastOwner && state.agents.get(lastOwner)) || null;
    chain = owner ? [owner] : [];
  }
  const hop = (label, sub, cls) => `<span class="hop ${cls}"><small>${esc(sub)}</small>${esc(label)}</span>`;
  const heard = ["listening"].includes(vs) ? "on" : voice.log.length ? "done" : "";
  const understood = ["transcribing", "thinking"].includes(vs) ? "on" : voice.log.length && !["listening"].includes(vs) ? "done" : "";
  const report = vs === "speaking" ? "on" : vs === "completed" ? "done" : vs === "failed" ? "bad" : "";
  const agents = chain.map((a) => {
    const os = agentState(a, state.agents).state;
    const cls = os === "RUNNING" || os === "PAUSED" || os === "WAITING" ? "on" : os === "FAILED" ? "bad" : a.finished_at ? "done" : "";
    const shown = os === "READY" && a.finished_at ? (a.status === "FAILED" ? "FAILED" : "COMPLETED") : os;
    return hop(shown, a.name.replace(/ Agent$/, ""), cls);
  });
  return [
    hop("Listening", "Voice", heard),
    hop("Understanding", "Voice", understood),
    ...(agents.length ? agents : [hop("No task", "Specialist", "")]),
    hop("Reporting", "Voice", report),
  ].join('<span class="arrow" aria-hidden="true">&#10148;</span>');
}

function lightsHtml() {
  const items = Object.fromEntries((state.health?.items || []).map((i) => [i.key, i]));
  const mic = voice.micError ? ["FAIL", "Blocked", voice.micError]
    : voice.status === "listening" || voice.wake ? ["RUNNING", "Live", "The microphone is open."]
      : voice.settings?.voice_enabled === false || voice.settings?.stt_engine === "off" ? ["NOT_CONFIGURED", "Off", "Voice input is off."] : ["PASS", "Ready", "Press the mic to talk."];
  const light = (label, status, value, detail) => `<div class="cc-light" title="${esc(detail || "")}"><span class="lamp ${esc(status)}"></span><span>${esc(label)}</span><b>${esc(value)}</b></div>`;
  const it = (k, label) => (items[k] ? light(label, items[k].status, items[k].value, items[k].detail) : light(label, "NOT_CONFIGURED", "Checking…", ""));
  return light("Microphone", ...mic) + it("speech", "Speech") + it("ai", "Local AI") + it("tfs", items.tfs?.label || "TFS") + it("playwright", "Playwright");
}

function radioHtml(s) {
  const pct = (v, d) => Math.round((Number.isFinite(Number(v)) ? Number(v) : d) * 100);
  const sw = (name, on, label) => `<label class="cc-switch"><input type="checkbox" name="${name}" ${on ? "checked" : ""}><span>${label}</span></label>`;
  const rg = (name, v, label) => `<label class="cc-range"><span>${label}</span><input type="range" name="${name}" min="0" max="100" step="5" value="${v}"></label>`;
  return `<form id="ccRadio" class="cc-radio">
    ${sw("speak_replies", s.speak_replies, "Voice reply")}
    ${sw("radio", s.reply_style === "radio", "Radio effect")}
    ${sw("wireless_tone", s.wireless_tone !== false, "Wireless tone")}
    ${sw("ambience_enabled", s.ambience_enabled, "Background audio")}
    ${rg("voice_volume", pct(s.voice_volume, 0.9), "Voice")}
    ${rg("radio_intensity", pct(s.radio_intensity, 0.5), "Radio")}
    ${rg("static_volume", pct(s.static_volume, 0.25), "Static")}
    ${rg("ambience_volume", pct(s.ambience_volume, 0.2), "Background")}
    <div class="row"><button class="btn" type="button" id="ccTestVoice">Test Voice</button><button class="btn" type="button" id="ccTestRadio">Test Radio</button>
      <button class="btn" type="button" id="ccStop">Stop Speaking</button><a class="btn" href="#/voice">All settings</a></div>
  </form>`;
}

export async function mount(view) {
  const s = await loadSettings(true).catch(() => voice.settings || {});
  if (!voice.log.length) {
    try {
      const { items, context } = await api(`/api/voice/commands?conversation_id=${encodeURIComponent(conversationId)}&limit=30`);
      for (const it of items) {
        voice.log.push({ who: "you", text: it.text });
        if (it.reply) voice.log.push({ who: "agent", text: it.reply, kind: it.status });
        if (it.final_reply) voice.log.push({ who: "agent", text: it.final_reply, kind: it.status === "failed" ? "failed" : "completed" });
      }
      voice.pending = context?.pending || null;
    } catch { /* history is a convenience */ }
  }
  // Load the offline speech model in the background so the first command is not slow.
  if (s.stt_engine === "builtin" && s.voice_enabled !== false) api("/api/voice/warmup", { method: "POST", body: {} }).catch(() => {});
  const wakeMode = s.listen_mode === "wake_name";
  view.innerHTML = `
    <div class="cockpit cc">
      <header class="ck-head">
        <div class="ck-callsign"><span class="ck-wing" aria-hidden="true">&#9650;</span> TACTICAL VOICE COMMAND</div>
        <div class="ck-meta"><span>CH 07</span><span>SECURE</span><span>${s.stt_engine === "builtin" || s.stt_engine === "local" ? "OFFLINE LINK" : s.stt_engine === "browser" ? "ONLINE LINK" : "KEYBOARD ONLY"}</span>
          <span>${wakeMode ? `WAKE NAME "${esc((s.agent_command_name || "Hami").toUpperCase())}"` : "PUSH-TO-TALK"}</span></div>
        <div class="ck-clock" id="ccClock" title="Windows system time"></div>
      </header>
      <div class="cc-top">
        <section class="ck-panel ck-scope">
          <div class="radar"><div class="radar-sweep" aria-hidden="true"></div>
            <button class="mic-btn" id="ccMic" aria-label="Speak a command"><svg viewBox="0 0 24 24" width="40" height="40" aria-hidden="true"><path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12Z"/></svg></button>
          </div>
          <div class="cc-agent" dir="auto" id="ccAgent">${esc(agentName())}</div>
          <div class="ck-status"><span class="ck-lamp" id="ccLamp"></span><strong id="ccState"></strong></div>
          <div class="ck-hint" id="ccHint"></div>
          <div class="ck-meter" id="ccMeter" aria-hidden="true">${"<i></i>".repeat(BARS)}</div>
          <div class="row" style="justify-content:center">
            ${wakeMode ? `<button class="btn" id="ccWake">Start wake-name listening</button>` : ""}
            <button class="btn" id="ccCancel">Cancel</button>
          </div>
          <div class="ck-readout"><span>CALLSIGN</span><b dir="auto" id="ccName">${esc(agentName())}</b></div>
          <div class="ck-readout"><span>RECOGNITION</span><b>${s.stt_engine === "builtin" ? "BUILT-IN OFFLINE RECOGNIZER" : s.stt_engine === "local" ? "LOCAL SPEECH SERVER" : s.stt_engine === "browser" ? "BROWSER" : "OFF"}</b></div>
          <div class="ck-readout"><span>REPLY</span><b>${s.speak_replies ? (s.reply_style === "radio" ? "PILOT RADIO" : "PLAIN VOICE") : "TEXT ONLY"}</b></div>
          <div class="cc-lights" id="ccLights"></div>
        </section>
        <section class="ck-panel ck-comms">
          <h3>COMMS LOG</h3>
          <div class="vlog" id="ccLog" aria-live="polite"></div>
          <div class="row" id="ccConfirm" hidden><span class="muted">Confirm this operation?</span><button class="btn primary" id="ccYes">Confirm</button><button class="btn" id="ccNo">Cancel</button></div>
          <form class="row ck-typed" id="ccTyped"><span class="ck-prompt" aria-hidden="true">&gt;</span>
            <input name="text" placeholder="Type an order, for example: automate user story 118" autocomplete="off" maxlength="500" dir="auto">
            <button class="btn primary" type="submit">Send</button></form>
        </section>
      </div>
      <section class="ck-panel"><h3>COMMAND HANDOFF</h3><div class="handoff" id="ccHandoff"></div></section>
      <section class="ck-panel"><h3>AGENT SQUADRON</h3><div class="squad" id="ccSquad"></div></section>
      <div class="cc-bottom">
        <section class="ck-panel"><h3>RADIO CONFIGURATION</h3>${radioHtml(s)}</section>
        <section class="ck-panel"><h3>ORDERS YOU CAN GIVE</h3>
          <div class="chips">${EXAMPLES.map((e) => `<button class="chip" data-say="${esc(e)}">${esc(e)}</button>`).join("")}</div>
          <p class="muted">Follow-ups use what you just opened or ran ("select the second one", "run it again", "go back"). Anything that changes or deletes data in TFS asks "Confirm this operation?" first.</p>
        </section>
      </div>
    </div>`;

  const $ = (sel) => view.querySelector(sel);
  const squad = $("#ccSquad");
  squad.innerHTML = SQUADRON.map((id) => state.agents.get(id)).filter(Boolean).map((a) => cardHtml(a, { compact: true })).join("");
  const bars = [...view.querySelectorAll("#ccMeter i")];
  let shown = {};

  const update = () => {
    const vs = voiceState();
    const [label, , hint] = VOICE_STATE[vs] || VOICE_STATE.idle;
    const level = vs === "listening" || voice.wake ? voice.level : vs === "speaking" ? 0.35 + Math.random() * 0.3 : 0;
    bars.forEach((b, i) => {
      const center = 1 - Math.abs(i - (BARS - 1) / 2) / (BARS / 2);
      b.style.transform = `scaleY(${Math.max(0.06, Math.min(1, level * (0.5 + center) * (0.75 + Math.random() * 0.5)))})`;
    });
    const key = `${voice.log.length}|${vs}|${voice.error}|${voice.pending?.kind}|${voice.wake}|${voice.micError}|${voice.executing?.owner}`;
    if (key === shown.key) return;
    shown.key = key;
    view.querySelector(".cockpit").dataset.state = vs === "speaking" ? "speaking" : vs === "idle" || vs === "completed" ? "idle" : vs === "listening" ? "listening" : "thinking";
    $("#ccState").textContent = voice.wake && vs === "idle" ? `LISTENING FOR "${(voice.settings?.agent_command_name || "Hami").toUpperCase()}"` : label;
    $("#ccHint").textContent = voice.micError || voice.error || (voice.settings?.stt_engine === "off" ? "Voice input is off; type your instruction." : hint);
    const mic = $("#ccMic");
    mic.classList.toggle("on", vs === "listening");
    mic.disabled = voice.settings?.stt_engine === "off" || voice.settings?.voice_enabled === false || vs === "thinking" || vs === "transcribing";
    const log = $("#ccLog");
    log.innerHTML = logHtml();
    log.scrollTop = log.scrollHeight;
    $("#ccConfirm").hidden = voice.pending?.kind !== "confirm";
    const wake = $("#ccWake");
    if (wake) wake.textContent = voice.wake ? "Stop wake-name listening" : "Start wake-name listening";
    if (voice.executing?.owner) lastOwner = voice.executing.owner;
    $("#ccHandoff").innerHTML = handoffHtml();
    $("#ccLights").innerHTML = lightsHtml();
  };

  cleanups.push(onVoice(update));
  cleanups.push(subscribe((c) => {
    if (c.type === "event" && c.agent) { updateCard(squad, c.agent); $("#ccHandoff").innerHTML = handoffHtml(); }
    if (c.type === "event" && c.event.name === "voice.handoff" && c.event.details?.to) { lastOwner = c.event.details.to; $("#ccHandoff").innerHTML = handoffHtml(); }
    if (c.type === "health") $("#ccLights").innerHTML = lightsHtml();
  }));
  const clock = $("#ccClock");
  const showClock = () => { clock.textContent = `${new Date().toLocaleTimeString([], { hour12: false })} LOCAL`; };
  showClock();
  cleanups.push(onTick(() => { showClock(); refreshCards(squad); $("#ccHandoff").innerHTML = handoffHtml(); }));
  if (!state.health) loadHealth();
  update();

  const run = (fn) => fn().catch((err) => { update(); if (err?.message) toast(err.message, "error"); });
  $("#ccMic").addEventListener("click", () => run(listen));
  $("#ccCancel").addEventListener("click", () => { cancelListening(); stopSpeaking(); if (voice.pending) run(() => sendCommand("cancel")); });
  $("#ccWake")?.addEventListener("click", () => run(() => setWake(!wakeActive())));
  $("#ccTyped").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = e.target.text.value;
    e.target.text.value = "";
    run(() => sendCommand(text, "typed"));
  });
  view.querySelectorAll("[data-say]").forEach((b) => b.addEventListener("click", () => run(() => sendCommand(b.dataset.say, "typed"))));
  $("#ccYes").addEventListener("click", () => run(() => sendCommand("confirm")));
  $("#ccNo").addEventListener("click", () => run(() => sendCommand("cancel")));

  // Radio quick settings are saved as they change (one PUT per change, no polling).
  const form = $("#ccRadio");
  const patch = () => {
    const f = form.elements;
    return {
      speak_replies: f.speak_replies.checked, reply_style: f.radio.checked ? "radio" : "plain", wireless_tone: f.wireless_tone.checked,
      ambience_enabled: f.ambience_enabled.checked, voice_volume: f.voice_volume.value / 100, radio_intensity: f.radio_intensity.value / 100,
      static_volume: f.static_volume.value / 100, ambience_volume: f.ambience_volume.value / 100,
    };
  };
  form.addEventListener("input", () => applyAmbience({ ...voice.settings, ...patch() }));
  form.addEventListener("change", async () => {
    try { voice.settings = (({ ok, ...rest }) => rest)(await api("/api/voice/settings", { method: "PUT", body: patch() })); applyAmbience(); }
    catch (err) { toast(err.message, "error"); }
  });
  $("#ccTestVoice").addEventListener("click", () => {
    speak(`${agentName()} on frequency. Loud and clear. Ready for your instructions.`, { ...voice.settings, ...patch(), speak_replies: true, voice_enabled: true });
  });
  $("#ccTestRadio").addEventListener("click", () => testRadio({ ...voice.settings, ...patch() }));
  $("#ccStop").addEventListener("click", stopSpeaking);
}

export function unmount() {
  for (const fn of cleanups) { try { fn(); } catch { /* ignore */ } }
  cleanups = [];
  // Wake-name listening keeps the microphone open, so it never outlives the page that started it.
  if (wakeActive()) setWake(false);
}
