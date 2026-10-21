export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Timestamps come from the host system clock; display them in the browser's local time.
export function timeOf(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleTimeString([], { hour12: false });
}

export function dateTimeOf(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString([], { hour12: false });
}

export function elapsed(fromIso, toIso) {
  if (!fromIso) return "";
  const end = toIso ? new Date(toIso) : new Date();
  let s = Math.max(0, Math.round((end - new Date(fromIso)) / 1000));
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60); s -= m * 60;
  return h ? `${h}h ${m}m ${s}s` : m ? `${m}m ${s}s` : `${s}s`;
}

export function toast(message, kind = "info", ms = 4500) {
  const host = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

export function h(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

// TFS rich-text fields are HTML. Keep basic formatting only; drop scripts, styles, handlers and images.
const SAFE_TAGS = new Set(["P", "BR", "UL", "OL", "LI", "B", "STRONG", "I", "EM", "U", "DIV", "SPAN", "TABLE", "THEAD",
  "TBODY", "TR", "TD", "TH", "H1", "H2", "H3", "H4", "CODE", "PRE", "A", "BLOCKQUOTE"]);

export function safeHtml(html) {
  if (!html) return "";
  const doc = new DOMParser().parseFromString(String(html), "text/html");
  const walk = (node) => {
    for (const child of [...node.children]) {
      if (["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "IMG", "SVG", "TEMPLATE"].includes(child.tagName)) {
        child.remove();
        continue;
      }
      walk(child); // sanitize the subtree first, so unwrapping below only moves clean nodes
      if (!SAFE_TAGS.has(child.tagName)) {
        child.replaceWith(...child.childNodes);
        continue;
      }
      for (const attr of [...child.attributes]) {
        const keep = child.tagName === "A" && attr.name === "href" && /^https?:/i.test(attr.value);
        if (!keep) child.removeAttribute(attr.name);
      }
      if (child.tagName === "A") { child.setAttribute("target", "_blank"); child.setAttribute("rel", "noopener noreferrer"); }
    }
  };
  walk(doc.body);
  return doc.body.innerHTML;
}

export function debounce(fn, ms = 300) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}
