// 23.3.17: date / time rules for test values, shared by Guided Browser Automation > Edit Script and the
// AI-Driven Test Data tab. A rule such as {{TODAY+2d:%d/%m/%Y}} or {{NOW-1h-30min:%H:%M}} is worked out from the
// system clock of the PC that runs the test, at the moment it runs, so a script recorded on 3 Nov still enters
// a valid date on 5 Nov.

import { esc } from "./util.js";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n, w = 2) => String(n).padStart(w, "0");
const RULE = /\{\{\s*(TODAY|DATE|NOW)((?:\s*[+-]\s*\d+\s*(?:min|[dwmyh]))*)\s*(?::([^}]*))?\}\}/g;
const OFFSET = /([+-])\s*(\d+)\s*(min|[dwmyh])/g;

export const DATE_FORMATS = [["%d/%m/%Y", "dd/mm/yyyy"], ["%m/%d/%Y", "mm/dd/yyyy"], ["%Y-%m-%d", "yyyy-mm-dd"],
  ["%d-%m-%Y", "dd-mm-yyyy"], ["%d.%m.%Y", "dd.mm.yyyy"], ["%d-%b-%Y", "dd-Mon-yyyy"], ["%d %b %Y", "dd Mon yyyy"],
  ["%b %d, %Y", "Mon dd, yyyy"]];
export const TIME_FORMATS = [["%H:%M", "HH:MM (24 h)"], ["%H:%M:%S", "HH:MM:SS"], ["%I:%M %p", "hh:MM AM/PM"]];
export const DATETIME_FORMATS = [["%d/%m/%Y %H:%M", "dd/mm/yyyy HH:MM"], ["%Y-%m-%dT%H:%M", "yyyy-mm-ddTHH:MM (date-time field)"],
  ["%Y-%m-%d %H:%M", "yyyy-mm-dd HH:MM"], ["%m/%d/%Y %I:%M %p", "mm/dd/yyyy hh:MM AM/PM"]];

export function strftime(d, fmt) {
  return fmt.replace(/%([dmYyHIMSpbB%])/g, (_, k) => ({ d: pad(d.getDate()), m: pad(d.getMonth() + 1), Y: d.getFullYear(),
    y: pad(d.getFullYear() % 100), H: pad(d.getHours()), I: pad(d.getHours() % 12 || 12), M: pad(d.getMinutes()),
    S: pad(d.getSeconds()), p: d.getHours() < 12 ? "AM" : "PM", b: MONTHS[d.getMonth()],
    B: d.toLocaleString("en", { month: "long" }), "%": "%" })[k]);
}

function shifted(offs) {
  const d = new Date();
  for (const [, sign, n, unit] of String(offs || "").matchAll(OFFSET)) {
    const v = Number(n) * (sign === "-" ? -1 : 1);
    if (unit === "d") d.setDate(d.getDate() + v);
    else if (unit === "w") d.setDate(d.getDate() + 7 * v);
    else if (unit === "m") d.setMonth(d.getMonth() + v);
    else if (unit === "y") d.setFullYear(d.getFullYear() + v);
    else if (unit === "h") d.setHours(d.getHours() + v);
    else d.setMinutes(d.getMinutes() + v);
  }
  return d;
}

// What a value gives now (rules replaced, everything else kept).
export function previewRule(text) {
  return String(text ?? "").replace(RULE, (_, kind, offs, fmt) => strftime(shifted(offs), fmt || (kind === "NOW" ? "%Y-%m-%d %H:%M" : "%Y-%m-%d")));
}

export function hasRule(text) {
  RULE.lastIndex = 0;
  const found = RULE.test(String(text ?? ""));
  RULE.lastIndex = 0;
  return found;
}

const WORDS = { d: ["day", "days"], w: ["week", "weeks"], m: ["month", "months"], y: ["year", "years"], h: ["hour", "hours"], min: ["minute", "minutes"] };

// "Current system date + 2 days" for {{TODAY+2d:%d/%m/%Y}}; null when the value is not one rule.
export function ruleLabel(text) {
  const m = /^\s*\{\{\s*(TODAY|DATE|NOW)((?:\s*[+-]\s*\d+\s*(?:min|[dwmyh]))*)\s*(?::([^}]*))?\}\}\s*$/.exec(String(text ?? ""));
  if (!m) return null;
  const withDate = m[1] !== "NOW" || /%[dYm]/.test(m[3] || "");
  let out = m[1] === "NOW" ? (withDate && m[3] ? "Current system date and time" : "Current system time") : "Current system date";
  for (const [, sign, n, unit] of (m[2] || "").matchAll(OFFSET)) out += ` ${sign} ${n} ${WORDS[unit][n === "1" ? 0 : 1]}`;
  return out;
}

function parse(rule) {
  const m = /^\s*\{\{\s*(TODAY|DATE|NOW)((?:\s*[+-]\s*\d+\s*(?:min|[dwmyh]))*)\s*(?::([^}]*))?\}\}\s*$/.exec(String(rule ?? ""));
  if (!m) return null;
  const out = { kind: m[1] === "NOW" ? "time" : "date", sign: "", d: 0, h: 0, min: 0, m: 0, fmt: m[3] || "" };
  for (const [, sign, n, unit] of (m[2] || "").matchAll(OFFSET)) {
    out.sign = out.sign || sign;
    const v = Number(n);
    if (unit === "w") out.d += 7 * v; else if (unit === "y") out.m += 12 * v; else out[unit] += v;
  }
  if (out.kind === "time" && /%[dYm]/.test(out.fmt)) out.kind = "datetime";
  return out;
}

const QUICK = [
  ["Today", "date", "", 0, 0, 0], ["Tomorrow", "date", "+", 1, 0, 0], ["Yesterday", "date", "-", 1, 0, 0],
  ["Today + 7 days", "date", "+", 7, 0, 0], ["Now", "time", "", 0, 0, 0], ["Now + 1 hour", "time", "+", 0, 1, 0],
  ["Now + 4 hours", "time", "+", 0, 4, 0], ["Now - 1 hour", "time", "-", 0, 1, 0], ["Now + 30 min", "time", "+", 0, 0, 30],
];

// The Rule dialog. current: the value now in the field; onApply(rule) gets the new value.
export function openRuleBuilder(current, onApply, { title = "Date / time rule", recorded = null } = {}) {
  const el = document.createElement("div");
  el.className = "modal-backdrop";
  const opts = (list) => list.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
  el.innerHTML = `<div class="modal card" role="dialog" aria-modal="true" aria-label="${esc(title)}" style="width:min(640px,100%)">
    <div class="row"><h3 style="margin:0">${esc(title)}</h3><span class="spacer"></span><button class="icon-btn" data-close aria-label="Close">✕</button></div>
    <div class="modal-body">
      <p class="muted">The value is worked out from the system clock of the PC that runs the test, at the moment it runs.
        ${recorded ? `Recorded value: <strong>${esc(recorded)}</strong>.` : ""}</p>
      <div class="row" style="flex-wrap:wrap;gap:6px;margin-bottom:10px">${QUICK.map((q, i) => `<button class="btn small" data-quick="${i}" type="button">${esc(q[0])}</button>`).join("")}</div>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr))">
        <label>Value <select name="kind"><option value="date">Current system date</option><option value="time">Current system time</option>
          <option value="datetime">Current system date and time</option></select></label>
        <label>Change <select name="sign"><option value="">No change (now)</option><option value="+">Plus (future)</option><option value="-">Minus (past)</option></select></label>
        <label data-for="d">Days <input name="d" type="number" min="0" max="3650" value="0"></label>
        <label data-for="m">Months <input name="m" type="number" min="0" max="240" value="0"></label>
        <label data-for="h">Hours <input name="h" type="number" min="0" max="999" value="0"></label>
        <label data-for="min">Minutes <input name="min" type="number" min="0" max="5999" value="0"></label>
        <label>Format <select name="fmt"></select></label>
        <label>Custom format <input name="custom" placeholder="%d.%m.%Y"></label>
      </div>
      <p style="margin-top:10px"><span id="ruleLabel"></span><br>Rule <code id="rule"></code> → now this gives <strong id="preview"></strong></p>
      <p class="muted small">Format codes: %d day, %m month, %Y year, %b month name, %H hour (24 h), %I hour (12 h), %M minutes, %p AM/PM.</p>
      <div class="row"><span class="spacer"></span><button class="btn" data-close type="button">Cancel</button>
        <button class="btn primary" id="apply" type="button">Use this rule</button></div>
    </div></div>`;
  const f = (n) => el.querySelector(`[name=${n}]`);
  const formatsFor = (kind) => (kind === "time" ? TIME_FORMATS : kind === "datetime" ? DATETIME_FORMATS : DATE_FORMATS);
  const fillFormats = (kind, keep) => {
    const list = formatsFor(kind);
    f("fmt").innerHTML = opts(list) + '<option value="custom">Custom…</option>';
    if (keep && list.some(([v]) => v === keep)) f("fmt").value = keep;
    else if (keep) { f("fmt").value = "custom"; f("custom").value = keep; }
  };
  const showFields = () => {
    const kind = f("kind").value, change = !!f("sign").value;
    const show = { d: change && kind !== "time", m: change && kind === "date", h: change && kind !== "date", min: change && kind !== "date" };
    for (const k of Object.keys(show)) el.querySelector(`[data-for="${k}"]`).hidden = !show[k];
    el.querySelector('[name="custom"]').closest("label").hidden = f("fmt").value !== "custom";
  };
  const rule = () => {
    const kind = f("kind").value, sign = f("sign").value;
    const fmt = f("fmt").value === "custom" ? f("custom").value.trim() : f("fmt").value;
    let offs = "";
    if (sign) {
      const parts = kind === "date" ? [["m", "m"], ["d", "d"]] : kind === "time" ? [["h", "h"], ["min", "min"]] : [["d", "d"], ["h", "h"], ["min", "min"]];
      for (const [field, unit] of parts) { const n = Math.max(0, Math.floor(Number(f(field).value) || 0)); if (n) offs += `${sign}${n}${unit}`; }
    }
    return `{{${kind === "date" ? "TODAY" : "NOW"}${offs}${fmt ? `:${fmt}` : ""}}}`;
  };
  const show = () => {
    showFields();
    const r = rule();
    el.querySelector("#rule").textContent = r;
    el.querySelector("#preview").textContent = previewRule(r);
    el.querySelector("#ruleLabel").textContent = ruleLabel(r) || "";
  };
  const set = (p) => {
    f("kind").value = p.kind; f("sign").value = p.sign;
    for (const k of ["d", "m", "h", "min"]) f(k).value = p[k] || 0;
    fillFormats(p.kind, p.fmt || formatsFor(p.kind)[0][0]);
    show();
  };
  const parsed = parse(current);
  set(parsed || { kind: /:\d\d/.test(String(recorded || current || "")) && !/\d{4}/.test(String(recorded || current || "")) ? "time" : "date",
    sign: "", d: 0, m: 0, h: 0, min: 0, fmt: "" });
  f("kind").addEventListener("change", () => { fillFormats(f("kind").value); show(); });
  el.querySelectorAll("select,input").forEach((x) => x.addEventListener("input", show));
  el.querySelectorAll("[data-quick]").forEach((b) => b.addEventListener("click", () => {
    const [, kind, sign, d, h, min] = QUICK[Number(b.dataset.quick)];
    const fmt = f("kind").value === kind || (kind === "date" && f("kind").value === "date") ? (f("fmt").value === "custom" ? f("custom").value : f("fmt").value) : "";
    set({ kind, sign, d, h, min, m: 0, fmt });
  }));
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const close = () => { if (!el.isConnected) return; el.remove(); document.removeEventListener("keydown", onKey); };
  el.addEventListener("click", (e) => { if (e.target === el || e.target.closest("[data-close]")) close(); });
  document.addEventListener("keydown", onKey);
  el.querySelector("#apply").addEventListener("click", () => { onApply(rule()); close(); });
  document.body.appendChild(el);
  return { el, close };
}
