// Voice & Command Agent settings: the agent's name, how it listens, the radio voice and effects, and the playback
// cursor. Settings are saved in the runtime's settings file on this PC; the conversation itself is on the
// AI Voice Command Center page.

import { api } from "../api.js";
import { esc, toast } from "../util.js";
import { voice, loadSettings, localVoices, speak, stopSpeaking, applyAmbience, agentName } from "../voice.js";
import { testRadio } from "../radio.js";

const ENGINES = {
  builtin: ["Built-in offline recognizer (recommended)", "Speech is recognized on this PC by the bundled offline model (Vosk). No Internet and no speech server are needed."],
  local: ["Local speech server", "A whisper.cpp or OpenAI-compatible speech server on this PC or your network turns speech into text. Nothing leaves your network."],
  browser: ["Browser speech recognition", "Chrome and Edge send the audio to their online service, so this needs Internet. Do not use it on offline PCs."],
  off: ["Off (typed commands only)", "The microphone is not used. Type commands on the Command Center."],
};

const pct = (v, d) => Math.round((Number.isFinite(Number(v)) ? Number(v) : d) * 100);
const check = (name, on, label) => `<label><span><input type="checkbox" name="${name}" ${on ? "checked" : ""}> ${label}</span></label>`;
const range = (name, value, label) => `<label>${label} <span class="range"><input type="range" name="${name}" min="0" max="100" step="5" value="${value}"><output>${value}%</output></span></label>`;

function formHtml(s, radio, pb) {
  const voices = localVoices();
  return `
    <form class="settings" id="vset" style="max-width:none">
      <div class="grid2">
        <section class="card">
          <h3>Agent identity</h3>
          <label>Display name <input name="agent_display_name" value="${esc(s.agent_display_name || "HAMI")}" maxlength="40" dir="auto" required></label>
          <p class="muted">Shown on every page and in replies. Any language, for example an Arabic name, works.</p>
          <label>Command name <input name="agent_command_name" value="${esc(s.agent_command_name || "Hami")}" maxlength="30" pattern="[A-Za-z][A-Za-z' \\-]*" required></label>
          <p class="muted">The word you say to address the agent ("${esc(s.agent_command_name || "Hami")}, run test case 310"). Use Latin letters so the offline recognizer can hear it.</p>
          <label>Reply wording <select name="phrase_style">
            <option value="standard" ${s.phrase_style !== "tactical" ? "selected" : ""}>Standard: "OK, I will do your task now."</option>
            <option value="tactical" ${s.phrase_style === "tactical" ? "selected" : ""}>Tactical: "Command received. Executing now."</option></select></label>
        </section>
        <section class="card">
          <h3>Listening</h3>
          ${check("voice_enabled", s.voice_enabled !== false, "Voice on (microphone and spoken replies)")}
          <label>Listening mode <select name="listen_mode">
            <option value="push_to_talk" ${s.listen_mode !== "wake_name" ? "selected" : ""}>Push-to-talk: press the mic, speak, it stops on silence</option>
            <option value="wake_name" ${s.listen_mode === "wake_name" ? "selected" : ""}>Wake name: the mic stays open; only sentences that start with the command name are used</option></select></label>
          <label>Speech recognition <select name="stt_engine">${Object.entries(ENGINES).map(([k, [l]]) => `<option value="${k}" ${k === s.stt_engine ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
          <p class="muted" id="engineHelp">${esc(ENGINES[s.stt_engine]?.[1] || "")}</p>
          <div data-builtin ${s.stt_engine === "builtin" ? "" : "hidden"}>
            <label>Speech model folder (optional) <input name="stt_model_dir" value="${esc(s.stt_model_dir || "")}" placeholder="bundled model in runtime\\speech"></label>
          </div>
          <div data-local ${s.stt_engine === "local" ? "" : "hidden"}>
            <label>Server type <select name="stt_api">
              <option value="whisper_cpp" ${s.stt_api === "whisper_cpp" ? "selected" : ""}>whisper.cpp server (/inference)</option>
              <option value="openai" ${s.stt_api === "openai" ? "selected" : ""}>OpenAI-compatible (/audio/transcriptions)</option></select></label>
            <label>Server URL <input name="stt_url" value="${esc(s.stt_url)}" placeholder="http://127.0.0.1:8081"></label>
            <label data-openai ${s.stt_api === "openai" ? "" : "hidden"}>Model name <input name="stt_model" value="${esc(s.stt_model)}" placeholder="as the server lists it"></label>
          </div>
          <label>Language <input name="language" value="${esc(s.language)}" maxlength="5" style="width:90px"></label>
        </section>
        <section class="card">
          <h3>Voice reply</h3>
          ${check("speak_replies", s.speak_replies, "Speak replies aloud")}
          <label>Reply style <select name="reply_style">
            <option value="radio" ${s.reply_style === "radio" ? "selected" : ""}>Pilot radio</option>
            <option value="plain" ${s.reply_style === "plain" ? "selected" : ""}>Plain voice</option></select></label>
          <label>Radio voice (Windows) <select name="radio_voice" ${radio.available ? "" : "disabled"}><option value="">Adult male voice (default)</option>
            ${radio.voices.map((v) => `<option value="${esc(v.name)}" ${v.name === s.radio_voice ? "selected" : ""}>${esc(v.name)} (${esc(v.gender)}, ${esc(v.lang)})</option>`).join("")}</select></label>
          <p class="muted">${radio.available ? `Installed Windows voices: ${radio.voices.map((v) => esc(v.name)).join(", ") || "none"}. They are rendered on this PC, so replies work offline.` : "No Windows voices were found by the runtime, so the browser's installed voice is used with the radio beeps and static around it."}</p>
          <label>Browser voice (fallback) <select name="tts_voice"><option value="">Male voice for the language</option>
            ${voices.map((v) => `<option value="${esc(v.name)}" ${v.name === s.tts_voice ? "selected" : ""}>${esc(v.name)} (${esc(v.lang)})</option>`).join("")}</select></label>
          <p class="muted">Installed browser voices: ${voices.length ? voices.map((v) => esc(v.name)).join(", ") : "none found"}.</p>
          <label>Speaking speed <input type="number" name="tts_rate" min="0.5" max="2" step="0.1" value="${esc(s.tts_rate)}" style="width:90px"></label>
          ${range("voice_volume", pct(s.voice_volume, 0.9), "Voice volume")}
        </section>
        <section class="card">
          <h3>Radio effect and background audio</h3>
          ${range("radio_intensity", pct(s.radio_intensity, 0.5), "Radio intensity")}
          ${check("wireless_tone", s.wireless_tone !== false, "Wireless tones (radio-open and radio-close beeps)")}
          ${range("static_volume", pct(s.static_volume, 0.25), "Wireless static volume")}
          ${check("ambience_enabled", s.ambience_enabled, "Tactical background ambience")}
          ${range("ambience_volume", pct(s.ambience_volume, 0.2), "Background volume")}
          <p class="muted">The ambience drops to almost silent while you speak and stays very low while the agent speaks. Effects are generated on this PC and never reach the microphone.</p>
        </section>
        <section class="card">
          <h3>Playback cursor</h3>
          ${check("visual_cursor", pb.visual_cursor !== false, "Show the playback cursor")}
          ${check("highlight", pb.highlight !== false, "Highlight the element before each action")}
          <label>Cursor movement time (ms) <input type="number" name="cursor_speed_ms" min="0" max="2000" step="50" value="${pb.cursor_speed_ms ?? 450}" style="width:110px"></label>
          ${check("click_sound", pb.click_sound !== false, "Quiet click sound")}
          ${range("click_volume", pct(pb.click_volume, 0.35), "Click volume")}
        </section>
      </div>
      <div class="row"><button class="btn primary" type="submit">Save</button>
        <button class="btn" type="button" id="testVoice">Test Voice</button>
        <button class="btn" type="button" id="testRadio">Test Radio</button>
        <button class="btn" type="button" id="stopSpeak">Stop Speaking</button>
        <button class="btn" type="button" id="diag">Check voice setup</button>
        <a class="btn" href="#/command-center">Open Command Center</a></div>
      <div id="diagOut"></div>
    </form>`;
}

function readForm(form) {
  const f = (n) => form.elements[n];
  const frac = (n) => Number(f(n).value) / 100;
  return {
    voice: {
      agent_display_name: f("agent_display_name").value.trim(), agent_command_name: f("agent_command_name").value.trim(),
      phrase_style: f("phrase_style").value, voice_enabled: f("voice_enabled").checked, listen_mode: f("listen_mode").value,
      stt_engine: f("stt_engine").value, stt_api: f("stt_api").value, stt_url: f("stt_url").value, stt_model: f("stt_model").value,
      stt_model_dir: f("stt_model_dir").value.trim(), language: f("language").value.trim() || "en",
      speak_replies: f("speak_replies").checked, reply_style: f("reply_style").value, radio_voice: f("radio_voice").value,
      tts_voice: f("tts_voice").value, tts_rate: Number(f("tts_rate").value) || 1, voice_volume: frac("voice_volume"),
      radio_intensity: frac("radio_intensity"), wireless_tone: f("wireless_tone").checked, static_volume: frac("static_volume"),
      ambience_enabled: f("ambience_enabled").checked, ambience_volume: frac("ambience_volume"),
    },
    playback: {
      visual_cursor: f("visual_cursor").checked, highlight: f("highlight").checked,
      cursor_speed_ms: Math.max(0, Math.min(2000, Number(f("cursor_speed_ms").value) || 0)),
      click_sound: f("click_sound").checked, click_volume: frac("click_volume"),
    },
  };
}

async function render(view) {
  const s = await loadSettings(true);
  const [radio, all] = await Promise.all([
    api("/api/voice/radio-voices").catch(() => ({ available: false, voices: [] })),
    api("/api/settings").catch(() => ({ settings: { playback: {} } })),
  ]);
  view.innerHTML = `
    <div class="page-head"><div><h2>Voice &amp; Command Agent</h2>
      <p class="muted">Settings for <b dir="auto">${esc(agentName())}</b>. Everything runs on this PC: offline recognition, installed voices and locally generated radio effects.</p></div></div>
    ${formHtml(s, radio, all.settings.playback || {})}`;

  const form = view.querySelector("#vset");
  form.querySelectorAll(".range input").forEach((r) => r.addEventListener("input", () => { r.nextElementSibling.textContent = `${r.value}%`; }));
  form.stt_engine.addEventListener("change", () => {
    view.querySelector("#engineHelp").textContent = ENGINES[form.stt_engine.value][1];
    view.querySelector("[data-local]").hidden = form.stt_engine.value !== "local";
    view.querySelector("[data-builtin]").hidden = form.stt_engine.value !== "builtin";
  });
  form.stt_api.addEventListener("change", () => { view.querySelector("[data-openai]").hidden = form.stt_api.value !== "openai"; });
  // Ambience changes are heard at once, before saving.
  ["ambience_enabled", "ambience_volume"].forEach((n) => form.elements[n].addEventListener("input", () => {
    const v = readForm(form).voice;
    applyAmbience({ ambience_enabled: v.ambience_enabled, ambience_volume: v.ambience_volume });
  }));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const { voice: vpatch, playback } = readForm(form);
    try {
      const saved = await api("/api/voice/settings", { method: "PUT", body: vpatch });
      await api("/api/settings", { method: "PATCH", body: { playback } });
      voice.settings = (({ ok, ...rest }) => rest)(saved);
      applyAmbience();
      const foot = document.getElementById("agentNameFoot");
      if (foot) foot.textContent = agentName();
      toast("Voice & Command Agent settings saved", "success");
      render(view);
    } catch (err) { toast(err.message, "error"); }
  });
  view.querySelector("#testVoice").addEventListener("click", async () => {
    const trial = { ...voice.settings, ...readForm(form).voice, speak_replies: true, voice_enabled: true };
    if (trial.radio_voice !== voice.settings.radio_voice || trial.tts_rate !== voice.settings.tts_rate) toast("Save first to hear a different Windows voice or speed.", "info");
    await speak(`${trial.agent_display_name} on frequency. Radio check, loud and clear. Ready for your instructions.`, trial);
  });
  view.querySelector("#testRadio").addEventListener("click", () => testRadio(readForm(form).voice).catch((err) => toast(err.message, "error")));
  view.querySelector("#stopSpeak").addEventListener("click", stopSpeaking);
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
      else {
        try {
          const inputs = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
          if (!inputs.length) { mic = "FAIL"; micDetail = "No microphone is connected."; }
          else micDetail += ` ${inputs.length} input device${inputs.length > 1 ? "s" : ""} found.`;
        } catch { /* listing devices is optional */ }
      }
    }
    if (voice.micError) { mic = "FAIL"; micDetail = voice.micError; }
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
  if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = null;
}
