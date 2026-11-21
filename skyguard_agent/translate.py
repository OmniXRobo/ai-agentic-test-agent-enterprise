"""23.13.0: Arabic (Modern Standard and UAE / Gulf Arabic) commands to English, offline.

The AI Agent and the voice assistant plan in English. A command typed or spoken in Arabic is translated first by the
local model (the On-Premise Qwen model through SkyGuard's AI gateway; nothing leaves the PC). When the model is not
running, a built-in word list translates the common command words (open, log in, book, show, from, to, cities, roles,
new window ...) so that simple commands still work. Names, numbers and addresses are kept as they are.

Standard library only (the browser extension's companion uses it too).
"""

from __future__ import annotations

import re
from typing import Any

ARABIC = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_DIACRITICS = re.compile(r"[ً-ْٰـ]")  # harakat and tatweel

# Longest phrases first. UAE / Gulf words are included (ورني = show me, سوّ / سوي = make, باكر / بكرة = tomorrow,
# شو = what, وين = where, ابا / ابغى = I want).
PHRASES: list[tuple[str, str]] = [
    ("سجل الدخول", "log in"), ("سجل دخول", "log in"), ("تسجيل الدخول", "log in"), ("ادخل الى", "log in to"),
    ("سجل الخروج", "log out"), ("تسجيل الخروج", "log out"),
    ("نافذة جديدة", "new window"), ("نافذه جديده", "new window"), ("متصفح جديد", "new browser"),
    ("ابوظبي", "Abu Dhabi"), ("أبوظبي", "Abu Dhabi"), ("ابو ظبي", "Abu Dhabi"), ("أبو ظبي", "Abu Dhabi"),
    ("رأس الخيمة", "Ras Al Khaimah"), ("راس الخيمة", "Ras Al Khaimah"),
    ("كلمة المرور", "password"), ("كلمة السر", "password"), ("اسم المستخدم", "user name"),
    ("اذهب الى", "go to"), ("اذهب إلى", "go to"), ("روح الى", "go to"), ("روح على", "go to"), ("انتقل الى", "navigate to"),
    ("انتقل إلى", "navigate to"), ("خذني الى", "take me to"),
    ("صفحة الحجز", "booking page"), ("الصفحة الرئيسية", "home page"),
    ("ذهاب وعودة", "round trip"), ("ذهاب فقط", "one way"),
    ("بعد بكرة", "the day after tomorrow"), ("بعد باكر", "the day after tomorrow"),
]
WORDS: dict[str, str] = {
    "افتح": "open", "فتح": "open", "تصفح": "browse", "اعرض": "show", "عرض": "show", "ورني": "show me", "وريني": "show me",
    "اظهر": "show", "ابحث": "search", "دور": "search", "احجز": "book", "حجز": "book", "سوي": "create", "سو": "create",
    "انشئ": "create", "أنشئ": "create", "اضف": "add", "أضف": "add", "عدل": "edit", "احذف": "delete", "امسح": "delete",
    "الغ": "cancel", "الغي": "cancel", "إلغاء": "cancel", "الغاء": "cancel", "وافق": "approve", "اعتمد": "approve",
    "ارسل": "send", "أرسل": "send", "شغل": "run", "نفذ": "run", "اختبر": "test", "اكمل": "continue", "كمل": "continue",
    "توقف": "stop", "وقف": "stop", "انتظر": "wait", "اختر": "choose", "اكتب": "type", "اضغط": "click", "دوس": "click",
    "رحلة": "flight", "رحلات": "flights", "طيران": "flight", "تذكرة": "ticket", "تذاكر": "tickets", "فندق": "hotel",
    "سيارة": "car", "صفحة": "page", "الصفحة": "the page", "الموقع": "the website", "موقع": "website",
    "القائمة": "menu", "قائمة": "menu", "التطبيق": "the application", "النظام": "the system", "الحساب": "the account",
    "حساب": "account", "مستخدم": "user", "المستخدم": "the user", "بيانات": "data",
    "المدير": "manager", "مدير": "manager", "الادمن": "admin", "ادمن": "admin", "المسؤول": "admin", "مسؤول": "admin",
    "العميل": "client", "عميل": "client", "الزبون": "customer", "زبون": "customer", "الموظف": "employee",
    "موظف": "employee", "السائق": "driver", "سائق": "driver",
    "من": "from", "الى": "to", "إلى": "to", "لـ": "to", "على": "on", "في": "in", "مع": "with", "و": "and",
    "بصفة": "as", "باسم": "as", "كـ": "as", "ك": "as", "اليوم": "today", "بكرة": "tomorrow", "باكر": "tomorrow",
    "غدا": "tomorrow", "غداً": "tomorrow", "امس": "yesterday", "أمس": "yesterday", "الآن": "now", "الحين": "now",
    "الحين،": "now", "هذا": "this", "هذه": "this", "هذي": "this", "كل": "all", "جميع": "all", "جديد": "new",
    "جديدة": "new", "شو": "what", "ماذا": "what", "وين": "where", "أين": "where", "كم": "how many", "ابا": "I want",
    "ابغى": "I want", "أريد": "I want", "اريد": "I want", "لو سمحت": "please", "رجاء": "please", "من فضلك": "please",
    "دبي": "Dubai", "الشارقة": "Sharjah", "عجمان": "Ajman", "العين": "Al Ain", "الفجيرة": "Fujairah",
    "كراتشي": "Karachi", "لاهور": "Lahore", "اسلام اباد": "Islamabad", "الرياض": "Riyadh", "جدة": "Jeddah",
    "الدوحة": "Doha", "مسقط": "Muscat", "القاهرة": "Cairo", "لندن": "London", "بومباي": "Mumbai", "مومباي": "Mumbai",
    "دلهي": "Delhi", "مانيلا": "Manila",
    "واحد": "one", "اثنين": "two", "ثلاثة": "three", "اربعة": "four", "خمسة": "five", "ستة": "six", "سبعة": "seven",
    "ثمانية": "eight", "تسعة": "nine", "عشرة": "ten",
}
# Speech recognition often mishears the last letter or a vowel in Gulf speech ("افتح" heard as "افتا", "صفحة" as
# "صفا"). These spellings mean the same command word. (Checked after the exact words above.)
MISHEARD: dict[str, str] = {
    "افتا": "open", "افتاح": "open", "افته": "open", "افتحي": "open", "افتحلي": "open", "فتحلي": "open",
    "افتحو": "open", "افتحوا": "open", "ابتح": "open", "افدح": "open",
    "صفا": "page", "صفحه": "page", "صفحا": "page", "سفحة": "page", "سفحه": "page", "صفحت": "page", "صفح": "page",
    "الصفحه": "the page", "الصفا": "the page",
    "اعرظ": "show", "عرظ": "show", "ورنى": "show me", "وريني": "show me", "ورينا": "show me",
    "سجلني": "log me in", "دخلني": "log me in", "شغلي": "run", "شغلو": "run",
    "احجزلي": "book", "احجزي": "book", "ابحثلي": "search", "دورلي": "search",
}
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

PROMPT = ("You translate instructions for a software testing assistant from Arabic (Modern Standard or UAE / Gulf "
          "Arabic) to English. Write the instruction as one short English command, as a tester would type it "
          "(for example: 'open the booking page', 'log in as manager', 'book flight from Dubai to Karachi tomorrow'). "
          "Keep names, numbers, codes and web addresses exactly as written. Do not add anything. Reply with the "
          "English command only.")


def has_arabic(text: str | None) -> bool:
    return bool(text and ARABIC.search(text))


def normalize(text: str) -> str:
    """Without harakat / tatweel, with Western digits (the word list holds both hamza spellings)."""
    return _DIACRITICS.sub("", text or "").translate(_DIGITS)


def by_words(text: str) -> str:
    """The built-in word list: good enough for short commands when the local model is not running."""
    t = normalize(text)
    for ar, en in PHRASES:
        t = t.replace(ar, f" {en} ")
    out: list[str] = []
    for tok in re.split(r"(\s+)", t):
        if not tok.strip():
            out.append(tok)
            continue
        core = tok.strip("،,.؟?!")
        en = WORDS.get(core)
        if en is None:
            en = MISHEARD.get(core)
        if en is None and core.startswith("ال") and core[2:] in WORDS:
            en = WORDS[core[2:]]
        if en is None and len(core) > 2 and core[0] in "وبك" and core[1:] in WORDS:  # و / ب / ك: and, with, as
            en = {"و": "and ", "ب": "with ", "ك": "as "}[core[0]] + WORDS[core[1:]]
        out.append(en if en is not None else tok)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def to_english(text: str, llm: Any = None) -> tuple[str, bool]:
    """(English text, translated?) for a command. Text without Arabic letters is returned unchanged."""
    if not has_arabic(text):
        return text, False
    if llm is not None:
        try:
            out = llm.chat([{"role": "system", "content": PROMPT}, {"role": "user", "content": text.strip()[:1000]}],
                           max_tokens=120)
            out = (out or "").strip().strip('"').splitlines()[0].strip() if out else ""
            if out and not has_arabic(out) and len(out) <= 400:
                return out, True
        except Exception:  # the model is not running: the word list below
            pass
    return by_words(text), True


# ---- the wake name spoken in Arabic ------------------------------------------------------------------------------
# "Captain" said by an Arabic speaker and written by the recognizer: كابتن, كابدان, كبتن, كابتين ... The name is
# compared by its consonants (b/p, t/d, k/q/c are one sound each), so any spelling of the configured name works
# without a hard-coded list.
_AR_SOUND = {"ك": "k", "ق": "k", "ج": "j", "ب": "b", "پ": "b", "ت": "t", "د": "t", "ط": "t", "ض": "t", "ث": "s",
             "س": "s", "ص": "s", "ز": "z", "ذ": "z", "ظ": "z", "ر": "r", "ل": "l", "م": "m", "ن": "n", "ه": "h",
             "ة": "", "ح": "h", "خ": "h", "ش": "x", "ف": "f", "ڤ": "f", "غ": "g", "گ": "g", "ع": ""}
_CALLS = ("يا", "هي", "اوكي", "اوك", "هاي", "ي")


def name_sound(word: str) -> str:
    """Consonant outline of a name in Latin or Arabic letters: Captain / كابتن / كابدان -> 'kbtn'."""
    w = normalize(word or "").lower().strip("،,.؟?!:")
    if has_arabic(w):
        return "".join(_AR_SOUND.get(ch, "") for ch in w)
    w = re.sub(r"[^a-z]", "", w)
    w = w.replace("ph", "f").replace("ck", "k").replace("sh", "x").replace("kh", "h").replace("gh", "g").replace("th", "t")
    w = re.sub(r"c(?=[eiy])", "s", w)
    table = str.maketrans({"c": "k", "q": "k", "p": "b", "d": "t", "v": "f"})
    w = re.sub(r"[aeiouyw]", "", w.translate(table))
    return re.sub(r"(.)\1+", r"\1", w)


def _close(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    if min(len(a), len(b)) < 3 or abs(len(a) - len(b)) > 1:
        return False
    # one letter added, dropped or changed
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] <= 1


def strip_arabic_wake(text: str, names: list[str] | tuple[str, ...] | set[str]) -> str | None:
    """The Arabic sentence without a leading call of the wake name ("" if it was only the name), or None if the
    sentence does not start with the name. 'كابدان افتا صفا' with the name Captain -> 'افتا صفا'."""
    words = (text or "").strip().split()
    if words and normalize(words[0]).strip("،,.!") in _CALLS:
        words = words[1:]
    sounds = [name_sound(n) for n in names if n and n.strip()]
    for size in (2, 1):  # a two-word name ("Captain Saqr") first
        if len(words) < size:
            continue
        heard = name_sound("".join(words[:size]))
        if any(_close(heard, s) for s in sounds if len(s) >= 2 or heard == s):
            return " ".join(words[size:]).strip(" ،,.:!-")
    return None
