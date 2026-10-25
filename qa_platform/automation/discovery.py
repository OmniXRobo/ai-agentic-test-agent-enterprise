"""Application Discovery & Update: explore the application and keep a versioned Application Memory.

The Application Discovery Agent opens the application, signs in when the application asks
for it (saved Application Credentials, or values the tester types when the agent pauses),
then explores it: it follows same-site links, clicks script-driven links, buttons and menus
(never destructive ones, never logout, never a business form's submit), and inventories
pages, elements, forms, menus, dialogs, tables, navigation, business flows and the API
endpoints the pages call. The API inventory also feeds JMeter test generation.

"Update" compares a new discovery with the previous version for the same application and
reports new/removed pages, fields, locators, APIs and workflows.

Interactive discoveries run as a ``DiscoveryJob``: when a value is needed that the agent
cannot know (credentials, OTP, tenant, CAPTCHA), the job enters WAITING_FOR_INPUT and the
browser context stays open, so Proceed continues the same session.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from typing import Any
from collections.abc import Callable
from urllib.parse import parse_qsl, urldefrag, urljoin, urlparse

from ..agents.base import BaseAgent
from ..clock import now_iso
from ..credentials import APP_CREDENTIAL_NAMESPACE
from ..errors import QAError
from ..storage.db import Database, dumps, loads
from .browser import js, launch, start_playwright

STATIC = re.compile(r"\.(png|jpe?g|gif|svg|ico|css|js|woff2?|ttf|map|pdf|zip)(\?|$)", re.I)
LOGOUT = re.compile(r"log[\s_-]?out|sign[\s_-]?out|log[\s_-]?off|sign[\s_-]?off", re.I)
# Recorded in the inventory but never clicked during discovery unless the tester authorises it.
DESTRUCTIVE = re.compile(
    r"\b(delete|remove|approve|reject|pay|payment|transfer|terminate|cancel\s+order|publish|purchase|place\s+order|"
    r"buy(\s+now)?|finish|confirm\s+(order|payment)|submit\s+(order|payment)|reset|archive|deactivate|revoke|"
    r"close\s+account|unsubscribe|refund|void|withdraw)\b", re.I)
STATES = ("IDLE", "PREPARING", "READING_REQUIREMENTS", "RESOLVING_HIERARCHY", "BUILDING_REQUIREMENT_MEMORY",
          "OPENING_APPLICATION", "AUTHENTICATING", "RUNNING", "WAITING_FOR_INPUT", "RESUMING",
          "MAPPING_REQUIREMENTS", "ANALYZING_GAPS", "UPDATING_MEMORY", "COMPLETED", "FAILED", "STOPPED")
ACTIVE_STATES = set(STATES) - {"IDLE", "COMPLETED", "FAILED", "STOPPED"}
INPUT_TIMEOUT_S = 30 * 60
CLICKS_PER_PAGE = 15


class AppDiscoveryAgent(BaseAgent):
    agent_id = "app_discovery"


class DiscoveryStopped(QAError):
    def __init__(self) -> None:
        super().__init__("DISCOVERY_STOPPED", "Discovery was stopped by the tester.", status=409)


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


def _normalize_endpoint(url: str) -> str:
    p = urlparse(url)
    path = re.sub(r"/\d+(?=/|$)", "/{id}", p.path)
    path = re.sub(r"/[0-9a-f]{8,}(?=/|$)", "/{id}", path, flags=re.I)
    return f"{p.scheme}://{p.netloc}{path}"


def default_application(base_url: str) -> str:
    host = urlparse(base_url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def is_destructive(text: str | None) -> bool:
    return bool(text and DESTRUCTIVE.search(text))


def page_name(url: str, title: str | None, auth_kind: str | None = None) -> str:
    """A Page Object name for the page (LoginPage, InventoryPage, CheckoutStepOnePage)."""
    if auth_kind == "login":
        return "LoginPage"
    if auth_kind == "otp":
        return "VerificationPage"
    path = urlparse(url).path
    stem = re.sub(r"\.(html?|aspx?|php|jsp)$", "", path.rstrip("/").rsplit("/", 1)[-1], flags=re.I)
    words = re.findall(r"[A-Za-z][a-z]*|[0-9]+", stem) or re.findall(r"[A-Za-z][a-z]*", title or "")[:3]
    words = [w for w in words if w.lower() not in ("page", "index")] or ["Home"]
    return "".join(w[:1].upper() + w[1:] for w in words)[:40] + "Page"


def field_key(label: str | None, name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (label or name or "field").lower()).strip("_")[:40] or "field"


class DiscoveryJob:
    """Live state of one interactive discovery: state machine, pending input request, stop."""

    def __init__(self, run_id: str, options: dict[str, Any]) -> None:
        self.run_id = run_id
        self.options = options
        self.state = "IDLE"
        self.request: dict[str, Any] | None = None
        self.discovery_id: str | None = None
        self.error: str | None = None
        self.summary: dict[str, Any] | None = None
        self.requirement_memory_id: str | None = None
        self.knowledge_id: str | None = None
        self.started_at = now_iso()
        self.finished_at: str | None = None
        self.history: list[dict[str, str]] = []
        self._answer: dict[str, Any] | None = None
        self._answered = threading.Event()
        self._stop = threading.Event()
        self.on_state: Callable[[str, str], None] | None = None

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    def set_state(self, state: str, text: str = "") -> None:
        if state not in STATES:
            raise ValueError(state)
        self.state = state
        self.history.append({"at": now_iso(), "state": state, "text": text})
        if self.on_state:
            self.on_state(state, text)

    def check(self) -> None:
        if self._stop.is_set():
            raise DiscoveryStopped()

    def stop(self) -> bool:
        if not self.active:
            return False
        self._stop.set()
        self._answered.set()
        return True

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def ask(self, request: dict[str, Any], timeout: float = INPUT_TIMEOUT_S) -> dict[str, Any]:
        """Pause until the tester answers (Proceed) or cancels; the caller's browser stays open meanwhile."""
        previous = self.state
        self._answer = None
        self._answered.clear()
        self.request = {**request, "id": f"IN-{len(self.history) + 1}", "asked_at": now_iso()}
        self.set_state("WAITING_FOR_INPUT", request.get("title", "Input required"))
        deadline = time.monotonic() + timeout
        while not self._answered.wait(0.5):
            if time.monotonic() > deadline:
                self.request = None
                raise QAError("DISCOVERY_INPUT_TIMEOUT", "Nobody answered the input request in time.", status=408)
        self.request = None
        self.check()
        self.set_state("RESUMING", "Input received; continuing in the same browser session")
        self.set_state(previous if previous in ACTIVE_STATES and previous != "WAITING_FOR_INPUT" else "RUNNING")
        return self._answer or {}

    def answer(self, values: dict[str, str], save_static: bool = False) -> None:
        if self.state != "WAITING_FOR_INPUT" or self.request is None:
            raise QAError("DISCOVERY_NOT_WAITING", "This discovery is not waiting for input.", status=409)
        wanted = {f["key"] for f in self.request.get("fields", [])}
        missing = [k for k in wanted if not (values.get(k) or "").strip()] if self.request.get("fields") else []
        if missing:
            raise QAError("DISCOVERY_INPUT_MISSING", f"Enter a value for: {', '.join(missing)}.")
        self._answer = {"values": {k: v for k, v in values.items() if k in wanted}, "save_static": bool(save_static)}
        self._answered.set()

    def view(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "state": self.state, "active": self.active, "request": self.request,
                "discovery_id": self.discovery_id, "error": self.error, "summary": self.summary,
                "requirement_memory_id": self.requirement_memory_id, "knowledge_id": self.knowledge_id,
                "started_at": self.started_at, "finished_at": self.finished_at, "history": self.history[-30:],
                "options": {k: v for k, v in self.options.items() if k not in ("password",)}}


class Discovery:
    def __init__(self, agent: AppDiscoveryAgent, db: Database, creds: Any = None, secrets: Any = None) -> None:
        self.agent = agent
        self.db = db
        self.creds = creds  # Application Credentials (CredentialProfiles); optional for tests
        self.secrets = secrets

    # ---- crawl ----------------------------------------------------------------------------

    def crawl(self, run_id: str, base_url: str, *, max_pages: int = 15, max_depth: int = 3, browser: str = "chromium",
              headless: bool = True, login_steps: Any = None, job: DiscoveryJob | None = None,
              application: str | None = None, credential_role: str | None = None, environment: str | None = None,
              explore: bool = True, knowledge_source: str = "application", visual: bool | None = None,
              allow_destructive: bool = False, guidance: list[str] | None = None) -> dict[str, Any]:
        if not re.match(r"^https?://", base_url or ""):
            raise QAError("DISCOVERY_URL_INVALID", "Discovery needs an http(s) start URL.")
        application = (application or "").strip() or default_application(base_url)
        visual = (not headless) if visual is None else visual
        ex = _Explorer(self, run_id, base_url, max_pages=max_pages, max_depth=max_depth, browser=browser,
                       headless=headless, job=job, application=application, role=credential_role,
                       environment=environment, explore=explore, visual=visual, allow_destructive=allow_destructive,
                       guidance=guidance)
        with self.agent.activity(f"Discovering {base_url}", run_id=run_id) as act:
            ex.act = act
            if job:
                job.set_state("OPENING_APPLICATION", f"Opening {base_url}")
            ex.run()
            if job:
                job.set_state("UPDATING_MEMORY", f"Updating Application Memory for {application}")
            result = ex.result()
            result["knowledge_source"] = knowledge_source
            result["diff"] = self._diff(base_url, result, application)
            result["version"] = self._next_version(application)
            s = result["summary"]
            changes = result["diff"]["total_changes"] if result["diff"] else None
            act.complete(f"Discovered {s['pages']} pages, {s['elements']} elements, {s['endpoints']} API endpoints"
                         + (f"; signed in ({result['authentication']['type']})"
                            if result["authentication"]["status"] == "AUTHENTICATED" else "")
                         + (f"; {changes} change(s) since v{result['version'] - 1}" if changes is not None else ""),
                         pages=s["pages"], endpoints=s["endpoints"], state="UPDATING_MEMORY")
        did = f"DISC-{run_id.split('-', 1)[1]}"
        self.db.execute("INSERT INTO discoveries (id, run_id, base_url, created_at, result, application, version,"
                        " knowledge_source, state) VALUES (?,?,?,?,?,?,?,?,?)",
                        (did, run_id, base_url, result["created_at"], dumps(result), application, result["version"],
                         knowledge_source, "COMPLETED"))
        result["id"] = did
        return result

    # ---- memory -----------------------------------------------------------------------------

    def _next_version(self, application: str) -> int:
        row = self.db.query_one("SELECT MAX(version) AS v FROM discoveries WHERE lower(application) = lower(?)",
                                (application,))
        return int((row or {}).get("v") or 0) + 1

    def _previous(self, base_url: str, application: str | None) -> dict[str, Any] | None:
        row = None
        if application:
            row = self.db.query_one("SELECT * FROM discoveries WHERE lower(application) = lower(?)"
                                    " ORDER BY created_at DESC LIMIT 1", (application,))
        return row or self.db.query_one("SELECT * FROM discoveries WHERE base_url = ? ORDER BY created_at DESC LIMIT 1",
                                        (base_url,))

    def _diff(self, base_url: str, new: dict[str, Any], application: str | None = None) -> dict[str, Any] | None:
        prev_row = self._previous(base_url, application)
        if not prev_row:
            return None
        return diff_memory(loads(prev_row["result"]), new, prev_row["id"], prev_row.get("version"))

    def list(self, limit: int = 50, application: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT id, run_id, base_url, created_at, result, application, version, knowledge_source FROM discoveries"
        params: list[Any] = []
        if application:
            sql += " WHERE lower(application) = lower(?)"
            params.append(application)
        rows = self.db.query(sql + " ORDER BY created_at DESC LIMIT ?", (*params, limit))
        out = []
        for r in rows:
            res = loads(r["result"])
            summary = res.get("summary") or {}
            out.append({"id": r["id"], "run_id": r["run_id"], "base_url": r["base_url"], "created_at": r["created_at"],
                        "application": r.get("application") or default_application(r["base_url"]),
                        "version": r.get("version"), "knowledge_source": r.get("knowledge_source") or "application",
                        "pages": summary.get("pages", len([p for p in res.get("pages", []) if not p.get("error")])),
                        "elements": summary.get("elements", sum(len(p.get("elements", [])) for p in res.get("pages", []))),
                        "endpoints": len(res.get("endpoints", [])),
                        "authentication": (res.get("authentication") or {}).get("status"),
                        "changes": (res.get("diff") or {}).get("total_changes")})
        return out

    def applications(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT COALESCE(application, base_url) AS app, COUNT(*) AS versions, MAX(created_at) AS last"
                             " FROM discoveries GROUP BY lower(COALESCE(application, base_url)) ORDER BY last DESC")
        return [{"application": r["app"], "versions": r["versions"], "last_discovered": r["last"]} for r in rows]

    def get(self, discovery_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM discoveries WHERE id = ?", (discovery_id,))
        if not row:
            raise QAError("DISCOVERY_NOT_FOUND", f"Discovery {discovery_id} does not exist.", status=404)
        return {**loads(row["result"]), "id": row["id"]}

    def latest_for(self, base_url: str, application: str | None = None) -> dict[str, Any] | None:
        row = None
        if application:
            row = self.db.query_one("SELECT * FROM discoveries WHERE lower(application) = lower(?)"
                                    " ORDER BY created_at DESC LIMIT 1", (application,))
        row = row or self.db.query_one("SELECT * FROM discoveries WHERE base_url = ? ORDER BY created_at DESC LIMIT 1",
                                       (base_url,))
        if row is None:
            # The same application started from another page of the site (for example /index.html vs /).
            p = urlparse(base_url)
            row = self.db.query_one("SELECT * FROM discoveries WHERE base_url LIKE ? ORDER BY created_at DESC LIMIT 1",
                                    (f"{p.scheme}://{p.netloc}/%",))
        return {**loads(row["result"]), "id": row["id"]} if row else None

    # ---- stored static authentication values ---------------------------------------------------

    def _field_secret_key(self, application: str, environment: str | None, key: str) -> str:
        return f"{application.strip().lower()}|{(environment or '').strip().lower()}|field|{key}"

    def stored_field(self, application: str, environment: str | None, key: str) -> str | None:
        if self.secrets is None or not self.secrets.available:
            return None
        return self.secrets.get(APP_CREDENTIAL_NAMESPACE, self._field_secret_key(application, environment, key))

    def store_field(self, application: str, environment: str | None, key: str, value: str) -> None:
        self.secrets.set(APP_CREDENTIAL_NAMESPACE, self._field_secret_key(application, environment, key), value)


def diff_memory(prev: dict[str, Any], new: dict[str, Any], prev_id: str, prev_version: int | None = None) -> dict[str, Any]:
    """Previous memory + current discovery -> what changed (pages, fields, locators, APIs, workflows)."""
    def pages(d: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {_path_key(p["url"]): p for p in d.get("pages", []) if not p.get("error")}

    def element_keys(d: dict[str, Any]) -> set[str]:
        return {f"{p['url']} :: {e['selector']}" for p in d.get("pages", []) for e in p.get("elements", [])}

    def identities(d: dict[str, Any]) -> dict[str, str]:
        """Stable identity (test id, element id, name+role) -> selector, to spot locators that changed."""
        out = {}
        for key, p in pages(d).items():
            for e in p.get("elements", []):
                ident = e.get("test_id") or e.get("element_id") or (f"{e.get('role')}:{e.get('name')}" if e.get("name") else None)
                if ident:
                    out[f"{key} :: {ident}"] = e["selector"]
        return out

    def fields(d: dict[str, Any]) -> dict[str, dict[str, Any]]:
        out = {}
        for key, p in pages(d).items():
            for form in p.get("forms", []):
                for f in form.get("fields", []):
                    out[f"{key} :: {f['name']}"] = {"type": f.get("type"), "required": bool(f.get("required"))}
            for e in p.get("elements", []):  # inputs outside <form> (common in SPAs)
                if e.get("role") in ("textbox", "combobox", "checkbox", "radio") and (e.get("element_id") or e.get("test_id")):
                    out.setdefault(f"{key} :: {e.get('element_id') or e.get('test_id')}",
                                   {"type": e.get("input_type") or e.get("tag"), "required": False})
        return out

    def ep_keys(d: dict[str, Any]) -> set[str]:
        return {f"{e['method']} {e['endpoint']}" for e in d.get("endpoints", [])}

    def flows(d: dict[str, Any]) -> dict[str, list[str]]:
        return {f["name"]: f["pages"] for f in d.get("flows", [])}

    old_pages, new_pages = pages(prev), pages(new)
    old_el, new_el = element_keys(prev), element_keys(new)
    old_id, new_id = identities(prev), identities(new)
    old_f, new_f = fields(prev), fields(new)
    old_ep, new_ep = ep_keys(prev), ep_keys(new)
    old_fl, new_fl = flows(prev), flows(new)
    out = {
        "compared_with": prev_id,
        "compared_version": prev_version,
        "pages_added": sorted(new_pages[k]["url"] for k in new_pages.keys() - old_pages.keys()),
        "pages_removed": sorted(old_pages[k]["url"] for k in old_pages.keys() - new_pages.keys()),
        "elements_added": len(new_el - old_el),
        "elements_removed": sorted(old_el - new_el)[:200],
        "fields_added": sorted(new_f.keys() - old_f.keys()),
        "fields_removed": sorted(old_f.keys() - new_f.keys()),
        "fields_changed": sorted(k for k in new_f.keys() & old_f.keys() if new_f[k] != old_f[k]),
        "locators_added": sorted(new_id.keys() - old_id.keys())[:200],
        "locators_changed": [{"element": k, "old": old_id[k], "new": new_id[k]}
                             for k in sorted(new_id.keys() & old_id.keys()) if old_id[k] != new_id[k]][:200],
        "endpoints_added": sorted(new_ep - old_ep),
        "endpoints_removed": sorted(old_ep - new_ep),
        "workflows_added": sorted(new_fl.keys() - old_fl.keys()),
        "workflows_changed": sorted(k for k in new_fl.keys() & old_fl.keys() if new_fl[k] != old_fl[k]),
    }
    out["total_changes"] = (len(out["pages_added"]) + len(out["pages_removed"]) + len(out["fields_added"])
                            + len(out["fields_removed"]) + len(out["fields_changed"]) + len(out["locators_changed"])
                            + len(out["endpoints_added"]) + len(out["endpoints_removed"]) + len(out["workflows_added"])
                            + len(out["workflows_changed"]))
    return out


def _path_key(url: str) -> str:
    p = urlparse(url)
    return f"{p.netloc}{p.path.rstrip('/') or '/'}" + (f"?{p.query}" if p.query else "")


class _Explorer:
    """One discovery run inside one browser context (kept open across input pauses)."""

    def __init__(self, owner: Discovery, run_id: str, base_url: str, *, max_pages: int, max_depth: int, browser: str,
                 headless: bool, job: DiscoveryJob | None, application: str, role: str | None,
                 environment: str | None, explore: bool, visual: bool, allow_destructive: bool,
                 guidance: list[str] | None = None) -> None:
        self.owner = owner
        self.run_id = run_id
        self.base_url = base_url
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.browser_kind = browser
        self.headless = headless
        self.job = job
        self.application = application
        self.role = (role or "").strip() or None
        self.environment = (environment or "").strip() or None
        self.explore = explore
        self.visual = visual
        self.allow_destructive = allow_destructive
        # Requirement words (TFS + Application): related controls are explored first; nothing else is excluded.
        self.guidance = {g.lower() for g in guidance or []}
        self.act: Any = None
        self.pages: list[dict[str, Any]] = []
        self.by_key: dict[str, dict[str, Any]] = {}
        self.endpoints: dict[tuple[str, str], dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self.skipped: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.auth: dict[str, Any] = {"required": False, "status": "NOT_REQUIRED", "type": "none", "login_url": None,
                                     "inputs_requested": [], "runtime_inputs": [], "captcha": False,
                                     "credential": None, "attempts": 0}
        self.queue: deque[tuple[str, int, str | None]] = deque()
        self.seen: set[str] = set()
        self.page: Any = None
        self.login_url: str | None = None
        self.click_budget = max(30, max_pages * CLICKS_PER_PAGE)

    # ---- logging ---------------------------------------------------------------------------

    def log(self, text: str, percent: int | None = None) -> None:
        if self.job:
            self.job.check()
        if self.act is None:
            return
        if percent is None:
            self.act.log(text, state=self.job.state if self.job else None)
        else:
            self.act.progress(percent, text, state=self.job.state if self.job else None)

    def state(self, state: str, text: str) -> None:
        if self.job:
            self.job.set_state(state, text)
        self.log(text)

    def banner(self, text: str) -> None:
        if not self.visual or self.page is None:
            return
        try:
            self.page.evaluate("t => window.__qaOverlay && window.__qaOverlay.status(t)", f"Application Discovery Agent · {text}")
        except Exception:
            pass

    # ---- browser ---------------------------------------------------------------------------------

    def run(self) -> None:
        pw = start_playwright()
        b = context = None
        try:
            b = launch(pw, self.browser_kind, headless=self.headless)
            context = b.new_context(viewport={"width": 1366, "height": 768}, ignore_https_errors=True)
            context.add_init_script(js("fingerprint"))
            context.add_init_script(js("discovery"))
            if self.visual:
                context.add_init_script(js("overlay"))
            self.page = context.new_page()
            self.page.on("response", self._on_response)
            self.page.on("dialog", lambda d: self._native_dialog(d))
            self.queue.append((self.base_url, 0, None))
            if self.job:
                self.job.set_state("RUNNING", "Exploring the application")
            while self.queue and self._counted() < self.max_pages:
                url, depth, via = self.queue.popleft()
                url = urldefrag(url)[0]
                if url in self.seen:
                    continue
                self.seen.add(url)
                self._visit(url, depth, via)
        finally:
            try:
                if context is not None:
                    context.close()
                if b is not None:
                    b.close()
            finally:
                pw.stop()

    def _native_dialog(self, dialog: Any) -> None:
        self.warnings.append(f"Browser {dialog.type} dialog dismissed: {dialog.message[:120]}")
        try:
            dialog.dismiss()
        except Exception:
            pass

    def _on_response(self, resp: Any) -> None:
        req = resp.request
        if req.resource_type not in ("xhr", "fetch") or STATIC.search(req.url):
            return
        key = (req.method, _normalize_endpoint(req.url))
        ep = self.endpoints.setdefault(key, {"method": req.method, "endpoint": key[1], "count": 0, "statuses": [],
                                             "content_type": None, "sample_url": req.url,
                                             "has_body": bool(req.post_data)})
        ep["count"] += 1
        if resp.status not in ep["statuses"]:
            ep["statuses"].append(resp.status)
        ep["content_type"] = ep["content_type"] or resp.headers.get("content-type")

    def _counted(self) -> int:
        return len([p for p in self.pages if not p.get("error")])

    def _goto(self, url: str) -> Any:
        resp = None
        try:
            resp = self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:
            # A slow page that rendered anyway is still usable; a page that never rendered is an error.
            if self.page.url in ("about:blank", "") or not self._has_elements(0):
                raise
            self.warnings.append(f"{url} loaded slowly ({str(exc).splitlines()[0][:120]}); its content was used.")
        self._settle()
        return resp

    def _settle(self, idle_ms: int = 5000) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=idle_ms)
        except Exception:
            pass
        self._has_elements(3000)  # single-page applications render after the load event

    def _has_elements(self, wait_ms: int) -> bool:
        try:
            if wait_ms:
                self.page.wait_for_function("() => window.__qaFp && window.__qaFp.candidates(1).length > 0",
                                            timeout=wait_ms)
                return True
            return bool(self.page.evaluate("() => !!(window.__qaFp && window.__qaFp.candidates(1).length)"))
        except Exception:
            return False

    # ---- visit and inventory -----------------------------------------------------------------------

    def _visit(self, url: str, depth: int, via: str | None) -> None:
        self.log(f"Visiting {url}", int(self._counted() * 100 / max(1, self.max_pages)))
        self.banner(f"Opening {url}")
        try:
            resp = self._goto(url)
        except Exception as exc:
            self.pages.append({"url": url, "error": str(exc).splitlines()[0][:200], "depth": depth})
            self.log(f"Could not open {url}: {str(exc).splitlines()[0][:160]}")
            return
        auth = self._auth_state()
        if auth["kind"] in ("login", "otp", "captcha"):
            self._record(self.page.url, depth, via, resp, auth_kind=auth["kind"])
            if not self._authenticate(auth):
                return
            landed = urldefrag(self.page.url)[0]
            # After signing in, continue from where the application took us, then from the URL we asked for.
            if landed not in self.seen:
                self.seen.add(landed)
                self._record_and_explore(landed, 0, "authentication", None)
            if _path_key(url) != _path_key(landed) and _path_key(url) not in self.by_key \
                    and self._counted() < self.max_pages:
                try:
                    resp = self._goto(url)
                    if self._auth_state()["kind"] == "app" and _path_key(self.page.url) not in self.by_key:
                        self._record_and_explore(self.page.url, depth, via, resp)
                except Exception:
                    pass
            return
        self._record_and_explore(self.page.url, depth, via, resp)

    def _record_and_explore(self, url: str, depth: int, via: str | None, resp: Any) -> None:
        entry = self._record(url, depth, via, resp)
        if entry is None:
            return
        self._enqueue_links(entry, depth)
        if self.explore and depth < self.max_depth:
            self._explore_clicks(entry, depth)

    def _record(self, url: str, depth: int, via: str | None, resp: Any, auth_kind: str | None = None) -> dict[str, Any] | None:
        key = _path_key(url)
        if key in self.by_key:
            return self.by_key[key]
        info = self.page.evaluate(
            """() => ({
                title: document.title,
                elements: window.__qaFp ? window.__qaFp.candidates(400) : [],
                forms: [...document.forms].map(f => ({action: f.getAttribute('action'), method: (f.method||'get').toUpperCase(),
                    fields: [...f.elements].filter(e => e.name || e.id).map(e => ({name: e.name || e.id, type: e.type, required: e.required}))})),
                links: [...document.querySelectorAll('a[href]')].map(a => a.href),
                structure: window.__qaDisc ? window.__qaDisc.structure() : {}
            })""")
        for el in info["elements"]:
            label = el.get("name") or el.get("text") or ""
            if is_destructive(label) and el.get("role") in ("button", "link", "menuitem"):
                el["destructive"] = True
        structure = info.get("structure") or {}
        entry = {
            "url": url, "status": resp.status if resp else None, "title": info["title"], "elements": info["elements"],
            "forms": info["forms"], "depth": depth, "via": via, "name": page_name(url, info["title"], auth_kind),
            "auth": auth_kind, "menus": structure.get("menus", []), "dialogs": structure.get("dialogs", []),
            "tables": structure.get("tables", []), "headings": structure.get("headings", []), "links": info["links"],
        }
        self.pages.append(entry)
        self.by_key[key] = entry
        self.log(f"{entry['name']} discovered: {url} ({len(entry['elements'])} elements, {len(entry['forms'])} forms)")
        return entry

    def _enqueue_links(self, entry: dict[str, Any], depth: int) -> None:
        if depth >= self.max_depth:
            return
        for link in entry.pop("links", []):
            link = urldefrag(urljoin(entry["url"], link))[0]
            if link.startswith("http") and _same_origin(link, self.base_url) and link not in self.seen \
                    and not STATIC.search(link) and not LOGOUT.search(link) and not self._template_full(link):
                self.queue.append((link, depth + 1, entry["url"]))
                self.edges.append({"from": entry["url"], "to": link, "via": "link"})

    def _template_full(self, url: str) -> bool:
        """Pages that differ only in query values (product?id=1, ?id=2 ...) share one template; keep two examples."""
        p = urlparse(url)
        if not p.query:
            return False
        template = (p.netloc, p.path, tuple(sorted(k for k, _ in parse_qsl(p.query, keep_blank_values=True))))
        known = [u for u in [x["url"] for x in self.pages] + [q[0] for q in self.queue]
                 if (urlparse(u).netloc, urlparse(u).path,
                     tuple(sorted(k for k, _ in parse_qsl(urlparse(u).query, keep_blank_values=True)))) == template]
        return len(known) >= 2

    # ---- click exploration (single-page applications, menus, dialogs) --------------------------------

    def _explore_clicks(self, entry: dict[str, Any], depth: int) -> None:
        origin = entry["url"]
        try:
            cands = self.page.evaluate("() => window.__qaDisc ? window.__qaDisc.clickables(60) : []")
        except Exception:
            return
        if self.guidance:
            cands.sort(key=lambda c: 0 if set(re.findall(r"[a-z]{3,}", (c.get("text") or "").lower())) & self.guidance else 1)
        done: set[str] = set()
        clicks = 0
        i = 0
        while i < len(cands) and clicks < CLICKS_PER_PAGE and self.click_budget > 0:
            c = cands[i]
            i += 1
            if c["selector"] in done:
                continue
            done.add(c["selector"])
            label = c.get("text") or c["selector"]
            if LOGOUT.search(label) or LOGOUT.search(c["selector"]):
                self.skipped.append({"page": origin, "element": label, "reason": "logout"})
                continue
            if is_destructive(label) and not self.allow_destructive:
                self.skipped.append({"page": origin, "element": label, "reason": "destructive action (not clicked)"})
                continue
            if _path_key(self.page.url) != _path_key(origin):
                try:
                    self._goto(origin)
                except Exception:
                    return
                if self._auth_state()["kind"] != "app":
                    return  # the session ended; nothing more to explore here
            loc = self.page.locator(c["selector"]).first
            try:
                if not loc.is_visible(timeout=500):
                    continue
            except Exception:
                continue
            clicks += 1
            self.click_budget -= 1
            before_els = {e["selector"] for e in entry["elements"]}
            before_dialogs = len(entry.get("dialogs", []))
            self.banner(f"Exploring: {label}")
            try:
                self._interact(loc, "click", label, lambda: loc.click(timeout=3000))
            except Exception:
                continue
            try:
                self.page.wait_for_load_state("networkidle", timeout=2500)
            except Exception:
                pass
            self.page.wait_for_timeout(250)
            after = urldefrag(self.page.url)[0]
            if _path_key(after) != _path_key(origin):
                if _same_origin(after, self.base_url) and not LOGOUT.search(after):
                    self.edges.append({"from": origin, "to": after, "via": label})
                    if after not in self.seen and _path_key(after) not in self.by_key and not self._template_full(after):
                        self.queue.append((after, depth + 1, origin))
                        self.log(f"'{label}' on {entry['name']} leads to {after}")
                continue
            # Same page: a menu, a dialog or more controls may have appeared.
            try:
                now = self.page.evaluate("() => ({els: window.__qaFp.candidates(400), st: window.__qaDisc.structure(),"
                                         " clicks: window.__qaDisc.clickables(60)})")
            except Exception:
                continue
            new_els = [e for e in now["els"] if e["selector"] not in before_els]
            for e in new_els:
                e["revealed_by"] = label
                if is_destructive(e.get("name") or e.get("text")) and e.get("role") in ("button", "link", "menuitem"):
                    e["destructive"] = True
            entry["elements"].extend(new_els)
            if len(now["st"].get("dialogs", [])) > before_dialogs:
                for d in now["st"]["dialogs"][before_dialogs:]:
                    entry.setdefault("dialogs", []).append({**d, "opened_by": label})
                self.log(f"Dialog opened by '{label}' on {entry['name']}")
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
            for m in now["st"].get("menus", []):
                if m not in entry.get("menus", []):
                    entry.setdefault("menus", []).append({**m, "opened_by": label})
            if new_els:
                self.log(f"'{label}' revealed {len(new_els)} more element(s) on {entry['name']}")
                known = {x["selector"] for x in cands}
                cands.extend(x for x in now["clicks"] if x["selector"] not in known)

    def _interact(self, loc: Any, kind: str, label: str, action: Callable[[], Any]) -> None:
        """Find target -> highlight -> move cursor -> interact -> remove highlight."""
        box = None
        if self.visual:
            try:
                loc.scroll_into_view_if_needed(timeout=2000)
                box = loc.bounding_box(timeout=2000)
                if box:
                    self.page.evaluate("([b, k, l]) => window.__qaOverlay && window.__qaOverlay.point(b.x, b.y, b.width, b.height, k, l)",
                                       [box, kind, label[:60]])
                    self.page.wait_for_timeout(350)
                    if kind == "click":
                        self.page.evaluate("([x, y]) => window.__qaOverlay && window.__qaOverlay.click(x, y)",
                                           [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
            except Exception:
                box = None
        try:
            action()
        finally:
            if self.visual:
                try:
                    self.page.evaluate("window.__qaOverlay && window.__qaOverlay.clear()")
                except Exception:
                    pass  # the page navigated; the new page starts without a highlight

    # ---- authentication ---------------------------------------------------------------------------------

    def _auth_state(self) -> dict[str, Any]:
        try:
            return self.page.evaluate("() => window.__qaDisc ? window.__qaDisc.auth() : {kind: 'app'}")
        except Exception:
            return {"kind": "app", "fields": []}

    def _credential(self) -> tuple[dict[str, Any] | None, str | None]:
        creds = self.owner.creds
        if creds is None:
            return None, None
        row = None
        if self.role:
            row = creds.find(self.application, self.role, self.environment)
        else:
            rows = [r for r in creds.list() if r["application"].strip().lower() == self.application.lower()]
            env_rows = [r for r in rows if (r.get("environment") or "") == (self.environment or "").lower()] or rows
            row = env_rows[0] if len(env_rows) == 1 else None
        if row is None:
            return None, None
        return row, creds.password_of(row["id"])

    def _authenticate(self, auth: dict[str, Any]) -> bool:
        """Sign in and pass verification screens. False when discovery cannot get past authentication."""
        self.auth["required"] = True
        self.auth["login_url"] = self.auth["login_url"] or self.page.url
        if self.job:
            self.job.set_state("AUTHENTICATING", f"{auth['kind'].upper()} screen detected at {self.page.url}")
        for attempt in range(6):
            kind = auth["kind"]
            if kind == "app":
                self.auth["status"] = "AUTHENTICATED"
                self.log(f"Authentication successful; now at {self.page.url}")
                if self.job:
                    self.job.set_state("RUNNING", "Exploring the application")
                return True
            self.auth["attempts"] += 1
            if _path_key(self.page.url) not in self.by_key:  # verification screens belong to the memory too
                self._record(self.page.url, 0, None, None, auth_kind=kind)
            if kind == "captcha":
                self.auth["captcha"] = True
                if not self._captcha():
                    return False
            elif kind == "login":
                self.auth["type"] = "form" if self.auth["type"] in ("none", "form") else self.auth["type"]
                if not self._login(auth, retry=attempt > 0):
                    return False
            elif kind == "otp":
                self.auth["type"] = "form+otp" if self.auth["type"] in ("form", "form+otp") else "otp"
                if not self._otp(auth):
                    return False
            self._settle(4000)
            auth = self._auth_state()
        self.auth["status"] = "FAILED"
        self.log("Authentication did not succeed after several attempts; discovery continues with the public pages.")
        return False

    def _pause(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Ask the tester. None when this discovery cannot ask (non-interactive run)."""
        if self.job is None:
            self.auth["status"] = "INPUT_REQUIRED"
            self.log(f"{request['title']}: no tester is attached to this discovery, so it continues without signing in.")
            return None
        self.banner(f"Waiting for the tester: {request['title']}")
        self.log(f"WAITING FOR INPUT: {request['title']} on {request.get('page')}")
        answer = self.job.ask(request)
        self.log("Input received; continuing in the same browser session")
        return answer

    def _login(self, auth: dict[str, Any], retry: bool) -> bool:
        fields = auth.get("fields", [])
        user_f = next((f for f in fields if f["role"] == "username"), None)
        pass_f = next((f for f in fields if f["role"] == "password"), None)
        extras = [f for f in fields if f["role"] in ("extra", "otp") and f.get("selector")
                  and (f["required"] or f["otp"] or _sensitive(f["label"]))]
        row, password = (None, None) if retry else self._credential()
        values: dict[str, str] = {}
        ask: list[dict[str, Any]] = []
        if row and password:
            values["username"], values["password"] = row["username"], password
            self.auth["credential"] = {"application": row["application"], "role": row["role"],
                                       "environment": row.get("environment") or None}
            self.log(f"Using saved Application Credentials ({row['application']} / {row['role']})")
        else:
            if user_f:
                ask.append({"key": "username", "label": user_f["label"] or "Username", "secret": False})
            ask.append({"key": "password", "label": (pass_f or {}).get("label") or "Password", "secret": True})
        for f in extras:
            key = field_key(f["label"], f["name"])
            stored = None if f["otp"] else self.owner.stored_field(self.application, self.environment, key)
            if stored:
                values[key] = stored
            else:
                ask.append({"key": key, "label": f["label"] or key, "secret": f["type"] == "password" or f["otp"],
                            "runtime_only": bool(f["otp"])})
        if ask:
            answer = self._pause({
                "kind": "credentials", "title": "Application Discovery Requires Input",
                "page": "Sign in" if not retry else "Sign in (the previous attempt was rejected)",
                "url": self.page.url, "fields": ask, "error": auth.get("error") if retry else None,
                "message": "The agent cannot safely determine these values." + (
                    f" The application said: {auth['error']}" if retry and auth.get("error") else ""),
                "static_allowed": any(not a.get("runtime_only") for a in ask)})
            if answer is None:
                return False
            values.update(answer["values"])
            self.auth["inputs_requested"].extend(a["label"] for a in ask)
            if answer.get("save_static"):
                self._save_static(ask, values)
        self.log("Filling the sign-in form (values are never logged)")
        self.banner("Signing in")
        if user_f and values.get("username"):
            self._fill(user_f["selector"], values["username"], "Username", secret=False)
        if pass_f:
            self._fill(pass_f["selector"], values.get("password", ""), "Password", secret=True)
        for f in extras:
            key = field_key(f["label"], f["name"])
            if values.get(key):
                self._fill(f["selector"], values[key], f["label"] or key, secret=True)
                if f["otp"]:
                    self.auth["runtime_inputs"].append(f["label"] or key)
        self._submit(auth, pass_f)
        return True

    def _otp(self, auth: dict[str, Any]) -> bool:
        fields = [f for f in auth.get("fields", []) if f.get("selector")]
        ask = []
        values: dict[str, str] = {}
        for f in fields:
            key = field_key(f["label"], f["name"])
            stored = None if f["otp"] else self.owner.stored_field(self.application, self.environment, key)
            if stored:
                values[key] = stored
            elif f["otp"] or f["required"] or _sensitive(f["label"]):
                ask.append({"key": key, "label": f["label"] or "Authentication Code", "secret": True,
                            "runtime_only": bool(f["otp"])})
        if ask:
            answer = self._pause({"kind": "otp", "title": "Application Discovery Requires Input",
                                  "page": "Two-Factor Authentication", "url": self.page.url, "fields": ask,
                                  "message": "The agent cannot safely determine this value. Codes that expire are "
                                             "used once and never saved (Runtime Authentication Input).",
                                  "static_allowed": any(not a["runtime_only"] for a in ask)})
            if answer is None:
                return False
            values.update(answer["values"])
            self.auth["inputs_requested"].extend(a["label"] for a in ask)
            self.auth["runtime_inputs"].extend(a["label"] for a in ask if a["runtime_only"])
            if answer.get("save_static"):
                self._save_static([a for a in ask if not a["runtime_only"]], values)
        self.banner("Entering the verification code")
        for f in fields:
            key = field_key(f["label"], f["name"])
            if values.get(key):
                self._fill(f["selector"], values[key], f["label"] or key, secret=True)
        self._submit(auth, fields[0] if fields else None)
        return True

    def _captcha(self) -> bool:
        if self.job is None:
            self.auth["status"] = "INPUT_REQUIRED"
            self.log("Manual verification (CAPTCHA) required; discovery does not bypass it.")
            return False
        message = ("Complete the verification in the visible browser, then choose Proceed."
                   if not self.headless else
                   "This application asks for a manual verification (CAPTCHA). The browser is running in the "
                   "background, so it cannot be completed here: cancel and start again with Show Browser.")
        answer = self._pause({"kind": "captcha", "title": "Manual Verification Required", "page": self.page.title(),
                              "url": self.page.url, "fields": [], "message": message, "static_allowed": False})
        return answer is not None

    def _save_static(self, asked: list[dict[str, Any]], values: dict[str, str]) -> None:
        keys = {a["key"] for a in asked if not a.get("runtime_only")}
        if {"username", "password"} <= keys and self.owner.creds is not None:
            role = self.role or "user"
            self.owner.creds.save(self.application, role, values["username"], values["password"], self.auth["login_url"],
                                  self.environment)
            self.auth["credential"] = {"application": self.application, "role": role, "environment": self.environment}
            self.log(f"Credentials saved to Application Credentials ({self.application} / {role})")
        elif "password" in keys and "username" not in keys and self.owner.creds is not None:
            row, _ = self._credential()
            if row:
                self.owner.creds.save(row["application"], row["role"], row["username"], values["password"],
                                      row.get("login_url"), row.get("environment"))
        for key in keys - {"username", "password"}:
            if values.get(key) and self.owner.secrets is not None and self.owner.secrets.available:
                self.owner.store_field(self.application, self.environment, key, values[key])
                self.log(f"Static value '{key}' saved securely for {self.application}")

    def _fill(self, selector: str, value: str, label: str, secret: bool) -> None:
        loc = self.page.locator(selector).first

        def action() -> None:
            if self.visual and not secret:
                loc.click(timeout=3000)
                loc.fill("")
                loc.press_sequentially(value, delay=40)
            else:
                loc.fill(value, timeout=5000)

        self._interact(loc, "type", label, action)

    def _submit(self, auth: dict[str, Any], last_field: dict[str, Any] | None) -> None:
        before = self.page.url
        if auth.get("submit"):
            loc = self.page.locator(auth["submit"]).first
            try:
                self._interact(loc, "click", "Sign in", lambda: loc.click(timeout=5000))
            except Exception:
                auth = {**auth, "submit": None}
        if not auth.get("submit") and last_field:
            self.page.locator(last_field["selector"]).first.press("Enter")
        try:
            self.page.wait_for_url(lambda u: u != before, timeout=8000)
        except Exception:
            pass

    # ---- result ---------------------------------------------------------------------------------------------

    def result(self) -> dict[str, Any]:
        ok_pages = [p for p in self.pages if not p.get("error")]
        for p in self.pages:
            p.pop("links", None)
        flows = business_flows(ok_pages, self.edges)
        n_el = sum(len(p.get("elements", [])) for p in ok_pages)
        forms = sum(len(p.get("forms", [])) for p in ok_pages)
        auth = dict(self.auth)
        if auth["required"] and auth["status"] == "NOT_REQUIRED":
            auth["status"] = "FAILED"
        return {
            "base_url": self.base_url, "application": self.application, "created_at": now_iso(), "pages": self.pages,
            "endpoints": sorted(self.endpoints.values(), key=lambda e: (e["endpoint"], e["method"])),
            "navigation": _dedupe_edges(self.edges), "flows": flows, "authentication": auth,
            "skipped_actions": _group_skipped(self.skipped)[:200], "warnings": self.warnings[:50],
            "mode": "visible" if not self.headless else "headless",
            "summary": {"pages": len(ok_pages), "failed_pages": len(self.pages) - len(ok_pages), "elements": n_el,
                        "forms": forms, "endpoints": len(self.endpoints), "flows": len(flows),
                        "dialogs": sum(len(p.get("dialogs", [])) for p in ok_pages),
                        "tables": sum(len(p.get("tables", [])) for p in ok_pages),
                        "menus": sum(len(p.get("menus", [])) for p in ok_pages),
                        "destructive_skipped": len([s for s in self.skipped if s["reason"].startswith("destructive")])},
        }


SENSITIVE = re.compile(r"\b(tenant|company|domain|environment|pin|otp|code|token|customer|employee|patient|policy|"
                       r"account|department|security|answer|branch|organi[sz]ation)\b", re.I)


def _sensitive(label: str | None) -> bool:
    return bool(label and SENSITIVE.search(label))


def _group_skipped(skipped: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per control and reason, with the pages it was seen on (menus repeat on every page)."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for s in skipped:
        row = out.setdefault((s["element"], s["reason"]), {"element": s["element"], "reason": s["reason"],
                                                          "page": s["page"], "pages": []})
        if s["page"] not in row["pages"]:
            row["pages"].append(s["page"])
    return list(out.values())


def _dedupe_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for e in edges:
        key = (_path_key(e["from"]), _path_key(e["to"]), e["via"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def business_flows(pages: list[dict[str, Any]], edges: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """Reusable flows: the longest distinct paths through the pages, following how discovery reached each one."""
    names = {_path_key(p["url"]): p["name"] for p in pages}
    graph: dict[str, list[str]] = {}
    for e in edges:
        a, b = _path_key(e["from"]), _path_key(e["to"])
        if a in names and b in names and a != b and b not in graph.setdefault(a, []):
            graph[a].append(b)
    starts = [_path_key(p["url"]) for p in pages if p.get("auth") == "login"] or \
             ([_path_key(pages[0]["url"])] if pages else [])
    # The page reached right after signing in continues the login page's flow.
    for p in pages:
        if p.get("via") == "authentication":
            for s in starts:
                graph.setdefault(s, [])
                if _path_key(p["url"]) not in graph[s]:
                    graph[s].insert(0, _path_key(p["url"]))
    paths: list[list[str]] = []

    def walk(node: str, path: list[str]) -> None:
        nxt = [n for n in graph.get(node, []) if n not in path]
        if not nxt or len(path) >= 8:
            paths.append(path)
            return
        for n in nxt:
            walk(n, path + [n])

    for s in starts:
        walk(s, [s])
    paths = [p for p in paths if len(p) > 1]
    paths.sort(key=len, reverse=True)
    out, seen = [], set()
    for p in paths:
        labels = [names[k].removesuffix("Page") for k in p]
        name = " → ".join(labels)
        if name in seen or any(set(p) <= set(q["keys"]) for q in out):
            continue
        seen.add(name)
        out.append({"name": name, "pages": [names[k] for k in p], "keys": p})
        if len(out) >= limit:
            break
    for f in out:
        f.pop("keys")
    return out
