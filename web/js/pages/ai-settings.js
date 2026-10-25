// AI runtime settings (On-Premise AI or Cloud AI) and PASS/FAIL diagnostics.

import { api } from "../api.js";
import { esc, toast } from "../util.js";
import { loadHealth } from "../store.js";

function checksHtml(r) {
  return `<p><span class="pill ${r.ok ? "PASS" : "FAIL"}">${r.ok ? "PASS" : "FAIL"}</span> ${esc(r.label || "")} ${esc(r.base_url || "")}</p>
    <table class="data"><thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr></thead><tbody>
    ${r.checks.map((c) => `<tr><td>${esc(c.name)}</td><td><span class="pill ${c.status === "SKIP" ? "WAITING" : esc(c.status)}">${esc(c.status)}</span></td><td>${esc(c.detail)}</td></tr>`).join("")}
    </tbody></table>`;
}

export async function mount(view) {
  const cfg = await api("/api/ai/settings");
  view.innerHTML = `
    <form class="settings card" id="aiForm">
      <h2>AI Runtime</h2>
      <label>AI runtime
        <select name="mode">
          <option value="on_premise" ${cfg.mode !== "cloud" ? "selected" : ""}>On-Premise AI</option>
          <option value="cloud" ${cfg.mode === "cloud" ? "selected" : ""}>Cloud AI</option>
        </select>
      </label>
      <fieldset data-mode="on_premise">
        <legend>On-Premise AI</legend>
        <p class="muted">An OpenAI-compatible server on this PC or your network, such as llama.cpp
          <code>llama-server</code> running your Qwen GGUF model, or Ollama. No Internet access is needed, and models are never downloaded.</p>
        <label>Server URL<input name="base_url" value="${esc(cfg.on_premise?.base_url ?? cfg.base_url)}" placeholder="http://127.0.0.1:8080/v1"></label>
        <label>Model
          <span class="row"><input name="model" list="modelList" value="${esc(cfg.on_premise?.model ?? cfg.model)}" placeholder="Leave blank to use the server's loaded model" style="flex:1">
          <button class="btn" type="button" data-load-models>Load models</button></span>
        </label>
      </fieldset>
      <fieldset data-mode="cloud">
        <legend>Cloud AI</legend>
        <p class="muted">Any Cloud AI endpoint that offers the OpenAI-compatible chat completions API. The API key is kept in the
          Windows Credential Manager, never in settings files or logs.</p>
        <label>Endpoint URL<input name="cloud_base_url" value="${esc(cfg.cloud?.base_url || "")}" placeholder="https://your-ai-endpoint/v1"></label>
        <label>Model
          <span class="row"><input name="cloud_model" list="modelList" value="${esc(cfg.cloud?.model || "")}" placeholder="Model or deployment name" style="flex:1">
          <button class="btn" type="button" data-load-models>Load models</button></span>
        </label>
        <label>API key <span class="muted">${cfg.cloud?.has_api_key ? "(saved; leave blank to keep it)" : "(not saved)"}</span>
          <input name="api_key" type="password" autocomplete="new-password" placeholder="${cfg.cloud?.has_api_key ? "••••••••" : "Paste the API key"}"></label>
        ${cfg.cloud?.has_api_key ? '<label class="row"><input type="checkbox" name="clear_api_key"> Remove the saved API key</label>' : ""}
      </fieldset>
      <datalist id="modelList"></datalist>
      <label>Timeout (seconds)<input name="timeout_seconds" type="number" min="5" max="1800" value="${esc(cfg.timeout_seconds)}"></label>
      <label>Temperature<input name="temperature" type="number" step="0.1" min="0" max="2" value="${esc(cfg.temperature)}"></label>
      <label>Max output tokens<input name="max_tokens" type="number" min="256" max="32768" value="${esc(cfg.max_tokens)}"></label>
      <div class="row"><button class="btn primary" type="submit">Save</button><button class="btn" type="button" id="diag">Run diagnostics</button></div>
      <div id="result"></div>
    </form>
    <section class="settings card" id="managed" style="margin-top:14px"><p class="muted">Loading Managed Local LLM…</p></section>`;
  const form = view.querySelector("#aiForm");
  const showMode = () => form.querySelectorAll("fieldset[data-mode]").forEach((fs) => {
    fs.hidden = fs.dataset.mode !== form.elements.mode.value;
  });
  form.elements.mode.addEventListener("change", showMode);
  showMode();
  const save = async () => {
    const f = new FormData(form);
    const body = {
      mode: f.get("mode"), base_url: f.get("base_url"), model: f.get("model"),
      cloud_base_url: f.get("cloud_base_url"), cloud_model: f.get("cloud_model"),
      timeout_seconds: Number(f.get("timeout_seconds")), temperature: Number(f.get("temperature")),
      max_tokens: Number(f.get("max_tokens")),
    };
    if (f.get("api_key")) body.api_key = f.get("api_key");
    if (f.get("clear_api_key")) body.clear_api_key = true;
    await api("/api/ai/settings", { method: "PUT", body });
    form.elements.api_key.value = "";
  };
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await save(); toast(`${form.elements.mode.value === "cloud" ? "Cloud AI" : "On-Premise AI"} settings saved`, "success"); } catch (err) { toast(err.message, "error"); }
  });
  view.querySelectorAll("[data-load-models]").forEach((b) => b.addEventListener("click", async () => {
    try {
      await save();
      const { models } = await api("/api/ai/models");
      view.querySelector("#modelList").innerHTML = models.map((m) => `<option value="${esc(m)}">`).join("");
      toast(models.length ? `Loaded models: ${models.join(", ")}` : "The server reports no models.", models.length ? "success" : "warning");
    } catch (err) { toast(err.message, "error"); }
  }));
  view.querySelector("#diag").addEventListener("click", async (e) => {
    e.target.disabled = true;
    const out = view.querySelector("#result");
    out.innerHTML = '<p class="muted">Running diagnostics. The completion test can take a while on CPU-only machines.</p>';
    try { await save(); out.innerHTML = checksHtml(await api("/api/ai/diagnostics", { method: "POST" })); }
    catch (err) { out.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`; }
    finally { e.target.disabled = false; }
  });
  mountManaged(view.querySelector("#managed"));
}

// ---- Managed Local LLM: start/stop llama-server with the GGUF model (only the process this application started) ----

let followTimer = null;

function managedHtml(st, found) {
  const c = st.config;
  const lamp = { RUNNING: "PASS", STARTING: "RUNNING", FAILED: "FAIL", STOPPED: "WAITING" }[st.state] || "WAITING";
  return `
    <h2>Managed Local LLM</h2>
    <p class="muted">Starts the llama.cpp <code>llama-server</code> with your Qwen GGUF model on this PC and points the On-Premise AI at it.
      Stop ends only the server started here; a server you run yourself is never touched. Opening pages never starts or restarts it.</p>
    <p><span class="pill ${lamp}">${esc(st.state)}</span> ${st.pid ? `PID ${esc(st.pid)} · ` : ""}${esc(st.url)} · model <b>${esc(st.alias)}</b>
      ${st.external_server ? '<br><span class="muted">Another AI server already answers on this address; it is not managed by this application.</span>' : ""}
      ${st.error ? `<br><span class="pill FAIL">${esc(st.error)}</span>` : ""}</p>
    ${st.log_tail ? `<pre class="code" style="max-height:180px;overflow:auto">${esc(st.log_tail)}</pre>` : ""}
    <form id="mForm">
      <label>llama-server file <input name="server_path" value="${esc(c.server_path)}" placeholder="${esc(found?.servers?.[0] || "found automatically in runtime\\ai")}" list="srvList"></label>
      <datalist id="srvList">${(found?.servers || []).map((p) => `<option value="${esc(p)}">`).join("")}</datalist>
      <label>GGUF model <select name="model_path"><option value="">${found?.models?.length ? "First model found" : "Auto detect (none found yet)"}</option>
        ${(found?.models || []).map((m) => `<option value="${esc(m.path)}" ${m.path === c.model_path ? "selected" : ""}>${esc(m.name)} (${esc(m.size_mb)} MB)</option>`).join("")}
        ${c.model_path && !(found?.models || []).some((m) => m.path === c.model_path) ? `<option value="${esc(c.model_path)}" selected>${esc(c.model_path)}</option>` : ""}</select></label>
      <div class="row">
        <label>Model alias <input name="alias" value="${esc(c.alias)}" style="width:140px"></label>
        <label>Host <input name="host" value="${esc(c.host)}" style="width:140px"></label>
        <label>Port <input name="port" type="number" min="1024" max="65535" value="${esc(c.port)}" style="width:100px"></label>
        <label>Context <input name="context_size" type="number" min="512" max="262144" value="${esc(c.context_size)}" style="width:110px"></label>
        <label>Startup timeout (s) <input name="startup_timeout_s" type="number" min="10" max="1800" value="${esc(c.startup_timeout_s)}" style="width:100px"></label>
      </div>
      <label><span><input type="checkbox" name="auto_start" ${c.auto_start ? "checked" : ""}> Start with the application</span></label>
      <div class="row"><button class="btn" type="button" data-m="detect">Auto Detect</button><button class="btn" type="submit">Save</button>
        <button class="btn primary" type="button" data-m="start">${st.state === "RUNNING" ? "Restart" : "Start"}</button>
        <button class="btn" type="button" data-m="stop" ${st.pid ? "" : "disabled"}>Stop Managed LLM</button></div>
    </form>`;
}

async function mountManaged(root, found = null, prev = null) {
  clearTimeout(followTimer);
  if (!root.isConnected) return;
  let st;
  try { st = await api("/api/ai/managed"); } catch (err) { root.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`; return; }
  root.innerHTML = managedHtml(st, found);
  const form = root.querySelector("#mForm");
  const body = () => {
    const f = form.elements;
    return { server_path: f.server_path.value, model_path: f.model_path.value, alias: f.alias.value.trim(), host: f.host.value.trim(),
      port: Number(f.port.value), context_size: Number(f.context_size.value), startup_timeout_s: Number(f.startup_timeout_s.value),
      auto_start: f.auto_start.checked };
  };
  const act = async (fn) => { try { await fn(); } catch (err) { toast(err.message, "error"); } };
  form.addEventListener("submit", (e) => { e.preventDefault(); act(async () => { await api("/api/ai/managed", { method: "PUT", body: body() }); toast("Managed LLM settings saved", "success"); mountManaged(root, found); }); });
  root.querySelector('[data-m="detect"]').addEventListener("click", () => act(async () => {
    const d = await api("/api/ai/managed/detect", { method: "POST" });
    toast(`Found ${d.servers.length} llama-server file(s) and ${d.models.length} GGUF model(s).`, d.servers.length && d.models.length ? "success" : "warning");
    mountManaged(root, d);
  }));
  root.querySelector('[data-m="start"]').addEventListener("click", () => act(async () => {
    await api("/api/ai/managed", { method: "PUT", body: body() });
    await api(`/api/ai/managed/${st.state === "RUNNING" ? "restart" : "start"}`, { method: "POST" });
    mountManaged(root, found);
  }));
  root.querySelector('[data-m="stop"]').addEventListener("click", () => act(async () => { await api("/api/ai/managed/stop", { method: "POST" }); mountManaged(root, found); }));
  // Follow a start only while the model loads (bounded); no polling otherwise.
  if (st.state === "STARTING") followTimer = setTimeout(() => mountManaged(root, found, "STARTING"), 2000);
  if (prev === "STARTING" && st.state !== "STARTING") loadHealth(true); // the status strip shows the new Local AI state
}

export function unmount() { clearTimeout(followTimer); followTimer = null; }
