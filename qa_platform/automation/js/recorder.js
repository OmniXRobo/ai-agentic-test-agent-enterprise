// Guided Browser Automation recorder. Sends steps to the runtime through the
// exposed binding window.__qaRecord. Password values are never sent.
(() => {
  if (window.__qaRecorderInstalled || !window.__qaFp) return;
  window.__qaRecorderInstalled = true;
  const send = (step) => { try { window.__qaRecord(step); } catch (e) { /* binding not ready */ } };
  const target = (el) => window.__qaFp.fingerprint(el);
  const isOverlay = (el) => el && el.closest && el.closest("#__qa_overlay_host");
  const editable = (el) => el && (el.tagName === "TEXTAREA" || (el.tagName === "INPUT" && !["button", "submit", "reset", "checkbox", "radio", "file", "image"].includes(el.type)) || el.isContentEditable);

  document.addEventListener("click", (e) => {
    const el = e.target.closest("a,button,input,select,textarea,label,summary,[role],[onclick]") || e.target;
    if (isOverlay(el) || editable(el) || el.tagName === "SELECT" || el.tagName === "OPTION") return;
    if (el.tagName === "INPUT" && (el.type === "checkbox" || el.type === "radio")) return; // recorded on change
    if (el.tagName === "LABEL" && el.control) return; // the labelled control records its own change
    send({ action: "click", target: target(el) });
  }, true);

  document.addEventListener("change", (e) => {
    const el = e.target;
    if (isOverlay(el)) return;
    if (el.tagName === "SELECT") {
      send({ action: "select", target: target(el), value: el.value });
    } else if (el.tagName === "INPUT" && (el.type === "checkbox" || el.type === "radio")) {
      send({ action: el.checked || el.type === "radio" ? "check" : "uncheck", target: target(el) });
    } else if (editable(el)) {
      send({ action: "fill", target: target(el), value: el.type === "password" ? "__PASSWORD__" : (el.value ?? el.innerText) });
    }
  }, true);

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || isOverlay(e.target)) return;
    const el = e.target;
    if (editable(el) && el.tagName !== "TEXTAREA") {
      send({ action: "fill", target: target(el), value: el.type === "password" ? "__PASSWORD__" : el.value });
      send({ action: "press", target: target(el), value: "Enter" });
    }
  }, true);
})();
