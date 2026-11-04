// Global voice agent (23.3): the compact assistant in the shared top bar of every page.
//
// * Wake-name listening starts by itself when the dashboard opens (setting "wake_listening", on by default), with the
//   command name from Voice & Command Agent. The browser still decides about the microphone: if it asks, blocks or
//   holds audio until the first click, the assistant says so instead of claiming to listen.
// * It lives in the shell (index.html), outside the page area, so it keeps listening while pages change; the
//   conversation and its context are the voice client's (voice.js) and survive navigation.
// * Mute releases the microphone for this browser session; Stop turns wake listening off (saved, survives restarts).
// * A microphone that drops out (unplugged, taken by another app) is retried with back-off; a refused permission is
//   not retried until the browser reports it allowed again.

import { api } from "./api.js";
import { esc, toast } from "./util.js";
import {
  voice, onVoice, loadSettings, agentName, wakeName, wakeCapable, setWake, wakeActive, listen, sendCommand,
  stopSpeaking, cancelListening, settingsChanged,
} from "./voice.js";

const MUTE_KEY = "qa.voice.muted";
const RETRY_SECONDS = [2, 5, 10, 30, 60];
const MIC_SVG = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12Z"/></svg>`;

let root = null;
let permission = "unknown"; // granted | prompt | denied | unknown (from the browser's Permissions API)
let retries = 0, retryTimer = null, lastEnd = null;
let bubbleTimer = null, lastLogLen = 0;
let ensuring = false;
let holding = false; // push-to-talk has the microphone; wake listening resumes afterwards

function readMuted() { try { return sessionStorage.getItem(MUTE_KEY) === "1"; } catch { return false; } }
function writeMuted(on) { try { if (on) sessionStorage.setItem(MUTE_KEY, "1"); else sessionStorage.removeItem(MUTE_KEY); } catch { /* ignore */ } }

/** Wake listening is wanted: enabled in settings, possible with this engine, and not muted for this session. */
export function wakeWanted(s = voice.settings) { return wakeCapable(s) && s.wake_listening !== false && !voice.muted; }

/** Start wake listening if it is wanted and not running. Never starts a second session. */
export async function ensureListening() {
  if (ensuring || holding || wakeActive() || !wakeWanted()) return;
  if (permission === "denied") { voice.wakeState = "blocked"; render(); return; }
  ensuring = true;
  try { await setWake(true); }
  catch (err) { voice.error = err.message; }
  finally { ensuring = false; render(); }
}

/** The saved on/off switch (Voice & Command Agent, Command Center and this panel all use it). */
export async function setWakeListening(on) {
  const next = (({ ok, ...rest }) => rest)(await api("/api/voice/settings", { method: "PUT", body: { wake_listening: Boolean(on) } }));
  if (on) { voice.muted = false; writeMuted(false); }
  settingsChanged(next);
  if (on) await setWake(true).catch((err) => { voice.error = err.message; throw err; });
  else setWake(false);
}

export function setMuted(on) {
  voice.muted = Boolean(on); writeMuted(voice.muted);
  if (voice.muted) { setWake(false); cancelListening(); } else ensureListening();
  render();
}

function scheduleRetry() {
  clearTimeout(retryTimer);
  const wait = RETRY_SECONDS[Math.min(retries, RETRY_SECONDS.length - 1)];
  retries += 1;
  retryTimer = setTimeout(() => { retryTimer = null; ensureListening(); }, wait * 1000);
}

// What the orb shows. Each state is the real one: "listening" only while the microphone is open.
export function assistantState() {
  const s = voice.settings;
  if (!s) return { key: "off", label: "Voice loading…" };
  if (s.voice_enabled === false || s.stt_engine === "off") return { key: "off", label: "Voice off" };
  if (voice.speaking) return { key: "speaking", label: "Speaking" };
  if (["transcribing", "thinking"].includes(voice.status)) return { key: "processing", label: voice.status === "thinking" ? "Understanding…" : "Processing…" };
  if (voice.status === "executing") return { key: "processing", label: "Working on it…" };
  if (voice.status === "listening" || voice.following) return { key: "active", label: "Listening… speak now" };
  if (voice.muted) return { key: "muted", label: "Microphone muted" };
  if (voice.wakeState === "blocked" || permission === "denied") return { key: "blocked", label: "Microphone blocked", hint: voice.micError || "Allow the microphone for this page in the browser's site settings (the icon left of the address)." };
  if (voice.needsGesture && voice.wake) return { key: "attention", label: "Click to start voice", hint: "The browser starts audio only after you click or press a key on this page once." };
  if (voice.wakeState === "other-tab") return { key: "attention", label: "Listening in another tab", hint: "Only one dashboard tab listens at a time. Close the other tab to listen here." };
  if (voice.wakeState === "starting") return { key: "processing", label: permission === "prompt" ? "Allow the microphone…" : "Starting microphone…", hint: permission === "prompt" ? "The browser is asking for the microphone. Choose Allow." : "" };
  if (voice.wakeState === "on") return { key: "standby", label: `Say "${wakeName()}"` };
  if (voice.wakeState === "error") return { key: "attention", label: "Microphone reconnecting…", hint: voice.micError || voice.error || "" };
  if (!wakeCapable(s)) return { key: "idle", label: "Press to talk", hint: s.stt_engine === "browser" ? "Wake-name listening needs Faster-Whisper or the built-in offline recognizer." : "" };
  if (s.wake_listening === false) return { key: "idle", label: "Wake listening off" };
  return { key: "idle", label: "Voice ready" };
}

function micLive() { return voice.status === "listening" || voice.wakeState === "on"; }

function logHtml() {
  const items = voice.log.slice(-6);
  if (!items.length) return `<p class="muted va-empty">Say "${esc(wakeName())}" and then your instruction, or type it below.</p>`;
  return items.map((m) => `<div class="vmsg ${m.who}"><span class="vwho">${m.who === "you" ? "You" : esc(agentName())}</span><span class="vtext" dir="auto">${esc(m.text)}</span></div>`).join("");
}

function shell() {
  root.innerHTML = `
    <button class="va-orb" id="vaOrb" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="vaPanel">
      ${MIC_SVG}<span class="va-live" aria-hidden="true"></span>
    </button>
    <button class="va-label" id="vaLabel" type="button" tabindex="-1" aria-hidden="true"></button>
    <div class="va-bubble" id="vaBubble" hidden aria-live="polite"></div>
    <div class="va-panel" id="vaPanel" role="dialog" aria-label="Voice assistant" hidden>
      <div class="va-head">
        <span class="va-dot" aria-hidden="true"></span>
        <div class="va-title"><b dir="auto" id="vaName"></b><span id="vaState" aria-live="polite"></span></div>
        <button class="icon-btn va-min" id="vaMin" type="button" aria-label="Minimize the voice assistant" title="Minimize (keeps listening)">&#8211;</button>
      </div>
      <div class="va-hint" id="vaHint" hidden></div>
      <div class="va-log" id="vaLog"></div>
      <div class="va-confirm" id="vaConfirm" hidden><span>Confirm this operation?</span>
        <button class="btn primary" type="button" id="vaYes">Confirm</button><button class="btn" type="button" id="vaNo">Cancel</button></div>
      <form class="va-typed" id="vaTyped"><input name="text" placeholder="Type an instruction" autocomplete="off" maxlength="500" dir="auto" aria-label="Type an instruction"><button class="btn primary" type="submit">Send</button></form>
      <div class="va-controls">
        <button class="btn" type="button" id="vaTalk">Talk now</button>
        <button class="btn" type="button" id="vaMute" aria-pressed="false">Mute mic</button>
        <button class="btn" type="button" id="vaWake"></button>
        <button class="btn" type="button" id="vaStopSpeak">Stop speaking</button>
      </div>
      <div class="va-foot"><a href="#/command-center" id="vaCenter">Open Voice Command Center</a><a href="#/voice">Voice settings</a></div>
    </div>`;
}

function setOpen(open) {
  const panel = root.querySelector("#vaPanel");
  panel.hidden = !open;
  root.querySelector("#vaOrb").setAttribute("aria-expanded", String(open));
  if (open) { root.querySelector("#vaBubble").hidden = true; const log = root.querySelector("#vaLog"); log.scrollTop = log.scrollHeight; }
}

let shownKey = "";
function render() {
  if (!root) return;
  const st = assistantState();
  const s = voice.settings || {};
  root.dataset.state = st.key;
  root.classList.toggle("mic-live", micLive());
  const orb = root.querySelector("#vaOrb");
  const title = `${agentName()}: ${st.label}${micLive() ? " (microphone on)" : ""}`;
  orb.title = title; orb.setAttribute("aria-label", `Voice assistant. ${st.label}`);
  root.querySelector("#vaLabel").textContent = st.label;
  const key = `${st.key}|${st.label}|${st.hint}|${voice.log.length}|${voice.pending?.kind}|${voice.muted}|${s.wake_listening}|${wakeCapable(s)}|${agentName()}`;
  if (key === shownKey) return;
  shownKey = key;
  root.querySelector("#vaName").textContent = agentName();
  root.querySelector("#vaState").textContent = st.label;
  const hint = root.querySelector("#vaHint");
  hint.hidden = !st.hint; hint.textContent = st.hint || "";
  const log = root.querySelector("#vaLog");
  log.innerHTML = logHtml(); log.scrollTop = log.scrollHeight;
  root.querySelector("#vaConfirm").hidden = voice.pending?.kind !== "confirm";
  const mute = root.querySelector("#vaMute");
  mute.textContent = voice.muted ? "Unmute mic" : "Mute mic";
  mute.setAttribute("aria-pressed", String(voice.muted));
  mute.hidden = !wakeCapable(s) || s.wake_listening === false;
  const wake = root.querySelector("#vaWake");
  wake.hidden = !wakeCapable(s);
  wake.textContent = s.wake_listening === false ? `Listen for "${wakeName()}"` : "Stop listening";
  root.querySelector("#vaTalk").disabled = s.voice_enabled === false || s.stt_engine === "off";
  // A short bubble with the newest line, when the panel is closed (the Command Center shows its own log).
  if (voice.log.length !== lastLogLen) {
    const fresh = voice.log.length > lastLogLen;
    lastLogLen = voice.log.length;
    const bubble = root.querySelector("#vaBubble");
    if (fresh && root.querySelector("#vaPanel").hidden && !location.hash.startsWith("#/command-center")) {
      const m = voice.log[voice.log.length - 1];
      bubble.innerHTML = `<span class="vwho">${m.who === "you" ? "You" : esc(agentName())}</span> ${esc(m.text)}`;
      bubble.hidden = false;
      clearTimeout(bubbleTimer);
      bubbleTimer = setTimeout(() => { bubble.hidden = true; }, 8000);
    }
  }
}

async function watchPermission() {
  try {
    const p = await navigator.permissions.query({ name: "microphone" });
    permission = p.state;
    p.addEventListener("change", () => {
      permission = p.state;
      if (p.state === "granted") { voice.micError = null; if (voice.wakeState === "blocked") voice.wakeState = "off"; retries = 0; ensureListening(); }
      if (p.state === "denied") setWake(false);
      render();
    });
  } catch { permission = "unknown"; } // older browsers: getUserMedia reports it
}

function bind() {
  const $ = (sel) => root.querySelector(sel);
  const run = (fn) => Promise.resolve().then(fn).catch((err) => { render(); if (err?.message) toast(err.message, "error"); });
  $("#vaOrb").addEventListener("click", (e) => { e.stopPropagation(); setOpen($("#vaPanel").hidden); if (voice.wakeState === "blocked" || voice.wakeState === "error") run(ensureListening); });
  $("#vaLabel").addEventListener("click", (e) => { e.stopPropagation(); setOpen(true); });
  $("#vaBubble").addEventListener("click", (e) => { e.stopPropagation(); setOpen(true); });
  $("#vaMin").addEventListener("click", () => setOpen(false));
  $("#vaPanel").addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", () => { if (!$("#vaPanel").hidden) setOpen(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#vaPanel").hidden) { setOpen(false); $("#vaOrb").focus(); } });
  $("#vaTalk").addEventListener("click", () => run(talkNow));
  $("#vaMute").addEventListener("click", () => setMuted(!voice.muted));
  $("#vaWake").addEventListener("click", () => run(() => setWakeListening(voice.settings?.wake_listening === false)));
  $("#vaStopSpeak").addEventListener("click", () => stopSpeaking());
  $("#vaTyped").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = e.target.text.value;
    e.target.text.value = "";
    run(() => sendCommand(text, "typed"));
  });
  $("#vaYes").addEventListener("click", () => run(() => sendCommand("confirm")));
  $("#vaNo").addEventListener("click", () => run(() => sendCommand("cancel")));
  $("#vaCenter").addEventListener("click", () => setOpen(false));
}

/** Push-to-talk from anywhere: pauses wake listening (one microphone session at a time), then resumes it. */
export async function talkNow() {
  const s = await loadSettings();
  if (s.stt_engine === "off" || (s.stt_engine === "local" && !s.stt_url)) { location.hash = "#/voice"; toast("Set up speech recognition first, or type a command here.", "info"); return null; }
  if (holding) { cancelListening(); return null; }
  holding = true;
  try {
    if (wakeActive()) { setWake(false); for (let i = 0; i < 20 && wakeActive(); i++) await new Promise((r) => setTimeout(r, 50)); }
    return await listen();
  } finally { holding = false; ensureListening(); }
}

function onChange() {
  // Recover from a dropped microphone; never retry a refused permission or a session the user stopped.
  if (voice.wakeEnd && voice.wakeEnd !== lastEnd) {
    lastEnd = voice.wakeEnd;
    if (voice.wakeEnd.reason === "error" && !voice.wakeEnd.blocked && wakeWanted()) scheduleRetry();
  }
  if (voice.wakeState === "on") retries = 0;
  // Settings changed elsewhere (Voice & Command Agent, Command Center): follow them.
  const want = wakeWanted();
  if (!want && wakeActive() && !voice.muted) setWake(false);
  else if (want && !holding && !wakeActive() && !retryTimer && voice.wakeState === "off") ensureListening();
  render();
}

/** Mount the assistant in the shared layout and start wake listening when it is enabled. */
export async function initVoiceAgent(el) {
  if (!el || root) return;
  root = el;
  voice.muted = readMuted();
  shell();
  bind();
  onVoice(() => { if (retryTimer && voice.wakeState === "on") { clearTimeout(retryTimer); retryTimer = null; } onChange(); });
  window.addEventListener("hashchange", () => { root.querySelector("#vaBubble").hidden = true; });
  if (navigator.mediaDevices?.addEventListener) {
    navigator.mediaDevices.addEventListener("devicechange", () => { if (wakeWanted() && !wakeActive()) { retries = 0; ensureListening(); } });
  }
  render();
  await watchPermission();
  try { await loadSettings(); } catch { render(); return; }
  if ((voice.settings.stt_engine === "builtin" || (voice.settings.stt_engine === "local" && voice.settings.stt_api === "faster_whisper")) && voice.settings.voice_enabled !== false && voice.settings.wake_listening !== false) {
    api("/api/voice/warmup", { method: "POST", body: {} }).catch(() => {}); // load the offline model before the first sentence
  }
  render();
  await ensureListening();
}
