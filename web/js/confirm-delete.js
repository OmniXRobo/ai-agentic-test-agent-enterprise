// 23.3.16: "Delete script?" confirmation, shared by AI-Driven, Guided Browser and Hybrid Test Automation.
// Resolves true only when the user presses Delete; Cancel, the close button, Escape or a click outside resolve false.

import { esc } from "./util.js";

export function confirmDelete({ name, detail = "", note = "", title = "Delete script?", body = null }) {
  return new Promise((resolve) => {
    const el = document.createElement("div");
    el.className = "modal-backdrop";
    el.innerHTML = `<div class="modal card confirm-delete" role="alertdialog" aria-modal="true" aria-labelledby="cdTitle" aria-describedby="cdText">
      <div class="row"><h3 id="cdTitle" style="margin:0">${esc(title)}</h3><span class="spacer"></span>
        <button class="icon-btn" data-no aria-label="Close">✕</button></div>
      <div class="modal-body" id="cdText">
        <p>Delete <strong>${esc(name)}</strong>${detail ? ` <span class="muted">(${esc(detail)})</span>` : ""}?</p>
        <p class="muted">${body !== null ? esc(body) : `It is removed from this list and from Download, Play Selected and Run all.${note ? ` ${esc(note)}` : ""}`}</p>
        <div class="row" style="margin-top:12px"><span class="spacer"></span>
          <button class="btn" data-no>Cancel</button><button class="btn danger" data-yes>Delete</button></div>
      </div></div>`;
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      document.removeEventListener("keydown", onKey, true);
      el.remove();
      resolve(value);
    };
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); finish(false); } };
    el.addEventListener("click", (e) => {
      if (e.target.closest("[data-yes]")) finish(true);
      else if (e.target === el || e.target.closest("[data-no]")) finish(false);
    });
    document.addEventListener("keydown", onKey, true);
    document.body.appendChild(el);
    el.querySelector(".btn[data-no]").focus();
  });
}
