"""Multi-account session manager: who is signed in to the application right now, and is that verified?

The agent never impersonates anyone. It signs in with an account's own login through the application's normal sign-in
page, then *verifies* the identity and role from what the application itself shows (never from the role the user
typed when registering the account). Switching accounts is: normal logout -> verify the session ended (sign-in page
is back) -> sign in as the next account -> verify identity and role. SkyGuard's Playwright driver additionally gives
every account its own isolated browser context, so cookies are never shared; the extension, which works in the user's
own tab, relies on the verified logout.

MFA, OTP, CAPTCHA and driver authentication codes are never automated: the agent pauses and the person completes them.
"""

from __future__ import annotations

import re
from typing import Any

from .util import norm

LOGGED_OUT_WORDS = re.compile(r"\b(sign ?in|log ?in|login)\b", re.I)


def logged_out(snapshot: dict[str, Any], profile: dict[str, Any]) -> bool:
    """The page is a sign-in page (password field visible, or the profile's own logged-out signal)."""
    if (snapshot.get("auth") or {}).get("password_fields"):
        return True
    signals = profile.get("logout", {}).get("signals") or []
    text = norm(snapshot.get("text"))
    return any(norm(s) in text for s in signals if s)


def auth_challenge(snapshot: dict[str, Any]) -> str | None:
    """A step only a person may complete: one-time code / MFA / driver code, or CAPTCHA."""
    auth = snapshot.get("auth") or {}
    if auth.get("captcha"):
        return "captcha"
    if auth.get("otp_fields"):
        text = norm(snapshot.get("text"))
        return "driver_code" if "driver" in text and "code" in text else "otp"
    return None


def read_identity(snapshot: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Identity and role as the application displays them, using the learned identity rule.

    profile["identity"] = {"pattern": "Logged in as (?P<user>\\S+) \\((?P<role>[^)]+)\\)"}   (regex on page text)
    or {"user_label": "...", "role_label": "..."}: the text following those labels on the page.
    """
    ident = profile.get("identity") or {}
    text = " ".join(str(snapshot.get(k) or "") for k in ("text",))
    user = role = None
    pattern = ident.get("pattern")
    if pattern:
        try:
            m = re.search(pattern, text, re.I)
        except re.error:
            m = None
        if m:
            groups = m.groupdict()
            user, role = groups.get("user"), groups.get("role")
    for key, label in (("user", ident.get("user_label")), ("role", ident.get("role_label"))):
        if label and not (user if key == "user" else role):
            m = re.search(re.escape(label) + r"\s*[:\-]?\s*([^\n|,]{1,60})", text, re.I)
            if m:
                value = m.group(1).strip().split("  ")[0]
                if key == "user":
                    user = value.split()[0]
                else:
                    role = value
    return {"user": user.strip() if user else None, "role": role.strip() if role else None}


def role_key(displayed: str | None, profile: dict[str, Any]) -> str | None:
    """Map the role the application displays to a role key (learned role names), else its normalised text."""
    if not displayed:
        return None
    shown = norm(displayed)
    for key, names in (profile.get("role_names") or {}).items():
        if shown == norm(key) or shown in {norm(n) for n in names or []}:
            return norm(key)
    return shown


def verify_identity(snapshot: dict[str, Any], profile: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    """Compare the application's own display with the account we signed in with."""
    seen = read_identity(snapshot, profile)
    result: dict[str, Any] = {"displayed_user": seen["user"], "displayed_role": seen["role"], "identity": "unverified",
                              "role": None, "role_status": "unverified"}
    if seen["user"]:
        result["identity"] = "verified" if norm(seen["user"]) == norm(account["login_id"]) else "mismatch"
    key = role_key(seen["role"], profile)
    if key:
        result["role"] = key
        expected = norm(account.get("role_key"))
        result["role_status"] = "verified" if not expected or key == expected else "mismatch"
    return result


class SessionState:
    """The session the executor believes is active, kept inside the run checkpoint."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        d = data or {}
        self.account_id: str | None = d.get("account_id")
        self.identity: str | None = d.get("identity")
        self.role: str | None = d.get("role")
        self.verified: bool = bool(d.get("verified"))
        self.role_verified: bool = bool(d.get("role_verified"))
        self.context: str | None = d.get("context")  # SkyGuard: isolated browser context name

    def to_dict(self) -> dict[str, Any]:
        return {"account_id": self.account_id, "identity": self.identity, "role": self.role, "verified": self.verified,
                "role_verified": self.role_verified, "context": self.context}

    def clear(self) -> None:
        self.account_id = self.identity = self.role = None
        self.verified = self.role_verified = False
