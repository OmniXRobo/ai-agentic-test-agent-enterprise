// First-run setup: TFS, the local LLM model and (optionally) the voice model on one screen. No commands needed.

import { api } from "../api.js";
import { esc, toast } from "../util.js";

function stepsHtml(steps) {
  return `<table class="data"><thead><tr><th>Step</th><th>Result</th><th>Detail</th></tr></thead><tbody>
    ${steps.map((s) => `<tr><td>${esc(s.name)}</td><td><span class="pill ${esc(s.status)}">${esc(s.status)}</span></td><td>${esc(s.detail)}</td></tr>`).join("")}
    </tbody></table>`;
}

async function render(view, result = null) {
  const s = await api("/api/setup");
  const t = s.tfs, l = s.llm, v = s.voice;
  view.innerHTML = `
    <p class="muted">Three things and the platform is ready. Everything else is already bundled: Python, Chromium, Java,
      JMeter, llama-server and the offline speech model. Nothing is downloaded.</p>
    <form class="settings card" data-f="setup">
      <h2 style="margin:0">1. TFS connection</h2>
      <label>TFS server URL<input name="tfs_url" value="${esc(t.url)}" placeholder="https://tfs-server/tfs/DefaultCollection" autocomplete="off" required></label>
      <label>Project<input name="tfs_project" value="${esc(t.project)}" autocomplete="off" required></label>
      <label>Personal Access Token
        <input name="tfs_pat" type="password" autocomplete="new-password" ${s.credential_store_available ? "" : "disabled"}
          placeholder="${t.has_pat ? "Saved. Leave blank to keep it." : "Paste a PAT with Work Items and Test Management scopes"}">
      </label>
      <label><span><input type="checkbox" name="verify_tls" checked> Verify the server's TLS certificate (local TFS)</span></label>
      ${s.credential_store_available ? "" : '<p class="pill FAIL">The Windows Credential Manager is not available, so a PAT cannot be stored on this machine.</p>'}

      <h2 style="margin:12px 0 0">2. Local LLM model</h2>
      <label>Model file (.gguf)<input name="llm_model" value="${esc(l.model_path || l.models_found[0] || "")}" placeholder="D:\\Models\\qwen2.5-7b-instruct-q4_k_m.gguf" autocomplete="off" required></label>
      <p class="muted">${l.server_bundled ? `llama-server: bundled (${esc(l.server_path)}).` : "llama-server was not found in this install; enter its path below."}
        The model starts now and with the application from then on.</p>
      <details ${l.server_bundled ? "" : "open"}><summary>Use a different llama-server</summary>
        <label>llama-server.exe<input name="llm_server" value="" placeholder="${esc(l.server_path || "C:\\llama.cpp\\llama-server.exe")}" autocomplete="off"></label>
      </details>

      <h2 style="margin:12px 0 0">3. Voice model (optional)</h2>
      <label>Vosk model folder or .zip<input name="voice_model" value="${esc(v.model_dir)}" placeholder="Leave blank to use the bundled offline model" autocomplete="off"></label>
      <p class="muted">${v.active_model ? `In use now: ${esc(v.active_model)}` : "No speech model found yet."}</p>

      <div class="row">
        <button class="btn primary" type="submit">Save and start</button>
        <button class="btn" type="button" data-act="skip">Skip for now</button>
      </div>
      <div data-f="result">${result ? stepsHtml(result.steps) : ""}</div>
    </form>`;
  const form = view.querySelector('form[data-f="setup"]');
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = form.querySelector('button[type="submit"]');
    btn.disabled = true;
    form.querySelector('[data-f="result"]').innerHTML = '<p class="muted">Saving and testing…</p>';
    const f = new FormData(form);
    const body = { tfs_url: f.get("tfs_url"), tfs_project: f.get("tfs_project"), verify_tls: f.get("verify_tls") === "on",
      llm_model: f.get("llm_model"), llm_server: f.get("llm_server") || null, voice_model: f.get("voice_model") || null };
    if (f.get("tfs_pat")) body.tfs_pat = f.get("tfs_pat");
    try {
      const r = await api("/api/setup", { method: "POST", body });
      const failed = r.steps.filter((x) => x.status === "FAIL").length;
      toast(failed ? "Saved. Some checks need attention." : "Setup complete", failed ? "info" : "success");
      await render(view, r);
    } catch (err) {
      form.querySelector('[data-f="result"]').innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`;
      btn.disabled = false;
    }
  });
  form.querySelector('[data-act="skip"]').addEventListener("click", async () => {
    try { await api("/api/setup/dismiss", { method: "POST" }); location.hash = "#/dashboard"; }
    catch (err) { toast(err.message, "error"); }
  });
}

export async function mount(view) {
  await render(view);
}
