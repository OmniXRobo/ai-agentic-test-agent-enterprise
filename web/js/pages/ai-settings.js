// AI runtime settings (On-Premise AI or Cloud AI) and PASS/FAIL diagnostics.

import { api } from "../api.js";
import { esc, toast } from "../util.js";

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
    </form>`;
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
}
