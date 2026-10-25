"""Python source for Page Objects, flows and tests.

The Automation Engineering Agent decides *what* to automate (which element, which
action, which data, which business flow); the code itself is written here from that
plan, so every generated file has the same structure and always parses. Changes to
existing shared files are additive: new locators and methods are inserted into the
existing source text, so hand edits and existing members stay exactly as they were.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_WORD = re.compile(r"[A-Za-z0-9]+")
PY_KEYWORDS = {"False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue", "def",
               "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in", "is",
               "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
               "page", "data", "credentials", "self", "step"}


def words(text: str, limit: int = 5) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")][:limit]


def snake(text: str, default: str = "item", limit: int = 5) -> str:
    out = "_".join(words(text, limit)) or default
    if out[0].isdigit():
        out = f"{default}_{out}"
    return f"{out}_" if out in PY_KEYWORDS else out


def camel(text: str, default: str = "Item") -> str:
    out = "".join(w.capitalize() for w in words(text, 5)) or default
    return f"{default}{out}" if out[0].isdigit() else out


def py_str(value: Any) -> str:
    """A Python string literal (double quotes, like the rest of the generated code)."""
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def comment(text: str) -> str:
    return " ".join(str(text or "").replace("\\", "/").split())


def docstring(text: str) -> str:
    text = comment(text).replace('"""', "'''")
    if text and not text.endswith((".", "!", "?")):
        text += "."
    return f'"""{text}"""'


# ---- plans --------------------------------------------------------------------------------


@dataclass
class LocatorPlan:
    name: str
    strategy: str
    value: str
    role_name: str | None = None
    alternates: list[str] = field(default_factory=list)
    description: str = ""
    fingerprint: dict[str, Any] = field(default_factory=dict)


@dataclass
class MethodPlan:
    name: str
    params: list[str]
    doc: str
    body: list[str]  # lines without indentation


@dataclass
class FlowCall:
    var: str
    class_name: str
    method: str
    args: list[str]


@dataclass
class FlowPlan:
    name: str
    module: str
    params: list[str]
    doc: str
    calls: list[FlowCall]


@dataclass
class TestStepPlan:
    title: str
    lines: list[str]  # statements inside the step block
    checkpoint: bool = False
    tc_steps: list[int] = field(default_factory=list)


@dataclass
class TestPlan:
    function: str
    title: str
    header: list[str]  # traceability comment lines (without '# ')
    markers: list[str]
    imports: dict[str, set[str]]  # module -> names
    instances: list[tuple[str, str]]  # (variable, Page Object class)
    params: list[str]
    steps: list[TestStepPlan]
    doc: str = ""


def render_locator(loc: LocatorPlan, indent: str = "    ") -> str:
    head = [py_str(loc.strategy), py_str(loc.value)]
    if loc.role_name:
        head.append(f"name={py_str(loc.role_name)}")
    if loc.description:
        head.append(f"description={py_str(loc.description)}")
    lines = [f"{indent}{loc.name} = Loc(", f"{indent}    {', '.join(head)},"]
    if loc.alternates:
        lines.append(f"{indent}    alternates=[{', '.join(py_str(a) for a in loc.alternates)}],")
    if loc.fingerprint:
        fp = {k: v for k, v in loc.fingerprint.items() if v not in (None, "", [])}
        lines.append(f"{indent}    fingerprint={json.dumps(fp, ensure_ascii=False)},")
    lines.append(f"{indent})")
    return "\n".join(lines)


def render_method(method: MethodPlan, indent: str = "    ") -> str:
    params = ", ".join(["self", *method.params])
    lines = [f"{indent}def {method.name}({params}):", f"{indent}    {docstring(method.doc)}"]
    lines += [f"{indent}    {line}" for line in method.body] or [f"{indent}    pass"]
    return "\n".join(lines)


def render_page_object(class_name: str, url: str, doc: str, locators: list[LocatorPlan], methods: list[MethodPlan]) -> str:
    out = [
        docstring(f"Page Object: {doc}"),
        "",
        "from utilities.base_page import BasePage",
        "from utilities.locators import Loc",
        "",
        "",
        f"class {class_name}(BasePage):",
        f"    {docstring(doc)}",
        "",
        f"    URL = {py_str(url)}",
        "",
    ]
    if locators:
        out.append("    # Locators (strongest strategy first; alternates and fingerprint support self-healing)")
        out += [render_locator(loc) for loc in locators]
        out.append("")
    for method in methods:
        out.append(render_method(method))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def add_to_page_object(source: str, info: Any, locators: list[LocatorPlan], methods: list[MethodPlan]) -> str:
    """Insert new locators after the last existing locator and new methods at the end of the class."""
    lines = source.splitlines()
    if methods:
        block = [""] + [line for m in methods for line in ("", *render_method(m).splitlines())][1:]
        at = info.end_lineno  # 1-based last line of the class
        lines[at:at] = block
    if locators:
        block = [line for loc in locators for line in render_locator(loc).splitlines()]
        at = info.last_locator_lineno
        lines[at:at] = block
    return "\n".join(lines).rstrip() + "\n"


def replace_lines(source: str, start: int, end: int, new_block: str) -> str:
    lines = source.splitlines()
    lines[start - 1:end] = new_block.splitlines()
    return "\n".join(lines).rstrip() + "\n"


def render_flow_function(flow: FlowPlan) -> str:
    lines = [f"def {flow.name}({', '.join(['page', *flow.params])}):", f"    {docstring(flow.doc)}"]
    seen = set()
    for call in flow.calls:
        if call.var not in seen:
            lines.append(f"    {call.var} = {call.class_name}(page)")
            seen.add(call.var)
    for call in flow.calls:
        lines.append(f"    {call.var}.{call.method}({', '.join(call.args)})")
    return "\n".join(lines)


def page_imports(classes: dict[str, str]) -> list[str]:
    """``from page_objects.login_page import LoginPage`` lines (class -> module path)."""
    return [f"from {_module(path)} import {cls}" for cls, path in sorted(classes.items(), key=lambda x: (x[1], x[0]))]


def _module(path: str) -> str:
    return path[:-3].replace("/", ".") if path.endswith(".py") else path.replace("/", ".")


def render_flow_module(doc: str, classes: dict[str, str], flows: list[FlowPlan]) -> str:
    out = [docstring(f"Business flow: {doc}"), "", *page_imports(classes), "", ""]
    out.append("\n\n\n".join(render_flow_function(f) for f in flows))
    return "\n".join(out).rstrip() + "\n"


def add_to_flow_module(source: str, classes: dict[str, str], flows: list[FlowPlan]) -> str:
    lines = source.splitlines()
    existing = set(lines)
    imports = [line for line in page_imports(classes) if line not in existing]
    if imports:
        last_import = max((i for i, line in enumerate(lines) if line.startswith(("from ", "import "))), default=0)
        lines[last_import + 1:last_import + 1] = imports
    text = "\n".join(lines).rstrip() + "\n"
    for flow in flows:
        text += "\n\n" + render_flow_function(flow) + "\n"
    return text


def render_test(plan: TestPlan) -> str:
    out = [docstring(plan.title)]
    out += [f"# {comment(line)}" for line in plan.header]
    out += ["import pytest", ""]
    for module in sorted(plan.imports):
        out.append(f"from {module} import {', '.join(sorted(plan.imports[module]))}")
    out += ["", "pytestmark = ["]
    out += [f"    {m}," for m in plan.markers]
    out += ["]", "", ""]
    out.append(f"def {plan.function}({', '.join(plan.params)}):")
    out.append(f"    {docstring(plan.doc or plan.title)}")
    for var, cls in plan.instances:
        out.append(f"    {var} = {cls}(page)")
    if plan.instances:
        out.append("")
    for step in plan.steps:
        args = [py_str(step.title)]
        if step.checkpoint:
            args.append("checkpoint=True")
        if step.tc_steps:
            args.append(f"tc_step={step.tc_steps[0]}" if len(step.tc_steps) == 1 else
                        f"tc_step={py_str(','.join(str(s) for s in step.tc_steps))}")
        out.append(f"    with step({', '.join(args)}):")
        out += [f"        {line}" for line in step.lines] or ["        pass"]
    if not plan.steps:
        out.append("    pass")
    return "\n".join(out).rstrip() + "\n"
