// Agent Workflow, Classic Squadron look (Preferences > Agent Workflow look): Air Force squadron cards driven only by runtime events, plus Live Agent Activity,
// on a tactical defense backdrop (23.3.14), under an Air Force scene at the top of the page (23.3.30).

import { setCardDeco } from "../../agent-card.js";
import { counts, feedHtml, gridHtml, wire, EXPLAIN } from "./common.js";
import { esc } from "../../util.js";

let cleanup = null;
export const css = null; // the Classic look uses defense.css, always loaded

// Fighter silhouette (top view), drawn in a 100 x 100 box pointing up.
const JET = "M50 2 54 30 96 56v8L54 52l-2 26 13 9v5l-15-4-15 4v-5l13-9-2-26L4 64v-8l42-26z";

// Tactical backdrop: radar rings and grid, HUD ladder, three fighter silhouettes and a sweeping radar.
// Decorative only (aria-hidden) and drawn behind the cards.
function backdropHtml() {
  const jets = [[78, 12, 0.9, 35], [86, 30, 0.6, 35], [70, 30, 0.6, 35]]
    .map(([x, y, s, r]) => `<path d="${JET}" transform="translate(${x * 12} ${y * 8}) rotate(${r}) scale(${s})" class="wf-jet"/>`).join("");
  const rings = [90, 180, 270, 360].map((r) => `<circle cx="1000" cy="160" r="${r}" class="wf-ring"/>`).join("");
  return `<div class="wf-backdrop" aria-hidden="true"><div class="wf-stick">
    <svg viewBox="0 0 1200 800" preserveAspectRatio="xMidYMid slice">
      <defs><pattern id="wfGrid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" class="wf-grid"/></pattern></defs>
      <rect width="1200" height="800" fill="url(#wfGrid)"/>
      ${rings}<path d="M1000 -200V520M640 160h720" class="wf-ring"/>
      <path d="M0 760h1200" class="wf-hud"/><text x="1180" y="785" text-anchor="end" class="wf-hudtxt">ALT 32000 · HDG 045 · SQN QA-1 · DEFENSE GRID ONLINE</text>
      ${jets}
    </svg>
    <div class="wf-sweep"></div>
  </div></div>`;
}

// 23.3.30: the Air Force scene at the top of the page, above the cards, where it is not covered: a formation of
// fighters crossing the sky, radar rings with a sweep and blips, and the squadron status strip.
function heroHtml() {
  const jet = (x, y, s) => `<path d="${JET}" transform="translate(${x} ${y}) rotate(90 ${50 * s} ${50 * s}) scale(${s})" class="wf-hero-jet"/>`;
  const rings = [36, 72, 108, 144].map((r) => `<circle cx="1050" cy="86" r="${r}" class="wf-hero-ring"/>`).join("");
  return `<div class="wf-hero">
    <svg class="wf-hero-art" viewBox="0 0 1200 172" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <defs><pattern id="wfHeroGrid" width="32" height="32" patternUnits="userSpaceOnUse"><path d="M32 0H0V32" class="wf-grid"/></pattern>
        <linearGradient id="wfTrail" x1="0" x2="1"><stop offset="0" stop-color="#38d6ff" stop-opacity="0"/><stop offset="1" stop-color="#38d6ff" stop-opacity=".55"/></linearGradient></defs>
      <rect width="1200" height="172" fill="url(#wfHeroGrid)"/>
      ${rings}<path d="M1050 -70V242M890 86h320" class="wf-hero-ring"/>
      <circle cx="1002" cy="52" r="3" class="wf-blip"/><circle cx="1110" cy="120" r="3" class="wf-blip b2"/><circle cx="1078" cy="34" r="3" class="wf-blip b3"/>
      <path d="M0 160h1200" class="wf-hud"/>
      <g class="wf-flight">
        <path d="M-60 37H70M-80 19H38M-80 55H38" stroke="url(#wfTrail)" stroke-width="1.6" fill="none"/>
        ${jet(70, 22, 0.3)}${jet(38, 8, 0.22)}${jet(38, 44, 0.22)}
      </g>
    </svg>
    <div class="wf-hero-sweep" aria-hidden="true"></div>
    <div class="wf-hero-copy">
      <div class="wf-hero-kicker">&#9650; Air Defense Operations</div>
      <div class="wf-hero-title">Agent Squadron</div>
      <div class="wf-hero-sub">Defense grid online · every card reports real work</div>
    </div>
    <div class="wf-hero-strip" id="wfStrip">${statusStripHtml()}</div>
  </div>`;
}

function statusStripHtml() {
  const n = counts();
  const counts_ = { RUNNING: n.airborne, READY: n.standby, FAILED: n.down };
  return `<div class="wf-strip">
    <span class="wf-count run"><span class="lamp ${counts_.RUNNING ? "RUNNING" : ""}"></span>AIRBORNE <b>${counts_.RUNNING}</b></span>
    <span class="wf-count"><span class="lamp READY"></span>ON STANDBY <b>${counts_.READY}</b></span>
    <span class="wf-count bad"><span class="lamp ${counts_.FAILED ? "FAILED" : ""}"></span>DOWN <b>${counts_.FAILED}</b></span>
  </div>`;
}

export async function mount(view) {
  setCardDeco(false);
  view.innerHTML = `<div class="wf-defense">
    ${backdropHtml()}
    ${heroHtml()}
    <div class="row" style="margin-bottom:14px">
      <p class="muted" style="margin:0">${esc(EXPLAIN)}</p>
      <span class="spacer"></span>
      <button class="btn primary" id="runDiag">Run system diagnostics</button>
    </div>
    <div class="workflow">
      <section class="agents-grid" id="agentsGrid">${gridHtml()}</section>
      <section class="card">
        <h2>Live Agent Activity</h2>
        <div class="feed" id="feed">${feedHtml()}</div>
      </section>
    </div></div>`;
  cleanup = wire(view, { strip: statusStripHtml });
}

export function unmount() {
  if (cleanup) cleanup();
  cleanup = null;
}
