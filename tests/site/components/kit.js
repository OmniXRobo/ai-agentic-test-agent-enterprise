// Test fixture (23.6.0): small hand-written widgets that behave like the custom controls of real travel and business
// sites, without ids or with generated / duplicate / changing ids and class names. Used only by the tests.
(() => {
  const rnd = () => Math.random().toString(36).slice(2, 8);
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const SHORT_D = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const el = (tag, attrs = {}, text) => { const e = document.createElement(tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text != null) e.textContent = text; return e; };
  const portal = () => { let p = document.getElementById("portal-root"); if (!p) { p = el("div", { id: "portal-root" }); document.body.appendChild(p); } return p; };
  const later = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---- autocomplete with suggestions loaded after a delay, rendered in a portal ------------------------------------
  function autocomplete(input, source, { delay = 350, onPick } = {}) {
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    let list = null, seq = 0;
    const close = () => { if (list) list.remove(); list = null; input.setAttribute("aria-expanded", "false"); };
    input.addEventListener("input", async () => {
      const q = input.value.trim().toLowerCase(); const mine = ++seq;
      close();
      if (!q) return;
      await later(delay);
      if (mine !== seq) return;
      const items = source.filter((s) => s.title.toLowerCase().includes(q) || (s.sub || "").toLowerCase().includes(q));
      list = el("ul", { role: "listbox", class: `css-${rnd()}-menu`, id: `listbox-${rnd()}` });
      const r = input.getBoundingClientRect();
      list.style.cssText = `position:absolute;left:${r.left + scrollX}px;top:${r.bottom + scrollY + 2}px;background:#fff;border:1px solid #888;list-style:none;margin:0;padding:4px;z-index:50;min-width:${r.width}px`;
      input.setAttribute("aria-controls", list.id);
      for (const it of items) {
        const li = el("li", { role: "option", class: `css-${rnd()}-option` });
        li.style.cssText = "padding:4px;cursor:pointer";
        li.appendChild(el("div", {}, it.title));
        if (it.sub) li.appendChild(el("div", { class: "sub" }, it.sub));
        li.addEventListener("mousedown", (e) => e.preventDefault()); // keep focus, like real autocompletes
        li.addEventListener("click", () => { input.value = it.value || it.title; input.dataset.chosen = it.title; close(); input.dispatchEvent(new Event("change", { bubbles: true })); onPick && onPick(it); });
        list.appendChild(li);
      }
      portal().appendChild(list);
      input.setAttribute("aria-expanded", "true");
    });
    input.addEventListener("blur", () => setTimeout(close, 150));
  }

  // ---- calendar: one or two month panels, day cells named by full date (or numbers only) ---------------------------
  function calendar(host, { panels = 2, style = "long", numbersOnly = false, onPick, label = () => "", minDate = today, priceEvery = 3 } = {}) {
    let first = new Date(today.getFullYear(), today.getMonth(), 1);
    const box = el("div", { class: `cal-${rnd()}`, role: "dialog", "aria-label": "Choose dates" });
    box.style.cssText = "position:absolute;background:#fff;border:1px solid #333;padding:8px;z-index:40;display:flex;gap:16px";
    const draw = () => {
      box.innerHTML = "";
      const prev = el("button", { type: "button", "aria-label": "Previous month", class: `nav-${rnd()}` }, "‹");
      const next = el("button", { type: "button", "aria-label": "Next month", class: `nav-${rnd()}` }, "›");
      prev.onclick = () => { first = new Date(first.getFullYear(), first.getMonth() - 1, 1); draw(); };
      next.onclick = () => { first = new Date(first.getFullYear(), first.getMonth() + 1, 1); draw(); };
      box.appendChild(prev);
      for (let p = 0; p < panels; p++) {
        const m = new Date(first.getFullYear(), first.getMonth() + p, 1);
        const panel = el("div", { class: `panel-${rnd()}` });
        panel.appendChild(el("div", { class: `title-${rnd()}` }, `${MONTHS[m.getMonth()]} ${m.getFullYear()}`));
        const grid = el("div", { role: "grid", class: `grid-${rnd()}` });
        grid.style.cssText = "display:grid;grid-template-columns:repeat(7,34px);gap:2px";
        for (let i = 0; i < m.getDay(); i++) grid.appendChild(el("div", {}, ""));
        const days = new Date(m.getFullYear(), m.getMonth() + 1, 0).getDate();
        for (let d = 1; d <= days; d++) {
          const date = new Date(m.getFullYear(), m.getMonth(), d);
          const off = date < minDate;
          const name = style === "short" ? `${SHORT_D[date.getDay()]} ${MONTHS[date.getMonth()].slice(0, 3)} ${String(d).padStart(2, "0")} ${date.getFullYear()}`
            : `${DAYS[date.getDay()]}, ${MONTHS[date.getMonth()]} ${d}, ${date.getFullYear()}` + (d % priceEvery === 0 ? `, The price is AED ${(1 + d / 20).toFixed(2)}k` : "") + label();
          const cell = el("div", numbersOnly ? { class: `day-${rnd()}${off ? " is-disabled" : ""}` } : { role: style === "short" ? "gridcell" : "checkbox", "aria-label": name, class: `day-${rnd()}${off ? " is-disabled" : ""}`, "aria-disabled": off ? "true" : "false" });
          cell.style.cssText = `cursor:${off ? "default" : "pointer"};padding:4px;text-align:center`;
          cell.appendChild(el("span", {}, String(d)));
          if (!numbersOnly && d % priceEvery === 0) cell.appendChild(el("div", { class: "price" }, `${(1 + d / 20).toFixed(2)}k`));
          if (!off) cell.addEventListener("click", () => { cell.setAttribute("aria-checked", "true"); cell.classList.add("is-selected"); onPick(iso(date), date); });
          grid.appendChild(cell);
        }
        panel.appendChild(grid);
        box.appendChild(panel);
      }
      box.appendChild(next);
    };
    return {
      open(anchor) { draw(); const r = anchor.getBoundingClientRect(); box.style.left = `${r.left + scrollX}px`; box.style.top = `${r.bottom + scrollY + 4}px`; (host || document.body).appendChild(box); },
      close() { box.remove(); },
      redraw: draw,
      get isOpen() { return box.isConnected; },
    };
  }

  // ---- custom dropdown (button + listbox in a portal) ----------------------------------------------------------------
  function dropdown(button, options, { asyncMs = 0, onPick, multi = false } = {}) {
    button.setAttribute("aria-haspopup", "listbox");
    button.setAttribute("aria-expanded", "false");
    let list = null;
    const chosen = new Set();
    button.addEventListener("click", async () => {
      if (list) { list.remove(); list = null; button.setAttribute("aria-expanded", "false"); return; }
      list = el("ul", { role: "listbox", class: `MuiList-${rnd()}` });
      const wrap = el("div", { class: "MuiPopover-paper" });
      const r = button.getBoundingClientRect();
      wrap.style.cssText = `position:absolute;left:${r.left + scrollX}px;top:${r.bottom + scrollY}px;background:#fff;border:1px solid #444;z-index:100`;
      wrap.appendChild(list);
      portal().appendChild(wrap);
      button.setAttribute("aria-expanded", "true");
      if (asyncMs) { list.appendChild(el("li", { class: "loading" }, "Loading…")); await later(asyncMs); list.innerHTML = ""; }
      for (const o of options) {
        const li = el("li", { role: "option", "aria-selected": chosen.has(o) ? "true" : "false" }, o);
        li.style.cssText = "padding:4px 10px;cursor:pointer";
        li.addEventListener("click", () => {
          if (multi) { chosen.has(o) ? chosen.delete(o) : chosen.add(o); button.querySelector(".value").textContent = [...chosen].join(", ") || "None"; li.setAttribute("aria-selected", chosen.has(o)); }
          else { button.querySelector(".value").textContent = o; wrap.remove(); list = null; button.setAttribute("aria-expanded", "false"); }
          onPick && onPick(o);
        });
        list.appendChild(li);
      }
    });
  }

  window.__kit = { autocomplete, calendar, dropdown, iso, today, rnd, el, later, MONTHS };
})();
