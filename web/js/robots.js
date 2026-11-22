// Air Force squadron units for the runtime agents (23.3.14). Every agent is a pilot (flight helmet, visor, oxygen
// mask) or a combat drone (armoured sensor head), with its own squadron colour, rank chevrons, wings and a
// shield badge carrying the emblem of what it does. Pure inline SVG: works offline, no image files.
// Classes used by CSS: .beacon (blinks while RUNNING), .eye-glow (HUD light), .visor.

const E = {
  requirements: '<path d="M3 1h7l3 3v11H3z" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M5 7h6M5 10h6M5 13h4" stroke="#fff" stroke-width="1.4"/>',
  planning: '<path d="M3 15V2" stroke="#fff" stroke-width="1.6"/><path d="M3 2h10l-2.5 3.5L13 9H3" fill="#fff"/>',
  suite: '<path d="M8 1 15 5 8 9 1 5z" fill="#fff"/><path d="M1 8l7 4 7-4M1 11l7 4 7-4" fill="none" stroke="#fff" stroke-width="1.4"/>',
  testcase: '<rect x="1.5" y="1.5" width="13" height="13" rx="2" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M4.5 8.5 7 11l4.5-6" fill="none" stroke="#fff" stroke-width="1.8"/>',
  testdata: '<ellipse cx="8" cy="3.5" rx="6" ry="2.3" fill="#fff"/><path d="M2 3.5v9c0 1.3 2.7 2.3 6 2.3s6-1 6-2.3v-9M2 8c0 1.3 2.7 2.3 6 2.3s6-1 6-2.3" fill="none" stroke="#fff" stroke-width="1.4"/>',
  discovery: '<circle cx="6.5" cy="6.5" r="4.5" fill="none" stroke="#fff" stroke-width="1.8"/><path d="m10 10 5 5" stroke="#fff" stroke-width="2.2"/>',
  automation: '<path d="M5 3 1 8l4 5M11 3l4 5-4 5" fill="none" stroke="#fff" stroke-width="1.8"/><path d="M9.5 2 6.5 14" stroke="#fff" stroke-width="1.4"/>',
  recorder: '<circle cx="8" cy="8" r="6.5" fill="none" stroke="#fff" stroke-width="1.5"/><circle cx="8" cy="8" r="3.5" fill="#ff5a5a"/>',
  execution: '<path d="M4 2v12l10-6z" fill="#fff"/>',
  healing: '<rect x="6" y="1" width="4" height="14" rx="1" fill="#fff"/><rect x="1" y="6" width="14" height="4" rx="1" fill="#fff"/>',
  performance: '<path d="M2 12a6 6 0 1 1 12 0" fill="none" stroke="#fff" stroke-width="1.8"/><path d="M8 12 11.5 6" stroke="#fff" stroke-width="1.8"/><circle cx="8" cy="12" r="1.5" fill="#fff"/>',
  jmeter: '<rect x="1.5" y="9" width="3" height="6" fill="#fff"/><rect x="6.5" y="5" width="3" height="10" fill="#fff"/><rect x="11.5" y="1" width="3" height="14" fill="#fff"/>',
  analysis: '<path d="M1 9h3l2-5 3 9 2-4h4" fill="none" stroke="#fff" stroke-width="1.7"/>',
  defect: '<ellipse cx="8" cy="9" rx="4" ry="5" fill="#fff"/><path d="M8 4V2M4 7 1 5.5M12 7l3-1.5M4 11l-3 1M12 11l3 1" stroke="#fff" stroke-width="1.4"/><path d="M8 5v9" stroke="rgba(0,0,0,.35)" stroke-width="1"/>',
  orchestrator: '<circle cx="8" cy="8" r="2.6" fill="#fff"/><circle cx="2.5" cy="2.5" r="1.8" fill="#fff"/><circle cx="13.5" cy="2.5" r="1.8" fill="#fff"/><circle cx="2.5" cy="13.5" r="1.8" fill="#fff"/><circle cx="13.5" cy="13.5" r="1.8" fill="#fff"/><path d="M3.5 3.5l3 3M12.5 3.5l-3 3M3.5 12.5l3-3M12.5 12.5l-3-3" stroke="#fff" stroke-width="1.3"/>',
  playback: '<path d="M1 2v12l7-6zM8 2v12l7-6z" fill="#fff"/>',
  reporting: '<path d="M8 1.5A6.5 6.5 0 1 0 14.5 8H8z" fill="#fff"/><path d="M9.5 0.5v6h6a6 6 0 0 0-6-6z" fill="rgba(255,255,255,.6)"/>',
  voice: '<rect x="5" y="1" width="6" height="9" rx="3" fill="#fff"/><path d="M2.5 7.5a5.5 5.5 0 0 0 11 0M8 13v2.5M5 15.5h6" fill="none" stroke="#fff" stroke-width="1.5"/>',
  // 23.12.0
  aiops: '<circle cx="8" cy="8" r="6.5" fill="none" stroke="#fff" stroke-width="1.5"/><path d="M1.5 8h13M8 1.5c2.2 2 2.2 11 0 13M8 1.5c-2.2 2-2.2 11 0 13" fill="none" stroke="#fff" stroke-width="1.2"/>',
  comparison: '<rect x="1" y="2" width="6" height="12" rx="1" fill="none" stroke="#fff" stroke-width="1.5"/><rect x="9" y="2" width="6" height="12" rx="1" fill="none" stroke="#fff" stroke-width="1.5"/><path d="M3 6h2M3 9h2M11 6h2M11 9h2" stroke="#fff" stroke-width="1.4"/>',
  api: '<path d="M2 4h12M2 8h12M2 12h12" stroke="#fff" stroke-width="1.2" opacity=".5"/><path d="M5 3 2 8l3 5M11 3l3 5-3 5" fill="none" stroke="#fff" stroke-width="1.8"/><circle cx="8" cy="8" r="1.6" fill="#fff"/>',
  apianalysis: '<circle cx="6.5" cy="6.5" r="4.5" fill="none" stroke="#fff" stroke-width="1.6"/><path d="m10 10 5 5" stroke="#fff" stroke-width="2"/><path d="M4.5 6.5h4" stroke="#fff" stroke-width="1.4"/>',
  contract: '<path d="M3 1h7l3 3v11H3z" fill="none" stroke="#fff" stroke-width="1.5"/><path d="m5.5 9 2 2 3.5-4" fill="none" stroke="#fff" stroke-width="1.7"/>',
  scheduler: '<circle cx="8" cy="8" r="6.5" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M8 4v4l3 2" fill="none" stroke="#fff" stroke-width="1.7"/>',
  // 23.14.0: AURA memory, Security, Fuse, ActiveMQ and the shared result-action agents
  aura: '<path d="M8 1.5c-3 0-5 2-5 4.5 0 1.6.8 2.6 1.8 3.4V13h6.4V9.4C12.2 8.6 13 7.6 13 6c0-2.5-2-4.5-5-4.5z" fill="none" stroke="#fff" stroke-width="1.5"/><path d="M6 15h4M8 4v5M6 6.5h4" stroke="#fff" stroke-width="1.3"/>',
  secdiscovery: '<path d="M8 1 14 3.5V8c0 3.5-2.6 6-6 7-3.4-1-6-3.5-6-7V3.5z" fill="none" stroke="#fff" stroke-width="1.5"/><circle cx="7.5" cy="7.5" r="2.4" fill="none" stroke="#fff" stroke-width="1.4"/><path d="m9.3 9.3 2.2 2.2" stroke="#fff" stroke-width="1.6"/>',
  vulnscan: '<path d="M8 1 14 3.5V8c0 3.5-2.6 6-6 7-3.4-1-6-3.5-6-7V3.5z" fill="#fff"/><path d="M8 4.5v4.5" stroke="#c0262d" stroke-width="2"/><circle cx="8" cy="11.3" r="1.1" fill="#c0262d"/>',
  authz: '<rect x="3" y="7" width="10" height="8" rx="1.4" fill="#fff"/><path d="M5 7V5a3 3 0 0 1 6 0v2" fill="none" stroke="#fff" stroke-width="1.7"/><circle cx="8" cy="10.6" r="1.3" fill="rgba(0,0,0,.45)"/>',
  fuzz: '<path d="M1 8h2l1.5-5 2 10 2-8 1.5 6 1.5-3H15" fill="none" stroke="#fff" stroke-width="1.5"/><circle cx="3" cy="13" r="1" fill="#fff"/><circle cx="13" cy="3" r="1" fill="#fff"/>',
  validate: '<path d="M8 1 14 3.5V8c0 3.5-2.6 6-6 7-3.4-1-6-3.5-6-7V3.5z" fill="none" stroke="#fff" stroke-width="1.5"/><path d="m5 8 2 2 4-4.5" fill="none" stroke="#fff" stroke-width="1.8"/>',
  fusediscovery: '<circle cx="3" cy="8" r="2" fill="#fff"/><circle cx="13" cy="3" r="2" fill="#fff"/><circle cx="13" cy="13" r="2" fill="#fff"/><path d="M5 8h3m0 0 3.4-4M8 8l3.4 4" fill="none" stroke="#fff" stroke-width="1.4"/>',
  fusemap: '<path d="M1 4h5l4 8h5M1 12h5l4-8h5" fill="none" stroke="#fff" stroke-width="1.5"/><path d="M13 2l2 2-2 2M13 10l2 2-2 2" fill="none" stroke="#fff" stroke-width="1.3"/>',
  fuseexec: '<path d="M1 8h4M11 8h4" stroke="#fff" stroke-width="1.6"/><path d="M6 3v10l6-5z" fill="#fff"/>',
  fusecompare: '<path d="M2 3h5v10H2zM9 3h5v10H9z" fill="none" stroke="#fff" stroke-width="1.4"/><path d="M4.5 1v14M11.5 1v14" stroke="#fff" stroke-width=".9" opacity=".6"/><path d="M6 8h4" stroke="#fff" stroke-width="1.6"/>',
  broker: '<rect x="1.5" y="5" width="13" height="6" rx="1.5" fill="none" stroke="#fff" stroke-width="1.5"/><path d="M4 8h1M7 8h1M10 8h1" stroke="#fff" stroke-width="1.8"/><path d="M8 1v4M8 11v4" stroke="#fff" stroke-width="1.3"/>',
  publish: '<path d="M1.5 3.5h13v9h-13z" fill="none" stroke="#fff" stroke-width="1.4"/><path d="m1.5 3.5 6.5 5 6.5-5" fill="none" stroke="#fff" stroke-width="1.4"/><path d="M12 15V10m-2 2 2-2 2 2" fill="none" stroke="#fff" stroke-width="1.2"/>',
  consumer: '<path d="M1.5 3.5h13v9h-13z" fill="none" stroke="#fff" stroke-width="1.4"/><path d="m4.5 8 2.5 2.5L11.5 5.5" fill="none" stroke="#fff" stroke-width="1.7"/>',
  dlq: '<path d="M13 8A5 5 0 1 1 8 3h3" fill="none" stroke="#fff" stroke-width="1.6"/><path d="m10 1 2 2-2 2" fill="none" stroke="#fff" stroke-width="1.5"/><path d="M6 6.5 10 10.5M10 6.5l-4 4" stroke="#fff" stroke-width="1.4"/>',
  bugai: '<ellipse cx="7" cy="9" rx="3.5" ry="4.5" fill="#fff"/><path d="M7 4.5V3M3.5 7 1 6M3.5 11 1 12" stroke="#fff" stroke-width="1.3"/><path d="M12.5 1.5l.7 1.6 1.6.7-1.6.7-.7 1.6-.7-1.6-1.6-.7 1.6-.7zM13 8l.5 1 1 .5-1 .5-.5 1-.5-1-1-.5 1-.5z" fill="#fff"/>',
  evidence: '<rect x="1.5" y="3.5" width="13" height="10" rx="1.5" fill="none" stroke="#fff" stroke-width="1.5"/><circle cx="8" cy="8.5" r="2.6" fill="none" stroke="#fff" stroke-width="1.5"/><path d="M5 3.5 6 1.5h4l1 2" fill="none" stroke="#fff" stroke-width="1.3"/>',
  tfsprep: '<path d="M3 1h7l3 3v11H3z" fill="none" stroke="#fff" stroke-width="1.5"/><ellipse cx="8" cy="9.5" rx="2.2" ry="2.8" fill="#fff"/><path d="M8 6.3v-1M5 8.5h-1M11 8.5h1M5.5 11.5l-1 1M10.5 11.5l1 1" stroke="#fff" stroke-width="1"/>',
  pdf: '<path d="M3 1h7l3 3v11H3z" fill="#fff"/><path d="M10 1v3h3" fill="none" stroke="rgba(0,0,0,.35)" stroke-width="1"/><path d="M5 9h6M5 11.5h4" stroke="#c0262d" stroke-width="1.4"/>',
  email: '<rect x="1" y="3" width="14" height="10" rx="1.5" fill="#fff"/><path d="m1.5 3.8 6.5 5 6.5-5" fill="none" stroke="rgba(0,0,0,.4)" stroke-width="1.3"/>',
};

// type: pilot | drone; hue: squadron colour; visor: gold | cobalt | emerald (pilots only)
const STYLE = {
  requirements:   { type: "pilot", hue: 210, visor: "gold" },
  planning:       { type: "pilot", hue: 265, visor: "cobalt" },
  suite:          { type: "drone", hue: 190 },
  testcase:       { type: "pilot", hue: 145, visor: "gold" },
  testdata:       { type: "pilot", hue: 30,  visor: "cobalt" },
  discovery:      { type: "drone", hue: 175 },
  automation:     { type: "pilot", hue: 225, visor: "emerald" },
  recorder:       { type: "drone", hue: 0 },
  execution:      { type: "drone", hue: 120 },
  healing:        { type: "drone", hue: 330 },
  performance:    { type: "drone", hue: 45 },
  jmeter:         { type: "drone", hue: 15 },
  analysis:       { type: "pilot", hue: 285, visor: "gold" },
  defect:         { type: "drone", hue: 355 },
  orchestrator:   { type: "pilot", hue: 200, visor: "gold" },
  playback:       { type: "drone", hue: 160 },
  reporting:      { type: "pilot", hue: 245, visor: "emerald" },
  voice:          { type: "pilot", hue: 305, visor: "cobalt" },
  aiops:          { type: "pilot", hue: 185, visor: "gold" },
  comparison:     { type: "drone", hue: 260 },
  api:            { type: "pilot", hue: 95,  visor: "emerald" },
  apianalysis:    { type: "drone", hue: 300 },
  contract:       { type: "drone", hue: 55 },
  scheduler:      { type: "drone", hue: 205 },
  // 23.14.0
  aura:           { type: "pilot", hue: 170, visor: "cobalt" },
  secdiscovery:   { type: "drone", hue: 215 },
  vulnscan:       { type: "drone", hue: 350 },
  authz:          { type: "pilot", hue: 40,  visor: "gold" },
  fuzz:           { type: "drone", hue: 20 },
  validate:       { type: "pilot", hue: 135, visor: "emerald" },
  fusediscovery:  { type: "drone", hue: 10 },
  fusemap:        { type: "pilot", hue: 25,  visor: "cobalt" },
  fuseexec:       { type: "drone", hue: 0 },
  fusecompare:    { type: "pilot", hue: 340, visor: "gold" },
  broker:         { type: "drone", hue: 230 },
  publish:        { type: "pilot", hue: 240, visor: "emerald" },
  consumer:       { type: "drone", hue: 150 },
  dlq:            { type: "drone", hue: 280 },
  bugai:          { type: "pilot", hue: 315, visor: "cobalt" },
  evidence:       { type: "drone", hue: 195 },
  tfsprep:        { type: "pilot", hue: 220, visor: "gold" },
  pdf:            { type: "drone", hue: 5 },
  email:          { type: "pilot", hue: 200, visor: "emerald" },
};

const VISOR = {
  gold: ["#fff1b8", "#e0a02a", "#4a2405"],
  cobalt: ["#d6f1ff", "#2f7fd8", "#0a1840"],
  emerald: ["#d8ffe9", "#25b073", "#05301c"],
};

let seq = 0;

function star(cx, cy, r, fill) {
  const pts = [];
  for (let i = 0; i < 10; i++) {
    const a = -Math.PI / 2 + (i * Math.PI) / 5, rr = i % 2 ? r * 0.42 : r;
    pts.push(`${(cx + rr * Math.cos(a)).toFixed(2)},${(cy + rr * Math.sin(a)).toFixed(2)}`);
  }
  return `<polygon points="${pts.join(" ")}" fill="${fill}"/>`;
}

// Shoulders, harness, rank chevrons, flag patch, wings and the squadron shield with the role emblem.
function torso(c, key, drone) {
  const body = drone
    ? `<path d="M4 64v-6l8-10 13-3h14l13 3 8 10v6z" fill="url(#s${c.u})"/>
       <path d="M4 58l8-10 9 3-4 13H4zM60 58l-8-10-9 3 4 13h13z" fill="${c.dark}"/>
       <path d="M12 48l9 3M52 48l-9 3" stroke="${c.light}" stroke-width=".8" opacity=".6"/>`
    : `<path d="M5 64v-5c0-8 7-13.5 15.5-14.5L32 49l11.5-4.5C52 45.5 59 51 59 59v5z" fill="url(#s${c.u})"/>
       <path d="M22 44.6 32 51l10-6.4-2.6-1.8L32 47l-7.4-4.2z" fill="${c.dark}"/>
       <path d="M17 47.5 23.5 64M47 47.5 40.5 64" stroke="#0a111c" stroke-width="2.4" opacity=".75"/>`;
  return `${body}
    <path d="M8 53.2l4.2 2.6 4.2-2.6M8 56.4l4.2 2.6 4.2-2.6" stroke="#ffd34d" stroke-width="1.3" fill="none"/>
    <rect x="47" y="52" width="9" height="5.6" rx=".6" fill="${c.accent}"/>
    <path d="M47 54.8h9" stroke="#fff" stroke-width=".7" opacity=".8"/>
    <path d="M32 50.6 21.6 48.4l3.2 1.9-4.4.3 4.4 1.4H32zM32 50.6l10.4-2.2-3.2 1.9 4.4.3-4.4 1.4H32z" fill="#ffd34d"/>
    <path d="M25 51.6h14v6.2c0 3.3-3.2 5.3-7 6.2-3.8-.9-7-2.9-7-6.2z" fill="${c.main}" stroke="#ffd34d" stroke-width="1"/>
    <g transform="translate(28 53.2) scale(.5)">${E[key] || E.orchestrator}</g>`;
}

function visorDef(c, s) {
  const [v1, v2, v3] = VISOR[s.visor] || VISOR.gold;
  return `<linearGradient id="v${c.u}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${v1}"/><stop offset=".45" stop-color="${v2}"/><stop offset="1" stop-color="${v3}"/></linearGradient>`;
}

function pilot(c) {
  return `
    <rect x="10.5" y="26" width="6.5" height="12" rx="3.2" fill="${c.dark}"/>
    <rect x="47" y="26" width="6.5" height="12" rx="3.2" fill="${c.dark}"/>
    <path d="M13 31c0-14 8.5-23 19-23s19 9 19 23v5c0 4-3 7-7 7H20c-4 0-7-3-7-7z" fill="url(#h${c.u})"/>
    <path d="M29.6 8.3c.8-.2 1.6-.3 2.4-.3s1.6.1 2.4.3V17h-4.8z" fill="${c.accent}"/>
    ${star(20.5, 15.5, 2.6, "#fff")}
    <path d="M17 24c0-5 6.5-8 15-8s15 3 15 8v4c0 3.3-2.7 5.5-6 5.5H23c-3.3 0-6-2.2-6-5.5z" fill="url(#v${c.u})" class="visor"/>
    <path d="M20.5 21.2c3-2.3 6.8-3.2 11.5-3.3" stroke="#fff" stroke-opacity=".75" stroke-width="1.3" fill="none" stroke-linecap="round"/>
    <rect x="21.5" y="25" width="7.5" height="2.2" rx="1.1" class="eye-glow"/>
    <rect x="35" y="25" width="7.5" height="2.2" rx="1.1" class="eye-glow"/>
    <path d="M25 33h14l-2.4 8.4c-.4 1.3-1.5 2.1-2.9 2.1h-3.4c-1.4 0-2.5-.8-2.9-2.1z" fill="#29323f"/>
    <path d="M27 35.5h10M27.6 37.7h8.8" stroke="#4a5568" stroke-width=".8"/>
    <circle cx="32" cy="40" r="1.8" fill="#5b687a"/>
    <path d="M35 42.5c3.5 2 7 2.8 10.5 1.8" stroke="#141b25" stroke-width="2" fill="none"/>
    <circle cx="32" cy="6.6" r="2.4" class="beacon" fill="${c.tip}"/>`;
}

function drone(c) {
  return `
    <rect x="27" y="39" width="10" height="7" fill="#18212d"/>
    <path d="M18 10h28l8 14-4 14-8 4H22l-8-4-4-14z" fill="url(#h${c.u})"/>
    <path d="M18 10l-8 14M46 10l8 14M14 38l8-5h20l8 5" stroke="${c.dark}" stroke-width="1.2" fill="none"/>
    <path d="M24 10h16l-2 5H26z" fill="${c.accent}"/>
    ${star(32, 18.3, 2.1, "#fff")}
    <path d="M13.5 21h37l-3.2 9H16.7z" fill="#05090f"/>
    <rect x="18" y="24" width="28" height="2.6" rx="1.3" class="eye-glow"/>
    <circle cx="32" cy="25.3" r="3.4" class="eye-core" fill="${c.tip}"/>
    <circle cx="32" cy="25.3" r="1.3" fill="#fff" opacity=".85"/>
    <path d="M20 33.5h7v3.5h-5.5zM44 33.5h-7v3.5h5.5z" fill="${c.dark}"/>
    <path d="M44.5 10.5 48.5 3.6" stroke="${c.dark}" stroke-width="1.6"/>
    <path d="M19.5 10.5 17 5.5" stroke="${c.dark}" stroke-width="1.2"/>
    <circle cx="48.6" cy="3.4" r="2.3" class="beacon" fill="${c.tip}"/>`;
}

export function robotSvg(key, title = "") {
  const s = STYLE[key] || STYLE.orchestrator;
  const h = s.hue;
  const c = {
    u: `${key}-${++seq}`,
    main: `hsl(${h} 42% 42%)`, light: `hsl(${h} 50% 62%)`, dark: `hsl(${h} 38% 22%)`,
    accent: `hsl(${h} 90% 58%)`, tip: `hsl(${(h + 140) % 360} 90% 60%)`,
  };
  const t = String(title).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
  const drn = s.type === "drone";
  return `<svg class="robot-svg unit-${s.type}" viewBox="0 0 64 64" role="img" aria-label="${t}">
    <defs>
      <linearGradient id="h${c.u}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${c.light}"/><stop offset=".55" stop-color="${c.main}"/><stop offset="1" stop-color="${c.dark}"/></linearGradient>
      <linearGradient id="s${c.u}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="hsl(${h} 16% 30%)"/><stop offset="1" stop-color="#0b121c"/></linearGradient>
    ${drn ? "" : visorDef(c, s)}
    </defs>
    ${torso(c, key, drn)}
    ${drn ? drone(c) : pilot(c)}
  </svg>`;
}
