// 23.3.21: Automation Studio > Scheduled Runs, and Settings > Outlook Email.
import { api } from "../api.js";
import { esc, toast, dateTimeOf, openModal } from "../util.js";
import { confirmDelete } from "../confirm-delete.js";

const BROWSERS = [["chromium", "Chromium"], ["msedge", "Microsoft Edge"], ["chrome", "Google Chrome"], ["firefox", "Firefox"]];
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const PAGES = ["AI-Driven Automation", "Guided Browser Automation", "Hybrid Test Automation", "Application Discovery & Update"];
const pill = (status) => status ? `<span class="pill ${esc({ PASSED: "PASS", FAILED: "FAIL", ERROR: "FAIL", RUNNING: "RUNNING", QUEUED: "WAITING", STOPPED: "WARN" }[status] || "WAITING")}">${esc(status)}</span>` : '<span class="muted">-</span>';
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const inAMinute = () => { const d = new Date(Date.now() + 5 * 60000); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };

let timers = [];
const every = (ms, fn) => { const t = setInterval(fn, ms); timers.push(t); };

function scheduleForm(selected, current = {}, { runNow = false, emailReady = false } = {}) {
  const c = { repeat: "daily", time: inAMinute(), start_date: today(), browsers: ["chromium"], parallel: 1, headless: false,
    data_rows: "first", catch_up: true, publish_tfs: false, email: { enabled: false, to: "", cc: "", on: "always" }, days: [0, 1, 2, 3, 4], every_hours: 1, ...current };
  const em = c.email || {};
  return `<form class="grid" id="schForm" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
    <p style="grid-column:1/-1;margin:0"><strong>${selected.length} script(s):</strong> ${selected.map((s) => esc(s.name)).join(", ")}</p>
    ${runNow ? "" : `<label>Schedule name <input name="name" value="${esc(c.name || "")}" placeholder="e.g. Nightly regression"></label>
    <label>Repeat <select name="repeat">
      ${[["once", "Once (a future date and time)"], ["daily", "Every day"], ["weekdays", "Monday to Friday"], ["weekly", "Chosen days of the week"], ["hourly", "Every few hours"]]
        .map(([v, l]) => `<option value="${v}" ${c.repeat === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    <label>Time (this PC) <input name="time" type="time" required value="${esc(c.time)}"></label>
    <label><span data-start-label>${c.repeat === "once" ? "Date" : "Start date"}</span> <input name="start_date" type="date" required value="${esc(c.start_date)}"></label>
    <label data-end>Until (empty = continues) <input name="end_date" type="date" value="${esc(c.end_date || "")}"></label>
    <label data-hours>Every (hours) <input name="every_hours" type="number" min="1" max="24" value="${esc(c.every_hours || 1)}"></label>
    <div data-days style="grid-column:1/-1"><span class="muted">Days</span><div class="chips" style="margin-top:4px">
      ${DAYS.map((d, i) => `<label class="chip" style="display:inline-flex;gap:6px;align-items:center"><input type="checkbox" name="day" value="${i}" style="width:auto;margin:0" ${(c.days || []).includes(i) ? "checked" : ""}>${d}</label>`).join("")}</div></div>`}
    <div style="grid-column:1/-1"><span class="muted">Browsers (each script runs in each one)</span><div class="chips" style="margin-top:4px">
      ${BROWSERS.map(([v, l]) => `<label class="chip" style="display:inline-flex;gap:6px;align-items:center"><input type="checkbox" name="browser" value="${v}" style="width:auto;margin:0" ${(c.browsers || []).includes(v) ? "checked" : ""}>${l}</label>`).join("")}</div></div>
    <label>Runs at the same time <input name="parallel" type="number" min="1" max="8" value="${esc(c.parallel)}"></label>
    <label>Excel / CSV data <select name="data_rows"><option value="first" ${c.data_rows === "first" ? "selected" : ""}>Row 1 only</option><option value="all" ${c.data_rows === "all" ? "selected" : ""}>Every row (one run per row)</option></select></label>
    <label>Browser window <select name="headless"><option value="0" ${!c.headless ? "selected" : ""}>Visible</option><option value="1" ${c.headless ? "selected" : ""}>Hidden (headless)</option></select></label>
    <label style="display:flex;flex-direction:row;gap:8px;align-items:center"><input type="checkbox" name="publish_tfs" style="width:auto;margin:0" ${c.publish_tfs ? "checked" : ""}>Write Passed / Failed to TFS test runs</label>
    ${runNow ? "" : `<label style="display:flex;flex-direction:row;gap:8px;align-items:center"><input type="checkbox" name="catch_up" style="width:auto;margin:0" ${c.catch_up ? "checked" : ""}>Run a missed time when the app opens</label>`}
    <fieldset style="grid-column:1/-1;border:1px solid var(--border);border-radius:8px;padding:10px 12px"><legend>Email report (Outlook)</legend>
      ${emailReady ? "" : '<p class="muted" style="margin:0 0 8px">Set up the sender first in <a href="#/email-settings">Settings > Outlook Email</a>.</p>'}
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
      <label style="display:flex;flex-direction:row;gap:8px;align-items:center"><input type="checkbox" name="email_enabled" style="width:auto;margin:0" ${em.enabled ? "checked" : ""}>Send the report by email</label>
      <label>To <input name="email_to" value="${esc(em.to || "")}" placeholder="lead@company.com; qa@company.com"></label>
      <label>Cc <input name="email_cc" value="${esc(em.cc || "")}"></label>
      <label>Send <select name="email_on"><option value="always" ${em.on !== "failures" ? "selected" : ""}>After every run</option><option value="failures" ${em.on === "failures" ? "selected" : ""}>Only when something failed</option></select></label></div>
    </fieldset>
    <div class="row" style="grid-column:1/-1"><span class="spacer"></span><button class="btn primary" type="submit">${runNow ? "Run now" : current.id ? "Save schedule" : "Create schedule"}</button></div>
  </form>`;
}

function wireForm(root) {
  const f = root.querySelector("#schForm");
  const rep = f.querySelector("[name=repeat]");
  const sync = () => {
    if (!rep) return;
    const v = rep.value;
    f.querySelector("[data-days]").hidden = v !== "weekly";
    f.querySelector("[data-hours]").hidden = v !== "hourly";
    f.querySelector("[data-end]").hidden = v === "once";
    f.querySelector("[data-start-label]").textContent = v === "once" ? "Date" : "Start date";
  };
  rep?.addEventListener("change", sync); sync();
  return f;
}

function readForm(f, ids) {
  const fd = new FormData(f);
  const body = {
    script_ids: ids, browsers: fd.getAll("browser"), parallel: Number(fd.get("parallel")) || 1,
    headless: fd.get("headless") === "1", data_rows: fd.get("data_rows"), publish_tfs: fd.get("publish_tfs") === "on",
    email: { enabled: fd.get("email_enabled") === "on", to: fd.get("email_to") || "", cc: fd.get("email_cc") || "", on: fd.get("email_on") },
  };
  if (fd.has("repeat")) {
    Object.assign(body, { name: fd.get("name") || null, repeat: fd.get("repeat"), time: fd.get("time"), start_date: fd.get("start_date"),
      end_date: fd.get("end_date") || null, days: fd.getAll("day").map(Number), every_hours: Number(fd.get("every_hours")) || 1,
      catch_up: fd.get("catch_up") === "on" });
    if (body.repeat === "once") body.end_date = null;
  }
  if (!body.browsers.length) throw new Error("Choose at least one browser.");
  return body;
}

function batchHtml(b) {
  const c = b.counts || {};
  const done = b.items.filter((i) => !["QUEUED", "RUNNING"].includes(i.status)).length;
  const extra = [b.email ? (b.email.ok ? `Email sent to ${esc((b.email.to || []).join(", "))}` : `<span style="color:var(--fail)">Email not sent: ${esc(b.email.error)}</span>`) : "",
    b.tfs ? (b.tfs.ok ? `TFS: ${(b.tfs.runs || []).length} test run(s) written` : `<span style="color:var(--fail)">TFS: ${esc(b.tfs.error)}</span>`) : ""].filter(Boolean).join(" · ");
  return `<details class="card" style="margin-top:8px;padding:10px 12px"><summary class="row" style="cursor:pointer;gap:10px;flex-wrap:wrap">
      ${pill(b.status)}<strong>${esc(b.name)}</strong><span class="muted">${esc(dateTimeOf(b.started_at))} · ${b.trigger === "schedule" ? "scheduled" : b.trigger === "missed" ? "missed time, run when the app opened" : "run now"} ·
      ${b.status === "RUNNING" ? `${done}/${b.items.length} done` : `${c.PASSED || 0}/${b.items.length} passed`}</span></summary>
    ${extra ? `<p class="muted" style="margin:8px 0">${extra}</p>` : ""}
    <table class="data"><thead><tr><th>Script</th><th>Browser</th><th>Data row</th><th>Result</th><th>Time</th><th>Failure</th><th>Run</th></tr></thead><tbody>
    ${b.items.map((i) => `<tr><td>${esc(i.name)}</td><td>${esc(i.browser || "")}</td><td>${i.row != null ? `${i.row + 1}` : ""}</td><td>${pill(i.status)}</td>
      <td>${i.duration_ms != null ? `${(i.duration_ms / 1000).toFixed(1)} s` : ""}</td>
      <td style="max-width:420px">${i.failed_step ? `Step ${esc(i.failed_step)}: ` : ""}${esc(i.error || "")}</td>
      <td>${i.run_id && !["QUEUED"].includes(i.status) ? `<a href="#/history?run=${esc(i.run_id)}">${esc(i.run_id)}</a>` : ""}</td></tr>`).join("")}
    </tbody></table></details>`;
}

async function mountSchedules(view) {
  const state = { filterPage: "", filterText: "", filterApp: "", onlyScheduled: false, selected: new Set() };
  let scripts = [], schedules = [], meta = {};
  view.innerHTML = `
    <div class="card"><div class="row" style="flex-wrap:wrap;gap:8px"><h3 style="margin:0">Scripts</h3><span class="spacer"></span>
      <select id="fPage"><option value="">All pages</option>${PAGES.map((p) => `<option>${esc(p)}</option>`).join("")}</select>
      <select id="fApp"><option value="">All applications</option></select>
      <input id="fText" placeholder="Filter by name or test case" style="min-width:220px">
      <label class="row" style="gap:6px"><input type="checkbox" id="fSched" style="width:auto;margin:0">Only scheduled</label></div>
      <p class="muted" style="margin:6px 0 10px">Tick scripts, then <strong>Schedule</strong> (a future date and time, or repeating every day, Monday to Friday, chosen days or every few hours, until a date or continuing) or <strong>Run now</strong>. Times are this PC's clock; the application must be running.</p>
      <div class="row" style="gap:8px;margin-bottom:8px"><button class="btn primary" id="schBtn" disabled>Schedule</button><button class="btn" id="runBtn" disabled>Run now</button>
        <span class="muted" id="selCount"></span><span class="spacer"></span>
        <label class="row" style="gap:6px">Runs at the same time (Run all, Play selected) <input id="parallelRuns" type="number" min="1" max="8" style="width:70px"></label></div>
      <div id="scriptList"><p class="muted">Loading…</p></div></div>
    <div class="card" style="margin-top:12px"><h3 style="margin-top:0">Schedules</h3><div id="schList"><p class="muted">Loading…</p></div></div>
    <div class="card" style="margin-top:12px"><div class="row"><h3 style="margin:0">Run history</h3><span class="spacer"></span><select id="hFilter"><option value="">All schedules and Run now</option></select></div>
      <div id="history"><p class="muted">Loading…</p></div></div>`;

  const renderScripts = () => {
    const t = state.filterText.toLowerCase();
    const shown = scripts.filter((s) => (!state.filterPage || s.page === state.filterPage) && (!state.filterApp || (s.application || "") === state.filterApp)
      && (!state.onlyScheduled || s.schedules.length) && (!t || `${s.name} ${s.id} ${s.test_case_id || ""}`.toLowerCase().includes(t)));
    const box = view.querySelector("#scriptList");
    box.innerHTML = shown.length ? `<table class="data"><thead><tr><th><input type="checkbox" id="selAll" style="width:auto;margin:0"></th><th>Script</th><th>Page</th><th>Test case</th><th>Application</th><th>Steps</th><th>Data rows</th><th>Schedules</th></tr></thead><tbody>
      ${shown.map((s) => `<tr><td><input type="checkbox" data-sel="${esc(s.id)}" style="width:auto;margin:0" ${state.selected.has(s.id) ? "checked" : ""}></td>
        <td><strong>${esc(s.name)}</strong><div class="muted" style="font-size:12px">${esc(s.id)}</div></td><td>${esc(s.page)}</td><td>${esc(s.test_case_id || "")}</td>
        <td>${esc(s.application || "")}</td><td>${esc(s.steps ?? "")}</td><td>${s.rows || ""}</td>
        <td>${s.schedules.map((id) => { const x = schedules.find((y) => y.id === id); return x ? `<span class="pill ${x.enabled ? "PASS" : "WAITING"}" title="${esc(x.when)}">${esc(x.name)}</span>` : ""; }).join(" ")}</td></tr>`).join("")}
      </tbody></table>` : '<p class="muted">No scripts match the filter.</p>';
    box.querySelectorAll("[data-sel]").forEach((cb) => cb.addEventListener("change", () => { cb.checked ? state.selected.add(cb.dataset.sel) : state.selected.delete(cb.dataset.sel); syncSel(); }));
    box.querySelector("#selAll")?.addEventListener("change", (e) => { shown.forEach((s) => (e.target.checked ? state.selected.add(s.id) : state.selected.delete(s.id))); renderScripts(); syncSel(); });
  };
  const syncSel = () => {
    const n = state.selected.size;
    view.querySelector("#schBtn").disabled = view.querySelector("#runBtn").disabled = !n;
    view.querySelector("#selCount").textContent = n ? `${n} selected` : "";
  };
  const renderSchedules = () => {
    const box = view.querySelector("#schList");
    box.innerHTML = schedules.length ? `<table class="data"><thead><tr><th>Schedule</th><th>When</th><th>Scripts</th><th>Browsers</th><th>Next run</th><th>Last run</th><th>Email</th><th></th></tr></thead><tbody>
      ${schedules.map((s) => `<tr><td><strong>${esc(s.name)}</strong>${s.enabled ? "" : ' <span class="pill WAITING">PAUSED</span>'}${s.running ? ' <span class="pill RUNNING">RUNNING</span>' : ""}${s.finished ? ' <span class="pill WAITING">ENDED</span>' : ""}</td>
        <td>${esc(s.when)}</td><td>${s.script_names.map(esc).join("<br>")}</td><td>${esc(s.browsers.join(", "))}${s.parallel > 1 ? `<div class="muted">${s.parallel} at the same time</div>` : ""}</td>
        <td>${s.enabled && s.next_run_at ? esc(dateTimeOf(s.next_run_at)) : '<span class="muted">-</span>'}</td>
        <td>${s.last_run_at ? `${pill(s.last_status)} <span class="muted">${esc(dateTimeOf(s.last_run_at))}</span>` : '<span class="muted">never</span>'}</td>
        <td>${s.email?.enabled ? esc(s.email.to) : '<span class="muted">off</span>'}${s.publish_tfs ? '<div class="muted">+ TFS results</div>' : ""}</td>
        <td class="row" style="gap:6px;flex-wrap:wrap"><button class="btn" data-run="${esc(s.id)}" ${s.running ? "disabled" : ""}>Run now</button>
          <button class="btn" data-toggle="${esc(s.id)}">${s.enabled ? "Pause" : "Resume"}</button><button class="btn" data-edit="${esc(s.id)}">Edit</button>
          <button class="btn" data-del="${esc(s.id)}">Delete</button></td></tr>`).join("")}</tbody></table>` : '<p class="muted">No schedules yet. Tick scripts above and choose Schedule.</p>';
    const hf = view.querySelector("#hFilter"), cur = hf.value;
    hf.innerHTML = `<option value="">All schedules and Run now</option>${schedules.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}`;
    hf.value = cur;
  };
  const renderHistory = async () => {
    const id = view.querySelector("#hFilter").value;
    const { items } = await api(`/api/schedules/history?limit=30${id ? `&schedule_id=${encodeURIComponent(id)}` : ""}`);
    view.querySelector("#history").innerHTML = items.length ? items.map(batchHtml).join("") : '<p class="muted">No scheduled runs yet.</p>';
    const open = view.querySelector("#history details"); if (open && items[0]?.status === "RUNNING") open.open = true;
  };
  const load = async () => {
    const [{ items: sc }, list] = await Promise.all([api("/api/schedules/scripts"), api("/api/schedules")]);
    scripts = sc; schedules = list.items; meta = list;
    const apps = [...new Set(scripts.map((s) => s.application).filter(Boolean))].sort();
    const fa = view.querySelector("#fApp"), cur = fa.value;
    fa.innerHTML = `<option value="">All applications</option>${apps.map((a) => `<option>${esc(a)}</option>`).join("")}`; fa.value = cur;
    const pr = view.querySelector("#parallelRuns"); if (document.activeElement !== pr) pr.value = meta.parallel_runs;
    renderScripts(); renderSchedules(); syncSel();
  };
  const openForm = (title, ids, current, runNow) => {
    const selected = ids.map((id) => scripts.find((s) => s.id === id) || { id, name: id });
    const m = openModal(title, scheduleForm(selected, current, { runNow, emailReady: meta.email_configured }), { wide: true });
    const f = wireForm(m.el);
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const body = readForm(f, ids);
        if (runNow) {
          await api("/api/schedule-runs", { method: "POST", body: { ...body, name: selected.length === 1 ? selected[0].name : `Run now (${selected.length} scripts)` } });
          toast("Started. Progress is in Run history.", "success");
        } else if (current?.id) {
          await api(`/api/schedules/${encodeURIComponent(current.id)}`, { method: "PUT", body });
          toast("Schedule saved.", "success");
        } else {
          const { schedule } = await api("/api/schedules", { method: "POST", body });
          toast(`Scheduled: ${schedule.when}. Next run ${dateTimeOf(schedule.next_run_at)}.`, "success");
        }
        m.close(); state.selected.clear(); await load(); await renderHistory();
      } catch (err) { toast(err.message, "error"); }
    });
  };
  view.querySelector("#fPage").addEventListener("change", (e) => { state.filterPage = e.target.value; renderScripts(); });
  view.querySelector("#fApp").addEventListener("change", (e) => { state.filterApp = e.target.value; renderScripts(); });
  view.querySelector("#fText").addEventListener("input", (e) => { state.filterText = e.target.value; renderScripts(); });
  view.querySelector("#fSched").addEventListener("change", (e) => { state.onlyScheduled = e.target.checked; renderScripts(); });
  view.querySelector("#hFilter").addEventListener("change", renderHistory);
  view.querySelector("#schBtn").addEventListener("click", () => openForm("Schedule scripts", [...state.selected], {}, false));
  view.querySelector("#runBtn").addEventListener("click", () => openForm("Run now", [...state.selected], {}, true));
  view.querySelector("#parallelRuns").addEventListener("change", async (e) => {
    try { await api("/api/schedules-settings", { method: "PUT", body: { parallel_runs: Number(e.target.value) || 1 } }); toast(`Up to ${e.target.value} run(s) at the same time.`, "success"); }
    catch (err) { toast(err.message, "error"); }
  });
  view.querySelector("#schList").addEventListener("click", async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const s = schedules.find((x) => x.id === (b.dataset.run || b.dataset.toggle || b.dataset.edit || b.dataset.del));
    if (!s) return;
    try {
      if (b.dataset.run) { await api(`/api/schedules/${encodeURIComponent(s.id)}/run`, { method: "POST", body: {} }); toast("Started. Progress is in Run history.", "success"); }
      else if (b.dataset.toggle) { await api(`/api/schedules/${encodeURIComponent(s.id)}`, { method: "PUT", body: { enabled: !s.enabled } }); }
      else if (b.dataset.edit) { openForm(`Edit ${s.name}`, s.script_ids, s, false); return; }
      else if (b.dataset.del) {
        if (!(await confirmDelete({ title: "Delete schedule?", name: s.name, body: "It will not run again. Its run history is kept." }))) return;
        await api(`/api/schedules/${encodeURIComponent(s.id)}`, { method: "DELETE" });
      }
      await load(); await renderHistory();
    } catch (err) { toast(err.message, "error"); }
  });
  await load();
  await renderHistory();
  every(4000, async () => { try { const l = await api("/api/schedules"); schedules = l.items; meta = l; renderSchedules(); if (l.active.length || view.querySelector("#history .pill.RUNNING")) await renderHistory(); } catch { /* next time */ } });
}

async function mountEmail(view) {
  const { email: e } = await api("/api/email/settings");
  view.innerHTML = `<form class="card grid" id="emailForm" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr))">
    <h3 style="grid-column:1/-1;margin:0">Outlook email for run reports</h3>
    <p class="muted" style="grid-column:1/-1;margin:0">Scheduled Runs send their HTML report (and the screenshot of each failure) through this account.</p>
    <label>Send with <select name="mode"><option value="smtp" ${e.mode !== "outlook_app" ? "selected" : ""}>Outlook / Microsoft 365 account (SMTP)</option>
      <option value="outlook_app" ${e.mode === "outlook_app" ? "selected" : ""}>The Outlook app on this PC (no password)</option></select></label>
    <label data-smtp>Account type <select name="preset"><option value="office365" ${e.preset === "office365" ? "selected" : ""}>Microsoft 365 / Office 365 (smtp.office365.com)</option>
      <option value="outlook_com" ${e.preset === "outlook_com" ? "selected" : ""}>Outlook.com / Hotmail (smtp-mail.outlook.com)</option>
      <option value="custom" ${e.preset === "custom" ? "selected" : ""}>Company Exchange / other SMTP server</option></select></label>
    <label data-smtp data-custom>SMTP server <input name="host" value="${esc(e.host || "")}"></label>
    <label data-smtp data-custom>Port <input name="port" type="number" value="${esc(e.port || 587)}"></label>
    <label data-smtp data-custom>Security <select name="security">${[["starttls", "STARTTLS (587)"], ["ssl", "SSL (465)"], ["none", "None (internal relay)"]].map(([v, l]) => `<option value="${v}" ${e.security === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    <label data-smtp>Sign-in user (email) <input name="username" value="${esc(e.username || "")}" placeholder="qa.reports@company.com" autocomplete="off"></label>
    <label data-smtp>Password or app password <input name="secret" type="password" autocomplete="new-password" placeholder="${e.has_password ? "saved (leave empty to keep)" : "not saved"}"></label>
    <label data-smtp>From address <input name="sender" value="${esc(e.sender || "")}" placeholder="qa.reports@company.com"></label>
    <label>From name <input name="sender_name" value="${esc(e.sender_name || "")}"></label>
    <label>Default recipients <input name="default_to" value="${esc(e.default_to || "")}" placeholder="lead@company.com; team@company.com"></label>
    <p class="muted" data-app style="grid-column:1/-1;margin:0">Outlook (classic desktop) must be installed and signed in for the Windows user that runs QA Enterprise AI. The mail is sent from that mailbox and appears in its Sent Items.</p>
    <p class="muted" data-smtp style="grid-column:1/-1;margin:0">The password is kept in the Windows Credential Manager, never in the settings file. Microsoft 365 needs SMTP sign-in (SMTP AUTH) allowed for this mailbox; with multi-factor sign-in use an app password.</p>
    <div class="row" style="grid-column:1/-1;gap:8px"><span class="muted">${e.configured ? "Ready to send." : "Not set up yet."}</span><span class="spacer"></span>
      ${e.has_password ? '<button class="btn" type="button" id="clearPw">Remove saved password</button>' : ""}
      <input id="testTo" placeholder="Send a test email to…" value="${esc(e.default_to || e.sender || "")}" style="max-width:260px">
      <button class="btn" type="button" id="testBtn">Send test email</button><button class="btn primary" type="submit">Save</button></div>
  </form>`;
  const f = view.querySelector("#emailForm");
  const sync = () => {
    const app = f.mode.value === "outlook_app", custom = f.preset.value === "custom";
    f.querySelectorAll("[data-smtp]").forEach((x) => { x.hidden = app || (x.hasAttribute("data-custom") && !custom); });
    f.querySelectorAll("[data-app]").forEach((x) => { x.hidden = !app; });
  };
  f.mode.addEventListener("change", sync); f.preset.addEventListener("change", sync); sync();
  const save = async (extra = {}) => {
    const fd = Object.fromEntries(new FormData(f));
    const body = { mode: fd.mode, preset: fd.preset, username: fd.username ?? undefined, sender: fd.sender ?? undefined, sender_name: fd.sender_name,
      default_to: fd.default_to, ...extra };
    if (fd.preset === "custom") Object.assign(body, { host: fd.host, port: Number(fd.port) || 587, security: fd.security });
    if (fd.secret) body.secret = fd.secret;
    await api("/api/email/settings", { method: "PUT", body });
  };
  f.addEventListener("submit", async (ev) => { ev.preventDefault(); try { await save(); toast("Email settings saved.", "success"); await mountEmail(view); } catch (err) { toast(err.message, "error"); } });
  view.querySelector("#clearPw")?.addEventListener("click", async () => { try { await save({ clear_secret: true }); toast("Saved password removed.", "success"); await mountEmail(view); } catch (err) { toast(err.message, "error"); } });
  view.querySelector("#testBtn").addEventListener("click", async () => {
    try { await save(); const r = await api("/api/email/test", { method: "POST", body: { to: view.querySelector("#testTo").value } }); toast(`Test email sent to ${r.to.join(", ")}.`, "success"); }
    catch (err) { toast(`${err.message}${err.details ? ` (${err.details})` : ""}`, "error", 9000); }
  });
}

export const scheduledRuns = {
  async mount(view) { timers.forEach(clearInterval); timers = []; await mountSchedules(view); },
  unmount() { timers.forEach(clearInterval); timers = []; },
};
export const emailSettings = { async mount(view) { await mountEmail(view); } };
