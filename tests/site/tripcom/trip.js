// Test fixture (23.6.1): behaviour of the Trip.com search box, as the tests need it. See index.html for what it copies.
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const el = (tag, attrs = {}, text) => { const e = document.createElement(tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text != null) e.textContent = text; return e; };
  const later = (ms) => new Promise((r) => setTimeout(r, ms));
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const short = (d) => `${DAYS[d.getDay()].slice(0, 3)}, ${MON[d.getMonth()]} ${d.getDate()}`; // Trip.com shows "Wed, Apr 1"
  const portal = $("#portal");
  const state = { dest: "", in: null, out: null, rooms: 1, adults: 2, children: 0, ages: [], from: "Dubai", to: "", dep: null, ret: null,
    trip: "RT", fAdults: 1, fChildren: 0, fInfants: 0, cabin: "Economy" };
  window.__trip = state;
  const place = (box, anchor) => { const r = anchor.getBoundingClientRect(); box.style.left = `${r.left + scrollX}px`; box.style.top = `${r.bottom + scrollY + 4}px`; };
  const closeOnOutside = (box, keep, onClose) => {
    const h = (e) => { if (!box.isConnected) { document.removeEventListener("mousedown", h, true); return; } if (box.contains(e.target) || keep.some((k) => k.contains(e.target))) return; document.removeEventListener("mousedown", h, true); onClose(); };
    setTimeout(() => document.addEventListener("mousedown", h, true), 0);
  };

  // ---- tabs ------------------------------------------------------------------------------------------------------
  document.querySelectorAll("[role=tab]").forEach((t) => t.addEventListener("click", () => {
    document.querySelectorAll("[role=tab]").forEach((x) => { x.setAttribute("aria-selected", x === t); x.classList.toggle("is-active", x === t); });
    document.querySelectorAll("[data-form]").forEach((f) => { f.hidden = f.dataset.form !== t.dataset.tab; });
  }));

  // ---- suggestion lists: filled only from typed text, after a debounce and a "server" delay; close on blur ----------
  function suggest(input, { popular, search, render, onPick }) {
    let box = null, seq = 0, timer = null;
    const close = () => { if (box) box.remove(); box = null; };
    const show = (node) => { close(); box = node; place(box, input.parentElement); portal.appendChild(box); };
    const pick = (value, item) => { input.value = value; close(); onPick && onPick(item); };
    const row = (node, value, item) => { node.addEventListener("mousedown", (e) => e.preventDefault()); node.addEventListener("click", (e) => { e.stopPropagation(); pick(value, item); }); return node; };
    input.addEventListener("focus", () => { if (!input.value.trim() && popular) show(popular(row)); });
    input.addEventListener("click", () => { if (!box && !input.value.trim() && popular) show(popular(row)); });
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase(); const mine = ++seq;
      clearTimeout(timer);
      if (!q) { if (popular) show(popular(row)); else close(); return; }
      timer = setTimeout(async () => {
        await later(300); // the POI search request
        if (mine !== seq || document.activeElement !== input) return;
        show(render(search(q), row));
      }, 250);
    });
    input.addEventListener("blur", () => setTimeout(close, 150));
  }

  // ---- hotels ----------------------------------------------------------------------------------------------------
  const HOTEL_PLACES = [["Paris", "France"], ["Paris, Texas", "United States"], ["Dubai", "United Arab Emirates"], ["Dubai Marina", "Dubai, United Arab Emirates"],
    ["New Delhi", "Delhi, India"], ["Bangkok", "Thailand"]];
  const dest = $("#destinationInput");
  suggest(dest, {
    popular: (row) => { const b = el("div", { class: "Dx7hN" }); b.appendChild(el("div", { class: "r0w s" }, "Popular destinations")); HOTEL_PLACES.slice(0, 4).forEach(([t, s], i) => { const r = el("div", { class: "r0w", "data-testid": String(i) }); r.append(el("div", {}, t), el("div", { class: "s" }, s)); b.appendChild(row(r, t, t)); }); return b; },
    search: (q) => HOTEL_PLACES.filter(([t, s]) => t.toLowerCase().includes(q) || s.toLowerCase().includes(q)),
    render: (items, row) => { const b = el("div", { class: "Dx7hN" }); items.forEach(([t, s], i) => { const r = el("div", { class: "r0w", "data-testid": String(i) }); r.append(el("div", {}, t), el("div", { class: "s" }, s)); b.appendChild(row(r, t, t)); }); return b; },
    onPick: (t) => { state.dest = t; openHotelCal("in"); },
  });

  // hotel calendar: Trip.com's c-calendar markup
  const hcal = $("#hotelCal");
  let hFirst = new Date(today.getFullYear(), today.getMonth(), 1), hLeg = "in";
  function drawHotelCal() {
    hcal.innerHTML = "";
    const body = el("div", { class: "c-calendar__body" });
    for (let p = 0; p < 2; p++) {
      const m = new Date(hFirst.getFullYear(), hFirst.getMonth() + p, 1);
      const month = el("div", { class: "c-calendar-month" });
      month.appendChild(el("div", { class: "c-calendar-month__title" }, `${MON[m.getMonth()]} ${m.getFullYear()}`));
      const head = el("ul", { class: "c-calendar-month__week-head" }); ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].forEach((d) => head.appendChild(el("li", {}, d)));
      month.appendChild(head);
      const days = el("div", { class: "c-calendar-month__days" });
      let week = el("ul", { class: "c-calendar-month__week" });
      for (let i = 0; i < m.getDay(); i++) week.appendChild(el("li"));
      const n = new Date(m.getFullYear(), m.getMonth() + 1, 0).getDate();
      for (let d = 1; d <= n; d++) {
        const date = new Date(m.getFullYear(), m.getMonth(), d);
        const past = date < today;
        const sel = (state.in && +date === +state.in) || (state.out && +date === +state.out);
        const li = el("li", past ? { class: "is-disable" } : { class: `is-allow-hover${sel ? " is-selected" : ""}`, role: "button", tabindex: "0" });
        const w = el("div", { class: "tipWrapper" }); w.appendChild(el("span", { class: "day" }, String(d))); li.appendChild(w);
        if (!past) li.addEventListener("click", () => {
          if (hLeg === "in" || (state.in && date <= state.in)) { state.in = date; state.out = null; $("#checkInInput").value = short(date); $("#checkOutInput").value = ""; hLeg = "out"; drawHotelCal(); return; }
          state.out = date; $("#checkOutInput").value = short(date);
          $("#nights").textContent = `${Math.round((state.out - state.in) / 864e5)} nights`;
          hcal.classList.add("is-hide");
        });
        week.appendChild(li);
        if (date.getDay() === 6) { days.appendChild(week); week = el("ul", { class: "c-calendar-month__week" }); }
      }
      if (week.children.length) days.appendChild(week);
      month.appendChild(days);
      body.appendChild(month);
    }
    const atStart = hFirst.getFullYear() === today.getFullYear() && hFirst.getMonth() === today.getMonth();
    const prev = el("span", { class: `c-calendar-icon-prev${atStart ? " is-disable" : ""}` });
    const next = el("span", { class: "c-calendar-icon-next" });
    prev.addEventListener("click", () => { if (!atStart) { hFirst = new Date(hFirst.getFullYear(), hFirst.getMonth() - 1, 1); drawHotelCal(); } });
    next.addEventListener("click", () => { hFirst = new Date(hFirst.getFullYear(), hFirst.getMonth() + 1, 1); drawHotelCal(); });
    body.append(prev, next);
    hcal.appendChild(body);
  }
  function openHotelCal(leg) { hLeg = leg; drawHotelCal(); hcal.classList.remove("is-hide"); closeOnOutside(hcal, [$("#hotelDates")], () => hcal.classList.add("is-hide")); }
  $("#checkInInput").addEventListener("click", () => openHotelCal("in"));
  $("#checkOutInput").addEventListener("click", () => openHotelCal(state.in ? "out" : "in"));

  // rooms and guests: icon-only + / - spans (no role, no label), and a child-age list for each child
  let gbox = null;
  const guestsLabel = () => `${state.rooms} room${state.rooms > 1 ? "s" : ""}, ${state.adults} adult${state.adults > 1 ? "s" : ""}, ${state.children} child${state.children === 1 ? "" : "ren"}`;
  function stepperRow(title, sub, get, set, min, max, icon) {
    const r = el("div", { class: "LmN0rT" });
    const t = el("div"); t.append(el("div", { class: "t1" }, title), el("div", { class: "t2" }, sub));
    const ctl = el("div", { class: "Vb7kQe" });
    const minus = el("span", { class: "Hs5Ty" }); minus.appendChild(el("i", { class: `smarticon u-icon ${icon[0]}` }));
    const num = el("span", { class: "Nq3Ws" }, String(get()));
    const plus = el("span", { class: "Hs5Ty" }); plus.appendChild(el("i", { class: `smarticon u-icon ${icon[1]}` }));
    const upd = () => { num.textContent = String(get()); minus.classList.toggle("Hs5Ty--dis", get() <= min); plus.classList.toggle("Hs5Ty--dis", get() >= max); };
    minus.addEventListener("click", () => { if (get() > min) { set(get() - 1); upd(); } });
    plus.addEventListener("click", () => { if (get() < max) { set(get() + 1); upd(); } });
    upd();
    ctl.append(minus, num, plus); r.append(t, ctl);
    return r;
  }
  function drawGuests() {
    gbox.innerHTML = "";
    const ic = ["u-icon-ic_new_reduce_line", "u-icon-ic_new_add_line"];
    gbox.append(stepperRow("Rooms", "", () => state.rooms, (v) => { state.rooms = v; }, 1, 8, ic),
      stepperRow("Adults", "Ages 18 or above", () => state.adults, (v) => { state.adults = v; }, 1, 16, ic),
      stepperRow("Children", "Ages 0-17", () => state.children, (v) => { state.children = v; state.ages.length = v; drawGuests(); }, 0, 6, ic));
    for (let i = 0; i < state.children; i++) {
      const sel = el("div", { class: "Ag9Ks", tabindex: "0" }); sel.appendChild(el("span", {}, state.ages[i] != null ? `Child ${i + 1}: ${state.ages[i]} years old` : `Age of child ${i + 1}`));
      sel.addEventListener("click", (e) => {
        if (sel.querySelector(".Ag9Ks-l")) return;
        const l = el("div", { class: "Ag9Ks-l" });
        ["<1", ...Array.from({ length: 17 }, (_, k) => String(k + 1))].forEach((a) => { const o = el("div", { class: "ag-o" }, a === "<1" ? "<1 year old" : `${a} years old`); o.addEventListener("click", (ev) => { ev.stopPropagation(); state.ages[i] = a; drawGuests(); }); l.appendChild(o); });
        sel.appendChild(l);
      });
      gbox.appendChild(sel);
    }
    const done = el("button", { class: "Bt4Dn", type: "button" }, "Done");
    done.addEventListener("click", () => { $("#guestsText").textContent = guestsLabel(); gbox.remove(); gbox = null; });
    gbox.appendChild(done);
    $("#guestsText").textContent = guestsLabel();
  }
  $("#guestsBox").addEventListener("click", () => {
    if (gbox) return;
    gbox = el("div", { class: "Pz1oQw" }); drawGuests(); place(gbox, $("#guestsBox")); portal.appendChild(gbox);
    closeOnOutside(gbox, [$("#guestsBox")], () => { if (gbox) { gbox.remove(); gbox = null; } });
  });
  $("#hotelSearch").addEventListener("click", async () => {
    const missing = state.ages.filter((a) => a == null).length + (state.children - state.ages.length > 0 ? state.children - state.ages.length : 0);
    if (missing) { $("#out").innerHTML = '<p class="err">Please select the age of each child</p>'; return; }
    $("#out").innerHTML = '<div class="loading" aria-busy="true">Loading…</div>';
    await later(500);
    $("#out").innerHTML = `<h2 class="results">Hotels in ${state.dest} · ${state.in ? iso(state.in) : "?"} to ${state.out ? iso(state.out) : "?"} · ${state.rooms} room${state.rooms > 1 ? "s" : ""} · ${state.adults} adults · ${state.children} children${state.children ? " (" + state.ages.join(",") + ")" : ""}</h2>`;
  });

  // ---- flights ---------------------------------------------------------------------------------------------------
  const CITIES = [
    { city: "New Delhi", label: "New Delhi - All airports", country: "India", airports: [["Indira Gandhi International Airport", "DEL"], ["Hindon Airport", "HDO"]] },
    { city: "Delhi", label: "Delhi Cantonment", country: "India", airports: [] },
    { city: "Dubai", label: "Dubai - All airports", country: "United Arab Emirates", airports: [["Dubai International Airport", "DXB"], ["Al Maktoum International Airport", "DWC"]] },
    { city: "London", label: "London - All airports", country: "United Kingdom", airports: [["Heathrow Airport", "LHR"], ["Gatwick Airport", "LGW"]] },
    { city: "Bangkok", label: "Bangkok - All airports", country: "Thailand", airports: [["Suvarnabhumi Airport", "BKK"]] },
    { city: "Mumbai", label: "Mumbai", country: "India", airports: [["Chhatrapati Shivaji Maharaj International Airport", "BOM"]] },
  ];
  const popularFlights = (row) => {
    const b = el("div", { class: "Qp7cX2h" }); b.appendChild(el("div", { class: "h3x" }, "Popular cities"));
    ["Dubai", "London", "Bangkok", "New Delhi", "Mumbai"].forEach((c, i) => b.appendChild(row(el("div", { class: "rw9", "data-testid": String(i) }, c), c, c)));
    return b;
  };
  const renderPoi = (items, row) => {
    const b = el("div", { class: "m-flight-poi-search-B nh_poi-container", "data-testid": "search_result_box" });
    const ul = el("ul", { id: "m-flight-poi-list" });
    items.forEach((c, i) => {
      const li = el("li", { role: "listitem" });
      const t = el("div", { class: "poi-result__title title--new title--able", "data-testid": String(i) });
      t.append(el("span", { class: "poi-result__name" }, c.label), el("span", { class: "poi-result__country" }, c.country));
      li.appendChild(row(t, c.city, c));
      if (c.airports.length) {
        const sub = el("ul");
        c.airports.forEach(([n, code], j) => { const a = el("li", { class: "poi-result__airport", "data-testid": `${i}-${j}`, "data-value": JSON.stringify({ airportCode: code, code, name: n }) }); a.append(el("span", {}, n), el("span", { class: "code" }, code)); sub.appendChild(row(a, `${c.city} (${code})`, { ...c, code })); });
        li.appendChild(sub);
      }
      ul.appendChild(li);
    });
    b.appendChild(ul);
    return b;
  };
  const search = (q) => CITIES.filter((c) => c.city.toLowerCase().includes(q) || c.label.toLowerCase().includes(q) || c.airports.some(([n, code]) => n.toLowerCase().includes(q) || code.toLowerCase() === q));
  const from = $('[data-testid="search_city_from0"]'), to = $('[data-testid="search_city_to0"]');
  suggest(from, { popular: popularFlights, search, render: renderPoi, onPick: (c) => { state.from = typeof c === "string" ? c : c.city; to.focus(); } });
  suggest(to, { popular: popularFlights, search, render: renderPoi, onPick: (c) => { state.to = typeof c === "string" ? c : c.city; to.blur(); openFlightCal("dep"); } });
  document.querySelectorAll(".nh_sp-radio").forEach((r) => r.addEventListener("click", () => {
    document.querySelectorAll(".nh_sp-radio").forEach((x) => x.classList.toggle("is-active", x === r));
    state.trip = r.dataset.testid === "flightType_OW" ? "OW" : "RT"; $("#retWrap").hidden = state.trip === "OW";
  }));
  $(".nh_sp-switch").addEventListener("click", () => { const a = from.value; from.value = to.value; to.value = a; [state.from, state.to] = [state.to, state.from]; });

  // flight calendar: div role=checkbox cells named with the full date, the leg and today's price
  let fbox = null, fFirst = new Date(today.getFullYear(), today.getMonth(), 1), fLeg = "dep";
  function drawFlightCal() {
    fbox.innerHTML = "";
    const atStart = fFirst.getFullYear() === today.getFullYear() && fFirst.getMonth() === today.getMonth();
    const prev = el("span", { class: "nvBtn", role: "button", "aria-label": "Previous month", "aria-disabled": atStart ? "true" : "false" });
    const next = el("span", { class: "nvBtn", role: "button", "aria-label": "Next month" });
    prev.addEventListener("click", () => { if (!atStart) { fFirst = new Date(fFirst.getFullYear(), fFirst.getMonth() - 1, 1); drawFlightCal(); } });
    next.addEventListener("click", () => { fFirst = new Date(fFirst.getFullYear(), fFirst.getMonth() + 1, 1); drawFlightCal(); });
    fbox.appendChild(prev);
    for (let p = 0; p < 2; p++) {
      const m = new Date(fFirst.getFullYear(), fFirst.getMonth() + p, 1);
      const panel = el("div", { class: "Wm2s9Lr" }); panel.appendChild(el("div", { class: "Wm2s9Lr-h" }, `${MONTHS[m.getMonth()]} ${m.getFullYear()}`));
      const grid = el("div", { class: "Gq81zZp" });
      for (let i = 0; i < m.getDay(); i++) grid.appendChild(el("div"));
      const n = new Date(m.getFullYear(), m.getMonth() + 1, 0).getDate();
      for (let d = 1; d <= n; d++) {
        const date = new Date(m.getFullYear(), m.getMonth(), d);
        const past = date < today;
        const price = `AED ${(0.9 + ((d * 37) % 90) / 100).toFixed(2)}k`;
        const name = `${DAYS[date.getDay()]}, ${MONTHS[date.getMonth()]} ${d}, ${date.getFullYear()}, ${fLeg === "dep" ? "Select the start date you want" : "Select the end date you want"}${past ? "" : `, The price is ${price}`}`;
        const sel = (state.dep && +date === +state.dep) || (state.ret && +date === +state.ret);
        const c = el("div", { role: "checkbox", "aria-label": name, "aria-checked": sel ? "true" : "false", "aria-disabled": past ? "true" : "false", class: past ? "dDis" : "" });
        c.appendChild(el("span", {}, String(d)));
        if (!past) c.appendChild(el("div", { class: "pr" }, price.replace("AED ", "")));
        if (!past) c.addEventListener("click", () => {
          if (fLeg === "dep") { state.dep = date; $('[data-testid="search_date_depart0"]').value = short(date); if (state.trip === "RT") { fLeg = "ret"; drawFlightCal(); return; } }
          else { state.ret = date; $('[data-testid="search_date_return0"]').value = short(date); }
          fbox.remove(); fbox = null;
        });
        grid.appendChild(c);
      }
      panel.appendChild(grid); fbox.appendChild(panel);
    }
    fbox.appendChild(next);
  }
  function openFlightCal(leg) {
    fLeg = leg;
    if (!fbox) { fbox = el("div", { class: "k3FvMp9dQx" }); place(fbox, $('[data-testid="search_date_wrapper"]')); portal.appendChild(fbox);
      closeOnOutside(fbox, [$('[data-testid="search_date_wrapper"]')], () => { if (fbox) { fbox.remove(); fbox = null; } }); }
    drawFlightCal();
  }
  $('[data-testid="search_date_depart0"]').closest(".input-wrapper").addEventListener("click", () => openFlightCal("dep"));
  $('[data-testid="search_date_return0"]').closest(".input-wrapper").addEventListener("click", () => openFlightCal(state.dep ? "ret" : "dep"));

  // passengers: <button> + / - with only an icon inside (no text, no label), disabled at the limits
  let pbox = null;
  const paxLabel = () => { const parts = [`${state.fAdults} Adult${state.fAdults > 1 ? "s" : ""}`]; if (state.fChildren) parts.push(`${state.fChildren} Child${state.fChildren > 1 ? "ren" : ""}`); if (state.fInfants) parts.push(`${state.fInfants} Infant${state.fInfants > 1 ? "s" : ""}`); return `${parts.join(", ")}, ${state.cabin}`; };
  function paxRow(title, sub, key, min, max) {
    const r = el("div", { class: "LmN0rT" });
    const t = el("div"); t.append(el("div", { class: "t1" }, title), el("div", { class: "t2" }, sub));
    const ctl = el("div", { class: "Vb7kQe" });
    const minus = el("button", { class: "fbtn", type: "button" }); minus.appendChild(el("i", { class: "fi fi-ic_minus" }));
    const num = el("span", { class: "Nq3Ws" });
    const plus = el("button", { class: "fbtn", type: "button" }); plus.appendChild(el("i", { class: "fi fi-ic_plus" }));
    const upd = () => { num.textContent = String(state[key]); minus.disabled = state[key] <= min; plus.disabled = state[key] >= max || state.fAdults + state.fChildren + state.fInfants >= 9; };
    minus.addEventListener("click", () => { state[key] -= 1; pbox.querySelectorAll(".LmN0rT").forEach((x) => x.__upd()); });
    plus.addEventListener("click", () => { state[key] += 1; pbox.querySelectorAll(".LmN0rT").forEach((x) => x.__upd()); });
    r.__upd = upd; upd();
    ctl.append(minus, num, plus); r.append(t, ctl);
    return r;
  }
  $('[data-testid="flt_passenger_class"]').addEventListener("click", () => {
    if (pbox) return;
    pbox = el("div", { class: "Pz1oQw" });
    pbox.append(paxRow("Adults", "18+ years", "fAdults", 1, 9), paxRow("Children", "2-11 years", "fChildren", 0, 8), paxRow("Infants", "Under 2 years", "fInfants", 0, 4));
    const cabin = el("select", { class: "Kx2Cb", "aria-label": "Cabin class" });
    ["Economy", "Premium economy", "Business", "First"].forEach((c) => cabin.appendChild(el("option", { value: c }, c)));
    cabin.value = state.cabin; cabin.addEventListener("change", () => { state.cabin = cabin.value; });
    const done = el("button", { class: "Bt4Dn", type: "button" }, "Done");
    done.addEventListener("click", () => { $("#paxText").textContent = paxLabel(); pbox.remove(); pbox = null; });
    pbox.append(cabin, done);
    place(pbox, $('[data-testid="flt_passenger_class"]')); portal.appendChild(pbox);
    closeOnOutside(pbox, [$('[data-testid="flt_passenger_class"]')], () => { if (pbox) { $("#paxText").textContent = paxLabel(); pbox.remove(); pbox = null; } });
  });
  $('[data-testid="search_btn"]').addEventListener("click", async () => {
    $("#out").innerHTML = '<div class="loading" aria-busy="true">Loading…</div>';
    await later(500);
    $("#out").innerHTML = `<h2 class="results">Flights ${state.from} to ${state.to} · ${state.dep ? iso(state.dep) : "?"}${state.trip === "RT" ? " to " + (state.ret ? iso(state.ret) : "?") : " one-way"} · ${state.fAdults} adult, ${state.fChildren} child, ${state.fInfants} infant · ${state.cabin}</h2>`;
  });
})();
