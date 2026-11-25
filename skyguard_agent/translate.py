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
    # 23.14.0: Security Testing, Red Hat Fuse, ActiveMQ, Email / SMTP (both hamza spellings; longest first)
    ("الاختبارات الفاشلة في فيوز", "failed fuse tests"), ("اختبارات فيوز الفاشلة", "failed fuse tests"),
    ("الاختبارات الفاشلة في الوسيط", "failed broker tests"), ("اختبارات الوسيط الفاشلة", "failed broker tests"),
    ("قائمة الرسائل الميتة", "dead letter queue"), ("اختبار الرسائل الميتة", "dead letter tests"),
    ("الثغرات الأمنية الحرجة", "critical vulnerabilities"), ("الثغرات الامنية الحرجة", "critical vulnerabilities"),
    ("الثغرات الحرجة", "critical vulnerabilities"), ("الثغرات الأمنية", "security findings"),
    ("الثغرات الامنية", "security findings"), ("نتائج الأمان", "security findings"), ("نتائج الامان", "security findings"),
    ("اختبارات الأمان", "security testing"), ("اختبارات الامان", "security testing"), ("اختبار الأمان", "security testing"),
    ("اختبار الامان", "security testing"), ("الاختبار الأمني", "security testing"), ("الاختبار الامني", "security testing"),
    ("فحص الثغرات", "vulnerability scan"), ("فحص أمني", "security scan"), ("فحص امني", "security scan"),
    ("تقارير الأمان", "security reports"), ("تقارير الامان", "security reports"), ("تقرير الأمان", "security report"),
    ("تقرير الامان", "security report"), ("إعدادات الأمان", "security settings"), ("اعدادات الامان", "security settings"),
    ("ريد هات فيوز", "red hat fuse"), ("ريدهات فيوز", "red hat fuse"), ("مراقبة المسارات", "route monitoring"),
    ("مقارنة التحويل", "transformation comparison"), ("مقارنة التحويلات", "transformation comparison"),
    ("أكتيف إم كيو", "activemq"), ("اكتيف ام كيو", "activemq"), ("اكتف ام كيو", "activemq"), ("أكتيف ام كيو", "activemq"),
    ("مراقبة الوسيط", "broker monitoring"), ("صحة الوسيط", "broker health"), ("وسيط الرسائل", "message broker"),
    ("الرسائل الميتة", "dead letter tests"), ("سجل البريد", "email history"),
    ("إعدادات البريد", "email settings"), ("اعدادات البريد", "email settings"), ("البريد الإلكتروني", "email"),
    ("البريد الالكتروني", "email"), ("لوحة التحكم", "dashboard"), ("لوحة القيادة", "dashboard"),
    ("سير عمل الوكلاء", "agent workflow"), ("اختبار واجهات البرمجة", "api testing"),
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
    # 23.14.0
    "الثغرات": "vulnerabilities", "ثغرات": "vulnerabilities", "فيوز": "fuse", "الطوابير": "queues", "طوابير": "queues",
    "المسارات": "routes", "مسارات": "routes", "الوسيط": "message broker", "ابدأ": "start", "ابدا": "start",
    "حلل": "analyze", "تقرير": "report", "التقارير": "reports",
}
# Speech recognition often mishears the last letter or a vowel in Gulf speech ("افتح" heard as "افتا", "صفحة" as
# "صفا"). These spellings mean the same command word. (Checked after the exact words above.)
MISHEARD: dict[str, str] = {
    "افتا": "open", "افتاح": "open", "افتاحة": "open", "افتاحه": "open", "افتاحا": "open", "فتاحة": "open",
    "افتاحي": "open", "افتحة": "open", "افتحه": "open it", "افته": "open", "افتحي": "open", "افتحلي": "open", "فتحلي": "open",
    "افتحو": "open", "افتحوا": "open", "ابتح": "open", "افدح": "open",
    "صفا": "page", "صفحه": "page", "صفحا": "page", "سفحة": "page", "سفحه": "page", "صفحت": "page", "صفح": "page",
    "الصفحه": "the page", "الصفا": "the page",
    "اعرظ": "show", "عرظ": "show", "ورنى": "show me", "وريني": "show me", "ورينا": "show me",
    "سجلني": "log me in", "دخلني": "log me in", "شغلي": "run", "شغلو": "run",
    "احجزلي": "book", "احجزي": "book", "ابحثلي": "search", "دورلي": "search",
}
# 23.15.0: words that were missing (notifications, alerts) and the pages of the dashboard said in Arabic
WORDS.update({
    "الاشعارات": "notifications", "الإشعارات": "notifications", "اشعارات": "notifications", "إشعارات": "notifications",
    "الاشعار": "notification", "التنبيهات": "notifications", "تنبيهات": "notifications", "الإخطارات": "notifications",
    "الاخطارات": "notifications", "اللوحة": "dashboard", "لوحة": "dashboard", "السجل": "history", "سجل": "history",
    "التاريخ": "history", "النتائج": "results", "نتائج": "results", "التقرير": "report", "الاعدادات": "settings",
    "الإعدادات": "settings", "اعدادات": "settings", "التفضيلات": "preferences", "العيوب": "defects", "عيوب": "defects",
    "الاخطاء": "defects", "الأخطاء": "defects", "خلل": "defect", "الحالات": "test cases", "حالات": "test cases",
    "الخطط": "test plans", "خطط": "test plans", "الاداء": "performance", "الأداء": "performance",
    "المهام": "tasks", "المساعد": "the agent", "الوكيل": "the agent", "الصوت": "voice", "الصوتية": "voice",
    "القصص": "user stories", "قصص": "user stories", "الاستكشاف": "discovery", "اكتشاف": "discovery",
    "الاتمتة": "automation", "الأتمتة": "automation", "اتمتة": "automation", "البرنامج النصي": "script",
    "السكربت": "script", "سكربت": "script", "المراقبة": "monitoring", "الملخص": "summary",
})


def _distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def fuzzy_word(core: str) -> str | None:
    """23.15.0: the word list again, for a word the recognizer misheard by a letter or two.

    Speech recognition writes Gulf Arabic as it hears it: "إفتاء" for "افتح" (open) and "الإشارات" for "الإشعارات"
    (notifications). Those were answered "this operation is not supported". A word within one letter (two for a long
    word) of a known command word, and no other, is taken as that word; anything less certain is left alone.
    """
    core = _HAMZA_ALEF.sub("ا", normalize(core or "").strip("،,.؟?!:"))
    if len(core) < 3:
        return None
    if core in _FUZZY_KEYS:
        return _FUZZY_KEYS[core]
    budget = 1 if len(core) < 6 else 2
    best: list[tuple[int, str]] = []
    for key, english in _FUZZY_KEYS.items():
        if abs(len(key) - len(core)) > budget:
            continue
        d = _distance(core, key)
        if d <= budget:
            best.append((d, english))
    if not best:
        return None
    best.sort(key=lambda x: x[0])
    closest = [e for d, e in best if d == best[0][0]]
    return closest[0] if len({e for e in closest}) == 1 else None


_HAMZA_ALEF = re.compile("[أإآ]")
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_FUZZY_KEYS: dict[str, str] = {}

# 23.13.3: literal. The old example "open the booking page" made the model answer "Open the booking page" for a plain
# "افتح صفحة" (open page); a page, place or object that was not said is never added.
PROMPT = ("You translate instructions for a software testing assistant from Arabic (Modern Standard or UAE / Gulf "
          "Arabic) to English. The text comes from speech recognition and may be misspelled: translate what was "
          "meant (for example 'إفتاحة صفحة' and 'افتا صفا' mean 'open page'). Translate literally into one short "
          "English command, as a tester would type it. Never add a page name, place, date, account or any other "
          "detail that is not in the Arabic text. Keep names, numbers, codes and web addresses exactly as written. "
          "Reply with the English command only.")


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
            en = MISHEARD.get(core) or MISHEARD.get(_HAMZA_ALEF.sub("ا", core))  # إفتاحة is افتاحة
        if en is None and core.startswith("ال") and core[2:] in WORDS:
            en = WORDS[core[2:]]
        if en is None and len(core) > 2 and core[0] in "وبك" and core[1:] in WORDS:  # و / ب / ك: and, with, as
            en = {"و": "and ", "ب": "with ", "ك": "as "}[core[0]] + WORDS[core[1:]]
        if en is None:  # 23.15.0: a word the recognizer misheard by a letter or two
            en = fuzzy_word(core)
        out.append(en if en is not None else tok)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def to_english(text: str, llm: Any = None) -> tuple[str, bool]:
    """(English text, translated?) for a command. Text without Arabic letters is returned unchanged."""
    if not has_arabic(text):
        return text, False
    # 23.13.3: a short command that the word list translates completely ("افتح صفحة", "إفتاحة صفحة") is taken as it
    # is: exact, and the model cannot add a page that was not said
    words = by_words(text)
    if not has_arabic(words) and len(words.split()) <= 4:
        return words, True
    if llm is not None:
        try:
            out = llm.chat([{"role": "system", "content": PROMPT}, {"role": "user", "content": text.strip()[:1000]}],
                           max_tokens=120)
            out = (out or "").strip().strip('"').splitlines()[0].strip() if out else ""
            if out and not has_arabic(out) and len(out) <= 400:
                return out, True
        except Exception:  # the model is not running: the word list below
            pass
    return words, True


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


# ---- the spoken reply (SkyGuard 23.13.2) ---------------------------------------------------------------------------
# The AI Agent page and the extension speak their replies: in English for an English request, in Arabic for an Arabic
# one. The Arabic text comes from the local model; when it is not running, a short Arabic line for the kind of reply
# is spoken and the details stay on screen.
REPLY_PROMPT = ("Translate this short reply of a software testing assistant into clear Arabic as spoken in the UAE. "
                "Keep names, numbers, codes and web addresses unchanged. Reply with the Arabic text only.")
ARABIC_LINES = {
    "run": "حاضر. أجهز المعاينة الحين، وبعدها أكد لو سمحت قبل التنفيذ.",
    "navigate": "حاضر، فتحت الصفحة.",
    "records": "هذي النتائج على الشاشة.",
    "clarify": "أحتاج معلومة إضافية. شوف الشاشة لو سمحت.",
    "need_account": "أحتاج حساب للدخول. عبي البيانات على الشاشة.",
    "need_credential": "أحتاج كلمة المرور. احفظها في النموذج الآمن على الشاشة.",
    "sign_in": "حاضر، جاري تسجيل الدخول.",
    "needs_approval": "هذي المهمة تحتاج موافقتك أولاً.",
    "secret_intercepted": "لا تكتب كلمات السر في المحادثة. استخدم النموذج الآمن.",
    "error": "ما قدرت أنفذ الطلب. السبب على الشاشة.",
    # 23.13.3: a question with choices, and what happens to a task after the request (preview, done, failed ...)
    "choose": "أي صفحة تقصد؟ اختر من الأزرار على الشاشة.",
    "preview": "المعاينة جاهزة. راجعها وأكد لو سمحت.",
    "completed": "تم التنفيذ بنجاح.",
    "partial": "تم تنفيذ جزء من المهمة. التفاصيل على الشاشة.",
    "failed": "ما نجحت المهمة. السبب على الشاشة.",
    "stopped": "وقفت المهمة.",
    "cancelled": "تم إلغاء المهمة.",
    "paused": "أحتاجك تكمل خطوة على الشاشة، وبعدها اضغط استئناف.",
}
# 23.13.3: kinds of line that /say accepts (task progress spoken after the first reply)
LINE_KINDS = ("preview", "completed", "partial", "failed", "stopped", "cancelled", "paused")
ARABIC_DEFAULT = "تم. التفاصيل على الشاشة."
_URL = re.compile(r"https?://\S+")


def to_arabic(text: str, llm: Any = None) -> str | None:
    """The reply in Arabic by the local model, or None when it is not running or answered something else."""
    if not text or llm is None:
        return None
    try:
        out = llm.chat([{"role": "system", "content": REPLY_PROMPT}, {"role": "user", "content": text.strip()[:800]}],
                       max_tokens=300)
    except Exception:  # noqa: BLE001 - the model is not running: the short line is spoken instead
        return None
    out = (out or "").strip()
    return out if out and has_arabic(out) and len(out) < 1200 else None


def spoken_reply(reply: dict[str, Any], arabic: bool, llm: Any = None) -> dict[str, str]:
    """{"speak": text to say, "language": "en" | "ar"} for an agent reply. Web addresses are not read out."""
    text = _URL.sub("", str(reply.get("text") or "")).strip()
    text = re.sub(r"\s+", " ", text)[:500]
    if not arabic:
        return {"speak": text, "language": "en"}
    ar = to_arabic(text, llm) if text else None
    kind = "choose" if reply.get("type") == "clarify" and reply.get("options") else str(reply.get("type"))
    return {"speak": ar or ARABIC_LINES.get(kind, ARABIC_DEFAULT), "language": "ar"}


def _build_fuzzy_keys() -> None:
    for word, english in WORDS.items():
        _FUZZY_KEYS.setdefault(_HAMZA_ALEF.sub("ا", normalize(word)), english)
    for word, english in MISHEARD.items():
        _FUZZY_KEYS.setdefault(_HAMZA_ALEF.sub("ا", normalize(word)), english)
    for word, english in list(_FUZZY_KEYS.items()):
        if word.startswith("ال"):
            _FUZZY_KEYS.setdefault(word[2:], english)


_build_fuzzy_keys()
