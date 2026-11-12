"""Secret interception: passwords, OTPs, MFA and driver codes never reach the model, chat history, memory or logs.

Every chat or voice message passes ``inspect`` first. When it finds a credential the message is dropped as a whole
(nothing of it is stored, logged, sent to the LLM or indexed) and the user is sent to the secure credential form.
``redact`` scrubs text and structures that are about to be stored or logged anyway (page text, errors, evidence).

Detection is deliberately broad: a false alarm costs the user one retype, a miss would leak a password.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

REDACTED = "[REDACTED]"

_SECRET_WORDS = (r"pass(?:word|wd|code|phrase)?|pwd|pin|secret|otp|one[\s-]?time(?:\s+(?:code|password|pin))?|"
                 r"verification\s+code|auth(?:entication)?\s+code|security\s+code|2fa(?:\s+code)?|mfa(?:\s+code)?|"
                 r"totp|token|recovery\s+code|backup\s+code|driver\s+(?:auth(?:entication)?\s+)?code|access\s+code")

# "password is X", "password: X", "pwd=X", "my password X", "OTP 123456", "the code is 1234"
_LABELLED = re.compile(rf"\b(?P<word>{_SECRET_WORDS})\b\s*(?P<sep>is|was|=|:|-|would be|will be|should be)?\s*"
                       rf"[\"'`]?(?P<value>[^\s\"'`,;]{{3,}})", re.I)
_CODE_WORD = re.compile(r"^(?:otp|one[\s-]?time|verification|auth|security|2fa|mfa|totp|access|driver|recovery|"
                        r"backup)", re.I)
_CODE_IS = re.compile(r"\b(?:code|pin)\b\s*(?:is|=|:)\s*(?P<value>\d{4,10})\b", re.I)
# "login admin/Secret123", "user: bob pass: x" handled by _LABELLED; "admin / P@ssw0rd!" (user/password pairs)
_PAIR = re.compile(r"\b(?:log\s?in|login|username|user|id|account)\b[^\n]{0,40}?\b(?P<user>[\w.@-]{2,})\s*[/|:]\s*"
                   r"(?P<value>\S{4,})", re.I)
# Words after which a following token is not a secret value ("password field", "password reset", ...)
_HARMLESS_VALUES = {"field", "fields", "box", "reset", "change", "changed", "expired", "expires", "expiry", "policy",
                    "rules", "rule", "screen", "page", "form", "required", "manager", "vault", "for", "of", "and", "or",
                    "the", "a", "an", "to", "with", "without", "please", "prompt", "entry", "input", "dialog",
                    "button", "label", "length", "strength", "hint", "manually", "later", "securely", "store",
                    "stored", "saved", "save", "capture", "updated", "update", "if", "when", "then", "in", "on",
                    "into", "here", "there", "it", "this", "that", "is", "are", "be", "was", "were", "not", "no",
                    "yes", "management", "protected", "masked", "hidden", "account", "accounts", "login", "logins",
                    "credentials", "credential", "required.", "missing", "wrong", "incorrect", "invalid", "fails",
                    "failed", "needed", "needs", "asks", "asked", "screen.", "page.", "reset.", "number", "numbers",
                    "generator", "authentication", "process", "step", "stage", "entered", "enter", "provide",
                    "provided", "ask", "type", "typed", "should", "would", "will", "can", "cannot", "must"}
_WORD_HARMLESS = {"token": {"bucket", "limit", "count", "counts", "usage"}}


@dataclass
class Inspection:
    contains_secret: bool
    kinds: list[str] = field(default_factory=list)

    @property
    def reply(self) -> str:
        what = " and ".join(sorted(set(self.kinds))) or "a credential"
        return (f"I stopped that message because it looks like it contains {what}. It was not stored, logged or sent "
                "to the AI model. Please use the secure credential form (Accounts > Add or Replace credentials) for "
                "passwords. One-time codes, MFA and driver authentication codes are never stored: type them directly "
                "into the application when it asks, and I will wait.")


def _kind(word: str) -> str:
    w = word.lower()
    if any(k in w for k in ("otp", "one", "verification", "2fa", "mfa", "totp", "auth", "security", "access")):
        return "a one-time or authentication code"
    if "driver" in w:
        return "a driver authentication code"
    if "recovery" in w or "backup" in w:
        return "a recovery code"
    if "token" in w or "secret" in w:
        return "a secret token"
    if w == "pin":
        return "a PIN"
    return "a password"


def inspect(text: str, known_secrets: Iterable[str] = ()) -> Inspection:
    """Does this message contain a credential? ``known_secrets`` are values from the vault (exact match)."""
    kinds: list[str] = []
    if not text:
        return Inspection(False)
    for m in _LABELLED.finditer(text):
        value = m.group("value").strip(".!?")
        word = m.group("word")
        if value.lower() in _HARMLESS_VALUES or value.lower() in _WORD_HARMLESS.get(word.lower(), set()):
            continue
        if word.lower() in ("token",) and not re.search(r"\d", value):
            continue
        if word.lower() == "pin" and not re.search(r"\d", value):
            continue
        if _CODE_WORD.match(word) and not m.group("sep") and not re.search(r"\d", value):
            continue  # "the OTP Operator account", "MFA settings": a name, not a code
        kinds.append(_kind(word))
    for _m in _CODE_IS.finditer(text):
        kinds.append("a one-time or authentication code")
    for m in _PAIR.finditer(text):
        value = m.group("value")
        if value.lower().strip(".") in _HARMLESS_VALUES or value.startswith(("http://", "https://")):
            continue
        if re.search(r"[A-Za-z]", value) and (re.search(r"\d", value) or re.search(r"[^\w]", value)):
            kinds.append("a password")
    for secret in known_secrets:
        if secret and len(secret) >= 3 and secret in text:
            kinds.append("a saved password")
    return Inspection(bool(kinds), kinds)


def redact_text(text: str, known_secrets: Iterable[str] = ()) -> str:
    if not text:
        return text
    out = _LABELLED.sub(lambda m: m.group(0) if m.group("value").lower().strip(".!?") in _HARMLESS_VALUES
                        else m.group(0).replace(m.group("value"), REDACTED), text)
    out = _CODE_IS.sub(lambda m: m.group(0).replace(m.group("value"), REDACTED), out)
    for secret in known_secrets:
        if secret and len(secret) >= 3:
            out = out.replace(secret, REDACTED)
    return out


SECRET_KEYS = re.compile(r"pass(word|wd|code)?|pwd|secret|otp|token|mfa|2fa|pin$|auth.?code|recovery|cookie|session",
                         re.I)


def redact(value: Any, known_secrets: Iterable[str] = (), _key: str = "") -> Any:
    """A copy of ``value`` with secret-looking keys and known secret values replaced."""
    known = [s for s in known_secrets if s]
    if isinstance(value, dict):
        return {k: (REDACTED if SECRET_KEYS.search(str(k)) and v not in (None, "", False, True)
                    and not isinstance(v, (dict, list)) else redact(v, known, str(k))) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, known, _key) for v in value]
    if isinstance(value, str):
        return redact_text(value, known)
    return value
