"""Base class of every Page Object: element interactions with visual playback and step reporting.

Page Objects declare their locators (``Loc``) and business-level methods; the
interaction itself (locate with self-healing, highlight, move the cursor, act,
report) lives here once, so generated tests never repeat selectors or Playwright
calls.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import expect

from . import config, reporting
from .locators import Loc, resolve

VISUAL_KIND = {"fill": "type", "select": "type", "press": "type"}


class BasePage:
    #: Address of the page, relative to the application base URL (or absolute).
    URL = ""

    def __init__(self, page: Any) -> None:
        self.page = page

    # ---- navigation ------------------------------------------------------------------

    def url_for(self, path: str | None = None) -> str:
        target = self.URL if path is None else path
        if re.match(r"^[a-z]+://", target or ""):
            return target
        base = config.base_url(reporting.state.base_url)
        if not base:
            raise AssertionError("No application URL: set BASE_URL or the base_url of the test data.")
        return urljoin(base.rstrip("/") + "/", (target or "").lstrip("/"))

    def open(self, path: str | None = None) -> None:
        url = self.url_for(path)
        reporting.action("goto", f"Open {url}")
        reporting.show_status(f"Test Execution Agent - {reporting.current_label('Open ' + url)}")
        self.page.goto(url, wait_until="domcontentloaded", timeout=max(config.TIMEOUT_MS, 30000))

    def expect_url(self, fragment: str) -> None:
        reporting.action("expect_url", f"Verify the URL contains '{fragment}'")
        expect(self.page).to_have_url(re.compile(re.escape(fragment)), timeout=config.TIMEOUT_MS)

    def expect_title(self, text: str) -> None:
        reporting.action("expect_title", f"Verify the page title contains '{text}'")
        expect(self.page).to_have_title(re.compile(re.escape(text)), timeout=config.TIMEOUT_MS)

    # ---- element actions ---------------------------------------------------------------

    def _element(self, loc: Loc, kind: str, state: str = "visible") -> Any:
        element = resolve(self.page, loc, state=state)
        reporting.state.target = element
        reporting.show_status(f"Test Execution Agent - {reporting.current_label(loc.label())}")
        if config.VISUAL and state != "hidden":
            try:
                element.scroll_into_view_if_needed(timeout=config.TIMEOUT_MS)
                box = element.bounding_box(timeout=config.TIMEOUT_MS)
                if box:
                    # move (resolves when the cursor has arrived) -> highlight dwell -> click pulse -> act
                    self.page.evaluate("([b, k, l]) => window.__qaOverlay ? window.__qaOverlay.point(b.x, b.y, b.width,"
                                       " b.height, k, l) : null", [box, kind, loc.label()[:60]])
                    self.page.wait_for_timeout(config.VISUAL_DELAY_MS)
                    self._box = box
                    if kind == "click":
                        self._pulse(box)
            except Exception:
                pass
        return element

    _box: Any = None

    def _pulse(self, box: dict[str, float]) -> None:
        self.page.evaluate("([x, y]) => window.__qaOverlay ? window.__qaOverlay.click(x, y) : null",
                           [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])

    def _done(self) -> None:
        if config.VISUAL:
            try:
                self.page.wait_for_timeout(min(config.VISUAL_DELAY_MS, 250))
                self.page.evaluate("window.__qaOverlay && window.__qaOverlay.clear()")
            except Exception:
                pass  # the page may have navigated

    def click(self, loc: Loc) -> None:
        reporting.action("click", f"Click {loc.label()}", locator=loc.selector())
        self._element(loc, "click").click(timeout=config.TIMEOUT_MS)
        self._done()

    def double_click(self, loc: Loc) -> None:
        reporting.action("dblclick", f"Double-click {loc.label()}", locator=loc.selector())
        self._element(loc, "click").dblclick(timeout=config.TIMEOUT_MS)
        self._done()

    def fill(self, loc: Loc, value: Any) -> None:
        text = "" if value is None else str(value)
        reporting.action("fill", f"Enter value in {loc.label()}", locator=loc.selector())
        element = self._element(loc, "type")
        if config.VISUAL and config.TYPING_DELAY_MS > 0 and len(text) <= 200:
            # Visible typing: focus the field, clear it, then type character by character.
            element.click(timeout=config.TIMEOUT_MS)
            element.fill("", timeout=config.TIMEOUT_MS)
            element.press_sequentially(text, delay=config.TYPING_DELAY_MS, timeout=config.TIMEOUT_MS)
        else:
            element.fill(text, timeout=config.TIMEOUT_MS)
        self._done()

    def select(self, loc: Loc, value: Any) -> None:
        text = "" if value is None else str(value)
        reporting.action("select", f"Select '{text}' in {loc.label()}", locator=loc.selector())
        self._box = None
        element = self._element(loc, "type")
        if config.VISUAL and self._box:
            try:  # show the options and the cursor choosing one (a native list cannot be opened by automation)
                options = element.evaluate("el => el.options ? [...el.options].map(o => [o.value, o.text.trim()]) : null")
                index = next((i for i, (v, t) in enumerate(options or []) if v == text or t.lower() == text.lower()), None)
                if index is not None:
                    self.page.evaluate("([b, o, i]) => window.__qaOverlay ? window.__qaOverlay.choose(b.x, b.y, b.width,"
                                       " b.height, o, i) : null", [self._box, [t for _v, t in options], index])
            except Exception:
                pass
        try:
            element.select_option(value=text, timeout=config.TIMEOUT_MS)
        except Exception:
            element.select_option(label=text, timeout=config.TIMEOUT_MS)
        self._done()

    def pick_date(self, loc: Loc, value: Any) -> None:
        """Choose a date (YYYY-MM-DD) as a person would: open the calendar, page to the month, click the day.

        Date inputs are filled directly; if no calendar can be read the date is typed in the field's format.
        """
        text = "" if value is None else str(value)
        m = re.match(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})", text)
        if not m:
            raise AssertionError(f"pick_date needs a date as YYYY-MM-DD, got '{text}'.")
        from datetime import date

        target = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        iso = target.isoformat()
        reporting.action("pick_date", f"Pick date {iso} in {loc.label()}", locator=loc.selector())
        element = self._element(loc, "click")
        if (element.get_attribute("type") or "").lower() == "date":
            element.fill(iso, timeout=config.TIMEOUT_MS)
            self._done()
            return
        element.click(timeout=config.TIMEOUT_MS)
        self.page.wait_for_timeout(300)
        for _ in range(25):
            step = self.page.evaluate("iso => window.__qaCal ? window.__qaCal.step(iso) : ''", iso)
            if not step:
                break
            mark = self.page.locator('[data-qa-cal="target"]').first
            if config.VISUAL:
                try:
                    box = mark.bounding_box(timeout=config.TIMEOUT_MS)
                    if box:
                        self.page.evaluate("([b, l]) => window.__qaOverlay ? window.__qaOverlay.point(b.x, b.y, b.width,"
                                           " b.height, 'click', l) : null", [box, f"Day {target.day}" if step == "day" else "Change month"])
                        self.page.wait_for_timeout(min(config.VISUAL_DELAY_MS, 250))
                        self._pulse(box)
                except Exception:
                    pass
            mark.click(timeout=config.TIMEOUT_MS)
            if step == "day":
                self.page.evaluate("() => window.__qaCal && window.__qaCal.clear()")
                self._done()
                return
            self.page.wait_for_timeout(200)
        placeholder = (element.get_attribute("placeholder") or "").lower()
        fmt = "%Y-%m-%d"
        if re.search(r"mm[/.-]dd[/.-]y{2,4}", placeholder):
            fmt = "%m/%d/%Y"
        elif re.search(r"dd[/.-]mm[/.-]y{2,4}", placeholder):
            sep = re.search(r"dd([/.-])mm", placeholder).group(1)
            fmt = f"%d{sep}%m{sep}%Y"
        self.page.keyboard.press("Escape")
        element.fill(target.strftime(fmt), timeout=config.TIMEOUT_MS)
        self._done()

    def check(self, loc: Loc) -> None:
        reporting.action("check", f"Check {loc.label()}", locator=loc.selector())
        self._element(loc, "click").check(timeout=config.TIMEOUT_MS)
        self._done()

    def uncheck(self, loc: Loc) -> None:
        reporting.action("uncheck", f"Uncheck {loc.label()}", locator=loc.selector())
        self._element(loc, "click").uncheck(timeout=config.TIMEOUT_MS)
        self._done()

    def hover(self, loc: Loc) -> None:
        reporting.action("hover", f"Hover over {loc.label()}", locator=loc.selector())
        self._element(loc, "click").hover(timeout=config.TIMEOUT_MS)
        self._done()

    def press(self, loc: Loc, key: str = "Enter") -> None:
        reporting.action("press", f"Press {key} in {loc.label()}", locator=loc.selector())
        self._element(loc, "type").press(key, timeout=config.TIMEOUT_MS)
        self._done()

    # ---- validations -------------------------------------------------------------------

    def expect_visible(self, loc: Loc) -> None:
        reporting.action("expect_visible", f"Verify {loc.label()} is visible", locator=loc.selector())
        expect(self._element(loc, "assert")).to_be_visible(timeout=config.TIMEOUT_MS)
        self._done()

    def expect_hidden(self, loc: Loc) -> None:
        reporting.action("expect_hidden", f"Verify {loc.label()} is hidden", locator=loc.selector())
        expect(self._element(loc, "assert", state="hidden")).to_be_hidden(timeout=config.TIMEOUT_MS)

    def expect_text(self, loc: Loc, text: Any) -> None:
        reporting.action("expect_text", f"Verify {loc.label()} shows '{text}'", locator=loc.selector())
        expect(self._element(loc, "assert", state="attached")).to_contain_text(str(text), timeout=config.TIMEOUT_MS)
        self._done()

    def expect_value(self, loc: Loc, value: Any) -> None:
        reporting.action("expect_value", f"Verify {loc.label()} has value '{value}'", locator=loc.selector())
        expect(self._element(loc, "assert")).to_have_value(str(value), timeout=config.TIMEOUT_MS)
        self._done()

    def screenshot(self, name: str) -> str | None:
        return reporting.checkpoint(name)
