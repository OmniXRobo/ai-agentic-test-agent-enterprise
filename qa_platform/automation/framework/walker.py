"""Walks the manual Test Case steps through the application to find the element of every step (23.7.2).

Before 23.7.2 the whole Test Case was given to the AI in one request and whatever came back was saved, so a small
local model that answered with only the first step ("open the page") produced a script with one step, and steps
whose pages were never discovered (anything behind the login, the cart or a checkout form) could not be mapped at
all. Now every step is handled on its own:

1. ``steps.plan_steps`` reads each manual step (and the precondition) into intents and checks.
2. The walker opens the application in a browser and performs the steps in order, exactly as the test will:
   for each intent it looks at the page that is on screen now, picks the element the step names (by label,
   placeholder, accessible name, text, test id and id), performs the action and records the element. Pages that
   discovery never reached (checkout step two, the confirmation page) are seen this way.
3. A step it cannot read is given to the AI on its own, with only the elements of the current page (a small request
   a 3B model answers well); the selector it returns must be on the page.
4. A step that names something the application does not have (an "Address" field on a form without one) is not
   invented: it is kept in the script as a flagged step that says what is missing.

When no browser can be used the same resolution runs on the discovered element inventory (``StaticDriver``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urljoin

from .builder import Inventory, MappedStep, relative_url
from .steps import Check, Intent, PlannedStep, key_words, tokens

FILL_TYPES_EXCLUDED = {"button", "submit", "reset", "checkbox", "radio", "hidden", "file", "image", "range", "color"}
CLICK_ROLES = {"button", "link", "menuitem", "tab", "option", "switch"}
ADVANCE = ("continue", "next", "proceed", "submit", "save", "go")
FINISH = ("finish", "complete", "place order", "confirm", "pay", "purchase", "buy", "order", "done")
AVOID = re.compile(r"\b(?:cancel|back|continue\s+shopping|remove|delete|reset|clear|logout|log\s?out|sign\s?out|close|"
                   r"menu)\b", re.I)
SUCCESS_TEXT = re.compile(r"\b(?:thank|success\w*|complete[d]?|confirm\w*|placed|received|done)\b", re.I)
SAMPLE_VALUES = (
    ({"first"}, "Test"), ({"last"}, "User"), ({"postal"}, "12345"), ({"code"}, "12345"), ({"email"}, "qa.user@example.com"),
    ({"phone"}, "5550100"), ({"city"}, "Testville"), ({"address"}, "1 Test Street"), ({"street"}, "1 Test Street"),
    ({"state"}, "CA"), ({"country"}, "United States"), ({"company"}, "QA Company"), ({"name"}, "Test User"),
    ({"quantity"}, "1"), ({"age"}, "30"),
)


class WalkAborted(Exception):
    """The application could not be opened in the browser (the walk falls back to the discovered elements)."""


class Unresolved(Exception):
    """The step names something that is not on the application (the reason says what and where).

    ``authoritative``: the browser was on the page where the step happens and the field is not there (an Address
    field on a form without one), so no other source may map it to some other element."""

    def __init__(self, reason: str, authoritative: bool = False) -> None:
        super().__init__(reason)
        self.authoritative = authoritative


@dataclass
class Snapshot:
    url: str
    title: str
    elements: list[dict[str, Any]]


@dataclass
class WalkResult:
    steps: list[MappedStep] = field(default_factory=list)
    origins: list[tuple[str, dict[str, Any]] | None] = field(default_factory=list)  # page url + element per step
    test_data: dict[str, str] = field(default_factory=dict)
    data_labels: dict[str, str] = field(default_factory=dict)
    pages: list[dict[str, Any]] = field(default_factory=list)  # discovery-format pages seen while walking
    notes: list[str] = field(default_factory=list)
    flagged: list[dict[str, Any]] = field(default_factory=list)  # {"tc_step", "action", "reason", "authoritative"}
    resolved_steps: set[int] = field(default_factory=set)
    ai_steps: set[int] = field(default_factory=set)
    live: bool = False

    def covers(self, number: int) -> bool:
        return any(s.manual_step == number for s in self.steps)

    def settled(self, number: int) -> bool:
        """The step was automated, or every reason it was not is that the application lacks what it names."""
        flags = [f for f in self.flagged if f["tc_step"] == number]
        return number in self.resolved_steps and not flags or bool(flags) and all(f["authoritative"] for f in flags)

    def for_step(self, number: int) -> list[tuple[MappedStep, tuple[str, dict[str, Any]] | None]]:
        return [(s, o) for s, o in zip(self.steps, self.origins) if s.manual_step == number]


# ---- element classification ---------------------------------------------------------------------------------------


def is_fillable(fp: dict[str, Any]) -> bool:
    tag, itype, role = fp.get("tag"), (fp.get("input_type") or "text"), fp.get("role")
    if tag == "textarea" or role in ("textbox", "searchbox", "spinbutton"):
        return True
    return tag == "input" and itype not in FILL_TYPES_EXCLUDED


def is_selectable(fp: dict[str, Any]) -> bool:
    return fp.get("tag") == "select" or fp.get("role") in ("combobox", "listbox")


def is_checkable(fp: dict[str, Any]) -> bool:
    return fp.get("input_type") in ("checkbox", "radio") or fp.get("role") in ("checkbox", "radio", "switch")


def is_clickable(fp: dict[str, Any]) -> bool:
    tag, itype, role = fp.get("tag"), fp.get("input_type"), fp.get("role")
    if tag in ("button", "a", "summary"):
        return True
    if tag == "input" and itype in ("submit", "button", "reset", "image"):
        return True
    return role in CLICK_ROLES


def describe(fp: dict[str, Any]) -> str:
    for k in ("label", "name", "placeholder", "text", "test_id", "element_id"):
        v = fp.get(k)
        if v and str(v).strip():
            return " ".join(str(v).split())[:60]
    return fp.get("tag") or "element"


def words_of(fp: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for k in ("label", "name", "placeholder", "text", "test_id", "element_id"):
        out |= set(tokens(fp.get(k)))
    return out


def score(target: set[str], fp: dict[str, Any]) -> float:
    if not target:
        return 0.0
    have = words_of(fp)
    hit = len(target & have) / len(target)
    if hit == 0:
        return 0.0
    primary = set()
    for k in ("label", "name", "placeholder", "text"):
        primary |= set(tokens(fp.get(k)))
    exact = 0.3 if primary and {w for w in primary if w not in ("to", "the", "a")} == target else 0.0
    container = 0.01 if fp.get("tag") in ("div", "section", "form", "body") else 0.0
    return hit + exact - 0.02 * len(primary - target) - container


def best(target: set[str], elements: list[dict[str, Any]], kind: Callable[[dict[str, Any]], bool],
         threshold: float = 0.75, exclude: Callable[[dict[str, Any]], bool] | None = None) -> dict[str, Any] | None:
    scored = [(score(target, fp), -i, fp) for i, fp in enumerate(elements)
              if kind(fp) and not (exclude and exclude(fp))]
    scored = [x for x in scored if x[0] >= threshold]
    return max(scored, key=lambda x: (x[0], x[1]))[2] if scored else None


def check_name(fp: dict[str, Any]) -> str:
    """A readable name for an element that is checked: its text when that is a short label, else its test id or id
    ("shopping cart badge" for a badge showing "1", "complete header" for a long thank-you sentence)."""
    text = " ".join(str(fp.get("text") or fp.get("name") or "").split())
    ident = fp.get("test_id") or fp.get("element_id") or ""
    if ident and (len(text) < 3 or len(text.split()) > 3 or text.replace(".", "").isdigit()):
        return " ".join(tokens(ident)) or text or "element"
    return text or describe(fp)


def page_title(snap: Snapshot) -> str:
    """The document title, with the page's own heading when the title alone does not tell pages apart."""
    heading = next((" ".join(str(e.get("text") or "").split()) for e in snap.elements
                    if (e.get("tag") in ("h1", "h2") or "title" in (e.get("test_id") or "") or
                        "title" in " ".join(e.get("classes") or [])) and e.get("text")), "")
    if heading and heading.lower() != (snap.title or "").lower() and len(heading) <= 60:
        return f"{snap.title} - {heading}" if snap.title else heading
    return snap.title


def page_label(snap: Snapshot, base_url: str) -> str:
    rel = relative_url(snap.url, base_url) or "start page"
    title = page_title(snap)
    return f"{title} ({rel})" if title else rel


def sample_value(words: set[str]) -> str:
    for need, value in SAMPLE_VALUES:
        if need <= words:
            return value
    return "Test"


def data_key(text: str) -> str:
    ws = [w for w in re.findall(r"[a-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "").lower())
          if w not in ("the", "a", "an", "field", "box", "input", "your")]
    key = "_".join(ws[:4]) or "value"
    return f"value_{key}" if key[0].isdigit() else key


# ---- drivers ---------------------------------------------------------------------------------------------------------

_SNAPSHOT_JS = """() => {
  const fp = window.__qaFp;
  if (!fp) return {title: document.title, elements: []};
  const els = fp.candidates(400);
  const seen = new Set(els.map((e) => e.selector));
  for (const el of document.querySelectorAll('h1,h2,h3,[class*="title"],[class*="header"],[class*="badge"],[role="alert"]')) {
    if (!fp.visible(el) || !(el.innerText || '').trim()) continue;
    const f = fp.fingerprint(el);
    if (!seen.has(f.selector)) { seen.add(f.selector); els.push(f); }
  }
  for (const e of els) {
    let node = null;
    try { node = document.querySelector(e.selector); } catch (err) { node = null; }
    e.value = node && 'value' in node && node.type !== 'password' ? String(node.value || '') : null;
  }
  return {title: document.title, elements: els};
}"""


class LiveDriver:
    """The application in a real browser page (Playwright)."""

    live = True

    def __init__(self, page: Any, settle_ms: int = 4000) -> None:
        self.page = page
        self.settle_ms = settle_ms

    def snapshot(self) -> Snapshot:
        try:
            info = self.page.evaluate(_SNAPSHOT_JS)
        except Exception:
            self.settle()
            info = self.page.evaluate(_SNAPSHOT_JS)
        return Snapshot(self.page.url, info.get("title") or "", info.get("elements") or [])

    def settle(self) -> None:
        for state, timeout in (("domcontentloaded", self.settle_ms * 2), ("networkidle", self.settle_ms)):
            try:
                self.page.wait_for_load_state(state, timeout=timeout)
            except Exception:
                pass
        try:
            self.page.wait_for_function("() => window.__qaFp && window.__qaFp.candidates(1).length > 0",
                                        timeout=self.settle_ms)
        except Exception:
            pass

    def goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        self.settle()

    def _loc(self, fp: dict[str, Any]) -> Any:
        for sel in [fp.get("selector"), *(fp.get("alternates") or [])]:
            if not sel:
                continue
            try:
                loc = self.page.locator(sel)
                if loc.count() == 1:
                    return loc.first
            except Exception:
                continue
        return self.page.locator(fp["selector"]).first

    def act(self, action: str, fp: dict[str, Any], value: str | None = None) -> None:
        loc = self._loc(fp)
        before = self.page.url
        if action == "fill":
            loc.fill("" if value is None else str(value), timeout=10000)
            return
        if action == "select":
            try:
                loc.select_option(value=str(value), timeout=10000)
            except Exception:
                loc.select_option(label=str(value), timeout=10000)
        elif action == "check":
            loc.check(timeout=10000)
        elif action == "uncheck":
            loc.uncheck(timeout=10000)
        elif action == "press":
            loc.press(value or "Enter", timeout=10000)
        else:
            loc.click(timeout=10000)
        self.page.wait_for_timeout(300)
        if self.page.url == before:
            self.page.wait_for_timeout(500)
        self.settle()

    def check_value(self, fp: dict[str, Any], value: str) -> bool:
        try:
            return self._loc(fp).input_value(timeout=3000) == value
        except Exception:
            return False

    def check_text(self, fp: dict[str, Any], text: str) -> bool:
        try:
            return text in (self._loc(fp).inner_text(timeout=3000) or "")
        except Exception:
            return False


class StaticDriver:
    """The discovered element inventory, used when no browser can be opened (nothing is performed)."""

    live = False

    def __init__(self, discovery: dict[str, Any] | None, base_url: str) -> None:
        self.base_url = base_url
        self.pages = [p for p in (discovery or {}).get("pages", []) if not p.get("error")]
        self.current = self._page_for(base_url) or (self.pages[0] if self.pages else None)

    def _page_for(self, url: str) -> dict[str, Any] | None:
        want = relative_url(url, self.base_url)
        return next((p for p in self.pages if relative_url(p["url"], self.base_url) == want), None)

    def snapshot(self) -> Snapshot:
        if self.current is None:
            return Snapshot(self.base_url, "", [])
        return Snapshot(self.current["url"], self.current.get("title") or "", list(self.current.get("elements") or []))

    def others(self) -> list[Snapshot]:
        return [Snapshot(p["url"], p.get("title") or "", list(p.get("elements") or [])) for p in self.pages
                if p is not self.current]

    def switch(self, url: str) -> None:
        page = self._page_for(url)
        if page is not None:
            self.current = page

    def goto(self, url: str) -> None:
        self.switch(url)

    def act(self, action: str, fp: dict[str, Any], value: str | None = None) -> None:
        if action == "click" and fp.get("href") and not str(fp["href"]).startswith(("#", "javascript")):
            self.switch(urljoin(self.current["url"] if self.current else self.base_url, fp["href"]))

    def check_value(self, fp: dict[str, Any], value: str) -> bool:
        return True

    def check_text(self, fp: dict[str, Any], text: str) -> bool:
        return True


# ---- the walker ------------------------------------------------------------------------------------------------------


class StepWalker:
    def __init__(self, driver: Any, base_url: str, *, role: str | None = None,
                 credentials: tuple[str | None, str | None] | None = None, datasets: list[dict[str, Any]] | None = None,
                 ask_ai: Callable[[PlannedStep, Snapshot], list[MappedStep]] | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        self.driver = driver
        self.base_url = base_url
        self.role = role or "user"
        self.credentials = credentials or (None, None)
        self.datasets = [d for d in (datasets or []) if isinstance(d.get("values"), dict)]
        self.ask_ai = ask_ai
        self.log = log or (lambda text: None)
        self.result = WalkResult(live=bool(getattr(driver, "live", False)))
        self._pages: dict[str, dict[str, Any]] = {}
        self._logged_in = False
        self._cart_items = 0
        self._step: PlannedStep | None = None
        self._flow: str | None = None
        self._blind = False  # the walk could not go past the sign-in

    # ---- recording ------------------------------------------------------------------------------------------

    def _snap(self) -> Snapshot:
        snap = self.driver.snapshot()
        key = relative_url(snap.url, self.base_url).split("#")[0]
        page = self._pages.setdefault(key, {"url": snap.url.split("#")[0], "title": page_title(snap), "elements": []})
        known = {e["selector"] for e in page["elements"]}
        page["elements"] += [e for e in snap.elements if e.get("selector") and e["selector"] not in known]
        return snap

    def _record(self, action: str, snap: Snapshot | None, fp: dict[str, Any] | None, *, value: str | None = None,
                description: str = "", name: str | None = None) -> None:
        assert self._step is not None
        self.result.steps.append(MappedStep(
            action=action, selector=fp.get("selector") if fp else None, value=value, description=description,
            manual_step=self._step.number, business_flow=self._flow, element_name=name or (describe(fp) if fp else None)))
        self.result.origins.append((snap.url.split("#")[0], fp or {}) if snap is not None else None)

    def _data(self, label: str, value: str | None, words: set[str], *, generated_ok: bool = True) -> tuple[str, str]:
        """(data key, real value) for a field; the value from the step, else the Test Data, else a sample value."""
        if value is not None:
            key = data_key(label)
            self.result.test_data[key] = value
            self.result.data_labels.setdefault(key, label)
            return key, value
        for ds in self.datasets:
            for k, v in ds["values"].items():
                if v not in (None, "") and words and (key_words(k) == words or key_words(k) >= words):
                    self.result.data_labels.setdefault(k, label)
                    return k, str(v)
        key = data_key(label)
        if key in self.result.test_data:
            return key, self.result.test_data[key]
        sample = sample_value(words)
        self.result.test_data[key] = sample
        self.result.data_labels.setdefault(key, label)
        if generated_ok:
            self.result.notes.append(f"Test data '{key}' ({label}) is not in the Test Case: sample value '{sample}' "
                                     "was used; change it in Test Data.")
        return key, sample

    # ---- main ------------------------------------------------------------------------------------------------

    def walk(self, planned: list[PlannedStep]) -> WalkResult:
        for ps in planned:
            self._step = ps
            start = len(self.result.steps)
            label = "Precondition" if ps.number == 0 else f"Step {ps.number}"
            before = self._snap()
            if self._blind:
                self._flag(ps, "Not checked on the application: the sign-in before it needs Application Credentials.")
                continue
            if not ps.intents:
                self._ask_ai(ps, before)
            for intent in ps.intents:
                self._flow = None
                try:
                    getattr(self, f"_do_{intent.kind}")(intent)
                except Unresolved as exc:
                    self._flag(ps, str(exc), exc.authoritative)
                except Exception as exc:  # the page did not respond as expected: keep going, say why
                    first = str(exc).splitlines()[0][:200] if str(exc) else ""
                    if intent.kind == "goto" and self.driver.live and not self.result.resolved_steps:
                        raise WalkAborted(f"{type(exc).__name__}: {first}") from exc
                    self._flag(ps, f"{type(exc).__name__}: {first}".strip())
            self._flow = None
            mine = self.result.steps[start:]
            acted = [s for s in mine if s.action != "not_on_application"]
            if self._blind:  # written, not performed: nothing on screen to check, and not confirmed
                continue
            if acted and not (len(acted) < len(mine) and all(s.action.startswith("expect") for s in acted)):
                for check in ps.checks:
                    try:
                        self._verify(check, before)
                    except Exception as exc:
                        self.result.notes.append(f"{label}: expected result not verified automatically "
                                                 f"({type(exc).__name__}).")
            mine = self.result.steps[start:]
            done = [s for s in mine if s.action != "not_on_application"]
            if done and len(done) < len(mine) and all(s.action.startswith("expect") for s in done):
                done = []  # only a check of what was already on screen: the step itself was not done
            if done:
                self.result.resolved_steps.add(ps.number)
                self.log(f"{label}: {len(done)} action(s) on {page_label(self.driver.snapshot(), self.base_url)}")
            elif ps.number and not any(s.manual_step == ps.number for s in self.result.steps):
                self._flag(ps, "The step could not be matched to an element of the application.")
        self.result.pages = list(self._pages.values())
        return self.result

    def _flag(self, ps: PlannedStep, reason: str, authoritative: bool = False) -> None:
        self.result.flagged.append({"tc_step": ps.number, "action": ps.action, "reason": reason,
                                    "authoritative": authoritative})
        self._record("not_on_application", None, None, value=reason, description=ps.action or "Precondition")
        self.log(f"{'Precondition' if ps.number == 0 else 'Step ' + str(ps.number)} flagged: {reason}")

    def _ask_ai(self, ps: PlannedStep, snap: Snapshot) -> None:
        if self.ask_ai is None:
            return
        try:
            steps = self.ask_ai(ps, snap) or []
        except Exception as exc:
            self.result.notes.append(f"Step {ps.number}: the AI could not map it ({type(exc).__name__}).")
            return
        by_sel: dict[str, dict[str, Any]] = {}
        for fp in snap.elements:
            for s in [fp.get("selector"), *(fp.get("alternates") or [])]:
                if s:
                    by_sel.setdefault(s, fp)
        for st in steps[:4]:
            action = (st.action or "").strip().lower()
            if action == "goto":
                self.driver.goto(urljoin(self.base_url, st.value or ""))
                self._record("goto", None, None, value=st.value or self.base_url, description=st.description)
                continue
            fp = by_sel.get(st.selector or "")
            if fp is None:
                self.result.notes.append(f"Step {ps.number}: the AI chose an element that is not on the page "
                                         f"({st.selector}); ignored.")
                continue
            if action in ("fill", "type", "select"):
                key, real = self._data(describe(fp), st.value if st.value and "{{" not in st.value else None,
                                       key_words(describe(fp)))
                if action != "select":
                    self.driver.act("fill", fp, real)
                else:
                    self.driver.act("select", fp, real)
                self._record("select" if action == "select" else "fill", snap, fp, value="{{DATA:%s}}" % key,
                             description=st.description or ps.action)
            elif action in ("click", "check", "uncheck", "dblclick", "hover", "press"):
                self.driver.act("click" if action in ("dblclick", "hover") else action, fp, st.value)
                self._record(action, snap, fp, value=st.value, description=st.description or ps.action)
                snap = self._snap()
            elif action.startswith("expect_"):
                self._record(action, snap, fp, value=st.value, description=st.description or ps.expected)
        if any(s.manual_step == ps.number for s in self.result.steps):
            self.result.ai_steps.add(ps.number)

    # ---- intents -------------------------------------------------------------------------------------------

    def _find(self, words: set[str], kind: Callable[[dict[str, Any]], bool], what: str, threshold: float = 0.75,
              exclude: Callable[[dict[str, Any]], bool] | None = None,
              field_step: bool = False) -> tuple[Snapshot, dict[str, Any]]:
        snap = self._snap()
        fp = best(words, snap.elements, kind, threshold, exclude)
        if fp is None and isinstance(self.driver, StaticDriver):
            for other in self.driver.others():
                fp = best(words, other.elements, kind, threshold, exclude)
                if fp is not None:
                    self.driver.switch(other.url)
                    snap = other
                    break
        if fp is None:
            # A missing input field on the live page where the step's form is (other fields of it are there) is
            # authoritative; anything else may still be found another way.
            sure = field_step and self.driver.live and any(kind(e) for e in snap.elements)
            raise Unresolved(self._missing(what, snap, kind), authoritative=sure)
        return snap, fp

    def _missing(self, what: str, snap: Snapshot, kind: Callable[[dict[str, Any]], bool]) -> str:
        names = []
        for fp in snap.elements:
            if kind(fp):
                d = describe(fp)
                if d not in names:
                    names.append(d)
        listing = f" Available there: {', '.join(names[:12])}." if names else ""
        return f"The application has no {what} on {page_label(snap, self.base_url)}.{listing}"

    def _do_goto(self, intent: Intent) -> None:
        url = intent.value or self.base_url
        self.driver.goto(url)
        snap = self._snap()
        self._record("goto", None, None, value=url, description=f"Open {page_label(snap, self.base_url)}")

    def _do_login(self, intent: Intent) -> None:
        snap = self._snap()
        pw = next((e for e in snap.elements if e.get("input_type") == "password"), None)
        if pw is None and not self._logged_in and self.driver.live:
            link = best({"login"}, snap.elements, is_clickable, 0.75, lambda e: bool(words_of(e) & {"out"}))
            if link is not None:  # a "Sign in" link to the sign-in page
                self._flow = "login"
                self._click(snap, link, f"Open {describe(link)}")
                snap = self._snap()
                pw = next((e for e in snap.elements if e.get("input_type") == "password"), None)
        if pw is None:
            if self._logged_in:
                return
            if isinstance(self.driver, StaticDriver):
                for other in self.driver.others():
                    pw = next((e for e in other.elements if e.get("input_type") == "password"), None)
                    if pw is not None:
                        self.driver.switch(other.url)
                        snap = other
                        break
            if pw is None:
                raise Unresolved(f"No sign-in form (password field) on {page_label(snap, self.base_url)}.")
        index = snap.elements.index(pw)
        user = next((e for e in reversed(snap.elements[:index]) if is_fillable(e) and e.get("input_type") != "password"),
                    None)
        submit = best({"login"}, snap.elements[index + 1:], is_clickable, 0.5) or \
            next((e for e in snap.elements[index + 1:] if is_clickable(e) and (e.get("input_type") == "submit" or
                  re.search(r"log\s?in|sign\s?in|submit|continue|next", describe(e), re.I))), None)
        username, password = self.credentials
        blind = self.driver.live and not (username and password)
        if blind:  # the sign-in is written with the credential references, but cannot be performed now
            self.result.notes.append(f"Sign-in needs Application Credentials for role '{self.role}' (none saved), so "
                                     "the steps after the login could not be checked on the application. Save them in "
                                     "Credentials and regenerate.")
            self.log(self.result.notes[-1])
            self.driver.act = lambda *a, **k: None  # nothing more is performed in the browser
            self._blind = True
        self._flow = "login"
        if user is not None:
            self.driver.act("fill", user, username)
            self._record("fill", snap, user, value="{{CRED:%s.username}}" % self.role, description="Enter the username",
                         name=describe(user))
        self.driver.act("fill", pw, password)
        self._record("fill", snap, pw, value="{{CRED:%s.password}}" % self.role, description="Enter the password",
                     name=describe(pw))
        if submit is not None:
            self.driver.act("click", submit)
            self._record("click", snap, submit, description=f"Click {describe(submit)}", name=describe(submit))
        else:
            self.driver.act("press", pw, "Enter")
            self._record("press", snap, pw, value="Enter", description="Press Enter to sign in", name=describe(pw))
        self._logged_in = True
        if blind:
            return
        if self.driver.live:
            after = self._snap()
            if any(e.get("input_type") == "password" for e in after.elements) and after.url == snap.url:
                raise Unresolved("Sign-in did not succeed with the saved credentials (the sign-in form is still shown).")
        elif isinstance(self.driver, StaticDriver):
            nxt = next((p for p in self.driver.pages if not any(e.get("input_type") == "password"
                                                                for e in p.get("elements") or [])), None)
            if nxt is not None:
                self.driver.switch(nxt["url"])

    def _credential_field(self, words: set[str]) -> str | None:
        if words & {"password"}:
            return "password"
        if words and words <= {"user", "name", "email", "login", "id", "account"} and "user" in words | {"login"}:
            return "username"
        return None

    def _do_fill(self, intent: Intent) -> None:
        words = key_words(intent.target)
        snap, fp = self._find(words, is_fillable, f"'{intent.target}' field", field_step=True)
        cred = self._credential_field(words) if intent.value is None else None
        if cred:
            real = self.credentials[0 if cred == "username" else 1]
            self.driver.act("fill", fp, real or "")
            self._record("fill", snap, fp, value="{{CRED:%s.%s}}" % (self.role, cred),
                         description=self._step.action if self._step else "", name=intent.target)
            return
        key, real = self._data(intent.target, intent.value, words)
        self.driver.act("fill", fp, real)
        self._record("fill", snap, fp, value="{{DATA:%s}}" % key, description=self._step.action if self._step else "",
                     name=intent.target)

    def _do_select(self, intent: Intent) -> None:
        words = key_words(intent.target)
        snap, fp = self._find(words, is_selectable, f"'{intent.target}' list", field_step=True)
        key, real = self._data(intent.target, intent.value, words)
        self.driver.act("select", fp, real)
        self._record("select", snap, fp, value="{{DATA:%s}}" % key, description=self._step.action if self._step else "",
                     name=intent.target)

    def _do_check(self, intent: Intent) -> None:
        snap, fp = self._find(key_words(intent.target), is_checkable, f"'{intent.target}' checkbox", field_step=True)
        self.driver.act(intent.kind, fp)
        self._record(intent.kind, snap, fp, description=self._step.action if self._step else "", name=intent.target)

    _do_uncheck = _do_check

    def _do_click(self, intent: Intent) -> None:
        snap, fp = self._find(key_words(intent.target), is_clickable, f"'{intent.target}' button or link")
        self._click(snap, fp, self._step.action if self._step else f"Click {intent.target}", intent.target)

    def _click(self, snap: Snapshot, fp: dict[str, Any], description: str, name: str | None = None) -> None:
        self.driver.act("click", fp)
        self._record("click", snap, fp, description=description, name=name or describe(fp))

    def _do_add_to_cart(self, intent: Intent) -> None:
        snap = self._snap()
        buttons = [e for e in snap.elements if is_clickable(e) and {"add", "cart"} <= words_of(e)]
        if not buttons and isinstance(self.driver, StaticDriver):
            for other in self.driver.others():
                buttons = [e for e in other.elements if is_clickable(e) and {"add", "cart"} <= words_of(e)]
                if buttons:
                    self.driver.switch(other.url)
                    snap = other
                    break
        if not buttons:
            raise Unresolved(self._missing("'Add to cart' button", snap, is_clickable))
        chosen = buttons[0]
        if intent.value:
            want = key_words(intent.value)
            ranked = sorted(buttons, key=lambda e: -len(want & (words_of(e) | set(tokens(e.get("nearby_text"))))))
            if want & (words_of(ranked[0]) | set(tokens(ranked[0].get("nearby_text")))):
                chosen = ranked[0]
        product = re.sub(r"^add[\s-]+to[\s-]+cart[\s-]*", "", chosen.get("test_id") or chosen.get("element_id") or "",
                         flags=re.I).replace("-", " ").replace("_", " ").strip()
        name = f"add {product} to cart" if product else "add to cart"
        self._click(snap, chosen, f"Add {product.title() if product else 'a product'} to the cart", name)
        self._cart_items += 1

    def _do_open_cart(self, intent: Intent) -> None:
        snap = self._snap()
        if "cart" in tokens(relative_url(snap.url, self.base_url)) and not self._has_cart_link(snap):
            return
        snap, fp = self._find({"cart"}, is_clickable, "shopping cart link", 0.75,
                              exclude=lambda e: bool(words_of(e) & {"add", "remove", "continue"}))
        self._click(snap, fp, "Open the shopping cart", "shopping cart")

    def _has_cart_link(self, snap: Snapshot) -> bool:
        return best({"cart"}, snap.elements, is_clickable, 0.75,
                    lambda e: bool(words_of(e) & {"add", "remove", "continue"})) is not None

    def _on_page(self, snap: Snapshot, words: set[str]) -> bool:
        here = set(tokens(relative_url(snap.url, self.base_url))) | set(tokens(snap.title))
        heads = [e for e in snap.elements if e.get("tag") in ("h1", "h2") or "title" in (e.get("test_id") or "")]
        for h in heads:
            here |= set(tokens(h.get("text")))
        return bool(words) and words <= here

    def _do_reach(self, intent: Intent) -> None:
        words = key_words(intent.target)
        snap = self._snap()
        if self._on_page(snap, words):
            return
        fp = best(words, snap.elements, is_clickable, 0.75, lambda e: bool(AVOID.search(describe(e))))
        if fp is not None:
            self._click(snap, fp, f"Go to {intent.target}", intent.target)
            return
        if words & {"checkout", "cart", "payment", "order", "shipping", "billing"}:
            if self._cart_items == 0 and any(is_clickable(e) and {"add", "cart"} <= words_of(e) for e in snap.elements):
                self._do_add_to_cart(Intent("add_to_cart", "cart"))
            if words != {"cart"}:
                self._do_open_cart(Intent("open_cart", "cart"))
                snap = self._snap()
                if self._on_page(snap, words):
                    return
                fp = best(words, snap.elements, is_clickable, 0.75, lambda e: bool(AVOID.search(describe(e)))) or \
                    best({"checkout"}, snap.elements, is_clickable, 0.75, lambda e: bool(AVOID.search(describe(e))))
                if fp is not None:
                    self._click(snap, fp, f"Go to {intent.target}", intent.target)
                    return
            else:
                self._do_open_cart(Intent("open_cart", "cart"))
                return
        raise Unresolved(self._missing(f"way to the '{intent.target}' page", snap, is_clickable))

    def _do_complete(self, intent: Intent) -> None:
        """Fill the form on screen and press the forward buttons until the process finishes (or 6 screens)."""
        target = key_words(intent.target)
        clicked_finish = False
        for _ in range(6):
            snap = self._snap()
            empty = [e for e in snap.elements if is_fillable(e) and not (e.get("value") or "")
                     and e.get("input_type") not in ("search",) and "search" not in words_of(e)]
            flow = f"enter {intent.target} information" if len(empty) > 1 else None
            for e in empty:
                self._flow = flow
                label = describe(e)
                key, real = self._data(label, None, key_words(label))
                self.driver.act("fill", e, real)
                self._record("fill", snap, e, value="{{DATA:%s}}" % key, description=f"Enter {label}", name=label)
            avoid = lambda e: bool(AVOID.search(describe(e)) or AVOID.search(e.get("test_id") or ""))  # noqa: E731
            button = None
            for group, finish in ((target, False), (set(ADVANCE), False), (set(FINISH), True)):
                cands = [e for e in snap.elements if is_clickable(e) and not avoid(e) and words_of(e) & group]
                if group is target:
                    cands = [e for e in cands if score(target, e) >= 0.75]
                if cands:
                    button, clicked_finish = cands[0], finish
                    break
            if button is None:
                break
            self._flow = flow if empty else None
            self._click(snap, button, f"Click {describe(button)}")
            self._flow = None
            if clicked_finish:
                break
            if not self.driver.live:
                break
        if not clicked_finish and self.driver.live:
            self.result.notes.append(f"'{self._step.action if self._step else intent.target}': no Finish/Confirm "
                                     "button was found; check the last screen of the process.")

    def _do_view(self, intent: Intent) -> None:
        words = key_words(intent.target)
        snap = self._snap()
        fp = best(words, snap.elements, lambda e: True, 0.75)
        if fp is None:
            if self._on_page(snap, words):
                self._record("expect_url", snap, None, value=relative_url(snap.url, self.base_url) or "/",
                             description=f"{intent.target.capitalize()} are shown")
                return
            raise Unresolved(self._missing(f"'{intent.target}' to view", snap, lambda e: True))
        text = " ".join(str(fp.get("text") or "").split())
        if text and len(text) <= 40 and self.driver.check_text(fp, text):
            self._record("expect_text", snap, fp, value=text, description=f"{describe(fp)} is shown",
                         name=check_name(fp))
        else:
            self._record("expect_visible", snap, fp, description=f"{describe(fp)} is shown", name=check_name(fp))

    # ---- checks --------------------------------------------------------------------------------------------

    def _verify(self, check: Check, before: Snapshot) -> None:
        snap = self._snap()
        last = next((s for s, o in zip(reversed(self.result.steps), reversed(self.result.origins))
                     if s.manual_step == (self._step.number if self._step else None) and o and s.action == "fill"), None)
        if check.kind == "value" and check.value is not None:
            words = key_words(check.target)
            fp = best(words, snap.elements, is_fillable) if words else None
            if fp is None and last is not None:
                fp = next((o[1] for s, o in zip(self.result.steps, self.result.origins) if s is last and o), None)
            if fp is None or not self.driver.check_value(fp, check.value):
                return
            key = next((k for k, v in self.result.test_data.items() if v == check.value), None)
            self._record("expect_value", snap, fp, value="{{DATA:%s}}" % key if key else check.value,
                         description=self._step.expected if self._step else "")
            return
        if check.kind == "text" and check.value:
            fp = next((e for e in snap.elements if check.value in str(e.get("text") or "")), None)
            if fp is not None:
                self._record("expect_text", snap, fp, value=check.value, description=self._step.expected)
            return
        if check.kind == "success":
            heads = [e for e in snap.elements if SUCCESS_TEXT.search(str(e.get("text") or ""))
                     and len(str(e.get("text") or "")) <= 80 and not is_clickable(e)]
            heads.sort(key=lambda e: (e.get("tag") not in ("h1", "h2", "h3"), "header" not in (e.get("test_id") or "")))
            if heads:
                text = " ".join(str(heads[0]["text"]).split())
                self._record("expect_text", snap, heads[0], value=text, description=self._step.expected,
                             name=check_name(heads[0]))
            elif snap.url != before.url:
                self._record("expect_url", snap, None, value=relative_url(snap.url, self.base_url) or "/",
                             description=self._step.expected)
            return
        if check.kind == "changed":
            old = {e["selector"]: str(e.get("text") or "") for e in before.elements}
            fresh = [e for e in snap.elements if e["selector"] not in old or old[e["selector"]] != str(e.get("text") or "")]
            want = key_words(check.target)
            fresh.sort(key=lambda e: (-len(want & words_of(e)), not ("badge" in " ".join(e.get("classes") or [])
                                                                     or "badge" in (e.get("test_id") or ""))))
            for e in fresh:
                text = " ".join(str(e.get("text") or "").split())
                if want & words_of(e) and text and len(text) <= 30 and not is_fillable(e):
                    self._record("expect_text", snap, e, value=text, description=self._step.expected, name=check_name(e))
                    return
            return
        if check.kind == "navigated":
            if snap.url.split("#")[0] != before.url.split("#")[0]:
                rel = relative_url(snap.url, self.base_url)
                if rel:
                    self._record("expect_url", snap, None, value=rel, description=self._step.expected)
                    return
            landmark = next((e for e in snap.elements if e.get("tag") in ("h1", "h2") or
                             "title" in (e.get("test_id") or "")), None) or \
                next((e for e in snap.elements if is_fillable(e) or is_clickable(e)), None)
            if landmark is not None and not any(s.action.startswith("expect") for s in self.result.steps
                                                if s.manual_step == self._step.number):
                self._record("expect_visible", snap, landmark, description=self._step.expected,
                             name=check_name(landmark))
            return
        if check.kind == "visible":
            if any(s.action.startswith("expect") for s in self.result.steps if s.manual_step == self._step.number):
                return
            want = key_words(check.target)
            fp = best(want, snap.elements, lambda e: True, 0.5)
            if fp is not None:
                self._record("expect_visible", snap, fp, description=self._step.expected, name=check_name(fp))


def merge_pages(inventory: Inventory, pages: list[dict[str, Any]]) -> None:
    """Add the pages and elements seen while walking to the element inventory (live elements win)."""
    for page in pages:
        url = page["url"]
        inventory.pages.setdefault(url, page.get("title") or "")
        for el in page.get("elements", []):
            from .builder import InventoryElement
            item = InventoryElement(url, page.get("title") or "", el)
            inventory.by_selector.setdefault(el["selector"], item)
