"""23.7.0 test helper: a person simply uses FleetOps while the agent watches (no titles, no explanations).

The person signs in as an Entry Operator, creates three trips (one attempt is a duplicate trip number, one double-books
a bus, one is in the past), looks at the trip list, signs out; signs in as a Manager, tries to create a trip (refused),
approves two trips; signs in as a Second-Level Manager and authorizes one. The agent's driver only watches.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable

from skyguard_agent.drivers.playwright_driver import PlaywrightDriver
from tests import transport_site


def day(offset: int) -> str:
    return (dt.date.today() + dt.timedelta(days=offset)).isoformat()


def person_script(base: str) -> list[Callable[[Any], None]]:
    def login(user: str) -> list[Callable[[Any], None]]:
        return [lambda pg: pg.goto(base + "/login"),
                lambda pg: pg.locator("input[name=u]").fill(user),
                lambda pg: pg.locator("input[type=password]").fill(transport_site.USERS[user][0]),
                lambda pg: pg.get_by_role("button", name="Sign in").click()]

    def logout() -> list[Callable[[Any], None]]:
        return [lambda pg: pg.get_by_role("link", name="Logout").click()]

    def trip(no: str, route: str, date: str, time: str, bus: str, driver: str, *, first: bool = True) -> list:
        steps = [lambda pg: pg.get_by_role("link", name="New trip").click()] if first else []
        steps += [lambda pg: pg.locator("tr:has-text('Trip No') input").fill(no),
                  lambda pg: pg.locator("tr:has-text('Route') select").select_option(label=route),
                  lambda pg: pg.locator("tr:has-text('Departure date') input").fill(date),
                  lambda pg: pg.locator("tr:has-text('Departure time') input").fill(time),
                  lambda pg: pg.locator("tr:has-text('Bus') select").select_option(label=bus),
                  lambda pg: pg.locator("tr:has-text('Driver') select").select_option(label=driver),
                  lambda pg: pg.get_by_role("button", name="Save trip").click()]
        return steps

    def row_action(no: str, label: str) -> list[Callable[[Any], None]]:
        return [lambda pg: pg.get_by_role("link", name="Trips", exact=True).first.click(),
                lambda pg: pg.locator(f"tr:has(td:text-is('{no}'))").get_by_role("button", name=label).click()]

    d1, d2 = day(3), day(4)
    s: list[Callable[[Any], None]] = []
    s += login("operator")
    s += trip("T100", "Dubai to Abu Dhabi", d1, "07:00", "BUS-101", "Ahmed Khan")
    s += trip("T101", "Dubai to Sharjah", d2, "09:30", "BUS-102", "Bilal Saeed")
    s += trip("T101", "Abu Dhabi to Dubai", d2, "15:00", "BUS-103", "Carlos Diaz")          # duplicate number
    s += [lambda pg: pg.locator("tr:has-text('Trip No') input").fill("T102"),
          lambda pg: pg.locator("tr:has-text('Bus') select").select_option(label="BUS-101"),
          lambda pg: pg.locator("tr:has-text('Departure date') input").fill(d1),
          lambda pg: pg.locator("tr:has-text('Departure time') input").fill("08:00"),
          lambda pg: pg.get_by_role("button", name="Save trip").click()]                     # BUS-101 double-booked
    s += [lambda pg: pg.locator("tr:has-text('Bus') select").select_option(label="BUS-103"),
          lambda pg: pg.locator("tr:has-text('Departure date') input").fill(day(-2)),
          lambda pg: pg.get_by_role("button", name="Save trip").click()]                     # in the past
    s += [lambda pg: pg.locator("tr:has-text('Departure date') input").fill(d2),
          lambda pg: pg.get_by_role("button", name="Save trip").click()]                     # T102 created
    s += [lambda pg: pg.get_by_role("link", name="Trips", exact=True).first.click()]
    s += logout()
    s += login("manager")
    s += [lambda pg: pg.goto(base + "/trips/new")]                                           # refused
    s += row_action("T100", "Approve")
    s += row_action("T101", "Approve")
    s += [lambda pg: pg.get_by_role("link", name="Trips", exact=True).first.click()]
    s += logout()
    s += login("manager2")
    s += row_action("T100", "Authorize")
    s += [lambda pg: pg.get_by_role("link", name="Trips", exact=True).first.click()]
    s += logout()
    return s


def browse(agent: Any, browser: Any, app_id: str, base: str, script: list[Callable[[Any], None]] | None = None,
           session_id: str | None = None) -> dict[str, Any]:
    """Runs the person's script in a SkyGuard watch browser; everything goes to the agent's journal."""
    sid = session_id or agent.journal.new_session(app_id, interface="test", by="tester")["id"]
    steps = list(script if script is not None else person_script(base))
    drv = PlaywrightDriver(browser)
    stats = {"events": 0, "pages": 0}

    def record(events: list[dict[str, Any]], snap: dict[str, Any] | None) -> None:
        r = agent.journal.record(app_id, sid, events, snap)
        stats["events"] += r["events"]
        stats["pages"] += r["pages"]

    def tick(d: PlaywrightDriver) -> None:
        if steps:
            step = steps.pop(0)
            step(d.page)
        else:
            d.page.close()

    try:
        drv.watch(record, on_tick=tick, poll_s=0.15, idle_snapshot_s=30)
    finally:
        drv.close()
    agent.journal.end_session(sid)
    return {"session": sid, **stats}
