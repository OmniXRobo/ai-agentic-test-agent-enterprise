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
 * @param {{key:string, title:string, unit:string, color?:string, integer?:boolean}} opts
 */
export function lineChart(host, points, { key, title, unit, color = "var(--accent)", integer = false }) {
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
  for (let i = 0; i <= 4; i++) {
    const t = Math.round((xMax / 4) * i);
    const lbl = el("text", { x: x(t), y: H - 6, "text-anchor": i === 0 ? "start" : i === 4 ? "end" : "middle", class: "axis" });
    lbl.textContent = fmtDuration(t);
    svg.append(lbl);
  }
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(ys[i]).toFixed(1)}`).join("");
  svg.append(el("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
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
    tl.textContent = ` at ${fmtDuration(points[i].t)}`;
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
