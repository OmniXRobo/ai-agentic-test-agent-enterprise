"""23.7.1: the live flight-search check (VERIFY_LIVE_SITE.cmd) that the tester runs against the real site on their PC.
Here it runs against the trip.com stand-in, which proves the check itself; results on the real site come only from the
tester's PC (REPORT.md in the ZIP it writes)."""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qa_platform.automation import live_check  # noqa: E402


def test_parse_set():
    v = live_check.parse_set("Dubai|DXB>New Delhi|DEL@+9,+13#2", date(2026, 11, 15))
    assert v == {"from_city": "Dubai", "from_code": "DXB", "to_city": "New Delhi", "to_code": "DEL",
                 "depart": "2026-11-24", "return": "2026-11-28", "adults": 2}
    with pytest.raises(ValueError):
        live_check.parse_set("Dubai to Delhi", date(2026, 11, 15))


def test_two_searches_with_different_values_pass_on_the_stand_in(site, tmp_path):
    args = argparse.Namespace(url=f"{site}/tripcom/flights.html", browser="chromium", headless=True, out=str(tmp_path),
                              set=["Dubai|DXB>New Delhi|DEL@+9,+13#2", "London|LHR>Manila|MNL@+20,+27#1"])
    path, report = live_check.run(args)
    for r in report["runs"]:
        assert r["status"] == "PASS", r["steps"]
        assert [s["status"] for s in r["steps"]] == ["PASS"] * 7
    assert "DXB Dubai International Airport" in report["runs"][0]["steps"][1]["how"]
    assert "MNL" in report["runs"][1]["steps"][2]["how"]
    names = zipfile.ZipFile(path).namelist()
    assert {"REPORT.md", "REPORT.json", "search1-results.png", "search2-results.png"} <= set(names)
    md = zipfile.ZipFile(path).read("REPORT.md").decode()
    assert "**PASS**" in md and "nothing was booked" in md


def test_date_is_not_confused_with_the_departure_city_when_the_calendar_stays_closed(site, tmp_path):
    """Live check on trip.com, 15 Nov (both searches): the calendar did not open after the arrival city, and the field
    named "Departure airport or city" was clicked as the start date ("No open calendar could be read"). The stand-in in
    live mode behaves the same way; the date fields must be the ones that say date."""
    args = argparse.Namespace(url=f"{site}/tripcom/flights.html?live=1", browser="chromium", headless=True,
                              out=str(tmp_path), set=["Dubai|DXB>New Delhi|DEL@+9,+13#2"])
    path, report = live_check.run(args)
    r = report["runs"][0]
    assert r["status"] == "PASS", r["steps"]
    assert [s["status"] for s in r["steps"]] == ["PASS"] * 7


def test_date_fields_skip_place_fields(site):
    from playwright.sync_api import sync_playwright
    from qa_platform.automation import components
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.goto(f"{site}/tripcom/flights.html?live=1")
        components.ensure(page)
        fields = page.evaluate("() => window.__sgComp.dateFields().map(f => [f.role, f.text])")
        b.close()
    assert fields and all("airport" not in t.lower() for _, t in fields), fields
    assert fields[0][0] == "start" and "search_date_depart0" in fields[0][1], fields


def test_a_failing_step_is_reported_with_evidence_and_stops_that_search(site, tmp_path):
    """A city the site does not know: the From step fails with what was offered, the later steps do not run, and the
    engine's view, the accessibility tree and the HTML are saved."""
    args = argparse.Namespace(url=f"{site}/tripcom/flights.html", browser="chromium", headless=True, out=str(tmp_path),
                              set=["Atlantis|ATL>New Delhi|DEL@+9,+13#1"])
    path, report = live_check.run(args)
    r = report["runs"][0]
    assert r["status"] == "FAIL" and [s["status"] for s in r["steps"]] == ["PASS", "FAIL"]
    names = zipfile.ZipFile(path).namelist()
    assert {"search1-step02.png", "search1-step02.engine.json", "search1-step02.aria.yml", "search1-step02.html"} <= set(names)
    json.loads(zipfile.ZipFile(path).read("search1-step02.engine.json"))
