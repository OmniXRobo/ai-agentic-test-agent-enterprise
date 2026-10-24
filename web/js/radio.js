// Radio transmission effect for spoken replies: squelch, a band-limited "headset" voice, static and a roger beep.
// Everything is generated in the browser with Web Audio; no sound files and no Internet.

let ctx = null;

export function audioContext() {
  if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  return ctx;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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

function squelch(c, at) {
  const src = c.createBufferSource(), bp = c.createBiquadFilter(), g = c.createGain();
  src.buffer = noiseBuffer(c);
  bp.type = "bandpass"; bp.frequency.value = 2200; bp.Q.value = 0.8;
  g.gain.setValueAtTime(0, at);
  g.gain.linearRampToValueAtTime(0.22, at + 0.01);
  g.gain.exponentialRampToValueAtTime(0.001, at + 0.13);
  src.connect(bp).connect(g).connect(c.destination);
  src.start(at, Math.random()); src.stop(at + 0.15);
}

function rogerBeep(c, at) {
  [[1200, 0], [1600, 0.07]].forEach(([f, dt]) => {
    const o = c.createOscillator(), g = c.createGain();
    o.type = "square"; o.frequency.value = f;
    g.gain.setValueAtTime(0.0001, at + dt);
    g.gain.linearRampToValueAtTime(0.05, at + dt + 0.005);
    g.gain.setValueAtTime(0.05, at + dt + 0.055);
    g.gain.linearRampToValueAtTime(0.0001, at + dt + 0.065);
    o.connect(g).connect(c.destination);
    o.start(at + dt); o.stop(at + dt + 0.07);
  });
}

function staticBed(c, at) {
  const src = c.createBufferSource(), bp = c.createBiquadFilter(), g = c.createGain();
  src.buffer = noiseBuffer(c); src.loop = true;
  bp.type = "bandpass"; bp.frequency.value = 1400; bp.Q.value = 0.5;
  g.gain.setValueAtTime(0, at);
  g.gain.linearRampToValueAtTime(0.035, at + 0.05);
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
function radioChain(c) {
  const hp = c.createBiquadFilter(), lp = c.createBiquadFilter(), mid = c.createBiquadFilter();
  const shaper = c.createWaveShaper(), comp = c.createDynamicsCompressor(), out = c.createGain();
  hp.type = "highpass"; hp.frequency.value = 380;
  lp.type = "lowpass"; lp.frequency.value = 3100;
  mid.type = "peaking"; mid.frequency.value = 1700; mid.Q.value = 1; mid.gain.value = 7;
  shaper.curve = drive(6); shaper.oversample = "2x";
  comp.threshold.value = -26; comp.ratio.value = 8; comp.attack.value = 0.003; comp.release.value = 0.12;
  out.gain.value = 0.9;
  hp.connect(lp).connect(mid).connect(shaper).connect(comp).connect(out).connect(c.destination);
  return hp;
}

/** Play WAV bytes as a radio transmission. Resolves when the transmission ends. */
export async function playRadio(wav) {
  const c = audioContext();
  const buffer = await c.decodeAudioData(wav);
  const t0 = c.currentTime + 0.03;
  squelch(c, t0);
  const bed = staticBed(c, t0);
  const src = c.createBufferSource();
  src.buffer = buffer;
  src.connect(radioChain(c));
  const ended = new Promise((r) => { src.onended = r; });
  src.start(t0 + 0.14);
  await ended;
  const t1 = c.currentTime;
  rogerBeep(c, t1 + 0.05);
  bed.stop(t1 + 0.2);
  squelch(c, t1 + 0.22);
  await sleep(420);
}

/** Static and squelch around speech the browser plays itself (its audio cannot be filtered). */
export async function radioAround(speakFn) {
  let c;
  try { c = audioContext(); } catch { return speakFn(); }
  const t0 = c.currentTime + 0.02;
  squelch(c, t0);
  const bed = staticBed(c, t0);
  await sleep(160);
  try { await speakFn(); } finally {
    const t1 = c.currentTime;
    rogerBeep(c, t1 + 0.03);
    bed.stop(t1 + 0.18);
    squelch(c, t1 + 0.2);
    await sleep(400);
  }
}
