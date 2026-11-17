"""23.10.0 Integration Comparison: Guided Browser Automation for "Add Applications -> Connect to Application".

Replaces typing navigation steps by hand. The tester presses Start Guided Recording, a maximized browser opens on the
application (through the same live session the filters use), they sign in and go to the report as they always do:
menus, the report, its filters, the next pages, a detail page. Stop & Save turns what they did into:

* steps        the navigation the extractor replays (menus, tabs, links), in the step format it already runs
* filters      the controls the tester set on the report page before the data showed (kept apart from their values,
               so the values can be changed later in the Filters panel without recording again)
* pagination   seen, but not replayed: the extractor reads every page itself (Next, numbers, Load more, scrolling),
               with duplicate and loop protection, and says when not every page could be read
* detail       a click from a table row to a detail page becomes a parameterized address (``/orders/{key}``) taken
               from the row's own key value, so the detail of every record is read, not only the one clicked
* sign-in      never recorded: password fields arrive as "[secret]" and the sign-in page's steps are dropped; at
               replay the saved login or the tester signs in (MFA, CAPTCHA are never bypassed)

Pure conversion (``events_to_recording``) is separate from the browser part so it is tested on its own. Read-only:
a click whose label looks like a change (Save, Delete, Approve ...) is never kept as a step.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from skyguard_agent import navrec as _nr

from ..errors import QAError

AGENT_JS = Path(__file__).resolve().parents[2] / "skyguard_agent" / "js" / "agent_dom.js"
MAX_EVENTS = _nr.MAX_EVENTS
_now = _nr._now
events_to_recording = _nr.events_to_recording
steps_text = _nr.steps_text


def parse_steps_text(text: str, previous: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    try:
        return _nr.parse_steps_text(text, previous)
    except _nr.NavRecordingError as exc:
        raise QAError(exc.code, exc.message, status=exc.status) from exc


class Recorder:
    """The live part: one recording per application, inside its live browser session (FilterSession)."""

    def __init__(self, app_id: str) -> None:
        self.app_id = app_id
        self.events: list[dict[str, Any]] = []
        self.active = False
        self.started_at = _now()
        self.start_url = ""
        self.lock = threading.Lock()

    def on_event(self, _source: Any, event: Any) -> None:
        if self.active and isinstance(event, dict) and len(self.events) < MAX_EVENTS:
            with self.lock:
                self.events.append(event)

    def install(self, ex: Any) -> dict[str, Any]:
        """On the session's thread: observe every page of this browser context (new pages and navigations too)."""
        ctx = ex.page.context
        if not getattr(ctx, "_sg_guided", False):
            ctx.expose_binding("__sgGuided", self.on_event)
            ctx.add_init_script(AGENT_JS.read_text(encoding="utf-8") +
                                "\n;try{window.__sgAgent.observe(e => window.__sgGuided && window.__sgGuided(e));}"
                                "catch(e){}")
            ctx._sg_guided = True
        for frame in ex.page.frames:
            try:
                frame.evaluate(AGENT_JS.read_text(encoding="utf-8") +
                               "\n;window.__sgAgent.observe(e => window.__sgGuided && window.__sgGuided(e)); true")
            except Exception:
                pass
        try:
            ex.page.evaluate("t => window.__sgAgent && window.__sgAgent.banner(t)",
                             "SkyGuard is recording your navigation. Sign in, open the report, set its filters, "
                             "then press Stop & Save in SkyGuard.")
        except Exception:
            pass
        self.start_url = ex.page.url
        self.active = True
        return {"url": ex.page.url}

    def pump(self, ex: Any) -> dict[str, Any]:
        """Let Playwright deliver the page's events (the sync API delivers them during a call)."""
        try:
            ex.page.wait_for_timeout(60)
        except Exception:
            pass
        with self.lock:
            last = self.events[-1] if self.events else None
        return {"events": len(self.events), "url": ex.page.url,
                "last": {"kind": last.get("kind"), "what": ((last.get("element") or {}).get("name")
                                                            or (last.get("element") or {}).get("label") or "")[:60]}
                if last else None}

    def finish(self, ex: Any) -> dict[str, Any]:
        self.pump(ex)
        self.active = False
        try:
            ex.page.evaluate("() => window.__sgAgent && (window.__sgAgent.banner(''), window.__sgAgent.stopObserving())")
        except Exception:
            pass
        with self.lock:
            events = list(self.events)
        rec = events_to_recording(events, start_url=self.start_url)
        rec["final_url"] = ex.page.url
        # the tables on the page the tester ended on (View Captured Tables)
        try:
            data = ex._table_page(None, all_tables=True)
            tables = []
            if data:
                tables.append({"headers": data.get("headers"), "rows": (data.get("rows") or [])[:5],
                               "row_count": data.get("rowCount") or len(data.get("rows") or []),
                               "method": data.get("method")})
                for t in data.get("others") or []:
                    tables.append({"headers": t.get("headers"), "rows": (t.get("rows") or [])[:5],
                                   "row_count": len(t.get("rows") or []), "method": t.get("method")})
            rec["tables"] = tables
        except Exception as exc:  # noqa: BLE001 - reading is best effort; the replay reads everything
            rec["tables"] = []
            rec["tables_error"] = str(exc).splitlines()[0][:200]
        return rec
