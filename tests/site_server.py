"""Local HTTP server for the test site in tests/site (test fixture only)."""

from __future__ import annotations

import json
import re
import secrets
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl

SITE = Path(__file__).parent / "site"


# 23.3.24: a small login flow with a CSRF token, a session cookie and a bearer token, for performance recording tests
PERF_SESSIONS: dict[str, dict] = {}  # sid -> {"csrf": ..., "user": ...}
PERF_TOKENS: set[str] = set()
PERF_PASSWORD = "S3cret+pass/1"

LOGIN_PAGE = """<!doctype html><html><head><title>Perf login</title></head><body>
<form method="post" action="/perf/login"><input type="hidden" name="csrf" value="{csrf}">
<input name="username" id="user"><input type="password" name="password" id="pass">
<button id="go" type="submit">Sign in</button></form></body></html>"""

HOME_PAGE = """<!doctype html><html><head><title>Perf home</title></head><body><h1>Welcome {user}</h1>
<div id="orders">loading</div>
<script>
fetch("/perf/api/token", {{method: "POST", headers: {{"Content-Type": "application/json"}}, body: JSON.stringify({{user: "{user}"}})}})
  .then((r) => r.json())
  .then((t) => fetch("/perf/api/orders?page=1", {{headers: {{"Authorization": "Bearer " + t.data.access_token}}}}))
  .then((r) => r.json()).then((o) => {{ document.getElementById("orders").textContent = "orders: " + o.count; }});
</script></body></html>"""


def _perf_sid(handler) -> str | None:
    for part in (handler.headers.get("Cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == "sid":
            return v
    return None


class SiteHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, status: int, body: bytes = b"", ctype: str = "text/html", headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _perf_get(self) -> bool:
        path = self.path.split("?")[0]
        if path == "/perf/login":
            sid = secrets.token_hex(12)
            csrf = secrets.token_urlsafe(24)
            PERF_SESSIONS[sid] = {"csrf": csrf, "user": None}
            self._send(200, LOGIN_PAGE.format(csrf=csrf).encode(), headers={"Set-Cookie": f"sid={sid}; Path=/"})
            return True
        if path == "/perf/home":
            s = PERF_SESSIONS.get(_perf_sid(self) or "")
            if not s or not s["user"]:
                self._send(302, headers={"Location": "/perf/login"})
            else:
                self._send(200, HOME_PAGE.format(user=s["user"]).encode())
            return True
        if path == "/perf/api/orders":
            auth = self.headers.get("Authorization") or ""
            ok = auth.startswith("Bearer ") and auth[7:] in PERF_TOKENS
            body = json.dumps({"count": 3} if ok else {"error": "unauthorized"}).encode()
            self._send(200 if ok else 401, body, "application/json")
            return True
        return False

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode()
        if path == "/perf/login":
            form = dict(parse_qsl(raw))
            s = PERF_SESSIONS.get(_perf_sid(self) or "")
            if s and form.get("csrf") == s["csrf"] and form.get("password") == PERF_PASSWORD and form.get("username"):
                s["user"] = form["username"]
                self._send(302, headers={"Location": "/perf/home"})
            else:
                self._send(403, b"Forbidden: bad token or password")
            return
        if path == "/perf/api/token":
            s = PERF_SESSIONS.get(_perf_sid(self) or "")
            if not s or not s["user"]:
                self._send(401, b'{"error": "not signed in"}', "application/json")
                return
            token = secrets.token_hex(20)
            PERF_TOKENS.add(token)
            self._send(200, json.dumps({"data": {"access_token": token, "expires_in": 300}}).encode(), "application/json")
            return
        self._send(404, b"not found", "text/plain")

    def do_GET(self):
        if self.path.startswith("/perf/") and self._perf_get():
            return
        m = re.search(r"/api/items/(\d+)$", self.path)
        if m:
            body = json.dumps({"id": int(m.group(1)), "name": f"Item {m.group(1)}"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.split("?")[0].endswith("/api/inventory"):
            items = [{"id": 4, "name": "Backpack", "price": 29.99}, {"id": 0, "name": "Bike Light", "price": 9.99}]
            body = json.dumps(items).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


def start() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SiteHandler, directory=str(SITE)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"
