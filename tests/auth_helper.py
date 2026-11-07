"""23.3.34: sign a Playwright browser context in to a running SkyGuard QA (the dashboard needs an account)."""

from __future__ import annotations

import httpx

from qa_platform.auth import COOKIE, DEFAULT_ADMIN

TEST_ADMIN_PASSWORD = "Test-Admin-2026"


def session_token(base: str, username: str = DEFAULT_ADMIN[0], password: str = TEST_ADMIN_PASSWORD) -> str | None:
    """Signs in like the dashboard does (with an Origin header) and replaces the starting admin password once."""
    browser = {"Origin": base.rstrip("/"), "Sec-Fetch-Site": "same-origin"}
    with httpx.Client(base_url=base, headers=browser, timeout=30) as c:
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        if r.status_code in (404, 405):  # a version before 23.3.34 (no accounts)
            return None
        if r.status_code == 401 and username == DEFAULT_ADMIN[0]:  # still the starting password
            r = c.post("/api/auth/login", json={"username": username, "password": DEFAULT_ADMIN[1]})
            r.raise_for_status()
            c.post("/api/auth/password", json={"current": DEFAULT_ADMIN[1], "new": password}).raise_for_status()
        else:
            r.raise_for_status()
        return c.cookies[COOKIE]


def sign_in(context, base: str, **account: str) -> None:
    """Adds a signed-in session cookie to a Playwright BrowserContext (or a Page's context)."""
    ctx = getattr(context, "context", context)
    token = session_token(base, **account)
    if token is None:
        return
    ctx.add_cookies([{"name": COOKIE, "value": token, "url": base.rstrip("/") + "/"}])
