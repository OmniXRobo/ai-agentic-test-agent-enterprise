// Test fixture (23.6.2): behaviour of Trip.com's flight search box as of Nov 2026. See flights.html for what it copies.
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const el = (tag, attrs = {}, text) => { const e = document.createElement(tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text != null) e.textContent = text; return e; };
  const later = (ms) => new Promise((r) => setTimeout(r, ms));
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const portal = $("#portal");
  const seed = Math.floor(Math.random() * 97);   // prices differ on every load, as on the real site
  const state = { from: "", to: "", dep: null, ret: null, adults: 1, children: 0, infants: 0, cabin: "Economy", picks: [] };
  window.__trip = state;
  const place = (box, anchor) => { const r = anchor.getBoundingClientRect(); box.style.left = `${r.left + scrollX}px`; box.style.top = `${r.bottom + scrollY + 4}px`; };
  const outside = (box, keep, onClose) => {
    const h = (e) => { if (!box.isConnected) { document.removeEventListener("mousedown", h, true); return; } if (box.contains(e.target) || keep.some((k) => k.contains(e.target))) return; document.removeEventListener("mousedown", h, true); onClose(); };
    setTimeout(() => document.addEventListener("mousedown", h, true), 0);
  };

  // ---- airports and cities ----------------------------------------------------------------------------------------
  const PLACES = [
    { code: "DXB", name: "Dubai International Airport", city: "Dubai", country: "United Arab Emirates" },
    { code: "DWC", name: "Al Maktoum International Airport", city: "Dubai", country: "United Arab Emirates" },
    { code: "DEL", name: "Indira Gandhi International Airport", city: "New Delhi", country: "India" },
    { code: "HDO", name: "Hindon Airport", city: "New Delhi", country: "India" },
    { code: "BOM", name: "Chhatrapati Shivaji Maharaj International Airport", city: "Mumbai", country: "India" },
    { code: "LHR", name: "Heathrow Airport", city: "London", country: "United Kingdom" },
  ];
  const codeOf = (box) => box.querySelector(".cc") || box.appendChild(el("span", { class: "cc" }));
  const hay = (p) => `${p.code} ${p.name} ${p.city} ${p.country}`.toLowerCase();
  const search = (q) => { const words = q.toLowerCase().split(/\s+/).filter(Boolean); return PLACES.filter((p) => words.every((w) => hay(p).includes(w))); };

  function cityField(input, box, key, onPick) {
    let pop = null, seq = 0, timer = null;
    const close = () => { if (pop) pop.remove(); pop = null; input.setAttribute("aria-expanded", "false"); };
    const show = (node) => { close(); pop = node; place(pop, box); portal.appendChild(pop); input.setAttribute("aria-expanded", "true"); };
    // Trip.com writes only the city into the input after a choice; the airport code shows in a label beside it
    const pick = (p) => { state[key] = p.city; state.picks.push(key); input.value = p.city; codeOf(box).textContent = p.code; close(); onPick(p); };
    const row = (node, p) => { node.addEventListener("mousedown", (e) => e.preventDefault()); node.addEventListener("click", (e) => { e.stopPropagation(); pick(p); }); return node; };
    const popular = () => {
      const b = el("div", { class: "pop" }); b.appendChild(el("div", { class: "lb" }, "Popular cities"));
      const ul = el("ul"); ["DXB", "LHR", "DEL", "BOM"].forEach((c, i) => { const p = PLACES.find((x) => x.code === c); const li = el("li", { class: "cty", "data-testid": String(i) }, p.city); ul.appendChild(row(li, p)); });
      b.appendChild(ul); return b;
    };
    const results = (items) => {
      const b = el("div", { class: "pop", "data-testid": "search_result_box" });
      const list = el("div", { id: "m-flight-poi-list" }); list.appendChild(el("div", { class: "lb" }, "Airports and cities"));
      const wrap = el("div"); const ul = el("ul");
      items.forEach((p, i) => { const li = el("li"); const r = el("div", { role: "button", class: "X9ak2", "data-testid": String(i) }); r.append(el("span", { class: "cd" }, p.code), el("span", { class: "nm" }, p.name), el("span", { class: "ct" }, `${p.city}, ${p.country}`)); li.appendChild(row(r, p)); ul.appendChild(li); });
      if (!items.length) ul.appendChild(el("li", { class: "lb" }, "No results"));
      wrap.appendChild(ul); list.appendChild(wrap); b.appendChild(list); return b;
    };
    // the input is a combobox (role and name) only while it has focus, as on the real site: the recorded
    // role=combobox[name=...] locator stops matching when the focus moves on
    input.removeAttribute("role");
    input.addEventListener("focus", () => { input.setAttribute("role", "combobox"); });
    input.addEventListener("blur", () => setTimeout(() => input.removeAttribute("role"), 200));
    input.addEventListener("focus", () => { input.select(); if (!input.value.trim() || state[key]) show(popular()); });
    box.addEventListener("click", () => input.focus());
    input.addEventListener("input", () => {
      const q = input.value.trim(); const mine = ++seq; clearTimeout(timer);
      if (!q) { show(popular()); return; }
      timer = setTimeout(async () => { await later(300); if (mine !== seq || document.activeElement !== input) return; show(results(search(q))); }, 250);
    });
    // no choice made: the field goes back to the last chosen city
    input.addEventListener("blur", () => setTimeout(() => { close(); input.value = state[key] || ""; }, 150));
  }
  const from = $('[data-testid="search_city_from0"]'), to = $('[data-testid="search_city_to0"]');
  cityField(from, $("#fromBox"), "from", () => to.focus());
  cityField(to, $("#toBox"), "to", () => { to.blur(); openCal("dep"); });
  document.querySelectorAll("[role=tab]").forEach((t) => t.addEventListener("click", () => document.querySelectorAll("[role=tab]").forEach((x) => x.setAttribute("aria-selected", x === t))));

  // ---- calendar ------------------------------------------------------------------------------------------------------
  let cal = null, first = new Date(today.getFullYear(), today.getMonth(), 1), leg = "dep";
  const price = (d) => `AED ${(0.9 + ((d.getDate() * 37 + seed) % 90) / 100).toFixed(2)}k`;
  function drawCal() {
    cal.innerHTML = "";
    const atStart = first.getFullYear() === today.getFullYear() && first.getMonth() === today.getMonth();
    const prev = el("span", { class: "nav", role: "button", "aria-label": "Previous month", "aria-disabled": String(atStart) }, "<");
    const next = el("span", { class: "nav", role: "button", "aria-label": "Next month" }, ">");
    prev.addEventListener("click", () => { if (!atStart) { first = new Date(first.getFullYear(), first.getMonth() - 1, 1); drawCal(); } });
    next.addEventListener("click", () => { first = new Date(first.getFullYear(), first.getMonth() + 1, 1); drawCal(); });
    const wrap = el("div", { class: "cal" }); wrap.appendChild(prev);
    for (let p = 0; p < 2; p++) {
      const m = new Date(first.getFullYear(), first.getMonth() + p, 1);
      const panel = el("div"); panel.appendChild(el("div", { class: "lb" }, `${MONTHS[m.getMonth()]} ${m.getFullYear()}`));
      const grid = el("div", { class: "grid" });
      for (let i = 0; i < m.getDay(); i++) grid.appendChild(el("div"));
      for (let d = 1; d <= new Date(m.getFullYear(), m.getMonth() + 1, 0).getDate(); d++) {
        const date = new Date(m.getFullYear(), m.getMonth(), d); const past = date < today;
        const full = `${DAYS[date.getDay()]}, ${MONTHS[date.getMonth()]} ${d}, ${date.getFullYear()}`;
        const name = past ? full : leg === "dep" ? `${full}, Select the start date you want` : `${full}, The price is ${price(date)}, Select the end date you want`;
        const c = el("div", { role: "checkbox", "aria-label": name, "aria-checked": String(!!((state.dep && +date === +state.dep) || (state.ret && +date === +state.ret))), "aria-disabled": String(past) });
        c.appendChild(document.createTextNode(String(d))); if (!past) c.appendChild(el("i", {}, price(date).replace("AED ", "")));
        if (!past) c.addEventListener("click", () => {
          if (leg === "dep" || (state.dep && date < state.dep)) { state.dep = date; state.ret = null; $('[data-testid="search_date_depart0"]').value = `${MON[date.getMonth()]} ${d}`; leg = "ret"; drawCal(); return; }
          state.ret = date; $('[data-testid="search_date_return0"]').value = `${MON[date.getMonth()]} ${d}`; cal.remove(); cal = null;
        });
        grid.appendChild(c);
      }
      panel.appendChild(grid); wrap.appendChild(panel);
    }
    wrap.appendChild(next); cal.appendChild(wrap);
  }
  function openCal(l) {
    leg = l;
    if (!cal) { cal = el("div", { class: "pop" }); place(cal, $("#depBox")); portal.appendChild(cal); outside(cal, [$("#depBox"), $("#retBox")], () => { if (cal) { cal.remove(); cal = null; } }); }
    drawCal();
  }
  $("#depBox").addEventListener("click", () => openCal("dep"));
  $("#retBox").addEventListener("click", () => openCal(state.dep ? "ret" : "dep"));

  // ---- passengers: li rows with icon-only + and -, and the pop-up's own Search button ---------------------------------
  let pax = null;
  const paxName = () => { const parts = [`${state.adults} adult${state.adults > 1 ? "s" : ""}`]; if (state.children) parts.push(`${state.children} child${state.children > 1 ? "ren" : ""}`); if (state.infants) parts.push(`${state.infants} infant${state.infants > 1 ? "s" : ""}`); return `${parts.join(", ")}, ${state.cabin}`; };
  const relabel = () => { $("#paxText").textContent = paxName(); $("#paxBtn").setAttribute("aria-label", paxName()); };
  function paxRow(title, sub, key, min, max) {
    const li = el("li");
    const lbl = el("div"); lbl.append(el("span", {}, title), el("span", { class: "sub" }, sub));
    const ctl = el("div", { class: "ctl" });
    const minus = el("span", { class: "qT7v" }); minus.appendChild(el("i", { class: "fi fi-minus" }));
    const num = el("span", { class: "nM3" });
    const plus = el("span", { class: "qT7v" }); plus.appendChild(el("i", { class: "fi fi-plus" }));
    const upd = () => { num.textContent = String(state[key]); minus.classList.toggle("qT7v--disabled", state[key] <= min); plus.classList.toggle("qT7v--disabled", state[key] >= max); };
    minus.addEventListener("click", () => { if (state[key] > min) { state[key] -= 1; upd(); relabel(); } });
    plus.addEventListener("click", () => { if (state[key] < max) { state[key] += 1; upd(); relabel(); } });
    upd(); ctl.append(minus, num, plus); li.append(lbl, ctl);
    return li;
  }
  $("#paxBtn").addEventListener("click", () => {
    if (pax) return;
    pax = el("div", { class: "pop pxp" });
    const hd = el("div", { class: "lb" }, "Passengers and cabin class");
    const bd = el("div"); const inner = el("div");
    const cabin = el("div", {}, "Cabin class: Economy");
    const lst = el("div"); const ul = el("ul");
    ul.append(paxRow("Adults", "12+ years old", "adults", 1, 9), paxRow("Children", "2-11 years old", "children", 0, 8), paxRow("Infants", "Under 2 years old", "infants", 0, 4));
    const note = el("div", { class: "lb" }, "Children must travel with an adult");
    const ft = el("div"); const go = el("div", { class: "srch", role: "button", "data-testid": "passengernum_cabin_search" }, "Search");
    go.addEventListener("click", () => { pax.remove(); pax = null; runSearch(); });
    ft.appendChild(go); lst.append(ul, note, ft);
    inner.append(el("div", { class: "lb" }, "Passengers"), cabin, lst); bd.appendChild(inner);
    pax.append(hd, bd);
    place(pax, $("#paxBtn")); portal.appendChild(pax);
    outside(pax, [$("#paxBtn")], () => { if (pax) { pax.remove(); pax = null; } });
  });
  async function runSearch() {
    $("#out").innerHTML = '<div class="loading" aria-busy="true">Loading…</div>';
    await later(500);
    $("#out").innerHTML = `<h2 class="results">Flights ${state.from || "?"} to ${state.to || "?"} · ${state.dep ? iso(state.dep) : "?"} to ${state.ret ? iso(state.ret) : "?"} · ${state.adults} adult, ${state.children} child, ${state.infants} infant · ${state.cabin}</h2>`;
  }
  $('[data-testid="search_btn"]').addEventListener("click", runSearch);
})();
