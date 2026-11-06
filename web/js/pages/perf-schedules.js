// 23.3.29: Performance Testing > Performance Schedules. A schedule runs a saved scenario once, every day, on chosen
// days, every N weeks or every N minutes, with the versions it pins (or the latest saved ones), an overlap and a
// missed-run policy, a maximum runtime, the PDF report and an Outlook email. Schedules run only while the
// application runs on this PC; "Start at Windows sign-in" starts it without administrator rights.

import { api } from "../api.js";
import { esc, toast, dateTimeOf, openModal } from "../util.js";
import { confirmDelete } from "../confirm-delete.js";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const REPEATS = [["once", "Once (a date and time)"], ["daily", "Every day"], ["days", "Chosen weekdays"],
  ["weekly", "Every N weeks on chosen days"], ["interval", "Every N minutes or hours"]];
const JOB = { QUEUED: ["WAITING", "Queued"], VALIDATING: ["RUNNING", "Checking"], RUNNING: ["RUNNING", "Running"],
  STOPPING: ["RUNNING", "Stopping"], COMPLETED: ["PASS", "Completed"], FAILED: ["FAIL", "Failed"],
  CANCELLED: ["WARN", "Stopped"], INTERRUPTED: ["WARN", "Interrupted"], SKIPPED: ["WARN", "Skipped"],
  MISSED: ["WARN", "Missed"], ERROR: ["FAIL", "Not started"], CLAIMED: ["WAITING", "Starting"] };
const KIND = { schedule: "on time", catch_up: "missed time, run once", manual: "run now", missed: "missed" };
const VERDICT = { PASS: "targets met", FAIL: "targets missed", NOT_SPECIFIED: "no targets", INCONCLUSIVE: "inconclusive" };
const pad = (n) => String(n).padStart(2, "0");
const today = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
const soon = () => { const d = new Date(Date.now() + 10 * 60000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const pill = (status) => { const [cls, label] = JOB[status] || ["WAITING", status || "-"]; return `<span class="pill ${cls}">${esc(label)}</span>`; };

let timer = null;

function form(c, scenarios, host, emailReady) {
  const s = { repeat: "daily", time: soon(), start_date: today(), days: [0, 1, 2, 3, 4], every_weeks: 1, interval_minutes: 60,
    timezone: "system", version_policy: "latest", overlap: "skip", missed: "skip", report: { pdf: true },
    notify: { email: { enabled: false, to: "", cc: "", on: "always" } }, ...c };
  const em = (s.notify || {}).email || {};
  const zones = host.timezones || [];
  const sc = scenarios.find((x) => x.id === s.scenario_id);
  return `<form class="grid" id="psForm" style="grid-template-columns:repeat(auto-fit,minmax(210px,1fr))">
    <label>Scenario <select name="scenario_id" required>${scenarios.map((x) => `<option value="${esc(x.id)}" ${x.id === s.scenario_id ? "selected" : ""}>${esc(x.name)}${x.plan_id ? "" : " (no script yet)"}</option>`).join("")}</select></label>
    <label>Schedule name <input name="name" value="${esc(s.name || "")}" placeholder="${esc(sc ? sc.name : "e.g. Nightly load")}"></label>
    <label>Repeat <select name="repeat">${REPEATS.map(([v, l]) => `<option value="${v}" ${s.repeat === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    <label>Time <input name="time" type="time" required value="${esc(s.time)}"></label>
    <label><span data-start-label>Start date</span> <input name="start_date" type="date" required value="${esc(s.start_date)}"></label>
    <label data-end>Until (empty = continues) <input name="end_date" type="date" value="${esc(s.end_date || "")}"></label>
    <label data-weeks>Every (weeks) <input name="every_weeks" type="number" min="1" max="52" value="${esc(s.every_weeks || 1)}"></label>
    <label data-interval>Every (minutes, 15 or more) <input name="interval_minutes" type="number" min="15" max="10080" step="5" value="${esc(s.interval_minutes || 60)}"></label>
    <div data-days style="grid-column:1/-1"><span class="muted">Days</span><div class="chips" style="margin-top:4px">
      ${DAYS.map((d, i) => `<label class="chip" style="display:inline-flex;gap:6px;align-items:center"><input type="checkbox" name="day" value="${i}" style="width:auto;margin:0" ${(s.days || []).includes(i) ? "checked" : ""}>${d}</label>`).join("")}</div></div>
    <label>Time zone <select name="timezone"><option value="system">System: ${esc(host.system_timezone || "this PC")}</option>
      ${zones.map((z) => `<option ${z === s.timezone ? "selected" : ""}>${esc(z)}</option>`).join("")}</select></label>
    <label>Versions <select name="version_policy">
      <option value="latest" ${s.version_policy !== "pinned" ? "selected" : ""}>Latest saved (resolved when each run is created)</option>
      <option value="pinned" ${s.version_policy === "pinned" ? "selected" : ""}>Pinned (script, data, workload and targets as now)</option></select></label>
    ${s.version_policy === "pinned" && s.pinned ? `<label style="display:flex;flex-direction:row;gap:8px;align-items:center"><input type="checkbox" name="repin" style="width:auto;margin:0">Pin the current versions again</label>` : ""}
    <label>If the previous run is still active <select name="overlap">
      <option value="skip" ${s.overlap !== "queue" ? "selected" : ""}>Skip this time (default)</option>
      <option value="queue" ${s.overlap === "queue" ? "selected" : ""}>Queue it behind (at most one waiting)</option></select></label>
    <label>Times missed while the app was closed <select name="missed">
      <option value="skip" ${s.missed !== "run_once" ? "selected" : ""}>Record them, run nothing</option>
      <option value="run_once" ${s.missed === "run_once" ? "selected" : ""}>Record them, run the latest once</option></select></label>
    <label>Maximum runtime (minutes) <input name="max_runtime_min" type="number" min="1" max="1440" value="${esc(s.max_runtime_min || "")}" placeholder="from the workload"></label>
    <label>Application build (optional) <input name="build" value="${esc(s.build || "")}" placeholder="the environment's build"></label>
    <label style="display:flex;flex-direction:row;gap:8px;align-items:center"><input type="checkbox" name="pdf" style="width:auto;margin:0" ${(s.report || {}).pdf !== false ? "checked" : ""}>Make the PDF report after each run</label>
    <fieldset style="grid-column:1/-1;border:1px solid var(--border);border-radius:8px;padding:10px 12px"><legend>Notifications</legend>
      <p class="muted" style="margin:0 0 8px">The bell shows every finished, skipped or missed run.${emailReady ? "" : ' Email: set up the sender first in <a href="#/email-settings">Settings > Outlook Email</a>.'}</p>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
      <label style="display:flex;flex-direction:row;gap:8px;align-items:center"><input type="checkbox" name="email_enabled" style="width:auto;margin:0" ${em.enabled ? "checked" : ""}>Email the result (PDF attached)</label>
      <label>To <input name="email_to" value="${esc(Array.isArray(em.to) ? em.to.join("; ") : em.to || "")}" placeholder="lead@company.com; qa@company.com"></label>
      <label>Cc <input name="email_cc" value="${esc(Array.isArray(em.cc) ? em.cc.join("; ") : em.cc || "")}"></label>
      <label>Send <select name="email_on"><option value="always" ${em.on !== "failures" ? "selected" : ""}>After every run</option><option value="failures" ${em.on === "failures" ? "selected" : ""}>Only when it failed or missed targets</option></select></label></div>
    </fieldset>
    <p class="muted" style="grid-column:1/-1;margin:0">Only saved versions run; drafts open in the script editor never do. Load runs never overlap: this PC runs one test at a time.</p>
    <div class="row" style="grid-column:1/-1"><span class="spacer"></span><button class="btn primary" type="submit">${c.id ? "Save schedule" : "Create schedule"}</button></div>
  </form>`;
}

function wire(f) {
  const rep = f.querySelector("[name=repeat]");
  const sync = () => {
    const v = rep.value;
    f.querySelector("[data-days]").hidden = !["days", "weekly"].includes(v);
    f.querySelector("[data-weeks]").hidden = v !== "weekly";
    f.querySelector("[data-interval]").hidden = v !== "interval";
    f.querySelector("[data-end]").hidden = v === "once";
    f.querySelector("[data-start-label]").textContent = v === "once" ? "Date" : v === "interval" ? "First run on" : "Start date";
  };
  rep.addEventListener("change", sync); sync();
}

function read(f) {
  const fd = new FormData(f);
  const body = { scenario_id: fd.get("scenario_id"), name: fd.get("name") || null, repeat: fd.get("repeat"), time: fd.get("time"),
    start_date: fd.get("start_date"), end_date: fd.get("repeat") === "once" ? null : fd.get("end_date") || null,
    days: fd.getAll("day").map(Number), every_weeks: Number(fd.get("every_weeks")) || 1,
    interval_minutes: Number(fd.get("interval_minutes")) || null, timezone: fd.get("timezone"),
    version_policy: fd.get("version_policy"), repin: fd.get("repin") === "on", overlap: fd.get("overlap"), missed: fd.get("missed"),
    max_runtime_min: Number(fd.get("max_runtime_min")) || null, build: fd.get("build") || "",
    report: { pdf: fd.get("pdf") === "on" },
    notify: { email: { enabled: fd.get("email_enabled") === "on", to: fd.get("email_to") || "", cc: fd.get("email_cc") || "", on: fd.get("email_on") } } };
  if (body.repeat !== "interval") delete body.interval_minutes;
  return body;
}

function hostCard(h) {
  const st = h.startup || {};
  return `<div class="card"><div class="row" style="flex-wrap:wrap;gap:10px"><h3 style="margin:0">Scheduler on this PC</h3>
      <span class="pill ${h.running ? "PASS" : "FAIL"}">${h.running ? "Checking every " + esc(h.check_every_s) + " s" : "Not running"}</span>
      <span class="muted">${h.next ? `Next: <strong>${esc(h.next.name)}</strong> at ${esc(h.next.local)}` : "No schedule is due."}</span><span class="spacer"></span>
      ${st.supported ? `<label class="row" style="gap:6px" title="${esc(st.note)}"><input type="checkbox" id="startup" style="width:auto;margin:0" ${st.enabled ? "checked" : ""}>Start at Windows sign-in</label>` : `<span class="muted">${esc(st.note || "")}</span>`}</div>
    <p class="muted" style="margin:8px 0 0">${esc(h.explain)} Time zone of this PC: ${esc(h.system_timezone)}.</p></div>`;
}

function listHtml(items) {
  if (!items.length) return '<p class="muted">No schedules yet. Choose <strong>New schedule</strong>.</p>';
  return `<div class="data-grid"><table class="data"><thead><tr><th>Schedule</th><th>Scenario / environment</th><th>When</th><th>Next run</th><th>Versions</th><th>Policies</th><th>Last</th><th></th></tr></thead><tbody>
    ${items.map((s) => `<tr data-id="${esc(s.id)}"><td><strong>${esc(s.name)}</strong><div class="muted" style="font-size:12px">${esc(s.id)}</div>
        <span class="pill ${s.enabled ? (s.finished ? "WAITING" : "PASS") : "WARN"}">${s.enabled ? (s.finished ? "Finished" : "Active") : "Paused"}</span></td>
      <td>${esc(s.scenario_name)}<div class="muted">${esc(s.environment_name || "no environment")}</div></td>
      <td>${esc(s.when)}</td><td>${s.next_run_local ? esc(s.next_run_local) : '<span class="muted">-</span>'}</td>
      <td>${s.version_policy === "pinned" && s.pinned ? `Pinned: script v${esc(s.pinned.script_version ?? "-")}${Object.keys(s.pinned.datasets || {}).length ? `, ${Object.keys(s.pinned.datasets).length} data set(s)` : ""}` : "Latest saved"}</td>
      <td class="muted" style="font-size:12px">Overlap: ${s.overlap === "queue" ? "queue one" : "skip"}<br>Missed: ${s.missed === "run_once" ? "run once" : "skip"}<br>Max ${esc(s.max_runtime_min)} min${s.report?.pdf ? " · PDF" : ""}${s.notify?.email?.enabled ? " · email" : ""}</td>
      <td>${s.active_run ? `${pill(s.active_run.state)} <a href="#/perf-live?run=${esc(s.active_run.run_id)}">live</a>` : s.last ? `${pill(s.last.state || s.last.status)}${s.last.verdict ? ` <span class="muted">${esc(VERDICT[s.last.verdict] || "")}</span>` : ""}` : '<span class="muted">-</span>'}</td>
      <td class="row" style="gap:6px;flex-wrap:wrap"><button class="btn" data-act="run">Run now</button><button class="btn" data-act="edit">Edit</button>
        <button class="btn" data-act="${s.enabled ? "pause" : "resume"}">${s.enabled ? "Pause" : "Resume"}</button><button class="btn" data-act="jobs">History</button>
        <button class="btn danger" data-act="delete">Delete</button></td></tr>`).join("")}</tbody></table></div>`;
}

function jobsHtml(jobs) {
  if (!jobs.length) return '<p class="muted">Nothing has run yet.</p>';
  return `<div class="data-grid"><table class="data"><thead><tr><th>Due</th><th>How</th><th>Outcome</th><th>Run</th><th>Why</th></tr></thead><tbody>
    ${jobs.map((j) => `<tr><td>${esc(j.due_at ? dateTimeOf(j.due_at) : "")}</td><td>${esc(KIND[j.kind] || j.kind)}</td>
      <td>${pill(j.run_state || j.status)}${j.run_verdict ? ` <span class="muted">${esc(VERDICT[j.run_verdict] || "")}</span>` : ""}</td>
      <td>${j.run_id ? `<a href="#/perf-results?run=${esc(j.run_id)}">${esc(j.run_id)}</a>` : ""}</td>
      <td style="max-width:460px">${esc(j.reason || "")}</td></tr>`).join("")}</tbody></table></div>`;
}

async function mount(view) {
  view.innerHTML = '<p class="muted">Loading…</p>';
  const [scen, email] = await Promise.all([api("/api/performance/scenarios"), api("/api/email/settings").catch(() => ({ email: {} }))]);
  const scenarios = scen.items || [];
  const emailReady = !!(email.email || {}).configured;
  let data = await api("/api/performance/schedules");
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  view.innerHTML = `<div id="host"></div>
    <div class="card" style="margin-top:12px"><div class="row" style="gap:8px"><h3 style="margin:0">Performance schedules</h3><span class="spacer"></span>
      <button class="btn primary" id="newSch" ${scenarios.length ? "" : "disabled"}>New schedule</button></div>
      <div id="list" style="margin-top:10px"></div></div>
    <div class="card" style="margin-top:12px"><h3 style="margin-top:0">Recent scheduled runs</h3><div id="jobs"><p class="muted">Loading…</p></div></div>`;

  const editor = (current) => {
    const m = openModal(current.id ? `Edit ${current.name}` : "New performance schedule", form(current, scenarios, data.host, emailReady), { wide: true });
    const f = m.el.querySelector("#psForm");
    wire(f);
    f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        const body = read(f);
        const r = current.id ? await api(`/api/performance/schedules/${encodeURIComponent(current.id)}`, { method: "PUT", body })
          : await api("/api/performance/schedules", { method: "POST", body });
        m.close();
        toast(r.schedule.next_run_local ? `Saved. Next run: ${r.schedule.next_run_local}` : "Saved.", "success");
        await refresh();
      } catch (err) { toast(err.message, "error", 8000); }
    });
  };

  const render = () => {
    view.querySelector("#host").innerHTML = hostCard(data.host);
    view.querySelector("#list").innerHTML = listHtml(data.items);
    view.querySelector("#startup")?.addEventListener("change", async (e) => {
      try {
        const r = await api("/api/performance/startup", { method: "PUT", body: { enabled: e.target.checked } });
        toast(r.startup.enabled ? "QA Enterprise AI now starts when you sign in to Windows." : "It no longer starts at sign-in.", "success");
      } catch (err) { e.target.checked = !e.target.checked; toast(err.message, "error", 8000); }
    });
    view.querySelectorAll("#list [data-act]").forEach((b) => b.addEventListener("click", async () => {
      const id = b.closest("tr").dataset.id;
      const s = data.items.find((x) => x.id === id);
      const path = `/api/performance/schedules/${encodeURIComponent(id)}`;
      try {
        if (b.dataset.act === "edit") return editor(s);
        if (b.dataset.act === "jobs") {
          const r = await api(path);
          return openModal(`History: ${s.name}`, jobsHtml(r.jobs), { wide: true });
        }
        if (b.dataset.act === "delete") {
          if (!(await confirmDelete({ name: s.name, title: "Delete schedule?", body: "The schedule stops; its runs and their results stay in Performance Results." }))) return;
          await api(path, { method: "DELETE" });
          toast("Schedule deleted.", "success");
        } else if (b.dataset.act === "run") {
          const r = await api(`${path}/run`, { method: "POST", body: {} });
          toast(`Queued ${r.run_id}.`, "success");
        } else {
          const r = await api(`${path}/${b.dataset.act}`, { method: "POST", body: {} });
          toast(r.schedule.enabled ? `Resumed. Next run: ${r.schedule.next_run_local || "none"}` : "Paused. Nothing runs until it is resumed.", "success");
        }
        await refresh();
      } catch (err) { toast(err.message, "error", 8000); }
    }));
  };

  const refresh = async () => {
    data = await api("/api/performance/schedules");
    render();
    const jobs = await api("/api/performance/schedules/jobs?limit=30");
    view.querySelector("#jobs").innerHTML = jobsHtml(jobs.items.map((j) => ({ ...j, reason: j.schedule_name ? `${j.schedule_name}${j.reason ? ": " + j.reason : ""}` : j.reason })));
  };

  view.querySelector("#newSch").addEventListener("click", () => editor({ scenario_id: params.get("scenario") || scenarios[0]?.id }));
  await refresh();
  if (params.get("scenario") && scenarios.some((x) => x.id === params.get("scenario"))) editor({ scenario_id: params.get("scenario") });
  timer = setInterval(() => { if (!document.querySelector(".modal-backdrop")) refresh().catch(() => {}); }, 15000);
}

export const perfSchedules = {
  async mount(view) { clearInterval(timer); await mount(view); },
  unmount() { clearInterval(timer); },
};
