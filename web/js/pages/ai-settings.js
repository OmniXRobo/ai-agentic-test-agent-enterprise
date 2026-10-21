// On-Premise AI settings and PASS/FAIL diagnostics.

import { api } from "../api.js";
import { esc, toast } from "../util.js";

function checksHtml(r) {
  return `<p><span class="pill ${r.ok ? "PASS" : "FAIL"}">${r.ok ? "PASS" : "FAIL"}</span> ${esc(r.base_url || "")}</p>
    <table class="data"><thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr></thead><tbody>
    ${r.checks.map((c) => `<tr><td>${esc(c.name)}</td><td><span class="pill ${c.status === "SKIP" ? "WAITING" : esc(c.status)}">${esc(c.status)}</span></td><td>${esc(c.detail)}</td></tr>`).join("")}
    </tbody></table>`;
}

export async function mount(view) {
  const cfg = await api("/api/ai/settings");
  view.innerHTML = `
    <form class="settings card" id="aiForm">
      <h2>On-Premise AI</h2>
      <p class="muted">The platform talks only to this server, an OpenAI-compatible endpoint such as llama.cpp
        <code>llama-server</code> running your Qwen GGUF model, or Ollama. No Internet access is needed, and models are never downloaded.</p>
      <label>Server URL<input name="base_url" value="${esc(cfg.base_url)}" placeholder="http://127.0.0.1:8080/v1"></label>
      <label>Model
        <span class="row"><input name="model" list="modelList" value="${esc(cfg.model)}" placeholder="Leave blank to use the server's loaded model" style="flex:1">
        <button class="btn" type="button" id="loadModels">Load models</button></span>
        <datalist id="modelList"></datalist>
      </label>
      <label>Timeout (seconds)<input name="timeout_seconds" type="number" min="5" max="1800" value="${esc(cfg.timeout_seconds)}"></label>
      <label>Temperature<input name="temperature" type="number" step="0.1" min="0" max="2" value="${esc(cfg.temperature)}"></label>
      <label>Max output tokens<input name="max_tokens" type="number" min="256" max="32768" value="${esc(cfg.max_tokens)}"></label>
      <div class="row"><button class="btn primary" type="submit">Save</button><button class="btn" type="button" id="diag">Run diagnostics</button></div>
      <div id="result"></div>
    </form>`;
  const form = view.querySelector("#aiForm");
  const save = async () => {
    const f = new FormData(form);
    await api("/api/ai/settings", { method: "PUT", body: {
      base_url: f.get("base_url"), model: f.get("model"), timeout_seconds: Number(f.get("timeout_seconds")),
      temperature: Number(f.get("temperature")), max_tokens: Number(f.get("max_tokens")),
    } });
  };
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await save(); toast("On-Premise AI settings saved", "success"); } catch (err) { toast(err.message, "error"); }
  });
  view.querySelector("#loadModels").addEventListener("click", async () => {
    try {
      await save();
      const { models } = await api("/api/ai/models");
      view.querySelector("#modelList").innerHTML = models.map((m) => `<option value="${esc(m)}">`).join("");
      toast(models.length ? `Loaded models: ${models.join(", ")}` : "The server reports no models.", models.length ? "success" : "warning");
    } catch (err) { toast(err.message, "error"); }
  });
  view.querySelector("#diag").addEventListener("click", async (e) => {
    e.target.disabled = true;
    const out = view.querySelector("#result");
    out.innerHTML = '<p class="muted">Running diagnostics. The completion test can take a while on CPU-only machines.</p>';
    try { await save(); out.innerHTML = checksHtml(await api("/api/ai/diagnostics", { method: "POST" })); }
    catch (err) { out.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`; }
    finally { e.target.disabled = false; }
  });
}
