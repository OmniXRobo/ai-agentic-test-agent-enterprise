// Radio transmission effect for spoken replies: open beep, a band-limited "headset" voice with static, close beep,
// plus an optional tactical ambience bed that ducks while the person or the agent talks.
// Everything is generated in the browser with Web Audio; no sound files and no Internet. Nothing here is ever
// connected to the microphone: the effects only go to the speakers.

let ctx = null;

export function audioContext() {
  if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  return ctx;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const num = (v, d) => (Number.isFinite(Number(v)) ? Number(v) : d);

// Effect settings with their defaults (the Voice & Command Agent settings fill them in).
function mix(s = {}) {
  return {
    intensity: Math.max(0, Math.min(1, num(s.radio_intensity, 0.5))),
    tones: s.wireless_tone !== false,
    statics: Math.max(0, Math.min(1, num(s.static_volume, 0.25))),
    voice: Math.max(0, Math.min(1, num(s.voice_volume, 0.9))),
  };
}

let noise = null;
function noiseBuffer(c) {
  if (noise && noise.sampleRate === c.sampleRate) return noise;
  noise = c.createBuffer(1, c.sampleRate * 2, c.sampleRate);
  const d = noise.getChannelData(0);
  for (let i = 0; i < d.length; i++) {
    d[i] = (Math.random() * 2 - 1) * 0.6;
    if (Math.random() < 0.0006) d[i] = Math.random() < 0.5 ? -1 : 1; // crackle
  }
  return noise;
}

function squelch(c, at, level) {
  if (level <= 0) return;
  const src = c.createBufferSource(), bp = c.createBiquadFilter(), g = c.createGain();
  src.buffer = noiseBuffer(c);
  bp.type = "bandpass"; bp.frequency.value = 2200; bp.Q.value = 0.8;
  g.gain.setValueAtTime(0, at);
  g.gain.linearRampToValueAtTime(0.25 * level, at + 0.01);
  g.gain.exponentialRampToValueAtTime(0.001, at + 0.13);
  src.connect(bp).connect(g).connect(c.destination);
  src.start(at, Math.random()); src.stop(at + 0.15);
}

function beep(c, at, tones, volume = 0.05) {
  tones.forEach(([f, dt, len = 0.06]) => {
    const o = c.createOscillator(), g = c.createGain();
    o.type = "square"; o.frequency.value = f;
    g.gain.setValueAtTime(0.0001, at + dt);
    g.gain.linearRampToValueAtTime(volume, at + dt + 0.005);
    g.gain.setValueAtTime(volume, at + dt + len - 0.01);
    g.gain.linearRampToValueAtTime(0.0001, at + dt + len);
    o.connect(g).connect(c.destination);
    o.start(at + dt); o.stop(at + dt + len + 0.01);
  });
}
const openBeep = (c, at) => beep(c, at, [[1600, 0, 0.05]]);
const closeBeep = (c, at) => beep(c, at, [[1200, 0], [1600, 0.07]]); // roger beep

/** A short two-tone chirp: "transmission received", played the moment a command has been heard. */
export function ackTone(s) {
  try {
    const c = audioContext();
    beep(c, c.currentTime + 0.01, [[880, 0, 0.05], [1320, 0.06, 0.07]], 0.04 * (mix(s).voice || 0.5));
  } catch { /* audio is a convenience */ }
}

/** The tone that tells the person the microphone has opened (push-to-talk) and closed. */
export function micTone(open, s) {
  if (s && s.wireless_tone === false) return;
  try {
    const c = audioContext();
    beep(c, c.currentTime + 0.01, open ? [[1046, 0, 0.04]] : [[784, 0, 0.04]], 0.03);
  } catch { /* ignore */ }
}

function staticBed(c, at, level) {
  const src = c.createBufferSource(), bp = c.createBiquadFilter(), g = c.createGain();
  src.buffer = noiseBuffer(c); src.loop = true;
  bp.type = "bandpass"; bp.frequency.value = 1400; bp.Q.value = 0.5;
  g.gain.setValueAtTime(0, at);
  g.gain.linearRampToValueAtTime(0.12 * level, at + 0.05);
  src.connect(bp).connect(g).connect(c.destination);
  src.start(at);
  return { stop: (t) => { g.gain.setValueAtTime(g.gain.value, t); g.gain.linearRampToValueAtTime(0, t + 0.05); src.stop(t + 0.06); } };
}

function drive(amount) {
  const curve = new Float32Array(1024);
  for (let i = 0; i < curve.length; i++) {
    const x = (i * 2) / curve.length - 1;
    curve[i] = ((1 + amount) * x) / (1 + amount * Math.abs(x));
  }
  return curve;
}

// Voice -> narrow band (like a headset radio) -> light distortion -> compression -> speakers.
// Intensity 0 is a clean, slightly band-limited voice; 1 is a heavy field radio.
function radioChain(c, m) {
  const hp = c.createBiquadFilter(), lp = c.createBiquadFilter(), mid = c.createBiquadFilter();
  const shaper = c.createWaveShaper(), comp = c.createDynamicsCompressor(), out = c.createGain();
  hp.type = "highpass"; hp.frequency.value = 120 + 380 * m.intensity;
  lp.type = "lowpass"; lp.frequency.value = 7000 - 4200 * m.intensity;
  mid.type = "peaking"; mid.frequency.value = 1700; mid.Q.value = 1; mid.gain.value = 2 + 6 * m.intensity;
  shaper.curve = drive(1 + 8 * m.intensity); shaper.oversample = "2x";
  comp.threshold.value = -26; comp.ratio.value = 8; comp.attack.value = 0.003; comp.release.value = 0.12;
  out.gain.value = m.voice;
  hp.connect(lp).connect(mid).connect(shaper).connect(comp).connect(out).connect(c.destination);
  return hp;
}

// ---- tactical ambience: a quiet procedural bed (engine rumble and room tone), ducked while anyone talks ----

const amb = { node: null, gain: null, level: 0.2, duck: "none" };
const DUCK = { none: 1, agent: 0.25, user: 0.03 }; // very low under the agent, almost silent while the person talks

function ambienceTarget() { return 0.18 * amb.level * DUCK[amb.duck]; }

/** Start, update or stop the ambience bed. */
export function setAmbience(on, level) {
  amb.level = Math.max(0, Math.min(1, num(level, amb.level)));
  if (!on) {
    if (amb.node) {
      const c = audioContext(), t = c.currentTime;
      amb.gain.gain.setTargetAtTime(0, t, 0.2);
      const n = amb.node; amb.node = null;
      setTimeout(() => { try { n.stop(); } catch { /* already stopped */ } }, 800);
    }
    return;
  }
  const c = audioContext();
  if (!amb.node) {
    const len = c.sampleRate * 4, buf = c.createBuffer(1, len, c.sampleRate), d = buf.getChannelData(0);
    let last = 0;
    for (let i = 0; i < len; i++) { last = (last + 0.02 * (Math.random() * 2 - 1)) / 1.02; d[i] = last * 3.5; } // brown noise
    const src = c.createBufferSource(), lp = c.createBiquadFilter(), g = c.createGain();
    src.buffer = buf; src.loop = true;
    lp.type = "lowpass"; lp.frequency.value = 260;
    g.gain.value = 0;
    src.connect(lp).connect(g).connect(c.destination);
    src.start();
    amb.node = src; amb.gain = g;
  }
  amb.gain.gain.setTargetAtTime(ambienceTarget(), c.currentTime, 0.3);
}

/** Who is talking now: "user" (microphone open), "agent" (speaking) or "none". */
export function duck(who) {
  amb.duck = DUCK[who] ? who : "none";
  if (amb.node) amb.gain.gain.setTargetAtTime(ambienceTarget(), audioContext().currentTime, 0.08);
}

// ---- transmissions ----

let current = null; // {stop}

/** Stop the transmission being played now (Stop Speaking). */
export function stopRadio() { current?.stop(); current = null; }

/** Play WAV bytes as a radio transmission. Resolves when the transmission ends. */
export async function playRadio(wav, s) {
  const c = audioContext(), m = mix(s);
  const buffer = await c.decodeAudioData(wav);
  const t0 = c.currentTime + 0.03;
  if (m.tones) { openBeep(c, t0); squelch(c, t0 + 0.06, m.statics + 0.2); }
  const bed = staticBed(c, t0, m.statics * (0.4 + m.intensity));
  const src = c.createBufferSource();
  src.buffer = buffer;
  src.connect(radioChain(c, m));
  let stopped = false;
  const ended = new Promise((r) => { src.onended = r; });
  current = { stop: () => { stopped = true; try { src.stop(); } catch { /* not started */ } } };
  src.start(t0 + (m.tones ? 0.2 : 0.05));
  await ended;
  current = null;
  const t1 = c.currentTime;
  if (m.tones && !stopped) closeBeep(c, t1 + 0.05);
  bed.stop(t1 + (stopped ? 0 : 0.2));
  if (m.tones && !stopped) squelch(c, t1 + 0.22, m.statics + 0.2);
  await sleep(stopped ? 60 : 420);
}

/** Beeps and static around speech the browser plays itself (its audio cannot be filtered). */
export async function radioAround(speakFn, s) {
  let c;
  try { c = audioContext(); } catch { return speakFn(); }
  const m = mix(s);
  const t0 = c.currentTime + 0.02;
  if (m.tones) { openBeep(c, t0); squelch(c, t0 + 0.06, m.statics + 0.2); }
  const bed = staticBed(c, t0, m.statics * (0.3 + m.intensity) * 0.6);
  await sleep(m.tones ? 220 : 60);
  try { await speakFn(); } finally {
    const t1 = c.currentTime;
    if (m.tones) closeBeep(c, t1 + 0.03);
    bed.stop(t1 + 0.18);
    if (m.tones) squelch(c, t1 + 0.2, m.statics + 0.2);
    await sleep(400);
  }
}

/** Test Radio: the open beep, static and the close beep with no voice. */
export async function testRadio(s) {
  const c = audioContext(), m = { ...mix(s), tones: true };
  const t0 = c.currentTime + 0.03;
  openBeep(c, t0); squelch(c, t0 + 0.06, m.statics + 0.2);
  const bed = staticBed(c, t0, Math.max(0.15, m.statics) * (0.4 + m.intensity));
  await sleep(1100);
  const t1 = c.currentTime;
  closeBeep(c, t1); bed.stop(t1 + 0.15); squelch(c, t1 + 0.17, m.statics + 0.2);
  await sleep(400);
}
