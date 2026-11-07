"""23.3.35: Play opens tests in the browser chosen under Settings > Preferences, not the one a script was recorded in."""

from __future__ import annotations

import pytest

from qa_platform.automation import runner as runner_mod
from qa_platform.automation.dsl import Script, Step
from qa_platform.automation.store import new_script_id
from qa_platform.errors import QAError


@pytest.fixture()
def opened(monkeypatch):
    seen: list[str] = []

    def fake_launch(pw, browser="chromium", headless=False):
        seen.append(browser)
        raise QAError("BROWSER_LAUNCH_FAILED", "test stops here", status=503)

    monkeypatch.setattr(runner_mod, "launch", fake_launch)
    monkeypatch.setattr(runner_mod, "start_playwright", lambda: type("PW", (), {"stop": lambda self: None})())
    return seen


def _script(rt, browser):
    return rt.automation.scripts.save(Script(id=new_script_id(), name="Open", mode="guided", base_url="http://x.test",
                                             browser=browser, steps=[Step(action="goto", value="http://x.test")]))


def test_play_uses_the_preferences_browser(rt, opened):
    script = _script(rt, "chromium")  # recorded before Edge became the default
    rt.settings.update({"playback": {"browser": "msedge"}})
    rt.automation.run_script(script.id, headless=True, background=False)
    assert opened == ["msedge"]


def test_a_run_can_still_ask_for_another_browser(rt, opened):
    script = _script(rt, "msedge")
    rt.settings.update({"playback": {"browser": "msedge"}})
    rt.automation.run_script(script.id, headless=True, background=False, browser="chrome")  # schedules, voice
    assert opened == ["chrome"]


def test_without_a_preference_the_script_browser_is_kept(rt, opened):
    script = _script(rt, "chrome")
    rt.automation.run_script(script.id, headless=True, background=False)
    assert opened == ["chrome"]
