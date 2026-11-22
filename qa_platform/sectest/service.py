"""23.14.0: AI Security & Vulnerability Testing service.

Configure -> Discover -> (approve the application map) -> Generate scenarios -> Review -> Approve -> Execute ->
Verify -> Analyze -> Report. Every assessment needs the recorded authorization to test the application (who, when,
statement, reference). Production targets get discovery and passive checks only, unless an admin recorded a
production approval; categories that send POST (XML parser, lockout, body fuzzing, business-logic scenarios) also need
the target's state-changing permission and a non-production environment. These rules are code; the AI only drafts
scenarios and analyses results.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

from .. import auth
from ..agents.base import BaseAgent
from ..clock import now_iso
from ..errors import QAError
from ..itest.common import (validate, CONTROL, ENVIRONMENTS, ItestStore, Sanitizer, ScopeGuard, Throttle, actor,
                            check_id, new_id, run_in_background)
from ..logging_setup import get_logger
from ..storage.db import dumps, loads
from . import active, checks, crawler, fuzz, scenarios, zap
from .client import AUTH_TYPES, SecClient

log = get_logger("sectest")

MODULE = "security"
INTENSITY = {"passive": {"rate": 3, "max_requests": 600, "fuzz": 0},
             "standard": {"rate": 5, "max_requests": 1500, "fuzz": 12},
             "thorough": {"rate": 8, "max_requests": 4000, "fuzz": 30}}
KINDS = ("discovery", "passive", "vulnerability", "zap", "scenarios", "regression", "retest")
FINDING_STATUSES = ("open", "confirmed", "false_positive", "accepted_risk", "fixed")
RULE_CATEGORY = {"CORS-REFLECT": "cors", "XSS-REFLECTED": "xss", "SQLI-ERROR": "sqli", "PATH-TRAVERSAL": "path_traversal",
                 "OPEN-REDIRECT": "open_redirect", "CMD-INJECTION": "command_injection", "XML-ENTITY": "xml",
                 "AUTHN-MISSING": "authz", "AUTHZ-VERTICAL": "authz", "AUTHZ-BOLA": "authz", "SESSION-LOGOUT": "session",
                 "SESSION-FIXATION": "session", "AUTH-LOCKOUT": "lockout"}
SECRET_NAMES_FIXED = ("zap.key", "lockout.secret")


def _covers(targets: list[str], url: str) -> bool:
    """Whether the authorized targets already include this URL's host and port."""
    from ..apitesting.safety import Policy

    p = urlsplit(url)
    try:
        return Policy(targets).authorized((p.hostname or "").lower(), p.port or (443 if p.scheme == "https" else 80))
    except QAError:
        return False


def _agent(bus: Any, agent_id: str) -> BaseAgent:
    cls = type(f"Agent_{agent_id}", (BaseAgent,), {"agent_id": agent_id})
    return cls(bus)


class SecurityService:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self.db = rt.db
        self.store: ItestStore = rt.itest
        self.discovery_agent = _agent(rt.bus, "security_discovery")
        self.vuln_agent = _agent(rt.bus, "vulnerability_assessment")
        self.authz_agent = _agent(rt.bus, "authn_authz_testing")
        self.fuzz_agent = _agent(rt.bus, "controlled_fuzzing")
        self.validation_agent = _agent(rt.bus, "finding_validation")
        self.transport: Any = None  # tests: an httpx transport for the application
        self.zap_transport: Any = None
        self.resolver: Any = None  # tests: name resolution

    # ---- applications ---------------------------------------------------------------------------------------------
    def _own_port(self) -> int:
        try:
            return int(self.rt.settings.section("server").get("port") or 8765)
        except (TypeError, ValueError):
            return 8765

    def _secret_names(self, cfg: dict[str, Any]) -> list[str]:
        return [f"{a.get('role')}.secret" for a in cfg.get("accounts") or []] + list(SECRET_NAMES_FIXED)

    def clean_config(self, cfg: dict[str, Any], environment: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        base = str(cfg.get("base_url") or "").strip()
        p = urlsplit(base)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise QAError("VALIDATION_FAILED", "Enter the application's intranet URL (http:// or https://).", status=422)
        targets = [str(t).strip() for t in cfg.get("targets") or [] if str(t).strip()]
        if not _covers(targets, base):
            targets.insert(0, p.netloc.lower())
        ScopeGuard(targets, own_port=self._own_port())  # validates every target (no too-wide network)
        accounts = []
        roles = set()
        for a in cfg.get("accounts") or []:
            role = str(a.get("role") or "").strip()[:40]
            if not role or role in roles or role == "anonymous" or "#" in role:
                raise QAError("VALIDATION_FAILED", "Every account needs its own role name (not 'anonymous').", status=422)
            if a.get("auth", "none") not in AUTH_TYPES:
                raise QAError("VALIDATION_FAILED", f"Authentication must be one of {', '.join(AUTH_TYPES)}.", status=422)
            roles.add(role)
            keep = {k: a.get(k) for k in ("role", "auth", "username", "login_url", "logout_url", "username_field",
                                          "password_field", "header_name", "cookie_name", "protected_url", "form_action",
                                          "failure_text") if a.get(k) not in (None, "")}
            keep["auth"] = a.get("auth", "none")
            keep["base_url"] = base
            accounts.append(keep)
        intensity = cfg.get("intensity") if cfg.get("intensity") in INTENSITY else "standard"
        cats = [c for c in cfg.get("categories") or [] if c in active.CATEGORIES]
        allow_sc = bool(cfg.get("allow_state_changing"))
        if allow_sc and environment == "Production":
            raise QAError("SEC_PRODUCTION_STATE_CHANGE", "State-changing security tests are not allowed on Production. "
                          "Use an isolated test environment.", status=422)
        auth_rec = dict((previous or {}).get("authorization") or {})
        if cfg.get("authorization_confirmed"):
            statement = str(cfg.get("authorization_statement") or "").strip()
            if len(statement) < 10:
                raise QAError("SEC_AUTHORIZATION", "Describe the authorization to test (who approved it, the reference).",
                              status=422)
            auth_rec = {"confirmed": True, "statement": statement[:500], "reference": str(cfg.get("authorization_reference")
                                                                                        or "")[:120],
                        "by": actor("record the authorization to test"), "at": now_iso()}
        prod = dict((previous or {}).get("production_approval") or {})
        if cfg.get("production_approval") and environment == "Production":
            user = auth.current_user() or auth.LOCAL_PROGRAM
            if user.role != "admin":
                raise QAError("ADMIN_ONLY", "Only an admin can approve active security testing on Production.", status=403)
            prod = {"approved": True, "by": user.username, "at": now_iso(),
                    "statement": str(cfg.get("production_approval"))[:300]}
        clamp = lambda v, lo, hi, d: max(lo, min(hi, type(d)(v))) if v not in (None, "") else d  # noqa: E731
        return {
            "base_url": base, "targets": targets,
            "allowed": [str(x)[:300] for x in cfg.get("allowed") or []][:50],
            "excluded": [str(x)[:300] for x in cfg.get("excluded") or []][:100],
            "accounts": accounts, "intensity": intensity, "categories": cats, "allow_state_changing": allow_sc,
            "rate": clamp(cfg.get("rate"), 0.2, 50.0, float(INTENSITY[intensity]["rate"])),
            "concurrency": clamp(cfg.get("concurrency"), 1, 4, 1),
            "timeout": clamp(cfg.get("timeout"), 2.0, 120.0, 15.0),
            "duration_minutes": clamp(cfg.get("duration_minutes"), 1, 240, 20),
            "max_requests": clamp(cfg.get("max_requests"), 20, 20000, int(INTENSITY[intensity]["max_requests"])),
            "max_pages": clamp(cfg.get("max_pages"), 1, 500, 60),
            "restricted": [{"url": str(r.get("url")), "allowed_roles": [str(x) for x in r.get("allowed_roles") or []]}
                           for r in cfg.get("restricted") or [] if r.get("url")][:100],
            "objects": [{"template": str(o.get("template")), "ids": {str(k): str(v) for k, v in (o.get("ids") or {}).items()}}
                        for o in cfg.get("objects") or [] if o.get("template")][:40],
            "lockout_account": cfg.get("lockout_account") or None,
            "fuzz_operations": [o for o in cfg.get("fuzz_operations") or [] if isinstance(o, dict) and o.get("url")][:20],
            "tls": {"verify": (cfg.get("tls") or {}).get("verify", True) is not False or environment == "Production",
                    "ca_file": str((cfg.get("tls") or {}).get("ca_file") or "")},
            "zap": {"url": str((cfg.get("zap") or {}).get("url") or "http://127.0.0.1:8090"),
                    "use": bool((cfg.get("zap") or {}).get("use")), "active": bool((cfg.get("zap") or {}).get("active")),
                    "policy": str((cfg.get("zap") or {}).get("policy") or "")[:80]},
            "authorization": auth_rec, "production_approval": prod,
            "primary_role": accounts[0]["role"] if accounts else "anonymous",
        }

    def save_app(self, body: dict[str, Any], app_id: str | None = None) -> dict[str, Any]:
        env = body.get("environment") or "Test"
        if env not in ENVIRONMENTS:
            raise QAError("VALIDATION_FAILED", "Unknown environment.", status=422)
        prev = self.store.target(MODULE, app_id)["config"] if app_id else None
        cfg = self.clean_config(body.get("config") or {}, env, prev)
        t = self.store.save_target(MODULE, body.get("name") or "", env, cfg, app_id)
        return self.public_app(t)

    def public_app(self, t: dict[str, Any]) -> dict[str, Any]:
        cfg = t["config"]
        t["secrets"] = {n: bool(self.store.secret(MODULE, t["id"], n)) for n in self._secret_names(cfg)}
        last = self.db.query_one("SELECT id, kind, status, started_at, finished_at FROM itest_runs WHERE target_id = ? "
                                 "ORDER BY started_at DESC LIMIT 1", (t["id"],))
        t["last_run"] = last
        t["open_findings"] = self.counts(t["id"])["open"]
        t["map"] = self.map_info(t["id"])
        return t

    def apps(self) -> list[dict[str, Any]]:
        return [self.public_app(t) for t in self.store.targets(MODULE)]

    def app(self, app_id: str) -> dict[str, Any]:
        return self.public_app(self.store.target(MODULE, app_id))

    def set_secret(self, app_id: str, name: str, value: str | None) -> dict[str, Any]:
        actor("change credentials")
        t = self.store.target(MODULE, app_id)
        if name not in self._secret_names(t["config"]):
            raise QAError("VALIDATION_FAILED", "Unknown credential for this application.", status=422)
        self.store.set_secret(MODULE, app_id, name, value)
        self.store.audit(MODULE, "secret.set" if value else "secret.cleared", app_id, name=name)
        return self.app(app_id)

    def delete_app(self, app_id: str) -> None:
        t = self.store.target(MODULE, app_id)
        self.store.delete_target(MODULE, app_id, self._secret_names(t["config"]))

    # ---- clients ----------------------------------------------------------------------------------------------------
    def _scope(self, cfg: dict[str, Any]) -> ScopeGuard:
        return ScopeGuard(cfg["targets"], cfg.get("allowed"), cfg.get("excluded"), own_port=self._own_port(),
                          allow_state_changing=cfg.get("allow_state_changing", False), resolver=self.resolver)

    def _verify(self, cfg: dict[str, Any]) -> Any:
        from ..apitesting.client import trust_context

        tls = cfg.get("tls") or {}
        if tls.get("verify", True) is False:
            return False
        try:
            return trust_context(tls.get("ca_file") or None)
        except Exception:  # noqa: BLE001
            return True

    def _clients(self, app: dict[str, Any], throttle: Throttle) -> tuple[Any, dict[str, SecClient], Sanitizer]:
        cfg = app["config"]
        scope = self._scope(cfg)
        secrets = self.store.secret_values(MODULE, app["id"], self._secret_names(cfg))
        san = Sanitizer(secrets)
        cache: dict[str, SecClient] = {}
        accounts = {a["role"]: a for a in cfg.get("accounts") or []}

        def client_for(role: str) -> SecClient:
            if role in cache:
                return cache[role]
            base = role.split("#")[0]
            acc = accounts.get(base) or {"role": base, "auth": "none"}
            secret = self.store.secret(MODULE, app["id"], f"{base}.secret") if base in accounts else None
            if role.startswith("lockout#"):
                acc, secret = {"role": "lockout", "auth": "none"}, None
            c = SecClient(scope, throttle, account={**acc, "role": role}, secret=secret, verify=self._verify(cfg),
                          timeout=float(cfg.get("timeout") or 15), transport=self.transport, sanitizer=san)
            if base in accounts and "#fixation" not in role:
                try:
                    c.login()
                except QAError:
                    raise
            cache[role] = c
            return c

        return client_for, cache, san

    def _check_authorized(self, app: dict[str, Any], kind: str) -> None:
        cfg = app["config"]
        if not (cfg.get("authorization") or {}).get("confirmed"):
            raise QAError("SEC_NOT_AUTHORIZED", "Record the authorization to test this application first (Application "
                          "settings > Authorization).", status=409)
        if app["environment"] == "Production" and kind in ("vulnerability", "zap", "scenarios", "regression") and \
                not (cfg.get("production_approval") or {}).get("approved"):
            if kind != "zap" or cfg["zap"].get("active"):
                raise QAError("SEC_PRODUCTION", "Active security testing on Production needs an admin's production "
                              "approval. Discovery and passive checks are allowed.", status=409)

    # ---- runs -------------------------------------------------------------------------------------------------------
    def start(self, app_id: str, kind: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        actor("start security assessments")
        if kind not in KINDS or kind == "retest":
            raise QAError("VALIDATION_FAILED", f"Assessment type must be one of {', '.join(KINDS[:-1])}.", status=422)
        app = self.store.target(MODULE, app_id)
        self._check_authorized(app, kind)
        options = options or {}
        run_id = self.store.create_run(MODULE, app_id, kind, f"{kind.title()} assessment of {app['name']}",
                                       {"options": options, "intensity": app["config"].get("intensity")})
        agent = {"discovery": self.discovery_agent, "passive": self.vuln_agent, "vulnerability": self.vuln_agent,
                 "zap": self.vuln_agent, "scenarios": self.authz_agent, "regression": self.validation_agent}[kind]
        run_in_background(store=self.store, agent=agent, run_id=run_id, module=MODULE,
                          description=f"Security {kind} of {app['name']}",
                          work=lambda act: self._work(kind, app, run_id, act, options))
        self.store.audit(MODULE, "run.started", run_id, kind=kind, app=app_id)
        return self.store.run(run_id)

    def _throttle(self, cfg: dict[str, Any]) -> Throttle:
        return Throttle(rate=float(cfg.get("rate") or 5), budget=int(cfg.get("max_requests") or 1500),
                        seconds=float(cfg.get("duration_minutes") or 20) * 60)

    def _recorder(self, app: dict[str, Any], run_id: str, san: Sanitizer, source: str = "skyguard") -> Any:
        def record(name: str, status: str, category: str, *, finding: dict[str, Any] | None = None,
                   evidence: Any = None, expected: Any = None, actual: Any = None) -> None:
            fid = None
            sev = None
            if finding is not None:
                finding.setdefault("source", source)
                fid = self.upsert_finding(app["id"], run_id, finding, san)
                sev = finding["severity"]
                expected = expected or finding["evidence"].get("expected")
                actual = actual or finding["evidence"].get("actual")
                evidence = finding["evidence"]
            self.store.add_result(run_id, MODULE, app["id"], name, status, category=category, severity=sev,
                                  expected=expected, actual=actual, evidence=evidence, finding_id=fid, sanitizer=san)
        return record

    def _work(self, kind: str, app: dict[str, Any], run_id: str, act: Any, options: dict[str, Any]) -> dict[str, Any]:
        cfg = app["config"]
        throttle = self._throttle(cfg)
        client_for, cache, san = self._clients(app, throttle)
        record = self._recorder(app, run_id, san)
        try:
            if kind == "discovery":
                return self._discover(app, run_id, act, client_for, record, passive=False)
            if kind == "passive":
                return self._discover(app, run_id, act, client_for, record, passive=True)
            if kind == "vulnerability" or kind == "regression":
                before = {f["id"] for f in self.findings(app["id"], status="open,confirmed")}
                summary = self._discover(app, run_id, act, client_for, record, passive=True, store_map=False)
                app_map = self.approved_map(app["id"]) or summary.get("map") or {}
                cats = [c for c in (options.get("categories") or cfg.get("categories") or []) if c in active.CATEGORIES]
                blocked = [c for c in cats if c in active.NEEDS_STATE_CHANGE and not cfg.get("allow_state_changing")]
                for c in blocked:
                    record(f"{active.CATEGORIES[c]}", "BLOCKED", c, actual="Needs a non-production target that allows "
                           "state-changing tests")
                cats = [c for c in cats if c not in blocked]
                act.progress(45, f"Active checks: {', '.join(cats) or 'none approved'}")
                if any(c in ("authz", "session", "lockout") for c in cats):
                    with self.authz_agent.activity(f"Authentication & authorization tests on {app['name']}",
                                                   run_id=run_id) as a2:
                        active.run_checks([c for c in cats if c in ("authz", "session", "lockout")],
                                          client_for=client_for, app_map=app_map, cfg=cfg, run_id=run_id, record=record)
                        a2.complete("Authentication & authorization tests done")
                active.run_checks([c for c in cats if c not in ("authz", "session", "lockout")], client_for=client_for,
                                  app_map=app_map, cfg=cfg, run_id=run_id, record=record)
                per = int(INTENSITY[cfg.get("intensity", "standard")]["fuzz"])
                if per and (options.get("fuzz", True)):
                    act.progress(75, "Controlled fuzz testing")
                    with self.fuzz_agent.activity(f"Controlled fuzz testing of {app['name']}", run_id=run_id) as a3:
                        fuzz.run_fuzz(client_for(cfg.get("primary_role") or "anonymous"), run_id=run_id, app_map=app_map,
                                      operations=cfg.get("fuzz_operations") or [],
                                      allow_bodies=bool(cfg.get("allow_state_changing")) and app["environment"] != "Production",
                                      per_target=per, record=record)
                        a3.complete("Fuzz testing done")
                out = {"requests": throttle.used, "categories": cats}
                if kind == "regression":
                    after = {f["id"] for f in self.findings(app["id"], status="open,confirmed")}
                    seen_now = {r["finding_id"] for r in self.store.results(run_id=run_id) if r.get("finding_id")}
                    fixed = sorted(before - seen_now)  # candidates; only covered rules count
                    marked: list[str] = []
                    # a finding not seen again by a regression run that covered its category is marked fixed
                    for fid in fixed:
                        f = self.finding(fid)
                        rule = f.get("rule_id") or ""
                        covered = RULE_CATEGORY.get(rule) in cats if rule in RULE_CATEGORY else \
                            not rule.startswith(("FUZZ-", "BL-", "ZAP"))
                        if f.get("source") == "zap" or not covered:
                            continue
                        marked.append(fid)
                        self.db.execute("UPDATE sec_findings SET status = 'fixed', retest_status = 'fixed', retest_at = ?,"
                                        " updated_at = ? WHERE id = ?", (now_iso(), now_iso(), fid))
                    out.update({"still_open": len(before & seen_now), "fixed": len(marked), "new": len(after - before)})
                return out
            if kind == "zap":
                return self._zap(app, run_id, act, record)
            if kind == "scenarios":
                return self._scenarios(app, run_id, act, client_for, record, options)
            raise QAError("VALIDATION_FAILED", "Unknown assessment.", status=422)
        finally:
            for c in cache.values():
                c.close()

    def _discover(self, app: dict[str, Any], run_id: str, act: Any, client_for: Any, record: Any, *, passive: bool,
                  store_map: bool = True) -> dict[str, Any]:
        cfg = app["config"]
        roles = ["anonymous"] + [a["role"] for a in cfg.get("accounts") or []]
        maps = []
        seen_sigs: set[str] = set()

        def on_exchange(ex: dict[str, Any], first: bool) -> None:
            if not passive:
                return
            for f in checks.check_exchange(ex, first_for_site=first):
                if f["signature"] in seen_sigs:
                    continue
                seen_sigs.add(f["signature"])
                record(f"{f['title']}: {urlsplit(f['url']).path or '/'}", "FAILED", "passive", finding=f)

        for i, role in enumerate(roles):
            act.progress(int(5 + 35 * i / max(1, len(roles))), f"Discovering as {role}")
            c = client_for(role)
            m = crawler.crawl(c, [cfg["base_url"]], max_pages=int(cfg.get("max_pages") or 60), run_id=run_id,
                              on_exchange=on_exchange)
            for p in m["pages"]:
                for f in p.get("form_findings") or []:
                    if f["signature"] not in seen_sigs:
                        seen_sigs.add(f["signature"])
                        record(f"{f['title']}: {urlsplit(f['url']).path or '/'}", "FAILED", "passive", finding=f)
            maps.append(m)
        merged = crawler.merge_maps(maps)
        if passive:
            record(f"Passive checks on {len(merged['pages'])} pages", "PASSED" if not seen_sigs else "FAILED", "passive",
                   expected="No passive finding", actual=f"{len(seen_sigs)} passive finding(s)",
                   evidence={"pages": len(merged["pages"]), "requests": merged["requests"]})
        else:
            record(f"Application discovery ({len(merged['pages'])} pages, {len(merged['endpoints'])} endpoints)",
                   "PASSED" if merged["pages"] else "FAILED", "discovery",
                   expected="The application answers and pages are found",
                   actual=f"{len(merged['pages'])} pages, {len(merged['not_visited'])} state-changing links not opened",
                   evidence={"roles": merged["roles"], "pages": len(merged["pages"])})
        if store_map:
            self.db.execute("INSERT INTO sec_app_maps (target_id, run_id, map, created_at) VALUES (?,?,?,?)",
                            (app["id"], run_id, dumps(merged), now_iso()))
        return {"pages": len(merged["pages"]), "endpoints": len(merged["endpoints"]), "map": merged,
                "passive_findings": len(seen_sigs)} if not store_map else {
            "pages": len(merged["pages"]), "endpoints": len(merged["endpoints"]), "passive_findings": len(seen_sigs),
            "note": "Review and approve the application map before active testing uses it."}

    def _zap(self, app: dict[str, Any], run_id: str, act: Any, record: Any) -> dict[str, Any]:
        cfg = app["config"]
        zc = cfg.get("zap") or {}
        key = self.store.secret(MODULE, app["id"], "zap.key")
        client = zap.ZapClient(zc.get("url") or "http://127.0.0.1:8090", key, transport=self.zap_transport)
        try:
            version = client.version()
            act.progress(5, f"OWASP ZAP {version}")
            base = cfg["base_url"]
            host = urlsplit(base).netloc
            import re as _re

            include = [_re.escape(f"{urlsplit(base).scheme}://{host}") + ".*"]
            for a in cfg.get("allowed") or []:
                include.append(a[3:] if a.startswith("re:") else _re.escape(a) + ".*")
            exclude = [(x[3:] if x.startswith("re:") else _re.escape(x) + ".*") for x in cfg.get("excluded") or []]
            exclude.append(r".*(log-?out|sign-?out|delete|remove).*")
            ctx_name = f"skyguard-{app['id']}"
            ctx_id = client.setup_context(ctx_name, include, exclude)
            client.set_rate(float(cfg.get("rate") or 5), int(cfg.get("concurrency") or 1))
            deadline = time.monotonic() + float(cfg.get("duration_minutes") or 20) * 60
            sid = client.spider(base, ctx_name)
            self.store.set_run(run_id, checkpoint={"zap_spider": sid, "zap_url": client.base})
            point = lambda: self._zap_point(client, run_id, "spider", sid)  # noqa: E731
            zap.wait(lambda: client.spider_status(sid), point, deadline)
            act.progress(40, "ZAP passive scan")
            zap.wait(lambda: 100 if client.passive_pending() == 0 else 0, lambda: CONTROL.point(run_id), deadline)
            active_done = False
            if zc.get("active"):
                if app["environment"] == "Production" and not (cfg.get("production_approval") or {}).get("approved"):
                    record("ZAP active scan", "BLOCKED", "zap", actual="Production without an admin production approval")
                else:
                    aid = client.active_scan(base, ctx_id, zc.get("policy") or None)
                    self.store.set_run(run_id, checkpoint={"zap_spider": sid, "zap_active": aid, "zap_url": client.base})
                    act.progress(55, "ZAP active scan (approved)")
                    zap.wait(lambda: client.active_status(aid), lambda: self._zap_point(client, run_id, "ascan", aid),
                             deadline)
                    active_done = True
            alerts = client.alerts(f"{urlsplit(base).scheme}://{host}")
            san = Sanitizer(self.store.secret_values(MODULE, app["id"], self._secret_names(cfg)))
            n = 0
            for a in alerts:
                f = zap.alert_to_finding(a)
                ok, _ = self._scope(cfg).in_scope(f["url"]) if f["url"] else (True, "")
                if not ok:
                    continue
                n += 1
                record(f["title"] + f" ({urlsplit(f['url']).path or '/'})", "FAILED" if f["severity"] != "info" else
                       "UNVERIFIED", "zap", finding=f)
            record(f"OWASP ZAP {'spider, passive and active' if active_done else 'spider and passive'} scan",
                   "PASSED" if n == 0 else "FAILED", "zap", expected="No alerts", actual=f"{n} alert(s)",
                   evidence={"zap_version": version, "alerts": n})
            _ = san
            return {"zap_version": version, "alerts": n, "active": active_done}
        finally:
            client.close()

    def _zap_point(self, client: Any, run_id: str, kind: str, scan_id: str) -> None:
        from ..itest.common import Cancelled

        paused = self.store.run(run_id)["status"] == "PAUSED"
        try:
            CONTROL.point(run_id)
        except Cancelled:
            try:
                client.control(kind, scan_id, "stop")
            finally:
                raise
        if paused:
            client.control(kind, scan_id, "resume")

    def _scenarios(self, app: dict[str, Any], run_id: str, act: Any, client_for: Any, record: Any,
                   options: dict[str, Any]) -> dict[str, Any]:
        cfg = app["config"]
        cases = [c for c in self.store.cases(MODULE, app["id"]) if c["definition"].get("kind") == "scenario"]
        wanted = set(options.get("case_ids") or [c["id"] for c in cases])
        ran = 0
        for c in cases:
            if c["id"] not in wanted:
                continue
            if c["status"] != "approved":
                record(f"Scenario {c['name']}", "BLOCKED", "scenario", actual="Not approved yet")
                continue
            if not cfg.get("allow_state_changing") or app["environment"] == "Production":
                record(f"Scenario {c['name']}", "BLOCKED", "scenario",
                       actual="Needs a non-production target that allows state-changing tests")
                continue
            sc = scenarios.Scenario.model_validate(c["definition"]["scenario"])
            for s in sc.steps:
                self._scope(cfg).policy.check_url(s.url)
            status, f, ev = scenarios.execute(sc, client_for, run_id)
            ran += 1
            record(f"Scenario {sc.name}", status, "scenario", finding=f, evidence=ev,
                   expected=sc.description or f"{sc.type} is refused", actual=str(ev)[:300])
        return {"scenarios": ran}

    def control(self, run_id: str, action: str) -> dict[str, Any]:
        actor("control security assessments")
        run = self.store.run(run_id)
        if run["module"] != MODULE:
            raise QAError("ITEST_RUN_NOT_FOUND", "Not a security run.", status=404)
        ok = {"pause": CONTROL.pause, "resume": CONTROL.resume, "cancel": CONTROL.cancel}[action](run_id) \
            if action in ("pause", "resume", "cancel") else False
        if not ok:
            raise QAError("ITEST_RUN_NOT_ACTIVE", "This run is not running.", status=409)
        self.store.set_run(run_id, status={"pause": "PAUSED", "resume": "RUNNING", "cancel": "CANCELLING"}[action])
        if action == "pause" and (run.get("checkpoint") or {}).get("zap_url"):
            cp = run["checkpoint"]
            try:
                app = self.store.target(MODULE, run["target_id"])
                zc = zap.ZapClient(cp["zap_url"], self.store.secret(MODULE, app["id"], "zap.key"),
                                   transport=self.zap_transport)
                if cp.get("zap_active"):
                    zc.control("ascan", cp["zap_active"], "pause")
                elif cp.get("zap_spider"):
                    zc.control("spider", cp["zap_spider"], "pause")
                zc.close()
            except QAError as exc:
                log.info(f"ZAP pause: {exc.message}")
        self.store.audit(MODULE, f"run.{action}", run_id)
        return self.store.run(run_id)

    # ---- application maps -------------------------------------------------------------------------------------------
    def maps(self, app_id: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT id, run_id, approved, approved_by, approved_at, created_at FROM sec_app_maps WHERE "
                             "target_id = ? ORDER BY id DESC LIMIT 20", (app_id,))
        return rows

    def map_info(self, app_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT id, approved, approved_at, created_at FROM sec_app_maps WHERE target_id = ? "
                                "ORDER BY approved DESC, id DESC LIMIT 1", (app_id,))
        return row

    def get_map(self, app_id: str, map_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM sec_app_maps WHERE id = ? AND target_id = ?", (int(map_id), app_id))
        if not row:
            raise QAError("NOT_FOUND", "No such application map.", status=404)
        row["map"] = loads(row["map"])
        return row

    def approve_map(self, app_id: str, map_id: int) -> dict[str, Any]:
        by = actor("approve application maps")
        self.get_map(app_id, map_id)
        self.db.execute("UPDATE sec_app_maps SET approved = 0 WHERE target_id = ?", (app_id,))
        self.db.execute("UPDATE sec_app_maps SET approved = 1, approved_by = ?, approved_at = ? WHERE id = ?",
                        (by, now_iso(), int(map_id)))
        self.store.audit(MODULE, "map.approved", app_id, map_id=map_id)
        return self.get_map(app_id, map_id)

    def approved_map(self, app_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT map FROM sec_app_maps WHERE target_id = ? AND approved = 1 ORDER BY id DESC "
                                "LIMIT 1", (app_id,))
        return loads(row["map"]) if row else None

    # ---- scenarios (AI drafts, review, approval) ----------------------------------------------------------------------
    def draft_scenarios(self, app_id: str) -> list[dict[str, Any]]:
        actor("draft security scenarios")
        app = self.store.target(MODULE, app_id)
        m = self.approved_map(app_id)
        if not m:
            raise QAError("SEC_MAP_NOT_APPROVED", "Run discovery and approve the application map first.", status=409)
        small = {"roles": m.get("roles"), "pages": [{"url": p["url"], "forms": [{"action": f["action"], "method":
                 f["method"], "fields": [x["name"] for x in f["fields"]][:10]} for f in p.get("forms") or []][:4]}
                 for p in m.get("pages") or []][:40], "endpoints": (m.get("endpoints") or [])[:60]}
        import json as _json

        out = self.rt.ai.complete_json(scenarios.DRAFT_PROMPT, f"<map>{_json.dumps(small)[:12000]}</map>",
                                       scenarios.ScenarioDrafts, max_tokens=1800)
        scope = self._scope(app["config"])
        saved = []
        for sc in out.scenarios:
            if not all(scope.in_scope(s.url)[0] for s in sc.steps):
                continue  # a step outside the approved scope is dropped, never "fixed" by guessing
            saved.append(self.store.save_case(MODULE, app_id, sc.name, {"kind": "scenario",
                                                                         "scenario": sc.model_dump(by_alias=True)},
                                              origin="ai"))
        return saved

    def save_scenario(self, app_id: str, body: dict[str, Any], case_id: str | None = None) -> dict[str, Any]:
        self.store.target(MODULE, app_id)
        sc = validate(scenarios.Scenario, body)
        return self.store.save_case(MODULE, app_id, sc.name, {"kind": "scenario", "scenario": sc.model_dump(by_alias=True)},
                                    case_id)

    # ---- findings ---------------------------------------------------------------------------------------------------
    def upsert_finding(self, app_id: str, run_id: str, f: dict[str, Any], san: Sanitizer) -> str:
        ts = now_iso()
        ev = san.data(f.get("evidence") or {})
        row = self.db.query_one("SELECT id, status, occurrences FROM sec_findings WHERE target_id = ? AND signature = ?",
                                (app_id, f["signature"]))
        if row:
            status = row["status"]
            note = None
            if status == "fixed":
                status, note = "open", f"Detected again in {run_id} after it was marked fixed (regression)."
            self.db.execute("UPDATE sec_findings SET last_run = ?, occurrences = occurrences + 1, evidence = ?, status = ?,"
                            " review_note = COALESCE(?, review_note), updated_at = ? WHERE id = ?",
                            (run_id, dumps(ev), status, note, ts, row["id"]))
            return row["id"]
        fid = new_id("VULN")
        self.db.execute(
            "INSERT INTO sec_findings (id, target_id, signature, rule_id, title, category, owasp, severity, confidence, url,"
            " param, description, evidence, source, status, first_run, last_run, created_at, updated_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fid, app_id, f["signature"], f["rule_id"], f["title"][:250], f.get("category"), f.get("owasp"), f["severity"],
             f.get("confidence"), san.text(f.get("url") or "", 1000), f.get("param"), san.text(f.get("description") or "",
                                                                                                 3000),
             dumps(ev), f.get("source") or "skyguard", "open", run_id, run_id, ts, ts))
        return fid

    def finding(self, fid: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM sec_findings WHERE id = ?", (check_id(fid, "finding id"),))
        if not row:
            raise QAError("NOT_FOUND", f"Finding {fid} does not exist.", status=404)
        row["evidence"] = loads(row["evidence"])
        return row

    def findings(self, app_id: str | None = None, *, status: str | None = None, severity: str | None = None,
                 limit: int = 1000) -> list[dict[str, Any]]:
        where, params = [], []
        if app_id:
            where.append("target_id = ?")
            params.append(app_id)
        if status:
            st = [s for s in status.split(",") if s in FINDING_STATUSES]
            if st:
                where.append(f"status IN ({','.join('?' * len(st))})")
                params += st
        if severity:
            sv = [s for s in severity.split(",") if s in ("critical", "high", "medium", "low", "info")]
            if sv:
                where.append(f"severity IN ({','.join('?' * len(sv))})")
                params += sv
        order = "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END"
        rows = self.db.query("SELECT * FROM sec_findings" + (" WHERE " + " AND ".join(where) if where else "") +
                             f" ORDER BY {order}, updated_at DESC LIMIT ?", (*params, int(limit)))
        for r in rows:
            r["evidence"] = loads(r["evidence"])
        return rows

    def review(self, fid: str, status: str, note: str = "") -> dict[str, Any]:
        by = actor("review findings")
        if status not in FINDING_STATUSES:
            raise QAError("VALIDATION_FAILED", f"Status must be one of {', '.join(FINDING_STATUSES)}.", status=422)
        f = self.finding(fid)
        with self.validation_agent.activity(f"Reviewing finding {fid}: {status}") as act:
            self.db.execute("UPDATE sec_findings SET status = ?, reviewed_by = ?, reviewed_at = ?, review_note = ?, "
                            "updated_at = ? WHERE id = ?", (status, by, now_iso(), note[:1000], now_iso(), fid))
            act.complete(f"Finding {fid} marked {status}")
        self.store.audit(MODULE, "finding.reviewed", fid, status=status, previous=f["status"])
        return self.finding(fid)

    def retest(self, kind: str, subject_id: str) -> dict[str, Any]:
        """Retest one finding (or the finding behind a result): its own check on its own URL and parameter."""
        if kind == "result":
            r = self.store.result(int(subject_id))
            if not r.get("finding_id"):
                raise QAError("ITEST_RETEST_UNSUPPORTED", "Run the assessment again to retest this check.", status=409)
            subject_id = r["finding_id"]
        f = self.finding(subject_id)
        app = self.store.target(MODULE, f["target_id"])
        self._check_authorized(app, "passive")
        run_id = self.store.create_run(MODULE, app["id"], "retest", f"Retest of {f['id']} {f['title']}", {"finding": f["id"]})

        def work(act: Any) -> dict[str, Any]:
            throttle = Throttle(rate=float(app["config"].get("rate") or 5), budget=150, seconds=300)
            client_for, cache, san = self._clients(app, throttle)
            found: list[dict[str, Any]] = []

            def record(name: str, status: str, category: str, *, finding: dict[str, Any] | None = None, **kw: Any) -> None:
                if finding is not None:
                    found.append(finding)
            try:
                cat = RULE_CATEGORY.get(f["rule_id"])
                if f.get("source") == "zap":
                    self.store.add_result(run_id, MODULE, app["id"], f"Retest {f['title']}", "UNVERIFIED",
                                          category="retest", finding_id=f["id"],
                                          actual="ZAP findings are retested with a new ZAP scan")
                    return {"retest": "unverified"}
                if cat:
                    pm = {"pages": [{"url": f["url"], "access": {app["config"].get("primary_role") or "anonymous": 200},
                                     "forms": []}], "endpoints": [f["url"]], "soap": [f["url"]] if cat == "xml" else []}
                    active.run_checks([cat], client_for=client_for, app_map=pm, cfg=app["config"], run_id=run_id,
                                      record=record)
                else:
                    c = client_for(app["config"].get("primary_role") or "anonymous")
                    ex = c.get(f["url"] or app["config"]["base_url"])
                    found = [x for x in checks.check_exchange(ex, first_for_site=True)]
                still = any(x["signature"] == f["signature"] for x in found)
                status = "still_open" if still else "fixed"
                self.store.add_result(run_id, MODULE, app["id"], f"Retest {f['title']}", "FAILED" if still else "PASSED",
                                      category="retest", severity=f["severity"], finding_id=f["id"],
                                      expected="The issue is no longer present", actual="Still present" if still else
                                      "Not detected any more", sanitizer=san)
                self.db.execute("UPDATE sec_findings SET retest_status = ?, retest_at = ?, status = CASE WHEN ? = 'fixed' "
                                "THEN 'fixed' ELSE (CASE status WHEN 'fixed' THEN 'open' ELSE status END) END, "
                                "updated_at = ? WHERE id = ?", (status, now_iso(), status, now_iso(), f["id"]))
                return {"retest": status}
            finally:
                for c in cache.values():
                    c.close()

        run_in_background(store=self.store, agent=self.validation_agent, run_id=run_id, module=MODULE,
                          description=f"Retest of finding {f['id']}", work=work)
        return self.store.run(run_id)

    # ---- figures ----------------------------------------------------------------------------------------------------
    def counts(self, app_id: str | None = None) -> dict[str, Any]:
        q = "SELECT severity, status, COUNT(*) AS n FROM sec_findings" + (" WHERE target_id = ?" if app_id else "") + \
            " GROUP BY severity, status"
        rows = self.db.query(q, (app_id,) if app_id else ())
        sev = {s: 0 for s in ("critical", "high", "medium", "low", "info")}
        open_ = pending = 0
        for r in rows:
            if r["status"] in ("open", "confirmed"):
                sev[r["severity"]] = sev.get(r["severity"], 0) + r["n"]
                open_ += r["n"]
            if r["status"] == "open":
                pending += r["n"]
        return {"open": open_, "by_severity": sev, "pending_review": pending}

    def dashboard(self) -> dict[str, Any]:
        runs = self.db.query_one("SELECT COUNT(*) AS total, SUM(status = 'COMPLETED') AS done, SUM(kind = 'retest') AS "
                                 "retests FROM itest_runs WHERE module = 'security'") or {}
        c = self.counts()
        recent = self.db.query("SELECT id, title, severity, status, updated_at, target_id FROM sec_findings WHERE status IN "
                               "('open','confirmed') ORDER BY updated_at DESC LIMIT 6")
        return {"assessments": int(runs.get("total") or 0), "completed": int(runs.get("done") or 0),
                "retests": int(runs.get("retests") or 0), "open": c["open"], "by_severity": c["by_severity"],
                "pending_review": c["pending_review"], "recent": recent}
