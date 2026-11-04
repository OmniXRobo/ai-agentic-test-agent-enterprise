"""23.3.15: the part of the element inventory the AI needs for one Test Case.

The whole inventory (for example 290 selectors on 5 pages, about 6,000 tokens) was sent with every Test Case. A local
model on a CPU spent most of the request limit just reading it, and the mapping call timed out. Now the AI gets every
page (so it can still go to any of them) and only the elements that match the Test Case: words of its title, steps,
expected results and test data, plus the elements Intelligent Discovery mapped to its steps. The full inventory is
still used afterwards to check every selector the AI chose.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

_WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset("""
a an the and or of to in on at by for from with into onto as is are was were be been being it its this that these
those then than there their them they he she we you your our my me his her not no yes do does did done has have had
can could should would will shall may might must per via user users verify verifies verified check checks checked
ensure ensures ensured step steps expected result results test tests case cases page pages should when where which
who what how all any each every some same able also just only again new click clicks clicked enter enters entered
type types typed select selects selected open opens opened navigate navigates navigated go goes see sees seen
displayed display displays shown show shows visible appear appears appeared value values field fields button buttons
link links data valid invalid functional testing scenario given
""".split())


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def keywords(texts: Iterable[str]) -> set[str]:
    """Meaningful words (lower case, simple stem) of the texts; also camelCase and snake_case parts."""
    out: set[str] = set()
    for text in texts:
        if not text:
            continue
        text = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(text)).lower()
        for w in _WORD.findall(text):
            if len(w) >= 3 and w not in STOPWORDS and not w.isdigit():
                out.add(_stem(w))
    return out


@dataclass
class _Element:
    order: int
    page: int
    line: str
    score: float


@dataclass
class Focus:
    text: str
    shown: int
    total: int
    pages: int


def focus_inventory(lines: list[str], words: set[str], *, start_url: str = "", prefer: Iterable[str] = (),
                    limit: int = 60) -> Focus:
    """Every PAGE line of the inventory and at most ``limit`` element lines, in their original order.

    Elements are ranked by the Test Case words their selector, role and name contain (the name counts double, a word
    found on few elements counts more than one found on many); selectors in ``prefer`` (mapped by Intelligent Discovery)
    come first. When fewer than ``limit`` elements match, the
    rest is filled with the form controls of the start page and of the pages that matched."""
    pages: list[str] = []
    elements: list[_Element] = []
    prefer_set = {p for p in prefer if p}
    start = (start_url or "").rstrip("/").lower()
    start_page = -1
    parsed: list[tuple[str, set[str], set[str]]] = []
    for line in lines:
        if line.startswith("PAGE "):
            pages.append(line)
            url = line[5:].split(" (", 1)[0].rstrip("/").lower()
            if start_page < 0 and start and url == start:
                start_page = len(pages) - 1
            continue
        if not pages:
            continue
        parts = [p.strip() for p in line.split(" | ")]
        selector = parts[0] if parts else line.strip()
        name_words = keywords([parts[2] if len(parts) > 2 else ""])
        sel_words = keywords([selector, parts[1] if len(parts) > 1 else ""]) - name_words
        elements.append(_Element(len(elements), len(pages) - 1, line, 0.0))
        parsed.append((selector, name_words, sel_words))
    # A word found on many elements (for example "cart" in every link of the cart page) says little about which
    # element a step needs; a rare one ("quantity") says a lot. Each matching word counts log(elements / found on).
    found_on: dict[str, int] = {}
    for _, name_words, sel_words in parsed:
        for w in (name_words | sel_words) & words:
            found_on[w] = found_on.get(w, 0) + 1
    n = max(1, len(parsed))
    for e, (selector, name_words, sel_words) in zip(elements, parsed):
        weight = lambda ws: sum(math.log(1 + n / found_on[w]) for w in ws & words)  # noqa: E731
        e.score = 2.0 * weight(name_words) + weight(sel_words) + (100 if selector in prefer_set else 0)
    if start_page < 0 and pages:
        start_page = 0
    total = len(elements)
    if total <= limit:
        chosen = elements
    else:
        ranked = sorted((e for e in elements if e.score > 0), key=lambda e: (-e.score, e.order))[:limit]
        picked = {e.order for e in ranked}
        if len(picked) < limit:
            hit_pages = {e.page for e in ranked} | {start_page}
            controls = ("textbox", "input", "combobox", "select", "checkbox", "radio", "button", "spinbutton",
                        "searchbox", "textarea")
            fill = [e for e in elements if e.order not in picked and e.page in hit_pages
                    and any(c in e.line.lower() for c in controls)]
            fill.sort(key=lambda e: (e.page != start_page, e.order))
            picked |= {e.order for e in fill[: limit - len(picked)]}
        chosen = [e for e in elements if e.order in picked]
    out: list[str] = []
    by_page: dict[int, list[str]] = {}
    for e in chosen:
        by_page.setdefault(e.page, []).append(e.line)
    for i, page in enumerate(pages):
        out.append(page)
        out.extend(by_page.get(i, []))
    return Focus("\n".join(out), len(chosen), total, len(pages))


def case_keywords(case_title: str, steps: list[dict[str, Any]], datasets: list[dict[str, Any]],
                  known: str | None = None) -> set[str]:
    texts: list[str] = [case_title]
    for s in steps:
        texts += [s.get("action") or "", s.get("expected") or ""]
    for d in datasets:
        values = d.get("values") or {}
        if isinstance(values, dict):
            for k, v in values.items():
                texts += [str(k), str(v) if isinstance(v, (str, int, float)) else ""]
    if known:
        texts.append(known)
    return keywords(texts)


_KNOWN_SELECTOR = re.compile(r" / .*? \((.+)\)\s*$")


def known_selectors(known: str | None) -> list[str]:
    """Selectors in the Unified QA Knowledge lines ("Step 1: ... -> page / element (selector)")."""
    out = []
    for line in (known or "").splitlines():
        m = _KNOWN_SELECTOR.search(line)
        if m and line.startswith("Step "):
            out.append(m.group(1).strip())
    return out
