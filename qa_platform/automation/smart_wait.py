"""Smart Wait (23.11.0): one wait service for the recorder, the runner and the generated Python tests.

The same source is copied into every exported pytest file (between the SMART WAIT markers), so a test run outside
SkyGuard waits exactly as it does inside. It depends only on Playwright's public API and the standard library and
patches nothing: in-flight XHR/fetch requests are counted from Playwright's own ``request`` / ``requestfinished`` /
``requestfailed`` events of the browser context, and the page is read with one small script.

A wait is complete only when all of these hold for a short settle period:
  * no XHR/fetch request of the page is in flight (requests running longer than ``long_poll_s`` are long polling or
    streaming and are reported, not waited for);
  * the document is complete; jQuery has no active request; no visible ``aria-busy`` element, loader, spinner,
    progress bar, BlockUI overlay, autocomplete loading class or DataTables "processing" box;
  * the DOM has been quiet for ``dom_quiet_ms`` (a MutationObserver records the last change);
  * the kind's own condition: the element is visible / hidden, the text or value changed from what it was before
    the action (or equals the expected value), the table's rows changed, a matching network response arrived.

It never relies only on a fixed sleep, ``readyState`` or ``networkidle``. When the time runs out the result says so
(``ok`` False, ``uncertain`` True, with the reasons); the step that follows decides whether that is a failure.
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
import time
from typing import Any

# --- SMART WAIT BEGIN (copied verbatim into exported Python tests) ---------------------------------------------------

WAIT_KINDS = {
    "page_load": "Page Load",
    "ajax": "AJAX/Data Load",
    "element_visible": "Element Visible",
    "element_hidden": "Element Hidden",
    "text_change": "Text/Value Change",
    "fixed": "Fixed Delay",
    "network_response": "Network Response",
    "table_refresh": "Table Refresh",
}

SMART_WAIT_PROBE = r"""(arg) => {
  const w = window;
  if (!w.__sgWait) {
    w.__sgWait = { last: Date.now() };
    try {
      new MutationObserver(() => { w.__sgWait.last = Date.now(); })
        .observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true,
                                             attributeFilter: ["class", "style", "hidden", "aria-busy", "disabled"] });
    } catch (e) {}
  }
  const vis = (el) => {
    try {
      const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none" && Number(s.opacity) > 0.05;
    } catch (e) { return false; }
  };
  const busy = [];
  if (document.readyState !== "complete") busy.push("document is " + document.readyState);
  try { if (w.jQuery && w.jQuery.active > 0) busy.push("jQuery has " + w.jQuery.active + " active request(s)"); } catch (e) {}
  for (const el of document.querySelectorAll('[aria-busy="true"]')) { if (vis(el)) { busy.push("an element is aria-busy"); break; } }
  const LOADERS = ".dataTables_processing, .blockUI, .blockOverlay, .loading-mask, .k-loading-mask, .el-loading-mask, " +
    ".ui-autocomplete-loading, .spinner, .spinner-border, .spinner-grow, .loader, .loading, .is-loading, " +
    ".MuiCircularProgress-root, .MuiLinearProgress-root, .ant-spin-spinning, .v-progress-circular, .p-progress-spinner, " +
    "[role=progressbar]";
  for (const el of document.querySelectorAll(LOADERS)) {
    if (vis(el)) { busy.push("loading indicator " + String(el.className || el.getAttribute("role") || el.tagName).slice(0, 60)); break; }
  }
  const out = { busy, quiet_ms: Date.now() - w.__sgWait.last, url: location.href };
  if (arg && arg.selector) {
    let el = null;
    try { el = document.querySelector(arg.selector); } catch (e) { out.bad_selector = true; }
    if (el) {
      const rows = el.querySelectorAll ? el.querySelectorAll("tr, [role=row], li").length : 0;
      const text = (("value" in el && typeof el.value === "string") ? el.value + "|" : "") + (el.innerText || el.textContent || "");
      out.target = { exists: true, visible: vis(el), rows, text: text.slice(0, 4000), length: text.length };
    } else {
      out.target = { exists: false, visible: false, rows: 0, text: "", length: 0 };
    }
  }
  return out;
}"""


def _sig(target: dict | None) -> str:
    if not target:
        return ""
    raw = f"{target.get('exists')}|{target.get('rows')}|{target.get('length')}|{target.get('text')}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


class SmartWait:
    """One per browser context. ``capture`` before the action, ``wait`` after it."""

    def __init__(self, context: Any, *, long_poll_s: float = 20.0, dom_quiet_ms: int = 300, request_quiet_ms: int = 400,
                 settle_ms: int = 150) -> None:
        self.context = context
        self.long_poll_s = long_poll_s
        self.dom_quiet_ms = dom_quiet_ms
        self.request_quiet_ms = request_quiet_ms
        self.settle_ms = settle_ms
        self._inflight: dict[int, tuple[str, float]] = {}
        self._last_request_end = 0.0
        self._responses: list[tuple[float, str, int]] = []
        context.on("request", self._on_request)
        context.on("requestfinished", self._on_end)
        context.on("requestfailed", self._on_end)
        context.on("response", self._on_response)

    # Playwright events (read only)
    def _on_request(self, request: Any) -> None:
        try:
            if request.resource_type in ("xhr", "fetch"):
                self._inflight[id(request)] = (request.url, time.monotonic())
        except Exception:
            pass

    def _on_end(self, request: Any) -> None:
        if self._inflight.pop(id(request), None) is not None:
            self._last_request_end = time.monotonic()

    def _on_response(self, response: Any) -> None:
        try:
            self._responses.append((time.monotonic(), response.url, response.status))
            del self._responses[:-500]
        except Exception:
            pass

    def inflight(self) -> tuple[list[str], list[str]]:
        """(requests being waited for, long-running requests that are only reported)."""
        now = time.monotonic()
        waiting, long_running = [], []
        for url, started in list(self._inflight.values()):
            (long_running if now - started > self.long_poll_s else waiting).append(url)
        return waiting, long_running

    def _probe(self, page: Any, selector: str | None) -> dict:
        try:
            return page.evaluate(SMART_WAIT_PROBE, {"selector": selector} if selector else None)
        except Exception as exc:  # navigating, or the page is closing
            return {"busy": [f"page not readable ({str(exc).splitlines()[0][:80]})"], "quiet_ms": 0}

    def capture(self, page: Any, spec: dict | None = None) -> dict:
        """The state before the action: target text/value/rows and the time (for Network Response)."""
        spec = spec or {}
        selector = spec.get("selector")
        state = {"at": time.monotonic(), "url": None, "signature": None, "text": None}
        if selector:
            target = self._probe(page, selector).get("target")
            state.update(signature=_sig(target), text=(target or {}).get("text"))
        try:
            state["url"] = page.url
        except Exception:
            pass
        return state

    def wait(self, page: Any, spec: dict | None = None, prior: dict | None = None, *, timeout_ms: int | None = None,
             minimum_ms: int = 0) -> dict:
        """Wait for the page (and the kind's condition). Returns {ok, uncertain, kind, waited_ms, reasons}."""
        spec = dict(spec or {})
        kind = spec.get("kind") or "ajax"
        timeout_ms = int(timeout_ms or spec.get("timeout_ms") or 30000)
        minimum_ms = int(max(minimum_ms or 0, spec.get("minimum_ms") or 0))
        start = time.monotonic()
        if kind == "fixed":
            ms = int(spec.get("ms") or spec.get("value") or 1000)
            page.wait_for_timeout(ms)
            return {"ok": True, "uncertain": False, "kind": kind, "waited_ms": ms, "reasons": []}
        if kind == "page_load":
            try:
                page.wait_for_load_state("load", timeout=timeout_ms)
            except Exception as exc:
                return self._result(False, kind, start, [f"the page did not finish loading ({str(exc)[:80]})"])
        selector = spec.get("selector")
        deadline = start + timeout_ms / 1000.0
        settled_at = None
        reasons: list[str] = []
        notes: list[str] = []
        while True:
            now = time.monotonic()
            reasons = []
            waiting, long_running = self.inflight()
            if waiting:
                reasons.append(f"{len(waiting)} request(s) in flight: {waiting[0][:100]}")
            elif now - self._last_request_end < self.request_quiet_ms / 1000.0:
                reasons.append("a request just finished; waiting for the page to use it")
            probe = self._probe(page, selector if kind in ("element_visible", "element_hidden", "text_change",
                                                            "table_refresh") else None)
            reasons += probe.get("busy") or []
            if (probe.get("quiet_ms") or 0) < self.dom_quiet_ms:
                reasons.append("the page is still changing")
            reasons += self._condition(kind, spec, prior, probe, page)
            if long_running:
                notes = [f"{len(long_running)} long-running request(s) not waited for (long polling or streaming)"]
            if not reasons and now - start >= minimum_ms / 1000.0:
                settled_at = settled_at or now
                if now - settled_at >= self.settle_ms / 1000.0:
                    return self._result(True, kind, start, notes)
            else:
                settled_at = None
            if now >= deadline:
                return self._result(False, kind, start, reasons + notes)
            page.wait_for_timeout(50)

    def _condition(self, kind: str, spec: dict, prior: dict | None, probe: dict, page: Any) -> list[str]:
        target = probe.get("target") or {}
        selector = spec.get("selector")
        if probe.get("bad_selector"):
            return [f"the selector {selector!r} is not valid"]
        if kind == "element_visible" and not target.get("visible"):
            return [f"waiting for {selector} to be visible"]
        if kind == "element_hidden" and target.get("visible"):
            return [f"waiting for {selector} to disappear"]
        if kind == "text_change":
            expected = spec.get("value")
            text = target.get("text") or ""
            if expected not in (None, ""):
                if str(expected).strip().lower() not in text.lower():
                    return [f"waiting for {selector} to show {expected!r}"]
            elif prior and prior.get("text") is not None and text == prior.get("text"):
                return [f"waiting for {selector} to change"]
        if kind == "table_refresh":
            if not target.get("exists"):
                return [f"waiting for the table {selector}"]
            if prior and prior.get("signature") and _sig(target) == prior["signature"]:
                return [f"waiting for the table {selector} to refresh"]
        if kind == "network_response":
            pattern = spec.get("url") or spec.get("value") or "*"
            since = (prior or {}).get("at", 0.0)
            rx = re.compile(fnmatch.translate(pattern if any(c in pattern for c in "*?") else f"*{pattern}*"), re.I)
            if not any(t >= since and rx.match(u) for t, u, _s in self._responses):
                return [f"waiting for a response from {pattern}"]
        return []

    @staticmethod
    def _result(ok: bool, kind: str, start: float, reasons: list[str]) -> dict:
        return {"ok": ok, "uncertain": not ok, "kind": kind, "waited_ms": int((time.monotonic() - start) * 1000),
                "reasons": reasons[:6]}


def policy_applies(policy: dict | None, index: int, action: str) -> bool:
    """Does the script's global wait policy add a wait after step ``index`` (an ``action`` step)?"""
    p = policy or {}
    if not p.get("enabled") or action in ("wait", "wait_until", "screenshot", "dialog"):
        return False
    if action.startswith("expect"):
        return False  # a check already waits for its own condition
    if action == "goto" and not p.get("include_navigation", True):
        return False
    if p.get("scope") == "selected":
        return index in set(p.get("steps") or [])
    return True


def policy_wait(sw: SmartWait, page: Any, policy: dict) -> dict:
    mode = policy.get("mode") or "smart"
    timeout = int(policy.get("timeout_ms") or 30000)
    if mode == "fixed":
        return sw.wait(page, {"kind": "fixed", "ms": int(policy.get("fixed_ms") or 1000)})
    minimum = int(policy.get("minimum_ms") or 500) if mode == "smart_min" else 0
    return sw.wait(page, {"kind": "ajax"}, timeout_ms=timeout, minimum_ms=minimum)

# --- SMART WAIT END ------------------------------------------------------------------------------------------------


def embedded_source() -> str:
    """The code between the markers, for the exported Python tests (the same code the runner uses)."""
    from pathlib import Path

    text = Path(__file__).read_text(encoding="utf-8")
    start = text.index("# --- SMART WAIT BEGIN")
    end = text.index("# --- SMART WAIT END")
    return text[start:text.index("\n", end)] + "\n"


def describe_wait(spec: dict | None) -> str:
    spec = spec or {}
    kind = spec.get("kind") or "ajax"
    label = WAIT_KINDS.get(kind, kind)
    what = spec.get("selector") or spec.get("url") or ""
    if kind == "fixed":
        return f"Wait {int(spec.get('ms') or 1000)} ms (Fixed Delay)"
    if kind == "text_change" and spec.get("value"):
        return f"Smart Wait: {what} shows '{spec['value']}'"
    return f"Smart Wait: {label}" + (f" ({what})" if what else "")


def clean_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate a wait step's settings (what the step editor and the bulk tools send)."""
    from ..errors import QAError

    kind = str(spec.get("kind") or "ajax")
    if kind not in WAIT_KINDS:
        raise QAError("WAIT_KIND_INVALID", f"Unknown wait type '{kind}'. Use one of: {', '.join(WAIT_KINDS.values())}.")
    out: dict[str, Any] = {"kind": kind}
    timeout = int(spec.get("timeout_ms") or 30000)
    if not 500 <= timeout <= 300000:
        raise QAError("WAIT_TIMEOUT_INVALID", "The wait timeout must be between 0.5 and 300 seconds.")
    out["timeout_ms"] = timeout
    if kind in ("element_visible", "element_hidden", "text_change", "table_refresh"):
        sel = str(spec.get("selector") or "").strip()
        if not sel:
            raise QAError("WAIT_SELECTOR_REQUIRED", f"{WAIT_KINDS[kind]} needs the element (a CSS selector).")
        out["selector"] = sel[:500]
    if kind == "text_change" and spec.get("value") not in (None, ""):
        out["value"] = str(spec["value"])[:500]
    if kind == "network_response":
        url = str(spec.get("url") or "").strip()
        if not url:
            raise QAError("WAIT_URL_REQUIRED", "Network Response needs part of the request URL (for example /api/orders).")
        out["url"] = url[:500]
    if kind == "fixed":
        ms = int(spec.get("ms") or 1000)
        if not 50 <= ms <= 120000:
            raise QAError("WAIT_DELAY_INVALID", "A fixed delay must be between 50 ms and 120 seconds.")
        out["ms"] = ms
    if spec.get("minimum_ms"):
        out["minimum_ms"] = max(0, min(int(spec["minimum_ms"]), 60000))
    if spec.get("auto"):
        out["auto"] = True  # inserted by a bulk action (removed and updated by the bulk tools)
    return out


def clean_policy(policy: dict[str, Any] | None) -> dict[str, Any] | None:
    from ..errors import QAError

    if not policy:
        return None
    mode = policy.get("mode") or "smart"
    if mode not in ("smart", "fixed", "smart_min"):
        raise QAError("WAIT_MODE_INVALID", "The wait mode must be Smart, Fixed or Smart + Minimum.")
    timeout = int(policy.get("timeout_ms") or 30000)
    if not 500 <= timeout <= 300000:
        raise QAError("WAIT_TIMEOUT_INVALID", "The wait timeout must be between 0.5 and 300 seconds.")
    scope = policy.get("scope") or "all"
    if scope not in ("all", "selected"):
        raise QAError("WAIT_SCOPE_INVALID", "The scope must be all steps or selected steps.")
    return {"enabled": bool(policy.get("enabled")), "mode": mode, "timeout_ms": timeout,
            "minimum_ms": max(0, min(int(policy.get("minimum_ms") or 500), 60000)),
            "fixed_ms": max(50, min(int(policy.get("fixed_ms") or 1000), 120000)), "scope": scope,
            "steps": sorted({int(i) for i in policy.get("steps") or [] if int(i) >= 0}),
            "include_navigation": bool(policy.get("include_navigation", True))}


# ---- bulk tools (Insert Wait After Steps, remove, update) ------------------------------------------------------------

STEP_GROUPS = {
    "clicks": {"click", "dblclick", "right_click", "check", "uncheck", "press"},
    "dropdowns": {"select", "choose_option", "pick_date"},
    "navigation": {"goto"},
    "data": {"click", "press", "select", "choose_option"},  # actions that typically load data (search, filter, page)
}


def _targets(steps: list[Any], where: str, chosen: list[int]) -> list[int]:
    real = [i for i, st in enumerate(steps) if st.action not in ("wait", "wait_until")]
    if where == "all":
        return real
    if where == "selected":
        return [i for i in real if i in set(chosen)]
    group = STEP_GROUPS.get(where)
    if group is None:
        from ..errors import QAError

        raise QAError("WAIT_WHERE_INVALID", "Insert waits after: all, selected, clicks, dropdowns, navigation or data.")
    if where == "data":  # a click or key press that is followed by a check, or a filter / search control
        out = []
        for i in real:
            st = steps[i]
            text = " ".join(filter(None, [st.description, getattr(st.target, "description", None) if st.target else None,
                                          getattr(st.target, "text", None) if st.target else None])).lower()
            if st.action in ("select", "choose_option") or re.search(
                    r"search|filter|apply|find|load|refresh|next|previous|page|go\b|submit|show|retrieve|fetch", text):
                out.append(i)
        return out
    return [i for i in real if steps[i].action in group]


def bulk(script: Any, op: str, where: str = "all", chosen: list[int] | None = None,
         wait: dict[str, Any] | None = None) -> tuple[list[Any], dict[str, Any]]:
    """Insert, remove or update Smart Wait steps after the chosen steps. Returns (new steps, summary). A wait is never
    inserted twice after the same step (duplicate prevention)."""
    from ..errors import QAError
    from .dsl import Step

    steps = list(script.steps)
    chosen = [int(i) for i in chosen or []]
    if op == "insert":
        spec = clean_spec({**(wait or {"kind": "ajax"}), "auto": True})
        targets = set(_targets(steps, where, chosen))
        out: list[Any] = []
        added = skipped = 0
        for i, st in enumerate(steps):
            out.append(st)
            if i not in targets:
                continue
            nxt = steps[i + 1] if i + 1 < len(steps) else None
            if nxt is not None and nxt.action == "wait_until":
                skipped += 1  # a wait already follows this step
                continue
            out.append(Step(action="wait_until", wait=dict(spec), timeout_ms=min(spec["timeout_ms"], 300000)))
            added += 1
        return out, {"added": added, "skipped_existing": skipped}
    if op == "remove":
        keep, removed = [], 0
        for i, st in enumerate(steps):
            if st.action == "wait_until" and (where == "all" or i in set(chosen)) and \
                    ((wait or {}).get("include_manual") or (st.wait or {}).get("auto")):
                removed += 1
                continue
            keep.append(st)
        return keep, {"removed": removed}
    if op == "update":
        if not wait:
            raise QAError("WAIT_SPEC_REQUIRED", "Give the new wait settings.")
        changed = 0
        out = []
        for i, st in enumerate(steps):
            if st.action == "wait_until" and (where == "all" or i in set(chosen)):
                merged = clean_spec({**(st.wait or {}), **wait, "auto": (st.wait or {}).get("auto")})
                st = st.model_copy(update={"wait": merged})
                changed += 1
            out.append(st)
        return out, {"updated": changed}
    raise QAError("WAIT_OP_INVALID", "The operation must be insert, remove or update.")
