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
          playback: { visual_cursor: f.get("visual_cursor") === "on", highlight: f.get("highlight") === "on" },
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
