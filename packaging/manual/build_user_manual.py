"""Builds the SkyGuard QA user manual (PDF with screenshots of the application) for 23.15.1.

It starts SkyGuard QA in-process with an empty data folder (nothing is sent anywhere), signs in, adds one example
Guided Browser script so the script editor can be shown, takes a screenshot of each page described, writes the manual
as HTML and prints it to PDF with Playwright's Chromium. The PDF is shipped in web/manual and attached to the welcome
email of every new account.

    python packaging/manual/build_user_manual.py [--out web/manual/SkyGuard_QA_User_Manual.pdf] [--shots <folder>]
"""

from __future__ import annotations

import argparse
import base64
import html
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402

from qa_platform import __version__  # noqa: E402
from qa_platform.auth import COOKIE  # noqa: E402
from qa_platform.config import Paths  # noqa: E402
from qa_platform.credentials import MemoryBackend  # noqa: E402
from qa_platform.main import create_app  # noqa: E402

E = html.escape

# (route, title, what the reader learns, steps) -- written for testers, managers and developers
CHAPTERS: list[tuple[str, str, str, list[str]]] = [
    ("dashboard", "Dashboard",
     "The first page after signing in. The charts are at the top: pass / fail, executions per day, defects, defect "
     "drafts per module, test cases, user stories, API Testing results, self-healing and test data. Every slice, bar "
     "and day can be clicked to open the items behind it. Key figures, QA progress and performance follow; the AI "
     "dashboard analysis is at the bottom.",
     ["Click a slice of a chart (for example Failed) to open the matching runs in Execution History.",
      "Click a day in 'Executions · last 7 days' to see the runs of that day.",
      "At the bottom, press Generate AI analysis, read or edit it, then Generate PDF, Download PDF or Email PDF."]),
    ("command-center", "AI Voice Command Center",
     "Speak or type commands. English and Arabic (Modern Standard and UAE Arabic) are understood; an Arabic command "
     "gets an Arabic reply. Everything runs on this PC (Faster-Whisper and the On-Premise AI), nothing goes to the "
     "internet.",
     ["Press the microphone and say for example 'open the dashboard' or 'افتح لوحة التحكم'.",
      "Choose the voice (male or female) under Settings > Voice & Command Agent; Arabic replies use an Arabic Windows "
      "voice of the same gender when one is installed.",
      "For Arabic speech, set Language to Arabic or Auto and install the multilingual speech model (INSTALL_SPEECH_MODEL)."]),
    ("ai-automation", "AI-Driven Automation",
     "Generates Playwright tests from TFS user stories and test cases, runs them and keeps them up to date. The "
     "browser is the one chosen under Settings > Preferences (for example Microsoft Edge); when the included Chromium "
     "is not installed, Edge is used automatically.",
     ["Choose the user story or test cases, then Generate.",
      "Use the tabs View Script, Edit Script, Flow Logic, Test Data, Credentials, Components, History, Result, "
      "Evidence and Logs.",
      "Use the bar at the top for Generate AI result, Create TFS defect and Send email (PDF)."]),
    ("guided-automation", "Guided Browser Automation",
     "Record a test by using the application in the browser, then edit the steps and the Python test. When a step "
     "cannot find its element, the run pauses and asks you to show it: do the step yourself in the browser and press "
     "Continue. The element you used is saved in the script, so the next run finds it at once and does not ask again.",
     ["Record, give the script a name, Save.",
      "Open a script: the tabs show the Python test, the steps (Flow Logic), test data, credentials, components, "
      "history, result, evidence and the live log. Version history is at the bottom.",
      "Copy script copies the Python test to the clipboard; Taller / Full screen enlarge the editor."]),
    ("discovery", "Application Discovery & Update",
     "Explores an application, lists its pages and elements, and updates scripts when the application changed. "
     "Addresses with a changing id (for example a booking number in the address) are recognised as the same page.",
     ["Enter the start address and Discover.", "Review the pages found, then update the scripts that use them."]),
    ("hybrid-automation", "Hybrid Test Automation",
     "Records new steps into an existing script, before a step or in place of one, with the earlier steps played "
     "first.", ["Open a script, choose where to record, record the new steps, Save."]),
    ("api-testing", "API Testing",
     "Tests REST APIs: Test Connection, import an OpenAPI / Swagger document, generate and run tests, open each "
     "result. Test Connection trusts the company certificates that Windows trusts, so an intranet HTTPS API is "
     "checked correctly offline.",
     ["Add the application and its address, then Test Connection.", "Import the API description and Run.",
      "In Results, press Open on a row to see the request and the response."]),
    ("scheduled-runs", "Scheduled Runs",
     "Runs tests, API tests and comparisons on a schedule, visible or headless, and emails the report.",
     ["Add a schedule, choose what to run and when, Save."]),
    ("ai-agent", "Workflow Operator",
     "Works an application for you from plain commands, in one browser window for the whole conversation: "
     "'open https://...', then 'log in as manager' (the password is asked once and kept in the Windows Credential "
     "Manager), then 'go to the bookings menu'. A new window opens only when you ask for one ('open it in a new "
     "window'). Public websites that need no sign-in are used without an account: tick 'This website needs no "
     "sign-in' or say 'no account needed'. Arabic commands are translated to English first.",
     ["Connect the application (its address) or simply type 'open <address>'.",
      "Say 'log in as admin', 'log in as client' or 'log in as manager'.",
      "Give the next command; the same browser window is used."]),
    ("defect-review", "Defect Review",
     "Every failure becomes a draft here. You check it, choose who it is assigned to (the list of people comes from "
     "your TFS project), approve it and send it to TFS.",
     ["Open a draft, check the title, severity and steps.", "Choose the person in 'Assign to'.",
      "Approve, then Send to TFS. The defect number is shown when TFS created it."]),
    ("perf-results", "Performance Results",
     "Load, stress, spike and soak test results with response times, throughput, errors and SLA checks.",
     ["Open a run to see its requests and charts; Generate AI result writes an analysis with a PDF."]),
    ("analytics", "Analytics",
     "Functional and performance trends. At the bottom, Generate AI analysis writes what the trends show; make the "
     "PDF and email it from the same place.",
     ["Choose the period.", "Generate AI analysis, Generate PDF, Email PDF."]),
    ("tfs-settings", "TFS Connection",
     "Every account works in its own TFS project with its own Personal Access Token. Changing your project never "
     "changes anybody else's project.",
     ["Enter your project and your PAT, Save, then Test Connection."]),
    ("users", "Users & Network Access (administrators)",
     "Create accounts: user name, password, role, email, TFS project and the person's own PAT (checked against the "
     "project). The new user receives an email with the address of SkyGuard QA, the user name, the password and "
     "this manual. Here you also turn on Network access and HTTPS with a friendly name (for example "
     "https://skyguard-qa/), made on this PC without internet.",
     ["Fill in the new user and press Create user.",
      "HTTPS: enter the name, tick Use HTTPS, Save, press Trust on this PC, then restart SkyGuard QA.",
      "Colleagues run TRUST_SKYGUARD_QA.cmd once with the server's address."]),
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def seed(rt) -> str:
    from qa_platform.automation.dsl import Script, Step, Target, new_script_id
    script = Script(id=new_script_id(), name="Book a flight (example)", mode="guided",
                    base_url="https://booking.example.ae", application="Booking", credential_role="client",
                    steps=[Step(action="goto", value="https://booking.example.ae/"),
                           Step(action="fill", target=Target(selector="#from", label="From"), value="Dubai"),
                           Step(action="fill", target=Target(selector="#to", label="To"), value="Karachi"),
                           Step(action="pick_date", target=Target(selector="#date", label="Date"), value="{{TODAY+1d:%Y-%m-%d}}"),
                           Step(action="click", target=Target(selector="#search", text="Search", tag="button")),
                           Step(action="expect_text", target=Target(selector="h1"), value="Flights")])
    return rt.automation.scripts.save(script).id


def main() -> None:
    import uvicorn
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "web" / "manual" / "SkyGuard_QA_User_Manual.pdf"))
    ap.add_argument("--shots", default="")
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="sgmanual-"))
    shots_dir = Path(args.shots) if args.shots else work / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    paths = Paths(work / "data").ensure()
    app = create_app(paths, secret_backend=MemoryBackend())
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            if httpx.get(base + "/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.25)
    from tests.auth_helper import session_token
    token = session_token(base)
    script_id = seed(app.state.runtime)
    app.state.runtime.settings.update({"ui": {"theme": "light"}})
    images: dict[str, str] = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
        ctx.add_cookies([{"name": COOKIE, "value": token, "url": base + "/"}])
        page = ctx.new_page()
        page.add_init_script("try { localStorage.setItem('qa.theme', 'light'); } catch (e) {}")
        for route, *_ in CHAPTERS + [("guided-editor", "", "", [])]:
            target = f"#/guided-automation?script={script_id}" if route == "guided-editor" else f"#/{route}"
            page.goto(f"{base}/{target}")
            page.wait_for_timeout(2200)
            page.evaluate("document.documentElement.setAttribute('data-theme', 'light')")
            file = shots_dir / f"{route}.jpg"
            page.screenshot(path=str(file), type="jpeg", quality=72)
            images[route] = base64.b64encode(file.read_bytes()).decode()
        chapters = "".join(
            f"<section><h2>{i}. {E(title)}</h2><p>{E(text)}</p><ol>{''.join(f'<li>{E(s)}</li>' for s in steps)}</ol>"
            f"<img src='data:image/jpeg;base64,{images[route]}' alt='{E(title)}'>"
            + (f"<p class='cap'>A Guided Browser script with its tabs (View Script, Edit Script, Flow Logic ...).</p>"
               f"<img src='data:image/jpeg;base64,{images['guided-editor']}' alt='Script editor'>" if route == "guided-automation" else "")
            + "</section>" for i, (route, title, text, steps) in enumerate(CHAPTERS, start=3))
        doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>SkyGuard QA user manual</title><style>
@page {{ size: A4; margin: 16mm 14mm; }}
body {{ font-family: 'Segoe UI', Arial, sans-serif; color: #111827; font-size: 11pt; line-height: 1.45; }}
h1 {{ font-size: 26pt; margin: 0 0 6px; color: #0b3b7a; }} h2 {{ font-size: 15pt; color: #0b3b7a; margin: 0 0 6px; }}
section {{ page-break-before: always; }} img {{ width: 100%; border: 1px solid #d1d5db; border-radius: 6px; margin-top: 8px; }}
.cover {{ height: 240mm; display: flex; flex-direction: column; justify-content: center; }}
.muted, .cap {{ color: #6b7280; }} ol {{ margin: 6px 0 0 18px; padding: 0; }} li {{ margin: 2px 0; }}
table {{ border-collapse: collapse; width: 100%; }} td {{ border: 1px solid #e5e7eb; padding: 5px 8px; vertical-align: top; }}
</style></head><body>
<div class="cover"><h1>SkyGuard QA</h1><p style="font-size:15pt">User manual &middot; version {E(__version__)}</p>
<p class="muted">Every release, cleared for takeoff. This manual shows the pages you use every day, with a screenshot of each.
Everything runs on your office network; no internet is needed.</p></div>
<section><h2>1. Signing in</h2><p>Open the address in your welcome email in Microsoft Edge (for example
<b>https://skyguard-qa/</b>), enter your user name and the password from the email, and choose a new password.
Your name at the top right has <i>Change password</i> and <i>Sign out</i>.</p>
<table><tr><td><b>Administrator</b></td><td>Everything, plus Users, Network access, HTTPS and Exit.</td></tr>
<tr><td><b>Tester</b></td><td>Create, edit, run, record, schedules and settings.</td></tr>
<tr><td><b>Developer / Manager</b></td><td>View everything, run existing tests, view and export reports.</td></tr></table></section>
<section><h2>2. Finding your way</h2><p>The menu on the left groups the pages: Mission Control, Test Design, Automation
Studio, Performance Testing, Workflow Operator, Integration Comparison, Quality and Settings. On every testing page a bar at the
top offers <b>Generate AI result</b> (the On-Premise AI analyses the page's results), <b>Create TFS defect</b> (a defect
that goes to TFS after approval, with the person it is assigned to) and <b>Send email (PDF)</b> (the page's report as a
PDF through Outlook).</p></section>
{chapters}
<section><h2>{len(CHAPTERS) + 3}. Help</h2><p>Ask your SkyGuard QA administrator. Logs are in the data folder under
<i>logs</i>; Settings &gt; Browser Manager tests the browsers; DIAGNOSE_SITE.cmd checks a website.</p></section>
</body></html>"""
        src = work / "manual.html"
        src.write_text(doc, encoding="utf-8")
        p2 = b.new_page()
        p2.goto(src.as_uri(), wait_until="load")
        p2.pdf(path=str(out), format="A4", print_background=True,
               display_header_footer=True, header_template="<span></span>",
               footer_template="<div style='font-size:8pt;color:#6b7280;width:100%;text-align:center'>SkyGuard QA user manual "
                               f"{E(__version__)} &middot; page <span class='pageNumber'></span> of <span class='totalPages'></span></div>",
               margin={"top": "14mm", "bottom": "16mm", "left": "12mm", "right": "12mm"})
        b.close()
    server.should_exit = True
    th.join(10)
    print(f"{out} ({out.stat().st_size // 1024} KB, {len(CHAPTERS) + 4} chapters, {len(images)} screenshots)")


if __name__ == "__main__":
    main()
