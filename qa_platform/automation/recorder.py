"""Guided Browser Automation: record a person's actions in a real browser."""

from __future__ import annotations

import base64
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..agents.base import BaseAgent
from ..clock import now
from ..errors import QAError
from ..events import names
from . import visual
from .browser import js, launch, start_playwright
from . import date_rules
from .dsl import Step, Target
from .files import safe_name


class GuidedBrowserAgent(BaseAgent):
    agent_id = "guided_browser"


def merge_step(steps: list[dict[str, Any]], step: dict[str, Any]) -> None:
    """Collapse noise: repeated fills of one field keep the last value; exact repeats are dropped."""
    if steps:
        last = steps[-1]
        same_target = (last.get("target") or {}).get("selector") == (step.get("target") or {}).get("selector")
        if same_target and last["action"] == step["action"] == "fill":
            last["value"] = step.get("value")
            return
        if same_target and last["action"] == step["action"] and last.get("value") == step.get("value") \
                and step["action"] != "click":
            return
    steps.append(step)


class RecordingSession:
    """23.3.20: the browser can open without recording ("ready"). Record starts capturing, Pause stops it while the
    person browses freely, Record again resumes. Recording more steps into a script keeps one segment per insert
    position, so one session can add steps at several places and replace broken steps."""

    def __init__(self, agent: GuidedBrowserAgent, run_id: str, start_url: str, *, browser: str = "chromium",
                 headless: bool = False, credential_role: str | None = None,
                 playback: dict[str, Any] | None = None, files_dir: Path | None = None,
                 start_paused: bool = False, script_steps: int | None = None, insert_at: int | None = None,
                 replace: int = 0, replay: Any = None, replay_to: int = 0) -> None:
        self.agent = agent
        self.files_dir = files_dir  # 23.3.17: files picked for an upload while recording
        self.converted: list[dict[str, Any]] = []
        self.run_id = run_id
        self.start_url = start_url
        self.browser = browser
        self.headless = headless
        self.credential_role = credential_role
        self.playback = playback or {}
        # 23.3.20: the steps of each Record ... Pause stretch, with the place they go in the script
        self.script_steps = script_steps  # None: a new script (one segment, recorded from the Start URL)
        self.segments: list[dict[str, Any]] = []
        self.next_at = script_steps if insert_at is None else insert_at
        self.next_replace = max(0, int(replace or 0))
        self.replay = replay
        self.replay_to = max(0, int(replay_to or 0))
        self.replay_note: str | None = None
        self.state = "ready" if start_paused else "recording"
        self._after_replay = self.state
        if self.replay and self.replay_to:
            self.state = "replaying"  # until the earlier steps have been played
        self.url: str | None = None
        self.pick: str | None = None  # 23.3.21: "text" / "visible" while the next click picks an element to check
        self.dialog_policy = "accept"  # 23.3.21: pop-ups (alert / confirm / prompt) get OK, or Cancel
        self._pages: list[Any] = []  # 23.3.21: tabs in the order they opened
        self._frame_pending: list[Any] = []  # 23.3.21: steps in iframes whose iframe chain is not worked out yet
        self._state_ver = 0
        self.steps: list[dict[str, Any]] = []  # the segment being recorded into
        if script_steps is not None and self.next_at is not None:
            self._check_position(self.next_at, self.next_replace)
        if not start_paused:
            first = [{"action": "goto", "value": start_url, "description": f"Open {start_url}"}]
            if script_steps:
                first = []  # appending: the script already opens its page
            self._new_segment(first)
        self.error: str | None = None
        self._stop = threading.Event()
        self._done = threading.Event()
        self._commands: queue.Queue[tuple[str, Any, queue.Queue[Any]]] = queue.Queue()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._main, name=f"recorder-{run_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    # ---- 23.3.20: Record / Pause / insert position -------------------------------------------------

    @property
    def recording(self) -> bool:
        return self.state == "recording"

    def _new_segment(self, steps: list[dict[str, Any]] | None = None) -> None:
        seg = {"at": self.next_at, "replace": self.next_replace if self.script_steps is not None else 0,
               "steps": steps if steps is not None else [], "url": self.url}
        self.segments.append(seg)
        self.steps = seg["steps"]

    def _check_position(self, at: int, replace: int) -> None:
        n = self.script_steps or 0
        if not 0 <= at <= n:
            raise QAError("RECORDING_POSITION", f"Insert at step must be between 1 and {n + 1}.", status=422)
        if replace and at + replace > n:
            raise QAError("RECORDING_POSITION", f"The script has only {n} steps: steps {at + 1} to {at + replace}"
                                                f" cannot be replaced.", status=422)
        for seg in self.segments:
            if seg is (self.segments[-1] if self.segments else None) and seg["at"] == at and seg["replace"] == replace:
                continue  # resuming the segment of the same position
            a, r = seg["at"], seg["replace"]
            if r and replace and a < at + replace and at < a + r:
                raise QAError("RECORDING_POSITION", f"Steps {a + 1} to {a + r} are already replaced in this "
                                                    f"recording.", status=409)
            if r and not replace and a < at < a + r:
                raise QAError("RECORDING_POSITION", f"Step {at + 1} is replaced in this recording; insert before "
                                                    f"step {a + 1} or after step {a + r}.", status=409)
            if replace and not r and at < a < at + replace:
                raise QAError("RECORDING_POSITION", f"Steps are already inserted at step {a + 1}, inside steps "
                                                    f"{at + 1} to {at + replace}.", status=409)

    def control(self, action: str, insert_at: int | None = None, replace: int | None = None) -> dict[str, Any]:
        """record: start or resume capturing (at the chosen position); pause: stop capturing, the browser stays open
        for free browsing; position: only change where the next recorded steps go."""
        if action in ("pick_text", "pick_visible", "pick_off", "dialog_ok", "dialog_cancel"):
            return self._extra_control(action)
        if action not in ("record", "pause", "position"):
            raise QAError("RECORDING_CONTROL", f"Unknown recording control {action}.", status=422)
        with self._lock:
            if self._done.is_set():
                raise QAError("RECORDING_NOT_ACTIVE", "The recording session has ended.", status=409)
            if self.state == "replaying" and action == "record":
                raise QAError("RECORDING_REPLAYING", "The earlier steps are still being played. Press Record when "
                                                     "they are done.", status=409)
            if insert_at is not None or replace is not None:
                if self.script_steps is None:
                    raise QAError("RECORDING_POSITION", "A new script is recorded in order; the insert position is "
                                                        "for Record more steps.", status=422)
                if self.recording and action != "pause":
                    raise QAError("RECORDING_POSITION", "Pause the recording before changing where steps go.",
                                  status=409)
                at = self.next_at if insert_at is None else int(insert_at)
                rp = self.next_replace if replace is None else max(0, int(replace))
                self._check_position(at, rp)
                self.next_at, self.next_replace = at, rp
            if action == "record" and not self.recording:
                self.replay_note = None
                cur = self.segments[-1] if self.segments else None
                if cur is None or (self.script_steps is not None and (cur["at"], cur["replace"]) != (self.next_at, self.next_replace)):
                    self._new_segment()
                else:
                    self.steps = cur["steps"]
                self.state = "recording"
            elif action == "pause" and self.recording:
                self.state = "paused"
                self.pick = None
            self._state_ver += 1
            info = self.bar_state()
        verb = {"record": "Recording", "pause": "Paused", "position": "Position set"}[action]
        self.agent.emit(names.AGENT_LOG, f"{verb}: {info['where']}", run_id=self.run_id, details=info)
        return info

    def _extra_control(self, action: str) -> dict[str, Any]:
        """23.3.21: click-to-check (the next click picks an element) and how pop-ups are answered."""
        with self._lock:
            if self._done.is_set():
                raise QAError("RECORDING_NOT_ACTIVE", "The recording session has ended.", status=409)
            if action.startswith("pick_") and action != "pick_off" and not self.recording:
                raise QAError("RECORDING_NOT_STARTED", "Press Record (or Resume) first: the check is recorded "
                                                       "where the steps go.", status=409)
            if action == "pick_text":
                self.pick = "text"
            elif action == "pick_visible":
                self.pick = "visible"
            elif action == "pick_off":
                self.pick = None
            elif action == "dialog_ok":
                self.dialog_policy = "accept"
            elif action == "dialog_cancel":
                self.dialog_policy = "dismiss"
            self._state_ver += 1
            return self.bar_state()

    def bar_state(self) -> dict[str, Any]:
        n = sum(len(s["steps"]) for s in self.segments)
        if self.script_steps is None:
            where = "new script"
        elif self.next_replace:
            a, r = self.next_at, self.next_replace
            where = f"replace step {a + 1}" if r == 1 else f"replace steps {a + 1} to {a + r}"
        else:
            where = f"insert at step {self.next_at + 1}"
        return {"state": self.state, "where": where, "steps": n, "at": self.next_at, "replace": self.next_replace,
                "note": self.replay_note, "pick": self.pick, "dialogs": self.dialog_policy}

    def _on_control(self, _source: Any, action: str = "state") -> dict[str, Any]:
        """The Record / Pause bar inside the browser window."""
        try:
            if action in ("record", "pause", "pick_text", "pick_visible", "pick_off", "dialog_ok", "dialog_cancel"):
                return self.control(action)
        except QAError as exc:
            return {**self.bar_state(), "error": exc.message}
        with self._lock:
            return self.bar_state()

    def _on_record(self, _source: Any, step: dict[str, Any]) -> None:
        if not isinstance(step, dict) or "action" not in step:
            return
        if step.pop("_check", False):  # 23.3.21: a click-to-check ends the picking
            with self._lock:
                self.pick = None
                self._state_ver += 1
        if not self.recording:
            return  # 23.3.20: not recording (ready / paused): the person browses freely
        step["_at"] = now().isoformat()  # a recorded time becomes "current time +/- N" from this moment
        step["_t"] = time.monotonic()
        self._place(_source, step)  # 23.3.21: the tab, and the iframes the element is in
        if step["action"] == "dblclick":
            self._on_dblclick(step)
            return
        if step["action"] == "drag_to":
            dest = step.pop("dest", None) or {}
            step["value"] = dest.get("selector")
            t = step.get("target") or {}
            a = t.get("name") or t.get("text") or t.get("label") or t.get("selector") or "the element"
            b = dest.get("name") or dest.get("text") or dest.get("label") or dest.get("selector") or "the drop place"
            step["description"] = f"Drag {str(a)[:60]} to {str(b)[:60]}"
        if step["action"] == "upload_file":
            self._on_upload(step)
            return
        if step["action"] == "picked":
            self._on_picked(step)
            return
        target = step.get("target")
        if isinstance(target, dict) and target.get("input_type") == "password":
            target["text"] = None  # never keep a typed password in the fingerprint
        if step.get("value") == "__PASSWORD__":
            role = self.credential_role or "user"
            step["value"] = "{{CRED:" + role + ".password}}"
            step["description"] = f"Enter the {role} password (from saved credentials)"
        with self._lock:
            before = len(self.steps)
            merge_step(self.steps, step)
            added = len(self.steps) > before
        if added:
            t = step.get("target") or {}
            what = t.get("name") or t.get("text") or t.get("selector") or ""
            self.agent.emit(names.AGENT_LOG, f"Recorded {step['action']} on {what[:60]}", run_id=self.run_id,
                            details={"steps": len(self.steps)})

    def _place(self, source: Any, step: dict[str, Any]) -> None:
        """23.3.21: which tab the action happened in, and the chain of iframes around the element."""
        if not isinstance(source, dict):
            return
        page, frame = source.get("page"), source.get("frame")
        if page is not None:
            if page not in self._pages:
                self._pages.append(page)
            idx = self._pages.index(page)
            if idx:
                step["tab"] = idx
        try:
            in_frame = frame is not None and frame.parent_frame is not None
        except Exception:
            in_frame = False
        if in_frame:
            # The iframe chain is worked out later, in the recorder loop: Playwright calls made inside this callback,
            # while the page is still busy with the click, would freeze the recording.
            with self._lock:
                self._frame_pending.append((frame, [step.get(k) for k in ("target", "dest") if isinstance(step.get(k), dict)]))

    def _resolve_frames(self) -> None:
        """23.3.21: give steps done inside iframes their chain of iframe selectors (outermost first)."""
        with self._lock:
            pending, self._frame_pending = self._frame_pending, []
        for frame, targets in pending:
            chain: list[str] = []
            try:
                f = frame
                while f is not None and f.parent_frame is not None:
                    chain.insert(0, frame_selector(f))
                    f = f.parent_frame
            except Exception:
                chain = []
            if chain:
                with self._lock:
                    for tgt in targets:
                        tgt["frames"] = chain
                    self._state_ver += 1

    def _on_dblclick(self, step: dict[str, Any]) -> None:
        """The two clicks of a double-click become one dblclick step."""
        sel = (step.get("target") or {}).get("selector")
        with self._lock:
            for _ in range(2):
                last = self.steps[-1] if self.steps else None
                if last and last.get("action") == "click" and (last.get("target") or {}).get("selector") == sel \
                        and step["_t"] - last.get("_t", 0) < 1.5 and last.get("tab") == step.get("tab"):
                    self.steps.pop()
            self.steps.append(step)
        self.agent.emit(names.AGENT_LOG, "Recorded double-click", run_id=self.run_id, details={"steps": len(self.steps)})

    def _on_dialog(self, dialog: Any) -> None:
        """23.3.21: a pop-up (alert / confirm / prompt). It is answered with OK (or Cancel, from the bar) and, while
        recording, a dialog step goes before the click that opened it, so playback answers it the same way."""
        how = self.dialog_policy
        try:
            message, kind = dialog.message, dialog.type
        except Exception:
            message, kind = "", "alert"
        try:
            if how == "dismiss" and kind != "alert":
                dialog.dismiss()
            else:
                how = "accept"
                dialog.accept()
        except Exception:
            pass
        if not self.recording:
            return
        step = {"action": "dialog", "value": how, "_t": time.monotonic(), "_at": now().isoformat(),
                "description": f"{'Cancel' if how == 'dismiss' else 'OK'} on the pop-up \"{message[:80]}\""}
        with self._lock:
            at = len(self.steps)
            for i in range(len(self.steps) - 1, max(-1, len(self.steps) - 4), -1):
                if self.steps[i].get("action") in ("click", "dblclick", "right_click", "press"):
                    at = i
                    if self.steps[i].get("tab") is not None:
                        step["tab"] = self.steps[i]["tab"]
                    break
            self.steps.insert(at, step)
        self.agent.emit(names.AGENT_LOG, f"Recorded {step['description']}", run_id=self.run_id)

    def _on_upload(self, step: dict[str, Any]) -> None:
        """23.3.17: a file field got files. Keep a copy for playback; the click that opened the chooser is dropped."""
        names_ = []
        for f in step.pop("files", None) or []:
            try:
                name = safe_name(f.get("name") or "file")
                data = base64.b64decode(f.get("data") or "", validate=False)
            except Exception:
                continue
            if self.files_dir is not None:
                self.files_dir.mkdir(parents=True, exist_ok=True)
                (self.files_dir / name).write_bytes(data)
            names_.append(name)
        names_ += [safe_name(n) for n in step.pop("too_large", None) or []]
        if not names_:
            return
        step["value"] = "; ".join(names_)
        t = step.get("target") or {}
        what = t.get("label") or t.get("name") or t.get("placeholder") or "the file field"
        step["description"] = f"Upload {step['value']} in {what}"
        with self._lock:
            last = self.steps[-1] if self.steps else None
            if last and last.get("action") == "click" and step["_t"] - last.get("_t", 0) < 600:
                self.steps.pop()  # it opened the file chooser; playback fills the field directly
            self.steps.append(step)
        self.agent.emit(names.AGENT_LOG, f"Recorded upload of {step['value']}", run_id=self.run_id,
                        details={"steps": len(self.steps)})

    def _on_picked(self, step: dict[str, Any]) -> None:
        """23.3.18: a date / time picker set a field. Its clicks (open, month, year, day, hour) become one step:
        pick_date with Current system date +/- N days for a date, fill with Current system time +/- h/min for a time."""
        target = step.get("target") or {}
        value = str(step.get("value") or "")
        placeholder = step.get("placeholder") or target.get("placeholder") or ""
        at = step.get("_at")
        clock = (lambda a=at: datetime.fromisoformat(a)) if at else now
        found = date_rules.detect(value, clock, placeholder) if value else None
        name = target.get("name") or target.get("label") or placeholder or "the field"
        with self._lock:
            removed = 0
            while self.steps and (self.steps[-1].get("_cal") or (
                    self.steps[-1].get("action") == "fill"
                    and (self.steps[-1].get("target") or {}).get("selector") == target.get("selector"))):
                self.steps.pop()
                removed += 1
            if not value:
                return
            new: dict[str, Any] = {"target": target, "_at": at, "_t": step.get("_t"), "_picked": True}
            own = date_rules.format_from_placeholder(placeholder)
            is_date_only = bool(found) and found["rule"].startswith("{{TODAY")
            if is_date_only:
                iso_rule = found["rule"].split(":", 1)[0] + ":%Y-%m-%d}}"
                new.update(action="pick_date", value=iso_rule, original_value=value,
                           description=f"Pick date {date_rules.label(iso_rule).split(' (')[0]} in {name}")
            elif found:
                new.update(action="fill", value=found["rule"], original_value=value,
                           description=f"Enter {found['label'].split(' (')[0]} in {name}")
            else:
                new.update(action="fill", value=value, description=f"Enter value in {name}")
            if own:
                new["field_format"] = own
            self.steps.append(new)
        self.agent.emit(names.AGENT_LOG, f"Recorded {new['description']} (from the picker, {removed} click(s) merged)",
                        run_id=self.run_id, details={"steps": len(self.steps)})

    def _on_download(self, download: Any) -> None:
        """23.3.17: the last click started a download: it becomes a download step plus a file type check."""
        if not self.recording:
            return
        try:
            suggested = download.suggested_filename or "download"
        except Exception:
            suggested = "download"
        ext = Path(suggested).suffix.lower()
        with self._lock:
            for i in range(len(self.steps) - 1, -1, -1):
                st = self.steps[i]
                if st.get("action") == "click":
                    t = st.get("target") or {}
                    what = t.get("name") or t.get("text") or t.get("label") or "the link"
                    st["action"] = "download_file"
                    st["description"] = f"Download the file ({suggested}) from {what}"
                    if ext:
                        self.steps.insert(i + 1, {"action": "expect_file_name", "value": ext,
                                                  "description": f"Verify a {ext} file was downloaded"})
                    break
                if st.get("action") in ("download_file", "goto"):
                    break
        self.agent.emit(names.AGENT_LOG, f"Recorded download of {suggested}", run_id=self.run_id)

    def _main(self) -> None:
        try:
            with self.agent.activity(f"Recording browser session from {self.start_url}", run_id=self.run_id) as act:
                act.event(names.RECORDING_STARTED, "Browser recording started. Perform the test steps in the browser window.")
                pw = start_playwright()
                try:
                    browser = launch(pw, self.browser, headless=self.headless)
                    context = browser.new_context(viewport={"width": 1366, "height": 768}, ignore_https_errors=True,
                                                  accept_downloads=True)
                    context.expose_binding("__qaRecord", self._on_record)
                    context.on("page", self._on_page)
                    context.add_init_script(js("fingerprint"))
                    context.add_init_script(js("recorder"))
                    context.add_init_script(js("calendar"))
                    # The playback cursor shows what voice commands do in the recorded browser.
                    context.add_init_script(visual.overlay_config(self.playback))
                    context.add_init_script(js("overlay"))
                    context.expose_binding("__qaControl", self._on_control)  # 23.3.20: Record / Pause bar
                    context.add_init_script(js("recbar"))
                    page = context.new_page()
                    if page not in self._pages:
                        self._on_page(page)
                    page.goto(self.start_url, wait_until="domcontentloaded", timeout=60000)
                    if self.replay and self.replay_to:
                        self._run_replay(page, act)
                    pushed, pushed_n = self._state_ver, 0
                    while not self._stop.is_set():
                        try:
                            cmd, arg, reply = self._commands.get(timeout=0.05)
                        except queue.Empty:
                            open_pages = [p for p in context.pages if not p.is_closed()]
                            if not open_pages:
                                break
                            if page.is_closed():
                                page = open_pages[-1]
                            try:
                                # lets Playwright deliver the page's recorded actions and the bar's clicks now
                                page.wait_for_timeout(100)
                            except Exception:
                                pass
                            if self._frame_pending:
                                self._resolve_frames()
                            count = sum(len(s["steps"]) for s in self.segments)
                            if pushed != self._state_ver or pushed_n != count:
                                pushed, pushed_n = self._state_ver, count
                                self._push_state(context)
                            try:
                                self.url = page.url
                            except Exception:
                                pass
                            continue
                        try:
                            if cmd == "tab":  # 23.3.21 (tests): a command in another tab, (index, cmd, arg)
                                reply.put(cmd_result(self._pages[arg[0]], arg[1], arg[2]))
                                continue
                            reply.put(self._voice_gated(page, arg) if cmd == "voice" else cmd_result(page, cmd, arg))
                        except Exception as exc:  # command failures are reported to the caller
                            reply.put(exc)
                    try:
                        self._resolve_frames()
                    except Exception:
                        pass
                    context.close()
                    browser.close()
                finally:
                    pw.stop()
                act.event(names.RECORDING_COMPLETED, f"Recording finished: {len(self.steps)} steps captured")
                act.complete(f"Recorded {len(self.steps)} steps")
        except Exception as exc:
            self.error = getattr(exc, "message", None) or str(exc).splitlines()[0][:300]
        finally:
            self._done.set()

    def _run_replay(self, page: Any, act: Any) -> None:
        """23.3.20: play the script's steps before the insert position, so the browser is on the right screen."""
        with self._lock:
            after = self._after_replay if self.state == "replaying" else self.state
            self.state, self._state_ver = "replaying", self._state_ver + 1
        self._push_state(page.context)
        try:
            self.replay(page, act, self.replay_to)
            self.replay_note = f"Steps 1 to {self.replay_to} were played. Press Record to start capturing."
        except Exception as exc:
            msg = getattr(exc, "message", None) or str(exc).splitlines()[0][:300]
            self.replay_note = f"Playing the earlier steps stopped: {msg} Go to the right screen yourself, then press Record."
        finally:
            with self._lock:
                self.state, self._state_ver = after, self._state_ver + 1
            act.event(names.AGENT_LOG, self.replay_note)
            self.url = page.url

    def _on_page(self, page: Any) -> None:
        if page in self._pages:
            return
        self._pages.append(page)
        page.on("download", self._on_download)
        page.on("dialog", self._on_dialog)  # 23.3.21

    def _push_state(self, context: Any) -> None:
        with self._lock:
            info = self.bar_state()
        for p in list(context.pages):
            try:
                p.evaluate("s => window.__qaRecBar && window.__qaRecBar.set(s)", info)
            except Exception:
                pass
            for f in list(p.frames):  # 23.3.21: click-to-check works inside iframes too
                try:
                    f.evaluate("v => { window.__qaPick = v; }", info["pick"])
                except Exception:
                    pass

    def _voice_gated(self, page: Any, p: dict[str, Any]) -> str:
        """A spoken action while paused still happens in the browser but is not recorded (23.3.20)."""
        if self.recording:
            return self._voice(page, p)
        steps = self.steps
        before = len(steps)
        try:
            return self._voice(page, p)
        finally:
            with self._lock:
                del steps[before:]

    def command(self, cmd: str, arg: Any = None, timeout: float = 30.0) -> Any:
        """Run a Playwright command on the recording thread (used by tests and 'add assertion')."""
        if self._done.is_set():
            raise QAError("RECORDING_NOT_ACTIVE", "The recording session has ended.", status=409)
        reply: queue.Queue[Any] = queue.Queue()
        self._commands.put((cmd, arg, reply))
        result = reply.get(timeout=timeout)
        if isinstance(result, Exception):
            raise result
        return result

    # ---- voice commands during the recording -------------------------------------------------------

    def _voice(self, page: Any, p: dict[str, Any]) -> str:
        """Do one spoken browser action with the visible cursor. Clicks, entries and choices are recorded by the
        page recorder like the person's own; a date is recorded as a rule (Current Date +/- N days) computed at
        playback, never as the literal date."""
        action = p.get("action")
        delay = min(int(self.playback.get("visual_delay_ms", 350)), 400)
        from . import guided_voice  # 23.3.4: waits for elements, checks each result, credentials, checks, verify
        if action in guided_voice.ACTIONS:
            return guided_voice.run(self, page, p)
        if action == "scroll":
            page.mouse.wheel(0, 500 if p.get("value") != "up" else -500)
            return f"Scrolled {p.get('value') or 'down'}."
        if action == "press":
            page.keyboard.press(str(p.get("value") or "Enter"))
            return f"Pressed {p.get('value') or 'Enter'}."
        if action in ("select_date", "open_calendar"):
            named = p.get("target") if p.get("target") not in ("calendar", "date picker", "datepicker") else None
            field = find_field(page, named, date_like=True)
            name = field_name(field)
            if action == "open_calendar":
                page.evaluate("() => { window.__qaRecordPaused = true; }")
                try:
                    _visual_click(page, field, delay)
                    field.click(timeout=5000)
                finally:
                    page.evaluate("() => { window.__qaRecordPaused = false; }")
                return f"The calendar of {name} is open. Say which date to select, for example select tomorrow."
            days = int(p.get("days") or 0)
            rule = "{{TODAY" + (f"{days:+d}d" if days else "") + ":%Y-%m-%d}}"
            fp = field.evaluate("el => window.__qaFp.fingerprint(el)")
            page.evaluate("() => { window.__qaRecordPaused = true; }")
            try:
                from .dsl import ValueResolver

                how = visual.pick_date(page, field, ValueResolver().resolve(rule) or "", 10000, True, delay)
                page.wait_for_timeout(300)  # let the page's own change events pass while recording is paused
            finally:
                page.evaluate("() => { window.__qaRecordPaused = false; }")
            when = "Current Date" + (f" {'+' if days > 0 else '-'} {abs(days)} day{'s' if abs(days) != 1 else ''}" if days else "")
            with self._lock:
                self.steps.append({"action": "pick_date", "target": fp, "value": rule,
                                   "description": f"Pick date {when} in {name}"})
            self.agent.emit(names.AGENT_LOG, f"Recorded date rule {when} for {name} ({how})", run_id=self.run_id,
                            details={"steps": len(self.steps)})
            return f"{when} selected in {name}. Recorded as a rule, so playback uses the date of the day it runs."
        if action == "fill":
            field = find_field(page, p.get("target"))
            _visual_click(page, field, delay)
            field.fill(str(p.get("value") or ""))
            field.dispatch_event("change")
            return f"Entered {p.get('value')} in {field_name(field)}."
        if action == "select":
            field = find_field(page, p.get("target"), select=True)
            box = field.bounding_box()
            if box:
                visual.point(page, box, "type", field_name(field))
                visual.choose(page, field, box, str(p.get("value") or ""))
            try:
                field.select_option(label=str(p.get("value") or ""), timeout=3000)
            except Exception:
                field.select_option(value=str(p.get("value") or ""), timeout=3000)
            return f"Selected {p.get('value')} in {field_name(field)}."
        if action in ("click", "next_page"):
            target = "next" if action == "next_page" else str(p.get("target") or "")
            if target in ("dropdown",):
                el = find_field(page, None, select=True)
            else:
                el = find_clickable(page, target)
            _visual_click(page, el, delay)
            el.click(timeout=5000)
            return f"Clicked {target if action == 'click' else 'next page'}."
        raise QAError("VOICE_BROWSER_ACTION", "That browser action is not supported.")

    def add_assertion(self, selector: str, text: str | None) -> None:
        if not self.segments:
            raise QAError("RECORDING_NOT_STARTED", "Press Record first: the check is added where the steps go.",
                          status=409)
        fp = self.command("fingerprint", selector)
        step = {"action": "expect_text" if text else "expect_visible", "target": fp, "value": text}
        with self._lock:
            self.steps.append(step)

    def stop(self, timeout: float = 20.0) -> list[Step]:
        self._stop.set()
        self._done.wait(timeout)
        if self.error and sum(len(s["steps"]) for s in self.segments) <= 1:
            raise QAError("RECORDING_FAILED", f"Recording failed: {self.error}", status=502)
        return self.to_steps()

    def to_steps(self) -> list[Step]:
        """All recorded steps in recording order (a new script; the live preview)."""
        out: list[Step] = []
        for seg in self.segment_steps():
            out += seg["steps"]
        return out

    def segment_steps(self) -> list[dict[str, Any]]:
        """23.3.20: each Record ... Pause stretch with where it goes: at (0-based script position), replace (count)."""
        with self._lock:
            segs = [{"at": s["at"], "replace": s["replace"], "url": s.get("url"), "raw": [dict(x) for x in s["steps"]]}
                    for s in self.segments]
        self.converted = []
        out = []
        for seg in segs:
            if not seg["raw"] and not seg["replace"]:
                continue
            out.append({"at": seg["at"], "replace": seg["replace"], "url": seg["url"],
                        "steps": self._convert(seg["raw"])})
        return out

    def _convert(self, raw: list[dict[str, Any]]) -> list[Step]:
        out = []
        # 23.3.17: a typed date or time becomes a rule (Current system date +/- N days, current time +/- N h/min),
        # measured from the moment it was typed, so the script still works on another day.
        for i, s in enumerate(raw):
            if s.get("action") in date_rules.VALUE_ACTIONS and not s.get("original_value"):
                at = s.get("_at")
                clock = (lambda a=at: datetime.fromisoformat(a)) if at else now
                found = date_rules.detect(s.get("value"), clock,
                                          s.get("placeholder") or (s.get("target") or {}).get("placeholder"))
                if found:
                    s["original_value"], s["value"] = s.get("value"), found["rule"]
                    self.converted.append({"index": i, **found})
        for s in raw:
            target = Target.model_validate(s["target"]) if s.get("target") else None
            desc = s.get("description")
            if not desc and target:
                name = target.name or target.text or target.label or target.placeholder or target.selector
                desc = {"click": f"Click {name}", "fill": f"Enter value in {name}", "select": f"Select {s.get('value')} in {name}",
                        "check": f"Check {name}", "uncheck": f"Uncheck {name}", "press": f"Press {s.get('value')} in {name}",
                        "expect_text": f"Verify {name} shows {s.get('value')}", "expect_visible": f"Verify {name} is visible"
                        }.get(s["action"], s["action"])
            if target:
                target.description = target.description or (target.name or target.text or target.label or None)
            if s.get("original_value") and desc and not s.get("_picked"):
                rule_label = date_rules.label(s.get("value"))
                if rule_label and rule_label not in desc:
                    desc = f"{desc} ({rule_label})"
            out.append(Step(action=s["action"], target=target, value=s.get("value"), description=desc,
                            original_value=s.get("original_value"), tab=s.get("tab")))
        return out

    @property
    def active(self) -> bool:
        return not self._done.is_set()


def frame_selector(frame: Any) -> str:
    """23.3.21: a selector for an iframe element in its parent document: id, name, title, then src."""
    el = frame.frame_element()
    for attr, fmt in (("id", '#{}'), ("name", 'iframe[name="{}"]'), ("title", 'iframe[title="{}"]')):
        v = el.get_attribute(attr)
        if v and '"' not in v and (attr != "id" or v.replace("-", "").replace("_", "").isalnum()):
            return fmt.format(v)
    src = el.get_attribute("src") or ""
    if src and '"' not in src:
        tail = src.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        if tail:
            return f'iframe[src*="{tail}"]'
    try:
        return el.evaluate("el => window.__qaFp ? window.__qaFp.fingerprint(el).selector : 'iframe'") or "iframe"
    except Exception:
        return "iframe"


def cmd_result(page: Any, cmd: str, arg: Any) -> Any:
    if cmd == "fingerprint":
        return page.locator(arg).first.evaluate("el => window.__qaFp.fingerprint(el)")
    if cmd == "click":
        page.locator(arg).first.click()
        return True
    if cmd == "upload":  # 23.3.17 (tests): choose files in a file field, as a person does in the file dialog
        selector, paths = arg
        page.locator(selector).first.set_input_files(paths)
        return True
    if cmd == "fill":
        selector, value = arg
        page.locator(selector).first.fill(value)
        page.locator(selector).first.dispatch_event("change")
        return True
    if cmd == "select":
        selector, value = arg
        page.locator(selector).first.select_option(value)
        return True
    if cmd == "url":
        return page.url
    if cmd == "eval":  # tests
        return page.evaluate(arg)
    if cmd == "text":  # 23.3.20 (tests)
        return page.locator(arg).first.inner_text()
    if cmd in ("dblclick", "rightclick", "hover"):  # 23.3.21 (tests): as a person does it
        loc = page.locator(arg).first
        if cmd == "dblclick":
            loc.dblclick()
        elif cmd == "rightclick":
            loc.click(button="right")
        else:
            loc.hover()
        return True
    if cmd == "drag":  # 23.3.21 (tests)
        page.locator(arg[0]).first.drag_to(page.locator(arg[1]).first)
        return True
    if cmd == "frame_click":  # 23.3.21 (tests)
        page.frame_locator(arg[0]).locator(arg[1]).first.click()
        return True
    if cmd == "screenshot":  # 23.3.20 (tests)
        page.screenshot(path=str(arg))
        return True
    raise ValueError(f"Unknown recorder command {cmd}")


# ---- finding what a spoken command names ---------------------------------------------------------

def _visible(locator: Any) -> Any | None:
    try:
        n = min(locator.count(), 10)
    except Exception:
        return None
    for i in range(n):
        el = locator.nth(i)
        try:
            if el.is_visible():
                return el
        except Exception:
            continue
    return None


def _visual_click(page: Any, el: Any, delay: int) -> None:
    try:
        box = el.bounding_box(timeout=3000)
        if box:
            visual.point(page, box, "click", field_name(el))
            page.wait_for_timeout(delay)
            visual.click(page, box)
    except Exception:
        pass  # the cursor is a convenience; the action itself still runs


def field_name(el: Any) -> str:
    try:
        return el.evaluate("""el => (el.labels && el.labels[0] && el.labels[0].innerText.trim()) || el.getAttribute('aria-label')
            || el.getAttribute('placeholder') || el.getAttribute('name') || el.id || (el.innerText || '').trim().slice(0, 40)
            || el.tagName.toLowerCase()""") or "the field"
    except Exception:
        return "the field"


def find_field(page: Any, name: str | None, *, date_like: bool = False, select: bool = False) -> Any:
    """The field a spoken command means: by its label, placeholder or name; else the last one used; else the only
    date field (for dates) or the first visible dropdown."""
    if name:
        n = str(name)
        candidates = [page.get_by_label(n, exact=False), page.get_by_placeholder(n, exact=False)]
        css = "select" if select else "input, textarea, select"
        safe = n.replace('"', "")
        candidates += [page.locator(f'{css}[name*="{safe}" i]'), page.locator(f'{css}[id*="{safe}" i]')]
        for c in candidates:
            el = _visible(c)
            if el:
                return el
        raise QAError("VOICE_FIELD_NOT_FOUND", f"I could not find a field called {n} on this page.", status=404)
    kind = "SELECT" if select else ""
    path = page.evaluate(f"""() => {{
        const f = window.__qaLastField;
        if (!f || !f.isConnected || !f.getClientRects().length || ('{kind}' && f.tagName !== '{kind}')) return null;
        const parts = [];
        for (let el = f; el && el.nodeType === 1 && el !== document.documentElement; el = el.parentElement) {{
            const same = el.parentElement ? [...el.parentElement.children].filter((c) => c.tagName === el.tagName) : [el];
            parts.unshift(el.tagName.toLowerCase() + (same.length > 1 ? `:nth-of-type(${{same.indexOf(el) + 1}})` : ""));
        }}
        return "html > " + parts.join(" > ");
    }}""")
    if path:
        return page.locator(path).first
    if date_like:
        el = _visible(page.locator('input[type=date], input[name*="date" i], input[id*="date" i], '
                                   'input[placeholder*="dd" i], input[placeholder*="date" i], input[class*="date" i]'))
        if el:
            return el
        raise QAError("VOICE_FIELD_NOT_FOUND", "Which date field? Click it first, or say the field name, "
                                               "for example select tomorrow in departure date.", status=404)
    el = _visible(page.locator("select")) if select else None
    if el:
        return el
    raise QAError("VOICE_FIELD_NOT_FOUND", "Which field? Click it first, or say its name.", status=404)


def find_clickable(page: Any, name: str) -> Any:
    if name == "next":
        for c in (page.get_by_role("link", name="Next"), page.get_by_role("button", name="Next"),
                  page.locator('a[rel=next], [aria-label*="next page" i], [aria-label="Next" i]'), page.get_by_text("Next", exact=True)):
            el = _visible(c)
            if el:
                return el
        raise QAError("VOICE_TARGET_NOT_FOUND", "There is no next page control on this page.", status=404)
    for c in (page.get_by_role("button", name=name), page.get_by_role("link", name=name), page.get_by_role("tab", name=name),
              page.get_by_role("menuitem", name=name), page.get_by_label(name, exact=False), page.get_by_text(name, exact=True),
              page.get_by_text(name)):
        el = _visible(c)
        if el:
            return el
    raise QAError("VOICE_TARGET_NOT_FOUND", f"I could not find {name} on this page.", status=404)
