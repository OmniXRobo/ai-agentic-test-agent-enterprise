"""A stand-in transport application for the Adaptive Operations Agent tests (no external dependencies).

Deliberately poor HTML: labels in table cells, no ids, generated names, a clickable <div> button, server-side
validation only. It has the roles of the transport example (Administrator, Entry Operator, Manager, Second-Level
Manager, Driver, Officer), a trip lifecycle (Planned -> Approved -> Authorized -> Bus Ready -> Accepted), driver
acceptance that needs a driver code typed by a person, an account with a one-time code, pagination, and "variants"
that change the markup (ids, names, button text, a missing button) to exercise self-healing and manual recovery.

    server, url = start()    ...    server.shutdown()
"""

from __future__ import annotations

import datetime as _dt
import html
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

USERS = {
    "admin": ("Admin#2026", "Administrator"),
    "operator": ("Op#2026x", "Entry Operator"),
    "manager": ("Mgr#2026x", "Manager"),
    "manager2": ("Mgr2#2026", "Second-Level Manager"),
    "driver1": ("Drv#2026x", "Driver"),
    "officer": ("Off#2026x", "Officer"),
    "otpuser": ("Otp#2026x", "Entry Operator"),
}
OTP_CODE = "482913"
DRIVER_CODE = "7788"
ROUTES = ["Dubai to Abu Dhabi", "Abu Dhabi to Dubai", "Dubai to Sharjah"]
BUSES = ["BUS-101", "BUS-102", "BUS-103"]
DRIVERS = ["Ahmed Khan", "Bilal Saeed", "Carlos Diaz", "Dina Yousef"]
PAGE_SIZE = 5
DURATION = _dt.timedelta(hours=3)


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.trips: list[dict[str, Any]] = []
        self.variant = 1
        self.posts = 0  # trip creations that reached the server (duplicate-submission check)
        self.fail_next_save = False


STATE = State()


def page(title: str, body: str, user: dict[str, Any] | None = None, extra_head: str = "") -> bytes:
    who = ""
    if user and user.get("ok"):
        who = (f'<div class="topbar">Logged in as {html.escape(user["name"])} ({html.escape(user["role"])}) | '
               '<a href="/logout">Logout</a> | <a href="/trips">Trips</a> | <a href="/trips/new">New trip</a></div>')
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)} - FleetOps</title>"
            f"<style>.alert.error{{color:#b00}} .alert.success{{color:#070}} .btn{{cursor:pointer;border:1px solid #888;"
            f"padding:3px 8px;display:inline-block}}</style>{extra_head}</head><body>{who}<h2>{html.escape(title)}</h2>"
            f"{body}</body></html>").encode("utf-8")


def trip_form(values: dict[str, str], error: str = "") -> str:
    v = STATE.variant
    n = {1: ("tn", "rt", "dd", "dt", "bs", "dr"), 2: ("x91_a", "x91_b", "x91_c", "x91_d", "x91_e", "x91_f"),
         3: ("tn", "rt", "dd", "dt", "bs", "dr")}[v if v in (1, 2, 3) else 1]

    def opts(items: list[str], cur: str) -> str:
        return "<option value=''>-- choose --</option>" + "".join(
            f"<option{' selected' if cur == i else ''}>{html.escape(i)}</option>" for i in items)

    label = {1: "Save trip", 2: "Save Trip", 3: "Store"}.get(v, "Save trip")
    submit = (f'<div class="btn" role="button" onclick="document.forms[0].requestSubmit()">{label}</div>' if v == 2 else
              f'<input type="submit" value="{label}">' if v == 1 else
              f'<span class="btn" onclick="document.forms[0].requestSubmit()">{label}</span>')
    err = f'<div class="alert error" role="alert">{html.escape(error)}</div>' if error else ""
    gen = secrets.token_hex(5)  # a generated id that changes on every load
    return (f"{err}<form method='post' action='/trips/new' id='frm_{gen}'><table>"
            f"<tr><td>Trip No</td><td><input name='{n[0]}' id='inp_{gen}_1' value='{html.escape(values.get('trip_no', ''))}'></td></tr>"
            f"<tr><td>Route</td><td><select name='{n[1]}'>{opts(ROUTES, values.get('route', ''))}</select></td></tr>"
            f"<tr><td>Departure date</td><td><input name='{n[2]}' placeholder='YYYY-MM-DD' value='{html.escape(values.get('date', ''))}'></td></tr>"
            f"<tr><td>Departure time</td><td><input name='{n[3]}' placeholder='HH:MM' value='{html.escape(values.get('time', ''))}'></td></tr>"
            f"<tr><td>Bus</td><td><select name='{n[4]}'>{opts(BUSES, values.get('bus', ''))}</select></td></tr>"
            f"<tr><td>Driver</td><td><select name='{n[5]}'>{opts(DRIVERS, values.get('driver', ''))}</select></td></tr>"
            f"</table>{submit}</form>"), n


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _user(self) -> dict[str, Any] | None:
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "fo_sid":
                return STATE.sessions.get(v)
        return None

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

    def _form(self) -> dict[str, str]:
        n = int(self.headers.get("Content-Length") or 0)
        return {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8")).items()}

    def do_GET(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        user = self._user()
        if url.path == "/__variant":
            STATE.variant = int(q.get("v", "1"))
            return self._send(b"ok")
        if url.path == "/__reset":
            STATE.reset()
            return self._send(b"ok")
        if url.path == "/login" or (url.path == "/" and not user):
            return self._send(page("Sign in", "<form method='post' action='/login'><table>"
                                   "<tr><td>User ID</td><td><input name='u'></td></tr>"
                                   "<tr><td>Password</td><td><input type='password' name='p'></td></tr></table>"
                                   "<button type='submit'>Sign in</button></form>"))
        if url.path == "/logout":
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                k, _, v = part.strip().partition("=")
                if k == "fo_sid":
                    STATE.sessions.pop(v, None)
            return self._redirect("/login", {"Set-Cookie": "fo_sid=; Max-Age=0; Path=/"})
        if user and not user.get("ok"):
            return self._send(page("Verification", "<p>Enter the one-time code sent to your phone.</p>"
                                   "<form method='post' action='/otp'><table><tr><td>One-time code</td>"
                                   "<td><input name='otp' autocomplete='one-time-code'></td></tr></table>"
                                   "<button type='submit'>Verify</button></form>"))
        if not user:
            return self._redirect("/login")
        if url.path in ("/", "/home"):
            return self._send(page("Dashboard", "<p>Welcome to FleetOps.</p>", user))
        if url.path == "/trips":
            rows = [t for t in STATE.trips if not q.get("q") or q["q"].lower() in t["trip_no"].lower()]
            p = max(1, int(q.get("page", "1")))
            chunk = rows[(p - 1) * PAGE_SIZE: p * PAGE_SIZE]
            trs = []
            for t in chunk:
                act = ""
                if user["role"] == "Manager" and t["status"] == "Planned":
                    act = f"<form method='post' action='/trips/{t['trip_no']}/approve'><button>Approve</button></form>"
                elif user["role"] == "Second-Level Manager" and t["status"] == "Approved":
                    act = f"<form method='post' action='/trips/{t['trip_no']}/authorize'><button>Authorize</button></form>"
                elif user["role"] == "Driver" and t["status"] == "Authorized":
                    act = f"<a href='/trips/{t['trip_no']}/accept'>Accept</a>"
                trs.append(f"<tr><td>{html.escape(t['trip_no'])}</td><td>{html.escape(t['route'])}</td><td>{t['date']}</td>"
                           f"<td>{t['time']}</td><td>{t['bus']}</td><td>{html.escape(t['driver'])}</td>"
                           f"<td>{t['status']}</td><td>{act}</td></tr>")
            nav = ""
            if p * PAGE_SIZE < len(rows):
                nav += f" <a href='/trips?page={p + 1}{'&q=' + q['q'] if q.get('q') else ''}'>Next</a>"
            if p > 1:
                nav = f"<a href='/trips?page={p - 1}'>Previous</a>" + nav
            body = ("<table class='grid'><thead><tr><th>Trip No</th><th>Route</th><th>Date</th><th>Time</th><th>Bus</th>"
                    "<th>Driver</th><th>Status</th><th></th></tr></thead><tbody>" + "".join(trs) + "</tbody></table>"
                    f"<div class='pagination'>Page {p}{nav}</div>")
            return self._send(page("Trips", body, user))
        if url.path == "/trips/new":
            if user["role"] not in ("Entry Operator", "Administrator"):
                return self._send(page("Not allowed", "<p class='alert error'>Your role cannot create trips.</p>", user), 403)
            form, _ = trip_form({})
            return self._send(page("New trip", form, user))
        if url.path.endswith("/accept") and url.path.startswith("/trips/"):
            no = url.path.split("/")[2]
            return self._send(page("Driver acceptance", f"<p>Trip {html.escape(no)}</p><form method='post' "
                                   f"action='/trips/{html.escape(no)}/accept'><table><tr><td>Driver authentication code</td>"
                                   "<td><input name='code' autocomplete='one-time-code'></td></tr></table>"
                                   "<button>Accept trip</button></form>", user))
        return self._send(page("Not found", "<p>Not found</p>", user), 404)

    def do_POST(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        form = self._form()
        user = self._user()
        if url.path == "/login":
            u, pw = form.get("u", ""), form.get("p", "")
            if u in USERS and USERS[u][0] == pw:
                sid = secrets.token_hex(12)
                STATE.sessions[sid] = {"name": u, "role": USERS[u][1], "ok": u != "otpuser"}
                return self._redirect("/", {"Set-Cookie": f"fo_sid={sid}; Path=/; HttpOnly"})
            return self._send(page("Sign in", "<div class='alert error' role='alert'>Invalid user ID or password.</div>"
                                   "<form method='post' action='/login'><table><tr><td>User ID</td><td><input name='u'></td></tr>"
                                   "<tr><td>Password</td><td><input type='password' name='p'></td></tr></table>"
                                   "<button type='submit'>Sign in</button></form>"))
        if url.path == "/otp" and user:
            if form.get("otp") == OTP_CODE:
                user["ok"] = True
                return self._redirect("/")
            return self._redirect("/")
        if not user or not user.get("ok"):
            return self._redirect("/login")
        if url.path == "/trips/new":
            if user["role"] not in ("Entry Operator", "Administrator"):
                return self._send(page("Not allowed", "<p class='alert error'>Your role cannot create trips.</p>", user), 403)
            _, n = trip_form({})
            alt = ("x91_a", "x91_b", "x91_c", "x91_d", "x91_e", "x91_f")
            vals = {k: (form.get(a) or form.get(b) or "").strip() for k, a, b in
                    zip(("trip_no", "route", "date", "time", "bus", "driver"), n, alt)}
            vals.update({k: (form.get(c) or vals[k]) for k, c in zip(("trip_no", "route", "date", "time", "bus", "driver"),
                                                                     ("tn", "rt", "dd", "dt", "bs", "dr"))})
            err = validate(vals)
            if STATE.fail_next_save:
                STATE.fail_next_save = False
                err = "Server busy, please try again."
            if err:
                f, _ = trip_form(vals, err)
                return self._send(page("New trip", f, user))
            with STATE.lock:
                STATE.posts += 1
                STATE.trips.append({**vals, "status": "Planned", "created_by": user["name"]})
            return self._send(page("New trip", f"<div class='alert success' role='status'>Trip {html.escape(vals['trip_no'])} "
                                   "created successfully.</div>" + trip_form({})[0], user))
        parts = url.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "trips":
            trip = next((t for t in STATE.trips if t["trip_no"] == parts[1]), None)
            if not trip:
                return self._send(page("Not found", "<p class='alert error'>No such trip.</p>", user), 404)
            need = {"approve": ("Manager", "Planned", "Approved"), "authorize": ("Second-Level Manager", "Approved", "Authorized"),
                    "accept": ("Driver", "Authorized", "Accepted")}.get(parts[2])
            if not need or user["role"] != need[0] or trip["status"] != need[1]:
                return self._send(page("Not allowed", "<p class='alert error' role='alert'>Not allowed.</p>", user), 403)
            if parts[2] == "accept" and form.get("code") != DRIVER_CODE:
                return self._send(page("Driver acceptance", "<p class='alert error' role='alert'>Wrong code.</p>", user))
            trip["status"] = need[2]
            return self._send(page("Trips", f"<div class='alert success' role='status'>Trip {html.escape(trip['trip_no'])} "
                                   f"{need[2].lower()}.</div><p><a href='/trips'>Back to trips</a></p>", user))
        return self._send(page("Not found", "<p>Not found</p>", user), 404)


def validate(v: dict[str, str]) -> str:
    for k, label in (("trip_no", "Trip No"), ("route", "Route"), ("date", "Departure date"), ("time", "Departure time"),
                     ("bus", "Bus"), ("driver", "Driver")):
        if not v.get(k):
            return f"{label} is required."
    try:
        d = _dt.datetime.strptime(f"{v['date']} {v['time']}", "%Y-%m-%d %H:%M")
    except ValueError:
        return "Departure date or time is invalid."
    if d.date() < _dt.date.today():
        return "Planned trips must depart today or later."
    if any(t["trip_no"].lower() == v["trip_no"].lower() for t in STATE.trips):
        return f"Trip number {v['trip_no']} already exists."
    for t in STATE.trips:
        other = _dt.datetime.strptime(f"{t['date']} {t['time']}", "%Y-%m-%d %H:%M")
        if t["driver"] == v["driver"] and d < other + DURATION and other < d + DURATION:
            return f"Driver {v['driver']} is already assigned to trip {t['trip_no']} at that time."
        if t["bus"] == v["bus"] and d < other + DURATION and other < d + DURATION:
            return f"Bus {v['bus']} is not available at that time."
    return ""


def start(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


if __name__ == "__main__":
    import sys

    srv, base = start(int(sys.argv[1]) if len(sys.argv) > 1 else 8790)
    print(base, flush=True)
    threading.Event().wait()
