"""23.12.0: a small, safe JSONPath subset for API assertions and chained variables.

Supported: ``$`` (root), ``.name``, ``['name']`` / ``["name"]``, ``[n]`` (negative from the end), ``[*]`` and ``.*``
(every child), ``..name`` (recursive descent) and ``.length`` on a list (its size). No filters and no script
expressions: nothing in a path is evaluated as code.
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(r"""\.\.(?P<deep>[A-Za-z_$][\w$-]*|\*)|\.(?P<name>[A-Za-z_$][\w$-]*|\*)|\[(?P<idx>-?\d+)\]|\[\*\]|"""
                    r"""\[(?P<q>'[^']*'|"[^"]*")\]""")
MISSING = object()


class JSONPathError(ValueError):
    pass


def parse(path: str) -> list[tuple[str, Any]]:
    path = (path or "").strip()
    if not path.startswith("$"):
        raise JSONPathError(f"A JSONPath starts with $ (got {path[:40]!r}).")
    pos, out = 1, []
    while pos < len(path):
        m = _TOKEN.match(path, pos)
        if not m:
            raise JSONPathError(f"Unsupported JSONPath near {path[pos:pos + 20]!r}. Use $.a.b[0], $.items[*].id or $..id.")
        if m.group("deep") is not None:
            out.append(("deep", m.group("deep")))
        elif m.group("name") is not None:
            out.append(("all", None) if m.group("name") == "*" else ("key", m.group("name")))
        elif m.group("idx") is not None:
            out.append(("index", int(m.group("idx"))))
        elif m.group("q") is not None:
            out.append(("key", m.group("q")[1:-1]))
        else:
            out.append(("all", None))
        pos = m.end()
    if len(out) > 40:
        raise JSONPathError("The JSONPath is too long.")
    return out


def _children(node: Any) -> list[Any]:
    if isinstance(node, dict):
        return list(node.values())
    if isinstance(node, list):
        return list(node)
    return []


def _deep(node: Any, name: str, out: list[Any], depth: int = 0) -> None:
    if depth > 60:
        return
    if isinstance(node, dict):
        if name == "*":
            out.extend(node.values())
        elif name in node:
            out.append(node[name])
    elif isinstance(node, list) and name == "*":
        out.extend(node)
    for child in _children(node):
        _deep(child, name, out, depth + 1)


def find(doc: Any, path: str) -> list[Any]:
    """Every value the path matches (an empty list when none)."""
    nodes = [doc]
    for kind, arg in parse(path):
        nxt: list[Any] = []
        for n in nodes:
            if kind == "key":
                if isinstance(n, dict) and arg in n:
                    nxt.append(n[arg])
                elif isinstance(n, list) and arg == "length":
                    nxt.append(len(n))
            elif kind == "index":
                if isinstance(n, list) and -len(n) <= arg < len(n):
                    nxt.append(n[arg])
            elif kind == "all":
                nxt.extend(_children(n))
            elif kind == "deep":
                _deep(n, arg, nxt)
        nodes = nxt
        if len(nodes) > 10000:
            nodes = nodes[:10000]
    return nodes


def first(doc: Any, path: str, default: Any = MISSING) -> Any:
    found = find(doc, path)
    return found[0] if found else default


def is_multi(path: str) -> bool:
    return any(k in ("all", "deep") for k, _ in parse(path))
