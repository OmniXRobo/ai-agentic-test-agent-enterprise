// 23.11.0: the editable "Python Test (Pytest + Playwright)" of a Guided Browser script.
// Edit Script | Save | Validate | Run | Format | Revert | Version History | Download. Syntax highlighting, line numbers,
// indentation, search / replace, undo / redo (the browser's own, kept for every change made here), syntax errors
// with line numbers. The saved version is the one SkyGuard runs and downloads; the recorded steps stay as they are.

import { api } from "./api.js";
import { esc, toast, dateTimeOf, openModal } from "./util.js";
import { highlightPython } from "./pages/script-studio.js";

function lines(n) { return Array.from({ length: n }, (_, i) => i + 1).join("\n"); }

export function mountPythonEditor(card, data, { onRun } = {}) {
  const sid = encodeURIComponent(data.script.id);
  let state = data.python_state || { edited: false };
  let versions = data.python_versions || [];
  let source = data.python;
  let editing = false;
  let errors = [];

  const render = () => {
    card.innerHTML = `
      <div class="row" style="flex-wrap:wrap;gap:6px"><h3 style="margin:0">Python test (pytest + Playwright)</h3>
        <span class="pill ${state.edited ? "WARN" : "WAITING"}">${state.edited ? `Edited · Python v${state.version}` : "Generated from the steps"}</span><span class="spacer"></span>
        ${editing ? "" : '<button class="btn" data-a="edit">Edit Script</button>'}
        ${editing ? '<button class="btn primary" data-a="save">Save</button><button class="btn" data-a="cancel">Cancel</button>' : ""}
        <button class="btn" data-a="validate">Validate</button>
        <button class="btn" data-a="run" title="Runs the saved version">Run</button>
        ${editing ? '<button class="btn" data-a="format">Format</button>' : ""}
        ${state.edited ? '<button class="btn" data-a="revert" title="Use the Python generated from the recorded steps again (your edits stay in Version History)">Revert</button>' : ""}
        <button class="btn" data-a="regen" title="Write the Python again from the recorded steps">Regenerate</button>
        <button class="btn" data-a="history">Version History (${versions.length})</button>
        <a class="btn" href="/api/automation/scripts/${sid}/export">Download ${esc(data.python_file)}</a>
        <button class="btn" data-a="zip" title="With pytest.ini, requirements.txt, README.txt and RUN_TESTS.cmd">Download ready-to-run .zip</button></div>
      ${state.steps_changed ? `<p class="pill WARN" style="white-space:normal">The recorded steps changed after this Python was edited (steps v${esc(state.steps_version)} → v${esc(data.script.version)}). The saved Python still runs; Regenerate to include the new steps (it replaces your edits, which stay in Version History).</p>` : ""}
      <p class="muted">Runs with <code>pytest</code> (one test per data set) or <code>python ${esc(data.python_file)}</code>. Passwords are read from environment variables, never stored in the file.
        Only modules a browser test needs can be imported; running programs, network sockets, eval and deleting files are refused.</p>
      ${editing ? `<div class="row" style="gap:6px;flex-wrap:wrap;margin:6px 0">
          <input id="pyFind" placeholder="Find" style="width:180px"><input id="pyRepl" placeholder="Replace with" style="width:180px">
          <button class="btn small" data-a="find">Find next</button><button class="btn small" data-a="replace">Replace</button><button class="btn small" data-a="replaceAll">Replace all</button>
          <span class="spacer"></span><button class="btn small" data-a="undo" title="Ctrl+Z">Undo</button><button class="btn small" data-a="redo" title="Ctrl+Y">Redo</button>
          <input id="pyNote" placeholder="What changed (optional)" style="width:220px"></div>` : ""}
      <div id="pyErrors">${errorsHtml()}</div>
      <div class="pyed" id="pyed">
        <pre class="gutter" aria-hidden="true" id="pyGutter">${lines(source.split("\n").length)}</pre>
        <div class="pyed-stack"><pre class="code py pyed-hl" aria-hidden="true" id="pyHl">${highlightPython(source)}\n</pre>
          ${editing ? `<textarea id="pySrc" class="pyed-ta" spellcheck="false" wrap="off" aria-label="Python test">${esc(source)}</textarea>` : ""}</div></div>`;
    bind();
  };
  const errorsHtml = () => errors.length ? `<div class="pill FAIL" style="white-space:normal;display:block">${errors.map((e) =>
    `<a href="#" data-line="${e.line}">Line ${e.line}, column ${e.column}</a>: ${esc(e.message)}`).join("<br>")}</div>` : "";

  const ta = () => card.querySelector("#pySrc");
  const refresh = () => {
    const t = ta();
    if (!t) return;
    source = t.value;
    card.querySelector("#pyHl").innerHTML = highlightPython(source) + "\n";
    card.querySelector("#pyGutter").textContent = lines(source.split("\n").length);
    t.style.height = "0px"; t.style.height = `${t.scrollHeight}px`;
    t.style.width = "100%"; t.style.width = `${Math.max(t.scrollWidth, t.parentElement.clientWidth)}px`;
  };
  // every change made here goes through insertText, so Undo / Redo keep working
  const insert = (text) => { ta().focus(); if (!document.execCommand("insertText", false, text)) { const t = ta(); t.setRangeText(text, t.selectionStart, t.selectionEnd, "end"); } refresh(); };
  const goLine = (n) => {
    const t = ta();
    if (!t) { editing = true; render(); return goLine(n); }
    const starts = [0]; t.value.split("\n").forEach((l) => starts.push(starts[starts.length - 1] + l.length + 1));
    t.focus(); t.setSelectionRange(starts[n - 1] || 0, (starts[n] || t.value.length + 1) - 1);
    const lh = parseFloat(getComputedStyle(t).lineHeight) || 18;
    card.querySelector("#pyed").scrollTop = Math.max(0, (n - 4) * lh);
  };
  const showErrors = (errs) => { errors = errs || []; card.querySelector("#pyErrors").innerHTML = errorsHtml(); bindErrors(); };
  const bindErrors = () => card.querySelectorAll("[data-line]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); goLine(Number(a.dataset.line)); }));

  const findNext = () => {
    const t = ta(), q = card.querySelector("#pyFind").value;
    if (!q) return false;
    let i = t.value.indexOf(q, t.selectionEnd);
    if (i < 0) i = t.value.indexOf(q);
    if (i < 0) { toast("Not found", "info"); return false; }
    t.focus(); t.setSelectionRange(i, i + q.length);
    return true;
  };

  const actions = {
    edit: () => { editing = true; render(); refresh(); },
    cancel: () => { editing = false; source = data.python; errors = []; render(); },
    format: async () => {
      const r = await api(`/api/automation/scripts/${sid}/python/format`, { method: "POST", body: { source: ta().value } });
      const t = ta(); t.focus(); t.select(); insert(r.source); showErrors(r.errors);
      toast(r.ok ? "Formatted" : "Formatted; see the errors", r.ok ? "success" : "error");
    },
    validate: async () => {
      const r = await api(`/api/automation/scripts/${sid}/python/validate`, { method: "POST", body: { source: ta() ? ta().value : source } });
      showErrors(r.errors);
      toast(r.ok ? "No errors found" : `${r.errors.length} problem(s) found`, r.ok ? "success" : "error");
    },
    save: async () => {
      try {
        const r = await api(`/api/automation/scripts/${sid}/python`, { method: "PUT", body: { source: ta().value, note: card.querySelector("#pyNote")?.value || "" } });
        Object.assign(data, { python: r.python }); state = r.python_state; versions = r.python_versions; source = r.python;
        editing = false; errors = []; render();
        toast(`Saved as Python v${state.version}. This version runs and is downloaded.`, "success");
      } catch (err) { if (err.details?.errors) showErrors(err.details.errors); throw err; }
    },
    run: async () => {
      if (editing && ta().value !== data.python && !confirm("Run uses the saved version. Your unsaved changes are not included. Run anyway?")) return;
      const path = state.edited ? `/api/automation/scripts/${sid}/python/run` : `/api/automation/scripts/${sid}/run`;
      const r = await api(path, { method: "POST", body: {} });
      toast(`Run started (${r.run_id})`, "success");
      onRun?.(r.run_id);
    },
    revert: async () => {
      if (!confirm("Use the Python generated from the recorded steps again? Your edited versions stay in Version History.")) return;
      const r = await api(`/api/automation/scripts/${sid}/python/revert`, { method: "POST", body: {} });
      Object.assign(data, { python: r.python }); state = r.python_state; versions = r.python_versions; source = r.python; editing = false; render();
    },
    regen: async () => {
      let r;
      try { r = await api(`/api/automation/scripts/${sid}/python/regenerate`, { method: "POST", body: {} }); }
      catch (err) {
        if (err.code !== "PYTHON_EDITED" || !confirm(`${err.message}`)) throw err;
        r = await api(`/api/automation/scripts/${sid}/python/regenerate`, { method: "POST", body: { confirm: true } });
      }
      Object.assign(data, { python: r.python }); state = r.python_state; versions = r.python_versions; source = r.python; editing = false; render();
      toast("Regenerated from the recorded steps", "success");
    },
    history: () => {
      const { el, close } = openModal("Python version history", versions.length ? `<table class="data"><thead><tr><th>Version</th><th>Note</th><th>By</th><th>When</th><th>Steps</th><th></th></tr></thead><tbody>
        ${versions.map((v) => `<tr><td>v${v.version}${state.edited && v.version === state.version ? ' <span class="pill PASS">active</span>' : ""}</td><td>${esc(v.note || "")}</td><td>${esc(v.created_by || "")}</td>
        <td>${esc(dateTimeOf(v.created_at))}</td><td>v${esc(v.steps_version)}</td><td><button class="btn small" data-view="${v.version}">View</button> <button class="btn small" data-restore="${v.version}">Restore</button></td></tr>`).join("")}</tbody></table><div id="pyVer"></div>`
        : '<p class="muted">No saved versions yet. The Python is generated from the recorded steps until you save an edit.</p>', { wide: true });
      el.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", async () => {
        const { version } = await api(`/api/automation/scripts/${sid}/python/versions/${b.dataset.view}`);
        el.querySelector("#pyVer").innerHTML = `<div class="codeview"><pre class="gutter">${lines(version.source.split("\n").length)}</pre><pre class="code py">${highlightPython(version.source)}</pre></div>`;
      }));
      el.querySelectorAll("[data-restore]").forEach((b) => b.addEventListener("click", async () => {
        try {
          const r = await api(`/api/automation/scripts/${sid}/python/restore?version=${b.dataset.restore}`, { method: "POST", body: {} });
          Object.assign(data, { python: r.python }); state = r.python_state; versions = r.python_versions; source = r.python; editing = false; close(); render();
          toast(`Restored as Python v${state.version}`, "success");
        } catch (err) { toast(err.message, "error"); }
      }));
    },
    find: () => findNext(),
    replace: () => {
      const t = ta(), q = card.querySelector("#pyFind").value, w = card.querySelector("#pyRepl").value;
      if (!q) return;
      if (t.value.slice(t.selectionStart, t.selectionEnd) !== q && !findNext()) return;
      insert(w); findNext();
    },
    replaceAll: () => {
      const t = ta(), q = card.querySelector("#pyFind").value, w = card.querySelector("#pyRepl").value;
      if (!q) return;
      const n = t.value.split(q).length - 1;
      if (!n) { toast("Not found", "info"); return; }
      t.focus(); t.select(); insert(t.value.split(q).join(w));
      toast(`${n} replaced`, "success");
    },
    undo: () => { ta().focus(); document.execCommand("undo"); refresh(); },
    redo: () => { ta().focus(); document.execCommand("redo"); refresh(); },
    zip: () => data.downloadZip?.(),
  };

  function bind() {
    card.querySelectorAll("[data-a]").forEach((b) => b.addEventListener("click", async (e) => {
      e.preventDefault();
      b.disabled = true;
      try { await actions[b.dataset.a](); } catch (err) { toast(err.message, "error"); } finally { b.disabled = false; }
    }));
    bindErrors();
    const t = ta();
    if (!t) return;
    t.addEventListener("input", refresh);
    t.addEventListener("keydown", (e) => {
      if (e.key === "Tab" && !e.ctrlKey && !e.altKey) {  // indentation: four spaces (Shift+Tab removes them)
        e.preventDefault();
        if (e.shiftKey) {
          const s = t.value.lastIndexOf("\n", t.selectionStart - 1) + 1;
          if (t.value.slice(s, s + 4) === "    ") { t.setSelectionRange(s, s + 4); insert(""); }
        } else insert("    ");
      } else if (e.key === "Enter" && !e.ctrlKey) {  // keep the indentation of the line, one more after ':'
        e.preventDefault();
        const s = t.value.lastIndexOf("\n", t.selectionStart - 1) + 1;
        const line = t.value.slice(s, t.selectionStart);
        const indent = (line.match(/^\s*/) || [""])[0] + (/:\s*(#.*)?$/.test(line) ? "    " : "");
        insert("\n" + indent);
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); card.querySelector('[data-a="save"]')?.click(); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") { e.preventDefault(); card.querySelector("#pyFind")?.focus(); }
    });
    requestAnimationFrame(refresh);
  }
  render();
}
