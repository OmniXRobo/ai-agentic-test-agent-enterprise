// Calendar helper for the pick_date action: finds the open date picker, reads the month it shows, and marks the
// element to click next (previous/next month or the day) with data-qa-cal="target". The test runner then moves the
// visual cursor to that element and clicks it with Playwright, so the date is chosen exactly as a person would.
// Read-only apart from that one marker attribute; it never changes the application's own behaviour.
(() => {
  if (window.__qaCal) return;
  const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
  const ROOTS = [
    ".flatpickr-calendar.open", ".react-datepicker", ".MuiDateCalendar-root", ".MuiPickersLayout-root", ".mat-calendar",
    ".ui-datepicker", ".datepicker-dropdown", ".datepicker", ".pika-single:not(.is-hidden)", ".daterangepicker",
    ".k-calendar", ".ant-picker-dropdown:not(.ant-picker-dropdown-hidden)", ".vdp-datepicker__calendar", ".air-datepicker",
    "[role=dialog] [role=grid]", "[role=grid]", "table.calendar", ".calendar",
  ];
  const OUTSIDE = /(outside|other-month|othermonth|adjacent|prev-month|next-month|\bold\b|\bnew\b|disabled|unselectable|--outside)/i;

  const visible = (el) => {
    if (!el || !el.getClientRects().length) return false;
    const s = getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none" && Number(s.opacity) !== 0;
  };

  function root() {
    for (const sel of ROOTS) {
      const found = [...document.querySelectorAll(sel)].filter(visible);
      if (found.length) {
        const el = found[found.length - 1];
        return sel.includes("[role=grid]") ? (el.closest("[role=dialog]") || el.parentElement || el) : el;
      }
    }
    return null;
  }

  // The month and year the calendar shows, from its heading (e.g. "October 2026", "Oct 2026") or select boxes.
  function shown(r) {
    const selects = [...r.querySelectorAll("select")];
    let month = -1, year = 0;
    for (const s of selects) {
      const t = (s.selectedOptions[0]?.textContent || s.value || "").trim().toLowerCase();
      if (/^\d{4}$/.test(t)) year = Number(t);
      else if (MONTHS.includes(t.slice(0, 3))) month = MONTHS.indexOf(t.slice(0, 3));
    }
    const yearInput = r.querySelector("input.cur-year, input.numInput");
    if (yearInput && /^\d{4}$/.test(yearInput.value)) year = Number(yearInput.value);
    const heads = r.querySelectorAll("[aria-live], caption, thead th[colspan], [class*=title], [class*=header], [class*=caption], [class*=month], [class*=label], h2, h3, button, span, div");
    for (const h of heads) {
      if (month >= 0 && year) break;
      if (!visible(h) || h.children.length > 4) continue;
      const t = (h.textContent || "").trim().toLowerCase();
      if (t.length > 40) continue;
      const m = t.match(/([a-z]{3,})\.?\s*,?\s*(\d{4})/);
      if (m && MONTHS.includes(m[1].slice(0, 3))) { month = MONTHS.indexOf(m[1].slice(0, 3)); year = Number(m[2]); break; }
      if (month < 0 && MONTHS.includes(t.slice(0, 3)) && /^[a-z]+$/.test(t)) month = MONTHS.indexOf(t.slice(0, 3));
      if (!year && /^\d{4}$/.test(t)) year = Number(t);
    }
    return month >= 0 && year ? { month, year } : null;
  }

  function clear() { document.querySelectorAll("[data-qa-cal]").forEach((e) => e.removeAttribute("data-qa-cal")); }

  function mark(el) { clear(); el.setAttribute("data-qa-cal", "target"); return true; }

  function nav(r, dir) {
    const words = dir > 0 ? /(next|forward|›|»|>|→)/i : /(prev|previous|back|‹|«|<|←)/i;
    const cands = [...r.querySelectorAll("button, a, [role=button], span, th, div")].filter(visible).filter((e) => {
      const label = `${e.getAttribute("aria-label") || ""} ${e.getAttribute("title") || ""} ${e.className || ""} ${e.children.length ? "" : e.textContent || ""}`;
      return words.test(label) && !/year/i.test(label) && e.children.length <= 2;
    });
    return cands.length ? mark(cands[0]) : false;
  }

  function day(r, y, m, d) {
    const iso = `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const all = [...r.querySelectorAll("td, button, a, [role=gridcell], [role=button], span, div")].filter(visible);
    // Best: an element that names the full date.
    const named = all.find((e) => {
      const a = `${e.getAttribute("data-date") || ""} ${e.getAttribute("data-value") || ""} ${e.getAttribute("aria-label") || ""} ${e.getAttribute("title") || ""}`.toLowerCase();
      return a.includes(iso) || (a.includes(String(y)) && new RegExp(`\\b${d}\\b`).test(a) && a.includes(MONTHS[m]));
    });
    if (named) return mark(named.querySelector("button, a") || named);
    const plain = all.filter((e) => (e.textContent || "").trim() === String(d) && e.querySelectorAll("*").length <= 2
      && !OUTSIDE.test(`${e.className} ${e.parentElement?.className || ""}`) && e.getAttribute("aria-disabled") !== "true");
    return plain.length ? mark(plain[0]) : false;
  }

  window.__qaCal = {
    /** {month (0-11), year} the open calendar shows, or null if no calendar is open / it cannot be read. */
    state() { const r = root(); return r ? shown(r) : null; },
    /** Mark the next step towards the date: "day", "next" or "prev"; returns what was marked, or "" if nothing. */
    step(iso) {
      const r = root();
      if (!r) return "";
      const [y, mo, d] = iso.split("-").map(Number);
      const s = shown(r);
      if (!s) return day(r, y, mo - 1, d) ? "day" : "";
      const diff = (y - s.year) * 12 + (mo - 1 - s.month);
      if (diff === 0) return day(r, y, mo - 1, d) ? "day" : "";
      return nav(r, diff) ? (diff > 0 ? "next" : "prev") : "";
    },
    clear,
  };
})();
