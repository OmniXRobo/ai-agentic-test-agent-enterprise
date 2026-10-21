"""Application Discovery & Update: crawl the application, inventory pages, elements, forms and APIs.

The API endpoint inventory (XHR/fetch traffic) also feeds JMeter test generation.
"Update" compares a new discovery with the previous one for the same base URL.
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

from ..agents.base import BaseAgent
from ..clock import now_iso
from ..errors import QAError
from ..events import names
from ..storage.db import Database, dumps, loads
from .browser import js, launch, start_playwright

STATIC = re.compile(r"\.(png|jpe?g|gif|svg|ico|css|js|woff2?|ttf|map|pdf|zip)(\?|$)", re.I)


class AppDiscoveryAgent(BaseAgent):
    agent_id = "app_discovery"


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


def _normalize_endpoint(url: str) -> str:
    p = urlparse(url)
    path = re.sub(r"/\d+(?=/|$)", "/{id}", p.path)
    path = re.sub(r"/[0-9a-f]{8,}(?=/|$)", "/{id}", path, flags=re.I)
    return f"{p.scheme}://{p.netloc}{path}"


class Discovery:
    def __init__(self, agent: AppDiscoveryAgent, db: Database) -> None:
        self.agent = agent
        self.db = db

    def crawl(self, run_id: str, base_url: str, *, max_pages: int = 15, max_depth: int = 3, browser: str = "chromium",
              headless: bool = True, login_steps: Any = None) -> dict[str, Any]:
        if not re.match(r"^https?://", base_url or ""):
            raise QAError("DISCOVERY_URL_INVALID", "Discovery needs an http(s) start URL.")
        pages: list[dict[str, Any]] = []
        endpoints: dict[tuple[str, str], dict[str, Any]] = {}
        with self.agent.activity(f"Discovering {base_url}", run_id=run_id) as act:
            pw = start_playwright()
            try:
                b = launch(pw, browser, headless=headless)
                context = b.new_context(viewport={"width": 1366, "height": 768}, ignore_https_errors=True)
                context.add_init_script(js("fingerprint"))
                page = context.new_page()

                def on_response(resp: Any) -> None:
                    req = resp.request
                    if req.resource_type not in ("xhr", "fetch") or STATIC.search(req.url):
                        return
                    key = (req.method, _normalize_endpoint(req.url))
                    ep = endpoints.setdefault(key, {"method": req.method, "endpoint": key[1], "count": 0,
                                                    "statuses": [], "content_type": None, "sample_url": req.url,
                                                    "has_body": bool(req.post_data)})
                    ep["count"] += 1
                    if resp.status not in ep["statuses"]:
                        ep["statuses"].append(resp.status)
                    ep["content_type"] = ep["content_type"] or resp.headers.get("content-type")

                page.on("response", on_response)
                queue: deque[tuple[str, int]] = deque([(base_url, 0)])
                seen: set[str] = set()
                while queue and len(pages) < max_pages:
                    url, depth = queue.popleft()
                    url = urldefrag(url)[0]
                    if url in seen:
                        continue
                    seen.add(url)
                    act.progress(int(len(pages) * 100 / max_pages), f"Visiting {url}")
                    try:
                        resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        try:
                            page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            pass
                    except Exception as exc:
                        pages.append({"url": url, "error": str(exc).splitlines()[0][:200]})
                        continue
                    info = page.evaluate(
                        """() => ({
                            title: document.title,
                            elements: window.__qaFp ? window.__qaFp.candidates(400) : [],
                            forms: [...document.forms].map(f => ({action: f.getAttribute('action'), method: (f.method||'get').toUpperCase(),
                                fields: [...f.elements].filter(e => e.name).map(e => ({name: e.name, type: e.type, required: e.required}))})),
                            links: [...document.querySelectorAll('a[href]')].map(a => a.href)
                        })""")
                    pages.append({
                        "url": page.url, "status": resp.status if resp else None, "title": info["title"],
                        "elements": info["elements"], "forms": info["forms"], "depth": depth,
                    })
                    if depth < max_depth:
                        for link in info["links"]:
                            link = urldefrag(urljoin(page.url, link))[0]
                            if link.startswith("http") and _same_origin(link, base_url) and link not in seen \
                                    and not STATIC.search(link) and not re.search(r"logout|signout|log-out", link, re.I):
                                queue.append((link, depth + 1))
                context.close()
                b.close()
            finally:
                pw.stop()
            result = {
                "base_url": base_url, "created_at": now_iso(), "pages": pages,
                "endpoints": sorted(endpoints.values(), key=lambda e: (e["endpoint"], e["method"])),
            }
            result["diff"] = self._diff(base_url, result)
            n_el = sum(len(p.get("elements", [])) for p in pages)
            act.complete(f"Discovered {len(pages)} pages, {n_el} elements, {len(result['endpoints'])} API endpoints",
                         pages=len(pages), endpoints=len(result["endpoints"]))
        did = f"DISC-{run_id.split('-', 1)[1]}"
        self.db.execute("INSERT INTO discoveries (id, run_id, base_url, created_at, result) VALUES (?,?,?,?,?)",
                        (did, run_id, base_url, result["created_at"], dumps(result)))
        result["id"] = did
        return result

    def _diff(self, base_url: str, new: dict[str, Any]) -> dict[str, Any] | None:
        prev_row = self.db.query_one("SELECT * FROM discoveries WHERE base_url = ? ORDER BY created_at DESC LIMIT 1",
                                     (base_url,))
        if not prev_row:
            return None
        prev = loads(prev_row["result"])

        def element_keys(d: dict[str, Any]) -> set[str]:
            return {f"{p['url']} :: {e['selector']}" for p in d.get("pages", []) for e in p.get("elements", [])}

        def ep_keys(d: dict[str, Any]) -> set[str]:
            return {f"{e['method']} {e['endpoint']}" for e in d.get("endpoints", [])}

        old_el, new_el = element_keys(prev), element_keys(new)
        old_ep, new_ep = ep_keys(prev), ep_keys(new)
        return {
            "compared_with": prev_row["id"],
            "pages_added": sorted({p["url"] for p in new["pages"]} - {p["url"] for p in prev.get("pages", [])}),
            "pages_removed": sorted({p["url"] for p in prev.get("pages", [])} - {p["url"] for p in new["pages"]}),
            "elements_added": len(new_el - old_el),
            "elements_removed": sorted(old_el - new_el)[:200],
            "endpoints_added": sorted(new_ep - old_ep),
            "endpoints_removed": sorted(old_ep - new_ep),
        }

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT id, run_id, base_url, created_at, result FROM discoveries ORDER BY created_at DESC LIMIT ?",
                             (limit,))
        out = []
        for r in rows:
            res = loads(r["result"])
            out.append({"id": r["id"], "run_id": r["run_id"], "base_url": r["base_url"], "created_at": r["created_at"],
                        "pages": len(res.get("pages", [])), "endpoints": len(res.get("endpoints", []))})
        return out

    def get(self, discovery_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM discoveries WHERE id = ?", (discovery_id,))
        if not row:
            raise QAError("DISCOVERY_NOT_FOUND", f"Discovery {discovery_id} does not exist.", status=404)
        return {**loads(row["result"]), "id": row["id"]}

    def latest_for(self, base_url: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM discoveries WHERE base_url = ? ORDER BY created_at DESC LIMIT 1", (base_url,))
        return {**loads(row["result"]), "id": row["id"]} if row else None
