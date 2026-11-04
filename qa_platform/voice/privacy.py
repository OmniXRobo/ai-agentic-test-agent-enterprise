"""Privacy of voice transcripts (23.3.6, milestone 9).

Spoken passwords, PINs, one-time codes, tokens and card numbers are masked before a transcript is stored, logged or
sent to the event feed. The command itself still runs on the words as said, and nothing secret is typed from speech:
the guided browser uses the saved credentials instead.
"""

from __future__ import annotations

import re
from typing import Any

MASK = "[hidden]"
_SECRET_WORDS = (r"pass ?words?|pass ?codes?|pins?|pin codes?|otp|one time (?:code|password)|verification codes?|"
                 r"security codes?|cvv|cvc|secrets?|tokens?|api keys?|access keys?|private keys?")
# "password is abc123", "my pin 4321", "password: hunter2", "enter my password secret123"
_AFTER = re.compile(rf"\b({_SECRET_WORDS})\b(\s*(?:is|=|:|was|as|of|to)?\s*)(?!\[hidden\])(\S+)", re.I)
# "enter abc123 as the password", "type hunter2 in the password field"
_BEFORE = re.compile(rf"\b(enter|type|use|put|fill in|fill)\s+(?!the\b|my\b|a\b|an\b|saved\b|configured\b)(\S+)"
                     rf"(\s+(?:as|in|into|for)\s+(?:the\s+|my\s+)?(?:{_SECRET_WORDS}))\b", re.I)
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_NOT_A_VALUE = {"from", "field", "box", "the", "of", "for", "saved", "configured", "stored", "please", "and", "in", "into",
                "is", "was"}
SECRET_TARGET = re.compile(rf"\b({_SECRET_WORDS})\b", re.I)


def redact(text: str) -> str:
    """The transcript with secret values masked ("my password is hunter2" -> "my password is [hidden]")."""
    if not text:
        return text

    def after(m: re.Match) -> str:
        if m.group(3).lower().strip(".,") in _NOT_A_VALUE:
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{MASK}"

    out = _BEFORE.sub(lambda m: f"{m.group(1)} {MASK}{m.group(3)}", text)
    out = _AFTER.sub(after, out)
    return _CARD.sub(MASK, out)


def redact_params(params: dict[str, Any] | None) -> dict[str, Any] | None:
    """Intent parameters for the audit log: a value meant for a secret field, and any secret-looking text, masked."""
    if not params:
        return params
    out = dict(params)
    target = " ".join(str(out.get(k) or "") for k in ("target", "field"))
    if SECRET_TARGET.search(target) and out.get("value"):
        out["value"] = MASK
    for k, v in list(out.items()):
        if isinstance(v, str):
            out[k] = redact(v)
    return out


def is_secret_field(name: str | None) -> bool:
    return bool(name and SECRET_TARGET.search(name))
