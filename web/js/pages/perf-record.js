// 23.3.24: record a performance script from a browser journey, or import a HAR file.
// The browser window opens on this PC; the network requests of what the tester does in it become JMeter samplers,
// grouped in steps (transactions). Values the server hands out (tokens, CSRF fields, ids) are correlated and typed
// passwords become saved secrets. Nothing is load tested while recording or saving.

import { api } from "../api.js";
import { esc, toast } from "../util.js";

const KINDS = { load: "Load", stress: "Stress", spike: "Spike", soak: "Soak" };
const hostsOf = (text) => text.split(/[\s,]+/).map((h) => h.trim().toLowerCase()).filter(Boolean);
const scenarioParam = () => new URLSearchParams(location.hash.split("?")[1] || "").get("scenario");

// The card on the plan lists: start a recording, or import a HAR file
export function recordCard(routeName, kind) {
  const scenario = scenarioParam();
  return {
    html: `<div class="card" id="recordCard"><h3>Record a browser journey</h3>
      <p class="muted">A browser opens on this PC. Use the application as a user would; every request is recorded and becomes a JMeter script with tokens and form fields handled for you. Nothing is load tested.</p>
      <a class="btn primary" href="#/${routeName}?record=1&kind=${kind || "load"}${scenario ? `&scenario=${encodeURIComponent(scenario)}` : ""}">Start recording</a>
      <details style="margin-top:8px"><summary>Import a HAR file instead</summary>
        <form id="importHar" class="grid" style="margin-top:8px">
          <p class="muted" style="margin:0">Save the HAR from the browser's developer tools (Network tab, “Save all as HAR”). A HAR has no way to tell which field was a password, so check the script for passwords before saving it elsewhere.</p>
          <input type="file" name="file" accept=".har,.json" required>
          <label>Hosts to keep <input name="hosts" placeholder="www.example.com api.example.com" required></label>
          <label><span><input type="checkbox" name="static"> Keep images, styles, scripts and fonts</span></label>
          <div class="row"><select name="kind">${Object.entries(KINDS).map(([k, v]) => `<option value="${k}" ${k === (kind || "load") ? "selected" : ""}>${v}</option>`).join("")}</select>
            <button class="btn" type="submit">Import HAR</button></div></form></details></div>`,
    bind(root, onDone) {
      root.querySelector("#importHar").addEventListener("submit", async (e) => {
        e.preventDefault();
        const f = e.target, file = f.file.files[0];
        if (!file) return;
        if (file.size > 100 * 1024 * 1024) { toast("The file is larger than 100 MB.", "error"); return; }
        try {
          let har;
          try { har = JSON.parse(await file.text()); } catch { toast("This is not a HAR (JSON) file.", "error"); return; }
          const { plan } = await api("/api/performance/plans/import-har", { method: "POST", body: {
            har, filename: file.name, kind: f.kind.value, allowed_hosts: hostsOf(f.hosts.value),
            include_static: f.static.checked, scenario_id: scenario || null } });
          toast(`Created “${plan.name}” from the HAR file`, "success");
          onDone(plan);
        } catch (err) { toast(err.message, "error"); }
      });
    },
  };
}

function reportHtml(report, { secrets = true } = {}) {
  const corr = report.correlations || [];
  const excluded = report.excluded || [];
  const reasons = {};
  excluded.forEach((x) => { reasons[x.reason] = (reasons[x.reason] || 0) + 1; });
  return `
    <p><b>${report.kept}</b> request(s) kept in ${report.steps.length} step(s) · main host <code>${esc(report.main_host || "?")}</code>
      ${report.hosts.length > 1 ? ` · also ${report.hosts.filter((h) => h !== report.main_host).map((h) => `<code>${esc(h)}</code>`).join(" ")}` : ""}</p>
    <h4>Values handled automatically (${corr.length})</h4>
    ${corr.length ? `<table class="data"><thead><tr><th>Variable</th><th>Field</th><th>Taken from</th><th>How</th><th>Used in</th></tr></thead><tbody>
      ${corr.map((c) => `<tr><td><code>\${${esc(c.variable)}}</code><div class="muted">${esc(c.value)}</div></td><td>${esc(c.field)}</td><td style="word-break:break-all">#${c.source_request} ${esc(c.source_url)}</td><td>${esc(c.extractor)} <code>${esc(c.expression)}</code></td><td>${(c.used_in || []).length} request(s)</td></tr>`).join("")}
      </tbody></table>` : '<p class="muted">No value from a response was sent back in a later request.</p>'}
    ${(report.unresolved || []).length ? `<div class="problems"><b>Check these:</b> values sent as recorded:<ul>${report.unresolved.slice(0, 20).map((u) => `<li><code>${esc(u.field)}</code> = <code>${esc(u.value)}</code> in request #${u.request}: ${esc(u.note)}</li>`).join("")}</ul></div>` : ""}
    ${secrets ? `<h4>Secrets (${(report.secrets || []).length})</h4>
    ${(report.secrets || []).length ? `<p>${report.secrets.map((n) => `<code>\${__P(${esc(n)})}</code>`).join(" · ")}</p>
      <p class="muted">The typed values are saved in the Windows Credential Manager, not in the script, and are given to JMeter only while a test runs.</p>`
      : '<p class="muted">No password field was typed in.</p>'}` : ""}
    <details><summary>${excluded.length} request(s) left out</summary>
      <p class="muted">${Object.entries(reasons).map(([r, n]) => `${esc(r)}: ${n}`).join(" · ")}</p></details>`;
}

// The recorder page (?record=1)
export async function recorderView(view, { routeName, kind, backHref, addCleanup }) {
  const scenario = scenarioParam();
  let current = (await api("/api/performance/recordings/current")).recording;
  let timer = null;
  const stopPolling = () => { if (timer) { clearInterval(timer); timer = null; } };
  addCleanup(stopPolling);

  const startForm = () => {
    view.innerHTML = `
      <p><a href="${esc(backHref)}">← Back</a></p>
      <form class="card" id="startRec"><h3>Record a browser journey</h3>
        <p class="muted">Only record an application you are allowed to test. Requests to other hosts (analytics, fonts, ads) are left out of the script.</p>
        <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr))">
          <label>Start URL <input name="url" type="url" placeholder="https://www.example.com/login" required></label>
          <label>Hosts to keep <input name="hosts" placeholder="empty = the start URL's host"></label>
          <label>Browser <select name="browser"><option value="chromium">Chromium</option><option value="msedge">Microsoft Edge</option><option value="chrome">Google Chrome</option></select></label></div>
        <label class="row"><input type="checkbox" name="auto" checked> Start a new step on each page navigation</label>
        <label class="row"><input type="checkbox" name="static"> Keep images, styles, scripts and fonts (usually left out: browsers cache them)</label>
        <button class="btn primary" type="submit">Open browser and record</button></form>`;
    view.querySelector("#startRec").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.target;
      f.querySelector("button").disabled = true;
      try {
        const r = await api("/api/performance/recordings", { method: "POST", body: {
          url: f.url.value.trim(), allowed_hosts: hostsOf(f.hosts.value), include_static: f.static.checked,
          auto_steps: f.auto.checked, browser: f.browser.value } });
        current = r.recording;
        recording();
      } catch (err) { toast(err.message, "error"); f.querySelector("button").disabled = false; }
    });
  };

  const recording = () => {
    view.innerHTML = `
      <p><a href="${esc(backHref)}">← Back</a></p>
      <div class="card"><div class="row" style="flex-wrap:wrap;gap:8px"><h3 style="margin:0">Recording</h3>
        <span class="pill RUNNING" id="recState">RECORDING</span><span class="muted" id="recUrl"></span><span class="spacer"></span>
        <button class="btn" id="stopRec">Stop recording</button><button class="btn" id="discardRec">Discard</button></div>
        <p class="muted">Use the browser window on this PC. Before each user action you want measured separately (for example “Log in”, “Search”, “Checkout”), name the step here.</p>
        <form class="row" id="stepForm"><input name="name" placeholder="Next step, e.g. Log in" maxlength="120" required style="flex:1;min-width:200px"><button class="btn primary" type="submit">New step</button></form>
        <div id="recSteps" style="margin-top:8px"></div><p class="muted" id="recErr"></p></div>`;
    const render = () => {
      const r = current;
      if (!r) return;
      view.querySelector("#recUrl").textContent = r.url || "";
      view.querySelector("#recSteps").innerHTML = `<table class="data"><thead><tr><th>Step</th><th>Requests</th><th>Kept for the script</th></tr></thead><tbody>
        ${r.steps.map((s) => `<tr><td>${esc(s.name)}${s.note ? `<div class="muted">${esc(s.note)}</div>` : ""}</td><td>${s.requests}</td><td>${s.kept}</td></tr>`).join("")}</tbody></table>
        ${r.secret_fields.length ? `<p class="muted">Password fields typed: ${r.secret_fields.map(esc).join(", ")} (saved as secrets, never in the script)</p>` : ""}`;
      view.querySelector("#recErr").textContent = r.error || "";
      if (!r.active) { stopPolling(); review(); }
    };
    render();
    stopPolling();
    timer = setInterval(async () => {
      try { current = (await api("/api/performance/recordings/current")).recording; } catch { return; }
      if (!current) { stopPolling(); startForm(); return; }
      render();
    }, 1500);
    view.querySelector("#stepForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        current = (await api(`/api/performance/recordings/${encodeURIComponent(current.id)}/step`, { method: "POST", body: { name: e.target.name.value.trim() } })).recording;
        e.target.reset();
        render();
      } catch (err) { toast(err.message, "error"); }
    });
    view.querySelector("#stopRec").addEventListener("click", async () => {
      try {
        current = (await api(`/api/performance/recordings/${encodeURIComponent(current.id)}/stop`, { method: "POST", body: {} })).recording;
        stopPolling();
        review();
      } catch (err) { toast(err.message, "error"); }
    });
    view.querySelector("#discardRec").addEventListener("click", discard);
  };

  const discard = async () => {
    if (!confirm("Discard this recording? Its requests and typed passwords are dropped.")) return;
    try { await api(`/api/performance/recordings/${encodeURIComponent(current.id)}`, { method: "DELETE" }); } catch { /* already gone */ }
    current = null;
    stopPolling();
    startForm();
  };

  const review = async () => {
    view.innerHTML = `<p><a href="${esc(backHref)}">← Back</a></p><div class="card"><p class="muted">Building the script…</p></div>`;
    const state = { exclude: new Set(), hosts: current.allowed_hosts.join(" "), includeStatic: current.include_static };
    const load = async () => {
      let r;
      try {
        r = await api(`/api/performance/recordings/${encodeURIComponent(current.id)}/preview`, { method: "POST", body: {
          allowed_hosts: hostsOf(state.hosts), include_static: state.includeStatic, exclude_ids: [...state.exclude] } });
      } catch (err) { view.querySelector(".card").innerHTML = `<p class="problems">${esc(err.message)}</p>`; return; }
      const excludedIds = new Map((r.report.excluded || []).map((x) => [x.id, x.reason]));
      view.innerHTML = `
        <p><a href="${esc(backHref)}">← Back</a></p>
        <div class="card"><h3>Review the recording</h3>
          ${r.recording.error ? `<p class="problems">${esc(r.recording.error)}</p>` : ""}
          <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr))">
            <label>Hosts to keep <input id="hosts" value="${esc(state.hosts)}"></label>
            <label><span><input type="checkbox" id="static" ${state.includeStatic ? "checked" : ""}> Keep images, styles, scripts and fonts</span></label></div>
          <button class="btn" id="refresh">Update</button>
          <div id="report" style="margin-top:8px">${reportHtml(r.report)}</div></div>
        <div class="card" style="margin-top:12px"><h3>Requests (${r.requests.length})</h3>
          <p class="muted">Untick a request to leave it out of the script.</p>
          <table class="data"><thead><tr><th></th><th>Step</th><th>Request</th><th>Status</th><th>Type</th></tr></thead><tbody>
          ${r.requests.map((q) => {
            const reason = excludedIds.get(q.id);
            const manual = state.exclude.has(q.id);
            const filtered = reason && !manual;
            return `<tr class="${filtered ? "muted" : ""}"><td><input type="checkbox" data-req="${q.id}" ${reason ? "" : "checked"} ${filtered ? `disabled title="${esc(reason)}"` : ""}></td>
              <td>${esc((r.recording.steps.find((s) => s.index === q.step) || {}).name || "")}</td><td><b>${esc(q.method)}</b> <span style="word-break:break-all">${esc(q.url)}</span>${filtered ? `<div class="muted">${esc(reason)}</div>` : ""}</td><td>${q.status ?? "—"}</td><td>${esc(q.type)}</td></tr>`;
          }).join("")}</tbody></table></div>
        <form class="card" id="saveRec" style="margin-top:12px"><h3>Save as a script</h3>
          <div class="row"><input name="name" placeholder="Script name" value="Recorded journey" required style="flex:1;min-width:200px">
            <select name="kind">${Object.entries(KINDS).map(([k, v]) => `<option value="${k}" ${k === (kind || "load") ? "selected" : ""}>${v}</option>`).join("")}</select>
            <button class="btn primary" type="submit">Create script</button><button class="btn" type="button" id="discardRec">Discard</button></div>
          <p class="muted">The script starts with 1 user for 60 seconds. Set users and duration in the script editor (thread group) before a real test. Saving does not start a test.</p></form>`;
      view.querySelector("#refresh").addEventListener("click", () => {
        state.hosts = view.querySelector("#hosts").value;
        state.includeStatic = view.querySelector("#static").checked;
        load();
      });
      view.querySelectorAll("input[data-req]").forEach((cb) => cb.addEventListener("change", () => {
        const id = Number(cb.dataset.req);
        if (cb.checked) state.exclude.delete(id); else state.exclude.add(id);
        load();
      }));
      view.querySelector("#discardRec").addEventListener("click", discard);
      view.querySelector("#saveRec").addEventListener("submit", async (e) => {
        e.preventDefault();
        try {
          const { plan } = await api(`/api/performance/recordings/${encodeURIComponent(current.id)}/script`, { method: "POST", body: {
            name: e.target.name.value.trim(), kind: e.target.kind.value, scenario_id: scenario || null,
            allowed_hosts: hostsOf(state.hosts), include_static: state.includeStatic, exclude_ids: [...state.exclude] } });
          toast(`Created “${plan.name}”`, "success");
          location.hash = `#/${routeName}?script=${encodeURIComponent(plan.id)}`;
        } catch (err) { toast(err.message, "error"); }
      });
    };
    load();
  };

  if (!current) startForm();
  else if (current.active) recording();
  else review();
}

// The Secrets card in the script editor
export function secretsHtml(secrets, report) {
  if (!secrets.length && !report) return "";
  return `<details ${secrets.some((s) => !s.set) ? "open" : ""} style="margin-top:8px"><summary>Secrets${secrets.length ? ` (${secrets.length})` : ""}${report ? " and recording" : ""}</summary>
    ${secrets.length ? `<p class="muted">Used in the script as <code>\${__P(name)}</code>. Values are kept in the Windows Credential Manager and given to JMeter only while a test runs.</p>
      <table class="data"><tbody>${secrets.map((s) => `<tr><td><code>${esc(s.usage)}</code></td><td>${s.set ? '<span class="pill PASS">Saved</span>' : '<span class="pill FAIL">No value</span>'}</td>
        <td><form class="row" data-secret="${esc(s.name)}"><input type="password" name="value" placeholder="${s.set ? "Replace value" : "Enter value"}" autocomplete="new-password" required><button class="btn" type="submit">Save</button></form></td></tr>`).join("")}</tbody></table>` : ""}
    ${report ? `<div style="margin-top:8px">${reportHtml(report, { secrets: false })}</div>` : ""}</details>`;
}

export function bindSecrets(root, planId, onSaved) {
  root.querySelectorAll("form[data-secret]").forEach((f) => f.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const { items } = await api(`/api/performance/plans/${encodeURIComponent(planId)}/secrets/${encodeURIComponent(f.dataset.secret)}`,
        { method: "PUT", body: { value: f.value.value } });
      toast(`Secret ${f.dataset.secret} saved`, "success");
      onSaved(items);
    } catch (err) { toast(err.message, "error"); }
  }));
}
