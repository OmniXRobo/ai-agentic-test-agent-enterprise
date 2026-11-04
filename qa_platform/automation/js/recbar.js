// 23.3.20: Record / Pause bar inside the recorded browser window. Clicks on it are never recorded
// (recorder.js ignores #__qa_rec_host). The runtime keeps the state; this bar shows it and sends Record / Pause.
(() => {
  if (window.top !== window || window.__qaRecBar || typeof window.__qaControl !== "function") return;
  const LABEL = { ready: "NOT RECORDING", recording: "RECORDING", paused: "PAUSED", replaying: "PLAYING EARLIER STEPS" };
  let host, ui, last = null, dragged = null;
  function build() {
    if (host && host.isConnected) return;
    host = document.createElement("div");
    host.id = "__qa_rec_host";
    host.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;all:initial;position:fixed;right:16px;bottom:16px;z-index:2147483647";
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = `<style>
      .bar{font:600 12px/1.2 Segoe UI,Arial,sans-serif;display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:10px;
        background:#0b1726;color:#e6f1ff;border:1px solid #2a4a6b;box-shadow:0 6px 24px rgba(0,0,0,.45);user-select:none;cursor:move}
      .dot{width:10px;height:10px;border-radius:50%;background:#7b8a9a}
      .recording .dot{background:#ff3b3b;animation:b 1s infinite} .paused .dot{background:#ffb020} .replaying .dot{background:#3fa9ff;animation:b 1s infinite}
      @keyframes b{50%{opacity:.25}}
      .state{letter-spacing:.06em} .where{color:#9fb3c8;font-weight:500}
      button{font:700 12px Segoe UI,Arial,sans-serif;border-radius:7px;padding:6px 12px;cursor:pointer;border:1px solid #2a4a6b;background:#13263b;color:#e6f1ff}
      button.rec{background:#d92d2d;border-color:#d92d2d;color:#fff} button:disabled{opacity:.45;cursor:default}
      .err{color:#ff8a8a;font-weight:500;max-width:260px}
      .bar{flex-wrap:wrap;max-width:min(92vw,820px)}
    </style>
    <div class="bar ready" part="bar"><span class="dot"></span><span class="state"></span><span class="where"></span>
      <button class="rec" data-a="record">Record</button><button data-a="pause">Pause</button>
      <button data-a="pick_text" title="Click an element: its text is checked">Check text</button>
      <button data-a="pick_visible" title="Click an element: it must be visible">Check visible</button>
      <button data-a="dialog" title="How pop-ups (alert, confirm) are answered">Pop-ups: OK</button>
      <span class="err"></span></div>`;
    ui = root.querySelector(".bar");
    root.addEventListener("click", async (e) => {
      const b = e.target.closest && e.target.closest("button");
      if (!b || b.disabled) return;
      e.stopPropagation();
      let a = b.dataset.a;
      if (a === "dialog") a = last && last.dialogs === "dismiss" ? "dialog_ok" : "dialog_cancel";
      if ((a === "pick_text" || a === "pick_visible") && last && last.pick) a = "pick_off";
      try { set(await window.__qaControl(a)); } catch (err) { /* the session has ended */ }
    });
    ui.addEventListener("mousedown", (e) => {
      if (e.target.closest && e.target.closest("button")) return;
      const r = host.getBoundingClientRect();
      dragged = { dx: e.clientX - r.left, dy: e.clientY - r.top };
      e.preventDefault();
    });
    window.addEventListener("mousemove", (e) => {
      if (!dragged) return;
      host.style.left = `${Math.max(0, e.clientX - dragged.dx)}px`; host.style.top = `${Math.max(0, e.clientY - dragged.dy)}px`;
      host.style.right = "auto"; host.style.bottom = "auto";
    }, true);
    window.addEventListener("mouseup", () => { dragged = null; }, true);
    (document.body || document.documentElement).appendChild(host);
    if (last) set(last);
  }
  function set(s) {
    if (!s) return;
    last = s;
    if (!ui) return;
    ui.className = `bar ${s.state}`;
    ui.querySelector(".state").textContent = LABEL[s.state] || s.state;
    ui.querySelector(".where").textContent = `${s.where} · ${s.steps} step${s.steps === 1 ? "" : "s"}`;
    const rec = ui.querySelector("[data-a=record]"), pause = ui.querySelector("[data-a=pause]");
    rec.textContent = s.state === "paused" ? "Resume" : "Record";
    rec.disabled = s.state === "recording" || s.state === "replaying";
    pause.disabled = s.state !== "recording";
    // 23.3.21: click-to-check and pop-up answers
    window.__qaPick = s.pick || null;
    ui.querySelectorAll("[data-a^=pick]").forEach((x) => {
      x.disabled = s.state !== "recording";
      const on = s.pick && x.dataset.a === `pick_${s.pick}`;
      x.textContent = on ? "Cancel check" : (x.dataset.a === "pick_text" ? "Check text" : "Check visible");
      x.className = on ? "rec" : "";
    });
    ui.querySelector("[data-a=dialog]").textContent = `Pop-ups: ${s.dialogs === "dismiss" ? "Cancel" : "OK"}`;
    ui.querySelector(".err").textContent = s.error || (s.pick ? "Click the element to check" : "");
  }
  window.__qaRecBar = { set };
  const start = () => { build(); window.__qaControl("state").then(set).catch(() => {}); };
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
  // some single-page apps replace the body: put the bar back
  setInterval(() => { if (document.body && (!host || !host.isConnected)) build(); }, 1000);
})();
