"""The Enterprise QA agents.

``id`` is the stable internal identifier (persisted in events); ``name`` is the
professional display name. ``source`` names the runtime component that does the
agent's work. ``stage`` is the build stage that delivers the agent's
implementation, so the UI can say honestly when an agent is not available yet.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AgentDef:
    id: str
    name: str
    role: str
    source: str
    robot: str  # robot graphic key in web/js/robots.js
    stage: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


AGENTS: tuple[AgentDef, ...] = (
    AgentDef("requirements", "Requirements Intelligence Agent",
             "Reads user stories and acceptance criteria and extracts testable requirements.",
             "tfs.work_items + ai.gateway", "requirements", 3),
    AgentDef("test_planning", "Test Planning & Strategy Agent",
             "Builds the test strategy, scope and risk-based plan.", "ai.gateway", "planning", 3),
    AgentDef("test_suite_design", "Test Suite Design Agent",
             "Organizes test cases into suites aligned with test plans.", "tfs.test_management", "suite", 3),
    AgentDef("test_case_design", "Test Case Design Agent",
             "Generates test cases and steps from acceptance criteria.", "ai.gateway", "testcase", 3),
    AgentDef("test_data", "Test Data Intelligence Agent",
             "Builds positive, negative and boundary datasets.", "ai.gateway", "testdata", 3),
    AgentDef("app_discovery", "Application Discovery Agent",
             "Crawls the application to discover pages, elements and APIs.", "playwright.discovery", "discovery", 4),
    AgentDef("automation_engineering", "Automation Engineering Agent",
             "Creates Playwright automation scripts from test cases.", "playwright.codegen", "automation", 4),
    AgentDef("guided_browser", "Guided Browser Automation Agent",
             "Records guided browser sessions into automation.", "playwright.recorder", "recorder", 4),
    AgentDef("test_execution", "Test Execution Agent",
             "Runs automation and captures evidence.", "playwright.runner", "execution", 4),
    AgentDef("self_healing", "Self-Healing Locator Agent",
             "Recovers failed locators with scored alternatives and evidence.", "playwright.healing", "healing", 4),
    AgentDef("performance_engineering", "Performance Engineering Agent",
             "Plans, runs and evaluates performance tests against SLAs.", "performance.engine", "performance", 5),
    AgentDef("jmeter_load", "JMeter Load Testing Agent",
             "Generates JMX plans and runs JMeter in non-GUI mode.", "jmeter.cli", "jmeter", 5),
    AgentDef("failure_analysis", "Failure & Quality Analysis Agent",
             "Analyzes failures and separates observed facts from likely causes.", "ai.gateway", "analysis", 6),
    AgentDef("defect_intelligence", "Defect Intelligence Agent",
             "Detects duplicates and creates traceable defects in TFS.", "tfs.work_items", "defect", 6),
    AgentDef("orchestration", "QA Orchestration Agent",
             "Coordinates agents across the QA workflow and runs system checks.", "orchestrator", "orchestrator", 1),
    AgentDef("manual_playback", "Manual Playback Agent",
             "Replays recorded manual scripts with visual playback.", "playwright.runner", "playback", 4),
    AgentDef("reporting", "Reporting & Analytics Agent",
             "Produces HTML/PDF reports and analytics.", "reports.engine", "reporting", 8),
    AgentDef("voice_command", "Voice Command Agent",
             "Understands spoken QA commands and answers by voice.", "voice.engine", "voice", 7),
)

AGENTS_BY_ID: dict[str, AgentDef] = {a.id: a for a in AGENTS}


def get_agent(agent_id: str) -> AgentDef:
    try:
        return AGENTS_BY_ID[agent_id]
    except KeyError as exc:
        raise KeyError(f"Unknown agent: {agent_id}") from exc
