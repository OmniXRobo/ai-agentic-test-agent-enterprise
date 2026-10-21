// Element fingerprints and stable selectors. Injected at runtime; never modifies the application.
(() => {
  if (window.__qaFp) return;
  const clean = (s, n = 80) => (s || "").replace(/\s+/g, " ").trim().slice(0, n);
  const looksGenerated = (v) => !v || /\d{4,}/.test(v) || /^[0-9]/.test(v) || /[a-f0-9]{8,}/i.test(v) || v.length > 60;
  const q = (v) => JSON.stringify(v);
  const unique = (sel) => { try { return document.querySelectorAll(sel).length === 1; } catch (e) { return false; } };

  function implicitRole(el) {
    const t = el.tagName.toLowerCase();
    if (el.getAttribute("role")) return el.getAttribute("role");
    if (t === "button" || (t === "input" && ["button", "submit", "reset"].includes(el.type))) return "button";
    if (t === "a" && el.hasAttribute("href")) return "link";
    if (t === "input" && el.type === "checkbox") return "checkbox";
    if (t === "input" && el.type === "radio") return "radio";
    if (t === "select") return "combobox";
    if (t === "textarea" || (t === "input" && !["hidden", "file", "image", "range", "color"].includes(el.type))) return "textbox";
    return null;
  }

  function labelOf(el) {
    if (el.id) {
      const l = document.querySelector(`label[for=${q(el.id)}]`);
      if (l) return clean(l.textContent);
    }
    const parent = el.closest("label");
    if (parent) return clean(parent.textContent);
    const by = el.getAttribute("aria-labelledby");
    if (by) {
      const l = document.getElementById(by.split(" ")[0]);
      if (l) return clean(l.textContent);
    }
    return null;
  }

  function accessibleName(el) {
    return clean(el.getAttribute("aria-label") || labelOf(el) || (["input"].includes(el.tagName.toLowerCase()) ? (el.value && ["button", "submit"].includes(el.type) ? el.value : el.placeholder) : el.innerText) || el.title || el.alt || "");
  }

  function cssPath(el) {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 6) {
      let part = node.tagName.toLowerCase();
      if (node.id && !looksGenerated(node.id)) { parts.unshift(`#${CSS.escape(node.id)}`); break; }
      const parent = node.parentElement;
      if (parent) {
        const same = [...parent.children].filter((c) => c.tagName === node.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(" > ");
  }

  function selectors(el) {
    const t = el.tagName.toLowerCase();
    const out = [];
    for (const a of ["data-testid", "data-test", "data-qa", "data-cy"]) {
      const v = el.getAttribute(a);
      if (v) out.push(`[${a}=${q(v)}]`);
    }
    if (el.id && !looksGenerated(el.id)) out.push(`#${CSS.escape(el.id)}`);
    const name = el.getAttribute("name");
    if (name && !looksGenerated(name)) out.push(`${t}[name=${q(name)}]`);
    const aria = el.getAttribute("aria-label");
    if (aria) out.push(`${t}[aria-label=${q(aria)}]`);
    const ph = el.getAttribute("placeholder");
    if (ph) out.push(`${t}[placeholder=${q(ph)}]`);
    const role = implicitRole(el);
    const acc = accessibleName(el);
    const cssUnique = out.filter(unique);
    const result = [...cssUnique];
    if (role && acc && acc.length <= 60) result.push(`role=${role}[name=${q(acc)}]`);
    if (["a", "button"].includes(t) && clean(el.innerText, 60)) result.push(`text=${q(clean(el.innerText, 60))}`);
    const path = cssPath(el);
    if (unique(path)) result.push(path);
    return [...new Set(result)];
  }

  function fingerprint(el) {
    const sels = selectors(el);
    return {
      selector: sels[0] || cssPath(el),
      alternates: sels.slice(1, 5),
      tag: el.tagName.toLowerCase(),
      // Field values are user data (and may be passwords); only button-like inputs expose their value as text.
      text: clean(el.tagName === "INPUT" ? (["button", "submit", "reset"].includes(el.type) ? el.value : "") : el.innerText, 120) || null,
      role: implicitRole(el),
      name: accessibleName(el) || null,
      element_id: el.id || null,
      test_id: el.getAttribute("data-testid") || el.getAttribute("data-test") || el.getAttribute("data-qa") || null,
      label: labelOf(el),
      placeholder: el.getAttribute("placeholder"),
      input_type: el.tagName.toLowerCase() === "input" ? el.type : null,
      classes: [...el.classList].filter((c) => !looksGenerated(c)).slice(0, 6),
      href: el.getAttribute("href"),
    };
  }

  function visible(el) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
  }

  const INTERACTIVE = "a,button,input,select,textarea,label,summary,[role],[onclick],[tabindex],[contenteditable=true],[data-testid],[data-test],[data-qa]";

  function candidates(limit = 600) {
    const out = [];
    for (const el of document.querySelectorAll(INTERACTIVE)) {
      if (el.closest("#__qa_overlay_host")) continue;
      // A label tied to a control duplicates that control; the control itself is the candidate.
      if (el.tagName === "LABEL" && (el.control || el.htmlFor)) continue;
      if (!visible(el)) continue;
      out.push(fingerprint(el));
      if (out.length >= limit) break;
    }
    return out;
  }

  window.__qaFp = { fingerprint, candidates, selectors, visible, accessibleName, implicitRole };
})();
