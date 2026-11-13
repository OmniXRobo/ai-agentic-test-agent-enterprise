"""Automation script format and runtime value rules.

Scripts are stored as JSON steps rather than free Python, so every mode
(AI-Driven, Guided recording, Discovery, Hybrid, manual playback) shares one
runner with visual playback, self-healing and evidence. ``to_playwright_python``
exports a readable Playwright script for review.

Runtime values (resolved at execution time, from the Windows system clock only):

    {{TODAY}}                  today, default format %Y-%m-%d
    {{TODAY+3d:%d/%m/%Y}}      offset in d/w/m/y with an optional strftime format
    {{TODAY-2d:%d/%m/%Y}}      a past date
    {{NOW:%H:%M}}              current time
    {{NOW+1h+30min:%H:%M}}     current time + 1 hour 30 minutes (h hours, min minutes; offsets combine)
    {{NOW-4h:%H:%M}}           a past time
    {{RANDOM:6}}               6 random digits
    {{UNIQUE}}                 run-unique token
    {{DATA:field}}             value from the selected test dataset
    {{CRED:role.username}}     application credential (role-based)
    {{CRED:role.password}}     password from the credential store (never logged)
"""

from __future__ import annotations

import calendar
import random
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from ..clock import now

ACTIONS = ("goto", "click", "dblclick", "fill", "type", "select", "pick_date", "check", "uncheck", "press", "hover",
           "wait", "expect_visible", "expect_hidden", "expect_text", "expect_value", "expect_url", "screenshot",
           "upload_file", "download_file", "expect_file_name", "expect_file_text", "expect_excel_cell",
           "right_click", "drag_to", "dialog",  # 23.3.21
           "choose_option", "dismiss")  # 23.6.0: semantic dropdown / autocomplete choice; close a pop-up safely

Action = Literal["goto", "click", "dblclick", "fill", "type", "select", "pick_date", "check", "uncheck", "press", "hover",
                 "wait", "expect_visible", "expect_hidden", "expect_text", "expect_value", "expect_url", "screenshot",
                 "upload_file", "download_file", "expect_file_name", "expect_file_text", "expect_excel_cell",
                 "right_click", "drag_to", "dialog", "choose_option", "dismiss"]

NEEDS_TARGET = {"click", "dblclick", "fill", "type", "select", "pick_date", "check", "uncheck", "hover",
                "expect_visible", "expect_hidden", "expect_text", "expect_value", "upload_file", "download_file",
                "right_click", "drag_to", "choose_option"}

# 23.3.17: checks on the file the last download_file step saved (no element needed).
FILE_CHECKS = {"expect_file_name", "expect_file_text", "expect_excel_cell"}


class Target(BaseModel):
    """Where to act, plus the fingerprint the Self-Healing Locator Agent uses when the selector breaks."""

    selector: str
    alternates: list[str] = []
    description: str | None = None
    tag: str | None = None
    text: str | None = None
    role: str | None = None
    name: str | None = None
    element_id: str | None = None
    test_id: str | None = None
    label: str | None = None
    placeholder: str | None = None
    input_type: str | None = None
    classes: list[str] = []
    href: str | None = None
    # 23.3.21: the element is inside these iframes (outermost first, each a selector in its parent document)
    frames: list[str] = []
    # Final patch (defect 7): where the element sits, recorded for the Self-Healing Locator.
    parent: str | None = None
    ancestors: list[str] = []
    siblings: list[str] = []
    nearby_text: str | None = None
    form: str | None = None
    shadow_host: str | None = None


class Step(BaseModel):
    action: Action
    target: Target | None = None
    value: str | None = None
    description: str | None = None
    timeout_ms: int = Field(default=10000, ge=100, le=300000)
    optional: bool = False
    # 23.3.17: the value as it was recorded, when a date or time was turned into a rule such as {{TODAY+2d}}
    original_value: str | None = None
    # 23.3.21: the browser tab the step happens in (0 = the first tab; 1 = the first tab a click opened, ...)
    tab: int | None = Field(default=None, ge=0, le=50)
    # 23.6.0: what the step means, besides where it clicks: {"intent": "pick_date", "component": "date_range",
    # "role": "start" | "end", "field": "Check-in", "recorded_value": "2026-11-15", "option": "Paris", ...}.
    # Playback uses it to recover when the page's HTML changed; it never holds a secret value.
    semantic: dict[str, Any] | None = None
    # 23.6.0: a click on a payment / booking-finalisation control plays only when a person allowed it for this step
    commit_ok: bool = False


class DataTable(BaseModel):
    """23.3.17: test data imported from Excel or CSV; every row is one run, columns are {{DATA:column}}."""

    source: str | None = None
    sheet: str | None = None
    imported_at: str | None = None
    columns: list[str] = []
    rows: list[dict[str, str]] = []


class Script(BaseModel):
    id: str
    name: str
    mode: Literal["ai", "guided", "discovery", "hybrid", "manual"] = "manual"
    base_url: str | None = None
    test_case_id: str | None = None
    user_story_id: str | None = None
    application: str | None = None
    credential_role: str | None = None
    browser: Literal["chromium", "firefox", "webkit", "msedge", "chrome"] = "chromium"
    steps: list[Step] = []
    version: int = 1
    created_at: str | None = None
    updated_at: str | None = None
    notes: str | None = None
    data_table: DataTable | None = None


def describe(step: Step) -> str:
    """Plain-language description used when a step has none (shown in runs, history and defects)."""
    if step.description:
        return step.description
    t = step.target
    what = (t.description or t.name or t.text or t.label or t.selector) if t else ""
    v = step.value or ""
    return {
        "goto": f"Open {v}", "click": f"Click {what}", "dblclick": f"Double-click {what}", "fill": f"Enter value in {what}",
        "type": f"Type into {what}", "select": f"Select '{v}' in {what}", "pick_date": f"Pick date {v} in {what}", "check": f"Check {what}",
        "uncheck": f"Uncheck {what}", "press": f"Press {v or 'Enter'}" + (f" in {what}" if what else ""),
        "hover": f"Hover over {what}", "wait": f"Wait {v or 1000} ms", "expect_visible": f"Verify {what} is visible",
        "expect_hidden": f"Verify {what} is hidden", "expect_text": f"Verify {what} shows '{v}'",
        "expect_value": f"Verify {what} has value '{v}'", "expect_url": f"Verify the URL contains '{v}'",
        "screenshot": "Take a screenshot",
        "upload_file": f"Upload {v or 'a file'} in {what}", "download_file": f"Download the file from {what}",
        "expect_file_name": f"Verify the downloaded file name contains '{v}'",
        "expect_file_text": f"Verify the downloaded file contains '{v}'",
        "expect_excel_cell": f"Verify the downloaded Excel has {v}",
        "right_click": f"Right-click {what}", "drag_to": f"Drag {what} to {v}",
        "dialog": "Cancel the next pop-up" if v == "dismiss" else "OK on the next pop-up",
        "choose_option": f"Choose '{v}' in {what}", "dismiss": "Close the open pop-up",
    }.get(step.action, step.action)


def new_script_id() -> str:
    return "SCR-" + uuid.uuid4().hex[:10].upper()


# ---- runtime values -------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Z]+)((?:\s*[+-]\s*\d+\s*(?:min|[dwmyh]))*)\s*(?::([^}]*))?\s*\}\}")
_OFFSET = re.compile(r"([+-])\s*(\d+)\s*(min|[dwmyh])")


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def shift(base: datetime, amount: int, unit: str) -> datetime:
    if unit == "h":
        return base + timedelta(hours=amount)
    if unit == "min":
        return base + timedelta(minutes=amount)
    if unit == "d":
        return base + timedelta(days=amount)
    if unit == "w":
        return base + timedelta(weeks=amount)
    if unit == "m":
        return datetime.combine(_add_months(base.date(), amount), base.time(), base.tzinfo)
    if unit == "y":
        return datetime.combine(_add_months(base.date(), amount * 12), base.time(), base.tzinfo)
    return base


class ValueResolver:
    def __init__(
        self,
        data: dict[str, Any] | None = None,
        credentials: Callable[[str, str], str | None] | None = None,
        unique: str | None = None,
        clock: Callable[[], datetime] = now,
    ) -> None:
        self.data = data or {}
        self.credentials = credentials
        self.unique = unique or uuid.uuid4().hex[:8]
        self.clock = clock
        self.secrets_used: list[str] = []

    def resolve(self, text: str | None) -> str | None:
        if text is None:
            return None
        return _PLACEHOLDER.sub(self._one, text)

    def _one(self, m: re.Match[str]) -> str:
        kind, offsets, arg = m.group(1), m.group(2) or "", m.group(3)
        current = self.clock()
        if kind in ("TODAY", "DATE", "NOW"):
            value = current
            for sign, n, unit in _OFFSET.findall(offsets):
                value = shift(value, int(n) * (-1 if sign == "-" else 1), unit)
            return value.strftime(arg or ("%Y-%m-%d %H:%M" if kind == "NOW" else "%Y-%m-%d"))
        if offsets:
            return m.group(0)
        if kind == "RANDOM":
            n = int(arg) if arg and arg.isdigit() else 6
            return "".join(random.choice("0123456789") for _ in range(max(1, min(n, 32))))
        if kind == "UNIQUE":
            return self.unique
        if kind == "DATA":
            if arg not in self.data:
                raise KeyError(f"Test data field '{arg}' is not in the selected dataset.")
            text = str(self.data[arg])
            # 23.3.17: a data cell may hold a rule itself ({{TODAY+1d:%d/%m/%Y}}); one level, never another DATA
            if "{{" in text and "{{DATA" not in text.replace(" ", ""):
                return _PLACEHOLDER.sub(self._one, text)
            return text
        if kind == "CRED":
            if not arg or "." not in arg or self.credentials is None:
                raise KeyError(f"Credential reference '{arg}' is invalid.")
            role, field = arg.rsplit(".", 1)
            value = self.credentials(role, field)
            if value is None:
                raise KeyError(f"No saved credential '{field}' for role '{role}'.")
            if field == "password":
                self.secrets_used.append(value)
            return value
        return m.group(0)

    def mask(self, text: str | None) -> str | None:
        """Remove resolved secrets from text before it reaches logs, events or evidence."""
        if text is None:
            return None
        for secret in self.secrets_used:
            if secret:
                text = text.replace(secret, "***")
        return text


def is_secret_value(raw: str | None) -> bool:
    return bool(raw) and ".password" in raw and "{{CRED:" in raw


# ---- export ------------------------------------------------------------------------


def _py(s: str | None) -> str:
    return repr(s or "")


def to_playwright_python(script: Script) -> str:
    lines = [
        f"# {script.name} ({script.id}) - exported from AI Agentic Test Agent Enterprise",
        "# Runtime values such as {{TODAY}} and {{CRED:role.password}} are resolved by the Enterprise QA Runtime.",
        "import re",
        "",
        "from playwright.sync_api import sync_playwright, expect",
        "",
        "",
        "def run(page):",
    ]
    for i, step in enumerate(script.steps, start=1):
        sel = _py(step.target.selector) if step.target else "''"
        loc = f"page.locator({sel})"
        comment = f"    # {i}. {step.description}" if step.description else f"    # {i}."
        lines.append(comment)
        a = step.action
        if a == "goto":
            lines.append(f"    page.goto({_py(step.value)})")
        elif a in ("click", "dblclick", "hover", "check", "uncheck"):
            lines.append(f"    {loc}.{a}()")
        elif a == "right_click":
            lines.append(f"    {loc}.click(button=\"right\")")
        elif a == "drag_to":
            lines.append(f"    {loc}.drag_to(page.locator({_py(step.value)}))")
        elif a == "dialog":
            how = "dismiss" if step.value == "dismiss" else "accept"
            lines.append(f"    page.once(\"dialog\", lambda d: d.{how}())")
        elif a in ("fill", "type"):
            lines.append(f"    {loc}.fill({_py(step.value)})")
        elif a == "select":
            lines.append(f"    {loc}.select_option({_py(step.value)})")
        elif a == "pick_date":  # the Enterprise QA Runtime opens the calendar and clicks the day; plain export types it
            lines.append(f"    {loc}.fill({_py(step.value)})")
        elif a == "press":
            lines.append(f"    page.keyboard.press({_py(step.value)})" if not step.target else f"    {loc}.press({_py(step.value)})")
        elif a == "wait":
            lines.append(f"    page.wait_for_timeout({int(step.value or 1000)})")
        elif a == "expect_visible":
            lines.append(f"    expect({loc}).to_be_visible()")
        elif a == "expect_hidden":
            lines.append(f"    expect({loc}).to_be_hidden()")
        elif a == "expect_text":
            lines.append(f"    expect({loc}).to_contain_text({_py(step.value)})")
        elif a == "expect_value":
            lines.append(f"    expect({loc}).to_have_value({_py(step.value)})")
        elif a == "expect_url":
            lines.append(f"    expect(page).to_have_url(re.compile({_py(re.escape(step.value or ''))}))")
        elif a == "screenshot":
            lines.append(f"    page.screenshot(path={_py((step.value or 'step') + '.png')})")
        elif a == "choose_option":
            lines.append(f"    {loc}.click()")
            lines.append(f"    page.get_by_role(\"option\", name={_py(step.value)}).first.click()")
        elif a == "dismiss":
            lines.append("    page.keyboard.press(\"Escape\")")
    if not script.steps:
        lines.append("    pass")
    lines += [
        "",
        "",
        'if __name__ == "__main__":',
        "    with sync_playwright() as p:",
        "        browser = p.chromium.launch(headless=False)",
        "        run(browser.new_page())",
        "        browser.close()",
        "",
    ]
    return "\n".join(lines)
