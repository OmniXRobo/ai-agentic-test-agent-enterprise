"""23.7.1: site diagnostic the tester runs on their own PC (DIAGNOSE_SITE.cmd in the patch folder).

It opens a visible browser on the site (trip.com flights by default) with the same Playwright and browser the
application uses, and asks the person to do five ordinary things one by one (show the search box, click the
departure field, type a city, open the calendar, open the passengers). After each one it saves what the page holds
at that moment:

* a screenshot,
* the accessibility tree of the page (what roles and names Playwright sees),
* the page's HTML (scripts and styles removed; password fields blanked),
* what SkyGuard's own component engine finds: the focused field and its box, open lists and their rows, the rows drawn
  next to the field, the option it would choose for the typed city, calendar days and date fields, counters.

It also adds the newest recorded scripts for the site and the results of their last runs (execution.json). Everything
goes into one ZIP on the Desktop that the person sends back. It never clicks, types or books anything itself, needs
no network other than the site, and no administrator rights. Cookies, passwords and saved sign-ins are not collected.

    python -m qa_platform.automation.site_diagnostic --data <data folder> [--url https://www.trip.com/flights/]
           [--browser msedge|chrome|chromium] [--city Manila] [--out <folder>]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

STAGES = [
    ("1-search-box", "Close any banner or sign-in pop-up so that the flight search box (From, To, dates, passengers) "
                     "is visible. Do not click into it yet.", None),
    ("2-from-focused", "Click once into the departure field (From / Leaving from). Do not type. Wait until the list "
                       "under it (popular cities) is shown.", None),
    ("3-from-typed", "Type the city {city} into the departure field and wait until the list of airports is shown. "
                     "Do not choose yet.", "city"),
    ("4-calendar", "Choose the city in the list, choose an arrival city the same way, then open the departure date "
                   "calendar so that the days are shown. Do not choose a day yet.", None),
    ("5-passengers", "Choose two dates, then open the passengers list (Adults, Children, Infants with - and +). "
                     "Do not press Search.", None),
]

CLEAN_HTML = r"""() => {
  const doc = document.documentElement.cloneNode(true);
  doc.querySelectorAll('script,style,noscript,link[rel=stylesheet],svg path,iframe').forEach((n) => n.remove());
  doc.querySelectorAll('input[type=password]').forEach((n) => n.setAttribute('value', ''));
  doc.querySelectorAll('[style]').forEach((n) => { if (n.getAttribute('style').length > 200) n.removeAttribute('style'); });
  return '<!doctype html>\n' + doc.outerHTML;
}"""


def _say(msg: str = "") -> None:
    print(msg, flush=True)


def _aria(page: Any) -> str:
    try:
        return page.locator("body").aria_snapshot(timeout=15000)
    except Exception as exc:  # older Playwright: the deprecated accessibility snapshot
        try:
            return json.dumps(page.accessibility.snapshot(interesting_only=True), indent=1, ensure_ascii=False)
        except Exception:
            return f"(accessibility tree not available: {exc})"


def _capture(page: Any, folder: Path, stage: str, city: str | None) -> dict[str, Any]:
    from . import components

    out: dict[str, Any] = {"stage": stage, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        page.screenshot(path=str(folder / f"{stage}.png"), full_page=False)
    except Exception as exc:
        out["screenshot_error"] = str(exc)[:200]
    (folder / f"{stage}.aria.yml").write_text(_aria(page), encoding="utf-8")
    try:
        html = page.evaluate(CLEAN_HTML)
        (folder / f"{stage}.html").write_text(html[:6_000_000], encoding="utf-8")
    except Exception as exc:
        out["html_error"] = str(exc)[:200]
    try:
        components.ensure(page)
        out["engine"] = page.evaluate("w => window.__sgComp.probe(w)", city)
    except Exception as exc:
        out["engine_error"] = str(exc)[:300]
    out["frames"] = [f.url[:200] for f in page.frames][:10]
    (folder / f"{stage}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    return out


def _recent(data: Path, host: str, folder: Path) -> list[str]:
    """The newest recorded scripts for this site and their last runs."""
    found: list[str] = []
    scripts = data / "automation"
    hits: list[tuple[float, Path, dict[str, Any]]] = []
    for p in scripts.glob("SCR-*/script.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        text = json.dumps(d.get("steps", [])[:2]) + str(d.get("base_url") or "")
        if host in text:
            hits.append((p.stat().st_mtime, p, d))
    hits.sort(key=lambda h: h[0], reverse=True)
    ids = set()
    for _, p, d in hits[:5]:
        ids.add(d.get("id"))
        (folder / "scripts").mkdir(exist_ok=True)
        (folder / "scripts" / f"{d.get('id')}.json").write_text(json.dumps(d, indent=1, ensure_ascii=False),
                                                                 encoding="utf-8")
        found.append(f"script {d.get('id')}")
    runs = []
    for p in (data / "evidence").glob("*/execution.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("script_id") in ids:
            runs.append((p.stat().st_mtime, p, d))
    runs.sort(key=lambda r: r[0], reverse=True)
    for _, p, d in runs[:3]:
        (folder / "runs").mkdir(exist_ok=True)
        (folder / "runs" / f"{p.parent.name}.json").write_text(json.dumps(d, indent=1, ensure_ascii=False, default=str),
                                                               encoding="utf-8")
        found.append(f"run {p.parent.name}")
        # the screenshot of the failed step, if there is one
        failed = [s for s in d.get("steps", []) if s.get("status") == "FAILED"]
        for s in failed[:1]:
            shot = s.get("screenshot")
            if shot and Path(shot).is_file() and Path(shot).stat().st_size < 4_000_000:
                (folder / "runs" / f"{p.parent.name}-failed-step.png").write_bytes(Path(shot).read_bytes())
    return found


def _desktop() -> Path:
    for p in (Path(os.environ.get("USERPROFILE", "")) / "Desktop", Path.home() / "Desktop", Path.home()):
        if str(p) and p.is_dir():
            return p
    return Path.cwd()


def run(args: argparse.Namespace, ask: Any = input) -> Path:
    from . import browser as browsers
    from .. import __version__

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else _desktop()
    work = out_dir / f"SkyGuard_SiteDiagnostic_{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    host = re.sub(r"^https?://(www\.)?", "", args.url).split("/")[0]
    report: dict[str, Any] = {"skyguard": __version__, "url": args.url, "browser": args.browser, "city": args.city,
                              "python": sys.version.split()[0], "stages": []}
    pw = browsers.start_playwright()
    try:
        try:
            import playwright
            report["playwright"] = getattr(playwright, "__version__", "")
        except Exception:
            pass
        b = browsers.launch(pw, args.browser, headless=args.headless)
        report["browser_version"] = b.version
        ctx = b.new_context(viewport={"width": 1366, "height": 860}, locale="en-US")
        page = ctx.new_page()
        _say(f"Opening {args.url} ...")
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            _say(f"The page did not finish loading ({str(exc).splitlines()[0][:120]}); continuing.")
        for i, (stage, text, need) in enumerate(STAGES, 1):
            _say("")
            _say(f"Step {i} of {len(STAGES)}: " + text.format(city=args.city))
            answer = ask("   Press Enter when it is shown (or type s to skip this step, q to finish now): ").strip().lower()
            if answer == "q":
                break
            if answer == "s":
                report["stages"].append({"stage": stage, "skipped": True})
                continue
            page = ctx.pages[-1] if ctx.pages else page  # the site may have opened the search in a new tab
            got = _capture(page, work, stage, args.city if need == "city" else None)
            eng = got.get("engine") or {}
            summary = {"stage": stage, "url": eng.get("url"), "focused": (eng.get("active") or [None])[0],
                       "lists": len(eng.get("popups") or []) if isinstance(eng.get("popups"), list) else eng.get("popups"),
                       "rows_next_to_field": len(eng.get("near_rows") or []) if isinstance(eng.get("near_rows"), list) else None,
                       "find_option": eng.get("find_option"),
                       "calendar_days": (eng.get("calendar") or {}).get("cells") if isinstance(eng.get("calendar"), dict) else None,
                       "counters": eng.get("counters")}
            report["stages"].append(summary)
            _say(f"   Saved ({stage}).")
        b.close()
    finally:
        try:
            pw.stop()
        except Exception:
            pass
    if args.data:
        try:
            report["included"] = _recent(Path(args.data), host, work)
        except Exception as exc:
            report["included_error"] = str(exc)[:200]
    (work / "REPORT.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    zip_path = out_dir / f"{work.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(work.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(work).as_posix())
    return zip_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="https://www.trip.com/flights/")
    ap.add_argument("--browser", default="msedge", choices=("msedge", "chrome", "chromium"))
    ap.add_argument("--city", default="Manila")
    ap.add_argument("--data", help="the application's data folder (adds the newest scripts and runs for the site)")
    ap.add_argument("--out", help="where to save the ZIP (default: the Desktop)")
    ap.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    _say("SkyGuard QA site diagnostic: captures what the search box, the city list, the calendar and the passengers")
    _say("list look like to the automation, so that playback can be fixed for this site. Nothing is booked; you do")
    _say("every click yourself. Passwords and cookies are not collected.")
    try:
        path = run(args)
    except KeyboardInterrupt:
        _say("Stopped.")
        return 1
    except Exception as exc:  # noqa: BLE001 - the person needs a plain message, not a trace
        _say(f"The diagnostic could not finish: {exc}")
        return 1
    _say("")
    _say(f"Done. Please send this file back: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
