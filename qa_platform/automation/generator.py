"""AI-Driven Automation: the Automation Engineering Agent turns a TFS test case into a script.

Selectors are chosen only from elements actually discovered on the application,
and every generated selector is checked against that inventory.
"""

from __future__ import annotations

import html
import re
from typing import Any

from pydantic import BaseModel, Field

from ..agents.base import BaseAgent
from ..ai.gateway import AIGateway
from ..errors import QAError
from ..events import names
from ..tfs.service import TfsService
from .dsl import Script, Step, Target, new_script_id


class AutomationEngineeringAgent(BaseAgent):
    agent_id = "automation_engineering"


class GeneratedStep(BaseModel):
    action: str = Field(description="goto, click, fill, select, check, uncheck, press, expect_visible, expect_text, expect_url, wait")
    selector: str | None = Field(default=None, description="Exactly one selector copied from the element inventory")
    value: str | None = None
    description: str


class GeneratedScript(BaseModel):
    steps: list[GeneratedStep] = Field(min_length=1)
    notes: list[str] = []


_STEP = re.compile(r"<step[^>]*>(.*?)</step>", re.S | re.I)
_PARAM = re.compile(r"<parameterizedString[^>]*>(.*?)</parameterizedString>", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")


def parse_tfs_steps(xml: str | None) -> list[dict[str, str]]:
    """Microsoft.VSTS.TCM.Steps -> [{action, expected}] as plain text."""
    out = []
    for block in _STEP.findall(xml or ""):
        parts = [html.unescape(_TAGS.sub(" ", html.unescape(p))).strip() for p in _PARAM.findall(block)]
        parts = [re.sub(r"\s+", " ", p) for p in parts]
        if parts and parts[0]:
            out.append({"action": parts[0], "expected": parts[1] if len(parts) > 1 else ""})
    return out


def inventory(discovery: dict[str, Any], limit: int = 250) -> tuple[str, dict[str, dict[str, Any]]]:
    """Compact element list for the prompt and a selector -> fingerprint map for validation."""
    lines, index = [], {}
    for page in discovery.get("pages", []):
        if page.get("error"):
            continue
        lines.append(f"PAGE {page['url']} ({page.get('title') or ''})")
        for el in page.get("elements", []):
            if len(index) >= limit:
                break
            desc = el.get("name") or el.get("text") or el.get("label") or el.get("placeholder") or ""
            lines.append(f"  {el['selector']} | {el.get('role') or el.get('tag')} | {desc[:60]}")
            index[el["selector"]] = el
            for alt in el.get("alternates", []):
                index.setdefault(alt, el)
    return "\n".join(lines), index


class AutomationGenerator:
    def __init__(self, agent: AutomationEngineeringAgent, ai: AIGateway, tfs: TfsService) -> None:
        self.agent = agent
        self.ai = ai
        self.tfs = tfs

    def generate(self, run_id: str, *, test_case_id: int, base_url: str, discovery: dict[str, Any],
                 credential_role: str | None = None, application: str | None = None) -> Script:
        tc = str(test_case_id)
        with self.agent.activity(f"Creating Playwright automation for Test Case #{tc}", run_id=run_id,
                                 test_case_id=tc) as act:
            act.event(names.AUTOMATION_GENERATION_STARTED, f"Automation Engineering Agent reading Test Case #{tc}")
            item = self.tfs.detail(test_case_id)
            steps = parse_tfs_steps(item.get("steps"))
            if not steps:
                raise QAError("TEST_CASE_NO_STEPS", f"Test Case #{tc} has no steps in TFS.", status=409)
            story = next((l for l in item.get("links", []) if l.get("relation") == "Tests"), None)
            inv_text, index = inventory(discovery)
            if not index:
                raise QAError("DISCOVERY_EMPTY", "No interactive elements were discovered on the application.", status=409)
            act.progress(40, f"Mapping {len(steps)} manual steps to {len(index)} discovered elements")
            manual = "\n".join(f"{i + 1}. {s['action']} => expected: {s['expected']}" for i, s in enumerate(steps))
            cred = (f"Use {{{{CRED:{credential_role}.username}}}} and {{{{CRED:{credential_role}.password}}}} for login fields."
                    if credential_role else "Never write real passwords; use {{CRED:role.password}} placeholders.")
            generated = self.ai.complete_json(
                "You are a Playwright automation engineer. Convert the manual test steps into automation steps. "
                "Start with a goto step to the start URL. For every click/fill/select/check/expect on an element, copy "
                "exactly one selector from the ELEMENT INVENTORY; never invent selectors. Add an expect step for each "
                "expected result you can verify. Use {{TODAY}}/{{TODAY+Nd:%d/%m/%Y}} for dates. " + cred,
                f"Start URL: {base_url}\n\nManual test steps:\n{manual}\n\nELEMENT INVENTORY (selector | role | name):\n{inv_text}",
                GeneratedScript, max_tokens=6000)
            out_steps, unverified = [], []
            for g in generated.steps:
                action = g.action if g.action in Step.model_fields["action"].annotation.__args__ else None  # type: ignore[union-attr]
                if action is None:
                    unverified.append(f"Unsupported action '{g.action}' skipped: {g.description}")
                    continue
                target = None
                if g.selector and action not in ("goto", "wait", "expect_url"):
                    fp = index.get(g.selector)
                    if fp is None:
                        unverified.append(f"Selector not in the discovered inventory: {g.selector} ({g.description})")
                        target = Target(selector=g.selector, description=g.description)
                    else:
                        target = Target.model_validate({**fp, "selector": g.selector, "description": g.description})
                out_steps.append(Step(action=action, target=target, value=g.value, description=g.description))
            script = Script(
                id=new_script_id(), name=f"TC{tc} {item['title']}"[:120], mode="ai", base_url=base_url,
                test_case_id=tc, user_story_id=str(story["id"]) if story else None, steps=out_steps,
                credential_role=credential_role, application=application,
                notes="\n".join(generated.notes + unverified) or None,
            )
            act.event(names.AUTOMATION_GENERATION_COMPLETED,
                      f"Playwright script created for Test Case #{tc}: {len(out_steps)} steps"
                      + (f", {len(unverified)} need review" if unverified else ""), script_id=script.id)
            act.complete(f"Automation created: {len(out_steps)} steps" + (f" ({len(unverified)} to review)" if unverified else ""))
        return script
