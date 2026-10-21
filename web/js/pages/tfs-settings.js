// TFS Connection settings: Azure DevOps Online and Local TFS / Azure DevOps Server.

import { api } from "../api.js";
import { esc, toast } from "../util.js";

const MODES = {
  online: { label: "Azure DevOps Online", urlLabel: "Organization URL", placeholder: "https://dev.azure.com/your-organization", api: "7.1" },
  offline: { label: "Local TFS / Azure DevOps Server", urlLabel: "Collection URL", placeholder: "https://tfs-server/tfs/DefaultCollection", api: "6.0" },
};

function profileForm(mode, p, active, storeOk) {
  const m = MODES[mode];
  return `
  <form class="settings card" data-mode="${mode}">
    <div class="row"><h2 style="margin:0">${m.label}</h2>${active ? '<span class="pill PASSED">ACTIVE</span>' : ""}</div>
    <label>${m.urlLabel}<input name="url" value="${esc(p.url)}" placeholder="${m.placeholder}" autocomplete="off"></label>
    <label>Project<input name="project" value="${esc(p.project)}" autocomplete="off"></label>
    <label>API version<input name="api_version" value="${esc(p.api_version || m.api)}"></label>
    ${mode === "offline" ? `<label><span><input type="checkbox" name="verify_tls" ${p.verify_tls ? "checked" : ""}> Verify the server's TLS certificate</span></label>` : ""}
    <label>Personal Access Token
      <input name="pat" type="password" autocomplete="new-password"
        placeholder="${p.has_pat ? "Saved. Leave blank to keep it." : "Paste a PAT with Work Items and Test Management scopes"}"
        ${storeOk ? "" : "disabled"}>
    </label>
    ${storeOk ? "" : '<p class="pill FAIL">The Windows Credential Manager is not available, so a PAT cannot be stored on this machine.</p>'}
    <div class="row">
      <button class="btn primary" type="submit">Save</button>
      <button class="btn" type="button" data-act="test">Test connection</button>
      ${active ? "" : '<button class="btn" type="button" data-act="activate">Make active</button>'}
      ${p.has_pat ? '<button class="btn" type="button" data-act="forget">Remove saved PAT</button>' : ""}
    </div>
    <div data-f="result"></div>
  </form>`;
}

function resultHtml(r) {
  return `
    <p><span class="pill ${r.ok ? "PASS" : "FAIL"}">${r.ok ? "PASS" : "FAIL"}</span> ${esc(r.label)}</p>
    <table class="data"><thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr></thead><tbody>
    ${r.checks.map((c) => `<tr><td>${esc(c.name)}</td><td><span class="pill ${esc(c.status === "SKIP" ? "WAITING" : c.status)}">${esc(c.status)}</span></td><td>${esc(c.detail)}</td></tr>`).join("")}
    </tbody></table>`;
}

async function render(view) {
  const data = await api("/api/tfs/profiles");
  view.innerHTML = `
    <p class="muted">Only the active profile is used for TFS Explorer, work items and defect creation.
      Tokens are stored in the Windows Credential Manager, never in files or logs, and are never shown again after saving.</p>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(380px,1fr))">
      ${Object.keys(MODES).map((m) => profileForm(m, data.profiles[m], data.active_mode === m, data.credential_store_available)).join("")}
    </div>`;
  view.querySelectorAll("form[data-mode]").forEach((form) => {
    const mode = form.dataset.mode;
    const out = form.querySelector('[data-f="result"]');
    const save = async (extra = {}) => {
      const f = new FormData(form);
      const body = { url: f.get("url"), project: f.get("project"), api_version: f.get("api_version"), ...extra };
      if (mode === "offline") body.verify_tls = f.get("verify_tls") === "on";
      if (f.get("pat")) body.pat = f.get("pat");
      await api(`/api/tfs/profiles/${mode}`, { method: "PUT", body });
    };
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await save(); toast(`${MODES[mode].label} saved`, "success"); await render(view); }
      catch (err) { toast(err.message, "error"); }
    });
    form.querySelector('[data-act="test"]').addEventListener("click", async (e) => {
      e.target.disabled = true;
      out.innerHTML = '<p class="muted">Testing connection…</p>';
      try {
        await save();
        const patInput = form.querySelector('input[name="pat"]');
        if (patInput.value) { patInput.value = ""; patInput.placeholder = "Saved. Leave blank to keep it."; }
        out.innerHTML = resultHtml(await api(`/api/tfs/test/${mode}`, { method: "POST" }));
      } catch (err) {
        out.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`;
      } finally {
        e.target.disabled = false;
      }
    });
    form.querySelector('[data-act="activate"]')?.addEventListener("click", async () => {
      try { await save({ make_active: true }); toast(`${MODES[mode].label} is now active`, "success"); await render(view); }
      catch (err) { toast(err.message, "error"); }
    });
    form.querySelector('[data-act="forget"]')?.addEventListener("click", async () => {
      if (!confirm(`Remove the saved PAT for ${MODES[mode].label}?`)) return;
      try { await api(`/api/tfs/profiles/${mode}/pat`, { method: "DELETE" }); await render(view); }
      catch (err) { toast(err.message, "error"); }
    });
  });
}

export async function mount(view) {
  await render(view);
}
