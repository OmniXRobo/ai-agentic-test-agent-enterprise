"""Read-only application discovery.

Breadth-first over the approved scope with GET only. Forms are recorded, never submitted. Links whose words suggest a
state change (log out, delete, remove, approve, reset ...) are recorded as *not visited*. API descriptions
(OpenAPI / Swagger) and SOAP WSDLs are looked for at their usual addresses. With several accounts each role crawls
on its own, so the map shows which pages each role reaches (the authorization matrix starts from it).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import parse_qsl, urldefrag, urljoin, urlsplit

from ..itest.common import CONTROL
from . import checks

STATE_WORDS = re.compile(r"(?:^|[/_\-.?=&])(log-?out|sign-?out|logoff|delete|remove|destroy|drop|purge|reset|cancel|"
                         r"approve|reject|unsubscribe|deactivate|disable|revoke|terminate|shutdown|restart|pay|"
                         r"checkout|transfer|submit)(?:$|[/_\-.?=&])", re.I)
API_DOCS = ("/openapi.json", "/swagger.json", "/v3/api-docs", "/v2/api-docs", "/swagger/v1/swagger.json", "/api-docs",
            "/api/openapi.json")
JS_ENDPOINT = re.compile(r"""["'`](/(?:api|rest|services?|ws|v\d)/[A-Za-z0-9_\-/{}.:]*)["'`]""")
SKIP_EXT = re.compile(r"\.(?:png|jpe?g|gif|svg|ico|webp|woff2?|ttf|eot|mp4|mp3|zip|pdf|docx?|xlsx?|css)(?:$|\?)", re.I)


class PageParser(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.links: list[dict[str, str]] = []
        self.forms: list[dict[str, Any]] = []
        self.scripts: list[str] = []
        self.title = ""
        self._in_title = False
        self._nav = 0
        self._form: dict[str, Any] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "nav" or a.get("role") == "navigation":
            self._nav += 1
        elif tag == "a" and a.get("href") and not a["href"].startswith(("javascript:", "mailto:", "tel:", "#")):
            self.links.append({"url": urldefrag(urljoin(self.base, a["href"]))[0], "nav": "1" if self._nav else ""})
        elif tag in ("iframe", "frame") and a.get("src"):
            self.links.append({"url": urljoin(self.base, a["src"]), "nav": ""})
        elif tag == "script" and a.get("src"):
            self.scripts.append(urljoin(self.base, a["src"]))
        elif tag == "form":
            self._form = {"action": urljoin(self.base, a.get("action") or self.base),
                          "method": (a.get("method") or "GET").upper(), "enctype": a.get("enctype", ""), "fields": []}
        elif tag in ("input", "select", "textarea") and self._form is not None:
            self._form["fields"].append({"name": a.get("name", ""), "type": (a.get("type") or tag).lower(),
                                         "required": "required" in a, "maxlength": a.get("maxlength"),
                                         "pattern": a.get("pattern"), "accept": a.get("accept"),
                                         "autocomplete": a.get("autocomplete")})
        elif tag == "meta" and a.get("http-equiv", "").lower() == "refresh":
            m = re.search(r"url=(.+)", a.get("content", ""), re.I)
            if m:
                self.links.append({"url": urljoin(self.base, m.group(1).strip("'\" ")), "nav": ""})

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "nav" and self._nav:
            self._nav -= 1
        elif tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data.strip()
        elif len(self._text) < 400:
            self._text.append(data)


def classify_form(form: dict[str, Any]) -> list[str]:
    f = form["fields"]
    types = {x["type"] for x in f}
    names = " ".join((x.get("name") or "").lower() for x in f)
    tags = []
    if "password" in types and len([x for x in f if x["type"] not in ("hidden", "submit")]) <= 3:
        tags.append("login")
    if "file" in types:
        tags.append("upload")
    if "search" in types or re.search(r"\b(q|query|search|keyword)\b", names):
        tags.append("search")
    if re.search(r"page|offset|limit|sort|order|filter", names):
        tags.append("filter")
    if form["method"] == "POST" and "login" not in tags:
        tags.append("state-changing")
    return tags


def crawl(client: Any, start_urls: list[str], *, max_pages: int = 60, max_depth: int = 4, run_id: str | None = None,
          on_exchange: Callable[[dict[str, Any], bool], None] | None = None) -> dict[str, Any]:
    """Returns the application map of one role. ``on_exchange(exchange, first_for_site)`` sees every response."""
    seen: set[str] = set()
    queue: list[tuple[str, int]] = [(u, 0) for u in start_urls]
    pages: list[dict[str, Any]] = []
    not_visited: list[dict[str, str]] = []
    out_of_scope: set[str] = set()
    endpoints: set[str] = set()
    sites: set[str] = set()
    cookies: dict[str, str] = {}
    while queue and len(pages) < max_pages:
        if run_id:
            CONTROL.point(run_id)
        url, depth = queue.pop(0)
        url = urldefrag(url)[0]
        if url in seen:
            continue
        seen.add(url)
        ok, why = client.scope.in_scope(url)
        if not ok:
            out_of_scope.add(url)
            continue
        if STATE_WORDS.search(urlsplit(url).path + "?" + urlsplit(url).query):
            not_visited.append({"url": url, "reason": "looks state-changing (not opened during discovery)"})
            continue
        if SKIP_EXT.search(url):
            continue
        ex = client.get(url)
        site = f"{urlsplit(ex['url']).scheme}://{urlsplit(ex['url']).netloc}"
        first = site not in sites
        sites.add(site)
        if on_exchange:
            on_exchange(ex, first)
        for k, v in ex.get("headers") or []:
            if k.lower() == "set-cookie":
                name = v.split("=", 1)[0].strip()
                cookies[name] = "; ".join(a.strip() for a in v.split(";")[1:])  # attributes only, never the value
        page: dict[str, Any] = {"url": ex["url"], "status": ex["status"], "role": client.role, "depth": depth,
                                "title": "", "forms": [], "links": 0, "security_headers": {
                                    h: checks.header(ex, h) for h in checks.SECURITY_HEADERS},
                                "params": [k for k, _ in parse_qsl(urlsplit(ex["url"]).query)],
                                "error": ex.get("error")}
        if urldefrag(ex["url"])[0] != url:  # redirected: the parameters of the requested URL are tested too
            page["requested"] = url
            seen.add(urldefrag(ex["url"])[0])
        body = ex.get("body") or ""
        if "html" in (ex.get("content_type") or "") and body:
            p = PageParser(ex["url"])
            try:
                p.feed(body)
            except Exception:  # noqa: BLE001 - broken HTML still gives what was parsed
                pass
            page["title"] = p.title[:150]
            page["forms"] = [{**f, "tags": classify_form(f)} for f in p.forms]
            page["links"] = len(p.links)
            page["menu"] = [link["url"] for link in p.links if link["nav"]][:40]
            for link in p.links:
                if link["url"] not in seen and depth + 1 <= max_depth:
                    queue.append((link["url"], depth + 1))
            for m in JS_ENDPOINT.finditer(body):
                endpoints.add(urljoin(ex["url"], m.group(1)))
            if p.forms and on_exchange is not None:
                strict = all("samesite=strict" in a.lower() for a in cookies.values()) if cookies else False
                page["form_findings"] = checks.check_forms(ex["url"], p.forms, strict)
        elif "json" in (ex.get("content_type") or ""):
            endpoints.add(ex["url"])
        pages.append(page)
    # API descriptions (GET at the usual addresses of each site)
    api_docs = []
    for site in sorted(sites):
        for path in API_DOCS:
            if len(api_docs) >= 3 or client.throttle.used >= client.throttle.budget - 2:
                break
            u = site + path
            if not client.scope.in_scope(u)[0]:
                continue
            ex = client.get(u, follow=False)
            if ex["status"] == 200 and re.search(r'"(openapi|swagger)"\s*:', ex.get("body") or ""):
                api_docs.append({"url": u, "kind": "OpenAPI/Swagger"})
                for m in re.finditer(r'"(/[A-Za-z0-9_\-/{}.]+)"\s*:\s*\{\s*"(?:get|post|put|patch|delete)"',
                                     ex.get("body") or ""):
                    endpoints.add(site + m.group(1))
    soap = [e for e in endpoints | {p["url"] for p in pages} if re.search(r"\.(asmx|svc)(?:$|\?)|/soap|wsdl", e, re.I)]
    return {"role": client.role, "pages": pages, "not_visited": not_visited, "out_of_scope": sorted(out_of_scope)[:100],
            "endpoints": sorted(endpoints)[:300], "api_docs": api_docs, "soap": sorted(set(soap))[:50],
            "cookies": cookies, "requests": client.requests,
            "login_forms": [p["url"] for p in pages if any("login" in f["tags"] for f in p.get("forms") or [])],
            "uploads": [p["url"] for p in pages if any("upload" in f["tags"] for f in p.get("forms") or [])],
            "logout": [n["url"] for n in not_visited if re.search(r"log-?out|sign-?out|logoff", n["url"], re.I)]}


def merge_maps(maps: list[dict[str, Any]]) -> dict[str, Any]:
    """One application map from the maps of every role, with the role access matrix."""
    urls: dict[str, dict[str, Any]] = {}
    for m in maps:
        for p in m["pages"]:
            key = p["url"]
            entry = urls.setdefault(key, {"url": key, "title": p.get("title"), "forms": p.get("forms") or [],
                                          "params": p.get("params") or [], "access": {}})
            entry["access"][m["role"]] = p["status"]
            if p.get("forms") and not entry["forms"]:
                entry["forms"] = p["forms"]
            if p.get("requested") and not entry.get("requested"):
                entry["requested"] = p["requested"]
    return {"pages": sorted(urls.values(), key=lambda x: x["url"]),
            "roles": [m["role"] for m in maps],
            "endpoints": sorted({e for m in maps for e in m["endpoints"]}),
            "api_docs": [d for m in maps for d in m["api_docs"]],
            "soap": sorted({s for m in maps for s in m["soap"]}),
            "not_visited": [n for m in maps for n in m["not_visited"]],
            "login_forms": sorted({u for m in maps for u in m["login_forms"]}),
            "logout": sorted({u for m in maps for u in m["logout"]}),
            "uploads": sorted({u for m in maps for u in m["uploads"]}),
            "cookies": {k: v for m in maps for k, v in m["cookies"].items()},
            "requests": sum(m["requests"] for m in maps)}
