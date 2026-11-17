"""23.11.0: a small site with real delayed AJAX for the Smart Wait tests: a dependent (cascading) dropdown, a filter
that refreshes a table with a DataTables-style "processing" box, a jQuery-like active counter and a loader overlay."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PAGE = """<!doctype html><html><head><title>Orders</title>
<style>.dataTables_processing{position:fixed;top:40%;left:40%;padding:20px;background:#eee}
.loader{position:fixed;inset:0;background:rgba(0,0,0,.2)}</style></head><body>
<h1>Orders</h1>
<label>Country <select id="country"><option value="">Choose</option><option>UAE</option><option>India</option></select></label>
<label>City <select id="city"><option value="">Choose a country first</option></select></label>
<button id="apply">Apply filter</button>
<div id="processing" class="dataTables_processing" style="display:none">Processing...</div>
<table id="orders"><tbody><tr><td>SO-1</td><td>London</td></tr></tbody></table>
<p id="status">ready</p>
<script>
window.jQuery = { active: 0 };
const delay = (new URLSearchParams(location.search)).get("delay") || "1200";
async function get(url) {
  window.jQuery.active++;
  try { const r = await fetch(url + (url.includes("?") ? "&" : "?") + "delay=" + delay); return await r.json(); }
  finally { window.jQuery.active--; }
}
document.getElementById("country").addEventListener("change", async (e) => {
  const city = document.getElementById("city");
  city.innerHTML = '<option value="">Loading...</option>';
  const data = await get("/api/cities?country=" + encodeURIComponent(e.target.value));
  city.innerHTML = '<option value="">Choose</option>' + data.map((c) => `<option>${c}</option>`).join("");
});
document.getElementById("apply").addEventListener("click", async () => {
  const p = document.getElementById("processing");
  p.style.display = "block";
  document.getElementById("status").textContent = "loading";
  const rows = await get("/api/orders?city=" + encodeURIComponent(document.getElementById("city").value));
  document.querySelector("#orders tbody").innerHTML = rows.map((r) => `<tr><td>${r[0]}</td><td>${r[1]}</td></tr>`).join("");
  p.style.display = "none";
  document.getElementById("status").textContent = rows.length + " orders";
});
</script></body></html>"""

CITIES = {"UAE": ["Dubai", "Abu Dhabi"], "India": ["Mumbai", "Delhi"]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        delay = min(int(q.get("delay") or 1200), 10000) / 1000.0
        if u.path == "/api/cities":
            time.sleep(delay)
            return self._json(CITIES.get(q.get("country", ""), []))
        if u.path == "/api/orders":
            time.sleep(delay)
            city = q.get("city") or "?"
            return self._json([["SO-10", city], ["SO-11", city]])
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"
