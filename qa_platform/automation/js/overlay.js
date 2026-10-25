// Visual playback overlay: cursor movement and target highlight.
// Lives in a closed shadow root with pointer-events: none, so it never changes the
// application's DOM behaviour, and it is removed after each action.
(() => {
  if (window.__qaOverlay) return;
  let host = null, cursor = null, box = null, tag = null, banner = null;

  function ensure() {
    if (host && host.isConnected) return;
    host = document.createElement("div");
    host.id = "__qa_overlay_host";
    host.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647;";
    const root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `
      <style>
        .cursor{position:fixed;left:0;top:0;width:22px;height:22px;transition:transform .35s ease;transform:translate(-40px,-40px);filter:drop-shadow(0 1px 2px rgba(0,0,0,.5));}
        .box{position:fixed;border:3px solid #22c55e;border-radius:6px;box-shadow:0 0 0 4px rgba(34,197,94,.25);transition:all .2s ease;display:none;}
        .box.type{border-color:#3b82f6;box-shadow:0 0 0 4px rgba(59,130,246,.25);}
        .box.assert{border-color:#a855f7;box-shadow:0 0 0 4px rgba(168,85,247,.25);}
        .box.heal{border-color:#f59e0b;box-shadow:0 0 0 4px rgba(245,158,11,.3);}
        .box.fail{border-color:#ef4444;box-shadow:0 0 0 4px rgba(239,68,68,.3);}
        .tag{position:fixed;font:12px/1.4 Segoe UI,sans-serif;background:#111827;color:#fff;padding:2px 8px;border-radius:4px;display:none;white-space:nowrap;}
        .banner{position:fixed;right:12px;bottom:12px;max-width:60vw;font:12px/1.4 Segoe UI,sans-serif;background:rgba(17,24,39,.92);color:#fff;padding:6px 10px;border-radius:6px;border-left:4px solid #22c55e;display:none;}
        .ripple{position:fixed;width:30px;height:30px;margin:-15px 0 0 -15px;border-radius:50%;border:3px solid #22c55e;animation:r .5s ease-out forwards;}
        @keyframes r{from{transform:scale(.3);opacity:1}to{transform:scale(1.6);opacity:0}}
      </style>
      <svg class="cursor" viewBox="0 0 24 24"><path d="M3 2l7 19 2.5-7.5L20 11z" fill="#fff" stroke="#111" stroke-width="1.5" stroke-linejoin="round"/></svg>
      <div class="box"></div><div class="tag"></div><div class="banner"></div>`;
    cursor = root.querySelector(".cursor");
    box = root.querySelector(".box");
    tag = root.querySelector(".tag");
    banner = root.querySelector(".banner");
    host.__root = root;
    (document.body || document.documentElement).appendChild(host);
  }

  window.__qaOverlay = {
    point(x, y, w, h, kind, label) {
      ensure();
      const cx = x + w / 2, cy = y + h / 2;
      cursor.style.transform = `translate(${cx}px, ${cy}px)`;
      box.className = `box ${kind || ""}`;
      Object.assign(box.style, { display: "block", left: `${x - 4}px`, top: `${y - 4}px`, width: `${w + 8}px`, height: `${h + 8}px` });
      if (label) {
        tag.textContent = label;
        Object.assign(tag.style, { display: "block", left: `${Math.max(4, x)}px`, top: `${Math.max(4, y - 26)}px` });
      } else {
        tag.style.display = "none";
      }
    },
    click(x, y) {
      ensure();
      const r = document.createElement("div");
      r.className = "ripple";
      r.style.left = `${x}px`;
      r.style.top = `${y}px`;
      host.__root.appendChild(r);
      setTimeout(() => r.remove(), 600);
    },
    clear() {
      if (!host) return;
      box.style.display = "none";
      tag.style.display = "none";
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
