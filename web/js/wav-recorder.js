// Push-to-talk recorder: 16 kHz mono WAV, recorded only between an explicit Start and Stop (or maxMs).
// Shared by the SkyGuard AI Operations Agent page and the Chrome/Edge extension side panel. Audio never leaves this
// PC except to the local speech recognizer (SkyGuard's Faster-Whisper, or the companion's).
export async function startRecording({ maxMs = 30000, onLevel = () => {} } = {}) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  const ctx = new AudioContext();
  if (ctx.state !== "running") await ctx.resume().catch(() => {});
  const source = ctx.createMediaStreamSource(stream);
  const node = ctx.createScriptProcessor(4096, 1, 1);
  const sink = ctx.createGain();
  sink.gain.value = 0;
  const chunks = [];
  node.onaudioprocess = (e) => {
    const data = e.inputBuffer.getChannelData(0);
    chunks.push(new Float32Array(data));
    let sum = 0; for (let i = 0; i < data.length; i += 16) sum += data[i] * data[i];
    onLevel(Math.min(1, Math.sqrt(sum / (data.length / 16)) * 4));
  };
  source.connect(node); node.connect(sink); sink.connect(ctx.destination);
  let finish;
  const done = new Promise((resolve) => { finish = resolve; });
  const timer = setTimeout(() => stop(), maxMs);
  let stopped = false;
  function stop() {
    if (stopped) return done;
    stopped = true;
    clearTimeout(timer);
    node.disconnect(); source.disconnect();
    stream.getTracks().forEach((t) => t.stop());
    const rate = ctx.sampleRate;
    ctx.close();
    finish(encodeWav(downsample(chunks, rate, 16000), 16000));
    return done;
  }
  return { stop, done };
}

function downsample(chunks, from, to) {
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const input = new Float32Array(total);
  let o = 0; for (const c of chunks) { input.set(c, o); o += c.length; }
  if (from === to) return input;
  const ratio = from / to;
  const out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const start = Math.floor(i * ratio), end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let s = 0; for (let j = start; j < end; j++) s += input[j];
    out[i] = s / Math.max(1, end - start);
  }
  return out;
}

function encodeWav(samples, rate) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); w(8, "WAVE"); w(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data");
  v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) { const s = Math.max(-1, Math.min(1, samples[i])); v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true); }
  return new Blob([buf], { type: "audio/wav" });
}
