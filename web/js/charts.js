// Small single-series SVG line charts with a crosshair tooltip. One measure per chart (no dual axes);
// the chart title names the series, so no legend box is needed.

const NS = "http://www.w3.org/2000/svg";

function el(name, attrs = {}) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function fmt(v) {
  if (Math.abs(v) >= 1000) return `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k`;
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

export function fmtDuration(s) {
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60), r = s % 60;
  if (m < 60) return r ? `${m}m ${r}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

/**
 * @param {HTMLElement} host
 * @param {{t:number}[]} points  timeline points, t = seconds from start
 * @param {{key:string, title:string, unit:string, color?:string, integer?:boolean,
 *          xLabel?:(t:number)=>string, tipLabel?:(i:number)=>string}} opts
 *   xLabel formats axis ticks (default: elapsed time); tipLabel names a point in the tooltip.
 */
export function lineChart(host, points, { key, title, unit, color = "var(--accent)", integer = false, xLabel = fmtDuration, tipLabel = null }) {
  host.classList.add("chart");
  host.innerHTML = "";
  const head = document.createElement("div");
  head.className = "chart-title";
  head.textContent = title;
  host.append(head);
  if (!points.length) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No data.";
    host.append(p);
    return;
  }
  const W = 380, H = 160, L = 40, R = 10, T = 8, B = 22;
  const xs = points.map((p) => p.t), ys = points.map((p) => Number(p[key]) || 0);
  const xMax = Math.max(1, xs[xs.length - 1]);
  const yMax = integer ? Math.max(4, Math.ceil(niceMax(Math.max(...ys)) / 4) * 4) : niceMax(Math.max(...ys));
  const x = (t) => L + (t / xMax) * (W - L - R);
  const y = (v) => T + (1 - v / yMax) * (H - T - B);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${title} over time` });
  for (let i = 0; i <= 4; i++) {
    const v = (yMax / 4) * i;
    svg.append(el("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" }));
    const lbl = el("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end", class: "axis" });
    lbl.textContent = fmt(v);
    svg.append(lbl);
  }
  // at most five ticks; duplicates (few runs) and repeated labels are dropped so they never overlap
  const ticks = [...new Set([0, 1, 2, 3, 4].map((i) => Math.round((xMax / 4) * i)))];
  let lastText = null;
  ticks.forEach((t, i) => {
    const text = xLabel(t);
    if (text === lastText) return;
    lastText = text;
    const lbl = el("text", { x: x(t), y: H - 6, "text-anchor": i === 0 ? "start" : i === ticks.length - 1 ? "end" : "middle", class: "axis" });
    lbl.textContent = text;
    svg.append(lbl);
  });
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(ys[i]).toFixed(1)}`).join("");
  svg.append(el("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  if (points.length <= 40) {
    // few points (one per run): mark each so single values are visible
    points.forEach((p, i) => svg.append(el("circle", { cx: x(p.t), cy: y(ys[i]), r: 3, fill: color })));
  }
  const hair = el("line", { y1: T, y2: H - B, class: "hair", visibility: "hidden" });
  const dot = el("circle", { r: 4, fill: color, stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" });
  svg.append(hair, dot);
  const hit = el("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent", tabindex: 0 });
  svg.append(hit);
  host.append(svg);
  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.hidden = true;
  const tv = document.createElement("strong"), tl = document.createElement("span");
  tip.append(tv, tl);
  host.append(tip);
  const show = (i) => {
    const px = x(points[i].t), py = y(ys[i]);
    hair.setAttribute("x1", px); hair.setAttribute("x2", px); hair.setAttribute("visibility", "visible");
    dot.setAttribute("cx", px); dot.setAttribute("cy", py); dot.setAttribute("visibility", "visible");
    tv.textContent = `${fmt(ys[i])} ${unit}`;
    tl.textContent = tipLabel ? ` ${tipLabel(i)}` : ` at ${fmtDuration(points[i].t)}`;
    tip.hidden = false;
    const box = svg.getBoundingClientRect(), scale = box.width / W;
    tip.style.left = `${Math.min(px * scale + 10, box.width - tip.offsetWidth - 4)}px`;
    tip.style.top = `${Math.max(0, py * scale - 34) + head.offsetHeight}px`;
  };
  const hide = () => { hair.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.hidden = true; };
  let current = points.length - 1;
  hit.addEventListener("pointermove", (e) => {
    const box = svg.getBoundingClientRect();
    const t = ((e.clientX - box.left) / box.width * W - L) / (W - L - R) * xMax;
    let best = 0;
    for (let i = 1; i < points.length; i++) if (Math.abs(points[i].t - t) < Math.abs(points[best].t - t)) best = i;
    current = best;
    show(best);
  });
  hit.addEventListener("pointerleave", hide);
  hit.addEventListener("focus", () => show(current));
  hit.addEventListener("blur", hide);
  hit.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") current = Math.max(0, current - 1);
    else if (e.key === "ArrowRight") current = Math.min(points.length - 1, current + 1);
    else return;
    e.preventDefault();
    show(current);
  });
}

/**
 * Bars per category (for example per day), stacked when there is more than one series, with a hover tooltip per bar.
 * @param {HTMLElement} host
 * @param {object[]} rows
 * @param {{title:string, label:(row:object)=>string, series:{key:string,label:string,color:string}[], unit?:string}} opts
 */
export function barChart(host, rows, { title, label, series, unit = "" }) {
  host.classList.add("chart");
  host.innerHTML = "";
  const head = document.createElement("div");
  head.className = "chart-title";
  head.textContent = title;
  host.append(head);
  const totals = rows.map((r) => series.reduce((n, s) => n + (Number(r[s.key]) || 0), 0));
  if (!rows.length || !totals.some((t) => t > 0)) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No data in this period.";
    host.append(p);
    return;
  }
  const W = 380, H = 160, L = 40, R = 10, T = 8, B = 22;
  const yMax = Math.max(4, Math.ceil(Math.max(...totals) / 4) * 4);
  const slot = (W - L - R) / rows.length;
  const bw = Math.max(2, Math.min(24, slot - 2));
  const y = (v) => T + (1 - v / yMax) * (H - T - B);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": title });
  for (let i = 0; i <= 4; i++) {
    const v = (yMax / 4) * i;
    svg.append(el("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" }));
    const lbl = el("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end", class: "axis" });
    lbl.textContent = fmt(v);
    svg.append(lbl);
  }
  const n = rows.length;
  const ticks = n > 5 ? [...new Set([0, 1, 2, 3, 4].map((i) => Math.round((i * (n - 1)) / 4)))] : rows.map((_, i) => i);
  for (const i of ticks) {
    const lbl = el("text", { x: L + slot * (i + 0.5), y: H - 6, "text-anchor": n > 1 && i === 0 ? "start" : n > 1 && i === n - 1 ? "end" : "middle", class: "axis" });
    lbl.textContent = label(rows[i]);
    svg.append(lbl);
  }
  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.hidden = true;
  rows.forEach((r, i) => {
    const cx = L + slot * (i + 0.5);
    let base = H - B;
    for (const s of series) {
      const v = Number(r[s.key]) || 0;
      if (v <= 0) continue;
      const h = (v / yMax) * (H - T - B);
      // 2px surface gap between stacked segments
      svg.append(el("rect", { x: cx - bw / 2, y: base - h, width: bw, height: Math.max(0, h - 2), rx: 2, fill: s.color }));
      base -= h;
    }
    const hit = el("rect", { x: L + slot * i, y: T, width: slot, height: H - T - B, fill: "transparent", tabindex: 0 });
    const show = () => {
      tip.innerHTML = `<strong>${label(r)}</strong> <span>${series.map((s) => `${fmt(Number(r[s.key]) || 0)}${unit} ${s.label.toLowerCase()}`).join(", ")}</span>`;
      tip.hidden = false;
      const box = svg.getBoundingClientRect(), scale = box.width / W;
      tip.style.left = `${Math.max(0, Math.min(cx * scale - tip.offsetWidth / 2, box.width - tip.offsetWidth - 4))}px`;
      tip.style.top = `${head.offsetHeight}px`;
    };
    hit.addEventListener("pointerenter", show);
    hit.addEventListener("focus", show);
    hit.addEventListener("pointerleave", () => { tip.hidden = true; });
    hit.addEventListener("blur", () => { tip.hidden = true; });
    svg.append(hit);
  });
  host.append(svg, tip);
  if (series.length > 1) {
    const legend = document.createElement("div");
    legend.className = "legend";
    legend.innerHTML = series.map((s) => `<span><i style="background:${s.color}"></i>${s.label}</span>`).join("");
    host.append(legend);
  }
}
