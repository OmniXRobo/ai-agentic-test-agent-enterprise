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
