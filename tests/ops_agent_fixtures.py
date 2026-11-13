"""Shared set-up for the Operations Agent tests: the FleetOps stand-in application with approved knowledge.

Everything here is what a person would approve after learning (workflows, rules, the sign-in profile); the tests then
exercise the engine through a real browser against tests/transport_site.py.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from tests import transport_site

PROFILE = {"login": {"url": "/login", "username": "User ID", "password": "Password", "submit": "Sign in"},
           "logout": {"control": "Logout"},
           "identity": {"pattern": r"Logged in as (?P<user>\S+) \((?P<role>[^)]+)\)"}}

CREATE_TRIP = {
    "title": "Create trip", "operation": "create", "entity": "trip", "commits": True,
    "roles": ["entry operator", "administrator"], "start_url": "/trips/new",
    "parameters": [
        {"name": "trip_no", "field": "Trip No", "required": True},
        {"name": "route", "field": "Route", "type": "select", "required": True},
        {"name": "date", "field": "Departure date", "type": "date", "required": True},
        {"name": "time", "field": "Departure time", "type": "time", "required": True},
        {"name": "bus", "field": "Bus", "type": "select", "required": True},
        {"name": "driver", "field": "Driver", "type": "select", "required": True, "options": transport_site.DRIVERS}],
    "steps": [
        {"action": "navigate", "url": "/trips/new"},
        {"action": "fill", "screen": "New trip", "target": "Trip No", "param": "trip_no"},
        {"action": "select", "screen": "New trip", "target": "Route", "param": "route"},
        {"action": "fill", "screen": "New trip", "target": "Departure date", "param": "date"},
        {"action": "fill", "screen": "New trip", "target": "Departure time", "param": "time"},
        {"action": "select", "screen": "New trip", "target": "Bus", "param": "bus"},
        {"action": "select", "screen": "New trip", "target": "Driver", "param": "driver"},
        {"action": "click", "screen": "New trip", "target": "Save trip", "commit": True}],
    "verify": {"success_pattern": "Trip {trip_no} created",
               "record_listed": {"url": "/trips?q={trip_no}", "column": "Trip No", "param": "trip_no"}},
    "exists_check": {"url": "/trips?q={trip_no}", "list_url": "/trips", "column": "Trip No", "param": "trip_no"},
    "bookings_source": {"url": "/trips", "columns": {"driver": "Driver", "date": "Date", "time": "Time", "bus": "Bus"}},
}

APPROVE_TRIP = {
    "title": "Approve trip", "operation": "approve", "entity": "trip", "commits": True, "roles": ["manager"],
    "target_kind": "existing", "target": {"status_column": "Status", "requires_status": "Planned"},
    "expected_status": "Approved",
    "parameters": [{"name": "trip_no", "field": "Trip No", "required": True}],
    "steps": [{"action": "navigate", "url": "/trips?q={trip_no}"},
              {"action": "open_row", "control": "Approve", "param": "trip_no", "commit": True}],
    "verify": {"success_pattern": "Trip {trip_no} approved",
               "record_listed": {"url": "/trips?q={trip_no}", "column": "Trip No", "param": "trip_no",
                                 "status_column": "Status"}},
    "exists_check": {"url": "/trips?q={trip_no}", "column": "Trip No", "param": "trip_no", "status_column": "Status"},
}

RULES = {
    "Driver no overlap": {"type": "no_overlap", "resource": "driver", "date": "date", "time": "time",
                          "duration_minutes": 180},
    "Trip unique": {"type": "unique", "field": "trip_no"},
    "Future date": {"type": "date", "field": "date", "min_days": 0},
}


def day(offset: int = 3) -> str:
    return (dt.date.today() + dt.timedelta(days=offset)).isoformat()


def setup_fleetops(agent: Any, base: str) -> dict[str, Any]:
    """Registers FleetOps with accounts (credentials saved with consent) and approved workflows and rules."""
    kb = agent.kb
    app = kb.register_application("FleetOps", base + "/", mode="testing")
    profile = {**PROFILE, "login": {**PROFILE["login"], "url": base + "/login"}}
    kb.set_profile(app["id"], profile)
    accounts = {}
    for login, label, role in (("operator", "Entry Operator (test)", "Entry Operator"),
                               ("manager", "Manager (test)", "Manager"),
                               ("otpuser", "OTP Operator (test)", "Entry Operator")):
        acc = kb.add_account(app["id"], label, login, role_key=role)
        kb.set_credential(acc["id"], transport_site.USERS[login][0], consent=True)
        accounts[login] = acc
    workflows = {}
    for wf in (CREATE_TRIP, APPROVE_TRIP):
        e = kb.propose(app["id"], "workflow", wf["title"], wf, source="test")
        workflows[wf["title"]] = kb.approve(e["id"], by="tester")
    for key, rule in RULES.items():
        e = kb.propose(app["id"], "rule", key, rule, source="test")
        kb.approve(e["id"], by="tester")
    return {"app": app, "accounts": accounts, "workflows": workflows}
