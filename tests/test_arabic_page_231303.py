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
from skyguard_agent.translate import ARABIC_LINES, LINE_KINDS, spoken_reply, to_english

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
    assert 'r.type === "clarify" && r.options?.length' in page and 'send(o, "typed", log, null, false, spoken' in page
    panel = (ROOT / "browser-extension" / "extension" / "sidepanel.js").read_text(encoding="utf-8")
    assert 'data-act="say"' in panel and 'if (act === "say")' in panel


# ---- the question and what follows, in the language the person spoke (23.13.3, after the user's answer) ----------

def test_the_page_question_is_asked_in_arabic_without_the_local_ai():
    r = spoken_reply({"type": "clarify", "text": "Which page?", "options": ["Open the Book flights page"]}, True)
    assert r == {"speak": ARABIC_LINES["choose"], "language": "ar"}
    assert spoken_reply({"type": "clarify", "text": "Which page?", "options": ["x"]}, False) == {
        "speak": "Which page?", "language": "en"}


def test_every_task_step_has_an_arabic_line():
    assert set(LINE_KINDS) <= set(ARABIC_LINES)
    assert spoken_reply({"type": "completed", "text": "1 trip created."}, True)["speak"] == ARABIC_LINES["completed"]


def test_task_steps_and_button_answers_are_spoken_in_the_request_language(client):
    for lang, check in (("en", lambda t: t == "The preview is ready."), ("ar", lambda t: t == ARABIC_LINES["preview"])):
        r = client.post("/api/ops/simple/say", json={"kind": "preview", "text": "The preview is ready.", "language": lang})
        assert r.status_code == 200, r.text
        assert r.json()["language"] == lang and check(r.json()["speak"])
    assert client.post("/api/ops/simple/say", json={"kind": "run_anything", "text": "x"}).status_code == 422
    # a button under a spoken Arabic question: typed English text, answered in Arabic
    r = client.post("/api/ops/simple/command", json={"text": "open the start page", "reply_language": "ar"})
    assert r.status_code == 200, r.text
    assert r.json()["reply"]["language"] == "ar"


def test_page_and_extension_speak_task_steps_and_show_arabic():
    page = (ROOT / "web" / "js" / "pages" / "ai-agent.js").read_text(encoding="utf-8")
    for needle in ('runLine(runId, "preview"', 'runLine(runId, r.status', '"/api/ops/simple/say"', 'dir="rtl" lang="ar"',
                   "reply_language: speakAs"):
        assert needle in page, needle
    panel = (ROOT / "browser-extension" / "extension" / "sidepanel.js").read_text(encoding="utf-8")
    for needle in ('runLine(r.id, "preview"', "runLine(r.id, r.status", "api.lineToSpeak", 'dir="rtl" lang="ar"',
                   "reply_language: replyLang"):
        assert needle in panel, needle
    server = (ROOT / "browser-extension" / "companion" / "skyguard_companion" / "server.py").read_text(encoding="utf-8")
    assert '"/v1/voice/line"' in server and 'b.get("reply_language") == "ar"' in server


# ---- websites whose addresses change on every visit (23.13.3) -----------------------------------------------------

from skyguard_agent.understanding import path_template, rebase_url  # noqa: E402


def test_changing_addresses_are_the_same_page():
    assert path_template("https://abc.com/12345") == path_template("https://abc.com/by123") == "/{id}"
    assert path_template("https://abc.com/(S(x1y2z3))/home.aspx") == path_template("https://abc.com/(S(q9))/home.aspx")
    assert path_template("https://abc.com/s/AbCdEfGhIjKlMnOpQr/booking") == "/s/{id}/booking"
    assert path_template("https://abc.com/app;jsessionid=AB12/orders") == "/app/orders"
    assert path_template("https://abc.com/ae/english/book") == "/ae/english/book"  # ordinary words stay


def test_a_learned_page_opens_with_the_changing_part_of_today():
    assert rebase_url("https://abc.com/12345/booking", "https://abc.com/by123/home") == "https://abc.com/by123/booking"
    assert rebase_url("https://abc.com/12345", "https://abc.com/by123") == "https://abc.com/by123"
    assert rebase_url("https://abc.com/{id}/booking", None) is None          # cannot be rebuilt: the start page
    assert rebase_url("https://abc.com/ae/book?x=1", "https://abc.com/by9") == "https://abc.com/ae/book?x=1"
    assert rebase_url("https://abc.com/12345/b", "https://other.com/by123/a") == "https://abc.com/12345/b"


def test_connecting_the_new_address_finds_the_learned_application(client):
    first = client.post("/api/ops/simple/connect", json={"url": "https://www.abc-shop.example/12345", "learn": False})
    assert first.status_code == 201, first.text
    for again in ("https://www.abc-shop.example/by123", "https://abc-shop.example/by123/home"):
        r = client.post("/api/ops/simple/connect", json={"url": again, "learn": False})
        assert r.status_code == 201 and r.json()["created"] is False, r.text
        assert r.json()["application"]["id"] == first.json()["application"]["id"]


def test_tasks_and_the_extension_use_the_address_of_today():
    executor = (ROOT / "skyguard_agent" / "executor.py").read_text(encoding="utf-8")
    assert "rebase_url(url, here) or app[\"base_url\"]" in executor
    api = (ROOT / "qa_platform" / "api" / "agent_simple.py").read_text(encoding="utf-8")
    assert "rebase_url(reply[\"url\"], svc.current_url(" in api
    bg = (ROOT / "browser-extension" / "extension" / "background.js").read_text(encoding="utf-8")
    assert "let url = rebaseUrl(msg.url, same ? same.url : \"\");" in bg
