// Visual playback overlay: a large glowing cursor that glides to each element, a highlight around the target,
// a click pulse with an optional quiet click sound, and a visible list when a dropdown value is chosen.
// Lives in a closed shadow root with pointer-events: none, so it never changes the application's DOM behaviour.
// Settings come from window.__qaOverlayConfig ({speedMs, highlight, sound, volume}) when the runner sets it.
// point(), click() and choose() return Promises that resolve when the movement or pulse has finished, so the
// runner acts only after the cursor has arrived (move -> highlight -> click -> execute).
(() => {
  if (window.__qaOverlay) return;
  const cfg = () => Object.assign({ speedMs: 450, highlight: true, sound: true, volume: 0.35 }, window.__qaOverlayConfig || {});
  let host = null, root = null, cursor = null, ring = null, box = null, tag = null, banner = null, list = null;
  let at = null; // where the cursor is now
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  function ensure() {
    if (host && host.isConnected) return;
    host = document.createElement("div");
    host.id = "__qa_overlay_host";
    host.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647;";
    root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `
      <style>
        .cur{position:fixed;left:0;top:0;width:0;height:0;transform:translate(-80px,-80px);transition-property:transform;transition-timing-function:cubic-bezier(.22,.8,.3,1);}
        .cur svg{position:absolute;left:-3px;top:-2px;width:34px;height:34px;filter:drop-shadow(0 0 6px rgba(34,199,240,.85)) drop-shadow(0 2px 3px rgba(0,0,0,.6));}
        .ring{position:absolute;left:-22px;top:-22px;width:44px;height:44px;border-radius:50%;border:2px solid rgba(34,199,240,.85);box-shadow:0 0 14px rgba(34,199,240,.6), inset 0 0 10px rgba(34,199,240,.35);opacity:.55;transition:transform .2s ease,opacity .2s ease;}
        .ring.hover{transform:scale(.75);opacity:1;}
        .ring.down{transform:scale(.5);opacity:1;border-color:#2fe08a;box-shadow:0 0 18px rgba(47,224,138,.8);}
        .box{position:fixed;border:2px solid #22c7f0;border-radius:6px;box-shadow:0 0 0 4px rgba(34,199,240,.22),0 0 18px rgba(34,199,240,.45);transition:all .2s ease;display:none;}
        .box.type{border-color:#3b82f6;box-shadow:0 0 0 4px rgba(59,130,246,.25),0 0 18px rgba(59,130,246,.4);}
        .box.assert{border-color:#a855f7;box-shadow:0 0 0 4px rgba(168,85,247,.25);}
        .box.heal{border-color:#f59e0b;box-shadow:0 0 0 4px rgba(245,158,11,.3);}
        .box.fail{border-color:#ef4444;box-shadow:0 0 0 4px rgba(239,68,68,.3);}
        .tag{position:fixed;font:12px/1.4 Segoe UI,sans-serif;background:#0b1626;color:#bfefff;border:1px solid #22c7f0;padding:2px 8px;border-radius:4px;display:none;white-space:nowrap;}
        .banner{position:fixed;right:12px;bottom:12px;max-width:60vw;font:12px/1.4 Segoe UI,sans-serif;background:rgba(5,11,22,.92);color:#e6f6ff;padding:6px 10px;border-radius:6px;border-left:4px solid #22c7f0;display:none;}
        .ripple{position:fixed;width:34px;height:34px;margin:-17px 0 0 -17px;border-radius:50%;border:3px solid #2fe08a;box-shadow:0 0 12px rgba(47,224,138,.7);animation:r .55s ease-out forwards;}
        @keyframes r{from{transform:scale(.3);opacity:1}to{transform:scale(1.9);opacity:0}}
        .list{position:fixed;display:none;min-width:140px;max-height:260px;overflow:hidden;font:13px/1.2 Segoe UI,sans-serif;background:#fff;color:#111;border:1px solid #22c7f0;border-radius:4px;box-shadow:0 6px 20px rgba(0,0,0,.35);}
        .list div{padding:5px 10px;white-space:nowrap;}
        .list div.on{background:#22c7f0;color:#001018;}
      </style>
      <div class="box"></div><div class="tag"></div><div class="list"></div><div class="banner"></div>
      <div class="cur"><div class="ring"></div><svg viewBox="0 0 24 24"><path d="M3 2l7 19 2.5-7.5L20 11z" fill="#fff" stroke="#0b1626" stroke-width="1.4" stroke-linejoin="round"/></svg></div>`;
    cursor = root.querySelector(".cur");
    ring = root.querySelector(".ring");
    box = root.querySelector(".box");
    tag = root.querySelector(".tag");
    banner = root.querySelector(".banner");
    list = root.querySelector(".list");
    (document.body || document.documentElement).appendChild(host);
    at = null;
  }

  // Glide to (x, y). Resolves when the cursor has arrived.
  async function move(x, y) {
    const c = cfg();
    const dist = at ? Math.hypot(x - at[0], y - at[1]) : 400;
    const ms = Math.max(0, Math.round(c.speedMs * Math.min(1, 0.35 + dist / 900)));
    cursor.style.transitionDuration = `${ms}ms`;
    cursor.style.transform = `translate(${x}px, ${y}px)`;
    at = [x, y];
    ring.classList.remove("hover", "down");
    if (ms) await wait(ms + 20);
    ring.classList.add("hover");
  }

  let audio = null;
  function tick() {
    const c = cfg();
    if (!c.sound || !(c.volume > 0)) return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      if (audio.state === "suspended") audio.resume().catch(() => {});
      const t = audio.currentTime + 0.005, len = Math.floor(audio.sampleRate * 0.025);
      const buf = audio.createBuffer(1, len, audio.sampleRate), d = buf.getChannelData(0);
      for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 6);
      const src = audio.createBufferSource(), bp = audio.createBiquadFilter(), g = audio.createGain();
      src.buffer = buf; bp.type = "bandpass"; bp.frequency.value = 3200; bp.Q.value = 1.2;
      g.gain.value = 0.9 * Math.min(1, c.volume);
      src.connect(bp).connect(g).connect(audio.destination);
      src.start(t);
    } catch { /* sound is a convenience */ }
  }

  window.__qaOverlay = {
    async point(x, y, w, h, kind, label) {
      ensure();
      const c = cfg();
      list.style.display = "none";
      if (c.highlight !== false) {
        box.className = `box ${kind || ""}`;
        Object.assign(box.style, { display: "block", left: `${x - 4}px`, top: `${y - 4}px`, width: `${w + 8}px`, height: `${h + 8}px` });
      } else box.style.display = "none";
      if (label) {
        tag.textContent = label;
        Object.assign(tag.style, { display: "block", left: `${Math.max(4, x)}px`, top: `${Math.max(4, y - 26)}px` });
      } else tag.style.display = "none";
      await move(x + w / 2, y + h / 2);
      return true;
    },
    async click(x, y) {
      ensure();
      if (!at || Math.hypot(x - at[0], y - at[1]) > 2) await move(x, y);
      ring.classList.add("down");
      const r = document.createElement("div");
      r.className = "ripple";
      r.style.left = `${x}px`;
      r.style.top = `${y}px`;
      root.appendChild(r);
      tick();
      setTimeout(() => r.remove(), 650);
      await wait(120);
      ring.classList.remove("down");
      return true;
    },
    // Show a dropdown's options under it, glide to the chosen one and pulse on it (native selects cannot be
    // opened by automation, so the choice is made visible here; the value is then set with Playwright).
    async choose(x, y, w, h, options, index) {
      ensure();
      const shown = options.slice(Math.max(0, index - 6), Math.max(0, index - 6) + 10);
      const offset = Math.max(0, index - 6);
      list.innerHTML = shown.map((o, i) => `<div class="${i + offset === index ? "on" : ""}"></div>`).join("");
      [...list.children].forEach((d, i) => { d.textContent = shown[i]; });
      const below = y + h + 2 + 26 * shown.length < window.innerHeight;
      Object.assign(list.style, { display: "block", left: `${x}px`, minWidth: `${w}px`, top: below ? `${y + h + 2}px` : `${Math.max(4, y - 2 - 26 * shown.length)}px` });
      await wait(150);
      const on = list.querySelector(".on");
      if (on) {
        const b = on.getBoundingClientRect();
        await this.click(b.left + Math.min(40, b.width / 2), b.top + b.height / 2);
      }
      await wait(Math.min(400, cfg().speedMs));
      list.style.display = "none";
      return true;
    },
    clear() {
      if (!host) return;
      box.style.display = "none";
      tag.style.display = "none";
      list.style.display = "none";
      ring && ring.classList.remove("hover", "down");
    },
    // Current executing agent and action, shown in a corner (kept until replaced or cleared with an empty text).
    status(text) {
      ensure();
      banner.textContent = text || "";
      banner.style.display = text ? "block" : "none";
    },
    remove() {
      if (host) host.remove();
      host = null;
    },
  };
})();
