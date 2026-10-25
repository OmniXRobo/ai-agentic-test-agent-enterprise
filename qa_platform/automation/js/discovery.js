// Application Discovery helpers: authentication screens, explorable controls and page structure.
// Injected at runtime next to fingerprint.js; reads the DOM only and never changes the application.
(() => {
  if (window.__qaDisc) return;
  const fp = () => window.__qaFp;
  const clean = (s, n = 80) => (s || "").replace(/\s+/g, " ").trim().slice(0, n);
  const OTP = /\b(otp|one[\s_-]?time|verification[\s_-]?code|auth(entication)?[\s_-]?code|security[\s_-]?code|2fa|mfa|two[\s_-]?factor|passcode|token|totp|sms[\s_-]?code)\b/i;
  const USER = /(user|email|e-mail|login|account|member|customer)/i;
  const SUBMIT = /^(log ?in|sign ?in|login|continue|next|submit|verify|proceed|ok|go)\b/i;

  const visible = (el) => fp() && fp().visible(el);
  const sel = (el) => {
    const s = fp().selectors(el);
    return s[0] || null;
  };

  function labelFor(el) {
    const f = fp();
    return clean(el.getAttribute("aria-label") || (el.labels && el.labels[0] && el.labels[0].textContent)
      || el.getAttribute("placeholder") || el.getAttribute("name") || el.id || (f && f.accessibleName(el)) || "", 60);
  }

  function describeField(el) {
    const label = labelFor(el);
    const hint = [label, el.name, el.id, el.getAttribute("autocomplete"), el.getAttribute("placeholder")].join(" ");
    return {
      selector: sel(el), label, name: el.name || el.id || null, type: el.type || el.tagName.toLowerCase(),
      required: !!el.required || el.getAttribute("aria-required") === "true",
      empty: !el.value, otp: el.getAttribute("autocomplete") === "one-time-code" || OTP.test(hint),
    };
  }

  function textInputs(root) {
    return [...root.querySelectorAll("input,textarea,select")].filter((el) => visible(el)
      && !["hidden", "submit", "button", "reset", "checkbox", "radio", "file", "image", "range", "color"].includes(el.type)
      && !el.disabled && !el.readOnly);
  }

  function submitIn(root) {
    const buttons = [...root.querySelectorAll("button,input[type=submit],input[type=button],[role=button]")].filter(visible);
    const text = (b) => clean(b.tagName === "INPUT" ? b.value : (b.innerText || b.getAttribute("aria-label")), 40);
    return buttons.find((b) => b.type === "submit" && SUBMIT.test(text(b)))
      || buttons.find((b) => SUBMIT.test(text(b)))
      || buttons.find((b) => b.type === "submit") || null;
  }

  function captcha() {
    if (document.querySelector(".g-recaptcha,.h-captcha,.cf-turnstile,[data-sitekey],#captcha,[name=captcha]")) return true;
    return [...document.querySelectorAll("iframe")].some((f) => /recaptcha|hcaptcha|turnstile|captcha/i.test(f.src || f.title || ""));
  }

  function errorText() {
    const nodes = document.querySelectorAll("[role=alert],.error,.error-message,.alert-danger,.alert-error,[data-test=error],.invalid-feedback");
    for (const n of nodes) {
      const t = clean(n.innerText, 160);
      if (t && visible(n)) return t;
    }
    return null;
  }

  // What kind of screen is this: captcha, login (password field), otp (code field) or the application itself.
  function auth() {
    const pw = [...document.querySelectorAll("input[type=password]")].find(visible);
    const inputs = textInputs(document);
    const otpField = inputs.find((el) => describeField(el).otp && el.type !== "password");
    const out = { kind: "app", captcha: captcha(), error: errorText(), fields: [], submit: null };
    if (!pw && !otpField) {
      if (out.captcha) out.kind = "captcha";
      return out;
    }
    const anchor = pw || otpField;
    const root = anchor.form || anchor.closest("form,[role=form],main,section,div") || document;
    const fields = textInputs(root).map(describeField);
    if (pw) {
      out.kind = "login";
      const pwSel = sel(pw);
      const before = textInputs(root).filter((el) => el !== pw && el.type !== "password"
        && (el.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING));
      const user = before.reverse().find((el) => USER.test(labelFor(el) + " " + (el.name || "") + " " + (el.id || "") + " " + el.type))
        || before[0] || null;
      const userSel = user ? sel(user) : null;
      out.fields = fields.map((f) => ({ ...f, role: f.selector === pwSel ? "password" : f.selector === userSel ? "username" : (f.otp ? "otp" : "extra") }));
    } else {
      out.kind = "otp";
      out.fields = fields.map((f) => ({ ...f, role: f.otp ? "otp" : "extra" }));
    }
    if (out.captcha) out.kind = "captcha";
    const btn = submitIn(root) || submitIn(document);
    out.submit = btn ? sel(btn) : null;
    return out;
  }

  // Controls worth clicking to find more of the application (links without a real target, buttons, menu toggles).
  function clickables(limit = 60) {
    const out = [];
    const seen = new Set();
    const els = document.querySelectorAll("a,button,[role=button],[role=link],[role=menuitem],[role=tab],summary,input[type=button]");
    for (const el of els) {
      if (out.length >= limit) break;
      if (!visible(el) || el.disabled || el.closest("#__qa_overlay_host")) continue;
      const href = el.getAttribute("href");
      const tag = el.tagName.toLowerCase();
      // Real links are crawled by URL; only script-driven ones need a click.
      if (tag === "a" && href && !/^(#|javascript:)/i.test(href.trim())) continue;
      if (el.form && tag !== "a") continue;  // never submit or reset business forms
      const s = sel(el);
      if (!s || seen.has(s)) continue;
      seen.add(s);
      const text = clean(el.innerText || el.value || el.getAttribute("aria-label") || el.title || el.id || "", 60);
      out.push({ selector: s, text, tag, role: fp().implicitRole(el) || el.getAttribute("role"),
        menu: !!el.closest("nav,[role=navigation],[role=menu],[role=menubar],aside,.menu,.sidebar,.bm-menu"),
        haspopup: el.getAttribute("aria-haspopup") || el.getAttribute("aria-expanded") !== null });
    }
    return out;
  }

  function structure() {
    const menus = [...document.querySelectorAll("nav,[role=navigation],[role=menu],[role=menubar],aside")].filter(visible).slice(0, 10)
      .map((m) => ({ name: clean(m.getAttribute("aria-label") || m.id || m.className, 60),
        items: [...m.querySelectorAll("a,button,[role=menuitem]")].filter(visible).slice(0, 40).map((a) => clean(a.innerText || a.getAttribute("aria-label"), 50)).filter(Boolean) }));
    const dialogs = [...document.querySelectorAll("[role=dialog],[role=alertdialog],dialog[open],.modal.show,.modal[style*='block']")].filter(visible)
      .map((d) => ({ title: clean((d.querySelector("h1,h2,h3,.modal-title") || d).innerText, 80), selector: sel(d) }));
    const tables = [...document.querySelectorAll("table,[role=grid],[role=table]")].filter(visible).slice(0, 10)
      .map((t) => ({ selector: sel(t), headers: [...t.querySelectorAll("th,[role=columnheader]")].slice(0, 20).map((h) => clean(h.innerText, 40)),
        rows: t.querySelectorAll("tbody tr,[role=row]").length }));
    const headings = [...document.querySelectorAll("h1,h2,h3,[role=heading],.title,.header_secondary_container .title")].filter(visible)
      .slice(0, 12).map((h) => clean(h.innerText, 80)).filter(Boolean);
    return { menus, dialogs, tables, headings };
  }

  window.__qaDisc = { auth, clickables, structure, describeField };
})();
