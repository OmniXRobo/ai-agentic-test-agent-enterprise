// Role-specific robot graphics. Each agent gets its own head shape, eyes,
// antenna, colour and a chest emblem describing what it does.

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
};

const STYLE = {
  requirements:   { hue: 210, head: "square", eyes: "round",  antenna: "single" },
  planning:       { hue: 265, head: "dome",   eyes: "visor",  antenna: "double" },
  suite:          { hue: 190, head: "wide",   eyes: "square", antenna: "single" },
  testcase:       { hue: 145, head: "round",  eyes: "round",  antenna: "bolt" },
  testdata:       { hue: 30,  head: "hex",    eyes: "round",  antenna: "dish" },
  discovery:      { hue: 175, head: "dome",   eyes: "single", antenna: "dish" },
  automation:     { hue: 225, head: "square", eyes: "visor",  antenna: "bolt" },
  recorder:       { hue: 0,   head: "round",  eyes: "single", antenna: "single" },
  execution:      { hue: 120, head: "wide",   eyes: "round",  antenna: "double" },
  healing:        { hue: 330, head: "round",  eyes: "square", antenna: "none" },
  performance:    { hue: 45,  head: "hex",    eyes: "visor",  antenna: "bolt" },
  jmeter:         { hue: 15,  head: "square", eyes: "square", antenna: "dish" },
  analysis:       { hue: 285, head: "dome",   eyes: "round",  antenna: "single" },
  defect:         { hue: 355, head: "hex",    eyes: "single", antenna: "double" },
  orchestrator:   { hue: 200, head: "wide",   eyes: "visor",  antenna: "double" },
  playback:       { hue: 160, head: "square", eyes: "round",  antenna: "none" },
  reporting:      { hue: 245, head: "round",  eyes: "visor",  antenna: "dish" },
  voice:          { hue: 305, head: "dome",   eyes: "round",  antenna: "bolt" },
};

function head(shape, fill) {
  switch (shape) {
    case "round": return `<circle cx="32" cy="24" r="14" fill="${fill}"/>`;
    case "dome": return `<path d="M17 32V24a15 15 0 0 1 30 0v8z" fill="${fill}"/>`;
    case "hex": return `<path d="M24 10h16l8 14-8 14H24l-8-14z" fill="${fill}"/>`;
    case "wide": return `<rect x="12" y="13" width="40" height="22" rx="7" fill="${fill}"/>`;
    default: return `<rect x="17" y="11" width="30" height="25" rx="5" fill="${fill}"/>`;
  }
}

function eyes(kind) {
  switch (kind) {
    case "visor": return `<rect x="21" y="19" width="22" height="8" rx="4" class="eye"/><rect x="25" y="21" width="14" height="4" rx="2" class="pupil"/>`;
    case "single": return `<circle cx="32" cy="23" r="6" class="eye"/><circle cx="32" cy="23" r="3" class="pupil"/>`;
    case "square": return `<rect x="22" y="19" width="8" height="8" rx="1.5" class="eye"/><rect x="34" y="19" width="8" height="8" rx="1.5" class="eye"/><rect x="24.5" y="21.5" width="3" height="3" class="pupil"/><rect x="36.5" y="21.5" width="3" height="3" class="pupil"/>`;
    default: return `<circle cx="26" cy="23" r="4.5" class="eye"/><circle cx="38" cy="23" r="4.5" class="eye"/><circle cx="26" cy="23" r="2" class="pupil"/><circle cx="38" cy="23" r="2" class="pupil"/>`;
  }
}

function antenna(kind, fill, tip) {
  switch (kind) {
    case "double": return `<path d="M25 12 21 4M39 12l4-8" stroke="${fill}" stroke-width="2"/><circle cx="21" cy="4" r="2.5" fill="${tip}" class="antenna-tip"/><circle cx="43" cy="4" r="2.5" fill="${tip}" class="antenna-tip"/>`;
    case "dish": return `<path d="M32 11V6" stroke="${fill}" stroke-width="2"/><path d="M25 6a7 4 0 0 0 14 0z" fill="${tip}" class="antenna-tip"/>`;
    case "bolt": return `<path d="M33 1 28 8h4l-2 5 6-8h-4l2-4z" fill="${tip}" class="antenna-tip"/>`;
    case "none": return "";
    default: return `<path d="M32 11V4" stroke="${fill}" stroke-width="2"/><circle cx="32" cy="3.5" r="3" fill="${tip}" class="antenna-tip"/>`;
  }
}

export function robotSvg(key, title = "") {
  const s = STYLE[key] || STYLE.orchestrator;
  const main = `hsl(${s.hue} 70% 52%)`;
  const dark = `hsl(${s.hue} 60% 36%)`;
  const tip = `hsl(${(s.hue + 140) % 360} 85% 60%)`;
  return `<svg class="robot-svg" viewBox="0 0 64 64" role="img" aria-label="${title}">
    ${antenna(s.antenna, dark, tip)}
    ${head(s.head, main)}
    ${eyes(s.eyes)}
    <rect x="28" y="35" width="8" height="4" fill="${dark}"/>
    <rect x="14" y="38" width="36" height="24" rx="7" fill="${dark}"/>
    <rect x="9" y="42" width="5" height="14" rx="2.5" fill="${main}"/>
    <rect x="50" y="42" width="5" height="14" rx="2.5" fill="${main}"/>
    <g transform="translate(24 42)">${E[key] || E.orchestrator}</g>
  </svg>`;
}
