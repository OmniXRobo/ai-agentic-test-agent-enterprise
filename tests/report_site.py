"""A stand-in reporting application for the 23.10.0 guided-recording tests (no external dependencies).

Sign-in page -> Home with a menu -> Reports -> Orders Summary with a Region filter and a Search button, server-side
pagination of 15 rows (North: 15 + 15 + 10 = 40 orders), each Order No links to /orders/<no> with its item lines.
A "Delete" button exists on the report so the tests can check that a recording never keeps it.

    server, url = start()    ...    server.shutdown()
"""

from __future__ import annotations

import html
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

USER, PASSWORD = "analyst", "Rep#2026x"
PAGE = 15
REGIONS = ["North", "South"]


def orders() -> list[dict[str, Any]]:
    out = []
    for i in range(1, 51):
        region = "North" if i <= 40 else "South"
        items = [{"item": f"Item {chr(64 + j)}", "qty": j, "price": 10 * i + j} for j in range(1, 1 + (i % 3) + 1)]
        out.append({"no": f"SO-{1000 + i}", "customer": f"Customer {i:02d}", "region": region, "items": items,
                    "total": sum(x["qty"] * x["price"] for x in items)})
    return out


ORDERS = orders()
SESSIONS: set[str] = set()
DELETES: list[str] = []


def page(title: str, body: str) -> bytes:
    return (f"<!doctype html><html><head><meta charset=utf-8><title>{html.escape(title)}</title></head><body>"
            f"<nav><a href='/'>Home</a> <a href='/reports'>Reports</a> <a href='/logout'>Sign out</a></nav>"
            f"<h1>{html.escape(title)}</h1>{body}</body></html>").encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, body: bytes, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, to: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", to)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _signed_in(self) -> bool:
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "rs" and v in SESSIONS:
                return True
        return False

    def do_GET(self) -> None:  # noqa: N802
        u = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/login":
            self._send(page("Sign in", "<form method=post action='/login'><label>User <input name=user></label>"
                                       "<label>Password <input type=password name=pw></label>"
                                       "<button type=submit>Sign in</button></form>"))
            return
        if not self._signed_in():
            self._redirect("/login")
            return
        if u.path == "/logout":
            self._redirect("/login", {"Set-Cookie": "rs=; Max-Age=0; Path=/"})
            return
        if u.path == "/":
            self._send(page("Home", "<p>Welcome.</p><ul><li><a href='/reports'>Reports</a></li></ul>"))
            return
        if u.path == "/reports":
            self._send(page("Reports", "<ul><li><a href='/reports/orders'>Orders Summary</a></li></ul>"))
            return
        if u.path == "/reports/orders":
            region = q.get("region", "")
            n = max(1, int(q.get("page", "1") or 1))
            rows = [o for o in ORDERS if not region or o["region"] == region]
            pages = max(1, (len(rows) + PAGE - 1) // PAGE)
            shown = rows[(n - 1) * PAGE:n * PAGE]
            opts = "".join(f"<option{' selected' if r == region else ''}>{r}</option>" for r in [""] + REGIONS)
            trs = "".join(f"<tr><td><a href='/orders/{o['no']}'>{o['no']}</a></td><td>{o['customer']}</td>"
                          f"<td>{o['region']}</td><td>{o['total']}</td></tr>" for o in shown)
            base = f"/reports/orders?region={region}&page="
            links = " ".join(f"<a href='{base}{i}'>{i}</a>" if i != n else f"<b>{i}</b>" for i in range(1, pages + 1))
            nxt = f" <a href='{base}{n + 1}' rel=next>Next</a>" if n < pages else ""
            self._send(page("Orders Summary", f"""
              <form method=get action='/reports/orders'><label for=rg>Region</label><select id=rg name=region>{opts}</select>
              <button type=submit>Search</button></form>
              <p>Showing {len(shown)} of {len(rows)} orders</p>
              <table id=orders><thead><tr><th>Order No</th><th>Customer</th><th>Region</th><th>Total</th></tr></thead>
              <tbody>{trs}</tbody></table>
              <div class=pagination>{links}{nxt}</div>
              <form method=post action='/delete'><button type=submit>Delete selected</button></form>"""))
            return
        if u.path.startswith("/orders/"):
            no = u.path.rsplit("/", 1)[1]
            o = next((x for x in ORDERS if x["no"] == no), None)
            if not o:
                self._send(page("Not found", "<p>No such order.</p>"), 404)
                return
            trs = "".join(f"<tr><td>{x['item']}</td><td>{x['qty']}</td><td>{x['price']}</td>"
                          f"<td>{x['qty'] * x['price']}</td></tr>" for x in o["items"])
            self._send(page(f"Order {no}", f"<table id=items><thead><tr><th>Item</th><th>Qty</th><th>Price</th>"
                                           f"<th>Amount</th></tr></thead><tbody>{trs}</tbody></table>"))
            return
        self._send(page("Not found", ""), 404)

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        if self.path == "/login":
            if form.get("user") == USER and form.get("pw") == PASSWORD:
                sid = secrets.token_hex(8)
                SESSIONS.add(sid)
                self._redirect("/", {"Set-Cookie": f"rs={sid}; Path=/; HttpOnly"})
            else:
                self._send(page("Sign in", "<p>Wrong user or password.</p>"), 401)
            return
        if self.path == "/delete":
            DELETES.append("delete")
            self._redirect("/reports/orders")
            return
        self._send(page("Not found", ""), 404)


def start(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    import threading

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"
