"""Turns the Automation Engineering Agent's step mapping into framework changes.

Input: the Test Case (with its User Story, Acceptance Criteria and Test Data), the
element inventory discovered on the application and the AI's mapping of each manual
step to an action on a discovered element. Output: the files to create or extend
(Page Objects, flows, the test, its data) plus the impact on existing tests.

Framework reuse rule: before anything is created the registry is checked. A page
with the same URL reuses its Page Object; an element already declared (same
selector, test id, id, or role and name) reuses its locator; a method that does the
same action on the same locator is reused; a flow with the same steps is reused.
Shared components are only ever extended, never changed, so existing tests keep
working; every extension is listed with the tests that use the component.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, Field

from ...errors import QAError
from . import codegen
from .codegen import FlowCall, FlowPlan, LocatorPlan, MethodPlan, TestPlan, TestStepPlan
from .registry import BASE_PAGE_METHODS, PageObjectInfo, Registry

ACTIONS = ("goto", "click", "dblclick", "fill", "type", "select", "check", "uncheck", "press", "hover",
           "expect_visible", "expect_hidden", "expect_text", "expect_value", "expect_url")
ELEMENT_ACTIONS = set(ACTIONS) - {"goto", "expect_url"}
CHECKPOINT_ACTIONS = {"expect_visible", "expect_hidden", "expect_text", "expect_value", "expect_url"}
_CRED = re.compile(r"^\{\{\s*CRED:([^}.]+)\.(username|password)\s*\}\}$")
_DATA = re.compile(r"^\{\{\s*DATA:([^}]+?)\s*\}\}$")
_GENERATED_ID = re.compile(r"\d{4,}|^[0-9]|[a-f0-9]{8,}", re.I)
ROLE_STRATEGY = {"button", "link", "checkbox", "radio", "combobox", "textbox", "tab", "menuitem", "option", "switch",
                 "searchbox", "spinbutton", "slider", "heading"}


# ---- AI output ------------------------------------------------------------------------------


class MappedStep(BaseModel):
    action: str = Field(description="goto, click, dblclick, fill, select, check, uncheck, press, hover, "
                                    "expect_visible, expect_hidden, expect_text, expect_value, expect_url")
    selector: str | None = Field(default=None, description="Exactly one selector copied from the ELEMENT INVENTORY")
    value: str | None = Field(default=None, description="Text to type/select/expect, a URL for goto, "
                                                        "{{DATA:field}}, {{CRED:role.username}} or a date rule")
    description: str = Field(description="What this step does, in plain business language")
    manual_step: int | None = Field(default=None, description="Number of the manual test step this implements")
    business_flow: str | None = Field(default=None, description="Short name of the reusable business process this "
                                                               "step belongs to, e.g. login, add product to cart")
    element_name: str | None = Field(default=None, description="Short readable name of the element, e.g. username field")


class MappedTest(BaseModel):
    steps: list[MappedStep] = Field(min_length=1)
    test_data: dict[str, str] = Field(default_factory=dict,
                                      description="Data field -> value taken from the test case or its test data")
    covered_acceptance_criteria: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---- inputs ---------------------------------------------------------------------------------


@dataclass
class CaseContext:
    test_case_id: str
    test_case_title: str
    steps: list[dict[str, str]]
    user_story_id: str | None = None
    user_story_title: str | None = None
    sprint: str | None = None
    project: str | None = None
    acceptance_criteria: list[dict[str, str]] = field(default_factory=list)
    datasets: list[dict[str, Any]] = field(default_factory=list)
    base_url: str = ""
    application: str | None = None
    environment: str | None = None
    credential_role: str | None = None
    variant: int = 1
    generated_at: str = ""


@dataclass
class InventoryElement:
    page_url: str
    page_title: str
    element: dict[str, Any]


class Inventory:
    """Elements discovered on the application, by selector, with the page they are on."""

    def __init__(self, discovery: dict[str, Any] | None) -> None:
        self.by_selector: dict[str, InventoryElement] = {}
        self.pages: dict[str, str] = {}
        self.lines: list[str] = []
        for page in (discovery or {}).get("pages", []):
            if page.get("error"):
                continue
            self.pages[page["url"]] = page.get("title") or ""
            self.lines.append(f"PAGE {page['url']} ({page.get('title') or ''})")
            for el in page.get("elements", []):
                if len(self.by_selector) >= 400:
                    break
                desc = el.get("name") or el.get("text") or el.get("label") or el.get("placeholder") or ""
                self.lines.append(f"  {el['selector']} | {el.get('role') or el.get('tag')} | {desc[:60]}")
                item = InventoryElement(page["url"], page.get("title") or "", el)
                self.by_selector.setdefault(el["selector"], item)
                for alt in el.get("alternates", []):
                    self.by_selector.setdefault(alt, item)

    def text(self) -> str:
        return "\n".join(self.lines)

    def __len__(self) -> int:
        return len(self.by_selector)


@dataclass
class BuildResult:
    changes: dict[str, str]
    test_rel: str
    data_rel: str
    data: dict[str, Any]
    components: list[dict[str, Any]]
    impact: list[dict[str, Any]]
    notes: list[str]
    flow_logic: list[str]
    credential_role: str | None
    covered_acceptance_criteria: list[str]


# ---- helpers --------------------------------------------------------------------------------


def relative_url(url: str, base_url: str) -> str:
    """Page URL relative to the application base URL (so Page Objects work in every environment)."""
    absolute = urljoin(base_url.rstrip("/") + "/", url or "")
    parts, base = urlsplit(absolute), urlsplit(base_url.rstrip("/") + "/")
    if (parts.scheme, parts.netloc) != (base.scheme, base.netloc):
        return absolute
    path = parts.path
    if path.startswith(base.path):
        path = path[len(base.path):]
    return path.lstrip("/")


def page_class_name(rel_url: str, title: str) -> str:
    path = urlsplit(rel_url).path if "://" in rel_url else rel_url.split("?")[0].split("#")[0]
    segments = [s for s in path.split("/") if s]
    last = re.sub(r"\.(html?|aspx?|php|jsp)$", "", segments[-1], flags=re.I) if segments else ""
    if last.lower() in ("", "index", "default", "home"):
        last = segments[-2] if len(segments) > 1 else (title or "home")
    name = codegen.camel(last, "Home")
    return name if name.endswith("Page") else f"{name}Page"


def element_label(fp: dict[str, Any], hint: str | None) -> str:
    for value in (hint, fp.get("label"), fp.get("name"), fp.get("placeholder"), fp.get("text"), fp.get("test_id"),
                  fp.get("element_id")):
        if value and str(value).strip():
            return str(value).strip()
    return fp.get("tag") or "element"


def choose_locator(fp: dict[str, Any], selector: str) -> tuple[str, str, str | None]:
    """Locator priority: test id, role + name, label, placeholder, stable id, stable CSS, XPath last."""
    if fp.get("test_id"):
        return "test_id", fp["test_id"], None
    role, name = fp.get("role"), (fp.get("name") or "").strip()
    if fp.get("input_type") == "password":
        role = None  # a password input has no ARIA role, so get_by_role would never find it
    if role in ROLE_STRATEGY and name and len(name) <= 60:
        return "role", role, name
    if fp.get("label") and len(fp["label"]) <= 60:
        return "label", fp["label"], None
    if fp.get("placeholder"):
        return "placeholder", fp["placeholder"], None
    if fp.get("element_id") and not _GENERATED_ID.search(fp["element_id"]):
        return "id", f"#{fp['element_id']}", None
    if selector.startswith(("//", "xpath=", "(/")):
        return "xpath", selector, None
    return "css", selector, None


def fingerprint_from_selector(selector: str, description: str) -> dict[str, Any]:
    fp: dict[str, Any] = {}
    m = re.match(r"^#([A-Za-z][\w-]*)$", selector or "")
    if m:
        fp["element_id"] = m.group(1)
    m = re.match(r'^\[data-testid=["\']?([^"\'\]]+)', selector or "")
    if m:
        fp["test_id"] = m.group(1)
    if description:
        fp["text"] = description[:120]
    return fp


METHOD_PREFIX = {
    "fill": ("enter_{}", ["value"], "fill", "Enter {} value"),
    "type": ("enter_{}", ["value"], "fill", "Enter {} value"),
    "select": ("select_{}", ["value"], "select", "Select a value in {}"),
    "click": ("click_{}", [], "click", "Click {}"),
    "dblclick": ("double_click_{}", [], "double_click", "Double-click {}"),
    "check": ("check_{}", [], "check", "Check {}"),
    "uncheck": ("uncheck_{}", [], "uncheck", "Uncheck {}"),
    "hover": ("hover_{}", [], "hover", "Hover over {}"),
    "press": ("press_key_in_{}", ["key"], "press", "Press a key in {}"),
    "expect_visible": ("expect_{}_visible", [], "expect_visible", "Verify {} is visible"),
    "expect_hidden": ("expect_{}_hidden", [], "expect_hidden", "Verify {} is hidden"),
    "expect_text": ("expect_{}_text", ["text"], "expect_text", "Verify {} shows the expected text"),
    "expect_value": ("expect_{}_value", ["value"], "expect_value", "Verify {} has the expected value"),
}


# ---- builder --------------------------------------------------------------------------------


@dataclass
class _Page:
    class_name: str
    module: str
    url: str
    title: str
    existing: PageObjectInfo | None
    new_locators: list[LocatorPlan] = field(default_factory=list)
    new_methods: list[MethodPlan] = field(default_factory=list)
    locators: dict[str, dict[str, Any]] = field(default_factory=dict)  # name -> {selectors, fp}
    methods: dict[str, tuple[str, str | None]] = field(default_factory=dict)  # name -> (action, locator)


@dataclass
class _Action:
    page: _Page
    method: str
    args: list[str]
    title: str
    checkpoint: bool
    manual_step: int | None
    flow: str | None


class FrameworkBuilder:
    def __init__(self, registry: Registry, inventory: Inventory, read: Any) -> None:
        self.registry = registry
        self.inventory = inventory
        self.read = read  # rel path -> current source
        self.pages: dict[str, _Page] = {}
        self.notes: list[str] = []
        self.data_values: dict[str, Any] = {}
        self.data_labels: dict[str, str] = {}
        self.uses_credentials = False
        self.credential_roles: list[str] = []

    # ---- pages, locators, methods ------------------------------------------------------

    def _page(self, url: str, base_url: str) -> _Page:
        rel = relative_url(url, base_url)
        for p in self.pages.values():
            if p.url == rel:
                return p
        existing = self.registry.page_for_url(rel)
        title = self.inventory.pages.get(urljoin(base_url.rstrip("/") + "/", rel), "")
        if existing:
            page = _Page(existing.class_name, existing.module, rel, title, existing)
            page.locators = {n: {"selectors": l.selectors(), "fp": l.fingerprint, "strategy": l.strategy,
                                 "value": l.value, "role_name": l.role_name} for n, l in existing.locators.items()}
            page.methods = {n: (m.actions[0][0], m.actions[0][1]) if len(m.actions) == 1 else ("", None)
                            for n, m in existing.methods.items()}
        else:
            name = page_class_name(rel, title)
            taken = set(self.registry.page_objects) | {p.class_name for p in self.pages.values()}
            base, n = name, 2
            while name in taken:
                name, n = f"{base[:-4]}{n}Page", n + 1
            module = f"page_objects/{codegen.snake(name[:-4] if name.endswith('Page') else name, 'home')}_page.py"
            page = _Page(name, module, rel, title, None)
        self.pages[page.class_name] = page
        return page

    def _locator(self, page: _Page, selector: str, fp: dict[str, Any], label: str) -> str:
        selectors = {selector, *(fp.get("alternates") or [])}
        for name, known in page.locators.items():
            kfp = known.get("fp") or {}
            same = bool(selectors & set(known["selectors"])) \
                or (fp.get("test_id") and fp.get("test_id") == kfp.get("test_id")) \
                or (fp.get("element_id") and fp.get("element_id") == kfp.get("element_id")) \
                or (fp.get("role") and fp.get("name") and (fp.get("role"), fp.get("name")) == (kfp.get("role"), kfp.get("name")))
            if same:
                return name
        strategy, value, role_name = choose_locator(fp, selector)
        base = codegen.snake(label, "element", 4).upper()
        name, n = base, 2
        while name in page.locators or name in BASE_PAGE_METHODS:
            name, n = f"{base}_{n}", n + 1
        primary = value if strategy in ("css", "id", "xpath") else None
        alternates = [s for s in [selector, *(fp.get("alternates") or [])] if s and s != primary][:4]
        fingerprint = {k: fp.get(k) for k in ("tag", "text", "role", "name", "element_id", "test_id", "label",
                                              "placeholder", "input_type", "classes", "href") if fp.get(k)}
        plan = LocatorPlan(name, strategy, value, role_name, list(dict.fromkeys(alternates)), label[:80], fingerprint)
        page.new_locators.append(plan)
        page.locators[name] = {"selectors": {selector, *alternates}, "fp": fingerprint, "strategy": strategy,
                               "value": value, "role_name": role_name}
        return name

    def _method(self, page: _Page, action: str, locator: str, label: str) -> tuple[str, list[str]]:
        template, params, base_action, doc = METHOD_PREFIX[action]
        for name, known in page.methods.items():
            if known == (base_action, locator):
                existing_params = page.existing.methods[name].params if page.existing and name in page.existing.methods \
                    else params
                return name, existing_params
        stem = codegen.snake(label, "element", 4)
        name, n = template.format(stem), 2
        while name in page.methods or name in BASE_PAGE_METHODS:
            name, n = f"{template.format(stem)}_{n}", n + 1
        args = ", ".join([f"self.{locator}", *params])
        page.new_methods.append(MethodPlan(name, params, doc.format(label), [f"self.{base_action}({args})"]))
        page.methods[name] = (base_action, locator)
        return name, params

    # ---- values --------------------------------------------------------------------------

    def _data_field(self, name: str, value: Any, label: str) -> str:
        key = codegen.snake(name, "value", 4)
        base, n = key, 2
        while key in self.data_values and self.data_values[key] != value:
            key, n = f"{base}_{n}", n + 1
        self.data_values[key] = value
        self.data_labels.setdefault(key, label)
        return key

    def _value_expr(self, value: str | None, field_name: str, label: str, known_data: set[str], missing: list[str]) -> str:
        if value is None:
            return '""'
        m = _CRED.match(value.strip())
        if m:
            self.uses_credentials = True
            if m.group(1) not in self.credential_roles:
                self.credential_roles.append(m.group(1))
            return f"credentials.{m.group(2)}"
        m = _DATA.match(value.strip())
        if m:
            key = m.group(1).strip()
            if key not in known_data:
                missing.append(key)
            self.data_labels.setdefault(key, label)
            return f"data[{codegen.py_str(key)}]"
        if "{{CRED:" in value:
            raise QAError("TEST_DATA_INVALID", f"A credential can only be used as a whole value ({value}).", status=422)
        return f"data[{codegen.py_str(self._data_field(field_name, value, label))}]"

    # ---- main --------------------------------------------------------------------------

    def build(self, ctx: CaseContext, mapped: MappedTest) -> BuildResult:
        tc = ctx.test_case_id
        if not ctx.base_url:
            raise QAError("APPLICATION_URL_MISSING", "The application URL is required to generate automation.", status=422)
        known_data: set[str] = set()
        for ds in ctx.datasets:
            known_data |= set((ds.get("values") or {}).keys())
        known_data |= set(mapped.test_data)
        missing: list[str] = []
        actions: list[_Action] = []
        current: _Page | None = None
        for i, step in enumerate(mapped.steps):
            action = step.action.strip().lower()
            if action not in ACTIONS:
                self.notes.append(f"Unsupported action '{step.action}' skipped: {step.description}")
                continue
            title = codegen.comment(step.description) or action
            if action == "goto":
                page = self._page(step.value or ctx.base_url, ctx.base_url)
                actions.append(_Action(page, "open", [], title, False, step.manual_step, step.business_flow))
                current = page
                continue
            if action == "expect_url":
                page = current or self._page(ctx.base_url, ctx.base_url)
                actions.append(_Action(page, "expect_url", [codegen.py_str(step.value or "")], title, True,
                                       step.manual_step, step.business_flow))
                continue
            if not step.selector:
                self.notes.append(f"Step '{title}' has no element; skipped for review.")
                continue
            found = self.inventory.by_selector.get(step.selector)
            if found:
                fp = dict(found.element)
                page = self._page(found.page_url, ctx.base_url)
            else:
                fp = fingerprint_from_selector(step.selector, step.element_name or step.description)
                page = current or self._page(ctx.base_url, ctx.base_url)
                self.notes.append(f"Selector not in the discovered inventory (review it): {step.selector} ({title})")
            label = element_label(fp, step.element_name)
            locator = self._locator(page, step.selector, fp, label)
            method, params = self._method(page, action, locator, label)
            args: list[str] = []
            if params:
                if action == "press":
                    args = [codegen.py_str(step.value or "Enter")]
                else:
                    field_name = label + ("" if action in ("fill", "type", "select") else " expected")
                    args = [self._value_expr(step.value, field_name, label, known_data, missing)]
            actions.append(_Action(page, method, args, title, action in CHECKPOINT_ACTIONS, step.manual_step,
                                   step.business_flow))
            current = page
        if not actions:
            raise QAError("PYTHON_GENERATION_FAILED", f"No automatable step was produced for Test Case #{tc}.",
                          status=422, details=self.notes)
        if missing:
            raise QAError("TEST_DATA_MISSING", f"Test Data missing for Test Case #{tc}: {', '.join(sorted(set(missing)))}.",
                          status=422, details={"fields": sorted(set(missing))})
        for key, value in mapped.test_data.items():
            if key in self.data_labels or key not in self.data_values:
                self.data_values.setdefault(key, value)
        return self._assemble(ctx, mapped, actions)

    def _flows(self, actions: list[_Action]) -> list[tuple[str | None, list[_Action]]]:
        """Group consecutive actions of one business flow; a credential login sequence is a 'login' flow."""
        groups: list[tuple[str | None, list[_Action]]] = []
        for act in actions:
            name = act.flow.strip() if act.flow and act.flow.strip() else None
            if name is None and any(a.startswith("credentials.") for a in act.args):
                name = "login"
            if name and groups and groups[-1][0] == name:
                groups[-1][1].append(act)
            elif name is None and groups and groups[-1][0] == "login" and act.method.startswith("click") and \
                    groups[-1][1][-1].page is act.page and any(a.startswith("credentials.") for x in groups[-1][1] for a in x.args):
                groups[-1][1].append(act)  # the submit click after typing the credentials
            else:
                groups.append((name, [act]))
        # A flow of a single action adds nothing over the Page Object method itself.
        return [(n if n and len(acts) > 1 else None, acts) for n, acts in groups]

    def _assemble(self, ctx: CaseContext, mapped: MappedTest, actions: list[_Action]) -> BuildResult:
        tc = ctx.test_case_id
        changes: dict[str, str] = {}
        components: list[dict[str, Any]] = []
        impact: list[dict[str, Any]] = []
        flows_used: dict[str, str] = {}  # function -> module
        test_imports: dict[str, set[str]] = {"utilities.reporting": {"step"}}
        instances: dict[str, str] = {}
        steps: list[TestStepPlan] = []
        flow_logic: list[str] = []

        def var_of(page: _Page) -> str:
            return codegen.snake(page.class_name[:-4] if page.class_name.endswith("Page") else page.class_name, "app") + "_page"

        for name, group in self._flows(actions):
            tc_steps = sorted({a.manual_step for a in group if a.manual_step})
            if name is None:
                for act in group:
                    var = var_of(act.page)
                    instances.setdefault(var, act.page.class_name)
                    test_imports.setdefault(_module(act.page.module), set()).add(act.page.class_name)
                    steps.append(TestStepPlan(act.title, [f"{var}.{act.method}({', '.join(act.args)})"], act.checkpoint,
                                              [act.manual_step] if act.manual_step else []))
                    flow_logic.append(act.title)
                continue
            func, call_args, created = self._flow(name, group, var_of, changes, components, impact)
            flows_used[func] = f"flows/{codegen.snake(name, 'flow')}_flow.py"
            test_imports.setdefault(_module(flows_used[func]), set()).add(func)
            title = codegen.comment(name).strip()
            title = title[0].upper() + title[1:] if title else func
            steps.append(TestStepPlan(title, [f"{func}({', '.join(['page', *call_args])})"],
                                      any(a.checkpoint for a in group), tc_steps))
            flow_logic.append(title + "".join(f"\n    {a.title}" for a in group))

        # Page Objects: new files or additive extensions.
        for page in self.pages.values():
            if page.existing is None:
                changes[page.module] = codegen.render_page_object(
                    page.class_name, page.url, page.title or f"{page.class_name[:-4]} page", page.new_locators,
                    page.new_methods)
                components.append({"type": "page_object", "name": page.class_name, "module": page.module,
                                   "change": "created", "added": [l.name for l in page.new_locators] +
                                   [m.name + "()" for m in page.new_methods]})
            elif page.new_locators or page.new_methods:
                source = changes.get(page.module) or self.read(page.module)
                changes[page.module] = codegen.add_to_page_object(source, page.existing, page.new_locators, page.new_methods)
                added = [l.name for l in page.new_locators] + [m.name + "()" for m in page.new_methods]
                components.append({"type": "page_object", "name": page.class_name, "module": page.module,
                                   "change": "extended", "added": added})
                impact.append({"component": page.class_name, "module": page.module, "change": "extended",
                               "added": added, "affected_tests": self.registry.tests_using(page.class_name),
                               "risk": "Additive: existing locators and methods are unchanged."})
            else:
                components.append({"type": "page_object", "name": page.class_name, "module": page.module,
                                   "change": "reused", "added": []})

        # Test data layer.
        variant = f"_v{ctx.variant}" if ctx.variant > 1 else ""
        data_name = f"tc{tc}{variant}"
        data_rel = f"test_data/{data_name}.json"
        role = (self.credential_roles[0] if self.credential_roles else None) or ctx.credential_role
        if self.uses_credentials and not role:
            role = "user"
        combos = []
        for n, ds in enumerate([d for d in ctx.datasets if isinstance(d.get("values"), dict)], start=1):
            values = {**self.data_values, **ds["values"]}
            combos.append({"name": str(ds.get("name") or f"Combination {n}"), "values": values})
        if not combos:
            combos = [{"name": "Combination 1", "values": dict(self.data_values)}]
        data = {
            "test_case_id": tc, "user_story_id": ctx.user_story_id, "base_url": ctx.base_url,
            "credentials": {"application": ctx.application, "environment": ctx.environment, "role": role}
            if self.uses_credentials else None,
            "fields": {k: {"label": v} for k, v in self.data_labels.items()},
            "combinations": combos,
        }
        changes[data_rel] = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

        # The test.
        stem = codegen.snake(re.sub(rf"^\s*(tc\s*#?\s*)?{re.escape(tc)}\b", "", ctx.test_case_title, flags=re.I),
                             "test_case", 6)
        test_rel = f"tests/test_tc{tc}_{stem}{variant}.py"
        covered = [a for a in mapped.covered_acceptance_criteria
                   if any(a == c["id"] for c in ctx.acceptance_criteria)] or [c["id"] for c in ctx.acceptance_criteria]
        ac_titles = {c["id"]: c["title"] for c in ctx.acceptance_criteria}
        header = [f"User Story: US{ctx.user_story_id}" + (f" - {ctx.user_story_title}" if ctx.user_story_title else "")
                  if ctx.user_story_id else "User Story: (not linked)"]
        header += [f"Acceptance Criteria: {a} - {ac_titles.get(a, '')}".rstrip(" -") for a in covered] or \
            ["Acceptance Criteria: (none recorded)"]
        header += [f"Test Case: TC{tc} - {ctx.test_case_title}", f"Sprint: {ctx.sprint or '(none)'}"]
        if ctx.project:
            header.append(f"Project: {ctx.project}")
        header += ["Generated by: Automation Engineering Agent", f"Generated: {ctx.generated_at}",
                   "Automation method: AI-Driven Automation (Python + Playwright + Page Object Model)"]
        markers = [f"pytest.mark.test_case({codegen.py_str(tc)})"]
        if ctx.user_story_id:
            markers.append(f"pytest.mark.user_story({codegen.py_str(ctx.user_story_id)})")
        if covered:
            markers.append(f"pytest.mark.acceptance_criteria({', '.join(codegen.py_str(a) for a in covered)})")
        markers.append(f"pytest.mark.test_data({codegen.py_str(data_name)})")
        body = "\n".join(line for s in steps for line in s.lines)
        params = ["page"] + (["data"] if "data[" in body else []) + (["credentials"] if "credentials." in body else [])
        used_instances = [(v, c) for v, c in instances.items() if f"{v}." in body]
        plan = TestPlan(function=f"test_tc{tc}_{stem}{variant}", title=f"TC{tc} - {ctx.test_case_title}",
                        header=header, markers=markers, imports=test_imports, instances=used_instances, params=params,
                        steps=steps, doc=f"{ctx.test_case_title} (automates Test Case #{tc}).")
        changes[test_rel] = codegen.render_test(plan)
        return BuildResult(changes, test_rel, data_rel, data, components, impact, self.notes + list(mapped.notes),
                           flow_logic, role, covered)

    def _flow(self, name: str, group: list[_Action], var_of: Any, changes: dict[str, str],
              components: list[dict[str, Any]], impact: list[dict[str, Any]]) -> tuple[str, list[str], bool]:
        func = codegen.snake(name, "flow")
        module = f"flows/{func}_flow.py"
        params: list[str] = []
        call_args: list[str] = []
        calls: list[FlowCall] = []
        for act in group:
            mapped_args = []
            for arg in act.args:
                if arg.startswith("credentials.") or arg.startswith("data["):
                    pname = arg.split(".", 1)[1] if arg.startswith("credentials.") else \
                        codegen.snake(json.loads(arg[5:-1]), "value", 4)
                    if pname not in params:
                        params.append(pname)
                        call_args.append(arg)
                    mapped_args.append(pname)
                else:
                    mapped_args.append(arg)
            calls.append(FlowCall(var_of(act.page), act.page.class_name, act.method, mapped_args))
        sequence = [(c.class_name, c.method) for c in calls]
        candidates = [f for f in self.registry.flows.values() if f.name == func or re.fullmatch(rf"{func}_\d+", f.name)]
        for existing in candidates:
            if existing.calls == sequence and len(existing.params) == len(params) + 1:
                components.append({"type": "flow", "name": existing.name, "module": existing.module, "change": "reused",
                                   "added": []})
                return existing.name, call_args, False
        final, n = func, 2
        taken = {f.name for f in self.registry.flows.values()}
        while final in taken:
            final, n = f"{func}_{n}", n + 1
        doc = codegen.comment(name)
        doc = (doc[0].upper() + doc[1:]) if doc else final
        plan = FlowPlan(final, module, params, doc + ": " + ", ".join(a.title.rstrip(".") for a in group), calls)
        classes = {c.class_name: self.pages[c.class_name].module for c in calls}
        if module in changes or (final != func and self._exists(module)):
            source = changes.get(module) or self.read(module)
            changes[module] = codegen.add_to_flow_module(source, classes, [plan])
            impact.append({"component": final, "module": module, "change": "extended", "added": [final + "()"],
                           "affected_tests": self.registry.tests_using(func),
                           "risk": "Additive: a new flow variant; existing flows are unchanged."})
            components.append({"type": "flow", "name": final, "module": module, "change": "extended",
                               "added": [final + "()"]})
        else:
            changes[module] = codegen.render_flow_module(doc, classes, [plan])
            components.append({"type": "flow", "name": final, "module": module, "change": "created",
                               "added": [final + "()"]})
        self.registry.flows[final] = _flow_info(final, module, ["page", *params], plan.doc, sequence)
        return final, call_args, True

    def _exists(self, rel: str) -> bool:
        try:
            self.read(rel)
            return True
        except QAError:
            return False


def _flow_info(name: str, module: str, params: list[str], doc: str, calls: list[tuple[str, str]]) -> Any:
    from .registry import FlowInfo

    return FlowInfo(name, module, params, doc, calls, {c for c, _ in calls})


def _module(path: str) -> str:
    return path[:-3].replace("/", ".")
