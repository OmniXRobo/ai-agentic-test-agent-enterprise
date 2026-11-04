"""Export scripts as standalone Python test files (pytest + Playwright).

Each file is self-contained: it runs with ``pytest`` (one test per test data set) or with
``python <file>.py``. Runtime values are resolved when the test runs, the same way the
Enterprise QA Runtime does:

    {{TODAY}} / {{TODAY+3d:%d/%m/%Y}} / {{TODAY-2d}}    system clock (date, +/- days, weeks, months, years)
    {{NOW:%H:%M}} / {{NOW+1h+30min:%H:%M}} / {{NOW-4h}}  system clock (time, +/- hours and minutes)
    {{DATA:field}}                                      the test data set of the current test
    {{CRED:role.username}} / {{CRED:role.password}}     environment variables ROLE_USERNAME / ROLE_PASSWORD
    {{RANDOM:6}} / {{UNIQUE}}                           random digits / run-unique token

No password or other secret is ever written into the file.
"""

from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from typing import Any

from ..config import APP_ROOT
from .dsl import Script, describe

_SLUG = re.compile(r"[^a-z0-9]+")
_CRED = re.compile(r"\{\{\s*CRED:([^}.]+)\.(\w+)\s*\}\}")

PYTEST_VERSION = "8.3.4"
PLAYWRIGHT_VERSION = "1.56.0"
PYPDF_VERSION = "6.21.4"


def file_name(script: Script) -> str:
    slug = _SLUG.sub("_", script.name.lower()).strip("_")[:70] or "script"
    if script.test_case_id:
        slug = re.sub(rf"^tc_?{re.escape(str(script.test_case_id))}_?", "", slug) or "test_case"
        return f"test_tc{script.test_case_id}_{slug}.py"
    return f"test_{slug}.py"


def _env_name(role: str, field: str) -> str:
    return re.sub(r"\W+", "_", f"{role}_{field}").upper()


def credential_variables(script: Script) -> list[str]:
    found: list[str] = []
    for step in script.steps:
        for role, field in _CRED.findall(step.value or ""):
            name = _env_name(role, field)
            if name not in found:
                found.append(name)
    return found


def _locator_args(step: Any) -> str:
    t = step.target
    selectors = [t.selector, *[a for a in t.alternates if a != t.selector]][:4]
    return repr(selectors)


_RUNTIME = '''
BASE_URL = os.environ.get("BASE_URL", {base_url!r})
BROWSER = os.environ.get("BROWSER", {browser!r})
HEADLESS = os.environ.get("HEADLESS", "0").lower() in ("1", "true", "yes")
UNIQUE = uuid.uuid4().hex[:8]
_VALUE = re.compile(r"\\{{\\{{\\s*([A-Z]+)((?:\\s*[+-]\\s*\\d+\\s*(?:min|[dwmyh]))*)\\s*(?::([^}}]*))?\\s*\\}}\\}}")
_OFFSET = re.compile(r"([+-])\\s*(\\d+)\\s*(min|[dwmyh])")
FILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files", {files_folder!r})
DOWNLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "downloads")


def _shift(base, amount, unit):
    if unit == "h":
        return base + timedelta(hours=amount)
    if unit == "min":
        return base + timedelta(minutes=amount)
    if unit == "d":
        return base + timedelta(days=amount)
    if unit == "w":
        return base + timedelta(weeks=amount)
    months = amount * (12 if unit == "y" else 1)
    month = base.month - 1 + months
    year, month = base.year + month // 12, month % 12 + 1
    return base.replace(year=year, month=month, day=min(base.day, calendar.monthrange(year, month)[1]))


def value(text, data):
    """Resolve {{{{TODAY}}}}, {{{{DATA:field}}}}, {{{{CRED:role.password}}}} and the other runtime values."""
    def one(m):
        kind, offsets, arg = m.groups()
        current = datetime.now()
        if kind in ("TODAY", "DATE", "NOW"):
            for sign, n, unit in _OFFSET.findall(offsets or ""):
                current = _shift(current, int(n) * (-1 if sign == "-" else 1), unit)
            return current.strftime(arg or ("%Y-%m-%d %H:%M" if kind == "NOW" else "%Y-%m-%d"))
        if kind == "RANDOM":
            return "".join(random.choice("0123456789") for _ in range(int(arg) if arg and arg.isdigit() else 6))
        if kind == "UNIQUE":
            return UNIQUE
        if kind == "DATA":
            if arg not in data:
                raise KeyError(f"Test data field '{{arg}}' is not in this data set.")
            text = str(data[arg])
            return _VALUE.sub(one, text) if "{{{{" in text and "DATA:" not in text else text
        if kind == "CRED":
            role, field = arg.rsplit(".", 1)
            name = re.sub(r"\\W+", "_", f"{{role}}_{{field}}").upper()
            if name not in os.environ:
                raise KeyError(f"Set the environment variable {{name}} before running this test.")
            return os.environ[name]
        return m.group(0)

    return None if text is None else _VALUE.sub(one, text)


def url(text, data):
    target = value(text, data) or BASE_URL
    return target if re.match(r"^[a-z]+://", target) else urljoin(BASE_URL.rstrip("/") + "/", target.lstrip("/"))


def find(page, selectors):
    """The first selector that matches an element now; the others are recorded alternates."""
    for selector in selectors:
        try:
            if page.locator(selector).count():
                return page.locator(selector).first
        except Exception:
            continue
    return page.locator(selectors[0]).first


def _launch(playwright):
    kind = "chromium" if BROWSER in ("chromium", "msedge", "chrome") else BROWSER
    options = {{"headless": HEADLESS}}
    if BROWSER in ("msedge", "chrome"):
        options["channel"] = BROWSER
    return getattr(playwright, kind).launch(**options)


def _open_page():
    playwright = sync_playwright().start()
    browser = _launch(playwright)
    page = browser.new_page()

    def close():
        browser.close()
        playwright.stop()

    return page, close


if pytest is not None:
    @pytest.fixture()
    def page():
        page, close = _open_page()
        yield page
        close()
'''


_FILE_HELPERS = '''

def upload_files(text, data):
    names = [n.strip() for n in re.split(r"[;\\n]", value(text, data) or "") if n.strip()]
    return [n if os.path.isabs(n) else os.path.join(FILES_DIR, n) for n in names]


def upload(page, locator, text, data, timeout):
    files = upload_files(text, data)
    if locator.evaluate("el => el.tagName === 'INPUT' && el.type === 'file'"):
        locator.set_input_files(files, timeout=timeout)
        return
    with page.expect_file_chooser(timeout=timeout) as chooser:
        locator.click(timeout=timeout)
    chooser.value.set_files(files)


_LAST_DOWNLOAD = []


def download(page, locator, name, timeout):
    with page.expect_download(timeout=max(timeout, 30000)) as info:
        locator.click(timeout=timeout)
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    path = os.path.join(DOWNLOADS_DIR, name or info.value.suggested_filename or "download")
    info.value.save_as(path)
    _LAST_DOWNLOAD[:] = [path]
    return path


def _last_download():
    assert _LAST_DOWNLOAD, "No file was downloaded before this check."
    return _LAST_DOWNLOAD[0]


def _norm(text):
    return " ".join(str(text).split()).casefold()


def _xlsx(path):
    import zipfile
    from xml.etree import ElementTree as ET
    ns = {{"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}}
    with zipfile.ZipFile(path) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            for si in ET.fromstring(zf.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter("{{%s}}t" % ns["m"])))
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = {{r.get("Id"): "xl/" + r.get("Target").lstrip("/").replace("xl/", "", 1) for r in
                ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))}}
        sheets = {{}}
        for n, sh in enumerate(wb.findall("m:sheets/m:sheet", ns), start=1):
            part = rels.get(sh.get("{{%s}}id" % ns["r"]), "xl/worksheets/sheet%d.xml" % n)
            cells = {{}}
            for c in ET.fromstring(zf.read(part)).iter("{{%s}}c" % ns["m"]):
                v = c.find("m:v", ns)
                if c.get("t") == "s" and v is not None:
                    text = shared[int(v.text)]
                elif c.get("t") == "inlineStr":
                    text = "".join(x.text or "" for x in c.iter("{{%s}}t" % ns["m"]))
                else:
                    text = v.text if v is not None and v.text else ""
                cells[c.get("r")] = text
            sheets[sh.get("name")] = cells
        return sheets


def file_text(path):
    if path.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader  # pip install pypdf
        except ImportError:  # the copy shipped with the QA platform (RUN_TESTS.cmd sets QA_PLATFORM_VENDOR)
            import sys
            sys.path.append(os.environ.get("QA_PLATFORM_VENDOR", ""))
            from pypdf import PdfReader
        return "\\n".join(p.extract_text() or "" for p in PdfReader(path).pages)
    if path.lower().endswith((".xlsx", ".xlsm")):
        return "\\n".join(" ".join(cells.values()) for cells in _xlsx(path).values())
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8", "replace")


def expect_file_name(text, data):
    name = os.path.basename(_last_download())
    assert _norm(value(text, data)) in _norm(name), f"The downloaded file is {{name}}"


def expect_file_text(text, data):
    content = _norm(file_text(_last_download()))
    for part in (value(text, data) or "").split("||"):
        assert _norm(part) in content, f"The downloaded file does not contain {{part.strip()!r}}"


def expect_excel_cell(text, data):
    left, expected = (value(text, data) or "").split("=", 1)
    sheet, cell = left.rsplit("!", 1) if "!" in left else (None, left)
    book = _xlsx(_last_download())
    cells = book[next(k for k in book if not sheet or k.lower() == sheet.strip().lower())]
    actual = cells.get(cell.strip().upper(), "")
    try:
        same = abs(float(actual) - float(expected)) < 1e-9
    except ValueError:
        same = _norm(actual) == _norm(expected)
    assert same, f"{{cell.strip().upper()}} is {{actual!r}}, expected {{expected.strip()!r}}"
'''

FILE_ACTIONS = {"upload_file", "download_file", "expect_file_name", "expect_file_text", "expect_excel_cell"}


def uses_files(script: Script) -> bool:
    return any(s.action in FILE_ACTIONS for s in script.steps)


def files_folder(script: Script) -> str:
    return script.id.lower().replace("-", "_")


def to_pytest(script: Script, datasets: list[dict[str, Any]] | None = None) -> str:
    """One pytest file for the script; ``datasets`` are [{name, values}] (one test per data set)."""
    datasets = [d for d in (datasets or []) if isinstance(d.get("values"), dict)]
    creds = credential_variables(script)
    func = "test_" + (_SLUG.sub("_", script.name.lower()).strip("_")[:60] or "script")
    header = [
        f'"""{_text(script.name)}',
        "",
        f"Test Case: #{script.test_case_id}" if script.test_case_id else "Test Case: (not linked)",
        f"User Story: #{script.user_story_id}" if script.user_story_id else "User Story: (not linked)",
        f"Script: {script.id} version {script.version} - generated by AI Agentic Test Agent Enterprise.",
        "",
        "Run with pytest:     pytest -v " + file_name(script),
        "Run without pytest:  python " + file_name(script),
        "Settings (environment variables): BASE_URL, BROWSER, HEADLESS=1"
        + (", " + ", ".join(creds) if creds else "") + ".",
        '"""',
        "",
        "import calendar",
        "import os",
        "import random",
        "import re",
        "import uuid",
        "from datetime import datetime, timedelta",
        "from urllib.parse import urljoin",
        "",
        "from playwright.sync_api import expect, sync_playwright",
        "",
        "try:",
        "    import pytest",
        "except ImportError:  # still runs with: python <this file>",
        "    pytest = None",
        "",
        "# One test run per data set (the Test Data of the TFS Test Case and the rows imported from Excel / CSV).",
        "TEST_DATA = " + _data_literal(datasets),
    ]
    body = _RUNTIME.format(base_url=script.base_url or "", browser=script.browser, files_folder=files_folder(script))
    if uses_files(script):
        body += _FILE_HELPERS.format()
    steps = [
        "",
        "",
        "def _data_sets(func):",
        "    if pytest is None:",
        "        return func",
        '    return pytest.mark.parametrize("data", [d for _, d in TEST_DATA], ids=[n for n, _ in TEST_DATA])(func)',
        "",
        "",
        "@_data_sets",
        f"def {func}(page, data):",
    ]
    for i, step in enumerate(script.steps, start=1):
        steps.append(f"    # Step {i}: {_text(describe(step))}")
        steps.extend("    " + line for line in _step_lines(step))
    if not script.steps:
        steps.append("    pass")
    steps += [
        "",
        "",
        'if __name__ == "__main__":',
        "    failed = 0",
        "    for name, data in TEST_DATA:",
        "        page, close = _open_page()",
        "        try:",
        f"            {func}(page, data)",
        '            print(f"PASSED  {name}")',
        "        except Exception as exc:",
        "            failed += 1",
        '            print(f"FAILED  {name}: {exc}")',
        "        finally:",
        "            close()",
        "    raise SystemExit(1 if failed else 0)",
        "",
    ]
    return "\n".join(header) + "\n" + body + "\n".join(steps)


def _data_literal(datasets: list[dict[str, Any]]) -> str:
    if not datasets:
        return '[("default", {})]'
    rows = [f"    ({str(d.get('name') or f'data set {n}')!r}, {_plain(d['values'])!r})"
            for n, d in enumerate(datasets, start=1)]
    return "[\n" + ",\n".join(rows) + ",\n]"


def _plain(value: Any) -> Any:
    """Only literals Python can read back (data sets come from JSON)."""
    return json.loads(json.dumps(value, default=str))


def _text(value: str) -> str:
    """Safe inside a docstring or a comment."""
    return " ".join(value.replace("\\", "/").replace('"""', "'''").split())


def _step_lines(step: Any) -> list[str]:
    a = step.action
    v = f"value({step.value!r}, data)"
    root = "page" + "".join(f".frame_locator({f!r})" for f in (step.target.frames if step.target else []))
    loc = f"find({root}, {_locator_args(step)})" if step.target else None  # 23.3.21: inside iframes too
    timeout = step.timeout_ms
    lines: list[str]
    if a == "goto":
        lines = [f"page.goto(url({step.value!r}, data), wait_until=\"domcontentloaded\")"]
    elif a == "wait":
        lines = [f"page.wait_for_timeout(int({v} or 1000))"]
    elif a == "screenshot":
        lines = [f"page.screenshot(path=f\"{{value({(step.value or 'step')!r}, data)}}.png\")"]
    elif a == "press" and not loc:
        lines = [f"page.keyboard.press({v} or \"Enter\")"]
    elif a == "expect_url":
        lines = [f"expect(page).to_have_url(re.compile(re.escape({v} or \"\")), timeout={timeout})"]
    elif a in ("expect_file_name", "expect_file_text", "expect_excel_cell"):
        lines = [f"{a}({step.value!r}, data)"]
    elif a == "dialog":  # 23.3.21: answer the next pop-up (alert / confirm / prompt)
        how = "dismiss" if step.value == "dismiss" else "accept"
        lines = [f"page.once(\"dialog\", lambda d: d.{how}())"]
    elif loc is None:
        lines = [f"pass  # '{a}' needs an element; none was recorded"]
    elif a in ("click", "dblclick", "hover", "check", "uncheck"):
        lines = [f"{loc}.{a}(timeout={timeout})"]
    elif a == "right_click":
        lines = [f"{loc}.click(button=\"right\", timeout={timeout})"]
    elif a == "drag_to":
        lines = [f"{loc}.drag_to(page.locator({step.value!r}), timeout={timeout})"]
    elif a in ("fill", "type"):
        lines = [f"{loc}.fill({v} or \"\", timeout={timeout})"]
    elif a == "select":
        lines = [f"{loc}.select_option({v}, timeout={timeout})"]
    elif a == "pick_date":  # typed in ISO form here; the runtime and the framework use the calendar itself
        lines = [f"{loc}.fill({v} or \"\", timeout={timeout})"]
    elif a == "press":
        lines = [f"{loc}.press({v} or \"Enter\", timeout={timeout})"]
    elif a == "expect_visible":
        lines = [f"expect({loc}).to_be_visible(timeout={timeout})"]
    elif a == "expect_hidden":
        lines = [f"expect({loc}).to_be_hidden(timeout={timeout})"]
    elif a == "expect_text":
        lines = [f"expect({loc}).to_contain_text({v} or \"\", timeout={timeout})"]
    elif a == "expect_value":
        lines = [f"expect({loc}).to_have_value({v} or \"\", timeout={timeout})"]
    elif a == "upload_file":
        lines = [f"upload(page, {loc}, {step.value!r}, data, {timeout})"]
    elif a == "download_file":
        lines = [f"download(page, {loc}, {v}, {timeout})"]
    else:
        lines = [f"pass  # unsupported action '{a}'"]
    if step.optional:
        return ["try:", *("    " + x for x in lines), "except Exception:", "    pass  # optional step"]
    return lines


# ---- bundle -----------------------------------------------------------------------------------

README = """Automated tests (Python, pytest + Playwright)
=============================================

Generated by AI Agentic Test Agent Enterprise on {generated}.
{count} test file(s) in the "tests" folder, one per Test Case.

How to run (Windows)
--------------------
1. Double-click RUN_TESTS.cmd. It uses the Python of the QA platform when it is
   on this PC (it already has pytest and Playwright), otherwise "py -3".
2. The results are printed in the window and saved to report.xml (JUnit format).

Settings
--------
Set these before running, or edit RUN_TESTS.cmd:
  BASE_URL   the address of the application (default: the address used when the script was made)
  HEADLESS   1 to run without showing the browser
  BROWSER    chromium (default), msedge, chrome, firefox or webkit
{credentials}
Without the QA platform
-----------------------
  py -3 -m pip install -r requirements.txt
  py -3 -m playwright install chromium
  py -3 -m pytest
"""

RUN_CMD = r"""@echo off
rem Runs every test in the "tests" folder with pytest and writes report.xml.
setlocal
cd /d "%~dp0"
set "PY="
if exist "{python}" set "PY={python}"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  echo Python was not found. Install Python 3, then run: py -3 -m pip install -r requirements.txt
  pause
  exit /b 1
)
if not defined PLAYWRIGHT_BROWSERS_PATH if exist "{browsers}" set "PLAYWRIGHT_BROWSERS_PATH={browsers}"
if not defined QA_PLATFORM_VENDOR if exist "{vendor}" set "QA_PLATFORM_VENDOR={vendor}"
rem set "BASE_URL=https://your-app/"
rem set "HEADLESS=1"
{credential_lines}%PY% -m pytest --junitxml=report.xml %*
pause
endlocal
"""


def bundle(scripts: list[tuple[Script, list[dict[str, Any]]]], generated: str,
           files: dict[str, Any] | None = None) -> bytes:
    """A zip with tests/, pytest.ini, requirements.txt, README.txt and RUN_TESTS.cmd."""
    creds: list[str] = []
    names: set[str] = set()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for script, datasets in scripts:
            name = file_name(script)
            if name in names:
                name = name[:-3] + f"_{script.id.lower().replace('-', '_')}.py"
            names.add(name)
            zf.writestr(f"tests/{name}", to_pytest(script, datasets))
            folder = (files or {}).get(script.id)
            if folder is not None and uses_files(script) and folder.is_dir():  # 23.3.17: files for upload steps
                for f in sorted(folder.iterdir()):
                    if f.is_file():
                        zf.write(f, f"tests/files/{files_folder(script)}/{f.name}")
            creds += [c for c in credential_variables(script) if c not in creds]
        zf.writestr("pytest.ini", "[pytest]\ntestpaths = tests\naddopts = -v\n")
        zf.writestr("requirements.txt", f"pytest=={PYTEST_VERSION}\nplaywright=={PLAYWRIGHT_VERSION}\n"
                    + (f"pypdf=={PYPDF_VERSION}\n" if any(uses_files(s) for s, _ in scripts) else ""))
        cred_text = ("  " + "\n  ".join(f"{c}{'   (secret: never saved in the files)' if 'PASSWORD' in c else ''}"
                                        for c in creds) + "\n") if creds else ""
        zf.writestr("README.txt", README.format(generated=generated, count=len(names), credentials=cred_text)
                    .replace("\n", "\r\n"))
        python = sys.executable if sys.platform == "win32" else ""
        browsers = str(APP_ROOT / "runtime" / "ms-playwright") if sys.platform == "win32" else ""
        cred_lines = "".join(f'rem set "{c}=..."\n' for c in creds)
        zf.writestr("RUN_TESTS.cmd", RUN_CMD.format(python=python or "python.exe-not-found",
                                                     browsers=browsers or "ms-playwright-not-found",
                                                     credential_lines=cred_lines,
                                                     vendor=str(APP_ROOT / "qa_platform" / "vendor")
                                                     if sys.platform == "win32" else "vendor-not-found")
                                                     .replace("\n", "\r\n"))
    return buf.getvalue()

