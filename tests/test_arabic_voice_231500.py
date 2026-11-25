"""23.15.0: Arabic that the recognizer misheard, and English that it misspelled, still open the right page.

* "إفتاء الإشارات" (open notifications, as speech recognition writes it) opened nothing and answered
  "this operation is not supported"; a word within a letter or two of a known command word is now understood
* a page said with a small slip ("dashbord", "execution histry", "notification") is matched to the page
* when the local AI's English misses the page, the built-in word list is tried before answering "not supported"
* a command spoken in Arabic is answered in Arabic
"""

from __future__ import annotations

import pytest

from qa_platform.voice.intents import match_page, near_page, parse
from skyguard_agent.translate import by_words, fuzzy_word


@pytest.mark.parametrize("spoken,english", [
    ("إفتاء الإشارات", "open notifications"),          # the words the user reported
    ("افتح الإشعارات", "open notifications"),
    ("إفتح لوحة التحكم", "open dashboard"),
    ("افتح الاعدادات", "open settings"),
    ("افتح سجل التنفيذ", "open history"),
])
def test_misheard_arabic_still_says_the_page(spoken, english):
    out = by_words(spoken).lower()
    for word in english.split():
        assert word in out, out
    assert parse(out).command == "open_page", out


@pytest.mark.parametrize("word", ["إفتاء", "افتاح", "إفتاحة"])
def test_a_word_within_a_letter_or_two_is_open(word):
    assert fuzzy_word(word) == "open"


def test_a_word_that_is_not_close_to_anything_is_left_alone():
    assert fuzzy_word("مرحبا") is None
    assert fuzzy_word("ab") is None


@pytest.mark.parametrize("phrase,route", [
    ("open the dashbord", "dashboard"),
    ("open notification", "notifications"),
    ("open execution histry", "history"),
])
def test_a_page_said_with_a_small_slip_is_matched(phrase, route):
    assert (match_page(phrase) or near_page(phrase)) == route


def test_a_phrase_that_names_no_page_matches_nothing():
    assert near_page("make me a coffee") is None


def test_the_word_list_is_tried_when_the_models_english_misses_the_page():
    """The local model sometimes translates the words but drops the page ("الإشارات" -> "the signals")."""
    assert parse("open the signals") is None                           # the model's English opens nothing
    assert parse(by_words("إفتاء الإشارات")).command == "open_page"     # the word list does
