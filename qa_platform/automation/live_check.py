"""23.7.1: live flight-search check the tester runs on their own PC (VERIFY_LIVE_SITE.cmd in the patch folder).

The build machine cannot open trip.com, so the playback engine is proven against the real site here: a visible
browser (the application's own Playwright and Edge) runs a flight search with the same component handlers that Play
uses, once for each set of values (two different sets by default, to show the search is driven by the values and not
by one recording):

    find From by what it means (accessible name / label / placeholder / group label), type the city, choose the airport
    by its code from the suggestions, verify; the same for To; choose the departure and return dates in the calendar
    (opened if needed) and verify; set Adults with the counter's + / -; find the Search button; check the form; search;
    verify that a results page for the two airports appears.

Nothing is hard-coded for one site: fields are found by their meaning, not by ids or classes. It stops at the
results page: nothing is booked or paid. A step that fails stops that run (later steps depend on it), saves what the
engine saw (screenshot, accessibility tree, HTML, engine view) and the next set of values runs. Everything goes into
one ZIP on the Desktop with REPORT.md (each step PASS / FAIL, how it was done, time) to send back.

    python -m qa_platform.automation.live_check [--url https://www.trip.com/flights/] [--browser msedge]
           [--set "Dubai|DXB>New Delhi|DEL@+9,+13#2"] [--set ...] [--out <folder>]

A set is "from city|from code > to city|to code @ +departure days, +return days # adults".
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

DEFAULT_SETS = ["Dubai|DXB>New Delhi|DEL@+9,+13#2", "London|LHR>Manila|MNL@+20,+27#1"]
FROM_RE = re.compile(r"\b(from|leaving|departure|depart(ing)?|origin)\b", re.I)
TO_RE = re.compile(r"\b(to|going|arrival|arrive|destination)\b", re.I)
SEARCH_RE = re.compile(r"^\s*search(\s+flights?)?\s*$", re.I)
DATE_WORDS = re.compile(r"\b(dates?|calendar|check-?in|check-?out)\b", re.I)
PAX_RE = re.compile(r"\b\d+\s+adults?\b", re.I)
PRICE_RE = re.compile(r"(AED|USD|EUR|GBP|INR|PHP|US\$|\$|€|£|₹)\s?\d[\d,.]*", re.I)

# what a text field says about itself: its accessible name, placeholder, label, the label of the box around it and
# the short text just before it, each with a weight (the field's own name counts most)
DESCRIBE_JS = r"""el => {
  const t = (s) => String(s || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  const out = [];
  const add = (w, s) => { s = t(s); if (s) out.push([w, s]); };
  add(5, el.getAttribute('aria-label'));
  add(4, el.getAttribute('placeholder'));
  for (const l of (el.labels || [])) add(5, l.innerText);
  for (const id of (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)) { const n = document.getElementById(id); if (n) add(5, n.innerText); }
  add(2, el.getAttribute('name')); add(2, el.getAttribute('data-testid')); add(1, el.id);
  const g = el.closest('[role=group][aria-label],[aria-label]:not(input)'); if (g && g !== el) add(3, g.getAttribute('aria-label'));
  let box = el.parentElement;
  for (let i = 0; box && i < 3; i++, box = box.parentElement) {
    const own = [...box.childNodes].filter((n) => n !== el && !(n.contains && n.contains(el))).map((n) => n.textContent || '').join(' ');
    if (t(own)) { add(2, own); break; }
  }
  const r = el.getBoundingClientRect();
  return { texts: out, box: [r.left, r.top, r.width, r.height], tag: el.tagName.toLowerCase(), type: el.type || '' };
}"""


def _say(msg: str = "") -> None:
    print(msg, flush=True)


def parse_set(text: str, today: date) -> dict[str, Any]:
    m = re.fullmatch(r"\s*(.+?)\|(\w{3})\s*>\s*(.+?)\|(\w{3})\s*@\s*\+?(\d{1,3})\s*,\s*\+?(\d{1,3})\s*(?:#\s*(\d))?\s*", text)
    if not m:
        raise ValueError(f"Cannot read the set '{text}'. Write it as: Dubai|DXB>New Delhi|DEL@+9,+13#2")
    dep, ret = today + timedelta(days=int(m.group(5))), today + timedelta(days=int(m.group(6)))
    if ret < dep:
        raise ValueError(f"The return date comes before the departure date in '{text}'.")
    return {"from_city": m.group(1).strip(), "from_code": m.group(2).upper(), "to_city": m.group(3).strip(),
            "to_code": m.group(4).upper(), "depart": dep.isoformat(), "return": ret.isoformat(),
            "adults": int(m.group(7) or 1)}


def find_field(page: Any, pattern: re.Pattern[str], exclude: list[Any] | None = None) -> tuple[Any | None, str]:
    """The one visible text field whose own description matches ``pattern`` best (scored by where the words are:
    accessible name and label first, then placeholder, the box's label, nearby text). None when two fields score
    the same (ambiguous) or none matches."""
    loc = page.locator("input:not([type=hidden]):not([type=checkbox]):not([type=radio]), textarea, [role=combobox], "
                       "[contenteditable=true]")
    scored: list[tuple[int, Any, str]] = []
    skip = [e.element_handle() for e in (exclude or []) if e is not None]
    for i in range(min(loc.count(), 60)):
        el = loc.nth(i)
        try:
            if not el.is_visible():
                continue
            d = el.evaluate(DESCRIBE_JS)
            if any(el.evaluate("(a, b) => a === b", h) for h in skip if h):
                continue
        except Exception:
            continue
        if d["type"] in ("date", "datetime-local") or any(DATE_WORDS.search(text) for _, text in d["texts"]):
            continue  # a date field ("Departure date") is not the departure city
        score, why = 0, ""
        for w, text in d["texts"]:
            if pattern.search(text) and w > score:
                score, why = w, text
        if score:
            scored.append((score, el, why))
    if not scored:
        return None, "no field describes itself that way"
    scored.sort(key=lambda s: -s[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None, f"two fields match equally ('{scored[0][2]}', '{scored[1][2]}')"
    return scored[0][1], f"'{scored[0][2]}'"


def _visible_one(loc: Any, largest: bool = True) -> Any | None:
    best, area = None, -1.0 if largest else 1e12
    for i in range(min(loc.count(), 20)):
        el = loc.nth(i)
        try:
            if not el.is_visible():
                continue
            b = el.bounding_box()
            a = (b["width"] * b["height"]) if b else 0
            if (largest and a > area) or (not largest and a < area):
                best, area = el, a
        except Exception:
            continue
    return best


class Run:
    def __init__(self, page: Any, folder: Path, name: str) -> None:
        self.page, self.folder, self.name = page, folder, name
        self.steps: list[dict[str, Any]] = []

    def step(self, title: str, fn: Any) -> Any:
        from . import components

        started = time.monotonic()
        _say(f"   {title} ...")
        try:
            how = fn()
            self.steps.append({"step": title, "status": "PASS", "how": how, "ms": int((time.monotonic() - started) * 1000)})
            _say(f"      PASS  {how}")
            return how
        except Exception as exc:  # noqa: BLE001 - recorded as a failed step with evidence
            msg = getattr(exc, "message", None) or str(exc).splitlines()[0][:300]
            stem = self.folder / f"{self.name}-step{len(self.steps) + 1:02d}"
            try:
                self.page.screenshot(path=str(stem.with_suffix(".png")))
                components.ensure(self.page)
                from .site_diagnostic import CLEAN_HTML, aria_snapshot
                stem.with_suffix(".engine.json").write_text(json.dumps(
                    self.page.evaluate("() => window.__sgComp.probe(null)"), indent=1, ensure_ascii=False), encoding="utf-8")
                stem.with_suffix(".aria.yml").write_text(aria_snapshot(self.page), encoding="utf-8")
                stem.with_suffix(".html").write_text(self.page.evaluate(CLEAN_HTML)[:3_000_000], encoding="utf-8")
            except Exception:
                pass
            self.steps.append({"step": title, "status": "FAIL", "error": msg, "ms": int((time.monotonic() - started) * 1000),
                               "evidence": stem.name})
            _say(f"      FAIL  {msg}")
            raise


def search_once(ctx: Any, url: str, values: dict[str, Any], folder: Path, name: str) -> dict[str, Any]:
    from . import components

    page = ctx.new_page()
    run = Run(page, folder, name)
    result: dict[str, Any] = {"name": name, "values": values, "steps": run.steps}
    state: dict[str, Any] = {}

    def open_site() -> str:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        components.ensure(page)
        components.settle(page, 400, 8000)
        components.dismiss(page)
        return page.url

    def city(pattern: re.Pattern[str], typed: str, code: str, key: str) -> str:
        field, why = None, ""
        for _ in range(12):  # the search box can still be drawing
            field, why = find_field(page, pattern, exclude=[state.get("from_field")] if key == "to" else None)
            if field is not None:
                break
            page.wait_for_timeout(500)
        if field is None:
            raise RuntimeError(f"The {key.upper()} field was not found: {why}.")
        state[f"{key}_field"] = field
        res = components.choose_option(page, field, code, typed=typed, timeout=12000)
        if not res.get("verified"):
            raise RuntimeError(f"'{res.get('chosen')}' was clicked but the {key.upper()} field does not show it.")
        return f"field {why}; typed '{typed}', chose '{res['chosen'][:70]}' ({res['how']}), verified"

    def day(iso: str, role: str) -> str:
        res = components.pick_date(page, iso, role=role, timeout=12000, today=date.today(),
                                   labels=components.date_labels(iso))
        if not res.get("verified"):
            raise RuntimeError(f"{iso} was clicked but neither the calendar nor a date field shows it.")
        return f"{iso} ({res['how']}), verified"

    def passengers(n: int) -> str:
        ctl = _visible_one(page.get_by_role("button", name=PAX_RE), largest=False) or \
            _visible_one(page.get_by_text(PAX_RE), largest=False)
        if ctl is None:
            raise RuntimeError("No passengers control ('1 adult ...') was found.")
        if n == 1 and re.search(r"\b1\s+adult\b", ctl.inner_text(), re.I):
            return "already 1 adult"
        ctl.click(timeout=8000)
        res = components.set_count(page, "Adults", n, timeout=8000)
        components.dismiss(page)
        if _visible_one(page.get_by_text(re.compile(rf"\b{n}\s+adults?\b", re.I)), largest=False) is None:
            ctl.click(timeout=5000)  # some sites apply the count when the pop-up is closed by its own control
            components.settle(page, 200, 2000)
        shown = _visible_one(page.get_by_text(re.compile(rf"\b{n}\s+adults?\b", re.I)), largest=False)
        if shown is None:
            raise RuntimeError(f"Adults was set to {n} in the pop-up but the form does not show '{n} adult(s)'.")
        return f"Adults {res['from']} -> {n} ({res['presses']} presses), form shows '{shown.inner_text()[:40]}'"

    def search() -> str:
        btn = _visible_one(page.get_by_role("button", name=SEARCH_RE)) or _visible_one(page.get_by_text(SEARCH_RE, exact=False))
        if btn is None:
            raise RuntimeError("No Search button was found.")
        before = page.url
        pages_before = len(ctx.pages)
        btn.click(timeout=8000)
        deadline = time.monotonic() + 60
        target = page
        while time.monotonic() < deadline:
            if len(ctx.pages) > pages_before:
                target = ctx.pages[-1]
            try:
                target.wait_for_load_state("domcontentloaded", timeout=5000)
                text = target.locator("body").inner_text(timeout=5000)
            except Exception:
                text = ""
            codes = all(c in text or ct.lower() in text.lower() for c, ct in (
                (values["from_code"], values["from_city"]), (values["to_code"], values["to_city"])))
            prices = len(PRICE_RE.findall(text))
            if target.url != before and codes and prices >= 3:
                target.screenshot(path=str(folder / f"{name}-results.png"))
                state["results"] = {"url": target.url, "title": target.title(), "prices_seen": prices}
                return f"results page {target.url[:120]} ({prices} prices; both airports named)"
            target.wait_for_timeout(1500)
        raise RuntimeError("No results page naming both airports with prices appeared within 60 s "
                           f"(now at {target.url[:120]}).")

    try:
        run.step("Open the flight search", open_site)
        run.step(f"From: {values['from_city']} ({values['from_code']})",
                 lambda: city(FROM_RE, values["from_city"], values["from_code"], "from"))
        run.step(f"To: {values['to_city']} ({values['to_code']})",
                 lambda: city(TO_RE, values["to_city"], values["to_code"], "to"))
        run.step(f"Departure date {values['depart']}", lambda: day(values["depart"], "start"))
        run.step(f"Return date {values['return']}", lambda: day(values["return"], "end"))
        run.step(f"Adults: {values['adults']}", lambda: passengers(values["adults"]))
        run.step("Search and verify the results", search)
        result["status"] = "PASS"
    except Exception:
        result["status"] = "FAIL"
    result["results"] = state.get("results")
    try:
        page.close()
    except Exception:
        pass
    return result


def report_md(report: dict[str, Any]) -> str:
    lines = [f"# Live flight-search check: {report['url']}", "",
             f"Run on this PC at {report['started']} with SkyGuard {report['skyguard']}, {report['browser']} "
             f"{report.get('browser_version', '')}. Search only: nothing was booked.", ""]
    for r in report["runs"]:
        v = r["values"]
        lines += [f"## {r['name']}: {v['from_city']} ({v['from_code']}) to {v['to_city']} ({v['to_code']}), "
                  f"{v['depart']} to {v['return']}, {v['adults']} adult(s): **{r['status']}**", "",
                  "| Step | Result | How / error | Time |", "|---|---|---|---|"]
        for s in r["steps"]:
            detail = (s.get("how") or s.get("error") or "").replace("|", "/")
            lines.append(f"| {s['step']} | {s['status']} | {detail} | {s['ms']} ms |")
        lines.append("")
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    from . import browser as browsers
    from .site_diagnostic import _desktop
    from .. import __version__

    today = date.today()
    sets = [parse_set(s, today) for s in (args.set or DEFAULT_SETS)]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else _desktop()
    work = out_dir / f"SkyGuard_LiveCheck_{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"skyguard": __version__, "url": args.url, "browser": args.browser,
                              "started": time.strftime("%Y-%m-%d %H:%M:%S"), "runs": []}
    pw = browsers.start_playwright()
    try:
        b = browsers.launch(pw, args.browser, headless=args.headless)
        report["browser_version"] = b.version
        # 23.9.0: maximized when visible; headless keeps a fixed 1366 x 860 viewport
        ctx = browsers.new_context(b, args.headless, viewport={"width": 1366, "height": 860}, locale="en-US")
        from .browser import js
        ctx.add_init_script(js("components"))
        for i, values in enumerate(sets, 1):
            _say("")
            _say(f"Search {i} of {len(sets)}: {values['from_city']} to {values['to_city']}, {values['depart']} to "
                 f"{values['return']}, {values['adults']} adult(s)")
            report["runs"].append(search_once(ctx, args.url, values, work, f"search{i}"))
        b.close()
    finally:
        try:
            pw.stop()
        except Exception:
            pass
    (work / "REPORT.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    (work / "REPORT.md").write_text(report_md(report), encoding="utf-8")
    zip_path = out_dir / f"{work.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(work.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(work).as_posix())
    return zip_path, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="https://www.trip.com/flights/")
    ap.add_argument("--browser", default="msedge", choices=("msedge", "chrome", "chromium"))
    ap.add_argument("--set", action="append", help='e.g. "Dubai|DXB>New Delhi|DEL@+9,+13#2" (repeat for more)')
    ap.add_argument("--out")
    ap.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    _say("SkyGuard QA live flight-search check. A browser opens and searches by itself; please do not click in it.")
    _say("It stops at the results page; nothing is booked or paid.")
    try:
        path, report = run(args)
    except Exception as exc:  # noqa: BLE001
        _say(f"The check could not run: {exc}")
        return 1
    _say("")
    for r in report["runs"]:
        _say(f"{r['name']}: {r['status']}")
    _say(f"Please send this file back: {path}")
    return 0 if all(r["status"] == "PASS" for r in report["runs"]) else 2


if __name__ == "__main__":
    sys.exit(main())
