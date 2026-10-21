import { api } from "../api.js";
import { subscribe } from "../store.js";
import { esc, dateTimeOf, toast } from "../util.js";

let unsub = null;

async function render(view) {
  const { notifications, unread } = await api("/api/notifications?limit=200");
  view.innerHTML = `
    <div class="row" style="margin-bottom:12px">
      <span class="muted">${unread} unread</span><span class="spacer"></span>
      <button class="btn" id="readAll" ${unread ? "" : "disabled"}>Mark all read</button>
    </div>
    <div class="card" style="padding:0">
      <ul class="note-list">
        ${notifications.length ? notifications.map((n) => `
          <li class="${n.read ? "" : "unread"}" data-id="${n.id}">
            <span class="note-dot ${esc(n.severity)}"></span>
            <div style="flex:1"><div class="note-title">${esc(n.title)}</div><div>${esc(n.message || "")}</div>
              <div class="note-time">${esc(dateTimeOf(n.timestamp))}${n.run_id ? " · " + esc(n.run_id) : ""}</div></div>
            ${n.read ? "" : `<button class="link-btn" data-read="${n.id}">Mark read</button>`}
          </li>`).join("") : `<li class="muted">No notifications yet. They appear when runs finish, defects are created or a service becomes unavailable.</li>`}
      </ul>
    </div>`;
  view.querySelector("#readAll").addEventListener("click", () => markRead(view));
  view.querySelectorAll("[data-read]").forEach((b) => b.addEventListener("click", () => markRead(view, [Number(b.dataset.read)])));
}

async function markRead(view, ids) {
  try {
    await api("/api/notifications/read", { method: "POST", body: ids ? { ids } : {} });
    await render(view);
  } catch (err) {
    toast(err.message, "error");
  }
}

export async function mount(view) {
  await render(view);
  unsub = subscribe((c) => { if (c.type === "notification") render(view); });
}

export function unmount() {
  if (unsub) unsub();
  unsub = null;
}
