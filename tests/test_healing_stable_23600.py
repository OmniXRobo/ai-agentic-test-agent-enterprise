"""23.6.0: self-healing ignores label parts that change between visits (prices, "N left"); healing.py stays free of
platform imports because the AI-Driven framework workspace copies it verbatim."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from qa_platform.automation import healing


def _fp(**kw):
    base = {k: None for k in healing.WEIGHTS}
    base.update(kw)
    return SimpleNamespace(**base)


def test_price_in_a_label_does_not_lower_the_match():
    fp = _fp(name="Friday, November 20, 2026, The price is AED 1.65k", tag="div")
    assert healing.score(fp, {"name": "Friday, November 20, 2026, The price is AED 1.80k", "tag": "div"})[0] == 1.0
    assert healing.score(fp, {"name": "Friday, November 20, 2026", "tag": "div"})[0] == 1.0
    room = _fp(text="Deluxe room 3 left", tag="button")
    assert healing.score(room, {"text": "Deluxe room 1 left", "tag": "button"})[0] == 1.0


def test_healing_module_has_no_platform_imports():
    tree = ast.parse(Path(healing.__file__).read_text(encoding="utf-8"))
    mods = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    mods += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert all(m and not m.startswith(("qa_platform", ".")) for m in mods), mods
