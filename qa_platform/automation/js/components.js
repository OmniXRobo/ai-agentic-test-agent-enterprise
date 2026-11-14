// SkyGuard Universal Web Component Recognition (23.6.0).
//
// Recognises what a control *is* (native select, custom / portal dropdown, autocomplete, calendar day, date-range
// calendar, date field, tab, switch, slider, container ...) from its role, ARIA state, structure and text, never from
// an id or a generated class alone. Used by Guided Browser Automation playback (qa_platform/automation/runner.py),
// the recorder (semantic steps) and the Operations Agent's Playwright driver, so all three read pages the same way.
//
// It only reads the page. The single thing it writes is a marker attribute (data-sg-target="1") on the element the
// Python side should click next with Playwright's trusted input, so every click is a real, user-like click.
(() => {
  if (window.__sgComp) return;
  const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
  const MONTH_RE = "(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)";
  const clean = (s, n = 200) => String(s || "").replace(/\s+/g, " ").trim().slice(0, n);
  const norm = (s) => clean(s, 400).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  const cls = (n) => (n && n.getAttribute && (n.getAttribute("class") || "")) || "";

  function visible(el) {
    if (!el || !el.isConnected || !el.getClientRects || !el.getClientRects().length) return false;
    const s = getComputedStyle(el);
    if (s.visibility === "hidden" || s.display === "none" || Number(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  // ---- dates ---------------------------------------------------------------------------------------------------
  const pad = (n) => String(n).padStart(2, "0");
  const iso = (y, m, d) => {
    const dt = new Date(y, m - 1, d);
    if (dt.getFullYear() !== y || dt.getMonth() !== m - 1 || dt.getDate() !== d) return "";
    return `${y}-${pad(m)}-${pad(d)}`;
  };
  const monthIndex = (word) => MONTHS.indexOf(String(word).toLowerCase().slice(0, 3)) + 1;

  /** A full date written in a label or attribute, as YYYY-MM-DD, or "" (never guesses day/month order). */
  function parseDate(raw) {
    const s = String(raw || "").trim();
    if (!s) return "";
    let m = s.match(/^\d{12,13}$/);
    if (m) { const d = new Date(Number(s)); return iso(d.getFullYear(), d.getMonth() + 1, d.getDate()); }
    m = s.match(/\b(\d{4})-(\d{1,2})-(\d{1,2})\b/) || s.match(/\b(\d{4})\/(\d{1,2})\/(\d{1,2})\b/) || s.match(/\b(\d{4})\.(\d{1,2})\.(\d{1,2})\b/);
    if (m) return iso(Number(m[1]), Number(m[2]), Number(m[3]));
    m = s.match(/^(20\d{2}|19\d{2})(\d{2})(\d{2})$/);
    if (m) return iso(Number(m[1]), Number(m[2]), Number(m[3]));
    // "Sunday, November 15, 2026", "Sun Nov 15 2026", "November 15th, 2026"
    m = s.match(new RegExp(`\\b${MONTH_RE}\\.?\\s+(\\d{1,2})(?:st|nd|rd|th)?,?\\s+(\\d{4})\\b`, "i"));
    if (m) return iso(Number(m[3]), monthIndex(m[1]), Number(m[2]));
    // "15 November 2026", "15 Nov, 2026"
    m = s.match(new RegExp(`\\b(\\d{1,2})(?:st|nd|rd|th)?\\s+${MONTH_RE}\\.?,?\\s+(\\d{4})\\b`, "i"));
    if (m) return iso(Number(m[3]), monthIndex(m[2]), Number(m[1]));
    return "";
  }

  // The month and year a heading names ("November 2026", "Nov 2026", "2026年11月" is not handled: no guessing).
  function monthYear(text) {
    const t = clean(text, 60);
    let m = t.match(new RegExp(`^${MONTH_RE}\\.?,?\\s+(\\d{4})$`, "i"));
    if (m) return { month: monthIndex(m[1]), year: Number(m[2]) };
    m = t.match(new RegExp(`^(\\d{4})\\s+${MONTH_RE}\\.?$`, "i"));
    if (m) return { month: monthIndex(m[2]), year: Number(m[1]) };
    m = t.match(/^(\d{4})[-/.](\d{1,2})$/);
    if (m && Number(m[2]) >= 1 && Number(m[2]) <= 12) return { month: Number(m[2]), year: Number(m[1]) };
    return null;
  }

  const DATE_ATTRS = ["data-date", "data-day", "data-iso", "data-value", "datetime", "data-time", "data-full-date", "data-testid", "aria-label", "title"];

  function attrDate(el) {
    if (!el || !el.getAttribute) return "";
    for (const a of DATE_ATTRS) {
      const v = el.getAttribute(a);
      if (!v) continue;
      // an aria-label / title must name a full date (with a year); short attribute values may be ISO or epoch
      const d = parseDate(v);
      if (d) return d;
    }
    return "";
  }

  function dayNumber(el) {
    const first = String(el.innerText || el.textContent || "").trim().split(/\n/)[0].trim();
    return /^\d{1,2}$/.test(first) && Number(first) >= 1 && Number(first) <= 31 ? Number(first) : 0;
  }

  const OUTSIDE = /(outside|other-?month|othermonth|adjacent|prev-?month|next-?month|--outside|is-other|not-?current|\bold\b|\bnew\b)/i;
  const DISABLED = /(disabled|is-disable\b|\bdisable\b|[-_]dis\b|unavailable|blocked|is-past|--past|\bpast\b|invalid|unselectable|not-?allowed|sold-?out|soldout)/i;
  const SELECTED = /(selected|range-?start|range-?end|start-?date|end-?date|is-start|is-end|checked|--start|--end|\bactive\b|is-active)/i;
  const around = (el, n = 2) => { const out = [el]; let p = el; for (let i = 0; i < n && p.parentElement; i++) { p = p.parentElement; out.push(p); } return out; };
  const outside = (el) => around(el, 1).some((n) => OUTSIDE.test(cls(n))) || el.getAttribute("aria-hidden") === "true";
  function disabled(el) {
    const nodes = [el, ...el.querySelectorAll("button,[role=button],[aria-disabled]")].slice(0, 4);
    return nodes.some((n) => n.disabled || n.getAttribute("aria-disabled") === "true" || DISABLED.test(cls(n)))
      // 23.6.1: the state can sit on the cell around the clicked text (Trip.com: li.is-disable > div > span.day)
      || around(el, 3).slice(1).some((p) => p.getAttribute("aria-disabled") === "true" || (DISABLED.test(cls(p)) && p.getBoundingClientRect().height < 120))
      || getComputedStyle(el).pointerEvents === "none";
  }
  function selected(el) {
    return around(el, 2).concat([...el.querySelectorAll("*")].slice(0, 6)).some((n) => n.getAttribute && (
      n.getAttribute("aria-selected") === "true" || n.getAttribute("aria-pressed") === "true"
      || n.getAttribute("aria-checked") === "true" || SELECTED.test(cls(n))));
  }

  // Elements a calendar uses for its days (cells, buttons, divs inside grids).
  const CELL_SEL = "td,[role=gridcell],[role=button],[role=checkbox],[role=option],button,a,div,span,li";

  /** Day cells currently visible, each with its full date: from its own attributes, or its number + panel heading. */
  function dayCells() {
    const out = [];
    const seen = new Set();
    // A. cells that name their full date (aria-label="Sunday, November 15, 2026", data-date="2026-11-15", ...)
    for (const el of document.querySelectorAll(DATE_ATTRS.map((a) => `[${a}]`).join(","))) {
      if (out.length > 1500) break;
      const d = attrDate(el);
      if (!d || !visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width > 220 || r.height > 160) continue; // a whole field or banner, not a day cell
      // keep the outermost dated element (cell), not its inner span with the same date
      let top = el;
      for (let p = el.parentElement, i = 0; p && i < 3; p = p.parentElement, i++) if (attrDate(p) === d && visible(p)) top = p;
      if (seen.has(top)) continue;
      seen.add(top);
      out.push({ el: top, iso: d, source: "attribute" });
    }
    // B. calendars with day numbers only: the month heading gives month and year
    const heads = [...document.querySelectorAll("h1,h2,h3,h4,h5,h6,caption,th,button,strong,b,div,span,[aria-live],[role=heading]")]
      .filter((h) => h.children.length <= 3 && monthYear(h.innerText || h.textContent) && visible(h));
    for (const h of heads.slice(0, 24)) {
      const my = monthYear(h.innerText || h.textContent);
      // the panel: the smallest ancestor that holds 28+ day numbers
      let panel = h.parentElement;
      for (let i = 0; panel && i < 6; i++, panel = panel.parentElement) {
        const n = [...panel.querySelectorAll(CELL_SEL)].filter((c) => !c.querySelector(CELL_SEL.replace(/,div,span,li$/, "")) && dayNumber(c)).length;
        if (n >= 28) break;
      }
      if (!panel) continue;
      const otherHeads = heads.filter((x) => x !== h && panel.contains(x));
      if (otherHeads.length) continue; // two months in one container: each heading is handled by its own panel
      for (const c of panel.querySelectorAll(CELL_SEL)) {
        const day = dayNumber(c);
        if (!day || seen.has(c) || !visible(c)) continue;
        if ([...c.children].some((k) => dayNumber(k) === day && k.matches(CELL_SEL))) continue; // the inner element is the cell
        // 23.6.1: a week row or the month's day grid starts with "1" too (Trip.com: ul > li > div > span.day); a cell
        // holds one day only
        const cr = c.getBoundingClientRect();
        if (cr.height > 120 || cr.width > 160 || [...c.querySelectorAll(CELL_SEL)].some((k) => dayNumber(k) && dayNumber(k) !== day)) continue;
        if (outside(c)) continue;
        const d = iso(my.year, my.month, day);
        if (!d) continue;
        seen.add(c);
        out.push({ el: c, iso: d, source: "panel" });
      }
    }
    return out;
  }

  function clear() { document.querySelectorAll("[data-sg-target]").forEach((e) => e.removeAttribute("data-sg-target")); }
  function mark(el) { clear(); el.setAttribute("data-sg-target", "1"); return true; }

  const NEXT = /(\bnext\b|forward|›|»|→|chevron-?right|arrow-?right|caret-?right|angle-?right|icon-?right|right-?arrow|\bnext-?month)/i;
  const PREV = /(\bprev\b|previous|back\b|‹|«|←|chevron-?left|arrow-?left|caret-?left|angle-?left|icon-?left|left-?arrow|prev-?month)/i;

  function navControl(dir, cells) {
    const rects = cells.map((c) => c.el.getBoundingClientRect());
    const box = rects.reduce((a, r) => ({ l: Math.min(a.l, r.left), t: Math.min(a.t, r.top), r: Math.max(a.r, r.right), b: Math.max(a.b, r.bottom) }),
      { l: 1e9, t: 1e9, r: -1e9, b: -1e9 });
    const words = dir > 0 ? NEXT : PREV;
    const cands = [...document.querySelectorAll("button,a,[role=button],span,i,div,svg")].filter(visible).filter((e) => {
      if (e.children.length > 3) return false;
      const icon = e.querySelector("i,svg,span");
      const label = `${e.getAttribute("aria-label") || ""} ${e.getAttribute("title") || ""} ${cls(e)} ${icon ? cls(icon) : ""} ${e.getAttribute("data-testid") || ""} ${e.children.length ? "" : (e.textContent || "").trim()}`;
      if (!words.test(label) || /year/i.test(label)) return false;
      if (disabled(e)) return false;
      const r = e.getBoundingClientRect();
      return r.width < 120 && r.height < 400 && r.right > box.l - 120 && r.left < box.r + 120 && r.bottom > box.t - 160 && r.top < box.b + 40;
    });
    if (!cands.length) return null;
    // the control is usually above the grid; the closest to the grid's top edge wins
    cands.sort((a, b) => Math.abs(a.getBoundingClientRect().top - box.t) - Math.abs(b.getBoundingClientRect().top - box.t));
    const c = cands[0];
    return c.closest("button,a,[role=button]") || c;
  }

  /** Mark the next element to click to choose ``want`` (YYYY-MM-DD): the day, or the next / previous month. */
  function findDay(want) {
    clear();
    const cells = dayCells();
    if (!cells.length) return { status: "none" };
    const hits = cells.filter((c) => c.iso === want);
    if (hits.length) {
      const usable = hits.filter((c) => !disabled(c.el));
      if (!usable.length) return { status: "disabled", label: clean(hits[0].el.getAttribute("aria-label") || hits[0].el.innerText, 120) };
      // the same date can be drawn twice (an animation copy): the one in view wins
      usable.sort((a, b) => Math.abs(a.el.getBoundingClientRect().top) - Math.abs(b.el.getBoundingClientRect().top));
      const el = usable[0].el;
      mark(el.matches("td") && el.querySelector("button,a,[role=button]") ? el.querySelector("button,a,[role=button]") : el);
      return { status: "day", source: usable[0].source, panels: months(cells) };
    }
    const ms = months(cells);
    const [y, m] = want.split("-").map(Number);
    const t = y * 12 + (m - 1);
    const min = Math.min(...ms.map((x) => x.year * 12 + x.month - 1));
    const max = Math.max(...ms.map((x) => x.year * 12 + x.month - 1));
    if (t >= min && t <= max) return { status: "missing", panels: ms };
    const dir = t > max ? 1 : -1;
    const nav = navControl(dir, cells);
    if (!nav) return { status: "no_nav", direction: dir > 0 ? "next" : "prev", panels: ms };
    mark(nav);
    return { status: dir > 0 ? "next" : "prev", panels: ms };
  }

  function months(cells) {
    const keys = new Map();
    for (const c of cells) {
      const [y, m] = c.iso.split("-").map(Number);
      const k = `${y}-${m}`;
      keys.set(k, (keys.get(k) || 0) + 1);
    }
    // a month counts as shown when most of its days are visible (adjacent-month spill-over is ignored)
    return [...keys.entries()].filter(([, n]) => n >= 20 || keys.size === 1).map(([k]) => { const [y, m] = k.split("-").map(Number); return { year: y, month: m }; });
  }

  /** Is the day ``want`` shown as chosen in an open calendar (or, after it closed, does a field show it)? */
  function daySelected(want) {
    const hit = dayCells().find((c) => c.iso === want);
    if (hit) return { open: true, selected: selected(hit.el) };
    return { open: false, selected: false };
  }

  /** Text that shows a date in any common way, for checking a date field after a choice. */
  function showsDate(text, want) {
    const [y, m, d] = want.split("-").map(Number);
    const t = String(text || "").toLowerCase();
    if (!t) return false;
    if (parseDate(t) === want) return true;
    const mon = MONTHS[m - 1];
    const dRe = new RegExp(`(^|[^0-9])0?${d}([^0-9]|$)`);
    if (t.includes(mon) && dRe.test(t)) return true;
    const nums = [`${y}-${pad(m)}-${pad(d)}`, `${pad(d)}/${pad(m)}/${y}`, `${pad(m)}/${pad(d)}/${y}`, `${d}/${m}/${y}`, `${m}/${d}/${y}`, `${pad(d)}.${pad(m)}.${y}`, `${pad(d)}-${pad(m)}-${y}`];
    return nums.some((n) => t.includes(n));
  }

  // ---- date fields (check-in / check-out, departure / return) ---------------------------------------------------
  const DATEISH = /(check.?in|check.?out|depart|departure|return|arrival date|start date|end date|from date|to date|\bdate\b|\bdates\b|calendar|when\b|travel date|dob|birth)/i;
  const START = /(check.?in|depart|departure|start|from|outbound|begin)/i;
  const END = /(check.?out|return|end\b|end date|until|inbound|to date)/i;

  function labelText(el) {
    const bits = [el.getAttribute("aria-label"), el.getAttribute("placeholder"), el.getAttribute("title"), el.getAttribute("name"),
      el.getAttribute("data-testid"), el.labels && el.labels.length ? el.labels[0].textContent : "", clean(el.innerText, 80)];
    let p = el.parentElement;
    for (let i = 0; p && i < 2; i++, p = p.parentElement) { const lab = p.querySelector("label"); if (lab) bits.push(lab.textContent); }
    return clean(bits.filter(Boolean).join(" "), 300);
  }

  function dateFields() {
    const out = [];
    for (const el of document.querySelectorAll("input,button,[role=button],[role=combobox],[tabindex],div,span,label")) {
      if (out.length > 30) break;
      if (!visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width > 520 || r.height > 160 || r.width < 20) continue;
      const tag = el.tagName.toLowerCase();
      if (["div", "span", "label"].includes(tag) && el.children.length > 6) continue;
      const native = tag === "input" && ["date", "datetime-local"].includes(el.type);
      const text = labelText(el);
      if (!native && !DATEISH.test(text)) continue;
      if (["div", "span", "label"].includes(tag) && getComputedStyle(el).cursor !== "pointer" && !el.querySelector("input")) continue;
      const role = START.test(text) && !END.test(text) ? "start" : END.test(text) && !START.test(text) ? "end" : "";
      out.push({ el, role, text: clean(text, 120), native });
    }
    // the innermost of nested matches
    return out.filter((a) => !out.some((b) => b !== a && a.el.contains(b.el) && b.role === a.role));
  }

  function markDateField(role) {
    clear();
    const fields = dateFields();
    const pick = fields.filter((f) => !role || f.role === role);
    const pool = pick.length ? pick : (role ? [] : fields);
    if (pool.length === 1 || (pool.length > 1 && role)) { mark(pool[0].el); return { status: "found", text: pool[0].text, count: pool.length }; }
    return { status: pool.length ? "ambiguous" : "none", fields: fields.map((f) => f.text).slice(0, 8) };
  }

  // ---- dropdowns, autocompletes, menus --------------------------------------------------------------------------
  const POPUP_SEL = [
    "[role=listbox]", "[role=menu]", "[role=tree]", "[role=grid][aria-label*=suggest i]", ".ant-select-dropdown", ".ant-cascader-dropdown",
    ".MuiPopover-paper", ".MuiMenu-paper", ".MuiAutocomplete-popper", ".MuiAutocomplete-listbox", ".select2-results", ".select2-dropdown",
    ".dropdown-menu.show", ".dropdown-menu[style*=block]", ".v-menu__content", ".v-select-list", ".ng-dropdown-panel", ".p-dropdown-panel",
    ".chosen-drop", ".choices__list--dropdown", "[class*=menu-list i]", "[class*=MenuList]", "[class*=suggest i]", "[class*=autocomplete i]",
    "[class*=typeahead i]", "[class*=dropdown i]", "[class*=popover i]", "[class*=listbox i]", "[class*=options i]", "[id*=autowhatever i]", "[class*=react-autosuggest]",
  ].join(",");
  const OPTION_SEL = "[role=option],[role=menuitem],[role=menuitemradio],[role=menuitemcheckbox],[role=treeitem],li,.dropdown-item,[class*=option i],[class*=item i],[class*=suggestion i],[class*=result i]";

  function popups() {
    const roots = [...document.querySelectorAll(POPUP_SEL)].filter(visible);
    // innermost containers that actually hold options
    const withOpts = roots.filter((r) => optionsOf(r).length);
    return withOpts.filter((r) => !withOpts.some((o) => o !== r && r.contains(o) && optionsOf(o).length === optionsOf(r).length));
  }

  function optionsOf(root) {
    const all = [...root.querySelectorAll(OPTION_SEL)].filter(visible);
    const leaves = all.filter((o) => !all.some((x) => x !== o && o.contains(x) && clean(x.innerText) === clean(o.innerText)));
    const roleOpts = leaves.filter((o) => /option|menuitem|treeitem/.test(o.getAttribute("role") || ""));
    const pool = roleOpts.length ? roleOpts : leaves;
    return pool.filter((o) => clean(o.innerText || o.textContent) && !o.matches("input,select,textarea") && o.getBoundingClientRect().height < 200
      && !/(loading|spinner|skeleton|no-?options|empty)/i.test(cls(o)) && o.getAttribute("aria-busy") !== "true"
      && !/^(loading|searching)\b/i.test(clean(o.innerText || o.textContent)));
  }

  function linkedPopup(field) {
    if (!field) return null;
    const ids = `${field.getAttribute("aria-controls") || ""} ${field.getAttribute("aria-owns") || ""}`.split(/\s+/).filter(Boolean);
    for (const id of ids) {
      const n = document.getElementById(id);
      if (n && visible(n) && optionsOf(n).length) return n;
    }
    const active = field.getAttribute("aria-activedescendant");
    if (active) { const a = document.getElementById(active); const r = a && a.closest(POPUP_SEL); if (r && visible(r)) return r; }
    // otherwise the popup closest to the field (portals render far away in the DOM but near on screen)
    const fr = field.getBoundingClientRect();
    const list = popups().map((p) => { const r = p.getBoundingClientRect(); return { p, d: Math.hypot(r.left - fr.left, Math.max(0, r.top - fr.bottom)) }; })
      .sort((a, b) => a.d - b.d);
    return list.length && list[0].d < 700 ? list[0].p : null;
  }

  function optionText(o) { return clean(o.innerText || o.textContent, 200); }

  // ---- 23.6.1: lists without roles or known class names (Trip.com, CSS-module sites) -----------------------------
  // The visible text of an element, its text pieces joined by spaces ("New Delhi - All airports" + "India"), because
  // innerText runs flex items together ("airportsIndia").
  function rowText(el) {
    if (!el) return "";
    const parts = [];
    const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let t = w.nextNode(); t && parts.length < 40; t = w.nextNode()) {
      const v = t.textContent.replace(/\s+/g, " ").trim();
      if (v && t.parentElement && visible(t.parentElement)) parts.push(v);
    }
    return clean(parts.join(" "), 200);
  }
  const pointer = (n) => n && n.nodeType === 1 && getComputedStyle(n).cursor === "pointer";
  /** The element a click on ``el`` really pressed: where the pointer cursor starts (the row, not its inner span). */
  function pointerRoot(el) {
    let n = el && (el.nodeType === 1 ? el : el.parentElement);
    if (!n) return null;
    const semantic = n.closest("button,a[href],[role=option],[role=menuitem],[role=button],[role=link],[role=tab],[role=checkbox]");
    if (!pointer(n)) return semantic || (n.matches("li,[role=listitem],[onclick]") ? n : null); // not clickable: no row
    for (let i = 0; i < 8 && n.parentElement && n.parentElement !== document.body && pointer(n.parentElement); i++) {
      if (repeated(n) && rowText(n)) break; // one row of a list (the cursor is inherited from the list around it)
      const r = n.parentElement.getBoundingClientRect();
      if (r.width > 900 || r.height > 220) break; // a whole pop-up or panel with a pointer cursor
      n = n.parentElement;
    }
    return semantic && n.contains(semantic) ? semantic : n;
  }
  const STATE_CLS = /(^|\s)(is-|has-)?(active|selected|hover|hovered|focus|focused|current|checked|highlight\w*|on)(\s|$)/gi;
  const signature = (n) => `${n.tagName}.${cls(n).replace(STATE_CLS, " ").trim().split(/\s+/).sort().join(".")}`;
  /** ``row`` is one of several look-alike siblings (a list), not a lone control. */
  function repeated(row) {
    if (!row || !row.parentElement) return false;
    const sig = signature(row);
    return [...row.parentElement.children].filter((k) => k !== row && visible(k) && signature(k) === sig).length >= 1;
  }
  /** A clicked element that can be a row of a list: small, with text, not a field, form, tab or a commit button. */
  function rowOf(el) {
    const r0 = pointerRoot(el);
    if (!r0 || r0 === document.body || r0 === document.documentElement) return null;
    if (r0.matches("input,select,textarea,[contenteditable=true],[role=tab],[role=checkbox],[role=radio],[role=switch],label")) return null;
    if (r0.querySelector("input:not([type=hidden]),select,textarea")) return null;
    const r = r0.getBoundingClientRect();
    if (r.height > 160 || r.width > 900 || !rowText(r0)) return null;
    return r0;
  }
  /** Rows a person can click near a field: what is drawn on screen below (or above) it, found by sampling points. */
  function nearRows(field) {
    if (!field || !visible(field)) return [];
    const fr = field.getBoundingClientRect();
    const out = new Set();
    const vw = innerWidth, vh = innerHeight;
    const xs = [];
    for (let x = Math.max(2, fr.left + 8); x < Math.min(vw - 2, fr.left + Math.max(fr.width, 380)); x += 40) xs.push(x);
    const probe = (x, y) => {
      if (y < 1 || y > vh - 1) return;
      const hit = document.elementFromPoint(x, y);
      if (!hit || hit === field || hit.contains(field)) return; // (a custom dropdown can hold its own list)
      const row = rowOf(hit);
      if (row && !row.contains(field) && layered(row)) out.add(row);
    };
    for (let y = fr.bottom + 6; y < Math.min(vh, fr.bottom + 640); y += 14) xs.forEach((x) => probe(x, y));
    if (!out.size) for (let y = fr.top - 6; y > Math.max(0, fr.top - 420); y -= 14) xs.forEach((x) => probe(x, y));
    return [...out];
  }
  /** Drawn on a layer above the page (a pop-up: absolute / fixed position), not page content that happens to be below. */
  function layered(el) {
    for (let n = el, i = 0; n && n !== document.body && i < 12; n = n.parentElement, i++) {
      const p = getComputedStyle(n).position;
      if (p === "absolute" || p === "fixed") return true;
    }
    return false;
  }
  const tokens = (s) => new Set(norm(s).split(" ").filter(Boolean));
  function similar(a, b) {
    const A = tokens(a), B = tokens(b);
    if (!A.size || !B.size) return 0;
    let n = 0; A.forEach((t) => { if (B.has(t)) n += 1; });
    return n / (A.size + B.size - n);
  }

  /** Mark the option of the open list that is ``want``: exact text, then first line, then unique prefix, then unique word match. */
  function findOption(want, field) {
    clear();
    const w = norm(want);
    if (!w) return { status: "none", options: [] };
    const root = linkedPopup(field) || null;
    const pools = root ? [root] : popups();
    let opts = [];
    for (const p of pools) opts = opts.concat(optionsOf(p));
    // 23.6.1: lists with no role and no known class (generated CSS-module names): the rows drawn next to the field
    if (field) for (const r of nearRows(field)) if (!opts.includes(r)) opts.push(r);
    opts = opts.filter((o) => !disabled(o));
    const texts = opts.map((o) => rowText(o) || optionText(o));
    if (!opts.length) return { status: "none", options: [] };
    const firstLine = (o) => norm(String(o.innerText || "").split("\n")[0]);
    const full = (o) => [norm(optionText(o)), norm(rowText(o))];
    const tiers = [
      (o) => full(o).includes(w),
      (o) => firstLine(o) === w,
      (o) => norm(o.getAttribute("aria-label")) === w || norm(o.getAttribute("data-value")) === w || norm(o.getAttribute("title")) === w,
      (o) => firstLine(o).startsWith(w + " ") || firstLine(o).startsWith(w),
      (o) => ` ${firstLine(o)} `.includes(` ${w} `),  // w is normalised: letters, digits and spaces only
      // 23.7.1: the typed words in a row whose parts are laid out side by side ("DXB | Dubai International Airport |
      // Dubai, United Arab Emirates" for "dubai international airport")
      (o) => full(o).some((t) => ` ${t} `.includes(` ${w} `)),
      (o) => similar(rowText(o) || optionText(o), want) >= 0.75,  // 23.6.1: same words, other punctuation / order
    ];
    for (let i = 0; i < tiers.length; i++) {
      let hits = opts.filter(tiers[i]);
      // the same row found twice (a list item and the clickable row inside it): keep the innermost
      hits = hits.filter((h) => !hits.some((x) => x !== h && h.contains(x)));
      if (hits.length === 1 || (hits.length > 1 && i < 2)) {
        // exact text twice (e.g. a category heading and its item): the first selectable one
        const h = hits[0];
        mark(h.matches("[role=option],[role=menuitem],li") ? h : (h.closest("[role=option],[role=menuitem]") || pointerRoot(h) || h));
        return { status: "found", text: rowText(h) || optionText(h), tier: i + 1, count: opts.length };
      }
      if (hits.length > 1) return { status: "ambiguous", options: hits.map((h) => rowText(h) || optionText(h)).slice(0, 15), count: opts.length };
    }
    return { status: "none", options: texts.slice(0, 15), count: opts.length };
  }

  // ---- 23.6.1: counters ("Children  [-] 1 [+]"): rooms, guests, passengers, quantities --------------------------
  const MINUS = /(minus|reduce|decrease|decrement|subtract|\bsub\b|less|remove|fewer)/i;
  const PLUS = /(plus|\badd\b|add_|add-|increase|increment|more)/i;
  function stepControl(c) {
    if (!visible(c)) return false;
    const r = c.getBoundingClientRect();
    if (r.width > 64 || r.height > 64 || r.width < 6 || r.height < 6) return false;
    const t = (c.innerText || c.textContent || "").trim();
    if (t.length > 1 && !/^(\+|-|−|–|＋|－)$/.test(t)) return false;
    return c.matches("button,[role=button]") || pointer(c) || MINUS.test(cls(c)) || PLUS.test(cls(c));
  }
  const numberOf = (n) => { const t = n.tagName === "INPUT" ? n.value : (n.textContent || ""); return /^\s*\d{1,3}\s*$/.test(t) ? Number(t) : null; };
  function counters() {
    const out = [];
    const nums = [...document.querySelectorAll("span,div,b,strong,p,em,label,input,output")]
      .filter((n) => (n.tagName === "INPUT" || !n.children.length) && numberOf(n) !== null && visible(n));
    for (const n of nums.slice(0, 200)) {
      if (n.closest("[role=grid],[role=checkbox],td,[class*=calendar i]")) continue; // calendar days are not counters
      let box = n.parentElement;
      for (let i = 0; box && i < 3; i++, box = box.parentElement) {
        const roots = [...new Set([...box.querySelectorAll("button,[role=button],span,i,a,div,svg")].filter((c) => c !== n && !c.contains(n) && stepControl(c)).map((c) => {
          let p = c; while (p.parentElement && p.parentElement !== box && stepControl(p.parentElement)) p = p.parentElement; return p;
        }))];
        const before = roots.filter((r) => r.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING);
        const after = roots.filter((r) => n.compareDocumentPosition(r) & Node.DOCUMENT_POSITION_FOLLOWING);
        if (before.length && after.length) {
          out.push({ num: n, box, minus: before[before.length - 1], plus: after[0] });
          break;
        }
        if (roots.length > 2) break;
      }
    }
    // each counter's label: the text of its row (the nearest ancestor that holds no other counter)
    for (const c of out) {
      let row = c.box, label = "", sub = "";
      for (let i = 0; row && i < 4; i++, row = row.parentElement) {
        if (out.some((o) => o !== c && row.contains(o.num))) break;
        const bits = [];
        const w = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
        for (let t = w.nextNode(); t; t = w.nextNode()) {
          const v = t.textContent.replace(/\s+/g, " ").trim();
          if (v && !c.box.contains(t.parentElement) && !/^[\d\s+\-−–]+$/.test(v) && visible(t.parentElement)) bits.push(v);
        }
        if (bits.length) { label = bits[0]; sub = bits[1] || ""; }
        if (label && i >= 1) break;
      }
      if (!label) { const al = `${c.minus.getAttribute("aria-label") || ""} ${c.plus.getAttribute("aria-label") || ""}`; label = clean(al.replace(/\b(add|remove|increase|decrease|plus|minus|one|number of)\b/gi, ""), 60); }
      c.label = clean(label, 60); c.sub = clean(sub, 60);
    }
    return out;
  }
  function findCounter(label) {
    const all = counters();
    const w = norm(label);
    const hit = all.filter((c) => norm(c.label) === w) .concat(all.filter((c) => norm(c.label) !== w && (norm(c.label).startsWith(w) || w.startsWith(norm(c.label)) && norm(c.label))));
    return { hit: hit[0] || null, labels: all.map((c) => c.label + (c.sub ? ` (${c.sub})` : "")) };
  }
  /** Read (dir 0) or mark the - (dir -1) / + (dir 1) control of the counter named ``label``. */
  function counter(label, dir) {
    clear();
    const { hit, labels } = findCounter(label);
    if (!hit) return { status: "none", labels };
    const res = { status: "found", label: hit.label, value: numberOf(hit.num), minusDisabled: disabled(hit.minus) || !!hit.minus.disabled,
      plusDisabled: disabled(hit.plus) || !!hit.plus.disabled };
    if (dir > 0) mark(hit.plus); else if (dir < 0) mark(hit.minus);
    return res;
  }
  /** For the recorder: the click on ``el`` pressed the + or - of a counter: which one, its label and value. */
  function stepperOf(el) {
    const all = counters();
    for (const c of all) {
      if (c.plus === el || c.plus.contains(el)) return { label: c.label, sub: c.sub, dir: 1, before: numberOf(c.num), num: c.num, control: c.plus };
      if (c.minus === el || c.minus.contains(el)) return { label: c.label, sub: c.sub, dir: -1, before: numberOf(c.num), num: c.num, control: c.minus };
    }
    return null;
  }

  // ---- classification -------------------------------------------------------------------------------------------
  const FIELDS = "input:not([type=hidden]),select,textarea,[contenteditable=true],[role=textbox],[role=combobox]";
  const INTERACTIVE = `${FIELDS},button,a[href],[role=button],[role=link],[role=tab],[role=checkbox],[role=radio],[role=switch],[role=option],[role=menuitem]`;
  const DROPDOWN_CLASS = /(select2|ant-select|MuiSelect|MuiAutocomplete|react-select|v-select|ng-select|chosen-container|choices|multiselect|dropdown-toggle|p-dropdown|selectize|custom-select)/i;

  function isContainer(el) {
    if (!el || el === document.body || el === document.documentElement) return true;
    const fields = el.querySelectorAll(FIELDS).length;
    const inter = el.querySelectorAll(`${INTERACTIVE},[tabindex]:not([tabindex='-1'])`).length;
    return fields >= 2 || inter >= 5 || (fields >= 1 && inter >= 4);
  }

  function classify(el) {
    if (!el) return { kind: "missing" };
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    const popup = (el.getAttribute("aria-haspopup") || "").toLowerCase();
    const info = { tag, type, role, label: clean(labelText(el), 120), text: clean(el.innerText || el.value, 120) };
    if (tag === "html" || tag === "body") return { ...info, kind: "page" };
    if (tag === "select") return { ...info, kind: el.multiple ? "native_multiselect" : "native_select" };
    if (tag === "input" && ["date", "datetime-local", "month", "week"].includes(type)) return { ...info, kind: "date_input" };
    if (tag === "input" && type === "time") return { ...info, kind: "time_input" };
    if ((tag === "input" && type === "checkbox") || role === "checkbox") return { ...info, kind: "checkbox" };
    if ((tag === "input" && type === "radio") || role === "radio") return { ...info, kind: "radio" };
    if (role === "switch" || /(\bswitch\b|toggle-?switch|form-switch|slide-toggle|\btoggle\b)/i.test(cls(el)) && !DROPDOWN_CLASS.test(cls(el))) return { ...info, kind: "switch" };
    if ((tag === "input" && type === "range") || role === "slider") return { ...info, kind: "slider" };
    if (tag === "input" && type === "file") return { ...info, kind: "file" };
    const d = attrDate(el) || attrDate(el.parentElement) || "";
    if (d) { const r = el.getBoundingClientRect(); if (r.width < 220 && r.height < 160) return { ...info, kind: "calendar_day", date: d }; }
    const editable = tag === "textarea" || el.isContentEditable || (tag === "input" && !["button", "submit", "reset", "image"].includes(type));
    const autocomplete = (el.getAttribute("aria-autocomplete") || "") !== "" || el.hasAttribute("list") || /autocomplete|typeahead|autosuggest/i.test(cls(el));
    if (role === "combobox" || ["listbox", "true", "menu", "tree", "grid", "dialog"].includes(popup) || autocomplete || DROPDOWN_CLASS.test(cls(el)) || DROPDOWN_CLASS.test(cls(el.parentElement))) {
      if (editable) return { ...info, kind: autocomplete || role === "combobox" ? "autocomplete" : "combobox", expanded: el.getAttribute("aria-expanded") === "true" };
      if (DATEISH.test(info.label) && popup !== "listbox") return { ...info, kind: "date_field" };
      return { ...info, kind: "dropdown", expanded: el.getAttribute("aria-expanded") === "true" };
    }
    if (DATEISH.test(info.label) && (editable || getComputedStyle(el).cursor === "pointer" || tag === "button" || role === "button")) return { ...info, kind: "date_field" };
    if (role === "tab") return { ...info, kind: "tab", selected: el.getAttribute("aria-selected") === "true" };
    if (/option|menuitem|treeitem/.test(role)) return { ...info, kind: "option" };
    if (editable) return { ...info, kind: "textbox" };
    if (isContainer(el)) return { ...info, kind: "container" };
    if (tag === "a") return { ...info, kind: "link" };
    if (tag === "button" || role === "button") return { ...info, kind: "button" };
    return { ...info, kind: "other" };
  }

  function fieldText(el) {
    if (!el) return "";
    if ("value" in el && el.tagName !== "BUTTON" && el.tagName !== "LI") {
      if (el.tagName === "SELECT") return clean([...el.selectedOptions].map((o) => o.text).join(", "));
      return clean(el.value);
    }
    return clean(el.innerText || el.textContent, 300);
  }

  /** Text around a field (its own value plus the closest container's), to check a choice was applied. */
  function shownNear(el) {
    if (!el) return "";
    const own = fieldText(el);
    let p = el.parentElement;
    for (let i = 0; p && i < 3 && p.innerText && p.innerText.length < 400; i++) p = p.parentElement;
    return clean(`${own} ${p ? p.innerText : ""}`, 600);
  }

  /** 23.7.1: what a field's own group shows (the field, the box around it up to the first ancestor that holds
   * another text field, chips and labels in it), to check a choice that a site writes somewhere else than the input
   * (Trip.com: "Dubai" in the input, or a chip / label next to it). */
  function groupText(el) {
    if (!el) return "";
    const isText = (i) => i.matches("input:not([type=hidden]):not([type=checkbox]):not([type=radio]),textarea,[role=combobox],[contenteditable=true]");
    let box = el;
    for (let p = el.parentElement, i = 0; p && i < 4; p = p.parentElement, i++) {
      const others = [...p.querySelectorAll("input,textarea,[role=combobox],[contenteditable=true]")].filter((x) => isText(x) && x !== el && !x.contains(el) && !el.contains(x) && visible(x));
      if (others.length || (p.innerText || "").length > 400) break;
      box = p;
    }
    const vals = [...box.querySelectorAll("input,textarea")].map((i) => i.value || "").join(" ");
    const attrs = [box.getAttribute("aria-label"), box.getAttribute("title"), el.getAttribute("title")].filter(Boolean).join(" ");
    return clean(`${fieldText(el)} ${vals} ${box === el ? "" : box.innerText || ""} ${attrs}`, 600);
  }

  // Resolves when the page has not changed for ``quiet`` ms (or after ``max`` ms): state-aware waiting, not sleeps.
  function settle(quiet = 300, max = 4000) {
    return new Promise((resolve) => {
      let timer = null;
      const start = Date.now();
      const done = (why) => { obs.disconnect(); clearTimeout(timer); clearTimeout(cap); resolve({ why, ms: Date.now() - start }); };
      const obs = new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(() => done("quiet"), quiet); });
      obs.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
      timer = setTimeout(() => done("quiet"), quiet);
      const cap = setTimeout(() => done("max"), max);
    });
  }

  /** Busy indicators an application shows while it loads (spinners, skeletons, aria-busy). */
  function busy() {
    return [...document.querySelectorAll("[aria-busy=true],[class*=spinner i],[class*=loading i],[class*=skeleton i],[class*=loader i]")]
      .filter((n) => visible(n) && n.getBoundingClientRect().width > 8).length;
  }

  function overlays() {
    return { popups: popups().length, calendars: months(dayCells()).length };
  }

  window.__sgComp = {
    version: "23.6.1", parseDate, monthYear, classify, isContainer, findDay, daySelected, showsDate, dateFields, markDateField,
    findOption, popups: () => popups().map((p) => ({ options: optionsOf(p).slice(0, 30).map(optionText) })), overlays, busy,
    fieldText, shownNear, groupText, settle, clear,
    // 23.6.1: lists without roles, counters (+ / -)
    rowText, rowOf, pointerRoot, repeated, nearRows, counter, counters: () => counters().map((c) => ({ label: c.label, sub: c.sub, value: numberOf(c.num) })),
    stepperOf, numberOf,
    // for the recorder: the date of a clicked calendar cell (with its panel), the option a click chose
    cellDate(el) {
      if (!el) return "";
      for (let n = el, i = 0; n && i < 4; n = n.parentElement, i++) {
        const d = attrDate(n);
        if (d) { const r = n.getBoundingClientRect(); if (r.width < 220 && r.height < 160) return d; }
      }
      const cells = dayCells();
      const hit = cells.find((c) => c.el === el || c.el.contains(el))
        || (el.getBoundingClientRect().height < 120 && cells.find((c) => el.contains(c.el))); // 23.6.1: the li around span.day
      return hit ? hit.iso : "";
    },
    optionOf(el) {
      const o = el && el.closest && el.closest("[role=option],[role=menuitem],[role=treeitem],li,[id*=-item-],[class*=option i],[class*=suggestion i]");
      if (!o || !o.closest(POPUP_SEL)) return null;
      return { text: optionText(o), first: clean(String(o.innerText || "").split("\n")[0], 120) };
    },
    dateRole(text) { const t = String(text || ""); return /end date|check.?out|return|until|inbound/i.test(t) ? "end" : /start date|check.?in|depart|from|outbound/i.test(t) ? "start" : ""; },
  };
})();
