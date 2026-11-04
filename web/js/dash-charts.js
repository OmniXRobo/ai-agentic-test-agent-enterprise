// Small dashboard charts (23.3.14): donut (pie), stacked day columns, horizontal stacked bars and progress rows.
// Plain inline SVG/HTML, no library, so they work offline. Every chart takes real counts only; with nothing to
// show it says so instead of drawing an empty or made-up figure.

import { esc } from "./util.js";

const pctOf = (part, whole) => (whole ? Math.round((1000 * part) / whole) / 10 : 0);

/**
 * Donut chart with a legend that carries the numbers.
 * @param {{label:string, value:number, color:string, href?:string}[]} parts
 * @param {{center?:string, sub?:string, empty?:string}} opts
 */
export function donut(parts, { center = null, sub = "", empty = "No data yet" } = {}) {
  const total = parts.reduce((n, p) => n + (Number(p.value) || 0), 0);
  let offset = 0;
  const arcs = total ? parts.filter((p) => p.value > 0).map((p) => {
    const len = (100 * p.value) / total;
    const gap = len >= 100 ? 0 : Math.min(0.8, len / 4);
    const arc = `<circle cx="60" cy="60" r="44" pathLength="100" fill="none" stroke="${p.color}" stroke-width="15"
      stroke-dasharray="${(len - gap).toFixed(3)} ${(100 - len + gap).toFixed(3)}" stroke-dashoffset="${(-offset).toFixed(3)}"
      transform="rotate(-90 60 60)" class="dc-arc"><title>${esc(p.label)}: ${p.value} (${pctOf(p.value, total)}%)</title></circle>`;
    offset += len;
    return arc;
  }).join("") : "";
  const legend = parts.map((p) => {
    const row = `<span class="dc-sw" style="background:${p.color}"></span><span class="dc-lab">${esc(p.label)}</span>
      <b>${esc(p.value ?? "—")}</b><small>${total && p.value != null ? `${pctOf(p.value, total)}%` : ""}</small>`;
    return p.href ? `<a class="dc-row" href="${p.href}">${row}</a>` : `<div class="dc-row">${row}</div>`;
  }).join("");
  return `<div class="donut">
    <svg viewBox="0 0 120 120" class="dc-svg" role="img" aria-label="${esc(parts.map((p) => `${p.label} ${p.value}`).join(", "))}">
      <circle cx="60" cy="60" r="44" fill="none" class="dc-track" stroke-width="15"/>
      ${arcs}
      <text x="60" y="${sub ? 60 : 66}" text-anchor="middle" class="dc-big"${String(total ? (center ?? total) : "—").length > 4 ? ' style="font-size:16px"' : ""}>${esc(total ? (center ?? total) : "—")}</text>
      ${sub ? `<text x="60" y="76" text-anchor="middle" class="dc-sub">${esc(total ? sub : empty)}</text>` : ""}
    </svg>
    <div class="dc-legend">${legend}${total ? "" : `<p class="muted small">${esc(empty)}</p>`}</div>
  </div>`;
}

/**
 * Stacked columns per day (for example passed and failed executions).
 * @param {{date:string, [k:string]:any}[]} days
 * @param {{key:string, label:string, color:string}[]} series
 */
export function dayColumns(days, series, { empty = "Nothing ran in this period" } = {}) {
  const totals = days.map((d) => series.reduce((n, s) => n + (d[s.key] || 0), 0));
  const max = Math.max(1, ...totals);
  const W = 320, H = 150, top = 16, base = 124, bw = Math.min(30, (W - 20) / days.length - 10);
  const step = (W - 20) / days.length;
  const cols = days.map((d, i) => {
    const x = 10 + i * step + (step - bw) / 2;
    let y = base;
    const rects = series.map((s) => {
      const v = d[s.key] || 0;
      if (!v) return "";
      const h = ((base - top) * v) / max;
      y -= h;
      return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" fill="${s.color}" rx="2"><title>${esc(s.label)}: ${v}</title></rect>`;
    }).join("");
    const day = new Date(`${d.date}T00:00:00`);
    const lab = day.toLocaleDateString([], { weekday: "short" });
    return `${rects}${totals[i] ? `<text x="${(x + bw / 2).toFixed(1)}" y="${(y - 4).toFixed(1)}" text-anchor="middle" class="dc-val">${totals[i]}</text>` : ""}
      <text x="${(x + bw / 2).toFixed(1)}" y="${base + 16}" text-anchor="middle" class="dc-axis">${esc(lab)}</text>`;
  }).join("");
  const any = totals.some(Boolean);
  return `<div class="cols">
    <svg viewBox="0 0 ${W} ${H}" class="cols-svg" role="img" aria-label="${esc(series.map((s) => s.label).join(" and "))} per day">
      <path d="M6 ${base}H${W - 6}" class="dc-base"/>${cols}
      ${any ? "" : `<text x="${W / 2}" y="${base / 2 + 10}" text-anchor="middle" class="dc-sub">${esc(empty)}</text>`}
    </svg>
    <div class="legend-inline">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join("")}</div>
  </div>`;
}

/**
 * Horizontal stacked bars, one row per item.
 * @param {{label:string, parts:{value:number,color:string,label:string}[], href?:string}[]} rows
 */
export function hbars(rows, { empty = "No data yet" } = {}) {
  if (!rows.length) return `<p class="muted small">${esc(empty)}</p>`;
  const max = Math.max(1, ...rows.map((r) => r.parts.reduce((n, p) => n + p.value, 0)));
  return `<div class="hbars">${rows.map((r) => {
    const tot = r.parts.reduce((n, p) => n + p.value, 0);
    const segs = r.parts.filter((p) => p.value > 0).map((p) =>
      `<span style="width:${((100 * p.value) / max).toFixed(2)}%;background:${p.color}" title="${esc(p.label)}: ${p.value}"></span>`).join("");
    const inner = `<span class="hb-lab" title="${esc(r.label)}">${esc(r.label)}</span><span class="hb-track">${segs}</span><b class="hb-val">${tot}</b>`;
    return r.href ? `<a class="hb-row" href="${r.href}">${inner}</a>` : `<div class="hb-row">${inner}</div>`;
  }).join("")}</div>`;
}

/** One progress row: label, "x of y", bar and percentage. Unknown totals show "—". */
export function progressRow(label, part, whole, { color = "var(--accent)", note = "", href = "" } = {}) {
  const known = whole !== null && whole !== undefined && part !== null && part !== undefined;
  const p = known ? pctOf(Math.min(part, whole), whole) : null;
  const inner = `<div class="pr-head"><span class="pr-lab">${esc(label)}</span><span class="pr-num">${known ? `${part} of ${whole}` : "—"}</span>
      <b class="pr-pct">${p === null || !whole ? "—" : `${p}%`}</b></div>
    <div class="pr-track"><span style="width:${p || 0}%;background:${color}"></span></div>
    ${note ? `<div class="pr-note">${esc(note)}</div>` : ""}`;
  return href ? `<a class="pr-row" href="${href}">${inner}</a>` : `<div class="pr-row">${inner}</div>`;
}
