"""Milestones 1-2 in a real browser against the running app.

Microphone, speech recognition and text-to-speech are mocked so the runs are deterministic:
* getUserMedia returns a generated audio stream; window.__say(ms) makes it "speak" (a tone above the speech threshold),
* /api/voice/transcribe answers with the next scripted sentence,
* speechSynthesis records what the agent says (window.__spoken) and ends after 30 ms.
A real microphone check stays a manual step.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from tests.auth_helper import sign_in
import pytest
from playwright.sync_api import sync_playwright, expect

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))

FAKE_MEDIA = r"""
(() => {
  const st = { gum: 0, perm: window.__PERM || "granted", deny: window.__DENY || false };
  window.__mic = st;
  const permListeners = [];
  window.__setPerm = (p) => { st.perm = p; permListeners.forEach((fn) => fn()); };
  const realQuery = navigator.permissions && navigator.permissions.query.bind(navigator.permissions);
  if (navigator.permissions) navigator.permissions.query = async (d) => {
    if (d && d.name === "microphone") {
      const obj = { get state() { return st.perm; }, addEventListener: (_e, fn) => permListeners.push(fn), removeEventListener() {} };
      return obj;
    }
    return realQuery(d);
  };
  let ctx = null, gain = null;
  navigator.mediaDevices.getUserMedia = async () => {
    st.gum += 1;
    if (st.perm === "denied" || st.deny) { const e = new Error("Permission denied"); e.name = "NotAllowedError"; throw e; }
    if (!ctx) {
      ctx = new (window.__RealAC || AudioContext)();
      const osc = ctx.createOscillator(); osc.frequency.value = 300;
      gain = ctx.createGain(); gain.gain.value = 0;
      const dest = ctx.createMediaStreamDestination();
      osc.connect(gain).connect(dest); osc.start();
      window.__dest = dest;
    }
    ctx.resume();
    const stream = window.__dest.stream.clone();
    window.__lastTrack = stream.getAudioTracks()[0];
    return stream;
  };
  window.__say = (ms = 600) => { gain.gain.value = 0.4; setTimeout(() => { gain.gain.value = 0; }, ms); };
  window.__spoken = [];
  class FakeUtterance { constructor(t) { this.text = t; } }
  window.SpeechSynthesisUtterance = FakeUtterance;
  const fakeVoice = { name: "Test Voice", lang: "en-US", localService: true };
  Object.defineProperty(window, "speechSynthesis", { value: {
    getVoices: () => [fakeVoice], cancel() {}, speak(u) { window.__spoken.push(u.text); setTimeout(() => u.onend && u.onend(), 30); },
    onvoiceschanged: null,
  } });
})();
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    home = tmp_path_factory.mktemp("qahome")
    port = free_port()
    (home / "settings.json").write_text(json.dumps({
        "server": {"host": "127.0.0.1", "port": port},
        "voice": {"agent_command_name": "Captain", "agent_display_name": "CAPTAIN", "reply_style": "plain", "stt_engine": "builtin"},
    }))
    env = {**os.environ, "QA_HOME": str(home), "PYTHONPATH": str(APP)}
    proc = subprocess.Popen([sys.executable, "-m", "qa_platform"], cwd=APP, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            if httpx.get(base + "/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    else:
        proc.kill()
        raise RuntimeError("server did not start")
    yield base
    proc.terminate()
    proc.wait(10)


@pytest.fixture(scope="module")
def pw():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def browser(pw):
    b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    yield b
    b.close()


class Harness:
    def __init__(self, page, base):
        self.page, self.base = page, base
        sign_in(page.context, base)  # 23.3.34
        self.queue: list[str] = []
        self.commands: list[str] = []
        self.errors: list[str] = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.route("**/api/voice/transcribe", self._transcribe)
        page.on("request", lambda r: self.commands.append(json.loads(r.post_data or "{}").get("text"))
                if r.url.endswith("/api/voice/commands") and r.method == "POST" else None)

    def _transcribe(self, route):
        text = self.queue.pop(0) if self.queue else ""
        if not text:
            route.fulfill(status=422, content_type="application/json",
                          body=json.dumps({"ok": False, "error_code": "VOICE_NOTHING_HEARD", "message": "No speech was recognized."}))
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "text": text}))

    def say(self, text, ms=500):
        """The user speaks one sentence: the fake mic carries sound for ms, the recognizer returns text."""
        self.queue.append(text)
        self.page.evaluate(f"window.__say({ms})")
        self.page.wait_for_timeout(ms + 1700)  # 1.2 s of silence ends the sentence, then transcription

    def spoken(self):
        return self.page.evaluate("window.__spoken")

    def state(self):
        return self.page.get_attribute("#voiceAgent", "data-state")

    def wait_state(self, st, timeout=8000):
        expect(self.page.locator("#voiceAgent")).to_have_attribute("data-state", st, timeout=timeout)


def open_page(browser, base, route="dashboard", init="", context=None, viewport=None):
    ctx = context or browser.new_context(viewport=viewport or {"width": 1400, "height": 900})
    ctx.add_init_script(init + FAKE_MEDIA)
    page = ctx.new_page()
    h = Harness(page, base)
    page.goto(f"{base}/#/{route}")
    return ctx, page, h


def put(base, **patch):
    r = httpx.put(base + "/api/voice/settings", json=patch)
    assert r.status_code == 200, r.text


@pytest.fixture(autouse=True)
def reset(server):
    put(server, wake_listening=True, phrase_style="conversational", address_as="Boss", agent_command_name="Captain")
    yield


def test_ac01_ac02_ac04_wake_listening_starts_by_itself(browser, server):
    ctx, page, h = open_page(browser, server)
    h.wait_state("standby")
    expect(page.locator("#vaLabel")).to_have_text('Say "Captain"')  # configured wake name, not hard-coded
    assert page.evaluate("window.__mic.gum") == 1
    assert page.locator("#voiceAgent.mic-live").count() == 1  # visible microphone indicator
    assert not h.errors
    ctx.close()


def test_ac03_ac05_ac06_ac08_wake_then_navigate_from_any_page(browser, server):
    ctx, page, h = open_page(browser, server, "reports")
    h.wait_state("standby")
    h.say("Captain")
    assert "Yes, Boss. I'm listening." in h.spoken()
    assert h.commands == []  # the bare name is answered here, not sent
    h.say("go to the test plans page")  # no name needed: follow-up window
    page.wait_for_function("location.hash === '#/test-plans'", timeout=8000)
    expect(page.locator("#pageTitle")).to_have_text("Test Plans")
    assert "Understood, Boss. I'm opening Test Plans now." in h.spoken()
    h.wait_state("standby")  # still listening after navigation
    assert page.evaluate("window.__mic.gum") == 1  # same microphone session, no restart
    h.say("Captain, open test suites")
    page.wait_for_function("location.hash === '#/test-suites'", timeout=8000)
    h.say("Captain go back")
    page.wait_for_function("location.hash === '#/test-plans'", timeout=8000)
    assert h.commands == ["go to the test plans page", "Captain, open test suites", "Captain go back"]
    assert not h.errors
    ctx.close()


def test_unaddressed_speech_is_ignored(browser, server):
    ctx, page, h = open_page(browser, server)
    h.wait_state("standby")
    h.say("open test cases")
    page.wait_for_timeout(500)
    assert h.commands == [] and page.evaluate("location.hash") == "#/dashboard"
    ctx.close()


def test_ac26_ac29_duplicates_and_own_voice_are_not_commands(browser, server):
    ctx, page, h = open_page(browser, server)
    h.wait_state("standby")
    h.say("Captain open test data")
    page.wait_for_function("location.hash === '#/test-data'", timeout=8000)
    h.say("Captain open test data")  # the recognizer delivers the same sentence again
    h.say("Understood, Boss. I'm opening Test Data now.")  # the speakers picked up the agent's own reply
    assert h.commands == ["Captain open test data"]
    ctx.close()


def test_ac07_ac19_failed_navigation_is_reported_not_claimed(browser, server):
    ctx, page, h = open_page(browser, server)
    h.wait_state("standby")
    page.route("**/api/voice/commands", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({
        "ok": True, "command_id": 1, "status": "completed", "say": "I'm opening Ghost Page now.", "speak": "Understood, Boss. I'm opening Ghost Page now.",
        "navigate": "#/ghost-page", "verify": {"route": "ghost-page", "label": "Ghost Page"}, "context": {}})))
    h.say("Captain open the ghost page")
    page.wait_for_timeout(800)
    spoken = h.spoken()
    assert "Understood, Boss. I'm opening Ghost Page now." in spoken
    assert any(s.startswith("Sorry, Boss. Ghost Page did not open.") for s in spoken), spoken
    ctx.close()


def test_ac27_permission_denied_is_explained_and_not_retried(browser, server):
    ctx, page, h = open_page(browser, server, init="window.__PERM='denied';")
    h.wait_state("blocked")
    expect(page.locator("#vaLabel")).to_have_text("Microphone blocked")
    page.wait_for_timeout(3000)
    assert page.evaluate("window.__mic.gum") == 0  # never forced
    page.click("#vaOrb")
    expect(page.locator("#vaHint")).to_contain_text("Allow the microphone")
    # the user allows it in the browser: listening starts without a reload
    page.evaluate("window.__setPerm('granted')")
    h.wait_state("standby")
    ctx.close()


def test_prompt_refused_reports_blocked(browser, server):
    ctx, page, h = open_page(browser, server, init="window.__PERM='prompt'; window.__DENY=true;")
    h.wait_state("blocked")
    assert page.evaluate("window.__mic.gum") == 1  # asked once, then stopped
    page.wait_for_timeout(2500)
    assert page.evaluate("window.__mic.gum") == 1
    ctx.close()


HELD_AUDIO = r"""
window.__RealAC = window.AudioContext;
let __unlocked = false;
addEventListener("pointerdown", () => { __unlocked = true; }, true);
// As in a normal browser window before the first click: new audio contexts stay suspended.
window.AudioContext = class extends window.__RealAC {
  get state() { return __unlocked ? super.state : "suspended"; }
  resume() { return __unlocked ? super.resume() : new Promise(() => {}); }
};
"""


def test_audio_held_until_first_click(browser, server):
    ctx, page, h = open_page(browser, server, init=HELD_AUDIO)
    h.wait_state("attention")
    expect(page.locator("#vaLabel")).to_have_text("Click to start voice")  # honest: not "listening"
    page.mouse.click(600, 400)
    h.wait_state("standby")
    h.say("Captain open test cases")
    page.wait_for_function("location.hash === '#/test-cases'", timeout=8000)
    ctx.close()


def test_mute_stop_and_persistence_across_refresh(browser, server):
    ctx, page, h = open_page(browser, server)
    h.wait_state("standby")
    page.click("#vaOrb")
    page.click("#vaMute")
    h.wait_state("muted")
    expect(page.locator("#voiceAgent.mic-live")).to_have_count(0)  # microphone released (asynchronously)
    page.reload()
    h.wait_state("muted")  # mute lasts for this browser session
    page.click("#vaOrb"); page.click("#vaMute")
    h.wait_state("standby")
    page.click("#vaWake")  # Stop listening (saved)
    h.wait_state("idle")
    expect(page.locator("#vaLabel")).to_have_text("Wake listening off")
    assert httpx.get(server + "/api/voice/settings").json()["wake_listening"] is False
    page.reload()
    h.wait_state("idle")  # survives refresh and restart (stored in settings)
    page.wait_for_timeout(800)
    gum = page.evaluate("window.__mic.gum")
    assert gum == 0
    page.click("#vaOrb"); page.click("#vaWake")
    h.wait_state("standby")
    ctx.close()


def test_one_tab_listens_at_a_time(browser, server):
    ctx = browser.new_context()
    ctx, p1, h1 = open_page(browser, server, context=ctx)
    h1.wait_state("standby")
    p2 = ctx.new_page()
    h2 = Harness(p2, server)
    p2.goto(f"{server}/#/dashboard")
    h2.wait_state("attention")
    expect(p2.locator("#vaLabel")).to_have_text("Listening in another tab")
    assert p2.evaluate("window.__mic.gum") == 0
    p1.close()
    h2.wait_state("standby", timeout=10000)
    ctx.close()


def test_floating_assistant_on_every_page_and_typed_command(browser, server):
    ctx, page, h = open_page(browser, server)
    h.wait_state("standby")
    routes = page.evaluate("[...document.querySelectorAll('.nav-link')].map(a => a.dataset.route)")
    assert len(routes) > 30
    for r in routes:
        page.evaluate(f"location.hash = '#/{r}'")
        page.wait_for_timeout(150)
        assert page.locator("#voiceAgent .va-orb").is_visible(), r
    assert page.evaluate("window.__mic.gum") == 1
    page.evaluate("location.hash = '#/dashboard'")
    page.click("#vaOrb")
    page.fill("#vaTyped input", "open analytics")
    page.press("#vaTyped input", "Enter")
    page.wait_for_function("location.hash === '#/analytics'", timeout=8000)
    assert not h.errors, h.errors
    ctx.close()


def test_mobile_layout(browser, server):
    ctx, page, h = open_page(browser, server, viewport={"width": 390, "height": 800})
    h.wait_state("standby")
    page.click("#vaOrb")
    box = page.locator("#vaPanel").bounding_box()
    assert box["x"] >= 15 and box["x"] + box["width"] <= 390 - 15
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    ctx.close()


def test_ac21_command_center_push_to_talk_and_settings_still_work(browser, server):
    put(server, wake_listening=False)
    ctx, page, h = open_page(browser, server, "command-center")
    expect(page.locator("#ccMic")).to_be_visible()
    h.wait_state("idle")
    h.queue.append("open reports")  # push-to-talk needs no name
    page.click("#ccMic")
    page.wait_for_timeout(300)
    page.evaluate("window.__say(500)")
    page.wait_for_function("location.hash === '#/reports'", timeout=10000)
    page.evaluate("location.hash = '#/voice'")
    page.wait_for_selector("#vset")
    assert page.is_checked("[name=wake_listening]") is False
    page.check("[name=wake_listening]")
    page.fill("[name=address_as]", "Chief")
    page.click("#vset button[type=submit]")
    h.wait_state("standby")
    s = httpx.get(server + "/api/voice/settings").json()
    assert s["wake_listening"] is True and s["address_as"] == "Chief"
    h.say("Captain")
    assert "Yes, Chief. I'm listening." in h.spoken()
    assert not h.errors, h.errors
    ctx.close()


def test_microphone_dropout_recovers(browser, server):
    ctx, page, h = open_page(browser, server)
    h.wait_state("standby")
    page.evaluate("window.__say(400)")  # a sentence is being captured when the device drops out
    page.wait_for_timeout(150)
    page.evaluate("window.__lastTrack && window.__lastTrack.dispatchEvent(new Event('ended'))")
    h.wait_state("standby", timeout=10000)
    assert page.evaluate("window.__mic.gum") == 2  # reopened once after the drop-out
    ctx.close()
