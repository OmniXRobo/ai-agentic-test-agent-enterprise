// Voice client: listen (local speech server or browser recognition), send the command,
// speak the agent's replies with the PC's own (offline) voices, and follow up on background results.

import { api } from "./api.js";
import { subscribe } from "./store.js";

const listeners = new Set();
export const voice = {
  status: "idle", // idle | listening | thinking
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

export function speak(text) {
  const s = voice.settings;
  if (!text || !s?.speak_replies || !("speechSynthesis" in window)) return;
  const voices = localVoices();
  const lang = (s.language || "en").toLowerCase();
  const chosen = voices.find((v) => v.name === s.tts_voice) || voices.find((v) => v.lang.toLowerCase().startsWith(lang)) || voices[0];
  if (!chosen) return; // no installed voice: the reply is still shown on screen
  const u = new SpeechSynthesisUtterance(text);
  u.voice = chosen;
  u.lang = chosen.lang;
  u.rate = Number(s.tts_rate) || 1;
  speechSynthesis.speak(u);
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
    voice.log.push({ who: "agent", text: r.say, kind: r.status });
    if (r.navigate && location.hash !== r.navigate) location.hash = r.navigate;
    if (r.status === "accepted") expectedHash.set(r.command_id, r.navigate || location.hash);
    speak(r.say);
    return r;
  } catch (err) {
    voice.log.push({ who: "agent", text: err.message, kind: "failed" });
    speak(err.message);
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
  voice.log.push({ who: "agent", text: d.speak, kind: change.event.name === "voice.command.failed" ? "failed" : "progress" });
  speak(d.speak);
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
  try {
    if (s.stt_engine === "browser") text = await listenBrowser(s);
    else if (s.stt_engine === "local") text = await listenLocal(s);
    else throw new Error("Voice input is off. Turn it on under Voice Commands, or type the command.");
  } catch (err) {
    voice.error = err.message;
    setStatus("idle");
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
  if (!s.stt_url) throw new Error("Set the local speech server URL under Voice Commands first.");
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
  if (!spoke) { setStatus("idle"); return ""; }
  setStatus("thinking");
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
