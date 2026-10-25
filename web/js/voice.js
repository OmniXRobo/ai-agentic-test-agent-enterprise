// Voice client: listen (built-in offline recognizer, local speech server or browser recognition), send the command,
// speak the agent's replies with the PC's own (offline) voices, as radio transmissions by default, and follow up on
// background results.
//
// Listening is half-duplex: the microphone never records while the agent is speaking (plus a short tail), so the
// agent cannot hear and transcribe itself. Radio effects and ambience only go to the speakers.

import { api } from "./api.js";
import { subscribe } from "./store.js";
import { playRadio, radioAround, ackTone, micTone, stopRadio, setAmbience, duck } from "./radio.js";

const listeners = new Set();
export const voice = {
  // idle | listening | transcribing | thinking | executing | speaking | completed | failed
  status: "idle",
  speaking: false,
  level: 0, // microphone level 0..1 while listening
  settings: null,
  log: [], // {who: "you"|"agent", text, kind}
  pending: null,
  error: null,
  micError: null, // why the microphone cannot be used (permission, no device, disconnected)
  wake: false, // wake-name listening is on
  executing: null, // {command_id, command, owner} while a background task runs
  task: null, // {since, command}: the last background task, for the agent handoff chain
  lastHeard: "",
};

function emit() {
  for (const fn of listeners) { try { fn(voice); } catch (err) { console.error(err); } }
}
export function onVoice(fn) { listeners.add(fn); return () => listeners.delete(fn); }

let settle = null;
function setStatus(status) {
  clearTimeout(settle);
  voice.status = status;
  duck(status === "listening" ? "user" : voice.speaking ? "agent" : "none");
  // Completed and Failed are shown for a moment, then the agent is idle again.
  if (status === "completed" || status === "failed") settle = setTimeout(() => { if (voice.status === status) setStatus(voice.executing ? "executing" : "idle"); }, 4000);
  emit();
}

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
    applyAmbience();
  }
  return voice.settings;
}

export function applyAmbience(s = voice.settings) {
  try { setAmbience(Boolean(s?.ambience_enabled), s?.ambience_volume); } catch { /* audio is a convenience */ }
}

/** The agent's display name (any language, set under Voice & Command Agent). */
export function agentName() { return voice.settings?.agent_display_name || "HAMI"; }

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
  const chosen = pickVoice(s, true);
  if (!chosen) return Promise.resolve(); // no installed voice: the reply is still shown on screen
  return new Promise((resolve) => {
    const u = new SpeechSynthesisUtterance(text);
    u.voice = chosen;
    u.lang = chosen.lang;
    u.rate = (Number(s.tts_rate) || 1) * (radio ? 0.98 : 1); // calm, measured delivery
    u.pitch = radio ? 0.8 : 0.9;
    u.volume = Math.max(0, Math.min(1, Number(s.voice_volume ?? 0.9)));
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

let speakEndedAt = 0;
let speechGen = 0; // bumped by Stop Speaking so queued replies are dropped

async function speakNow(text, s, gen) {
  if (gen !== speechGen) return;
  voice.speaking = true; duck("agent"); emit();
  try {
    if (s.reply_style !== "radio") return await browserSpeak(text, s, false);
    const wav = await radioWav(text);
    if (gen !== speechGen) return;
    if (wav) {
      try { return await playRadio(wav, s); } catch { /* fall back to the browser voice */ }
    }
    await radioAround(() => browserSpeak(text, s, true), s);
  } finally {
    voice.speaking = false; speakEndedAt = performance.now();
    duck(voice.status === "listening" ? "user" : "none"); emit();
  }
}

let speechQueue = Promise.resolve();

/** Speak a reply after the ones already queued. Resolves when it has been spoken. */
export function speak(text, s = voice.settings) {
  if (!text || !s?.speak_replies || s.voice_enabled === false) return Promise.resolve();
  const gen = speechGen;
  speechQueue = speechQueue.then(() => speakNow(text, s, gen)).catch((err) => console.error(err));
  return speechQueue;
}

/** Stop Speaking: cut the current transmission and drop queued replies. */
export function stopSpeaking() {
  speechGen += 1;
  stopRadio();
  if ("speechSynthesis" in window) speechSynthesis.cancel();
}

/** Resolves once the agent has finished speaking and the echo tail has passed. */
async function quietMic(tail = 300) {
  await speechQueue;
  const wait = tail - (performance.now() - speakEndedAt);
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));
}

// The runtime composes each spoken line ("OK, I will do your task now. …"); older runtimes send only `say`.
export function replyLine(r) { return r.speak || r.say; }

// ---- commands ----

function pageContext() {
  const [route, query] = location.hash.replace(/^#\/?/, "").split("?");
  return { route: route || "dashboard", params: Object.fromEntries(new URLSearchParams(query || "")) };
}

const expectedHash = new Map(); // command id -> page shown when it was accepted
const navStack = [];

function go(hash) {
  if (hash === "back") { if (navStack.length) location.hash = navStack.pop(); else history.back(); return; }
  if (location.hash !== hash) { navStack.push(location.hash || "#/command-center"); if (navStack.length > 20) navStack.shift(); location.hash = hash; }
}

export async function sendCommand(text, source = "typed") {
  text = (text || "").trim();
  if (!text) return null;
  voice.log.push({ who: "you", text });
  voice.error = null;
  setStatus("thinking");
  let final = "idle";
  try {
    await loadSettings().catch(() => null);
    const r = await api("/api/voice/commands", { method: "POST", body: { text, source, conversation_id: conversationId, page: pageContext() } });
    voice.pending = r.context?.pending || null;
    const line = replyLine(r);
    voice.log.push({ who: "agent", text: line, kind: r.status });
    if (r.navigate) go(r.navigate);
    if (r.status === "accepted") {
      expectedHash.set(r.command_id, r.navigate || location.hash);
      voice.executing = { command_id: r.command_id, command: r.command, owner: r.owner || null };
      voice.task = { since: Date.now() - 1500, command: r.command }; // agents started from now on worked on this task
      final = "executing";
    } else if (r.status === "completed") final = "completed";
    else if (r.status === "failed" || r.status === "unsupported") final = "failed";
    speak(line);
    return r;
  } catch (err) {
    const line = `${voice.settings?.phrase_style === "tactical" ? "Command unsuccessful." : "Unable to complete the command."} ${err.message}`;
    voice.log.push({ who: "agent", text: line, kind: "failed" });
    speak(line);
    final = "failed";
    throw err;
  } finally {
    setStatus(final);
  }
}

// Background progress and results for this conversation arrive as runtime events.
subscribe((change) => {
  if (change.type !== "event") return;
  const d = change.event.details || {};
  if (d.conversation_id !== conversationId || !d.speak) return;
  const name = change.event.name;
  const done = name === "voice.command.completed" || name === "voice.command.failed";
  voice.log.push({ who: "agent", text: d.speak, kind: name === "voice.command.failed" ? "failed" : done ? "completed" : "progress" });
  speak(d.speak);
  // Open the result only if the person is still looking at the page the command opened.
  if (done && d.navigate && expectedHash.get(d.command_id) === location.hash) go(d.navigate);
  if (done) {
    expectedHash.delete(d.command_id);
    if (voice.executing?.command_id === d.command_id) voice.executing = null;
    if (["executing", "idle", "completed", "failed"].includes(voice.status)) setStatus(name === "voice.command.failed" ? "failed" : "completed");
  }
  emit();
});

// ---- listening ----

let active = null; // {stop: () => void, cancel?: () => void}

export function isListening() { return voice.status === "listening"; }

/** Stop listening now and use what was heard so far. */
export function stopListening() { active?.stop(); }

/** Cancel listening and throw away what was heard. */
export function cancelListening() { (active?.cancel || active?.stop)?.(); }

function micMessage(err) {
  const n = err?.name || "";
  if (n === "NotAllowedError" || n === "SecurityError") return "Microphone access is blocked. Allow the microphone for this page in the browser's site settings, then try again.";
  if (n === "NotFoundError" || n === "OverconstrainedError") return "No microphone was found. Connect a headset or microphone and try again.";
  if (n === "NotReadableError" || n === "AbortError") return "The microphone is in use by another application or was disconnected.";
  return err?.message || "The microphone could not be opened.";
}

async function openMic() {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser cannot use the microphone on this address. Open the dashboard on http://127.0.0.1.");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    voice.micError = null;
    return stream;
  } catch (err) {
    voice.micError = micMessage(err);
    emit();
    throw new Error(voice.micError);
  }
}

if (navigator.mediaDevices?.addEventListener) {
  navigator.mediaDevices.addEventListener("devicechange", () => { voice.micError = null; emit(); });
}

/** Listen once (push-to-talk) and send what was heard. Resolves with the command reply, or null if nothing was heard. */
export async function listen() {
  if (active) { active.stop(); return null; }
  const s = await loadSettings();
  voice.error = null;
  if (voice.speaking) stopSpeaking(); // pressing the mic while the agent talks interrupts it
  let text;
  try {
    if (s.voice_enabled === false || s.stt_engine === "off") throw new Error("Voice input is off. Turn it on under Voice & Command Agent, or type the command.");
    await quietMic(300);
    if (s.stt_engine === "browser") text = await listenBrowser(s);
    else if (s.stt_engine === "local" || s.stt_engine === "builtin") {
      const stream = await openMic();
      try { text = await captureAndTranscribe(stream, s, { maxMs: 15000, noSpeechMs: 8000 }); }
      finally { stream.getTracks().forEach((t) => t.stop()); }
    } else throw new Error("Choose a speech recognition engine under Voice & Command Agent.");
  } catch (err) {
    voice.error = err.message;
    voice.level = 0;
    setStatus("failed");
    throw err;
  }
  if (!text) { setStatus("idle"); return null; }
  ackTone(s);
  return sendCommand(text, "speech");
}

function listenBrowser(s) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return Promise.reject(new Error("This browser has no speech recognition. Use the built-in offline recognizer instead."));
  return new Promise((resolve, reject) => {
    const rec = new SR();
    rec.lang = s.language || "en";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    let heard = "";
    rec.onresult = (e) => { heard = e.results[0][0].transcript; };
    rec.onerror = (e) => {
      active = null;
      if (e.error === "not-allowed") voice.micError = "Microphone access is blocked for this page.";
      reject(new Error(e.error === "network" ? "Browser speech recognition needs Internet. Use the built-in offline recognizer on offline PCs." : `Speech recognition error: ${e.error}`));
    };
    rec.onend = () => { active = null; resolve(heard); };
    active = { stop: () => rec.stop(), cancel: () => { heard = ""; rec.abort(); } };
    setStatus("listening");
    micTone(true, s);
    rec.start();
  });
}

const TARGET_RATE = 16000;
const SPEECH_RMS = 0.02;
const SILENCE_MS = 1200;

/**
 * Record one utterance from an open microphone stream and turn it into text with the local recognizer.
 * Ends after SILENCE_MS of quiet following speech, after maxMs, or when stopped. Returns "" when nothing was said.
 */
async function captureAndTranscribe(stream, s, { maxMs, noSpeechMs, quietStatus = false }) {
  const ctx = new AudioContext();
  const source = ctx.createMediaStreamSource(stream);
  const node = ctx.createScriptProcessor(4096, 1, 1);
  const sink = ctx.createGain();
  sink.gain.value = 0; // the processor must be connected to run; nothing is played back
  const chunks = [];
  let spoke = false, quietSince = 0, cancelled = false, unplugged = false;
  const started = performance.now();
  const track = stream.getAudioTracks()[0];
  const audio = await new Promise((resolve) => {
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      node.disconnect(); source.disconnect();
      if (track) track.onended = null;
      active = null;
      resolve(chunks);
    };
    if (track) track.onended = () => { unplugged = true; finish(); };
    node.onaudioprocess = (e) => {
      if (voice.speaking) return; // half-duplex: never record the agent's own voice
      const data = e.inputBuffer.getChannelData(0);
      let sum = 0;
      for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
      const rms = Math.sqrt(sum / data.length), t = performance.now();
      voice.level = Math.min(1, rms * 8);
      if (spoke || rms > SPEECH_RMS) chunks.push(new Float32Array(data));
      else { chunks.length = 0; chunks.push(new Float32Array(data)); } // keep a little lead-in before speech starts
      emit();
      if (rms > SPEECH_RMS) { spoke = true; quietSince = 0; } else if (spoke && !quietSince) quietSince = t;
      if ((spoke && quietSince && t - quietSince > SILENCE_MS) || t - started > maxMs || (!spoke && noSpeechMs && t - started > noSpeechMs)) finish();
    };
    active = { stop: finish, cancel: () => { cancelled = true; finish(); } };
    source.connect(node);
    node.connect(sink).connect(ctx.destination);
    if (!quietStatus) { setStatus("listening"); micTone(true, s); }
  });
  const rate = ctx.sampleRate;
  ctx.close();
  voice.level = 0;
  if (unplugged) { voice.micError = "The microphone was disconnected. Reconnect it and try again."; throw new Error(voice.micError); }
  if (!quietStatus) micTone(false, s);
  if (cancelled || !spoke) { if (!quietStatus) setStatus("idle"); return ""; }
  setStatus("transcribing");
  const wav = encodeWav(downsample(audio, rate));
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 25000);
  try {
    const r = await api("/api/voice/transcribe", { method: "POST", rawBody: wav, contentType: "audio/wav", signal: abort.signal });
    voice.lastHeard = r.text || "";
    return r.text || "";
  } catch (err) {
    if (err.name === "AbortError") throw new Error("Speech recognition took too long. Try a shorter command.");
    throw err;
  } finally { clearTimeout(timer); }
}

// ---- wake-name mode: the microphone stays open, and only sentences that start with the agent's command name are sent ----

let wakeLoop = null;

function normalizeWords(t) { return (t || "").toLowerCase().replace(/[^a-z0-9' ]+/g, " ").replace(/\s+/g, " ").trim(); }

/** True if the sentence starts with the command name ("Hami, run test case 12", "hey Hami …"). */
export function addressed(text, s = voice.settings) {
  const name = normalizeWords(s?.agent_command_name || "Hami");
  const t = normalizeWords(text).replace(/^(hey|ok|okay|hi)\s+/, "");
  return Boolean(name) && (t === name || t.startsWith(name + " "));
}

export function wakeActive() { return Boolean(wakeLoop); }

/** Start or stop wake-name listening (built-in or local recognizer only). */
export async function setWake(on) {
  if (!on) { wakeLoop?.stop(); return; }
  if (wakeLoop) return;
  const s = await loadSettings();
  if (s.voice_enabled === false || !["builtin", "local"].includes(s.stt_engine)) throw new Error("Wake-name listening needs the built-in offline recognizer or a local speech server.");
  const stream = await openMic();
  let running = true;
  wakeLoop = { stop: () => { running = false; active?.cancel?.(); } };
  voice.wake = true; emit();
  (async () => {
    try {
      while (running && stream.getAudioTracks()[0]?.readyState === "live") {
        await quietMic(300);
        if (!running) break;
        if (voice.status === "thinking" || voice.status === "transcribing") { await new Promise((r) => setTimeout(r, 300)); continue; }
        let text = "";
        try { text = await captureAndTranscribe(stream, s, { maxMs: 12000, noSpeechMs: 0, quietStatus: true }); }
        catch (err) { voice.error = err.message; if (voice.micError) break; continue; }
        if (!running) break;
        if (text && addressed(text, s)) { ackTone(s); await sendCommand(text, "speech").catch(() => null); }
        else if (voice.status === "transcribing") setStatus(voice.executing ? "executing" : "idle");
      }
    } finally {
      stream.getTracks().forEach((t) => t.stop());
      wakeLoop = null; voice.wake = false; voice.level = 0;
      if (voice.status === "listening" || voice.status === "transcribing") setStatus("idle");
      emit();
    }
  })();
}

window.addEventListener("pagehide", () => { wakeLoop?.stop(); active?.cancel?.(); });

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
