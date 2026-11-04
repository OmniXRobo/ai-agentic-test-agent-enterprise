"""Spoken actions in the Guided Browser Automation session (23.3.4, milestone 7).

Runs on the recording thread (RecordingSession._voice) against the session's own Playwright page. Every action:
* finds its element by accessible role and name, label, placeholder, test id or name/id, waiting for it while the
  page loads (up to the element timeout of the automation settings),
* acts with the visible playback cursor, then waits for the page to settle,
* checks the real result (the value now in the field, the option now selected, the page now shown) and says it.
Clicks, entries and choices are recorded by the page recorder exactly like the person's own. Values taken from the
saved application credentials are recorded as {{CRED:role.username}} / {{CRED:role.password}}, never as text, and
a password is never logged or said.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable
from urllib.parse import urlparse

from ..errors import QAError
from ..events import names
from . import visual

ACTIONS = {"click", "fill", "select", "fill_credential", "check", "uncheck", "choose", "open", "verify", "where",
           "submit", "inspect", "close_dialog"}
_MODAL = 'dialog[open], [role=dialog], [role=alertdialog], [aria-modal=true], .modal.show, .modal.in, .modal[style*="block"]'
_ERRORS = ('[aria-invalid=true], .error, .invalid-feedback, .field-error, .help-block.error, [class*="error" i], '
           '[role=alert]')
_STALE = ("not attached", "detached", "stale", "Execution context was destroyed", "Element is not attached")
_MESSAGE = ('[role=alert], [role=status], [aria-live], .alert, .toast, .notification, .message, .success, '
            '.confirmation, [class*="success" i], [class*="confirm" i], [class*="message" i], [id*="message" i]')


def _timeout(session: Any) -> float:
    try:
        return max(2.0, min(float(session.playback.get("timeout_seconds") or 10), 120.0))
    except (TypeError, ValueError):
        return 10.0


def wait_find(find: Callable[[], Any], seconds: float) -> Any:
    """Retry ``find`` until it returns an element, while the page loads; its last 'not found' error is kept."""
    end = time.monotonic() + seconds
    last: QAError | None = None
    while True:
        try:
            el = find()
            if el is not None:
                return el
        except QAError as exc:
            last = exc
        if time.monotonic() >= end:
            raise last or QAError("VOICE_TARGET_NOT_FOUND", "I could not find that on this page.", status=404)
        time.sleep(0.25)


def settle(page: Any, seconds: float = 10.0) -> None:
    """After an action: let a navigation or an asynchronous update finish."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=int(seconds * 1000))
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=1500)
    except Exception:
        pass


def _await_change(page: Any, before: tuple[str, str], seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if page.url != before[0]:
                return
        except Exception:
            return
        page.wait_for_timeout(100)


def page_name(page: Any) -> str:
    try:
        title = (page.title() or "").strip()
    except Exception:
        title = ""
    if title:
        return title
    path = urlparse(page.url).path.rstrip("/").rsplit("/", 1)[-1]
    return path or page.url


def _value_of(el: Any) -> str:
    try:
        return str(el.input_value(timeout=2000))
    except Exception:
        return ""


def _paused(page: Any, fn: Callable[[], Any]) -> Any:
    """Do something the recorder must not capture by itself (it is recorded as a rule instead)."""
    page.evaluate("() => { window.__qaRecordPaused = true; }")
    try:
        out = fn()
        page.wait_for_timeout(250)  # the page's own input/change events pass while paused
        return out
    finally:
        page.evaluate("() => { window.__qaRecordPaused = false; }")


def _credential(session: Any, page: Any, field: str) -> tuple[str | None, str]:
    """(value, role) of the saved application credential for the page's application."""
    creds = getattr(session, "creds", None)
    role = session.credential_role or "user"
    if creds is None:
        return None, role
    host = urlparse(page.url).hostname or ""
    app = None
    for row in creds.list():
        login = urlparse(row.get("login_url") or "").hostname
        if login and login == host and row["role"].lower() == role.lower():
            app = row["application"]
            break
    return creds.lookup(app or getattr(session, "application", None), role, field), role


def _watch(session: Any, page: Any) -> None:
    """Once per page: native dialogs (alert, confirm, prompt) are answered safely. An alert is acknowledged; a
    confirm or prompt is accepted only when the person confirmed the action that raised it, else cancelled."""
    if getattr(page, "_qa_dialogs", False):
        return

    def on_dialog(dialog: Any) -> None:
        session.last_dialog = {"type": dialog.type, "message": (dialog.message or "").strip()[:200],
                               "accepted": dialog.type == "alert" or bool(getattr(session, "allow_dialog", False))}
        try:
            dialog.accept() if session.last_dialog["accepted"] else dialog.dismiss()
        except Exception:
            pass

    page.on("dialog", on_dialog)
    page._qa_dialogs = True


def _dialog_note(session: Any) -> str:
    d = getattr(session, "last_dialog", None)
    session.last_dialog = None
    if not d:
        return ""
    if d["type"] == "alert":
        return f" The page showed a message: {d['message']}."
    if d["accepted"]:
        return f" The page asked: {d['message']} I answered OK, as you confirmed."
    return (f" The page asked: {d['message']} I answered Cancel, because that needs your confirmation; say it again "
            "and confirm to answer OK.")


def _again(fn: Callable[[], Any], refind: Callable[[], Any]) -> Any:
    """Run an action; when the element went stale (the page re-rendered it), find it again and retry once."""
    try:
        return fn()
    except Exception as exc:
        if not any(k in str(exc) for k in _STALE):
            raise
        refind()
        return fn()


def modal(page: Any) -> Any | None:
    """The open dialog or modal on top, when there is one: actions look inside it first."""
    loc = page.locator(_MODAL)
    try:
        for i in range(min(loc.count(), 10) - 1, -1, -1):
            el = loc.nth(i)
            if el.is_visible():
                return el
    except Exception:
        return None
    return None


def run(session: Any, page: Any, p: dict[str, Any]) -> str:
    from .recorder import field_name, find_clickable, find_field  # the recorder's element search
    from .recorder import _visual_click  # noqa: PLC2701 - the same cursor as the recorder's own actions

    action = p.get("action")
    limit = _timeout(session)
    delay = min(int(session.playback.get("visual_delay_ms", 350)), 400)
    _watch(session, page)
    session.allow_dialog = bool(p.get("confirmed"))
    session.last_dialog = None

    if action == "where":
        return f"You're on {page_name(page)}, at {page.url}."

    if action == "inspect":
        return _inspect(page)

    if action == "close_dialog":
        box = modal(page)
        if box is None:
            return "There is no dialog open."
        el = _first_visible(box.get_by_role("button", name=re.compile(r"^(close|cancel|no|dismiss|×|x)$", re.I)),
                            box.locator('[aria-label*="close" i], .close, .btn-close'))
        if el is None:
            raise QAError("VOICE_TARGET_NOT_FOUND", "I could not find how to close this dialog. Say which button to click.",
                          status=404)
        el.click(timeout=int(limit * 1000))
        settle(page, limit)
        return "Closed the dialog."

    if action == "submit":
        return _submit(session, page, limit, delay)

    if action in ("click", "open"):
        target = str(p.get("target") or "")
        before = (page.url, page_name(page))
        def find() -> Any:
            if action == "open":
                return _link(page, target)
            if target == "dropdown":
                return find_field(page, None, select=True)
            box = modal(page)  # a dialog on top: its buttons first
            if box is not None:
                inside = _first_visible(box.get_by_role("button", name=target), box.get_by_role("link", name=target),
                                        box.get_by_text(target, exact=True))
                if inside is not None:
                    return inside
            return find_clickable(page, target)

        holder = {"el": wait_find(find, limit)}
        target = _label(holder["el"]) or target  # say what was really clicked, as the page names it
        tabs = len(page.context.pages)
        _visual_click(page, holder["el"], delay)
        _again(lambda: holder["el"].click(timeout=int(limit * 1000)), lambda: holder.update(el=wait_find(find, limit)))
        _await_change(page, before, min(limit, 2.0))  # a click may start a navigation a moment later
        settle(page, limit)
        after = (page.url, page_name(page))
        note = _dialog_note(session) + _new_tab_note(page, tabs)
        if after != before and after[1] != before[1]:
            return f"Clicked {target}. The page is now {after[1]}.{note}"
        if after[0] != before[0]:
            return f"Clicked {target}. The page moved to {after[0]}.{note}"
        return f"Clicked {target}.{note}"

    if action == "fill":
        value = str(p.get("value") or "")
        if not value:  # named without a value: make sure the field is there, then ask for the value
            if not p.get("target"):
                raise QAError("VOICE_VALUE_NEEDED", "Which value should I enter, and in which field?", status=409)
            el = wait_find(lambda: find_field(page, p.get("target")), min(limit, 5))
            raise QAError("VOICE_VALUE_NEEDED", f"What should I enter in {field_name(el)}?", status=409)
        holder = {"el": wait_find(lambda: _scoped_field(page, p.get("target")), limit)}
        el = holder["el"]
        _visual_click(page, el, delay)

        def type_it() -> None:
            holder["el"].fill(value, timeout=int(limit * 1000))
            holder["el"].dispatch_event("change")

        _again(type_it, lambda: holder.update(el=wait_find(lambda: _scoped_field(page, p.get("target")), limit)))
        el = holder["el"]
        got = _value_of(el)
        name = field_name(el)
        if got != value:
            raise QAError("VOICE_VALUE_NOT_SET", f"I typed {value} in {name}, but the field now shows "
                                                 f"{got or 'nothing'}.", status=409)
        return f"Entered {value} in {name}."

    if action == "fill_credential":
        field = "password" if p.get("field") == "password" else "username"
        value, role = _credential(session, page, field)
        if not value:
            raise QAError("VOICE_CREDENTIAL_MISSING", f"There is no saved {field} for the {role} role of this "
                                                      "application. Add it in Application Credentials, with this "
                                                      "address as the login URL.", status=409)
        named = p.get("target") if p.get("target") not in (None, "", "username", "user name", "password") else None
        el = wait_find(lambda: _credential_field(page, named, field), limit)
        _visual_click(page, el, delay)
        fp = el.evaluate("el => window.__qaFp.fingerprint(el)")
        _paused(page, lambda: (el.fill(value, timeout=int(limit * 1000)), el.dispatch_event("input"),
                               el.dispatch_event("change")))
        if _value_of(el) != value:
            raise QAError("VOICE_VALUE_NOT_SET", f"The {field} could not be entered in {field_name(el)}.", status=409)
        rule = "{{CRED:" + role + "." + field + "}}"
        with session._lock:
            session.steps.append({"action": "fill", "target": fp, "value": rule,
                                  "description": f"Enter the {role} {field} (from saved credentials)"})
        session.agent.emit(names.AGENT_LOG, f"Recorded {field} of role {role} from saved credentials",
                           run_id=session.run_id, details={"steps": len(session.steps)})
        shown = "" if field == "password" else f" {value}"
        return (f"Entered the {role}{shown} {field} from the saved test credentials in {field_name(el)}. "
                f"It is recorded as the saved credential, not as text.")

    if action in ("check", "uncheck"):
        target = str(p.get("target") or "")
        el = wait_find(lambda: _toggle(page, target), limit)
        _visual_click(page, el, delay)
        (el.check if action == "check" else el.uncheck)(timeout=int(limit * 1000))
        state = el.is_checked()
        if state != (action == "check"):
            raise QAError("VOICE_VALUE_NOT_SET", f"{target} did not change.", status=409)
        return f"{'Checked' if state else 'Unchecked'} {target}."

    if action in ("select", "choose"):
        value = str(p.get("value") or "")
        named = p.get("target")
        if not value and named:  # "choose the country": ask which, when there is such a dropdown
            try:
                el = wait_find(lambda: find_field(page, named, select=True), min(limit, 3))
            except QAError:
                el = None
            if el is not None:
                options = el.evaluate("s => [...s.options].map(o => o.text.trim()).filter(t => t && !/^(select|choose|--)/i.test(t))")
                hint = f" The options are {', '.join(options[:5])}{' and more' if len(options) > 5 else ''}." if options else ""
                raise QAError("VOICE_VALUE_NEEDED", f"Which {field_name(el)}?{hint}", status=409)
            value, named = re.sub(r"\s+(category|item|entry|one|link)$", "", named), None
        el, kind = wait_find(lambda: _choice(page, named, value), limit)
        name = field_name(el)
        if kind == "select":
            label = el.evaluate("(s, v) => { const o = [...s.options].find(o => o.text.trim().toLowerCase() === v.toLowerCase())"
                                " || [...s.options].find(o => o.text.trim().toLowerCase().includes(v.toLowerCase()));"
                                " return o ? o.text.trim() : null; }", value)
            if not label:
                raise QAError("VOICE_OPTION_NOT_FOUND", f"{name} has no option {value}.", status=404)
            box = el.bounding_box()
            if box:
                visual.point(page, box, "type", name)
                visual.choose(page, el, box, label)
            el.select_option(label=label, timeout=int(limit * 1000))
            settle(page, limit)
            got = el.evaluate("s => s.selectedOptions[0] ? s.selectedOptions[0].text.trim() : ''")
            if got != label:
                raise QAError("VOICE_VALUE_NOT_SET", f"{name} still shows {got or 'nothing'}.", status=409)
            return f"Selected {label} in {name}."
        _visual_click(page, el, delay)
        if kind == "toggle":
            el.check(timeout=int(limit * 1000))
            return f"Selected {value}." if el.is_checked() else f"I clicked {value}, but it is not selected."
        el.click(timeout=int(limit * 1000))
        settle(page, limit)
        return f"Selected {value}."

    if action == "verify":
        text = (p.get("text") or "").strip()
        target = (p.get("target") or "").strip()
        el = wait_find(lambda: _message(page, text, target), limit)
        shown = re.sub(r"\s+", " ", el.inner_text(timeout=3000) or "").strip()[:160]
        fp = el.evaluate("el => window.__qaFp.fingerprint(el)")
        with session._lock:
            session.steps.append({"action": "expect_text" if text else "expect_visible", "target": fp,
                                  "value": text or None,
                                  "description": f"Verify the page shows {text}" if text else f"Verify {target or 'the message'} is visible"})
        shown = shown.rstrip(" .!")
        what = f"the {target}" if target else "the message"
        return (f"Verified: the page shows {text}." if text else f"Verified: {what} says {shown}.") + \
            " It is recorded as a check."

    raise QAError("VOICE_BROWSER_ACTION", "That browser action is not supported.")


# ---- finding elements ----------------------------------------------------------------------------------------

def _label(el: Any) -> str:
    try:
        t = re.sub(r"\s+", " ", el.evaluate("e => e.getAttribute('aria-label') || e.innerText || e.value || ''") or "").strip()
    except Exception:
        return ""
    return t if 0 < len(t) <= 40 else ""


def _first_visible(*candidates: Any) -> Any | None:
    from .recorder import _visible
    for c in candidates:
        el = _visible(c)
        if el is not None:
            return el
    return None


def _link(page: Any, name: str) -> Any:
    n = re.sub(r"\s+(page|tab|menu|section|screen)$", "", name.strip(), flags=re.I)
    el = _first_visible(page.get_by_role("link", name=n), page.get_by_role("tab", name=n),
                        page.get_by_role("menuitem", name=n), page.get_by_role("button", name=n),
                        page.get_by_test_id(n), page.get_by_text(n, exact=True))
    if el is None:
        raise QAError("VOICE_TARGET_NOT_FOUND", f"I could not find a link to {n} on this page.", status=404)
    return el


def _credential_field(page: Any, named: str | None, field: str) -> Any:
    from .recorder import find_field
    if named:
        return find_field(page, named)
    if field == "password":
        el = _first_visible(page.locator("input[type=password]"))
    else:
        el = _first_visible(page.get_by_label(re.compile(r"user ?name|e-?mail|login|user id", re.I)),
                            page.get_by_placeholder(re.compile(r"user ?name|e-?mail|login", re.I)),
                            page.locator('input[autocomplete=username], input[name*="user" i], input[id*="user" i], '
                                         'input[type=email], input[name*="login" i], input[name*="email" i]'))
    if el is None:
        raise QAError("VOICE_FIELD_NOT_FOUND", f"I could not find the {field} field on this page.", status=404)
    return el


def _toggle(page: Any, name: str) -> Any:
    el = _first_visible(page.get_by_role("checkbox", name=name), page.get_by_role("radio", name=name),
                        page.get_by_role("switch", name=name), page.get_by_label(name, exact=False),
                        page.get_by_test_id(name))
    if el is None:
        raise QAError("VOICE_TARGET_NOT_FOUND", f"I could not find a checkbox or option called {name}.", status=404)
    return el


def _choice(page: Any, named: str | None, value: str) -> tuple[Any, str]:
    """What 'select Electronics' means here: a dropdown that has that option, a radio button or checkbox with that
    label, or a clickable item (a category link, a list entry)."""
    from .recorder import find_field
    if named:
        el = find_field(page, named, select=True)
        return el, "select"
    v = value.replace('"', "")
    for i in range(min(page.locator("select").count(), 20)):
        s = page.locator("select").nth(i)
        try:
            if s.is_visible() and s.evaluate("(s, v) => [...s.options].some(o => o.text.trim().toLowerCase()"
                                             " .includes(v.toLowerCase()))", v):
                return s, "select"
        except Exception:
            continue
    el = _first_visible(page.get_by_role("radio", name=v), page.get_by_role("checkbox", name=v))
    if el is not None:
        return el, "toggle"
    el = _first_visible(page.get_by_role("option", name=v), page.get_by_role("link", name=v),
                        page.get_by_role("button", name=v), page.get_by_role("tab", name=v),
                        page.get_by_text(v, exact=True))
    if el is not None:
        return el, "click"
    raise QAError("VOICE_TARGET_NOT_FOUND", f"I could not find {value} on this page.", status=404)


def _message(page: Any, text: str, target: str) -> Any:
    if text:
        el = _first_visible(page.get_by_text(text, exact=False))
        if el is None:
            raise QAError("VOICE_VERIFY_FAILED", f"The page does not show {text}.", status=409)
        return el
    words = [w for w in re.findall(r"[a-z]+", target.lower()) if w not in ("the", "a", "message", "text", "is", "shown")]
    candidates = [page.locator(_MESSAGE)]
    if words:
        candidates.insert(0, page.locator(f'[class*="{words[0]}" i], [id*="{words[0]}" i]'))
    for c in candidates:
        try:
            n = min(c.count(), 20)
        except Exception:
            continue
        for i in range(n):
            el = c.nth(i)
            try:
                if el.is_visible() and (el.inner_text(timeout=1000) or "").strip():
                    return el
            except Exception:
                continue
    raise QAError("VOICE_VERIFY_FAILED", f"I could not find {target or 'a message'} on this page.", status=409)


def _scoped_field(page: Any, name: str | None) -> Any:
    """A field by name, inside the open dialog first."""
    from .recorder import find_field
    box = modal(page)
    if box is not None and name:
        n = str(name)
        el = _first_visible(box.get_by_label(n, exact=False), box.get_by_placeholder(n, exact=False))
        if el is not None:
            return el
    return find_field(page, name)


def _new_tab_note(page: Any, before: int) -> str:
    try:
        now = len(page.context.pages)
    except Exception:
        return ""
    if now > before:
        return " That opened a new tab. I only work in the guided session's own page, so I stayed here."
    return ""


def _submit(session: Any, page: Any, limit: float, delay: int) -> str:
    """Submit the form being filled (the one with the last used field, else the visible one) and report the outcome:
    the page that opened, the message shown, or the fields the application rejected."""
    from .recorder import _visual_click
    box = modal(page)
    scope = box if box is not None else page
    names = re.compile(r"^(submit|save|send|register|sign up|create|continue|next|confirm|apply|ok|done|place order|"
                       r"book|pay|buy|update)$", re.I)

    def find() -> Any:
        form = None
        try:
            idx = page.evaluate("() => { const f = window.__qaLastField && window.__qaLastField.form;"
                                " return f ? [...document.forms].indexOf(f) : -1; }")
            if isinstance(idx, int) and idx >= 0 and box is None:
                form = page.locator("form").nth(idx)
        except Exception:
            form = None
        for where in ([form] if form is not None else []) + [scope]:
            el = _first_visible(where.locator('button[type=submit], input[type=submit]'),
                                where.get_by_role("button", name=names))
            if el is not None:
                return el
        raise QAError("VOICE_TARGET_NOT_FOUND", "I could not find a submit button on this page. Say which button to "
                                                "click.", status=404)

    el = wait_find(find, limit)
    label = (el.get_attribute("value") or el.inner_text(timeout=2000) or "Submit").strip()[:40] or "Submit"
    before = (page.url, page_name(page))
    tabs = len(page.context.pages)
    _visual_click(page, el, delay)
    el.click(timeout=int(limit * 1000))
    _await_change(page, before, min(limit, 2.0))
    settle(page, limit)
    note = _dialog_note(session) + _new_tab_note(page, tabs)
    errors = _invalid(page)
    if errors:
        return f"I clicked {label}, but the form was not accepted: {'; '.join(errors[:3])}.{note}"
    after = (page.url, page_name(page))
    shown = _message_text(page)
    if after[0] != before[0] or after[1] != before[1]:
        return f"Submitted with {label}. The page is now {after[1]}" + (f", and it shows: {shown}" if shown else "") + f".{note}"
    if shown:
        return f"Submitted with {label}. The page shows: {shown}.{note}"
    return (f"I clicked {label}. The page did not change and shows no message, so I can't confirm it was accepted. "
            f"Say verify the page shows, and the text you expect.{note}")


def _invalid(page: Any) -> list[str]:
    try:
        return page.evaluate("""(sel) => {
            const out = [];
            const name = (el) => (el.labels && el.labels[0] && el.labels[0].innerText.trim()) || el.getAttribute('aria-label')
                || el.getAttribute('placeholder') || el.name || el.id || 'a field';
            for (const el of document.querySelectorAll('input, select, textarea')) {
                if (!el.getClientRects().length) continue;
                if (el.matches(':invalid') || el.getAttribute('aria-invalid') === 'true')
                    out.push(name(el) + (el.validationMessage ? ': ' + el.validationMessage : ' is not valid'));
            }
            for (const el of document.querySelectorAll(sel)) {
                const t = (el.innerText || '').trim();
                if (t && el.getClientRects().length && /error|invalid|required|must|not valid/i.test(t + ' ' + el.className)
                    && !out.some((o) => o.includes(t))) out.push(t.slice(0, 120));
            }
            return out.slice(0, 5);
        }""", '.error, .invalid-feedback, .field-error, [class*="error" i], [role=alert]')
    except Exception:
        return []


def _message_text(page: Any) -> str:
    loc = page.locator(_MESSAGE)
    try:
        for i in range(min(loc.count(), 20)):
            el = loc.nth(i)
            if el.is_visible():
                t = re.sub(r"\s+", " ", el.inner_text(timeout=1000) or "").strip()
                if t:
                    return t[:160].rstrip(" .!")
    except Exception:
        return ""
    return ""


def _inspect(page: Any) -> str:
    """Browser context inspection: what the page offers, so the person knows what to say."""
    info = page.evaluate("""() => {
        const vis = (el) => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
        const txt = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
        const label = (el) => (el.labels && el.labels[0] && el.labels[0].innerText.trim()) || el.getAttribute('aria-label')
            || el.getAttribute('placeholder') || el.name || '';
        const heading = [...document.querySelectorAll('h1, h2')].filter(vis).map(txt).find(Boolean) || '';
        const fields = [...document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea')]
            .filter(vis).map(label).filter(Boolean);
        const buttons = [...document.querySelectorAll('button, input[type=submit], [role=button]')].filter(vis).map(txt).filter(Boolean);
        const links = [...document.querySelectorAll('a[href]')].filter(vis).map(txt).filter(Boolean);
        return {heading, fields: [...new Set(fields)], buttons: [...new Set(buttons)], links: [...new Set(links)]};
    }""")
    parts = [f"This is {page_name(page)}" + (f", headed {info['heading']}" if info.get("heading") else "") + "."]
    for key, word in (("fields", "Fields"), ("buttons", "Buttons"), ("links", "Links")):
        items = info.get(key) or []
        if items:
            parts.append(f"{word}: {', '.join(items[:6])}{' and more' if len(items) > 6 else ''}.")
    if modal(page) is not None:
        parts.append("A dialog is open on top.")
    return " ".join(parts)
