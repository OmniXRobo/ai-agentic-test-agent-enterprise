import { api } from "../api.js";
import { toast, esc } from "../util.js";
import { loadBrand } from "../brand.js";
import { WORKFLOW_LOOKS } from "./workflow.js";

export async function mount(view) {
  const { settings } = await api("/api/settings");
  view.innerHTML = `
    <form class="settings card" id="prefs">
      <h2>Preferences</h2>
      <h3>Product name and logo</h3>
      <label>Product name <input name="brand_name" maxlength="40" required value="${esc(settings.ui.brand_name || "SkyGuard QA")}"></label>
      <label>Tagline <input name="brand_tagline" maxlength="80" value="${esc(settings.ui.brand_tagline ?? "Every release, cleared for takeoff.")}"></label>
      <label>Logo file path <input name="logo_path" maxlength="500" placeholder="C:\\Logos\\company-logo.png" value="${esc(settings.ui.logo_path || "")}" style="width:100%"></label>
      <p class="muted" id="logoNote">Shown at the top left, in the browser tab titles and on every report and PDF. The logo is a PNG, JPG, GIF, WEBP or SVG file on this PC, up to 2 MB; a square image looks best. Leave the path empty to use the built-in emblem.</p>
      <h3>Display</h3>
      <label>Open SkyGuard QA as
        <select name="open_as">
          <option value="browser" ${settings.ui.open_as !== "app" ? "selected" : ""}>A browser tab, with address bar (Microsoft Edge)</option>
          <option value="app" ${settings.ui.open_as === "app" ? "selected" : ""}>Its own app window, without address bar</option>
        </select>
      </label>
      <p class="muted">Used by the desktop icon the next time SkyGuard QA opens. Closing that browser window stops SkyGuard QA, the same as before.</p>
      <label>Theme
        <select name="theme">
          <option value="dark" ${settings.ui.theme === "dark" ? "selected" : ""}>Dark</option>
          <option value="light" ${settings.ui.theme === "light" ? "selected" : ""}>Light</option>
        </select>
      </label>
      <label>Agent Workflow look
        <select name="workflow_look" id="wfLook">
          ${WORKFLOW_LOOKS.map(([v, l]) => `<option value="${v}" ${(settings.ui.workflow_look || "classic") === v ? "selected" : ""}>${l}</option>`).join("")}
        </select>
      </label>
      <p class="muted" id="wfLookNote">${esc((WORKFLOW_LOOKS.find(([v]) => v === (settings.ui.workflow_look || "classic")) || WORKFLOW_LOOKS[0])[2])} Every look shows the same agents, states, activity and Stop / Pause / Resume buttons, works offline and follows the SkyGuard colours.</p>
      <label>Defect Creation Mode
        <select name="creation_mode">
          ${[["review", "Review Before Create"], ["automatic", "Automatic"], ["disabled", "Disabled"]]
            .map(([v, l]) => `<option value="${v}" ${settings.defects.creation_mode === v ? "selected" : ""}>${l}</option>`).join("")}
        </select>
      </label>
      <label><span><input type="checkbox" name="visual_cursor" ${settings.playback.visual_cursor ? "checked" : ""}> Show visual cursor during playback</span></label>
      <label><span><input type="checkbox" name="highlight" ${settings.playback.highlight ? "checked" : ""}> Highlight target elements during playback</span></label>
      <label>Cursor movement time (ms) <input type="number" name="cursor_speed_ms" min="0" max="2000" step="50" value="${settings.playback.cursor_speed_ms ?? 450}" style="width:110px"></label>
      <p class="muted">How long the playback cursor takes to glide to each element before it acts. 0 jumps straight there.</p>
      <label><span><input type="checkbox" name="click_sound" ${settings.playback.click_sound !== false ? "checked" : ""}> Quiet click sound during playback</span></label>
      <label>Click volume <input type="range" name="click_volume" min="0" max="1" step="0.05" value="${settings.playback.click_volume ?? 0.35}"></label>
      <h3>Browser for tests and automation runs</h3>
      <label>Browser <select name="browser">
          ${[["chromium", "Chromium (included)"], ["msedge", "Microsoft Edge"], ["chrome", "Google Chrome"]]
            .map(([v, l]) => `<option value="${v}" ${(settings.playback.browser || "chromium") === v ? "selected" : ""}>${l}</option>`).join("")}
        </select></label>
      <label>Browser window <select name="window_mode">
          ${[["maximized", "Maximized (every visible window, popups and new tabs)"], ["fixed", "Fixed size (below)"], ["default", "As the browser opens it"]]
            .map(([v, l]) => `<option value="${v}" ${(settings.playback.window_mode || "maximized") === v ? "selected" : ""}>${l}</option>`).join("")}
        </select></label>
      <label>Fixed size <input type="number" name="viewport_width" min="320" max="7680" value="${settings.playback.viewport_width ?? 1366}" style="width:90px"> ×
        <input type="number" name="viewport_height" min="240" max="4320" value="${settings.playback.viewport_height ?? 768}" style="width:90px"></label>
      <label>Element timeout (seconds) <input type="number" name="timeout_seconds" min="1" max="120" step="1" value="${settings.playback.timeout_seconds ?? 10}" style="width:110px"></label>
      <label><span><input type="checkbox" name="step_screenshots" ${settings.playback.step_screenshots !== false ? "checked" : ""}> Screenshot after every step (failures are always captured)</span></label>
      <label><span><input type="checkbox" name="video" ${settings.playback.video ? "checked" : ""}> Record a video of each test</span></label>
      <label><span><input type="checkbox" name="trace" ${settings.playback.trace ? "checked" : ""}> Save a Playwright trace of each test</span></label>
      <p class="muted">Every test plays and records in this browser: Play, Run selected, generated Python Playwright scripts and new recordings. Schedules can pick their own browsers. Edge and Chrome must be installed on this PC. The voice agent can change these too ("change the browser to Edge", "set timeout to 30 seconds").</p>
      <h3>Integration Comparison</h3>
      <label>OCR engine for screenshots <select name="ocr_engine">
          ${[["auto", "Automatic (bundled offline OCR, then Windows OCR, then Tesseract)"], ["rapidocr", "Bundled offline OCR"], ["windows", "Windows OCR"], ["tesseract", "Tesseract"]]
            .map(([v, l]) => `<option value="${v}" ${((settings.comparison || {}).ocr_engine || "auto") === v ? "selected" : ""}>${l}</option>`).join("")}
        </select></label>
      <label>OCR runtime folder (optional) <input name="ocr_runtime_path" value="${esc((settings.comparison || {}).ocr_runtime_path || "")}" placeholder="runtime\\ocr\\site-packages is used by default"></label>
      <label>Tesseract (optional) <input name="tesseract_path" value="${esc((settings.comparison || {}).tesseract_path || "")}" placeholder="C:\\Tools\\Tesseract\\tesseract.exe"></label>
      <label>Wait for live page data (ms) <input type="number" name="data_timeout_ms" min="1000" max="120000" step="1000" value="${(settings.comparison || {}).data_timeout_ms ?? 15000}" style="width:110px"></label>
      <label>Business vocabulary (one concept per line: <code>location: depot, hub</code>)
        <textarea name="concept_words" rows="3">${esc(Object.entries((settings.comparison || {}).concept_words || {}).map(([k, v]) => `${k}: ${v.join(", ")}`).join("\n"))}</textarea></label>
      <p class="muted">Concepts: id, location, date, status, quantity, money, percent, customer, product, employee, department. Extra words help the mapping recognise equivalent field names; values and units are still checked.</p>
      <p class="muted">Times always follow the Windows system clock. There is no separate time zone setting.</p>
      <div class="row"><button class="btn primary" type="submit">Save</button></div>
    </form>`;
  view.querySelector("#wfLook").addEventListener("change", (e) => {
    const d = (WORKFLOW_LOOKS.find(([v]) => v === e.target.value) || WORKFLOW_LOOKS[0])[2];
    view.querySelector("#wfLookNote").textContent = `${d} Every look shows the same agents, states, activity and Stop / Pause / Resume buttons, works offline and follows the SkyGuard colours.`;
  });
  view.querySelector("#prefs").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const theme = f.get("theme");
    try {
      await api("/api/settings", {
        method: "PATCH",
        body: {
          ui: { theme, open_as: f.get("open_as") || "browser", brand_name: String(f.get("brand_name") || "").trim(), brand_tagline: String(f.get("brand_tagline") || "").trim(),
                logo_path: String(f.get("logo_path") || "").trim(), workflow_look: f.get("workflow_look") || "classic" },
          defects: { creation_mode: f.get("creation_mode") },
          playback: {
            visual_cursor: f.get("visual_cursor") === "on", highlight: f.get("highlight") === "on",
            cursor_speed_ms: Math.max(0, Math.min(2000, Number(f.get("cursor_speed_ms")) || 0)),
            click_sound: f.get("click_sound") === "on", click_volume: Number(f.get("click_volume")),
            browser: f.get("browser"), timeout_seconds: Math.max(1, Math.min(120, Number(f.get("timeout_seconds")) || 10)),
            step_screenshots: f.get("step_screenshots") === "on", video: f.get("video") === "on", trace: f.get("trace") === "on",
            window_mode: f.get("window_mode") || "maximized",
            viewport_width: Math.max(320, Math.min(7680, Math.round(Number(f.get("viewport_width")) || 1366))),
            viewport_height: Math.max(240, Math.min(4320, Math.round(Number(f.get("viewport_height")) || 768))),
          },
          comparison: {
            ocr_engine: f.get("ocr_engine") || "auto", ocr_runtime_path: String(f.get("ocr_runtime_path") || "").trim(),
            tesseract_path: String(f.get("tesseract_path") || "").trim(),
            data_timeout_ms: Math.max(1000, Math.min(120000, Math.round(Number(f.get("data_timeout_ms")) || 15000))),
            concept_words: Object.fromEntries(String(f.get("concept_words") || "").split("\n").map((l) => l.split(":"))
              .filter((p) => p.length === 2 && p[0].trim()).map(([k, v]) => [k.trim().toLowerCase(), v.split(",").map((w) => w.trim()).filter(Boolean)])),
          },
        },
      });
      document.documentElement.dataset.theme = theme;
      try { localStorage.setItem("qa.theme", theme); localStorage.setItem("qa.workflowLook", f.get("workflow_look") || "classic"); } catch { /* ignore */ }
      const b = await loadBrand();
      toast(b.logo_problem ? `Preferences saved. ${b.logo_problem}` : "Preferences saved", b.logo_problem ? "warning" : "success");
    } catch (err) {
      toast(err.message, "error");
    }
  });
}
