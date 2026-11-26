"""Lenient JSON from a local model's answer (23.15.1).

Small on-premise models often write JSON that is almost right: a ```json fence or prose around it, a <think> block,
trailing commas, // comments, Python literals (True/False/None), typographic quotes, or an answer that stopped part
way because the answer limit was reached. ``parse_lenient`` accepts all of these and raises ``ValueError`` only when
no JSON value can be recovered at all.
"""

from __future__ import annotations

import json
import re
from typing import Any

_THINK = re.compile(r"<think>.*?(?:</think>|$)", re.S)
_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)(?:```|$)", re.S)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_SMART = str.maketrans({"“": '"', "”": '"', "„": '"', "″": '"'})


def close_json(text: str) -> str | None:
    """Cut a JSON text that stopped part way (answer limit reached) after the last complete value and close the
    open brackets, so everything written before the cut is kept. None when nothing complete was written."""
    stack: list[str] = []
    in_str = esc = False
    best: tuple[int, list[str]] | None = None
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack:
                break
            stack.pop()
            best = (i + 1, list(stack))
            if not stack:
                return text[: i + 1]
    if not best:
        return None
    end, rest = best
    return re.sub(r",\s*$", "", text[:end]) + "".join(reversed(rest))


def _strip_comments(text: str) -> str:
    """Remove // and /* */ comments outside strings."""
    out: list[str] = []
    i, n = 0, len(text)
    in_str = esc = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _python_literals(text: str) -> str:
    """True/False/None outside strings -> true/false/null."""
    parts = re.split(r'("(?:\\.|[^"\\])*")', text)
    for k in range(0, len(parts), 2):
        parts[k] = re.sub(r"\bNone\b", "null", re.sub(r"\bFalse\b", "false", re.sub(r"\bTrue\b", "true", parts[k])))
    return "".join(parts)


def _fixes(text: str) -> list[str]:
    a = _TRAILING_COMMA.sub(r"\1", text)
    b = _TRAILING_COMMA.sub(r"\1", _strip_comments(text.translate(_SMART)))
    c = _python_literals(b)
    out: list[str] = []
    for t in (text, a, b, c):
        if t not in out:
            out.append(t)
    return out


def parse_lenient(text: str, *, close: bool = True) -> Any:
    """The JSON value in a model's answer. ``close`` also accepts an answer that stopped part way (its complete
    part is returned). Raises ValueError when no JSON object or array is found."""
    text = _THINK.sub("", text or "").strip()
    m = _FENCE.search(text)
    if m and ("{" in m.group(1) or "[" in m.group(1)):
        text = m.group(1).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("No JSON found in the AI answer.")
    text = text[min(starts):]
    candidates = [text]
    if close:
        for fixed in _fixes(text)[1:]:
            closed = close_json(fixed)
            if closed:
                candidates.append(closed)
        closed = close_json(text)
        if closed:
            candidates.append(closed)
    decoder = json.JSONDecoder()
    for cand in candidates:
        for fixed in _fixes(cand):
            try:
                return decoder.raw_decode(fixed)[0]
            except json.JSONDecodeError:
                continue
    raise ValueError("The AI answer is not valid JSON.")
