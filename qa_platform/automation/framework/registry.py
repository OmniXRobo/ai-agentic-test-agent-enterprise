"""What the framework already contains, read from the files themselves.

The Automation Engineering Agent checks this before creating anything: existing Page
Objects (their URL, locators and methods), flows (their steps) and which tests use
which component. Reading the source files (not a separate index) means hand edits
are always taken into account.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BASE_PAGE_METHODS = {"open", "url_for", "expect_url", "expect_title", "click", "double_click", "fill", "select", "check",
                     "uncheck", "hover", "press", "expect_visible", "expect_hidden", "expect_text", "expect_value",
                     "screenshot", "page", "URL"}


@dataclass
class LocatorInfo:
    name: str
    strategy: str
    value: str
    role_name: str | None = None
    alternates: list[str] = field(default_factory=list)
    description: str = ""
    fingerprint: dict[str, Any] = field(default_factory=dict)
    lineno: int = 0
    end_lineno: int = 0

    def primary(self) -> str:
        """The primary locator as the selector text a run reports (same as utilities.locators.Loc.selector)."""
        s, v = self.strategy, self.value
        if s == "test_id":
            return f"[data-testid={json.dumps(v)}]"
        if s == "role":
            return f"role={v}[name={json.dumps(self.role_name)}]" if self.role_name else f"role={v}"
        if s in ("label", "placeholder", "text"):
            return f"{s}={json.dumps(v)}"
        if s == "xpath":
            return v if v.startswith(("xpath=", "//", "(")) else f"xpath={v}"
        return v  # id / css

    def selectors(self) -> set[str]:
        out = set(self.alternates)
        if self.strategy in ("css", "id", "xpath"):
            out.add(self.value)
        return out


@dataclass
class MethodInfo:
    name: str
    params: list[str]
    doc: str
    actions: list[tuple[str, str | None]]  # (base action, locator name)


@dataclass
class PageObjectInfo:
    class_name: str
    module: str  # workspace-relative path
    url: str
    doc: str
    locators: dict[str, LocatorInfo]
    methods: dict[str, MethodInfo]
    end_lineno: int
    last_locator_lineno: int


@dataclass
class FlowInfo:
    name: str
    module: str
    params: list[str]
    doc: str
    calls: list[tuple[str, str]]  # (Page Object class, method)
    page_objects: set[str]


@dataclass
class TestInfo:
    path: str
    functions: list[str]
    page_objects: set[str]
    flows: set[str]
    data_name: str | None
    test_case: str | None


@dataclass
class Registry:
    page_objects: dict[str, PageObjectInfo] = field(default_factory=dict)
    flows: dict[str, FlowInfo] = field(default_factory=dict)
    tests: dict[str, TestInfo] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def page_for_url(self, url: str) -> PageObjectInfo | None:
        return next((p for p in self.page_objects.values() if p.url == url), None)

    def flows_using(self, class_name: str) -> list[str]:
        return sorted(f.name for f in self.flows.values() if class_name in f.page_objects)

    def tests_using(self, component: str) -> list[str]:
        """Tests that use a Page Object (directly or through a flow) or a flow."""
        flows = set(self.flows_using(component)) if component in self.page_objects else {component}
        return sorted(t.path for t in self.tests.values()
                      if component in t.page_objects or t.flows & flows)


def _const(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None


def _doc(node: ast.AST) -> str:
    return (ast.get_docstring(node) or "").strip()  # type: ignore[arg-type]


def _imports(tree: ast.Module) -> dict[str, str]:
    """Imported name -> module (for 'from x import y')."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                out[alias.asname or alias.name] = node.module
    return out


def parse_locator(target: str, call: ast.Call, node: ast.AST) -> LocatorInfo | None:
    if not (isinstance(call.func, ast.Name) and call.func.id == "Loc") or len(call.args) < 2:
        return None
    kw = {k.arg: _const(k.value) for k in call.keywords if k.arg}
    return LocatorInfo(name=target, strategy=str(_const(call.args[0]) or ""), value=str(_const(call.args[1]) or ""),
                       role_name=kw.get("name"), alternates=list(kw.get("alternates") or []),
                       description=kw.get("description") or "", fingerprint=dict(kw.get("fingerprint") or {}),
                       lineno=node.lineno, end_lineno=node.end_lineno or node.lineno)


def _method_actions(fn: ast.FunctionDef) -> list[tuple[str, str | None]]:
    actions: list[tuple[str, str | None]] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
            loc = None
            if node.args and isinstance(node.args[0], ast.Attribute) and isinstance(node.args[0].value, ast.Name) \
                    and node.args[0].value.id == "self":
                loc = node.args[0].attr
            actions.append((node.func.attr, loc))
    return actions


def parse_page_object(rel: str, source: str) -> list[PageObjectInfo]:
    tree = ast.parse(source)
    out = []
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef):
            continue
        url, locators, methods, last_loc = "", {}, {}, 0
        for node in cls.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name == "URL":
                    url = str(_const(node.value) or "")
                    last_loc = max(last_loc, node.end_lineno or node.lineno)
                elif isinstance(node.value, ast.Call):
                    info = parse_locator(name, node.value, node)
                    if info:
                        locators[name] = info
                        last_loc = max(last_loc, info.end_lineno)
            elif isinstance(node, ast.FunctionDef):
                params = [a.arg for a in node.args.args[1:]]
                methods[node.name] = MethodInfo(node.name, params, _doc(node), _method_actions(node))
        out.append(PageObjectInfo(cls.name, rel, url, _doc(cls), locators, methods, cls.end_lineno or cls.lineno,
                                  last_loc or (cls.body[0].end_lineno or cls.lineno)))
    return out


def _calls(fn: ast.FunctionDef, imported: dict[str, str]) -> tuple[list[tuple[str, str]], set[str]]:
    instances: dict[str, str] = {}
    used: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) \
                and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            cls = node.value.func.id
            if imported.get(cls, "").startswith("page_objects"):
                instances[node.targets[0].id] = cls
                used.add(cls)
    ordered = []  # ast.walk is breadth-first, so sort by position to keep the source order
    for stmt in ast.walk(fn):
        if isinstance(stmt, ast.Call) and isinstance(stmt.func, ast.Attribute) and isinstance(stmt.func.value, ast.Name) \
                and stmt.func.value.id in instances:
            ordered.append((stmt.lineno, stmt.col_offset, instances[stmt.func.value.id], stmt.func.attr))
    calls = [(c, m) for _, _, c, m in sorted(ordered)]
    return calls, used


def parse_flows(rel: str, source: str) -> list[FlowInfo]:
    tree = ast.parse(source)
    imported = _imports(tree)
    out = []
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef) and not fn.name.startswith("_"):
            calls, used = _calls(fn, imported)
            out.append(FlowInfo(fn.name, rel, [a.arg for a in fn.args.args], _doc(fn), calls, used))
    return out


def parse_test(rel: str, source: str) -> TestInfo:
    tree = ast.parse(source)
    imported = _imports(tree)
    data_name = test_case = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args and \
                isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "mark":
            if node.func.attr == "test_data":
                data_name = str(_const(node.args[0]) or "") or None
            elif node.func.attr == "test_case":
                test_case = str(_const(node.args[0]) or "") or None
    return TestInfo(
        path=rel,
        functions=[n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test")],
        page_objects={n for n, m in imported.items() if m.startswith("page_objects")},
        flows={n for n, m in imported.items() if m.startswith("flows")},
        data_name=data_name, test_case=test_case)


def scan(root: Path) -> Registry:
    reg = Registry()
    for folder, parser in (("page_objects", parse_page_object), ("flows", parse_flows)):
        for path in sorted((root / folder).glob("*.py")) if (root / folder).is_dir() else []:
            if path.name == "__init__.py":
                continue
            rel = f"{folder}/{path.name}"
            try:
                items = parser(rel, path.read_text(encoding="utf-8"))
            except (SyntaxError, ValueError, OSError) as exc:
                reg.errors[rel] = str(exc)
                continue
            for item in items:
                if folder == "page_objects":
                    reg.page_objects[item.class_name] = item  # type: ignore[union-attr]
                else:
                    reg.flows[item.name] = item  # type: ignore[union-attr]
    tests = root / "tests"
    for path in sorted(tests.glob("test_*.py")) if tests.is_dir() else []:
        rel = f"tests/{path.name}"
        try:
            reg.tests[rel] = parse_test(rel, path.read_text(encoding="utf-8"))
        except (SyntaxError, ValueError, OSError) as exc:
            reg.errors[rel] = str(exc)
    return reg
