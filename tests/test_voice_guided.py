"""Milestone 7: Guided Browser Automation by voice. A real recording session (Playwright, Chromium) on a small local
shop site (tests/site): async page loads, a login with saved credentials, links, dropdowns, a checkbox, radio buttons,
values the agent asks for, and a check of the confirmation message. The recorded script is checked afterwards."""

import functools
import http.server
import json
import os
import sys
import threading
from pathlib import Path

import pytest

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(APP))
sys.path.insert(0, str(Path(__file__).parent))

from test_voice_plans import Talk  # noqa: E402

PASSWORD = "S3cret!pw-9"


@pytest.fixture(scope="module")
def site():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(Path(__file__).parent / "site"))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


class MemorySecrets:
    available = True

    def __init__(self):
        self.d = {}

    def set(self, ns, key, value):
        self.d[(ns, key)] = value

    def get(self, ns, key):
        return self.d.get((ns, key))

    def has(self, ns, key):
        return (ns, key) in self.d

    def delete(self, ns, key):
        self.d.pop((ns, key), None)


@pytest.fixture()
def env(tmp_path, monkeypatch, site):
    monkeypatch.setenv("QA_HOME", str(tmp_path))
    from fastapi.testclient import TestClient
    from qa_platform.main import create_app
    app = create_app()
    with TestClient(app) as c:
        rt = app.state.runtime
        rt.automation.creds.secrets = MemorySecrets()
        rt.automation.discovery.list = lambda limit=20: [{"base_url": site + "/login.html"}]
        c.put("/api/voice/settings", json={"agent_command_name": "Captain", "phrase_style": "conversational", "address_as": "Boss"})
        yield c, rt, site, tmp_path
        for rid, r in list(rt.automation.recordings.items()):
            if r["session"].active:
                r["session"].stop()


def say_ok(t, text, status="completed"):
    r = t.say(text)
    assert r["status"] == status, (text, r)
    return r


def test_ac15_voice_operates_guided_browser_and_records(env):
    c, rt, site, home = env
    rt.automation.creds.save("Shop", "user", "tester", PASSWORD, site + "/login.html")
    t = Talk(c, "conv-guided-0001")
    r = say_ok(t, "Captain, launch my application", "accepted")
    assert r["say"] == f"I'm opening your application at {site}/login.html, taken from your latest application discovery."
    assert r["navigate"].startswith("#/guided-automation?recording=")
    status, line = t.final(r, timeout=60)                                  # verified: the browser shows the page
    assert status == "completed" and line.startswith("Your application is open at 127.0.0.1:") and "Recording is on" in line

    r = say_ok(t, "enter the username")
    assert r["say"].startswith("Entered the user tester username from the saved test credentials in Username")
    r = say_ok(t, "Enter the password from the configured test credentials.")
    assert "password from the saved test credentials" in r["say"] and PASSWORD not in r["say"]
    r = say_ok(t, "Click Login.")
    assert r["say"] == "Clicked Login. The page is now Shop Home."          # the page transition is checked
    r = say_ok(t, "Open the Products page.")
    assert r["say"] == "Clicked Products. The page is now Products."
    r = say_ok(t, "Select Electronics.")                                    # the form appears after a delay
    assert r["say"] == "Selected Electronics in Category."
    r = say_ok(t, "Enter the product name.", "needs_input")
    assert r["say"] == "What should I enter in Product name?"
    r = say_ok(t, "Smart TV 55")
    assert r["say"] == "Entered Smart TV 55 in Product name."               # capitals as said, value read back
    r = say_ok(t, "Choose the country.", "needs_input")
    assert r["say"] == "Which Country? The options are Canada, United States."
    r = say_ok(t, "United States")
    assert r["say"] == "Selected United States in Country."
    assert say_ok(t, "set the zip code to 90210")["say"] == "Entered 90210 in ZIP code."
    assert say_ok(t, "check gift wrap")["say"] == "Checked gift wrap."
    assert say_ok(t, "select the express shipping option")["say"] == "Selected express shipping."
    say_ok(t, "Click Save.")
    r = say_ok(t, "Verify the confirmation message.")                       # waits for the asynchronous message
    assert r["say"] == "Verified: the confirmation message says Product Smart TV 55 saved. It is recorded as a check."
    r = say_ok(t, "verify the page shows Order shipped", "failed")
    assert "does not show Order shipped" in r["say"]
    assert say_ok(t, "where am i")["say"] == f"You're on Products, at {site}/products.html."

    r = say_ok(t, "stop recording")
    sid = r["navigate"].split("script=")[1]
    script = rt.automation.scripts.get(sid)
    steps = [(s.action, s.value) for s in script.steps]
    assert ("fill", "{{CRED:user.username}}") in steps and ("fill", "{{CRED:user.password}}") in steps
    assert ("select", "Electronics") in steps or any(a == "select" and v and "Electronics" in v for a, v in steps)
    assert ("fill", "Smart TV 55") in steps and ("fill", "90210") in steps
    assert any(a == "check" for a, v in steps) and any(a == "expect_visible" for a, v in steps)
    saved = json.dumps([s.model_dump() for s in script.steps])
    assert PASSWORD not in saved and "tester" not in saved                 # credentials are rules, never text
    replies = json.dumps(c.get("/api/voice/commands?conversation_id=conv-guided-0001").json())
    assert PASSWORD not in replies


def test_missing_values_and_spoken_passwords(env):
    c, rt, site, home = env
    t = Talk(c, "conv-guided-0002")
    t.final(say_ok(t, "launch my application", "accepted"), timeout=60)
    r = say_ok(t, "enter the username", "needs_input")                     # no saved credentials: ask
    assert "What username should I enter?" in r["say"]
    assert say_ok(t, "demo user")["say"] == "Entered demo user in Username."
    r = say_ok(t, "enter my password secret123", "failed")                 # a password said aloud is never typed
    assert "I don't take passwords by voice" in r["say"] and "secret123" not in r["say"]
    r = say_ok(t, "click checkout", "needs_confirmation")                  # checking out has consequences: asked first
    say_ok(t, "cancel")
    r = say_ok(t, "click wishlist", "failed")
    assert "could not find wishlist" in r["say"].lower()
    say_ok(t, "stop recording")
    r = t.say("click save")  # no guided session any more: nothing is clicked anywhere
    assert r["status"] == "failed" and "launch my application" in r["say"]


def test_ac16_third_party_app_registration_with_confirmations(env):
    c, rt, site, home = env
    rt.automation.creds.save("E-Commerce Shop", "user", "tester", PASSWORD, site + "/home.html")
    rt.automation.creds.save("HR Portal", "user", "hr", None, None)
    t = Talk(c, "conv-guided-0003")
    r = say_ok(t, "Captain, open my banking application", "failed")       # never an arbitrary or unknown app
    assert "I don't have an application called banking configured" in r["say"]
    assert "E-Commerce Shop and HR Portal" in r["say"] and not rt.automation.recordings
    r = say_ok(t, "open my HR application", "failed")
    assert "no login address" in r["say"]
    r = say_ok(t, "Captain, open my e-commerce application.", "accepted")
    assert r["say"] == (f"I'm opening your configured E-Commerce Shop test application at {site}/home.html, taken "
                        "from the E-Commerce Shop entry in Application Credentials.")
    status, line = t.final(r, timeout=60)
    assert status == "completed" and line.startswith("E-Commerce Shop is open at 127.0.0.1:")

    assert say_ok(t, "Go to the registration page.")["say"] == "Clicked Registration. The page is now Register."
    r = say_ok(t, "Submit the form.", "needs_confirmation")                  # an external submission: asked first
    assert "submit this form in E-Commerce Shop" in r["say"]
    r = say_ok(t, "confirm")
    assert r["say"].startswith("I clicked Submit, but the form was not accepted: Customer name")
    assert say_ok(t, "Enter the customer name as John Smith.")["say"] == "Entered John Smith in Customer name."
    assert say_ok(t, "Select United Arab Emirates.")["say"] == "Selected United Arab Emirates in Country."
    assert say_ok(t, "Enter ZIP code 00000.")["say"] == "Entered 00000 in ZIP code."
    assert say_ok(t, "Choose standard delivery.")["say"] == "Selected standard delivery."
    say_ok(t, "submit the form", "needs_confirmation")
    r = say_ok(t, "confirm")
    assert r["say"] == "Submitted with Submit. The page shows: Registration complete for John Smith."

    say_ok(t, "click delete account", "needs_confirmation")                  # a deletion: asked first
    say_ok(t, "cancel")
    say_ok(t, "verify the page shows Account deleted", "failed")            # nothing was clicked
    say_ok(t, "click delete account", "needs_confirmation")
    r = say_ok(t, "confirm")                                                 # the page's own confirm() is answered OK
    assert r["say"] == "Clicked Delete account. The page asked: Delete this account? I answered OK, as you confirmed."
    assert say_ok(t, "verify the page shows Account deleted")["say"].startswith("Verified: the page shows Account deleted.")

    say_ok(t, "click show terms")                                            # a modal dialog
    r = say_ok(t, "what is on this page")
    assert r["say"].startswith("This is Register, headed Create your account.") and "A dialog is open on top." in r["say"]
    assert say_ok(t, "click I agree")["say"] == "Clicked I agree."          # the button inside the dialog
    assert say_ok(t, "verify the page shows Terms accepted")["status"] == "completed"
    say_ok(t, "click show terms")
    assert say_ok(t, "close the dialog")["say"] == "Closed the dialog."
    r = say_ok(t, "click help")                                              # other tabs are not driven
    assert "That opened a new tab. I only work in the guided session's own page" in r["say"]
    assert say_ok(t, "where am i")["say"].startswith("You're on Register")

    c.put("/api/voice/settings", json={"approved_test_apps": ["E-Commerce Shop"]})  # an approved test workflow
    r = say_ok(t, "submit the form")
    assert r["say"].startswith("Submitted with Submit.")
    say_ok(t, "stop recording")
