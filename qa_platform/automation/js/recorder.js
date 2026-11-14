// Guided Browser Automation recorder. Sends steps to the runtime through the
// exposed binding window.__qaRecord. Password values are never sent.
(() => {
  if (window.__qaRecorderInstalled || !window.__qaFp) return;
  window.__qaRecorderInstalled = true;
  // Paused while a voice command records a rule step itself (e.g. "select tomorrow" -> Current Date + 1 day),
  // so the literal calendar clicks are not recorded as well.
  const send = (step) => { if (window.__qaRecordPaused) return; try { window.__qaRecord(step); } catch (e) { /* binding not ready */ } };
  const target = (el) => window.__qaFp.fingerprint(el);
  const isOverlay = (el) => el && el.closest && el.closest("#__qa_overlay_host, #__qa_rec_host"); // 23.3.20: the Record / Pause bar
  const editable = (el) => el && (el.tagName === "TEXTAREA" || (el.tagName === "INPUT" && !["button", "submit", "reset", "checkbox", "radio", "file", "image"].includes(el.type)) || el.isContentEditable);

  // 23.3.18: date and time pickers. A click that opens a picker and the clicks inside it (month, year, day, hour)
  // are sent marked; when the field's value has changed (a day was clicked, or the picker closed) the value is sent
  // as "picked" and the runtime turns the marked clicks into one pick_date / time step with a rule.
  const PICKER = "[class*=calendar i], [class*=datepicker i], [class*=date-picker i], [class*=timepicker i], [class*=time-picker i], .flatpickr-calendar, .react-datepicker, .mat-calendar, .ui-datepicker, .daterangepicker, .ant-picker-dropdown, [role=dialog] [role=grid]";
  const DATEISH = /(date|calendar|dob|birth|from|until|expiry|time|hour)/i;
  const native = (inp) => ["date", "time", "datetime-local", "month", "week"].includes(inp.type);
  const looksPicker = (inp) => inp && inp.tagName === "INPUT" && !native(inp) && !["checkbox", "radio", "file", "hidden", "password"].includes(inp.type)
    && (/(yyyy|dd|mm|hh)/i.test(inp.placeholder || "") || DATEISH.test(`${inp.name} ${inp.id} ${inp.className} ${inp.parentElement?.className || ""} ${inp.parentElement?.parentElement?.className || ""}`));
  const cls = (n) => (n && n.getAttribute && n.getAttribute("class")) || "";
  // A picker panel: a calendar with day numbers or a grid, or a time picker. Not the calendar icon of the field.
  const isPanel = (n) => /time-?picker/i.test(cls(n)) || !!n.querySelector("[role=grid]")
    || [...n.querySelectorAll("*")].filter((x) => !x.children.length && /^\d{1,2}$/.test((x.textContent || "").trim())).length >= 7;
  const inPicker = (el) => {
    for (let n = el && el.closest ? el.closest(PICKER) : null; n; n = n.parentElement ? n.parentElement.closest(PICKER) : null) {
      if (isPanel(n)) return n;
    }
    return null;
  };
  function fieldFor(el) {
    if (el.tagName === "INPUT") return looksPicker(el) ? el : null;
    // only a picker's own button / icon (calendar, clock), never a labelled button such as Search
    const iconLike = (el.textContent || "").trim().length <= 2
      && /(date|calendar|time|clock|picker)/i.test(`${cls(el)} ${cls(el.parentElement)} ${cls(el.parentElement?.parentElement)}`);
    if (!iconLike) return null;
    let node = el;
    for (let i = 0; node && i < 4; i += 1, node = node.parentElement) {
      const inputs = [...node.querySelectorAll("input:not([type=hidden])")];
      if (inputs.length === 1 && looksPicker(inputs[0])) return inputs[0];
      if (inputs.length > 1) return null;
    }
    return null;
  }
  let pickField = null, pickStart = null;
  const pickerOpen = () => [...document.querySelectorAll(PICKER)].some((p) => p.getClientRects().length && isPanel(p));
  function flushPick() {
    if (!pickField || !pickField.isConnected) { pickField = null; return; }
    const v = pickField.value;
    if (v === pickStart) return;
    pickStart = v;
    send({ action: "picked", target: target(pickField), value: v, placeholder: pickField.getAttribute("placeholder") || "" });
  }
  setInterval(() => { if (pickField && !pickerOpen()) { flushPick(); pickField = null; } }, 500);

  document.addEventListener("click", (e) => {
    const raw = e.target;
    if (isOverlay(raw)) return;
    const panel = inPicker(raw);
    if (panel && pickField) {
      const el = raw.closest("a,button,td,[role],li,div,span") || raw;
      send({ action: "click", target: target(el), _cal: "in" });
      if (/^\d{1,2}$/.test((el.textContent || "").trim())) setTimeout(flushPick, 400); // a day: the field has its date now
      return;
    }
    const opens = fieldFor(raw.closest("input") || raw);
    if (opens) {
      if (pickField && pickField !== opens) flushPick();
      pickField = opens; pickStart = opens.value;
      if (raw.closest("input") === opens) return; // clicking into the field itself: typing is recorded on change
      send({ action: "click", target: target(raw.closest("button,i,span,div") || raw), _cal: "open" });
      return;
    }
    if (pickField) { flushPick(); pickField = null; }
  }, true);

  // 23.6.1: what appeared on the page, and when (a suggestion list after typing, a list after a click), and the text
  // being typed. The typed text is recorded the moment the person presses anything else, so a suggestion click is
  // always recorded after the text that produced the suggestions (sites such as Trip.com report the field's change
  // only when it loses focus, which is after the click).
  const shownAt = new WeakMap();
  new MutationObserver((records) => {
    const t = Date.now();
    for (const r of records) {
      if (r.type === "attributes") shownAt.set(r.target, t);
      else r.addedNodes && r.addedNodes.forEach((x) => { if (x.nodeType === 1) shownAt.set(x, t); });
    }
  }).observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ["class", "style", "hidden", "aria-hidden", "aria-expanded"] });
  const freshSince = (el, since) => { for (let n = el; n && n !== document.body; n = n.parentElement) if ((shownAt.get(n) || 0) >= since) return true; return false; };
  let typing = null;     // {el, start, at, dirty}
  let lastFocus = null;  // {el, at}: the text field the person went into last
  let opener = null;     // {el, at, fp}: the last recorded click (a list it opens belongs to it)
  const fieldValue = (el) => (el.type === "password" ? "__PASSWORD__" : (el.value ?? el.innerText));
  function flushTyping() {
    if (!typing || !typing.dirty || !typing.el.isConnected) return;
    typing.dirty = false;
    const el = typing.el, v = fieldValue(el);
    if (el.__qaSent === v) return;
    el.__qaSent = v;
    send({ action: "fill", target: target(el), value: v, placeholder: el.getAttribute("placeholder") || "", _typed: true });
  }
  document.addEventListener("input", (e) => {
    const el = e.target;
    if (isOverlay(el) || !editable(el) || el === pickField) return;
    const t = Date.now();
    if (!typing || typing.el !== el || !typing.dirty) typing = { el, start: t, at: t, dirty: true, text: "" };
    typing.at = t; typing.dirty = true; typing.text = fieldValue(el);
  }, true);
  document.addEventListener("focusin", (e) => { if (!isOverlay(e.target) && editable(e.target)) lastFocus = { el: e.target, at: Date.now() }; }, true);
  let downActive = null; // the focused element when the mouse went down (before the press moves focus)
  document.addEventListener("mousedown", (e) => {
    downActive = document.activeElement;
    if (isOverlay(e.target) || !typing || !typing.dirty) return;
    if (typing.el === e.target || typing.el.contains(e.target)) return;
    flushTyping();
  }, true);
  // a click on a row of a list that appeared for a field (suggestions) or after a click (a custom dropdown's list)
  function listChoice(raw) {
    const c = comp(); if (!c) return null;
    const row = c.rowOf(raw);
    if (!row) return null;
    const now = Date.now();
    const value = c.rowText(row);
    if (!value || value.length > 160) return null;
    const near = (f) => { const a = f.getBoundingClientRect(), b = row.getBoundingClientRect(); return b.top > a.top - 460 && b.top < a.bottom + 700 && b.left < a.right + 500 && b.right > a.left - 500; };
    // the field is still the focused one when the row is pressed (suggestion rows keep the focus in the field), or
    // the person typed in it moments ago
    const tf = typing && typing.el.isConnected && !row.contains(typing.el) && (downActive === typing.el || now - typing.at < 15000)
      && freshSince(row, typing.start - 30) ? typing.el : null;
    const ff = !tf && lastFocus && lastFocus.el.isConnected && !row.contains(lastFocus.el) && downActive === lastFocus.el
      && freshSince(row, lastFocus.at - 30) ? lastFocus.el : null;
    const field = tf || ff;
    if (field && near(field)) return { kind: "suggestion", field, row, value, typed: tf ? (typing.text || "") : "" };
    if (opener && opener.el !== row && !row.contains(opener.el) && now - opener.at < 60000 && freshSince(row, opener.at - 30) && c.repeated(row)) {
      return { kind: "list", field: opener.el, fp: opener.fp, row, value, typed: "" };
    }
    return null;
  }

  // 23.6.0: semantic recording. A click on a calendar day is "choose this date" (with the date field that opened the
  // calendar), a click on a suggestion or custom-list option is "choose this option in that field", and a click is
  // never recorded on a whole form or container when the person clicked one small control inside it.
  const comp = () => window.__sgComp;
  let lastDateField = null, lastListField = null;
  document.addEventListener("focusin", (e) => {
    const c = comp(); if (!c || isOverlay(e.target)) return;
    const k = c.classify(e.target).kind;
    if (["autocomplete", "combobox", "textbox"].includes(k)) lastListField = e.target;
  }, true);
  function smallClickable(t, broad) {
    // the deepest element the person actually pressed that is a control on its own (pointer cursor, short text)
    for (let n = t; n && n !== broad; n = n.parentElement) {
      if (n.matches("button,a,[role],[onclick],li,svg,img,i") || getComputedStyle(n).cursor === "pointer") {
        const r = n.getBoundingClientRect();
        if (r.width < 400 && r.height < 200) return n.closest("button,a,[role=button],[role=option],li") || n;
      }
    }
    return t.nodeType === 1 ? t : broad;
  }
  document.addEventListener("click", (e) => {
    let el = e.target.closest("a,button,input,select,textarea,label,summary,[role],[onclick]") || e.target;
    if (isOverlay(el) || editable(el) || el.tagName === "SELECT" || el.tagName === "OPTION") return;
    if (inPicker(e.target) && pickField) return; // recorded above
    if (window.__qaSkipClick || window.__qaPick) return; // 23.3.21: the end of a drag, or a click that picks a check
    if (fieldFor(e.target.closest("input") || e.target)) return; // recorded above
    if (el.tagName === "INPUT" && (el.type === "checkbox" || el.type === "radio" || el.type === "file")) return; // recorded on change
    if (el.tagName === "LABEL" && el.control) return; // the labelled control records its own change
    const c = comp();
    if (c) {
      const day = c.cellDate(e.target);
      if (day) {
        const label = `${el.getAttribute("aria-label") || ""} ${e.target.getAttribute && e.target.getAttribute("aria-label") || ""}`;
        send({ action: "pick_date_cell", target: target(el), date: day, role: c.dateRole(label) || "",
          field: lastDateField && lastDateField.isConnected ? target(lastDateField) : null,
          field_label: lastDateField ? (c.classify(lastDateField).label || "") : "" });
        return;
      }
      // 23.6.1: the + or - of a counter (rooms, adults, children, passengers): "set Children to N", read after the click
      const st = c.stepperOf(e.target);
      if (st) {
        const fp = target(st.control);
        setTimeout(() => {
          const after = st.num.isConnected ? c.numberOf(st.num) : null;
          send({ action: "set_count", target: fp, label: st.label, sub: st.sub || "", direction: st.dir, before: st.before,
            value: after != null ? after : (st.before != null ? st.before + st.dir : null) });
        }, 250);
        opener = null;
        return;
      }
      const lc = listChoice(e.target);
      if (lc) {
        if (lc.kind === "suggestion") lc.field.__qaChosenAt = Date.now();
        send({ action: "choose_option", target: lc.fp || target(lc.field), value: lc.value, option: target(lc.row), has_field: true,
          typed: lc.typed, list: lc.kind, option_index: [...(lc.row.parentElement ? lc.row.parentElement.children : [])].indexOf(lc.row) });
        if (lc.kind === "suggestion") typing = null;
        opener = null;
        return;
      }
      const opt = c.optionOf(e.target);
      if (opt && opt.first) {
        const field = lastListField && lastListField.isConnected ? lastListField : null;
        if (field) field.__qaChosenAt = Date.now();
        send({ action: "choose_option", target: field ? target(field) : target(el.closest("[role=option],li") || el),
          value: opt.first, option: target(e.target.closest("[role=option],[role=menuitem],li") || el), has_field: !!field,
          typed: typing && typing.el === field ? typing.text || "" : "" });
        opener = null;
        return;
      }
      const kind = c.classify(el).kind;
      if (kind === "container") el = smallClickable(e.target, el);
      const k2 = c.classify(el).kind;
      if (k2 === "date_field") lastDateField = el;
      if (["dropdown", "combobox", "autocomplete"].includes(k2)) lastListField = el;
    }
    const fp = target(el);
    opener = { el, at: Date.now(), fp };
    send({ action: "click", target: fp });
  }, true);

  // 23.3.17: files chosen for an upload are copied to the script (up to 50 MB each) so playback can upload them again.
  const MAX_FILE = 50 * 1024 * 1024;
  const asBase64 = (file) => new Promise((resolve) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1] || "");
    r.onerror = () => resolve(null);
    r.readAsDataURL(file);
  });
  document.addEventListener("change", async (e) => {
    const el = e.target;
    if (isOverlay(el) || el.tagName !== "INPUT" || el.type !== "file" || !el.files || !el.files.length) return;
    const fp = target(el), files = [], tooLarge = [];
    for (const f of el.files) {
      if (f.size > MAX_FILE) { tooLarge.push(f.name); continue; }
      const data = await asBase64(f);
      if (data !== null) files.push({ name: f.name, data });
    }
    send({ action: "upload_file", target: fp, files, too_large: tooLarge });
  }, true);

  document.addEventListener("change", (e) => {
    const el = e.target;
    if (isOverlay(el)) return;
    if (el.tagName === "INPUT" && el.type === "file") return; // recorded above as an upload
    if (pickField && inPicker(el)) return; // an hour / minute box inside a time picker: the field's value is sent
    if (el.tagName === "SELECT") {
      send({ action: "select", target: target(el), value: el.value });
    } else if (el.tagName === "INPUT" && (el.type === "checkbox" || el.type === "radio")) {
      send({ action: el.checked || el.type === "radio" ? "check" : "uncheck", target: target(el) });
    } else if (editable(el)) {
      if (el === pickField) { flushPick(); return; } // typed into a picker field: one "picked" value
      if (el.__qaChosenAt && Date.now() - el.__qaChosenAt < 5000) return; // 23.6.1: written by the chosen suggestion
      if (typing && typing.el === el) typing.dirty = false;
      if (el.__qaSent === fieldValue(el)) return; // 23.6.1: already recorded when the person pressed something else
      el.__qaSent = fieldValue(el);
      send({ action: "fill", target: target(el), value: el.type === "password" ? "__PASSWORD__" : (el.value ?? el.innerText), placeholder: el.getAttribute("placeholder") || "" });
    }
  }, true);

  // The field the person (or a voice command) used last: "select tomorrow" without a field name means this one.
  document.addEventListener("focusin", (e) => { if (!isOverlay(e.target) && (editable(e.target) || e.target.tagName === "SELECT")) window.__qaLastField = e.target; }, true);

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || isOverlay(e.target)) return;
    const el = e.target;
    if (editable(el) && el.tagName !== "TEXTAREA") {
      if (typing && typing.el === el) typing.dirty = false;
      el.__qaSent = fieldValue(el);
      send({ action: "fill", target: target(el), value: el.type === "password" ? "__PASSWORD__" : el.value });
      send({ action: "press", target: target(el), value: "Enter" });
    }
  }, true);

  // ---- 23.3.21: double-click, right-click, hover, drag and drop, click-to-check -----------------------------
  const now = () => Date.now();
  document.addEventListener("dblclick", (e) => {
    const el = e.target.closest("a,button,input,select,textarea,label,summary,td,th,li,[role],[onclick]") || e.target;
    if (isOverlay(el) || window.__qaPick) return;
    send({ action: "dblclick", target: target(el) });
  }, true);
  document.addEventListener("contextmenu", (e) => {
    const el = e.target.closest("a,button,input,select,textarea,label,summary,td,th,li,[role],[onclick]") || e.target;
    if (isOverlay(el) || window.__qaPick) return;
    send({ action: "right_click", target: target(el) });
  }, true);

  // Hover: only when resting on an element shows something new (a menu, a sub-menu, a tooltip) and the next click is
  // on what appeared. Then a hover step goes before that click, so playback opens the menu first.
  let hover = null;
  const shown = (n) => n && n.nodeType === 1 && n.getClientRects().length > 0 && getComputedStyle(n).visibility !== "hidden";
  const appeared = new Set();
  new MutationObserver((records) => {
    if (!hover || now() - hover.at > 1500) return;
    for (const r of records) {
      const n = r.type === "attributes" ? r.target : null;
      if (n && shown(n)) appeared.add(n);
      r.addedNodes && r.addedNodes.forEach((x) => { if (x.nodeType === 1) appeared.add(x); });
    }
  }).observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ["class", "style", "hidden", "aria-expanded", "aria-hidden"] });
  document.addEventListener("mouseover", (e) => {
    const el = e.target.closest("a,button,li,[role=menuitem],[aria-haspopup],[role=button],nav *,[class*=menu i],[class*=dropdown i],[class*=nav i]");
    if (!el || isOverlay(el) || el === hover?.el) return;
    if (hover && now() - hover.at < 15000 && [...appeared].some((a) => a.isConnected && a.contains(el))) return; // moving into the menu it opened
    hover = { el, at: now() };
    appeared.clear();
  }, true);
  const revealedBy = (t) => {
    if (!hover || now() - hover.at > 15000 || !appeared.size) return null;
    // not for typing into a field, and not inside a date / time picker (the calendar recorder handles those)
    if (editable(t) || t.closest?.(PICKER) || hover.el.closest?.(PICKER)) return null;
    if (hover.el === t || hover.el.contains(t) && !([...appeared].some((a) => a.contains(t)))) return null;
    for (const a of appeared) if (a.isConnected && a.contains(t)) return hover.el;
    return null;
  };
  document.addEventListener("mousedown", (e) => {
    if (e.button !== 0 || isOverlay(e.target) || window.__qaPick) return;
    if ((typing && typing.dirty) || listChoice(e.target)) { hover = null; appeared.clear(); return; } // 23.6.1
    const h = revealedBy(e.target);
    if (h) { send({ action: "hover", target: target(h) }); hover = null; appeared.clear(); }
  }, true);

  // Drag and drop: HTML5 drag events, or a mouse drag (press, move, release on another element)
  let dragSrc = null, press = null, suppressUntil = 0;
  document.addEventListener("dragstart", (e) => { if (!isOverlay(e.target)) dragSrc = e.target.closest("[draggable=true]") || e.target; }, true);
  document.addEventListener("drop", (e) => {
    if (!dragSrc || isOverlay(e.target)) return;
    const dest = e.target.closest("[ondrop],[class*=drop i],[class*=zone i],[class*=column i],li,td,div") || e.target;
    send({ action: "drag_to", target: target(dragSrc), dest: target(dest) });
    dragSrc = null; press = null; suppressUntil = now() + 400;
  }, true);
  document.addEventListener("mousedown", (e) => {
    if (e.button !== 0 || isOverlay(e.target) || editable(e.target) || window.__qaPick) { press = null; return; }
    press = { el: e.target, x: e.clientX, y: e.clientY };
  }, true);
  document.addEventListener("mouseup", (e) => {
    const p = press; press = null;
    if (!p || dragSrc || e.button !== 0) return;
    const far = Math.hypot(e.clientX - p.x, e.clientY - p.y) > 30;
    const sel = String(window.getSelection && window.getSelection()).trim();
    const under = document.elementFromPoint(e.clientX, e.clientY) || e.target;
    if (!far || sel || !under || under === p.el || p.el.contains(under) || under.contains(p.el) || isOverlay(under)) return;
    send({ action: "drag_to", target: target(p.el), dest: target(under) });
    suppressUntil = now() + 400;
  }, true);
  window.addEventListener("click", (e) => {
    if (now() < suppressUntil) { suppressUntil = 0; window.__qaSkipClick = true; setTimeout(() => { window.__qaSkipClick = false; }, 0); }
  }, true);

  // Click-to-check: the Record / Pause bar (or the page) turns on window.__qaPick = "text" | "visible"; the next
  // click picks the element instead of clicking it, and a check step is recorded.
  let picked = null;
  const outline = (el) => {
    if (picked) { picked.style.outline = picked.__qaOutline || ""; }
    picked = el;
    if (el) { el.__qaOutline = el.style.outline; el.style.outline = "3px solid #ff3b3b"; }
  };
  document.addEventListener("mouseover", (e) => { if (window.__qaPick && !isOverlay(e.target)) outline(e.target); }, true);
  const pickHandler = (e) => {
    if (!window.__qaPick || isOverlay(e.target)) return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (e.type !== "click") return;
    const el = e.target, how = window.__qaPick;
    outline(null);
    window.__qaPick = null;
    const text = (el.innerText || el.value || el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 200);
    window.__qaRecord({ action: how === "text" && text ? "expect_text" : "expect_visible", target: target(el), value: how === "text" ? text : null, _check: true });
  };
  ["mousedown", "mouseup", "pointerdown", "pointerup", "click"].forEach((t) => window.addEventListener(t, pickHandler, true));
})();
