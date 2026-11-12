// Final patch: Preferences > TFS Integration, Browser Manager and AURA Memory.

import { api } from "../api.js";
import { esc, toast, dateTimeOf } from "../util.js";
import { isAdmin } from "../auth.js";

const card = (title, body) => `<div class="card" style="margin-bottom:12px"><h3>${esc(title)}</h3>${body}</div>`;
const table = (heads, rows) => rows.length ? `<div style="overflow-x:auto"><table class="data"><thead><tr>${heads.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>` : '<p class="muted">Nothing yet.</p>';

// ---- TFS Integration ---------------------------------------------------------------------------------------------

export const tfsIntegration = {
  async mount(view) {
    let prefs;
    try { prefs = await api("/api/tfs/preferences"); }
    catch (err) { view.innerHTML = card("TFS Integration", `<p class="pill FAIL">${esc(err.message)}</p><p><a href="#/tfs-settings">TFS Connection</a></p>`); return; }
    const p = prefs.profile;
    view.innerHTML = card("Connection", `<p>Project <b>${esc(p.project || "not set")}</b> · ${esc(p.mode)} · ${esc(p.url || p.collection_url || "")}</p>
        <div class="row"><button class="btn" id="test">Test Connection</button><button class="btn primary" id="disc">Discover Project Metadata</button>
        <button class="btn" id="refresh">Refresh Metadata</button><a class="btn" href="#/tfs-settings">Edit connection</a></div>
        <form class="row" id="ca" style="margin-top:8px"><label>Company CA certificate bundle (PEM, optional) <input name="ca" style="min-width:320px" value="${esc(p.ca_bundle || "")}" placeholder="C:\\Certs\\company-root.pem"></label>
        <button class="btn">Save</button></form><p class="muted">TLS certificates are always checked: Windows certificate store, the bundled CA list and this file.</p>`)
      + '<div id="meta"></div>';
    const meta = view.querySelector("#meta");
    view.querySelector("#test").addEventListener("click", async () => {
      try { const r = await api(`/api/tfs/test/${p.mode}`, { method: "POST" }); toast(r.message || "Connected", "success"); } catch (err) { toast(err.message, "error"); }
    });
    view.querySelector("#ca").addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await api(`/api/tfs/preferences/ca-bundle/${p.mode}`, { method: "PUT", body: { ca_bundle: e.target.ca.value } }); toast("Saved", "success"); } catch (err) { toast(err.message, "error"); }
    });
    const discover = async (refresh) => {
      meta.innerHTML = '<p class="muted">Reading the project…</p>';
      let m;
      try { m = await api(`/api/tfs/preferences/discover${refresh ? "?refresh=true" : ""}`, { method: "POST" }); }
      catch (err) { meta.innerHTML = card("Metadata", `<p class="pill FAIL">${esc(err.message)}</p>`); return; }
      meta.innerHTML = Object.entries(m.categories).map(([cat, info]) => info.types.map((t) => card(`${t.type} (${cat})`, `
        ${t.error ? `<p class="pill WARN">${esc(t.error)}</p>` : ""}
        <form data-type="${esc(t.type)}">${table(["Field", "Reference", "Required", "TFS default", "Allowed values", "Default for SkyGuard QA"], t.fields.map((f) => `<tr>
          <td>${esc(f.name)}</td><td class="muted">${esc(f.ref)}</td><td>${f.required ? '<span class="pill WARN">mandatory</span>' : ""}${f.read_only ? ' <span class="muted">read-only</span>' : ""}</td>
          <td>${esc(f.default ?? "")}</td><td class="muted">${esc((f.allowed || []).slice(0, 12).join(", "))}${(f.allowed || []).length > 12 ? "…" : ""}</td>
          <td>${f.read_only ? "" : (f.allowed || []).length ? `<select name="${esc(f.ref)}"><option value="">(none)</option>${f.allowed.map((a) => `<option ${String(f.saved_default ?? "") === String(a) ? "selected" : ""}>${esc(a)}</option>`).join("")}</select>`
            : `<input name="${esc(f.ref)}" value="${esc(f.saved_default ?? "")}" maxlength="400">`}</td></tr>`))}
        <div class="row"><button class="btn primary" type="submit">Save defaults</button><button class="btn" type="button" data-validate>Validate Mandatory Fields</button>
          <button class="btn" type="button" data-testcreate>Test Work Item Creation</button><span class="muted" data-out></span></div></form>`)).join("")).join("");
      meta.querySelectorAll("form[data-type]").forEach((form) => {
        const type = form.dataset.type, out = form.querySelector("[data-out]");
        form.addEventListener("submit", async (e) => {
          e.preventDefault();
          const values = Object.fromEntries([...new FormData(form)].filter(([, v]) => v !== ""));
          try { await api("/api/tfs/preferences/defaults", { method: "PUT", body: { type, values, replace: true } }); toast(`${type} defaults saved`, "success"); } catch (err) { toast(err.message, "error"); }
        });
        form.querySelector("[data-validate]").addEventListener("click", async () => {
          try {
            const r = await api(`/api/tfs/preferences/validate?type=${encodeURIComponent(type)}`);
            const ask = r.fields.filter((x) => x.required && !["default", "automatic", "server_default"].includes(x.status));
            out.textContent = r.ready ? "Every mandatory field has a value." : `Needs a value: ${ask.map((x) => `${x.name}${x.status === "invalid_default" ? " (saved value not allowed)" : ""}`).join(", ")}`;
          } catch (err) { out.textContent = err.message; }
        });
        form.querySelector("[data-testcreate]").addEventListener("click", async () => {
          try { const r = await api("/api/tfs/preferences/test-create", { method: "POST", body: { type } }); out.textContent = r.message; }
          catch (err) { out.textContent = err.message; }
        });
      });
    };
    view.querySelector("#disc").addEventListener("click", () => discover(false));
    view.querySelector("#refresh").addEventListener("click", () => discover(true));
  },
};

// ---- Browser Manager ---------------------------------------------------------------------------------------------

export const browserManager = {
  async mount(view) {
    const { settings } = await api("/api/settings");
    const pb = settings.playback || {};
    view.innerHTML = `<form class="settings card" id="bf"><h2>Browser Manager</h2>
      <p class="muted">One browser setting for every module: recording, playback, discovery, hybrid, performance recording and data comparison.
        SkyGuard QA never switches to another browser on its own; when the chosen browser cannot start, it says why.</p>
      <label>Browser <select name="browser">${[["chromium", "Chromium (included)"], ["msedge", "Microsoft Edge"], ["chrome", "Google Chrome"]].map(([v, l]) => `<option value="${v}" ${(pb.browser || "chromium") === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label>Window <select name="window_mode"><option value="maximized" ${pb.window_mode !== "default" ? "selected" : ""}>Maximized</option><option value="default" ${pb.window_mode === "default" ? "selected" : ""}>Normal size (1366 × 768)</option></select></label>
      <label>Monitor <select name="monitor"><option value="auto">Second monitor when connected, else the main one</option><option value="primary" ${pb.monitor === "primary" ? "selected" : ""}>Always the main monitor</option><option value="0" ${pb.monitor === 0 ? "selected" : ""}>Monitor 1</option><option value="1" ${pb.monitor === 1 ? "selected" : ""}>Monitor 2</option><option value="2" ${pb.monitor === 2 ? "selected" : ""}>Monitor 3</option></select></label>
      <label>Playback <select name="headless"><option value="auto" ${pb.headless == null ? "selected" : ""}>Visible on a desktop, hidden on servers</option><option value="false" ${pb.headless === false ? "selected" : ""}>Always visible (headed)</option><option value="true" ${pb.headless === true ? "selected" : ""}>Always hidden (headless)</option></select></label>
      <div class="row"><button class="btn primary">Save</button><button class="btn" type="button" id="check">Check browsers</button></div></form><div id="out"></div>`;
    if (pb.monitor === "auto" || pb.monitor == null) view.querySelector("[name=monitor]").value = "auto";
    view.querySelector("#bf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target));
      const monitor = ["auto", "primary"].includes(f.monitor) ? f.monitor : Number(f.monitor);
      try {
        await api("/api/settings", { method: "PATCH", body: { playback: { browser: f.browser, window_mode: f.window_mode, monitor,
          headless: f.headless === "auto" ? null : f.headless === "true" } } });
        toast("Browser settings saved", "success");
      } catch (err) { toast(err.message, "error"); }
    });
    view.querySelector("#check").addEventListener("click", async (e) => {
      e.target.disabled = true;
      const out = view.querySelector("#out");
      out.innerHTML = '<p class="muted">Starting each browser once, hidden…</p>';
      try {
        const r = await api("/api/automation/browsers/check");
        out.innerHTML = card("Browsers on this PC", table(["Browser", "Status", "Version", "Executable", "Problem"], r.browsers.map((b) => `<tr><td>${esc(b.name)}${b.browser === r.selected ? " <b>(selected)</b>" : ""}</td>
            <td><span class="pill ${b.status === "PASS" ? "PASS" : "FAIL"}">${esc(b.status || "-")}</span></td><td>${esc(b.version || "")}</td><td class="muted">${esc(b.executable || "not found")}</td>
            <td>${esc(b.message || "")}${b.details?.reason ? ` <span class="muted">(${esc(b.details.reason)})</span>` : ""}</td></tr>`)))
          + card("Monitors", table(["#", "Size", "Position", "Main"], (r.monitors || []).map((m, i) => `<tr><td>${i + 1}</td><td>${esc(m.width)} × ${esc(m.height)}</td><td>${esc(m.x)}, ${esc(m.y)}</td><td>${m.primary ? "yes" : ""}</td></tr>`))
            + `<p class="muted">Windows open on: ${esc(JSON.stringify(r.placement || {}))}</p>`);
      } catch (err) { out.innerHTML = `<p class="pill FAIL">${esc(err.message)}</p>`; }
      e.target.disabled = false;
    });
  },
};

// ---- AURA memory -------------------------------------------------------------------------------------------------

export const auraMemory = {
  async mount(view) {
    const render = async () => {
      let s;
      try { s = await api("/api/aura/status"); } catch (err) { view.innerHTML = card("AURA", `<p class="pill FAIL">${esc(err.message)}</p>`); return; }
      const [apps, backups, activity] = await Promise.all([api("/api/aura/applications").catch(() => ({ items: [] })),
        api("/api/aura/backups").catch(() => ({ items: [] })), api("/api/aura/activity?limit=50").catch(() => ({ items: [] }))]);
      view.innerHTML = card("AURA: Adaptive Understanding & Runtime Intelligence Agent", `
          <p>Status <span class="pill ${s.status === "ACTIVE" ? "PASS" : "WARN"}">${esc(s.status)}</span> ${s.problem ? `<span class="muted">${esc(s.problem)}</span>` : ""}
            ${s.pending ? ` · ${esc(s.pending)} lesson(s) waiting for the folder` : ""}</p>
          <p class="muted">AURA learns application structure, locators, healing, workflows, performance baselines and known defects. It never stores passwords, tokens or secrets, and never changes an approved script.</p>
          ${isAdmin() ? `<form class="row" id="pf" style="flex-wrap:wrap"><label>Memory folder <input name="path" style="min-width:360px" value="${esc(s.config.memory_path)}"></label>
            <button class="btn" type="button" id="checkp">Check folder</button><button class="btn" type="button" id="use">Use this folder</button><button class="btn" type="button" id="mig">Move memory here</button>
            <label class="row"><input type="checkbox" name="enabled" ${s.enabled ? "checked" : ""}> Learning on</label></form>` : `<p>Memory folder: <code>${esc(s.config.memory_path)}</code></p>`}`)
        + card("What AURA knows", table(["Kind", "Entries", "Versions", "Approved"], Object.entries(s.kinds).map(([k, v]) => `<tr><td>${esc(k.replaceAll("_", " "))}</td><td>${esc(v.entries)}</td><td>${esc(v.versions)}</td><td>${esc(v.approved)}</td></tr>`))
          + `<p class="muted">Applications: ${esc(apps.items.map((a) => a.application || a).join(", ") || "none yet")}</p>`)
        + card("Maintenance", `<div class="row" style="flex-wrap:wrap"><button class="btn" id="integ">Check integrity</button><button class="btn" id="bk">Back up now</button>
            <a class="btn" href="/api/aura/export">Export</a>${isAdmin() ? '<label class="btn">Import <input type="file" id="imp" accept=".zip" hidden></label><button class="btn" id="reset">Reset…</button>' : ""}<span class="muted" id="mout"></span></div>
            ${table(["Backup", "When", "Size", ""], backups.items.map((b) => `<tr><td>${esc(b.file)}</td><td>${esc(b.created)}</td><td>${esc(b.size_mb)} MB</td>
              <td>${isAdmin() ? `<button class="link-btn" data-restore="${esc(b.file)}">Restore</button>` : ""}</td></tr>`))}`)
        + card("Recent learning", table(["When", "Agent", "What", "Application", "Kind"], activity.items.map((a) => `<tr><td>${esc(dateTimeOf(a.at))}</td><td>${esc(a.agent || "")}</td><td>${esc(a.summary)}</td><td>${esc(a.application || "")}</td><td>${esc(a.kind || "")}</td></tr>`)));
      const mout = view.querySelector("#mout");
      const pf = view.querySelector("#pf");
      const call = async (fn, msg) => { try { const r = await fn(); toast(msg(r), "success"); await render(); } catch (err) { toast(err.message, "error"); } };
      pf?.querySelector("#checkp").addEventListener("click", async () => {
        try { const r = await api("/api/aura/check-path", { method: "POST", body: { path: pf.path.value } }); toast(`OK: ${r.free_mb} MB free${r.has_memory ? ", an AURA memory is already there" : ""}`, "success"); } catch (err) { toast(err.message, "error"); }
      });
      pf?.querySelector("#use").addEventListener("click", () => call(() => api("/api/aura/settings", { method: "PUT", body: { memory_path: pf.path.value } }), () => "Memory folder changed"));
      pf?.querySelector("#mig").addEventListener("click", () => { if (confirm("Copy the memory to this folder, verify it and switch to it? The old copy is kept.")) call(() => api("/api/aura/migrate", { method: "POST", body: { path: pf.path.value } }), () => "Memory moved and verified"); });
      pf?.querySelector("[name=enabled]").addEventListener("change", (e) => call(() => api("/api/aura/settings", { method: "PUT", body: { enabled: e.target.checked } }), () => e.target.checked ? "Learning on" : "Learning off"));
      view.querySelector("#integ").addEventListener("click", async () => { try { const r = await api("/api/aura/integrity"); mout.textContent = r.ok ? "Integrity OK" : `Problems: ${JSON.stringify(r.problems || r)}`; } catch (err) { mout.textContent = err.message; } });
      view.querySelector("#bk").addEventListener("click", () => call(() => api("/api/aura/backup", { method: "POST" }), (r) => `Backup ${r.file} created`));
      view.querySelector("#imp")?.addEventListener("change", (e) => { const f = e.target.files[0]; if (f) call(() => api("/api/aura/import", { method: "POST", rawBody: f, contentType: "application/zip" }), (r) => `Imported ${r.imported ?? ""} entries`); });
      view.querySelector("#reset")?.addEventListener("click", () => {
        const app = prompt("Reset the memory of one application (type its name), or leave empty to reset everything. A backup is made first.");
        if (app === null) return;
        const confirmText = app ? `RESET ${app}` : "RESET ALL";
        if (prompt(`Type ${confirmText} to confirm`) !== confirmText) return;
        call(() => api("/api/aura/reset", { method: "POST", body: { application: app || null, confirm: confirmText } }), () => "Memory reset");
      });
      view.querySelectorAll("[data-restore]").forEach((b) => b.addEventListener("click", () => {
        if (confirm(`Restore ${b.dataset.restore}? A safety backup of the current memory is made first.`)) call(() => api("/api/aura/restore", { method: "POST", body: { file: b.dataset.restore } }), () => "Memory restored");
      }));
    };
    await render();
  },
};
