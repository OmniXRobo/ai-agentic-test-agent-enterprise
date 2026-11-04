import { api } from "../api.js";
import { toast } from "../util.js";

export async function mount(view) {
  const { settings } = await api("/api/settings");
  view.innerHTML = `
    <form class="settings card" id="prefs">
      <h2>Preferences</h2>
      <label>Theme
        <select name="theme">
          <option value="dark" ${settings.ui.theme === "dark" ? "selected" : ""}>Dark</option>
          <option value="light" ${settings.ui.theme === "light" ? "selected" : ""}>Light</option>
        </select>
      </label>
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
      <h3>Automation runs</h3>
      <label>Browser <select name="browser">
          ${[["chromium", "Chromium (included)"], ["msedge", "Microsoft Edge"], ["chrome", "Google Chrome"], ["firefox", "Firefox"], ["webkit", "WebKit"]]
            .map(([v, l]) => `<option value="${v}" ${(settings.playback.browser || "chromium") === v ? "selected" : ""}>${l}</option>`).join("")}
        </select></label>
      <label>Element timeout (seconds) <input type="number" name="timeout_seconds" min="1" max="120" step="1" value="${settings.playback.timeout_seconds ?? 10}" style="width:110px"></label>
      <label><span><input type="checkbox" name="step_screenshots" ${settings.playback.step_screenshots !== false ? "checked" : ""}> Screenshot after every step (failures are always captured)</span></label>
      <label><span><input type="checkbox" name="video" ${settings.playback.video ? "checked" : ""}> Record a video of each test</span></label>
      <label><span><input type="checkbox" name="trace" ${settings.playback.trace ? "checked" : ""}> Save a Playwright trace of each test</span></label>
      <p class="muted">Used when generated Python Playwright scripts run. Edge and Chrome must be installed on this PC. The voice agent can change these too ("change the browser to Edge", "set timeout to 30 seconds").</p>
      <p class="muted">Times always follow the Windows system clock. There is no separate time zone setting.</p>
      <div class="row"><button class="btn primary" type="submit">Save</button></div>
    </form>`;
  view.querySelector("#prefs").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const theme = f.get("theme");
    try {
      await api("/api/settings", {
        method: "PATCH",
        body: {
          ui: { theme },
          defects: { creation_mode: f.get("creation_mode") },
          playback: {
            visual_cursor: f.get("visual_cursor") === "on", highlight: f.get("highlight") === "on",
            cursor_speed_ms: Math.max(0, Math.min(2000, Number(f.get("cursor_speed_ms")) || 0)),
            click_sound: f.get("click_sound") === "on", click_volume: Number(f.get("click_volume")),
            browser: f.get("browser"), timeout_seconds: Math.max(1, Math.min(120, Number(f.get("timeout_seconds")) || 10)),
            step_screenshots: f.get("step_screenshots") === "on", video: f.get("video") === "on", trace: f.get("trace") === "on",
          },
        },
      });
      document.documentElement.dataset.theme = theme;
      try { localStorage.setItem("qa.theme", theme); } catch { /* ignore */ }
      toast("Preferences saved", "success");
    } catch (err) {
      toast(err.message, "error");
    }
  });
}
