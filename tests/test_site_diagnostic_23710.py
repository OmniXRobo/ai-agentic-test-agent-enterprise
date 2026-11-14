"""23.7.1: the site diagnostic the tester runs on their PC captures the search box, city list, calendar and passengers
as the automation sees them, and adds the newest scripts and runs for the site, into one ZIP."""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qa_platform.automation import site_diagnostic  # noqa: E402


def test_diagnostic_captures_each_stage_into_one_zip(site, tmp_path):
    data = tmp_path / "data"
    (data / "automation" / "SCR-TRIPDIAG01").mkdir(parents=True)
    (data / "automation" / "SCR-TRIPDIAG01" / "script.json").write_text(json.dumps(
        {"id": "SCR-TRIPDIAG01", "base_url": "http://127.0.0.1", "steps": [{"action": "goto", "value": site}]}))
    (data / "evidence" / "RUN-1").mkdir(parents=True)
    (data / "evidence" / "RUN-1" / "execution.json").write_text(json.dumps(
        {"run_id": "RUN-1", "script_id": "SCR-TRIPDIAG01", "steps": [{"status": "FAILED"}]}))
    url = f"{site}/tripcom/flights.html"
    pages = []

    # the person's part, done here by the test between the prompts
    def ask(_prompt):
        n = len(pages)
        pages.append(n)
        return "" if n < 3 else "q"

    real_capture = site_diagnostic._capture

    def capture(page, folder, stage, city):
        if stage == "2-from-focused":
            page.click('[data-testid="search_city_from0"]')
            page.wait_for_timeout(300)
        if stage == "3-from-typed":
            page.keyboard.type("Manila", delay=30)
            page.wait_for_timeout(1200)
        return real_capture(page, folder, stage, city)

    site_diagnostic._capture = capture
    try:
        args = argparse.Namespace(url=url, browser="chromium", city="Manila", data=str(data), out=str(tmp_path),
                                  headless=True)
        path = site_diagnostic.run(args, ask=ask)
    finally:
        site_diagnostic._capture = real_capture
    names = zipfile.ZipFile(path).namelist()
    for stage in ("1-search-box", "2-from-focused", "3-from-typed"):
        assert {f"{stage}.png", f"{stage}.html", f"{stage}.aria.yml", f"{stage}.json"} <= set(names), names
    assert "scripts/SCR-TRIPDIAG01.json" in names and "runs/RUN-1.json" in names
    z = zipfile.ZipFile(path)
    report = json.loads(z.read("REPORT.json"))
    typed = json.loads(z.read("3-from-typed.json"))["engine"]
    assert typed["active"][0]["attrs"]["data-testid"] == "search_city_from0"
    assert typed["find_option"]["status"] == "found" and "Manila" in typed["find_option"]["text"]
    assert any("Manila" in r["text"] for r in typed["near_rows"])
    assert "<script" not in z.read("1-search-box.html").decode()
    assert report["stages"][2]["find_option"]["status"] == "found"
