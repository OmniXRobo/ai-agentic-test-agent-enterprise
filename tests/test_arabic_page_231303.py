"""23.13.3: a spoken Arabic "open page" on the AI Agent page. "افتح صفحة" heard as "إفتاحة صفحة" is translated literally
("open page"), never into a page that was not said ("Open the booking page"); the question "Which page do you mean?"
names the learned pages as a person says them (no addresses, no "| Emirates" title tails, no duplicates) and offers them
as buttons on the page and in the extension."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from skyguard_agent.autopilot import Autopilot, page_names
from skyguard_agent.intent import classify
from skyguard_agent.translate import spoken_reply, to_english

ROOT = Path(__file__).resolve().parent.parent
EMIRATES = {"id": "app1", "name": "Emirates (Test)", "base_url": "https://www.emirates.example/ae/english/"}
SCREENS = [  # as learned on the reported site
    {"name": "Book flights", "path": "/ae/english/book", "url": "https://www.emirates.example/ae/english/book"},
    {"name": "Book Flights | Find Hotels and Car Rental | Emirates", "path": "/ae/english",
     "url": "https://www.emirates.example/ae/english"},
    {"name": "/ae/english/book", "path": "/ae/english/book/"},
    {"name": "/ae/english", "path": "/ae/english/"},
    {"name": "Sign in", "type": "sign-in"},
    {"name": "Manage booking", "path": "/ae/english/manage-booking"},
]


class InventingLLM:
    """A local model that adds what was not said (the reported "Open the booking page")."""

    calls = 0

    def chat(self, messages, max_tokens=0):
        InventingLLM.calls += 1
        return "Open the booking page"


@pytest.mark.parametrize("heard", ["إفتاحة صفحة", "افتاحة صفحة", "افتح صفحة", "افتا صفا", "إفتاحه صفحه"])
def test_spoken_open_page_is_translated_literally(heard):
    llm = InventingLLM()
    before = InventingLLM.calls
    assert to_english(heard, llm) == ("open page", True)
    assert InventingLLM.calls == before  # a fully understood short command does not go to the model


def test_longer_arabic_still_goes_to_the_local_ai_with_a_literal_prompt():
    seen = {}

    class LLM:
        def chat(self, messages, max_tokens=0):
            seen["prompt"] = messages[0]["content"]
            return "book a flight from Dubai to Karachi tomorrow for two adults"

    out, translated = to_english("ابغى احجز رحلة من دبي الى كراتشي بكرة لشخصين بالغين", LLM())
    assert translated and out.startswith("book a flight")
    assert "Never add a page name" in seen["prompt"] and "booking page" not in seen["prompt"]


def test_page_names_are_what_a_person_would_say():
    assert page_names(SCREENS, EMIRATES) == ["Book flights", "Manage booking"]


def test_open_page_asks_a_clean_question_with_buttons():
    fake = SimpleNamespace(_screens=lambda app_id: SCREENS, _workflows=lambda app_id: [])
    r = Autopilot._navigate(fake, EMIRATES, classify("open page"))
    assert r["type"] == "clarify"
    assert r["text"] == "Which page of Emirates (Test) do you mean? I know: Book flights, Manage booking."
    assert "/ae/" not in r["text"] and "|" not in r["text"]
    assert r["options"] == ["Open the Book flights page", "Open the Manage booking page", "Open the start page"]
    # every button is a command that opens its page
    for option in r["options"]:
        assert Autopilot._navigate(fake, EMIRATES, classify(option))["type"] == "navigate", option


def test_the_question_is_spoken_in_arabic_for_an_arabic_request():
    r = spoken_reply({"type": "clarify", "text": "Which page of Emirates (Test) do you mean? I know: Book flights."}, True)
    assert r["language"] == "ar" and r["speak"]


def test_page_and_extension_show_the_choices_as_buttons():
    page = (ROOT / "web" / "js" / "pages" / "ai-agent.js").read_text(encoding="utf-8")
    assert 'r.type === "clarify" && r.options?.length' in page and 'send(o, "typed", log, null)' in page
    panel = (ROOT / "browser-extension" / "extension" / "sidepanel.js").read_text(encoding="utf-8")
    assert 'data-act="say"' in panel and 'if (act === "say")' in panel
