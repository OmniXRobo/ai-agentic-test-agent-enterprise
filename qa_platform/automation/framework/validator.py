"""Validation of framework files before they are saved.

Checks, in order: Python syntax and indentation, imports, Page Object references,
flow references, fixture references, test data references, Playwright API usage,
Test Case traceability and, last, that pytest really collects the test from a full
copy of the workspace (so imports and fixtures are proven, not assumed).
"""

from __future__ import annotations

import ast
import builtins
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PYTEST_BUILTIN_FIXTURES = {"request", "tmp_path", "tmp_path_factory", "monkeypatch", "capsys", "capfd", "caplog",
                           "recwarn", "pytestconfig", "record_property", "cache", "doctest_namespace"}


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, str]] = []

    def add(self, name: str, ok: bool, detail: str, *, error_code: str = "") -> None:
        self.checks.append({"name": name, "status": "PASS" if ok else "FAIL", "detail": detail,
                            **({"error_code": error_code} if not ok and error_code else {})})

    @property
    def ok(self) -> bool:
        return all(c["status"] == "PASS" for c in self.checks)

    def failures(self) -> list[dict[str, str]]:
        return [c for c in self.checks if c["status"] == "FAIL"]

    def first_error_code(self) -> str:
        return next((c.get("error_code") or "" for c in self.failures()), "") or "PYTHON_VALIDATION_FAILED"


def _module_file(root: Path, module: str) -> Path | None:
    base = root.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _defined_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.ImportFrom):
            names |= {a.asname or a.name for a in node.names}
        elif isinstance(node, ast.Import):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
    return names


def _undefined_names(tree: ast.Module) -> list[tuple[int, str, bool]]:
    """Names read inside module-level functions that are never defined: (line, name, used as an object)."""
    module_names = _defined_names(tree) | set(dir(builtins))
    out = []
    for fn in [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        local = {a.arg for a in [*fn.args.args, *fn.args.kwonlyargs, *fn.args.posonlyargs]}
        local |= {x.arg for x in (fn.args.vararg, fn.args.kwarg) if x}
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                local.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node is not fn:
                local.add(node.name)
                if not isinstance(node, ast.ClassDef):
                    local |= {a.arg for a in [*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs]}
                    local |= {x.arg for x in (node.args.vararg, node.args.kwarg) if x}
            elif isinstance(node, ast.Lambda):
                local |= {a.arg for a in [*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs]}
            elif isinstance(node, ast.ExceptHandler) and node.name:
                local.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                local |= {(a.asname or a.name).split(".")[0] for a in node.names}
        receivers = {id(n.value) for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in local \
                    and node.id not in module_names:
                out.append((node.lineno, node.id, id(node) in receivers))
    return out


def _class_members(tree: ast.Module) -> dict[str, tuple[set[str], list[str]]]:
    """class -> (members, base class names)."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            members = set()
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    members.add(item.name)
                elif isinstance(item, ast.Assign):
                    members |= {t.id for t in item.targets if isinstance(t, ast.Name)}
            out[node.name] = (members, [b.id for b in node.bases if isinstance(b, ast.Name)])
    return out


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError, ValueError):
        return None


def _base_page_members(root: Path) -> set[str]:
    tree = _parse(root / "utilities" / "base_page.py")
    if tree is None:
        return set()
    return _class_members(tree).get("BasePage", (set(), []))[0] | {"page"}


def _members_of(root: Path, module: str, cls: str, base_members: set[str], seen: int = 0) -> set[str] | None:
    path = _module_file(root, module)
    tree = _parse(path) if path else None
    if tree is None:
        return None
    classes = _class_members(tree)
    if cls not in classes:
        return None
    members, bases = classes[cls]
    out = set(members)
    imports = {a.asname or a.name: n.module for n in tree.body if isinstance(n, ast.ImportFrom) and n.module
               for a in n.names}
    for base in bases:
        if base == "BasePage":
            out |= base_members
        elif base in classes and seen < 5:
            out |= _members_of(root, module, base, base_members, seen + 1) or set()
        elif base in imports and seen < 5:
            out |= _members_of(root, imports[base], base, base_members, seen + 1) or set()
    return out


def _method_def(root: Path, module: str, cls: str, name: str) -> ast.FunctionDef | None:
    path = _module_file(root, module)
    tree = _parse(path) if path else None
    for node in (tree.body if tree else []):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return next((m for m in node.body if isinstance(m, ast.FunctionDef) and m.name == name), None)
    return None


def _arity_problem(fdef: ast.FunctionDef, call: ast.Call, skip_self: bool) -> str | None:
    params = fdef.args.args[1:] if skip_self else fdef.args.args
    required = len(params) - len(fdef.args.defaults)
    given = len(call.args) + len([k for k in call.keywords if k.arg])
    if fdef.args.vararg or any(k.arg is None for k in call.keywords) or required <= given <= len(params):
        return None
    return f"takes {len(params)} argument(s), {given} given"


def _fixtures(root: Path) -> set[str]:
    names = set(PYTEST_BUILTIN_FIXTURES)
    files = list((root / "fixtures").glob("*.py")) + [root / "conftest.py"]
    for path in files:
        tree = _parse(path) if path.is_file() else None
        if tree is None:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                for dec in node.decorator_list:
                    target = dec.func if isinstance(dec, ast.Call) else dec
                    if (isinstance(target, ast.Attribute) and target.attr == "fixture") or \
                            (isinstance(target, ast.Name) and target.id == "fixture"):
                        names.add(node.name)
    return names


def _playwright_members() -> tuple[set[str], set[str]] | None:
    try:
        from playwright.sync_api import Locator, LocatorAssertions, Page, PageAssertions
    except ImportError:
        return None
    return set(dir(Page)) | set(dir(Locator)), set(dir(LocatorAssertions)) | set(dir(PageAssertions))


def validate(root: Path, files: list[str], *, test_rel: str | None = None, collect: bool = True,
             collect_tests: list[str] | None = None) -> Report:
    """Validate ``files`` (workspace-relative) inside ``root`` (a staging copy or the workspace)."""
    report = Report()
    if test_rel and test_rel not in files:  # a data-only change is checked against the test that reads it
        files = [*files, test_rel]
    py_files = [f for f in files if f.endswith(".py")]
    trees: dict[str, ast.Module] = {}

    # 1. Python syntax and indentation.
    problems = []
    for rel in py_files:
        source = (root / rel).read_text(encoding="utf-8")
        try:
            trees[rel] = ast.parse(source, filename=rel)
            compile(source, rel, "exec")
        except (IndentationError, TabError) as exc:
            problems.append(f"{rel} line {exc.lineno}: indentation error: {exc.msg}")
        except SyntaxError as exc:
            problems.append(f"{rel} line {exc.lineno}: {exc.msg}")
    report.add("Python syntax", not problems, "; ".join(problems) or f"{len(py_files)} file(s) parse.",
               error_code="PYTHON_SYNTAX_INVALID")
    for rel in [f for f in files if f.endswith(".json")]:
        try:
            json.loads((root / rel).read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            report.add("Test data file", False, f"{rel}: {exc}", error_code="TEST_DATA_INVALID")
    if problems:
        return report

    # 2. Imports.
    problems = []
    for rel, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                path = _module_file(root, node.module)
                if path is not None:
                    target = _parse(path)
                    defined = _defined_names(target) if target else set()
                    for alias in node.names:
                        if alias.name != "*" and alias.name not in defined and \
                                _module_file(root, f"{node.module}.{alias.name}") is None:
                            problems.append(f"{rel}: '{alias.name}' is not defined in {node.module}")
                elif importlib.util.find_spec(node.module.split(".")[0]) is None:
                    problems.append(f"{rel}: module '{node.module}' cannot be imported")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if _module_file(root, alias.name) is None and importlib.util.find_spec(top) is None:
                        problems.append(f"{rel}: module '{alias.name}' cannot be imported")
    report.add("Imports", not problems, "; ".join(problems) or "All imports resolve.",
               error_code="IMPORT_VALIDATION_FAILED")

    # 3-4. Page Object and flow references.
    base_members = _base_page_members(root)
    po_problems, flow_problems, undefined = [], [], []
    for rel, tree in trees.items():
        imported = {a.asname or a.name: (n.module, a.name) for n in tree.body if isinstance(n, ast.ImportFrom)
                    and n.module for a in n.names}
        page_classes = {k: v for k, v in imported.items() if v[0].startswith("page_objects")}
        flows = {k: v for k, v in imported.items() if v[0].startswith("flows")}
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            instances: dict[str, str] = {}
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and \
                        isinstance(node.value.func, ast.Name) and node.value.func.id in page_classes:
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            instances[t.id] = node.value.func.id
            for node in ast.walk(fn):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in instances:
                    cls = instances[node.value.id]
                    members = _members_of(root, page_classes[cls][0], page_classes[cls][1], base_members)
                    if members is None:
                        po_problems.append(f"{rel}: Page Object {cls} cannot be read")
                    elif node.attr not in members:
                        po_problems.append(f"{rel} line {node.lineno}: {cls} has no '{node.attr}'")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                        isinstance(node.func.value, ast.Name) and node.func.value.id in instances:
                    cls = instances[node.func.value.id]
                    fdef = _method_def(root, page_classes[cls][0], page_classes[cls][1], node.func.attr)
                    problem = _arity_problem(fdef, node, True) if fdef else None
                    if problem:
                        po_problems.append(f"{rel} line {node.lineno}: {cls}.{node.func.attr}() {problem}")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in flows:
                    module, name = flows[node.func.id]
                    path = _module_file(root, module)
                    target = _functions(_parse(path)) if path and _parse(path) else {}
                    fdef = target.get(name)
                    if fdef is None:
                        flow_problems.append(f"{rel} line {node.lineno}: flow '{name}' is not defined in {module}")
                        continue
                    required = len(fdef.args.args) - len(fdef.args.defaults)
                    given = len(node.args) + len(node.keywords)
                    if not (required <= given <= len(fdef.args.args)) and not fdef.args.vararg:
                        flow_problems.append(f"{rel} line {node.lineno}: flow '{name}' takes {len(fdef.args.args)} "
                                             f"argument(s), {given} given")
        for line, name, is_object in _undefined_names(tree):
            message = f"{rel} line {line}: '{name}' is not defined"
            if is_object:
                po_problems.append(message)
            else:
                undefined.append(message)
        if rel.startswith("page_objects/"):
            for cls_node in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
                module = rel[:-3].replace("/", ".")
                members = _members_of(root, module, cls_node.name, base_members) or set()
                for node in ast.walk(cls_node):
                    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self" \
                            and isinstance(node.ctx, ast.Load) and node.attr not in members:
                        po_problems.append(f"{rel} line {node.lineno}: {cls_node.name} has no '{node.attr}'")
    report.add("Defined names", not undefined, "; ".join(undefined) or "Every name used is defined or imported.",
               error_code="IMPORT_VALIDATION_FAILED")
    report.add("Page Object references", not po_problems, "; ".join(po_problems) or "All Page Object members exist.",
               error_code="PAGE_OBJECT_REFERENCE_INVALID")
    report.add("Flow references", not flow_problems, "; ".join(flow_problems) or "All flows exist with matching arguments.",
               error_code="FLOW_REFERENCE_INVALID")

    # 5-6. Fixtures and test data of the test.
    if test_rel and test_rel in trees:
        tree = trees[test_rel]
        known = _fixtures(root)
        tests = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test")]
        unknown = sorted({a.arg for fn in tests for a in fn.args.args} - known)
        report.add("Fixture references", not unknown and bool(tests),
                   (f"Unknown fixture(s): {', '.join(unknown)}" if unknown else "") or
                   ("No test function found." if not tests else "All fixtures exist."),
                   error_code="FIXTURE_REFERENCE_INVALID")
        data_name = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "test_data" \
                    and node.args and isinstance(node.args[0], ast.Constant):
                data_name = str(node.args[0].value)
        keys = {n.slice.value for n in ast.walk(tree) if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                and n.value.id == "data" and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str)}
        problems = []
        if keys and not data_name:
            problems.append("the test reads data but has no @pytest.mark.test_data marker")
        elif data_name:
            path = root / "test_data" / f"{data_name}.json"
            try:
                combos = json.loads(path.read_text(encoding="utf-8")).get("combinations") or []
            except (OSError, ValueError):
                combos = None
                problems.append(f"test_data/{data_name}.json is missing or invalid")
            if combos is not None:
                if not combos and keys:
                    problems.append(f"test_data/{data_name}.json has no combinations")
                for combo in combos:
                    missing = sorted(keys - set((combo.get("values") or {}).keys()))
                    if missing:
                        problems.append(f"combination '{combo.get('name')}' has no {', '.join(missing)}")
        report.add("Test data references", not problems, "; ".join(problems) or
                   (f"{len(keys)} field(s) present in every combination." if keys else "The test reads no data fields."),
                   error_code="TEST_DATA_MISSING")

    # 7. Playwright API usage.
    members = _playwright_members()
    if members is not None:
        page_members, assertion_members = members
        problems = []
        for rel, tree in trees.items():
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    owner = node.value
                    is_page = (isinstance(owner, ast.Name) and owner.id == "page") or (
                        isinstance(owner, ast.Attribute) and owner.attr == "page" and isinstance(owner.value, ast.Name)
                        and owner.value.id == "self")
                    if is_page and not node.attr.startswith("_") and node.attr not in page_members:
                        problems.append(f"{rel} line {node.lineno}: Playwright Page has no '{node.attr}'")
                    if isinstance(owner, ast.Call) and isinstance(owner.func, ast.Name) and owner.func.id == "expect" \
                            and node.attr not in assertion_members:
                        problems.append(f"{rel} line {node.lineno}: Playwright expect() has no '{node.attr}'")
        report.add("Playwright syntax", not problems, "; ".join(problems) or "Playwright calls exist.",
                   error_code="PLAYWRIGHT_SYNTAX_INVALID")

    # 8. Traceability.
    if test_rel and test_rel in trees:
        source = (root / test_rel).read_text(encoding="utf-8")
        missing = [label for label, needle in (("Test Case comment", "# Test Case: "),
                                               ("test_case marker", "pytest.mark.test_case("),
                                               ("User Story comment", "# User Story: ")) if needle not in source]
        report.add("Test Case traceability", not missing, f"Missing: {', '.join(missing)}" if missing else
                   "Test Case, User Story and Acceptance Criteria recorded.", error_code="TRACEABILITY_MISSING")

    # 9. Collection by pytest from a full copy of the workspace.
    targets = collect_tests if collect_tests is not None else ([test_rel] if test_rel else [])
    if collect and targets and report.ok:
        ok, detail = collect_only(root, targets)
        report.add("pytest collection", ok, detail, error_code="IMPORT_VALIDATION_FAILED")
    return report


def collect_only(root: Path, tests: list[str], timeout: int = 120) -> tuple[bool, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("QA_", "PYTEST_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *tests],
                              cwd=root, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"pytest could not run: {exc}"
    out = (proc.stdout + "\n" + proc.stderr).strip()
    if proc.returncode != 0:
        lines = [line for line in out.splitlines() if line.strip()]
        interesting = [line for line in lines if "Error" in line or line.startswith("E ")] or lines[-5:]
        return False, " | ".join(interesting[:6])[:800]
    collected = sum(1 for line in out.splitlines() if "::" in line)
    return True, f"pytest collected {collected} test(s)."


def summarize(report: Report) -> dict[str, Any]:
    return {"ok": report.ok, "checks": report.checks}
