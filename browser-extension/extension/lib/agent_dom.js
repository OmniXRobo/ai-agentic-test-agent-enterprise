// SkyGuard Adaptive Agent: DOM inspection, guided-learning observer and user-like actions.
// One file shared by SkyGuard (injected by Playwright) and the Chrome/Edge extension (content script), so both
// interfaces describe elements with exactly the same fingerprint and the Python core heals locators the same way.
// It reads the page; the only things it changes are what a person would change (typing, choosing, clicking) and a
// temporary highlight / "manual control" banner. Page text is data: nothing on the page can call into this script.
(() => {
  if (window.__sgAgent) return;
  const MAX_TEXT = 4000;
  const clean = (s, n = 120) => (s || "").replace(/\s+/g, " ").trim().slice(0, n);
  const els = [];          // handle -> element (this document only)
  let prefix = "0";
  const SECRET_HINT = /(pass(word|code)?|pwd|otp|one[\s_-]?time|verification|auth.?code|security.?code|2fa|mfa|pin\b|token|captcha)/i;

  const visible = (el) => {
    if (!el || !el.isConnected || !el.getClientRects().length) return false;
    const s = getComputedStyle(el);
    if (s.visibility === "hidden" || s.display === "none" || Number(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  function implicitRole(el) {
    const t = el.tagName.toLowerCase();
    const role = el.getAttribute("role");
    if (role) return role.split(" ")[0];
    if (t === "button" || (t === "input" && ["button", "submit", "reset", "image"].includes(el.type))) return "button";
    if (t === "a" && el.hasAttribute("href")) return "link";
    if (t === "input" && el.type === "checkbox") return "checkbox";
    if (t === "input" && el.type === "radio") return "radio";
    if (t === "select") return el.multiple ? "listbox" : "combobox";
    if (t === "textarea" || (t === "input" && !["hidden", "file", "range", "color"].includes(el.type))) return "textbox";
    if (t === "summary") return "button";
    if (el.isContentEditable) return "textbox";
    return null;
  }

  function byIds(ids, root) {
    return (ids || "").split(/\s+/).map((id) => id && root.getElementById ? root.getElementById(id) : null)
      .filter(Boolean).map((e) => clean(e.textContent, 80)).join(" ").trim();
  }

  function labelOf(el) {
    const root = el.getRootNode();
    if (el.labels && el.labels.length) return clean([...el.labels].map((l) => l.textContent).join(" "), 80);
    if (el.id && root.querySelector) {
      try { const l = root.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return clean(l.textContent, 80); } catch (e) { /* ignore */ }
    }
    const wrap = el.closest("label");
    if (wrap) return clean(wrap.textContent, 80);
    return "";
  }

  // Poor HTML: the text a person reads next to the control (table cell to the left, previous sibling, row header).
  function nearbyText(el) {
    const cell = el.closest("td,th");
    if (cell) {
      let prev = cell.previousElementSibling;
      while (prev && !clean(prev.innerText)) prev = prev.previousElementSibling;
      if (prev) return clean(prev.innerText, 60);
      const row = cell.parentElement;
      const table = cell.closest("table");
      if (row && table) {
        const idx = [...row.children].indexOf(cell);
        const head = table.querySelector("thead tr, tr");
        if (head && head !== row && head.children[idx]) return clean(head.children[idx].innerText, 60);
      }
    }
    let node = el;
    for (let depth = 0; depth < 3 && node; depth++) {
      let prev = node.previousElementSibling;
      while (prev) {
        const t = clean(prev.innerText || prev.textContent, 60);
        if (t && !prev.matches("input,select,textarea,button")) return t;
        prev = prev.previousElementSibling;
      }
      // text nodes directly before the element
      let sib = node.previousSibling;
      while (sib) {
        if (sib.nodeType === 3 && clean(sib.textContent)) return clean(sib.textContent, 60);
        sib = sib.previousSibling;
      }
      node = node.parentElement;
      if (node && node.matches("form,body,table,fieldset,dialog,[role=dialog]")) break;
    }
    return "";
  }

  function accessibleName(el) {
    const root = el.getRootNode();
    const labelled = byIds(el.getAttribute("aria-labelledby"), root.getElementById ? root : document);
    if (labelled) return labelled;
    const aria = el.getAttribute("aria-label");
    if (aria) return clean(aria, 80);
    const t = el.tagName.toLowerCase();
    if (["input", "select", "textarea"].includes(t)) {
      if (t === "input" && ["button", "submit", "reset"].includes(el.type)) return clean(el.value, 80);
      return labelOf(el) || clean(el.getAttribute("title") || el.getAttribute("placeholder"), 80);
    }
    if (t === "img") return clean(el.alt, 80);
    const text = clean(el.innerText || el.textContent, 80);
    if (text) return text;
    const img = el.querySelector("img[alt]");
    return clean((img && img.alt) || el.getAttribute("title"), 80);
  }

  const generated = (v) => !v || /\d{4,}/.test(v) || /^[0-9]/.test(v) || /[a-f0-9]{8,}/i.test(v) || /^(ember|react|mui|ext|j_id|ctl\d|ng-|gwt-|x-auto)/i.test(v) || v.length > 60;

  function sectionOf(el) {
    const dlg = el.closest("dialog,[role=dialog],[role=alertdialog],.modal");
    if (dlg) {
      const h = dlg.querySelector("h1,h2,h3,h4,.modal-title,[class*=title]");
      return { dialog: true, section: clean(h ? h.innerText : dlg.getAttribute("aria-label"), 60) };
    }
    const fs = el.closest("fieldset");
    if (fs) { const lg = fs.querySelector("legend"); if (lg) return { dialog: false, section: clean(lg.innerText, 60) }; }
    const form = el.closest("form");
    if (form) {
      const h = form.querySelector("h1,h2,h3,h4,legend");
      return { dialog: false, section: clean((h && h.innerText) || form.getAttribute("aria-label") || (!generated(form.getAttribute("name") || "") && form.getAttribute("name")) || (!generated(form.id) && form.id) || "", 60) };
    }
    return { dialog: false, section: "" };
  }

  function tableContext(el) {
    const row = el.closest("tr");
    const table = el.closest("table");
    if (!row || !table) return null;
    const cells = [...row.children].map((c) => clean(c.innerText, 40));
    return { row: cells.filter(Boolean).slice(0, 6).join(" | ") };
  }

  function ancestors(el) {
    const out = [];
    let node = el.parentElement;
    for (let i = 0; i < 3 && node && node !== document.body; i++) {
      out.push(node.tagName.toLowerCase() + (node.classList[0] && !generated(node.classList[0]) ? "." + node.classList[0] : ""));
      node = node.parentElement;
    }
    return out;
  }

  function isSecretField(el) {
    if (el.type === "password") return true;
    const hint = [el.name, el.id, el.getAttribute("autocomplete"), el.getAttribute("placeholder"), el.getAttribute("aria-label"), labelOf(el)].join(" ");
    return el.getAttribute("autocomplete") === "one-time-code" || SECRET_HINT.test(hint);
  }

  function describe(el) {
    const t = el.tagName.toLowerCase();
    const r = el.getBoundingClientRect();
    const ctx = sectionOf(el);
    const secret = ["input", "textarea"].includes(t) && isSecretField(el);
    const d = {
      tag: t, type: (el.type || "").toLowerCase() || null, role: implicitRole(el), name: accessibleName(el),
      label: labelOf(el), placeholder: clean(el.getAttribute("placeholder"), 60), id: el.id && !generated(el.id) ? el.id : "",
      raw_id: el.id || "", name_attr: el.getAttribute("name") || "", test_id: el.getAttribute("data-testid") || el.getAttribute("data-test") || el.getAttribute("data-qa") || "",
      classes: [...el.classList].filter((c) => !generated(c)).slice(0, 4), text: clean(el.innerText || el.value, 80),
      nearby: nearbyText(el), section: ctx.section, dialog: ctx.dialog, table: tableContext(el), ancestors: ancestors(el),
      href: t === "a" ? (el.getAttribute("href") || "").slice(0, 200) : "", secret,
      required: !!el.required || el.getAttribute("aria-required") === "true",
      disabled: !!el.disabled || el.getAttribute("aria-disabled") === "true", readonly: !!el.readOnly,
      visible: visible(el), in_viewport: r.bottom > 0 && r.top < innerHeight,
      rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
      shadow: el.getRootNode() !== document,
    };
    if (["input", "textarea", "select"].includes(t)) {
      d.value = secret ? (el.value ? "[secret]" : "") : clean(el.value, 200);
      if (el.validity && !el.validity.valid) { d.invalid = true; d.validation = clean(el.validationMessage, 120); }
      if (el.getAttribute("aria-invalid") === "true") d.invalid = true;
    }
    if (t === "input" && ["checkbox", "radio"].includes(el.type)) d.checked = el.checked;
    if (t === "select") d.options = [...el.options].slice(0, 200).map((o) => ({ value: o.value, text: clean(o.text, 80), selected: o.selected, disabled: o.disabled }));
    if (t === "input" && el.getAttribute("list")) {
      const dl = document.getElementById(el.getAttribute("list"));
      if (dl) d.options = [...dl.options].slice(0, 200).map((o) => ({ value: o.value, text: clean(o.label || o.value, 80) }));
    }
    return d;
  }

  const INTERACTIVE = "input:not([type=hidden]),select,textarea,button,a[href],summary,[role=button],[role=link],[role=menuitem],[role=tab],[role=checkbox],[role=radio],[role=combobox],[role=option],[role=switch],[onclick],[contenteditable=true],[tabindex]:not([tabindex='-1'])";

  function* walk(root) {
    for (const el of root.querySelectorAll("*")) {
      yield el;
      if (el.shadowRoot) yield* walk(el.shadowRoot); // open shadow roots only (closed ones are not accessible)
    }
  }

  function messages() {
    const out = [];
    const sel = "[role=alert],[role=status],[aria-live],.alert,.error,.errors,.error-message,.validation-summary-errors,.field-validation-error,.invalid-feedback,.success,.message,.notice,.toast,.flash,.msg,.notification";
    for (const n of document.querySelectorAll(sel)) {
      const text = clean(n.innerText, 200);
      if (!text || !visible(n)) continue;
      const cls = (n.className && n.className.toString()) + " " + (n.getAttribute("role") || "");
      const kind = /error|danger|invalid|fail|alert/i.test(cls) && !/success/i.test(cls) ? "error" : /success|ok|done|saved/i.test(cls) ? "success" : "info";
      if (!out.some((m) => m.text === text)) out.push({ kind, text });
      if (out.length >= 12) break;
    }
    return out;
  }

  function tables() {
    return [...document.querySelectorAll("table")].filter(visible).slice(0, 8).map((t, i) => {
      const headRow = t.querySelector("thead tr") || t.querySelector("tr");
      const headers = headRow ? [...headRow.children].map((c) => clean(c.innerText, 40)) : [];
      const rows = [...t.querySelectorAll("tbody tr, tr")].filter((r) => r !== headRow).slice(0, 100)
        .map((r) => [...r.children].map((c) => clean(c.innerText, 80)));
      const caption = clean((t.caption && t.caption.innerText) || t.getAttribute("aria-label") || "", 60);
      return { index: i, caption, headers, rows };
    });
  }

  function pagination() {
    const candidates = [...document.querySelectorAll("a,button,[role=button],[role=link]")].filter(visible);
    const find = (re) => candidates.find((c) => re.test(clean(c.innerText || c.getAttribute("aria-label") || c.title, 30)) && !c.disabled && !/disabled/.test(c.className));
    const next = find(/^(next|›|»|>|next page)$/i);
    const prev = find(/^(prev|previous|‹|«|<|previous page)$/i);
    const info = [...document.querySelectorAll(".pagination,.pager,[aria-label*=pagination i],nav")].map((n) => clean(n.innerText, 80)).find(Boolean) || "";
    return { next: next ? handleOf(next) : null, prev: prev ? handleOf(prev) : null, info };
  }

  function handleOf(el) {
    let i = els.indexOf(el);
    if (i < 0) { els.push(el); i = els.length - 1; }
    return `${prefix}:${i}`;
  }

  function auth() {
    const pw = [...document.querySelectorAll("input[type=password]")].filter(visible).length;
    const otp = [...document.querySelectorAll("input")].filter((e) => visible(e) && e.type !== "password" && (e.getAttribute("autocomplete") === "one-time-code" || /(otp|one.?time|verification|auth.?code|security.?code|2fa|mfa|passcode|driver.?code)/i.test([e.name, e.id, e.placeholder, labelOf(e), nearbyText(e)].join(" ")))).length;
    const captcha = !!document.querySelector(".g-recaptcha,.h-captcha,.cf-turnstile,[data-sitekey],#captcha,[name=captcha]") ||
      [...document.querySelectorAll("iframe")].some((f) => /recaptcha|hcaptcha|turnstile|captcha/i.test(f.src || f.title || ""));
    return { password_fields: pw, otp_fields: otp, captcha };
  }

  function snapshot(opts = {}) {
    prefix = String(opts.prefix ?? prefix);
    els.length = 0;
    const max = opts.maxElements || 500;
    const elements = [];
    for (const el of walk(document)) {
      if (elements.length >= max) break;
      if (!el.matches || !el.matches(INTERACTIVE)) {
        // poor HTML: clickable divs/spans with a pointer cursor and short text
        if (!(el.tagName === "DIV" || el.tagName === "SPAN" || el.tagName === "TD" || el.tagName === "LI")) continue;
        if (el.children.length > 2 || getComputedStyle(el).cursor !== "pointer") continue;
        const t = clean(el.innerText, 40);
        if (!t || (el.parentElement && getComputedStyle(el.parentElement).cursor === "pointer" && clean(el.parentElement.innerText, 40) === t)) continue;
      }
      if (!visible(el)) continue;
      elements.push({ h: handleOf(el), ...describe(el) });
    }
    const h1 = document.querySelector("h1,h2,[role=heading]");
    const iframes = [...document.querySelectorAll("iframe")].filter(visible).map((f) => { let same = false; try { same = !!f.contentDocument; } catch (e) { same = false; } return { src: (f.src || "").slice(0, 200), same_origin: same }; });
    return {
      url: location.href, title: clean(document.title, 120), heading: clean(h1 && h1.innerText, 120),
      text: clean(document.body ? document.body.innerText : "", MAX_TEXT), elements, tables: tables(), messages: messages(),
      dialogs: [...document.querySelectorAll("dialog[open],[role=dialog],[role=alertdialog],.modal.show,.modal[style*='block']")].filter(visible)
        .map((d) => ({ title: clean((d.querySelector("h1,h2,h3,h4,.modal-title") || {}).innerText || d.getAttribute("aria-label"), 80), text: clean(d.innerText, 300) })),
      pagination: pagination(), auth: auth(), iframes, ready: document.readyState,
    };
  }

  function element(handle) {
    const [p, i] = String(handle).split(":");
    if (p !== prefix) return null;
    const el = els[Number(i)];
    return el && el.isConnected ? el : null;
  }

  // ---- user-like actions (extension; SkyGuard uses Playwright's own trusted input instead) ---------------------
  function act(a) {
    const el = element(a.handle);
    if (!el) return { ok: false, error: "STALE", message: "The element is no longer on the page." };
    if (!visible(el)) return { ok: false, error: "HIDDEN", message: "The element is not visible." };
    if (el.disabled || el.getAttribute("aria-disabled") === "true") return { ok: false, error: "DISABLED", message: "The control is disabled." };
    el.scrollIntoView({ block: "center", inline: "center" });
    const t = el.tagName.toLowerCase();
    try {
      if (a.type === "click") {
        if (t === "form" || (t === "input" && el.type === "submit" && el.form && a.noSubmit)) return { ok: false, error: "REFUSED" };
        el.focus({ preventScroll: true });
        el.click(); // activation behaviour: a disabled or covered control does not fire, form validation still runs
        return { ok: true };
      }
      if (a.type === "fill") {
        if (el.readOnly) return { ok: false, error: "READONLY", message: "The field is read-only." };
        el.focus();
        if (typeof el.select === "function") el.select();
        else document.execCommand("selectAll", false);
        // insertText goes through the browser's editing pipeline (beforeinput/input events, maxlength, pattern)
        const typed = document.execCommand("insertText", false, String(a.value ?? ""));
        if (!typed || (("value" in el) && el.value !== String(a.value ?? "") && el.type !== "date" && el.type !== "time")) {
          // never assign .value directly (that would skip the application's own input handling): the caller types
          // with the browser's trusted input instead (Playwright / chrome.debugger), or pauses for the person
          return { ok: false, error: "TYPING_NOT_ACCEPTED", needs_trusted_input: true, message: "The field did not accept typed text." };
        }
        el.dispatchEvent(new Event("change", { bubbles: true }));
        el.blur();
        const ok = !("value" in el) || el.value === String(a.value ?? "") || el.type === "date" || el.type === "time";
        return { ok, invalid: el.validity ? !el.validity.valid : false, validation: clean(el.validationMessage, 120), error: ok ? null : "VALUE_NOT_ACCEPTED" };
      }
      if (a.type === "select") {
        if (t !== "select") return { ok: false, error: "NOT_SELECT" };
        const want = String(a.value ?? "").trim().toLowerCase();
        const opt = [...el.options].find((o) => !o.disabled && (o.text.trim().toLowerCase() === want || o.value.toLowerCase() === want))
          || [...el.options].find((o) => !o.disabled && o.text.trim().toLowerCase().includes(want) && want.length >= 3);
        if (!opt) return { ok: false, error: "OPTION_NOT_FOUND", message: `No option "${a.value}".`, options: [...el.options].map((o) => o.text.trim()).slice(0, 50) };
        el.focus();
        opt.selected = true;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        el.blur();
        return { ok: true, chosen: opt.text.trim() };
      }
      if (a.type === "check") {
        if (el.checked !== !!a.value) el.click();
        return { ok: el.checked === !!a.value };
      }
      return { ok: false, error: "UNKNOWN_ACTION" };
    } catch (e) {
      return { ok: false, error: "EXCEPTION", message: String(e && e.message || e).slice(0, 200) };
    }
  }

  // ---- highlight and manual-control banner ---------------------------------------------------------------------
  function highlight(handle, on = true) {
    document.querySelectorAll("[data-sg-highlight]").forEach((n) => { n.style.outline = n.dataset.sgOutline || ""; n.removeAttribute("data-sg-highlight"); });
    const el = handle ? element(handle) : null;
    if (el && on) { el.dataset.sgOutline = el.style.outline; el.style.outline = "3px solid #22d3ee"; el.setAttribute("data-sg-highlight", "1"); }
  }

  function banner(text) {
    let b = document.getElementById("__sg_banner");
    if (!text) { if (b) b.remove(); return; }
    if (!b) {
      b = document.createElement("div");
      b.id = "__sg_banner";
      b.setAttribute("role", "status");
      b.style.cssText = "position:fixed;z-index:2147483647;left:50%;top:8px;transform:translateX(-50%);background:#0b1f3a;color:#e6f7ff;border:2px solid #22d3ee;border-radius:8px;padding:8px 14px;font:14px system-ui;box-shadow:0 4px 18px rgba(0,0,0,.35);max-width:80vw;animation:sgblink 1.2s step-start infinite";
      const style = document.createElement("style");
      style.textContent = "@keyframes sgblink{50%{border-color:#f59e0b}}";
      b.appendChild(style);
      b.appendChild(document.createElement("span"));
      document.documentElement.appendChild(b);
    }
    b.querySelector("span").textContent = text; // textContent: never interpreted as HTML
  }

  // ---- guided learning observer ----------------------------------------------------------------------------
  let observer = null;
  function observe(callback) {
    stopObserving();
    const target = (e) => {
      const path = e.composedPath ? e.composedPath() : [e.target];
      return path.find((n) => n.nodeType === 1 && n.matches && n.matches(INTERACTIVE)) || e.target;
    };
    const send = (kind, el, extra = {}) => {
      if (!el || el.nodeType !== 1 || el.id === "__sg_banner" || el.closest("#__sg_banner")) return;
      const d = describe(el);
      callback({ kind, at: Date.now(), url: location.href, title: clean(document.title, 120), heading: clean((document.querySelector("h1,h2") || {}).innerText, 120), element: d, ...extra });
    };
    const onClick = (e) => { const el = target(e); if (["input", "select", "textarea"].includes(el.tagName.toLowerCase()) && !["checkbox", "radio", "submit", "button", "image"].includes(el.type)) return; send("click", el); };
    const onChange = (e) => { const el = target(e); const t = el.tagName.toLowerCase(); if (!["input", "select", "textarea"].includes(t)) return;
      const secret = isSecretField(el);
      const value = secret ? "[secret]" : t === "select" ? clean(el.selectedOptions[0]?.text, 120) : ["checkbox", "radio"].includes(el.type) ? String(el.checked) : clean(el.value, 200);
      send(t === "select" ? "select" : ["checkbox", "radio"].includes(el.type) ? "check" : "fill", el, { value, secret }); };
    const onSubmit = (e) => send("submit", e.target);
    document.addEventListener("click", onClick, true);
    document.addEventListener("change", onChange, true);
    document.addEventListener("submit", onSubmit, true);
    observer = () => { document.removeEventListener("click", onClick, true); document.removeEventListener("change", onChange, true); document.removeEventListener("submit", onSubmit, true); };
    return true;
  }
  function stopObserving() { if (observer) observer(); observer = null; return true; }

  window.__sgAgent = { snapshot, act, element, highlight, banner, observe, stopObserving, describe, version: "1.0.0" };
})();
