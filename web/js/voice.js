// Voice client: listen (built-in offline recognizer, local speech server or browser recognition), send the command,
// speak the agent's replies with the PC's own (offline) voices, as radio transmissions by default, and follow up on
// background results.

import { api } from "./api.js";
import { subscribe } from "./store.js";
import { playRadio, radioAround } from "./radio.js";

const listeners = new Set();
export const voice = {
  status: "idle", // idle | listening | thinking
  speaking: false,
  level: 0, // microphone level 0..1 while listening
  settings: null,
  log: [], // {who: "you"|"agent", text, kind}
  pending: null,
  error: null,
};

function emit() {
  for (const fn of listeners) { try { fn(voice); } catch (err) { console.error(err); } }
}
export function onVoice(fn) { listeners.add(fn); return () => listeners.delete(fn); }

function setStatus(status) { voice.status = status; emit(); }

export const conversationId = (() => {
  const make = () => `conv-${(crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now()).replace(/[^\w-]/g, "").slice(0, 40)}`;
  try {
    let id = sessionStorage.getItem("qa.voice.conversation");
    if (!id) { id = make(); sessionStorage.setItem("qa.voice.conversation", id); }
    return id;
  } catch { return make(); }
})();

export async function loadSettings(force = false) {
  if (!voice.settings || force) {
    const { ok, ...s } = await api("/api/voice/settings");
    voice.settings = s;
  }
  return voice.settings;
}

// ---- speaking ----

// Only voices installed on this PC (localService) are used, so replies are spoken without Internet.
export function localVoices() {
  if (!("speechSynthesis" in window)) return [];
  return speechSynthesis.getVoices().filter((v) => v.localService);
}

const MALE = /david|mark|guy|george|james|richard|ryan|daniel|fred|alex|tom|male|man\b/i;
const FEMALE = /zira|hazel|susan|aria|jenny|eva|female|woman|samantha|victoria|karen/i;

function pickVoice(s, male) {
  const voices = localVoices();
  const lang = (s.language || "en").toLowerCase();
  const inLang = voices.filter((v) => v.lang.toLowerCase().startsWith(lang));
  return voices.find((v) => v.name === s.tts_voice)
    || (male && (inLang.find((v) => MALE.test(v.name)) || inLang.find((v) => !FEMALE.test(v.name))))
    || inLang[0] || voices[0];
}

function browserSpeak(text, s, radio) {
  if (!("speechSynthesis" in window)) return Promise.resolve();
  const chosen = pickVoice(s, radio);
  if (!chosen) return Promise.resolve(); // no installed voice: the reply is still shown on screen
  return new Promise((resolve) => {
    const u = new SpeechSynthesisUtterance(text);
    u.voice = chosen;
    u.lang = chosen.lang;
    u.rate = (Number(s.tts_rate) || 1) * (radio ? 1.08 : 1);
    if (radio) u.pitch = 0.7;
    const done = () => { clearTimeout(guard); resolve(); };
    const guard = setTimeout(done, 4000 + text.length * 120); // some browsers never fire onend
    u.onend = done;
    u.onerror = done;
    speechSynthesis.speak(u);
  });
}

// Radio replies use the PC's Windows voice rendered by the runtime, so the audio can go through the radio filter.
let windowsVoice = null; // null = unknown, false = not available
const wavCache = new Map();

async function radioWav(text) {
  if (windowsVoice === false) return null;
  if (wavCache.has(text)) return wavCache.get(text).slice(0);
  try {
    const resp = await fetch("/api/voice/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    if (!resp.ok) { if (resp.status === 409) windowsVoice = false; return null; }
    windowsVoice = true;
    const wav = await resp.arrayBuffer();
    if (wavCache.size > 40) wavCache.delete(wavCache.keys().next().value);
    wavCache.set(text, wav);
    return wav.slice(0);
  } catch { return null; }
}

/** Fetch a phrase ahead of time so it plays the moment it is needed. */
export function prepare(text) {
  if (voice.settings?.speak_replies && voice.settings.reply_style === "radio") radioWav(text);
}

async function speakNow(text, s) {
  voice.speaking = true; emit();
  try {
    if (s.reply_style !== "radio") return await browserSpeak(text, s, false);
    const wav = await radioWav(text);
    if (wav) {
      try { return await playRadio(wav); } catch { /* fall back to the browser voice */ }
    }
    await radioAround(() => browserSpeak(text, s, true));
  } finally {
    voice.speaking = false; emit();
  }
}

let speechQueue = Promise.resolve();

/** Speak a reply after the ones already queued. Resolves when it has been spoken. */
export function speak(text, s = voice.settings) {
  if (!text || !s?.speak_replies) return Promise.resolve();
  speechQueue = speechQueue.then(() => speakNow(text, s)).catch((err) => console.error(err));
  return speechQueue;
}

// What the agent says back, in radio style: acknowledge, then confirm what it is doing.
export const ACK = "Roger, sir. Stand by.";
const COPY = {
  generate_test_cases: "Copy, sir. Starting test case creation.",
  generate_automation: "Copy, sir. Starting automation test case creation.",
  run_performance: "Copy, sir. Spinning up the JMeter performance test.",
  run_test_case: "Copy, sir. Executing the test.",
  run_all_tests: "Copy, sir. Executing all automated tests.",
  create_defects: "Copy, sir. Preparing the defect reports.",
  analyze_failure: "Copy, sir. Analyzing the failure.",
  start_recording: "Copy, sir. Opening the browser recorder.",
  stop_run: "Copy, sir. Aborting the run.",
  open_page: "Copy, sir.",
  open_work_item: "Copy, sir.",
};

function radioStyle() { return voice.settings?.reply_style === "radio"; }

export function replyLine(r) {
  if (!radioStyle()) return r.say;
  switch (r.status) {
    case "unsupported": return `Say again, sir. ${r.say}`;
    case "failed": return `Negative, sir. ${r.say}`;
    case "needs_confirmation": return `Sir, request your confirmation. ${r.say} Over.`;
    case "needs_input": return `Sir, I need one more detail. ${r.say} Over.`;
    default: return `${COPY[r.command] || "Copy that, sir."} ${r.say}`;
  }
}

function eventLine(name, text) {
  if (!radioStyle()) return text;
  if (name === "voice.command.completed") return `Mission complete, sir. ${text} Over.`;
  if (name === "voice.command.failed") return `Be advised, sir. ${text}`;
  return text;
}

// ---- commands ----

function pageContext() {
  const [route, query] = location.hash.replace(/^#\/?/, "").split("?");
  return { route: route || "dashboard", params: Object.fromEntries(new URLSearchParams(query || "")) };
}

const expectedHash = new Map(); // command id -> page shown when it was accepted

export async function sendCommand(text, source = "typed") {
  text = (text || "").trim();
  if (!text) return null;
  voice.log.push({ who: "you", text });
  setStatus("thinking");
  try {
    await loadSettings().catch(() => null);
    const r = await api("/api/voice/commands", { method: "POST", body: { text, source, conversation_id: conversationId, page: pageContext() } });
    voice.pending = r.context?.pending || null;
    const line = replyLine(r);
    voice.log.push({ who: "agent", text: line, kind: r.status });
    if (r.navigate && location.hash !== r.navigate) location.hash = r.navigate;
    if (r.status === "accepted") expectedHash.set(r.command_id, r.navigate || location.hash);
    speak(line);
    return r;
  } catch (err) {
    const line = radioStyle() ? `Negative, sir. ${err.message}` : err.message;
    voice.log.push({ who: "agent", text: line, kind: "failed" });
    speak(line);
    throw err;
  } finally {
    setStatus("idle");
  }
}

// Background progress and results for this conversation arrive as runtime events.
subscribe((change) => {
  if (change.type !== "event") return;
  const d = change.event.details || {};
  if (d.conversation_id !== conversationId || !d.speak) return;
  const line = eventLine(change.event.name, d.speak);
  voice.log.push({ who: "agent", text: line, kind: change.event.name === "voice.command.failed" ? "failed" : "progress" });
  speak(line);
  // Open the result only if the person is still looking at the page the command opened.
  const done = change.event.name === "voice.command.completed" || change.event.name === "voice.command.failed";
  if (done && d.navigate && expectedHash.get(d.command_id) === location.hash) location.hash = d.navigate;
  if (done) expectedHash.delete(d.command_id);
  emit();
});

// ---- listening ----

let active = null; // {stop: () => void}

export function isListening() { return voice.status === "listening"; }

export function stopListening() { active?.stop(); }

/** Listen once and send what was heard. Resolves with the command reply, or null if nothing was heard. */
export async function listen() {
  if (active) { active.stop(); return null; }
  const s = await loadSettings();
  voice.error = null;
  let text;
  prepare(ACK);
  try {
    if (s.stt_engine === "browser") {
      text = await listenBrowser(s);
      if (text) speak(ACK);
    } else if (s.stt_engine === "local" || s.stt_engine === "builtin") text = await listenLocal(s);
    else throw new Error("Voice input is off. Turn it on under Voice Commands, or type the command.");
  } catch (err) {
    voice.error = err.message;
    voice.level = 0;
    setStatus("idle");
    if (radioStyle()) speak(`Say again, sir. ${err.message}`);
    throw err;
  }
  return text ? sendCommand(text, "speech") : null;
}

function listenBrowser(s) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return Promise.reject(new Error("This browser has no speech recognition. Use the local speech server instead."));
  return new Promise((resolve, reject) => {
    const rec = new SR();
    rec.lang = s.language || "en";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    let heard = "";
    rec.onresult = (e) => { heard = e.results[0][0].transcript; };
    rec.onerror = (e) => { active = null; reject(new Error(e.error === "network" ? "Browser speech recognition needs Internet. Use the local speech server on offline PCs." : `Speech recognition error: ${e.error}`)); };
    rec.onend = () => { active = null; resolve(heard); };
    active = { stop: () => rec.stop() };
    setStatus("listening");
    rec.start();
  });
}

const TARGET_RATE = 16000;

async function listenLocal(s) {
  if (s.stt_engine === "local" && !s.stt_url) throw new Error("Set the local speech server URL under Voice Commands first.");
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser cannot use the microphone on this address.");
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  const ctx = new AudioContext();
  const source = ctx.createMediaStreamSource(stream);
  const node = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  let spoke = false, quietSince = 0;
  const started = performance.now();
  const audio = await new Promise((resolve) => {
    const finish = () => {
      node.disconnect(); source.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      active = null;
      resolve(chunks);
    };
    node.onaudioprocess = (e) => {
      const data = e.inputBuffer.getChannelData(0);
      chunks.push(new Float32Array(data));
      let sum = 0;
      for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
      const rms = Math.sqrt(sum / data.length), t = performance.now();
      voice.level = Math.min(1, rms * 8);
      emit();
      if (rms > 0.02) { spoke = true; quietSince = 0; } else if (spoke && !quietSince) quietSince = t;
      // stop after 1.2 s of quiet following speech, or after 15 s
      if ((spoke && quietSince && t - quietSince > 1200) || t - started > 15000) finish();
    };
    active = { stop: finish };
    source.connect(node);
    node.connect(ctx.destination);
    setStatus("listening");
  });
  const rate = ctx.sampleRate;
  ctx.close();
  voice.level = 0;
  if (!spoke) { setStatus("idle"); return ""; }
  setStatus("thinking");
  speak(ACK); // acknowledge at once; recognition and analysis follow
  const wav = encodeWav(downsample(audio, rate));
  const r = await api("/api/voice/transcribe", { method: "POST", rawBody: wav, contentType: "audio/wav" });
  return r.text;
}

function downsample(chunks, rate) {
  const length = chunks.reduce((n, c) => n + c.length, 0);
  const input = new Float32Array(length);
  let o = 0;
  for (const c of chunks) { input.set(c, o); o += c.length; }
  if (rate === TARGET_RATE) return input;
  const ratio = rate / TARGET_RATE, out = new Float32Array(Math.floor(length / ratio));
  for (let i = 0; i < out.length; i++) {
    const start = Math.floor(i * ratio), end = Math.min(length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    out[i] = sum / Math.max(1, end - start);
  }
  return out;
}

function encodeWav(samples) {
  const buf = new ArrayBuffer(44 + samples.length * 2), v = new DataView(buf);
  const str = (off, s) => { for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE");
  str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, TARGET_RATE, true); v.setUint32(28, TARGET_RATE * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const x = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
  }
  return new Blob([buf], { type: "audio/wav" });
}
