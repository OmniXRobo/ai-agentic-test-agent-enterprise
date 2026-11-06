// 23.3.30: product name, tagline and logo from Preferences, shown in the header and browser tab titles.

import { api } from "./api.js";

const DEFAULT = { name: "SkyGuard QA", tagline: "Every release, cleared for takeoff.", logo_url: null };
let current = { ...DEFAULT };
const listeners = new Set();

export function productName() { return current.name || DEFAULT.name; }
export function brand() { return current; }
export function onBrand(fn) { listeners.add(fn); return () => listeners.delete(fn); }

function render() {
  const title = document.getElementById("brandTitle");
  const sub = document.getElementById("brandSub");
  const custom = document.getElementById("brandLogoImg");
  const emblem = document.getElementById("brandEmblem");
  if (title) title.textContent = productName();
  if (sub) { sub.textContent = current.tagline || ""; sub.hidden = !current.tagline; }
  if (custom && emblem) {
    if (current.logo_url) {
      custom.onerror = () => { custom.hidden = true; emblem.removeAttribute("hidden"); };
      custom.src = current.logo_url;
      custom.alt = `${productName()} logo`;
      custom.hidden = false;
      emblem.setAttribute("hidden", "");
    } else {
      custom.hidden = true;
      custom.removeAttribute("src");
      emblem.removeAttribute("hidden");
    }
  }
  listeners.forEach((fn) => { try { fn(current); } catch { /* a listener never breaks the header */ } });
}

/** Reads the saved branding (after start and after Preferences are saved). */
export async function loadBrand() {
  try {
    const b = await api("/api/branding");
    current = { name: b.name || DEFAULT.name, tagline: b.tagline ?? DEFAULT.tagline, logo_url: b.logo_url || null,
                logo_problem: b.logo_problem || null };
  } catch { /* keep what is shown */ }
  render();
  return current;
}
