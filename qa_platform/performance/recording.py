"""23.3.24: record a browser session into a performance script, with automatic correlation.

The recorder opens a real browser (Playwright) and listens to the network traffic the pages make; there is no proxy
and no certificate to install. Each request, its response and its timing are kept in memory while recording. Steps
(JMeter transactions) start when the person names one, or on each new page when automatic steps are on.

Building the script:

* Only the target's requests are kept: hosts outside the allowed hosts (analytics, fonts, CDNs) and static files
  (images, styles, scripts, fonts) are left out by default and listed with the reason.
* Think time is the pause between steps, as recorded, on the first request of each step.
* Dynamic values (CSRF tokens, session ids, OAuth tokens): a value sent in a request that came back in an earlier
  response is extracted from that response (JSON extractor for JSON, boundary extractor otherwise) and replaced by a
  variable everywhere it is sent later. Each extractor is checked against the recorded response before it is used.
* Secrets: what was typed in password fields, and Authorization headers that no response explains, are replaced by
  ``${__P(name)}`` and kept in the Windows Credential Manager, never in the script, the run folder or the logs.
* Each request checks the status code it got while recording, so a broken correlation fails the run visibly.

Recording sends only the person's own browsing: it never starts a load test.
"""

from __future__ import annotations

import base64
import json
import queue
import re
import threading
import time
import uuid
from typing import Any
from urllib.parse import parse_qsl, quote, quote_plus, urlsplit

from ..errors import QAError
from . import script as jscript
from .jmx import _a, _b, _s

KEEP_TYPES = {"document", "xhr", "fetch"}
STATIC_TYPES = {"image", "stylesheet", "font", "script", "media", "texttrack", "manifest"}
NOT_HTTP = {"websocket", "eventsource"}
STATIC_EXT = re.compile(r"\.(?:js|mjs|css|png|jpe?g|gif|svg|ico|webp|avif|woff2?|ttf|otf|eot|map|mp4|webm|mp3)$", re.I)
TEXT_TYPES = re.compile(r"json|text|xml|javascript|html|x-www-form-urlencoded", re.I)
MAX_BODY = 2 * 1024 * 1024
MAX_ENTRIES = 5000
MAX_THINK_S = 30.0
# Headers JMeter sets itself, or that the cookie manager and connection handle
DROP_HEADERS = {"cookie", "content-length", "host", "connection", "accept-encoding", "upgrade-insecure-requests",
                "keep-alive", "te", "priority", "pragma", "cache-control", "if-none-match", "if-modified-since"}
GLOBAL_HEADERS = ("user-agent", "accept-language")
HINT = re.compile(r"(token|csrf|xsrf|nonce|session|sid|state|viewstate|eventvalidation|execution|auth|ticket|code|key|"
                  r"guid|uuid|_id|id)$|^_", re.I)

PASSWORD_JS = """
(() => {
  if (window.__qaPerfSecrets) return;
  window.__qaPerfSecrets = true;
  const send = (el) => {
    if (el && el.tagName === "INPUT" && el.type === "password" && el.value && window.__qaPerfSecret)
      window.__qaPerfSecret(el.name || el.id || "password", el.value);
  };
  const all = () => document.querySelectorAll('input[type="password"]').forEach(send);
  document.addEventListener("change", (e) => send(e.target), true);
  document.addEventListener("submit", all, true);
  document.addEventListener("click", all, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Enter") all(); }, true);
})();
"""


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _var_name(raw: str, taken: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", raw or "value").strip("_").lower() or "value"
    if base[0].isdigit():
        base = "v_" + base
    base = base[:40]
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


class PerfRecording:
    """One browser recording session. Playwright runs on its own thread; the API thread sends commands."""

    def __init__(self, start_url: str, *, allowed_hosts: list[str] | None = None, include_static: bool = False,
                 auto_steps: bool = True, browser: str = "chromium", headless: bool = False) -> None:
        if not start_url.lower().startswith(("http://", "https://")):
            raise QAError("VALIDATION_FAILED", "The start URL must begin with http:// or https://.", status=422)
        self.id = "REC-" + uuid.uuid4().hex[:8].upper()
        self.start_url = start_url
        self.allowed_hosts = sorted({h.strip().lower() for h in (allowed_hosts or []) if h.strip()}
                                    or {_host(start_url)})
        self.include_static = include_static
        self.auto_steps = auto_steps
        self.browser = browser
        self.headless = headless
        self.entries: list[dict[str, Any]] = []
        self.steps: list[dict[str, Any]] = []
        self.secrets: dict[str, str] = {}  # field name -> value typed in a password field (memory only)
        self.state = "starting"
        self.error: str | None = None
        self.url: str | None = None
        self.started_at = time.time()
        self._t0 = time.monotonic()
        self._pending: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._done = threading.Event()
        self._ready = threading.Event()
        self._commands: queue.Queue[tuple[str, Any, queue.Queue[Any]]] = queue.Queue()
        self._new_step(f"Open {urlsplit(start_url).path or '/'}", user=False)
        self._thread = threading.Thread(target=self._main, name=f"perf-recorder-{self.id}", daemon=True)

    # ---- control ----------------------------------------------------------------------------------------------

    def start(self, timeout: float = 90.0) -> None:
        self._thread.start()
        self._ready.wait(timeout)
        if self.error:
            raise QAError("RECORDING_FAILED", f"The browser could not start: {self.error}", status=502)

    def _new_step(self, name: str, *, user: bool) -> dict[str, Any]:
        step = {"index": len(self.steps), "name": name[:120] or f"Step {len(self.steps) + 1}", "user": user,
                "started_s": round(time.monotonic() - self._t0, 3), "has_document": False}
        self.steps.append(step)
        return step

    def mark(self, name: str) -> dict[str, Any]:
        """Start a new step (transaction) with this name; the requests that follow belong to it. An empty current step
        is renamed instead."""
        name = (name or "").strip()
        if not name:
            raise QAError("VALIDATION_FAILED", "Name the step, e.g. Login.", status=422)
        with self._lock:
            cur = self.steps[-1]
            if not any(e["step"] == cur["index"] for e in self.entries) and not self._pending:
                cur.update(name=name[:120], user=True)
                return cur
            return self._new_step(name, user=True)

    def command(self, cmd: str, arg: Any = None, timeout: float = 60.0) -> Any:
        """Run a browser command on the recorder's thread (goto, click, fill, press, wait, eval). Used by tests and
        voice; the person normally uses the browser window directly."""
        if self._done.is_set():
            raise QAError("RECORDING_NOT_ACTIVE", "The recording browser is closed.", status=409)
        reply: queue.Queue[Any] = queue.Queue()
        self._commands.put((cmd, arg, reply))
        try:
            out = reply.get(timeout=timeout)
        except queue.Empty as exc:
            raise QAError("RECORDING_TIMEOUT", f"The browser did not finish {cmd} in time.", status=504) from exc
        if isinstance(out, Exception):
            raise out
        return out

    def stop(self, timeout: float = 30.0) -> None:
        self._stop.set()
        self._done.wait(timeout)
        with self._lock:
            self.state = "stopped"

    @property
    def active(self) -> bool:
        return self._thread.is_alive() and not self._done.is_set()

    # ---- browser thread ----------------------------------------------------------------------------------------

    def _main(self) -> None:
        from ..automation.browser import context_options, launch, start_playwright
        pw = None
        try:
            pw = start_playwright()
            browser = launch(pw, self.browser, headless=self.headless)
            context = browser.new_context(**context_options(self.headless, ignore_https_errors=False))
            context.expose_binding("__qaPerfSecret", self._on_secret)
            context.add_init_script(PASSWORD_JS)
            context.on("request", self._on_request)
            context.on("requestfinished", self._on_finished)
            context.on("requestfailed", self._on_failed)
            page = context.new_page()
            with self._lock:
                self.state = "recording"
            self._ready.set()
            try:
                page.goto(self.start_url, wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:  # the person can still type another address in the window
                self.error = None
                self.url = self.start_url
                with self._lock:
                    self.steps[-1]["note"] = f"Opening the start URL failed: {str(exc).splitlines()[0][:200]}"
            while not self._stop.is_set():
                try:
                    cmd, arg, reply = self._commands.get(timeout=0.05)
                except queue.Empty:
                    pages = [p for p in context.pages if not p.is_closed()]
                    if not pages:
                        break  # the person closed the browser window
                    if page.is_closed():
                        page = pages[-1]
                    try:
                        page.wait_for_timeout(100)  # lets Playwright deliver the network events now
                        self.url = page.url
                    except Exception:
                        pass
                    continue
                try:
                    reply.put(self._run(page, cmd, arg))
                except Exception as exc:
                    reply.put(QAError("RECORDING_COMMAND_FAILED", str(exc).splitlines()[0][:300], status=422))
            try:
                page.wait_for_timeout(300)  # last responses
            except Exception:
                pass
            context.close()
            browser.close()
        except Exception as exc:
            self.error = getattr(exc, "message", None) or str(exc).splitlines()[0][:300]
            self._ready.set()
        finally:
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass
            with self._lock:
                self.state = "stopped"
            self._done.set()

    @staticmethod
    def _run(page: Any, cmd: str, arg: Any) -> Any:
        if cmd == "goto":
            page.goto(arg, wait_until="domcontentloaded", timeout=60000)
        elif cmd == "click":
            page.click(arg, timeout=30000)
        elif cmd == "fill":
            page.fill(arg[0], arg[1], timeout=30000)
        elif cmd == "press":
            page.press(arg[0], arg[1], timeout=30000)
        elif cmd == "wait":
            page.wait_for_load_state("networkidle", timeout=float(arg or 30000))
        elif cmd == "wait_text":
            page.wait_for_selector(f"text={arg}", timeout=30000)
        elif cmd == "eval":
            return page.evaluate(arg)
        else:
            raise ValueError(f"Unknown command {cmd}")
        return page.url

    def _on_secret(self, _source: Any, name: str, value: str) -> None:
        if value:
            with self._lock:
                self.secrets[str(name)[:60] or "password"] = str(value)

    def _on_request(self, request: Any) -> None:
        now = round(time.monotonic() - self._t0, 3)
        try:
            rtype = request.resource_type
            nav = request.is_navigation_request() and request.frame == request.frame.page.main_frame
        except Exception:
            rtype, nav = "other", False
        with self._lock:
            if len(self.entries) + len(self._pending) >= MAX_ENTRIES:
                return
            step = self.steps[-1]
            if nav and rtype == "document" and request.redirected_from is None:
                if self.auto_steps and step["has_document"]:
                    path = urlsplit(request.url).path or "/"
                    step = self._new_step(f"Open {path}", user=False)
                step["has_document"] = True
            self._pending[id(request)] = {"step": step["index"], "started_s": now, "resource_type": rtype,
                                          "navigation": nav}

    def _entry(self, request: Any, response: Any, error: str | None) -> None:
        with self._lock:
            meta = self._pending.pop(id(request), None)
        if meta is None:
            return
        try:
            headers = request.all_headers()
        except Exception:
            headers = dict(request.headers)
        try:
            post = request.post_data
        except Exception:  # binary body
            try:
                raw = request.post_data_buffer
                post = None if raw is None else "base64:" + base64.b64encode(raw).decode()
            except Exception:
                post = None
        e: dict[str, Any] = {"method": request.method, "url": request.url, "host": _host(request.url),
                             "req_headers": {k.lower(): v for k, v in headers.items()}, "post_data": post,
                             "status": None, "resp_headers": {}, "body": None, "error": error,
                             "ended_s": round(time.monotonic() - self._t0, 3), **meta}
        if response is not None:
            try:
                e["status"] = response.status
                e["resp_headers"] = {k.lower(): v for k, v in response.all_headers().items()}
            except Exception:
                pass
            ctype = e["resp_headers"].get("content-type", "")
            if e["status"] and not 300 <= e["status"] < 400 and TEXT_TYPES.search(ctype) \
                    and e["resource_type"] not in ("image", "font", "media"):
                try:
                    body = response.body()
                    if len(body) <= MAX_BODY:
                        e["body"] = body.decode("utf-8", "replace")
                except Exception:
                    pass
        with self._lock:
            e["id"] = len(self.entries) + 1
            self.entries.append(e)

    def _on_finished(self, request: Any) -> None:
        try:
            response = request.response()
        except Exception:
            response = None
        self._entry(request, response, None)

    def _on_failed(self, request: Any) -> None:
        try:
            err = request.failure or "failed"
        except Exception:
            err = "failed"
        self._entry(request, None, str(err)[:200])

    # ---- views ------------------------------------------------------------------------------------------------

    def view(self) -> dict[str, Any]:
        with self._lock:
            entries = sorted(self.entries, key=lambda e: e["started_s"])
            steps = []
            for s in self.steps:
                mine = [e for e in entries if e["step"] == s["index"]]
                steps.append({"index": s["index"], "name": s["name"], "user": s["user"], "started_s": s["started_s"],
                              "requests": len(mine),
                              "kept": sum(1 for e in mine if classify(e, self.allowed_hosts, self.include_static)[0]),
                              **({"note": s["note"]} if s.get("note") else {})})
            return {"id": self.id, "state": self.state, "active": self.active, "start_url": self.start_url,
                    "url": self.url, "allowed_hosts": self.allowed_hosts, "include_static": self.include_static,
                    "auto_steps": self.auto_steps, "error": self.error, "steps": steps,
                    "requests": len(entries), "secret_fields": sorted(self.secrets),
                    "elapsed_s": round(time.monotonic() - self._t0)}

    def snapshot(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
        with self._lock:
            return (sorted((dict(e) for e in self.entries), key=lambda e: e["started_s"]),
                    [dict(s) for s in self.steps], dict(self.secrets))


# ---- HAR import -----------------------------------------------------------------------------------------------------

def entries_from_har(har: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Entries and steps from a HAR file (exported from the browser's developer tools). Pages become steps."""
    log = har.get("log") if isinstance(har, dict) else None
    if not isinstance(log, dict) or not isinstance(log.get("entries"), list):
        raise QAError("HAR_INVALID", "This is not a HAR file: it has no log.entries.", status=422)
    from datetime import datetime

    def ts(value: str) -> float:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (ValueError, AttributeError):
            return 0.0
    raw = log["entries"][:MAX_ENTRIES]
    t0 = min((ts(e.get("startedDateTime", "")) for e in raw), default=0.0)
    pages = {p.get("id"): p for p in log.get("pages") or [] if isinstance(p, dict)}
    order = sorted(pages, key=lambda pid: ts(pages[pid].get("startedDateTime", "")))
    steps = [{"index": i, "name": (pages[pid].get("title") or f"Page {i + 1}")[:120], "user": False,
              "started_s": round(ts(pages[pid].get("startedDateTime", "")) - t0, 3)} for i, pid in enumerate(order)]
    if not steps:
        steps = [{"index": 0, "name": "Recorded requests", "user": False, "started_s": 0.0}]
    index_of = {pid: i for i, pid in enumerate(order)}
    entries = []
    for n, h in enumerate(raw, start=1):
        req, resp = h.get("request") or {}, h.get("response") or {}
        url = req.get("url") or ""
        if not url.lower().startswith(("http://", "https://")):
            continue
        content = resp.get("content") or {}
        body = content.get("text")
        if body and content.get("encoding") == "base64":
            try:
                body = base64.b64decode(body).decode("utf-8", "replace")
            except ValueError:
                body = None
        ctype = (content.get("mimeType") or "")
        if body is not None and (not TEXT_TYPES.search(ctype) or len(body) > MAX_BODY):
            body = None
        req_headers = {str(x.get("name", "")).lower(): str(x.get("value", "")) for x in req.get("headers") or []}
        if (req.get("postData") or {}).get("mimeType") and "content-type" not in req_headers:
            req_headers["content-type"] = req["postData"]["mimeType"]
        rtype = h.get("_resourceType") or ("document" if "html" in ctype else "xhr" if "json" in ctype else "other")
        start = round(ts(h.get("startedDateTime", "")) - t0, 3)
        entries.append({
            "id": n, "method": (req.get("method") or "GET").upper(), "url": url, "host": _host(url),
            "req_headers": req_headers,
            "post_data": (req.get("postData") or {}).get("text"), "status": resp.get("status") or None,
            "resp_headers": {str(x.get("name", "")).lower(): str(x.get("value", "")) for x in resp.get("headers") or []},
            "body": body, "error": None, "step": index_of.get(h.get("pageref"), 0), "started_s": start,
            "ended_s": round(start + float(h.get("time") or 0) / 1000, 3), "resource_type": str(rtype).lower(),
            "navigation": rtype == "document"})
    entries.sort(key=lambda e: e["started_s"])
    title = next((s["name"] for s in steps), "Imported HAR")
    return entries, steps, title


# ---- from entries to a script -------------------------------------------------------------------------------------

def classify(e: dict[str, Any], allowed_hosts: list[str], include_static: bool) -> tuple[bool, str]:
    """(kept, reason when left out)."""
    if e.get("resource_type") in NOT_HTTP:
        return False, "WebSocket or event stream (not replayed by HTTP requests)"
    if e.get("error") and not e.get("status"):
        return False, f"Failed in the browser: {e['error']}"
    if e["host"] not in allowed_hosts:
        return False, "Host outside the allowed hosts (third party)"
    if e["method"] in ("OPTIONS",):
        return False, "CORS preflight (JMeter does not need it)"
    rtype = e.get("resource_type") or "other"
    static = rtype in STATIC_TYPES or (rtype == "other" and e["method"] == "GET"
                                       and STATIC_EXT.search(urlsplit(e["url"]).path or ""))
    if static and not include_static:
        return False, "Static file (image, style, script or font)"
    if rtype not in KEEP_TYPES and not static and e["method"] == "GET" and rtype != "other":
        return False, f"Resource type {rtype}"
    return True, ""


def _candidates(e: dict[str, Any]) -> list[tuple[str, str]]:
    """(field name, value) pairs a request sends that may be dynamic."""
    out: list[tuple[str, str]] = []
    u = urlsplit(e["url"])
    out += parse_qsl(u.query, keep_blank_values=False)
    body = e.get("post_data") or ""
    ctype = e["req_headers"].get("content-type", "")
    if body and not body.startswith("base64:"):
        if "x-www-form-urlencoded" in ctype:
            out += parse_qsl(body, keep_blank_values=False)
        elif "json" in ctype or body.lstrip()[:1] in "{[":
            try:
                out += list(_json_leaves(json.loads(body)))
            except ValueError:
                pass
    for k, v in e["req_headers"].items():
        if k == "authorization" and v.lower().startswith("bearer "):
            out.append(("authorization", v[7:].strip()))
        elif re.search(r"csrf|xsrf|token|nonce|session", k) and k not in DROP_HEADERS:
            out.append((k, v))
    return out


def _json_leaves(data: Any, key: str = "") -> Any:
    if isinstance(data, dict):
        for k, v in data.items():
            yield from _json_leaves(v, str(k))
    elif isinstance(data, list):
        for v in data:
            yield from _json_leaves(v, key)
    elif isinstance(data, str):
        yield key, data


def _dynamic(name: str, value: str) -> bool:
    if not value or len(value) < 5 or len(value) > 4096 or "${" in value:
        return False
    if value.lower() in ("true", "false", "null", "undefined", "none"):
        return False
    digit, alpha = any(c.isdigit() for c in value), any(c.isalpha() for c in value)
    if HINT.search(name or "") and len(value) >= 6 and (digit or len(value) >= 16):
        return True
    return len(value) >= 16 and digit and alpha and " " not in value


def _json_path(data: Any, value: str, path: str = "$") -> str | None:
    if isinstance(data, dict):
        for k, v in data.items():
            key = f"{path}.{k}" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(k)) else f"{path}['{k}']"
            found = _json_path(v, value, key)
            if found:
                return found
    elif isinstance(data, list):
        for i, v in enumerate(data):
            found = _json_path(v, value, f"{path}[{i}]")
            if found:
                return found
    elif isinstance(data, str) and data == value:
        return path
    return None


def boundary_matches(text: str, left: str, right: str) -> list[str]:
    """What JMeter's boundary extractor would find: the text between each left boundary and the next right one."""
    out, i = [], 0
    while True:
        a = text.find(left, i)
        if a < 0:
            return out
        start = a + len(left)
        b = text.find(right, start) if right else len(text)
        if b < 0:
            return out
        out.append(text[start:b])
        i = start


def _boundaries(body: str, value: str) -> tuple[str, str, int] | None:
    """Left and right boundaries around the first occurrence of value, and the match number that returns it.
    The shortest left context that works, so the extractor still finds the value when the page changes a little."""
    k = body.find(value)
    if k < 0:
        return None
    line_start = body.rfind("\n", 0, k) + 1
    after = body[k + len(value):]
    for right_len in (1, 2, 4, 8):
        right = after[:right_len]
        if not right or "\n" in right:
            right = after.split("\n", 1)[0][:right_len] or ""
        if not right:
            continue
        for left_len in (16, 24, 32, 48, 10, 6):  # some context first, so the extractor survives small changes
            left = body[max(line_start, k - left_len):k]
            if not left:
                continue
            found = boundary_matches(body, left, right)
            if value in found:
                return left, right, found.index(value) + 1
    return None


def _replace_value(text: str, value: str, var_expr: str) -> str:
    """Replace the value, and its URL-encoded forms, by the variable (URL-encoded where the value was)."""
    if not text or value not in text and quote_plus(value) not in text and quote(value, safe="") not in text:
        return text
    enc = f"${{__urlencode({var_expr})}}"
    out = text.replace(value, var_expr)
    for form in {quote_plus(value), quote(value, safe="")}:
        if form != value:
            out = out.replace(form, enc)
    return out


def build_script(entries: list[dict[str, Any]], steps: list[dict[str, Any]], *, name: str,
                 allowed_hosts: list[str], include_static: bool = False, secrets: dict[str, str] | None = None,
                 exclude_ids: set[int] | None = None, users: int = 1, duration_s: int = 60) -> dict[str, Any]:
    """The JMX for a recording, and a report of what was kept, left out, correlated and kept secret."""
    exclude_ids = exclude_ids or set()
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for e in entries:
        ok, why = classify(e, allowed_hosts, include_static)
        if ok and e["id"] in exclude_ids:
            ok, why = False, "Left out by you"
        if ok:
            kept.append({**e, "req_headers": dict(e["req_headers"])})
        else:
            excluded.append({"id": e["id"], "method": e["method"], "url": e["url"], "reason": why})
    if not kept:
        raise QAError("RECORDING_EMPTY", "Nothing to keep: no requests to the allowed hosts were recorded"
                      + (f" ({len(excluded)} left out)." if excluded else "."), status=409)

    # ---- secrets first, so they are never mistaken for correlations
    taken: set[str] = {"base_protocol", "base_host", "base_port"}
    secret_names: dict[str, str] = {}  # value -> property name
    for field, value in (secrets or {}).items():
        if value and value not in secret_names:
            secret_names[value] = _var_name(field if field != "password" else "password", taken)
    for e in kept:
        auth = e["req_headers"].get("authorization", "")
        if auth and not auth.lower().startswith("bearer "):  # Basic and other schemes: keep it secret
            secret_names.setdefault(auth, _var_name("authorization", taken))

    # ---- correlation: a value sent in a request that an earlier kept response returned
    correlations: list[dict[str, Any]] = []
    extractors: dict[int, list[dict[str, Any]]] = {}
    value_var: dict[str, str] = {}
    unresolved: list[dict[str, Any]] = []
    for i, e in enumerate(kept):
        for field, value in _candidates(e):
            if value in value_var or value in secret_names or not _dynamic(field, value):
                continue
            src = None
            for j in range(i - 1, -1, -1):
                body = kept[j].get("body") or ""
                if value in body:
                    src = j
                    break
            if src is None:
                if not any(value in (x.get("body") or "") for x in entries if x not in kept):
                    if field == "authorization":
                        secret_names.setdefault(e["req_headers"]["authorization"], _var_name("authorization", taken))
                        continue
                    unresolved.append({"request": e["id"], "field": field, "value": _mask(value),
                                       "note": "Not found in an earlier response: it may be made by the page's "
                                               "JavaScript or typed by the person; check it in the script."})
                else:
                    unresolved.append({"request": e["id"], "field": field, "value": _mask(value),
                                       "note": "Only found in a left-out response (static file or other host)."})
                continue
            source = kept[src]
            ext: dict[str, Any] | None = None
            ctype = source["resp_headers"].get("content-type", "")
            if "json" in ctype:
                try:
                    path = _json_path(json.loads(source["body"]), value)
                except ValueError:
                    path = None
                if path:
                    key = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", path.split(".")[-1])
                    ext = {"type": "json", "path": path, "name": key[0] if key else field}
            if ext is None:
                b = _boundaries(source["body"], value)
                if b:
                    ext = {"type": "boundary", "left": b[0], "right": b[1], "match": b[2], "name": field}
            if ext is not None:
                ext["variable"] = var = _var_name(re.split(r"[.\[]", ext.pop("name"))[-1] or "value", taken)
            if ext is None:
                unresolved.append({"request": e["id"], "field": field, "value": _mask(value),
                                   "note": "Found in an earlier response, but no extractor finds it reliably."})
                continue
            value_var[value] = var
            extractors.setdefault(src, []).append(ext)
            correlations.append({"variable": var, "field": field, "value": _mask(value), "extractor": ext["type"],
                                 "source_request": source["id"], "source_url": source["url"],
                                 "expression": ext.get("path") or f"{ext['left']} … {ext['right']}",
                                 "used_in": [], "first_use": e["id"]})

    # Replace correlated values and secrets in every request that sends them (after their source)
    first_source = {c["variable"]: next(i for i, k in enumerate(kept) if k["id"] == c["source_request"])
                    for c in correlations}
    by_var = {c["variable"]: c for c in correlations}
    for i, e in enumerate(kept):
        reps: list[tuple[str, str, str]] = [(v, f"${{{var}}}", var) for v, var in value_var.items()
                                             if i > first_source[var]]
        reps += [(v, f"${{__P({n})}}", "") for v, n in secret_names.items()]
        reps.sort(key=lambda r: -len(r[0]))  # longer values first
        for value, expr, var in reps:
            before = (e["url"], e.get("post_data"), dict(e["req_headers"]))
            e["url"] = _replace_value(e["url"], value, expr)
            if e.get("post_data") and not e["post_data"].startswith("base64:"):
                e["post_data"] = _replace_value(e["post_data"], value, expr)
            for k, v in list(e["req_headers"].items()):
                if value in v:
                    e["req_headers"][k] = v.replace(value, expr)
            if var and (e["url"], e.get("post_data"), e["req_headers"]) != before:
                by_var[var]["used_in"].append(e["id"])

    # ---- the JMX
    first = urlsplit(kept[0]["url"])
    main_host = first.hostname or ""
    main_port = first.port
    main_scheme = first.scheme
    common = {h: kept[0]["req_headers"][h] for h in GLOBAL_HEADERS if h in kept[0]["req_headers"]}
    vars_xml = "".join(
        f'<elementProp name="{n}" elementType="Argument">{_s("Argument.name", n)}{_s("Argument.value", v)}'
        f'{_s("Argument.metadata", "=")}</elementProp>'
        for n, v in (("base_protocol", main_scheme), ("base_host", main_host), ("base_port", main_port or "")))
    head = "".join(f'<elementProp name="{_a(k)}" elementType="Header">{_s("Header.name", _title(k))}'
                   f'{_s("Header.value", v)}</elementProp>' for k, v in common.items())
    step_names = {s["index"]: s["name"] for s in steps}
    groups: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    for i, e in enumerate(kept):
        groups.setdefault(e["step"], []).append((i, e))
    used_names: set[str] = set()
    body_xml = []
    prev_end: float | None = None
    for step_index in sorted(groups):
        items = groups[step_index]
        tname = step_names.get(step_index) or f"Step {step_index + 1}"
        while tname in used_names:
            tname += " (2)"
        used_names.add(tname)
        samplers = []
        for n, (i, e) in enumerate(items):
            think = 0
            if n == 0 and prev_end is not None:
                think = int(max(0.0, min(MAX_THINK_S, e["started_s"] - prev_end)) * 1000)
            samplers.append(_sampler_xml(e, main_host, main_port, main_scheme, common, extractors.get(i, []), think))
        prev_end = max(e["ended_s"] for _, e in items)
        body_xml.append(f'<TransactionController guiclass="TransactionControllerGui" testclass="TransactionController" '
                        f'testname="{_a(tname)}">{_b("TransactionController.includeTimers", False)}'
                        f'{_b("TransactionController.parent", False)}</TransactionController><hashTree>'
                        + "".join(samplers) + "</hashTree>")
    comments = (f"Recorded {time.strftime('%Y-%m-%d %H:%M')} by AI Agentic Test Agent Enterprise: {len(kept)} requests "
                f"kept, {len(excluded)} left out, {len(correlations)} correlated values"
                + (f", secrets {', '.join(sorted(set(secret_names.values())))} from the Credential Manager"
                   if secret_names else "") + ".")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3"><hashTree>'
        f'<TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="{_a(name)}">{_s("TestPlan.comments", comments)}'
        f'{_b("TestPlan.functional_mode", False)}{_b("TestPlan.serialize_threadgroups", False)}'
        f'<elementProp name="TestPlan.user_defined_variables" elementType="Arguments"><collectionProp '
        f'name="Arguments.arguments">{vars_xml}</collectionProp></elementProp></TestPlan><hashTree>'
        '<ConfigTestElement guiclass="HttpDefaultsGui" testclass="ConfigTestElement" testname="HTTP request defaults">'
        '<elementProp name="HTTPsampler.Arguments" elementType="Arguments" guiclass="HTTPArgumentsPanel" '
        'testclass="Arguments" testname="User Defined Variables"><collectionProp name="Arguments.arguments"/>'
        f'</elementProp>{_s("HTTPSampler.domain", "${base_host}")}{_s("HTTPSampler.port", "${base_port}")}'
        f'{_s("HTTPSampler.protocol", "${base_protocol}")}{_s("HTTPSampler.contentEncoding", "UTF-8")}'
        f'{_s("HTTPSampler.connect_timeout", 10000)}{_s("HTTPSampler.response_timeout", 60000)}</ConfigTestElement>'
        '<hashTree/>'
        '<CookieManager guiclass="CookiePanel" testclass="CookieManager" testname="Cookies">'
        f'<collectionProp name="CookieManager.cookies"/>{_b("CookieManager.clearEachIteration", True)}</CookieManager>'
        '<hashTree/>'
        + (f'<HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="Browser headers">'
           f'<collectionProp name="HeaderManager.headers">{head}</collectionProp></HeaderManager><hashTree/>'
           if head else "")
        + f'<ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Recorded users">'
        f'{_s("ThreadGroup.on_sample_error", "startnextloop")}'
        '<elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" '
        f'testclass="LoopController">{_b("LoopController.continue_forever", False)}{_s("LoopController.loops", -1)}'
        f'</elementProp>{_s("ThreadGroup.num_threads", users)}{_s("ThreadGroup.ramp_time", 0)}'
        f'{_b("ThreadGroup.scheduler", True)}{_s("ThreadGroup.duration", duration_s)}{_s("ThreadGroup.delay", 0)}'
        f'{_b("ThreadGroup.same_user_on_next_iteration", True)}</ThreadGroup><hashTree>'
        + "".join(body_xml) + "</hashTree></hashTree></hashTree></jmeterTestPlan>\n")
    text = jscript.normalize(xml)
    report = {
        "kept": len(kept), "excluded": excluded, "correlations": correlations, "unresolved": unresolved,
        "secrets": sorted(set(secret_names.values())), "steps": [{"name": step_names.get(k, f"Step {k + 1}"),
                                                                  "requests": len(v)} for k, v in sorted(groups.items())],
        "hosts": sorted({e["host"] for e in kept}), "main_host": main_host,
    }
    return {"script": text, "report": report, "secret_values": {n: v for v, n in secret_names.items()}}


def _mask(value: str) -> str:
    return value if len(value) <= 8 else f"{value[:4]}…{value[-3:]} ({len(value)} characters)"


def _title(header: str) -> str:
    return "-".join(p[:1].upper() + p[1:] for p in header.split("-"))


def _sampler_xml(e: dict[str, Any], main_host: str, main_port: int | None, main_scheme: str,
                 common: dict[str, str], extractors: list[dict[str, Any]], think_ms: int) -> str:
    u = urlsplit(e["url"])
    same = (u.hostname or "") == main_host and u.port == main_port and u.scheme == main_scheme
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    method = e["method"] if e["method"] in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS") else "GET"
    body = e.get("post_data")
    if body and body.startswith("base64:"):
        body = None  # binary uploads are not replayed from a recording
    args = ('<elementProp name="HTTPsampler.Arguments" elementType="Arguments" guiclass="HTTPArgumentsPanel" '
            'testclass="Arguments" testname="User Defined Variables"><collectionProp name="Arguments.arguments"/>'
            '</elementProp>')
    raw = ""
    if body is not None and method not in ("GET", "HEAD"):
        raw = _b("HTTPSampler.postBodyRaw", True)
        args = ('<elementProp name="HTTPsampler.Arguments" elementType="Arguments"><collectionProp '
                'name="Arguments.arguments"><elementProp name="" elementType="HTTPArgument">'
                f'{_b("HTTPArgument.always_encode", False)}{_s("Argument.value", body)}{_s("Argument.metadata", "=")}'
                '</elementProp></collectionProp></elementProp>')
    label = f"{method} {u.path or '/'}"[:200]
    note = f"Recorded: {e.get('status') or 'no response'}, {e.get('resource_type')}"
    target = "" if same else (f'{_s("HTTPSampler.domain", u.hostname or "")}{_s("HTTPSampler.port", u.port or "")}'
                              f'{_s("HTTPSampler.protocol", u.scheme)}')
    sampler = (f'<HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="{_a(label)}">'
               f'{args}{raw}{target}{_s("HTTPSampler.path", path)}{_s("HTTPSampler.method", method)}'
               f'{_b("HTTPSampler.follow_redirects", False)}{_b("HTTPSampler.auto_redirects", False)}'
               f'{_b("HTTPSampler.use_keepalive", True)}{_b("HTTPSampler.DO_MULTIPART_POST", False)}'
               f'{_s("TestPlan.comments", note)}'
               '</HTTPSamplerProxy>')
    children = []
    if think_ms:
        children.append(f'<ConstantTimer guiclass="ConstantTimerGui" testclass="ConstantTimer" testname="Think time">'
                        f'{_s("ConstantTimer.delay", think_ms)}</ConstantTimer><hashTree/>')
    headers = {k: v for k, v in e["req_headers"].items()
               if not k.startswith(":") and k not in DROP_HEADERS and not k.startswith("sec-")
               and k not in common}
    if headers:
        items = "".join(f'<elementProp name="{_a(_title(k))}" elementType="Header">{_s("Header.name", _title(k))}'
                        f'{_s("Header.value", v)}</elementProp>' for k, v in headers.items())
        children.append(f'<HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="Headers">'
                        f'<collectionProp name="HeaderManager.headers">{items}</collectionProp></HeaderManager>'
                        '<hashTree/>')
    for x in extractors:
        if x["type"] == "json":
            children.append(
                f'<JSONPostProcessor guiclass="JSONPostProcessorGui" testclass="JSONPostProcessor" '
                f'testname="Extract {_a(x["variable"])}">{_s("JSONPostProcessor.referenceNames", x["variable"])}'
                f'{_s("JSONPostProcessor.jsonPathExprs", x["path"])}{_s("JSONPostProcessor.match_numbers", 1)}'
                f'{_s("JSONPostProcessor.defaultValues", "NOT_FOUND_" + x["variable"])}</JSONPostProcessor><hashTree/>')
        else:
            children.append(
                f'<BoundaryExtractor guiclass="BoundaryExtractorGui" testclass="BoundaryExtractor" '
                f'testname="Extract {_a(x["variable"])}">{_s("BoundaryExtractor.useHeaders", "false")}'
                f'{_s("BoundaryExtractor.refname", x["variable"])}{_s("BoundaryExtractor.lboundary", x["left"])}'
                f'{_s("BoundaryExtractor.rboundary", x["right"])}{_s("BoundaryExtractor.default", "NOT_FOUND_" + x["variable"])}'
                f'{_b("BoundaryExtractor.default_empty_value", False)}{_s("BoundaryExtractor.match_number", x["match"])}'
                '</BoundaryExtractor><hashTree/>')
    if e.get("status"):
        children.append(
            f'<ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="Status {e["status"]}">'
            f'<collectionProp name="Asserion.test_strings"><stringProp name="{jscript.java_hash(str(e["status"]))}">'
            f'{e["status"]}</stringProp></collectionProp>{_s("Assertion.custom_message", "")}'
            f'{_s("Assertion.test_field", "Assertion.response_code")}{_b("Assertion.assume_success", True)}'
            f'<intProp name="Assertion.test_type">8</intProp></ResponseAssertion><hashTree/>')
    return sampler + "<hashTree>" + "".join(children) + "</hashTree>"

