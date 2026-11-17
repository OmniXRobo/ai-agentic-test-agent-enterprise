"""A stand-in transport application with the complete multi-role trip process of the 23.11.1 request:

    Entry Operator creates a trip           -> the application generates the number (TRP-0001), status Pending Approval
    Manager opens the trip and approves      -> Approved
    Authorizing Officer authorizes           -> Authorized
    the Driver enters his authentication code on the trip page -> Driver Authenticated
    Entry Operator records the completed trip (odometer, fuel)  -> Recorded

Actions are on each trip's own page (/trips/TRP-0001), not in the list. Every page shows "Logged in as <user>
(<role>)". Server-side rules: the trip date must be in the future; a driver cannot have two trips on the same day;
From and To must differ; every field is required. Role checks are done by the server, whatever the page shows.
A user "officer2" (Authorizing Officer) must enter a one-time code after the password.

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
    "operator": ("Op#2026x", "Entry Operator"),
    "manager": ("Mgr#2026x", "Manager"),
    "officer": ("Off#2026x", "Authorizing Officer"),
    "officer2": ("Off2#2026x", "Authorizing Officer"),
    "aslam": ("Drv#2026x", "Driver"),
}
OTP_CODE = "551204"
DRIVER_CODES = {"Aslam Khan": "7788", "Bilal Saeed": "4411"}
DRIVER_USER = {"Aslam Khan": "aslam"}
CITIES = ["Dubai", "Abu Dhabi", "Sharjah", "Al Ain"]
DRIVERS = ["Aslam Khan", "Bilal Saeed", "Carlos Diaz"]
VEHICLES = ["BUS-101", "BUS-102", "VAN-7"]
FLOW = {"approve": ("Manager", "Pending Approval", "Approved"),
        "authorize": ("Authorizing Officer", "Approved", "Authorized"),
        "authenticate": ("Driver", "Authorized", "Driver Authenticated"),
        "record": ("Entry Operator", "Driver Authenticated", "Recorded")}


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.trips: list[dict[str, Any]] = []
        self.posts = 0
        self.history: list[tuple[str, str, str, str]] = []  # (trip, from, to, by role)


STATE = State()


def page(title: str, body: str, user: dict[str, Any] | None = None) -> bytes:
    top = ""
    if user and user.get("ok"):
        links = "<a href='/trips'>Trips</a>"
        if user["role"] == "Entry Operator":
            links += " | <a href='/trips/new'>Create Trip</a>"
        top = (f"<div class='topbar'>Logged in as {html.escape(user['name'])} ({html.escape(user['role'])}) | "
               f"{links} | <a href='/logout'>Logout</a></div>")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)} - Transport Management"
            "</title><style>.error{color:#b00}.ok{color:#070}</style></head>"
            f"<body>{top}<h2>{html.escape(title)}</h2>{body}</body></html>").encode("utf-8")


def _opts(items: list[str], cur: str) -> str:
    return "<option value=''>-- select --</option>" + "".join(
        f"<option{' selected' if cur == i else ''}>{html.escape(i)}</option>" for i in items)


def trip_form(v: dict[str, str], error: str = "") -> str:
    err = f"<div class='error' role='alert'>{html.escape(error)}</div>" if error else ""
    return (f"{err}<form method='post' action='/trips/new'>"
            f"<label>From <select name='origin' id='origin'>{_opts(CITIES, v.get('origin', ''))}</select></label><br>"
            f"<label>To <select name='destination' id='destination'>{_opts(CITIES, v.get('destination', ''))}</select></label><br>"
            f"<label>Trip date <input type='date' name='date' id='date' value='{html.escape(v.get('date', ''))}'></label><br>"
            f"<label>Driver <select name='driver' id='driver'>{_opts(DRIVERS, v.get('driver', ''))}</select></label><br>"
            f"<label>Vehicle <select name='vehicle' id='vehicle'>{_opts(VEHICLES, v.get('vehicle', ''))}</select></label><br>"
            "<button type='submit'>Save Trip</button> <a href='/trips'>Cancel</a></form>")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _user(self) -> dict[str, Any] | None:
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "tm_sid":
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

    def _login(self, error: str = "") -> bytes:
        err = f"<div class='error' role='alert'>{html.escape(error)}</div>" if error else ""
        return page("Sign in", f"{err}<form method='post' action='/login'><label>User ID <input name='u' id='u'>"
                    "</label><br><label>Password <input type='password' name='p' id='p'></label><br>"
                    "<button type='submit'>Sign in</button></form>")

    def _trip_page(self, t: dict[str, Any], user: dict[str, Any], msg: str = "", error: str = "") -> bytes:
        note = (f"<div class='ok' role='status'>{html.escape(msg)}</div>" if msg else "") + \
            (f"<div class='error' role='alert'>{html.escape(error)}</div>" if error else "")
        info = (f"<p>Trip No: <b>{t['no']}</b></p><p>From: {html.escape(t['origin'])}</p>"
                f"<p>To: {html.escape(t['destination'])}</p><p>Trip date: {t['date']}</p>"
                f"<p>Driver: {html.escape(t['driver'])}</p><p>Vehicle: {t['vehicle']}</p>"
                f"<p>Status: <b>{t['status']}</b></p>")
        act = ""
        role, st = user["role"], t["status"]
        if role == "Manager" and st == "Pending Approval":
            act = f"<form method='post' action='/trips/{t['no']}/approve'><button>Approve</button></form>"
        elif role == "Authorizing Officer" and st == "Approved":
            act = f"<form method='post' action='/trips/{t['no']}/authorize'><button>Authorize</button></form>"
        elif role == "Driver" and st == "Authorized":
            act = (f"<form method='post' action='/trips/{t['no']}/authenticate'><label>Driver authentication code "
                   "<input name='code' id='code' autocomplete='one-time-code'></label> "
                   "<button>Authenticate</button></form>")
        elif role == "Entry Operator" and st == "Driver Authenticated":
            act = (f"<form method='post' action='/trips/{t['no']}/record'><label>Odometer end (km) <input "
                   "name='odometer' id='odometer'></label><br><label>Fuel used (litres) <input name='fuel' id='fuel'>"
                   "</label><br><button>Record Trip</button></form>")
        return page(f"Trip {t['no']}", note + info + act + "<p><a href='/trips'>Back to trips</a></p>", user)

    def do_GET(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        user = self._user()
        if url.path == "/__reset":
            STATE.reset()
            return self._send(b"ok")
        if url.path == "/login" or (url.path == "/" and not user):
            return self._send(self._login())
        if url.path == "/logout":
            return self._redirect("/login", {"Set-Cookie": "tm_sid=; Max-Age=0; Path=/"})
        if user and not user.get("ok"):
            return self._send(page("Verification", "<p>Enter the one-time code sent to your phone.</p>"
                                   "<form method='post' action='/otp'><label>One-time code <input name='otp' "
                                   "autocomplete='one-time-code'></label><button type='submit'>Verify</button></form>"))
        if not user:
            return self._redirect("/login")
        if url.path in ("/", "/home"):
            return self._send(page("Trip Management", "<p>Welcome. Use Trips to see and manage trips.</p>", user))
        if url.path == "/trips":
            rows = [t for t in STATE.trips if not q.get("status") or q["status"].lower() == t["status"].lower()]
            trs = "".join(f"<tr><td>{t['no']}</td><td>{html.escape(t['origin'])}</td><td>"
                          f"{html.escape(t['destination'])}</td><td>{t['date']}</td><td>{html.escape(t['driver'])}"
                          f"</td><td>{t['status']}</td><td><a href='/trips/{t['no']}'>Open</a></td></tr>" for t in rows)
            return self._send(page("Trips", "<table class='grid'><thead><tr><th>Trip No</th><th>From</th><th>To</th>"
                                   "<th>Trip date</th><th>Driver</th><th>Status</th><th></th></tr></thead><tbody>"
                                   f"{trs}</tbody></table>", user))
        if url.path == "/trips/new":
            if user["role"] != "Entry Operator":
                return self._send(page("Not allowed", "<p class='error' role='alert'>Your role cannot create trips."
                                       "</p>", user), 403)
            return self._send(page("Create Trip", trip_form({}), user))
        parts = url.path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "trips":
            t = next((x for x in STATE.trips if x["no"] == parts[1]), None)
            if not t:
                return self._send(page("Not found", "<p class='error'>No such trip.</p>", user), 404)
            return self._send(self._trip_page(t, user))
        return self._send(page("Not found", "<p>Not found</p>", user), 404)

    def do_POST(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        form = self._form()
        user = self._user()
        if url.path == "/login":
            u, pw = form.get("u", ""), form.get("p", "")
            if u in USERS and USERS[u][0] == pw:
                sid = secrets.token_hex(12)
                STATE.sessions[sid] = {"name": u, "role": USERS[u][1], "ok": u != "officer2"}
                return self._redirect("/", {"Set-Cookie": f"tm_sid={sid}; Path=/; HttpOnly"})
            return self._send(self._login("Invalid user ID or password."))
        if url.path == "/otp" and user:
            if form.get("otp") == OTP_CODE:
                user["ok"] = True
            return self._redirect("/")
        if not user or not user.get("ok"):
            return self._redirect("/login")
        if url.path == "/trips/new":
            if user["role"] != "Entry Operator":
                return self._send(page("Not allowed", "<p class='error' role='alert'>Your role cannot create trips."
                                       "</p>", user), 403)
            v = {k: (form.get(k) or "").strip() for k in ("origin", "destination", "date", "driver", "vehicle")}
            err = validate(v)
            if err:
                return self._send(page("Create Trip", trip_form(v, err), user))
            with STATE.lock:
                STATE.posts += 1
                no = f"TRP-{len(STATE.trips) + 1:04d}"
                STATE.trips.append({**v, "no": no, "status": "Pending Approval", "created_by": user["name"]})
            return self._send(page("Create Trip", f"<div class='ok' role='status'>Trip {no} created successfully. "
                                   "Status: Pending Approval.</div>" + trip_form({}), user))
        parts = url.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "trips" and parts[2] in FLOW:
            t = next((x for x in STATE.trips if x["no"] == parts[1]), None)
            if not t:
                return self._send(page("Not found", "<p class='error'>No such trip.</p>", user), 404)
            role, before, after = FLOW[parts[2]]
            if user["role"] != role:
                return self._send(page("Not allowed", "<p class='error' role='alert'>Your role is not allowed to do "
                                       "this.</p>", user), 403)
            if t["status"] != before:
                return self._send(self._trip_page(t, user, error=f"Only trips with status {before} can be "
                                                                  f"{parts[2]}d."))
            if parts[2] == "authenticate":
                if DRIVER_USER.get(t["driver"]) != user["name"]:
                    return self._send(self._trip_page(t, user, error="Only the trip's own driver can authenticate."))
                if form.get("code") != DRIVER_CODES.get(t["driver"]):
                    return self._send(self._trip_page(t, user, error="Wrong driver authentication code."))
            if parts[2] == "record":
                if not (form.get("odometer") or "").strip().isdigit():
                    return self._send(self._trip_page(t, user, error="Odometer end is required (whole km)."))
                if not (form.get("fuel") or "").strip():
                    return self._send(self._trip_page(t, user, error="Fuel used is required."))
                t["odometer"], t["fuel"] = form["odometer"].strip(), form["fuel"].strip()
            with STATE.lock:
                t["status"] = after
                STATE.history.append((t["no"], before, after, user["role"]))
            verb = {"approve": "approved", "authorize": "authorized", "authenticate": "authenticated",
                    "record": "recorded"}[parts[2]]
            return self._send(self._trip_page(t, user, msg=f"Trip {t['no']} {verb} successfully. Status: {after}."))
        return self._send(page("Not found", "<p>Not found</p>", user), 404)


def validate(v: dict[str, str]) -> str:
    for k, label in (("origin", "From"), ("destination", "To"), ("date", "Trip date"), ("driver", "Driver"),
                     ("vehicle", "Vehicle")):
        if not v.get(k):
            return f"{label} is required."
    if v["origin"] == v["destination"]:
        return "From and To must be different."
    try:
        d = _dt.date.fromisoformat(v["date"])
    except ValueError:
        return "Trip date is invalid."
    if d <= _dt.date.today():
        return "Trip date must be in the future."
    for t in STATE.trips:
        if t["driver"] == v["driver"] and t["date"] == v["date"] and t["status"] != "Recorded":
            return f"Driver {v['driver']} is already assigned to trip {t['no']} on that date."
    return ""


def start(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


if __name__ == "__main__":
    import sys

    srv, base = start(int(sys.argv[1]) if len(sys.argv) > 1 else 8795)
    print(base, flush=True)
    threading.Event().wait()
