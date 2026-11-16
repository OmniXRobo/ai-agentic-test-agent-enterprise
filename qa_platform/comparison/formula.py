"""Integration Comparison Workspace (23.8.0): calculation rules, evaluated with plain decimal arithmetic.

A rule says how an application's displayed value is calculated from other applications' values, for example

    [A:Completed Orders] / ([A:Completed Orders] + [A:Pending Orders]) * 100
    SUM([A:Total Sales])
    COUNT([A:Customer ID])

``[A:Field]`` is the field of application A (its letter). Only numbers, + - * / and parentheses and the functions
SUM, AVG, MIN, MAX, COUNT, ROUND and ABS exist: an expression is parsed into a syntax tree and anything else is
refused, so no code ever runs. The AI or the suggestion search may *propose* a rule; the comparison only uses a rule
a tester approved, and its result is always this module's arithmetic, never a model's answer.
"""

from __future__ import annotations

import ast
import itertools
import re
from decimal import ROUND_HALF_UP, Decimal, DivisionByZero, InvalidOperation
from typing import Any, Callable

from .normalize import is_null, parse_number

REF = re.compile(r"\[([A-Z]{1,2}):([^\[\]]{1,120})\]")
FUNCS = {"SUM", "AVG", "MIN", "MAX", "COUNT", "ROUND", "ABS"}
AGGREGATES = {"SUM", "AVG", "MIN", "MAX", "COUNT"}


class FormulaError(ValueError):
    pass


def refs(expression: str) -> list[tuple[str, str]]:
    """The (application letter, field) pairs an expression reads."""
    return [(m.group(1), m.group(2).strip()) for m in REF.finditer(expression or "")]


def _compile(expression: str) -> tuple[ast.AST, dict[str, tuple[str, str]]]:
    names: dict[str, tuple[str, str]] = {}

    def sub(m: re.Match[str]) -> str:
        key = f"r{len(names)}"
        names[key] = (m.group(1), m.group(2).strip())
        return key

    text = REF.sub(sub, expression or "")
    text = text.replace("×", "*").replace("÷", "/")
    if len(text) > 600:
        raise FormulaError("The calculation is too long.")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"The calculation is not valid: {exc.msg}.") from exc
    for node in ast.walk(tree):
        ok = isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div,
                               ast.USub, ast.UAdd, ast.Load, ast.Call, ast.Name, ast.Constant))
        if not ok:
            raise FormulaError("Only numbers, fields, + - * / ( ) and SUM, AVG, MIN, MAX, COUNT, ROUND, ABS are "
                               "allowed.")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise FormulaError("Only numbers are allowed as constants.")
        if isinstance(node, ast.Name) and node.id not in names and node.id.upper() not in FUNCS:
            raise FormulaError(f"Unknown name '{node.id}'. Write fields as [A:Field name].")
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.func.id.upper() not in FUNCS):
            raise FormulaError("Unknown function.")
    return tree, names


def validate(expression: str) -> list[tuple[str, str]]:
    _compile(expression)
    return refs(expression)


def to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if value is None or is_null(value):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    return parse_number(str(value))


def evaluate(expression: str, scalar: Callable[[str, str], Any],
             column: Callable[[str, str], list[Any]] | None = None) -> Decimal:
    """Evaluate. ``scalar(letter, field)`` gives one value (a record's cell or a single displayed value);
    ``column(letter, field)`` gives all values of a column (for SUM/AVG/MIN/MAX/COUNT)."""
    tree, names = _compile(expression)

    def val(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return val(node.body)
        if isinstance(node, ast.Constant):
            return Decimal(str(node.value))
        if isinstance(node, ast.Name):
            letter, field = names[node.id]
            d = to_decimal(scalar(letter, field))
            if d is None:
                raise FormulaError(f"[{letter}:{field}] has no numeric value.")
            return d
        if isinstance(node, ast.UnaryOp):
            v = val(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp):
            a, b = val(node.left), val(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if b == 0:
                raise FormulaError("Division by zero.")
            try:
                return a / b
            except (DivisionByZero, InvalidOperation) as exc:
                raise FormulaError("Division by zero.") from exc
        if isinstance(node, ast.Call):
            fn = node.func.id.upper()  # type: ignore[attr-defined]
            if fn in AGGREGATES:
                if len(node.args) != 1 or not isinstance(node.args[0], ast.Name) or column is None:
                    raise FormulaError(f"{fn} takes one field, e.g. {fn}([A:Amount]).")
                letter, field = names[node.args[0].id]
                raw = column(letter, field)
                if fn == "COUNT":
                    return Decimal(sum(1 for v in raw if v not in (None, "") and not is_null(v)))
                nums = [d for d in (to_decimal(v) for v in raw) if d is not None]
                if not nums:
                    raise FormulaError(f"[{letter}:{field}] has no numeric values.")
                if fn == "SUM":
                    return sum(nums, Decimal(0))
                if fn == "AVG":
                    return sum(nums, Decimal(0)) / len(nums)
                return min(nums) if fn == "MIN" else max(nums)
            if fn == "ABS":
                return abs(val(node.args[0]))
            if fn == "ROUND":
                places = int(val(node.args[1])) if len(node.args) > 1 else 0
                return val(node.args[0]).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        raise FormulaError("The calculation is not valid.")

    return val(tree)


def decimals_of(text: Any) -> int:
    m = re.search(r"[.,](\d+)\s*%?\s*$", str(text or "").strip())
    return len(m.group(1)) if m else 0


def same(expected: Decimal, shown: Any, decimals: int | None = None, tolerance: Any = None) -> tuple[bool, Decimal | None]:
    """Is the displayed value the expected value, rounded the way the application shows it?"""
    d = to_decimal(shown)
    if d is None:
        return False, None
    places = decimals if decimals is not None else decimals_of(shown)
    q = Decimal(1).scaleb(-places)
    exp_r = expected.quantize(q, rounding=ROUND_HALF_UP)
    diff = d - exp_r
    tol = Decimal(str(tolerance)) if tolerance not in (None, "") else Decimal(0)
    return abs(diff) <= tol, diff


def pretty(expression: str, names: dict[str, str] | None = None) -> str:
    """A readable form for testers: [A:Completed Orders] -> Order System › Completed Orders, * -> ×."""
    names = names or {}
    text = REF.sub(lambda m: f"{names.get(m.group(1), m.group(1))} › {m.group(2).strip()}", expression or "")
    return text.replace("*", "×").replace("/", "÷")


# ---- suggestions -------------------------------------------------------------------------------------------------

def _stem(word: str) -> str:
    w = word.lower()
    for suf in ("ions", "ion", "ed", "ing", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def _words(label: str) -> set[str]:
    return {_stem(w) for w in re.split(r"[^A-Za-z0-9]+", label or "") if len(w) > 2}


def _hint(target: str, field: str) -> float:
    a, b = _words(target), _words(field)
    if not a or not b:
        return 0.0
    hit = sum(1 for x in a for y in b if x == y or (len(x) >= 5 and len(y) >= 5 and x[:5] == y[:5]))
    return min(1.0, hit / max(1, len(a)))


def is_ratio(label: str, shown: list[Any]) -> bool:
    low = (label or "").lower()
    return any("%" in str(v) for v in shown) or any(w in low for w in ("%", "percent", "rate", "ratio", "completion",
                                                                       "share", "growth", "margin"))


def suggest(target_label: str, samples: list[dict[str, Any]], fields: list[tuple[str, str]],
            *, letters: dict[str, str] | None = None, limit: int = 3) -> list[dict[str, Any]]:
    """Formulas that could produce a displayed value from other fields of the same record (or the same screen).

    samples: [{"shown": "83.33%", "values": {(letter, field): value}}] aligned by record key.
    fields:  candidate (letter, field) pairs, numeric.
    A formula whose results equal every displayed value ranks first; a formula that only fits by name is offered
    for review with its mismatches stated (it may be the rule, and the application wrong)."""
    shown = [s["shown"] for s in samples]
    if not samples or not fields:
        return []
    fields = fields[:12]
    ratio = is_ratio(target_label, shown)
    templates: list[tuple[str, tuple[int, ...]]] = []
    n = len(fields)
    if ratio:
        templates += [("{a} / {b} * 100", p) for p in itertools.permutations(range(n), 2)]
        templates += [("{a} / ({a} + {b}) * 100", p) for p in itertools.permutations(range(n), 2)]
        templates += [("{a} / ({a} + {b} + {c}) * 100", (a, *bc)) for a in range(n)
                      for bc in itertools.combinations([i for i in range(n) if i != a], 2)]
        templates += [("({a} - {b}) / {b} * 100", p) for p in itertools.permutations(range(n), 2)]
    else:
        templates += [("{a} + {b}", p) for p in itertools.combinations(range(n), 2)]
        templates += [("{a} - {b}", p) for p in itertools.permutations(range(n), 2)]
        templates += [("{a} + {b} + {c}", p) for p in itertools.combinations(range(n), 3)]
        templates += [("{a} * {b}", p) for p in itertools.combinations(range(n), 2)]
    out = []
    for tpl, idx in templates:
        refs_ = [fields[i] for i in idx]
        names = dict(zip("abc", (f"[{lt}:{f}]" for lt, f in refs_)))
        expr = tpl.format(**names)
        matches, mism, usable = 0, [], 0
        for s in samples:
            try:
                v = evaluate(expr, lambda lt, f, s=s: s["values"].get((lt, f)))
            except FormulaError:
                continue
            usable += 1
            ok, diff = same(v, s["shown"])
            if ok:
                matches += 1
            else:
                mism.append({"shown": s["shown"], "expected": format(v.quantize(Decimal("0.01")), "f")})
        if not usable:
            continue
        hint = _hint(target_label, refs_[0][1])
        if ratio and "{a} / ({a}" not in tpl and "- {b}" not in tpl:
            hint *= 0.9
        fit = matches / usable
        if fit == 1:
            conf = 0.80 + 0.15 * hint
        elif hint >= 0.5 and fit >= 0:
            conf = 0.45 + 0.2 * hint * (0.5 + fit / 2) - 0.05 * (len(idx) - 2)
        else:
            continue
        out.append({"expression": expr, "confidence": round(min(conf, 0.95), 3), "matches": matches,
                    "checked": usable, "mismatches": mism[:5],
                    "explanation": (f"Matches the displayed value on {matches} of {usable} sample(s)"
                                    + (f"; the field names agree" if hint >= 0.5 else "")
                                    + ("" if fit == 1 else ". The values do NOT agree on every sample: either the rule "
                                                           "is different or the application calculates wrongly."))})
    # prefer simpler formulas on ties
    out.sort(key=lambda x: (-x["confidence"], -x["matches"], len(x["expression"])))
    seen, best = set(), []
    for x in out:
        if x["expression"] in seen:
            continue
        seen.add(x["expression"])
        best.append(x)
        if len(best) >= limit:
            break
    return best


def suggest_total(target_label: str, shown: Any, columns: dict[tuple[str, str], list[Any]]) -> list[dict[str, Any]]:
    """A summary card (KPI) from a column of another application: SUM, COUNT, AVG, MIN or MAX."""
    out = []
    for (lt, f), vals in list(columns.items())[:40]:
        for fn in ("SUM", "COUNT", "AVG", "MAX", "MIN"):
            expr = f"{fn}([{lt}:{f}])"
            try:
                v = evaluate(expr, lambda *_: None, lambda *_a, vals=vals: vals)
            except FormulaError:
                continue
            ok, _ = same(v, shown)
            hint = _hint(target_label, f)
            if fn == "COUNT":
                hint = max(hint, 0.6 if re.search(r"(?i)count|number|customers|records|orders|clients|total", target_label)
                           and re.search(r"(?i)\b(id|code|ref|reference|no|number)\b", f) else 0)
            if ok:
                out.append({"expression": expr, "confidence": round(0.78 + 0.17 * hint, 3), "matches": 1, "checked": 1,
                            "mismatches": [], "explanation": f"{fn} of the column equals the displayed value"
                                                             + ("; the field names agree" if hint >= 0.5 else "")})
            elif hint >= 0.6 and fn == "SUM":
                out.append({"expression": expr, "confidence": round(0.45 + 0.15 * hint, 3), "matches": 0,
                            "checked": 1, "mismatches": [{"shown": shown, "expected": format(v, "f")}],
                            "explanation": "The field names agree but the total does NOT equal the displayed value."})
    out.sort(key=lambda x: (-x["confidence"], x["expression"]))
    return out[:3]
