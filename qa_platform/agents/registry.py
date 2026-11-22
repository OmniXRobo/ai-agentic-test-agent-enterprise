"""The Enterprise QA agents.

``id`` is the stable internal identifier (persisted in events); ``name`` is the
professional display name. ``source`` names the runtime component that does the
agent's work. ``stage`` is the build stage that delivers the agent's
implementation, so the UI can say honestly when an agent is not available yet.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace


@dataclass(frozen=True)
class AgentDef:
    id: str
    name: str
    role: str
    source: str
    robot: str  # robot graphic key in web/js/robots.js
    stage: int
    # 23.14.0: shown in the agent's details on Agent Workflow
    category: str = ""
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    depends: tuple[str, ...] = ()  # other agent ids or engines it needs

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        for k in ("inputs", "outputs", "depends"):
            d[k] = list(d[k])
        return d


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
    # 23.12.0: capabilities that existed without a card, and the new API Testing agents. Added at the end so the
    # existing cards keep their places; the page itself is unchanged.
    AgentDef("ai_operations", "AI Application Learning & Operations Agent",
             "Connects to websites, learns workflows and executes authorized instructions.", "opsagent", "aiops", 9),
    AgentDef("integration_comparison", "Integration Comparison Agent",
             "Captures, extracts, normalizes and compares data between applications.", "comparison.workspace",
             "comparison", 9),
    AgentDef("api_testing", "API Testing Agent",
             "Configures, generates, executes and validates API tests.", "apitesting.runner", "api", 9),
    AgentDef("api_failure_analysis", "API Failure Analysis Agent",
             "Separates API defects from configuration, environment, authentication and test problems.",
             "apitesting.analysis + ai.gateway", "apianalysis", 9),
    AgentDef("api_contract", "API Contract Validation Agent",
             "Validates responses against OpenAPI, Swagger and JSON Schema contracts.", "apitesting.schema",
             "contract", 9),
    AgentDef("scheduled_execution", "Scheduled Execution Agent",
             "Runs saved UI, API and Integration Comparison tests on schedule.", "scheduler", "scheduler", 9),
    # 23.14.0: AURA existed without a card
    AgentDef("aura_memory", "AURA Learning Memory Agent",
             "Remembers what worked (locators, workflows, fixes) and offers it to the other agents.", "aura",
             "aura", 9, "Shared", ("execution results", "accepted fixes"), ("learned knowledge",), ("orchestration",)),
    # 23.14.0: AI Security & Vulnerability Testing
    AgentDef("security_discovery", "Security Discovery Agent",
             "Maps the approved application read-only: pages, forms, APIs, roles and state-changing links.",
             "sectest.crawler", "secdiscovery", 10, "Security", ("approved scope", "test accounts"),
             ("application map", "passive findings"), ()),
    AgentDef("vulnerability_assessment", "Vulnerability Assessment Agent",
             "Runs the OWASP checks and the offline OWASP ZAP scan inside the approved scope and rate limits.",
             "sectest.active + sectest.zap", "vulnscan", 10, "Security", ("approved application map", "categories"),
             ("findings with evidence",), ("security_discovery",)),
    AgentDef("authn_authz_testing", "Authentication & Authorization Testing Agent",
             "Tests sign-in, sessions, role access, object ownership and business-logic scenarios.",
             "sectest.active + sectest.scenarios", "authz", 10, "Security", ("role accounts", "approved scenarios"),
             ("access-control findings",), ("security_discovery",)),
    AgentDef("controlled_fuzzing", "Controlled Fuzzing Agent",
             "Sends bounded boundary, type, encoding and malformed-input cases within the request budget.",
             "sectest.fuzz", "fuzz", 10, "Security", ("parameters", "API operations"), ("input-validation findings",),
             ("security_discovery",)),
    AgentDef("finding_validation", "Finding Validation Agent",
             "Records reviews, retests findings and tracks security regressions.", "sectest.service", "validate", 10,
             "Security", ("findings", "reviews"), ("fixed / still open status", "regression results"),
             ("vulnerability_assessment",)),
    # 23.14.0: Red Hat Fuse Integration Testing
    AgentDef("fuse_route_discovery", "Fuse Route Discovery Agent",
             "Finds Camel contexts and routes on Karaf, Spring Boot or EAP read-only (or black-box).",
             "fusetest.adapters", "fusediscovery", 10, "Fuse", ("Fuse connection",), ("routes", "route health"), ()),
    AgentDef("fuse_route_mapping", "Fuse Route Mapping Agent",
             "Turns route definitions into entry points, destinations, routing rules, transformations and error "
             "handling.", "fusetest.routes", "fusemap", 10, "Fuse", ("route definitions",), ("route maps",),
             ("fuse_route_discovery",)),
    AgentDef("fuse_test_execution", "Fuse Test Execution Agent",
             "Sends approved test messages through test entry points and traces them end to end by correlation ID.",
             "fusetest.service", "fuseexec", 10, "Fuse", ("approved test cases",), ("results with traces",),
             ("fuse_route_mapping", "message_publish_verification")),
    AgentDef("fuse_transformation_comparison", "Transformation Comparison Agent",
             "Compares expected and actual messages field by field (JSON / XML, mappings, dates, precision).",
             "fusetest.compare", "fusecompare", 10, "Fuse", ("expected message", "actual message"),
             ("field differences",), ()),
    # 23.14.0: ActiveMQ testing
    AgentDef("broker_discovery", "Broker Discovery Agent",
             "Reads ActiveMQ Classic / Artemis queues, topics, consumers and health without consuming anything.",
             "amqtest.jolokia", "broker", 10, "ActiveMQ", ("broker connection",), ("destinations", "health alerts"),
             ()),
    AgentDef("message_publish_verification", "Message Publish & Verify Agent",
             "Publishes test messages to authorized test destinations and verifies them by browsing.",
             "amqtest.service + amqtest.stomp", "publish", 10, "ActiveMQ", ("approved message tests",),
             ("delivery results",), ("broker_discovery",)),
    AgentDef("consumer_delivery_validation", "Consumer Delivery Validation Agent",
             "Checks that the application's consumer takes the message (counters, browse, dead-letter).",
             "amqtest.service", "consumer", 10, "ActiveMQ", ("test message",), ("consumption result",),
             ("message_publish_verification",)),
    AgentDef("retry_dlq_testing", "Retry & Dead-Letter Testing Agent",
             "Verifies redelivery and that failing messages reach the dead-letter queue.", "amqtest.service", "dlq", 10,
             "ActiveMQ", ("poison test message",), ("retry / DLQ result",), ("message_publish_verification",)),
    # 23.14.0: shared result actions
    AgentDef("ai_bug_analysis", "AI Bug Analysis Agent",
             "Analyses a failure from its evidence with the local AI, keeping verified facts apart from hypotheses.",
             "itest.actions + ai.gateway", "bugai", 10, "Shared", ("sanitized evidence",), ("structured analysis",),
             ("evidence_collection",)),
    AgentDef("evidence_collection", "Evidence Collection Agent",
             "Collects and sanitizes the evidence of a result (no passwords, tokens or cookies).", "itest.actions",
             "evidence", 10, "Shared", ("result", "finding", "run"), ("sanitized evidence",), ()),
    AgentDef("tfs_defect_preparation", "TFS Defect Preparation Agent",
             "Prepares an editable defect draft for Defect Review; nothing reaches TFS without a person.",
             "itest.actions + defects", "tfsprep", 10, "Shared", ("evidence", "AI analysis"), ("defect draft",),
             ("defect_intelligence",)),
    AgentDef("pdf_reporting", "PDF Report Agent",
             "Generates offline PDF reports (execution, AI, security, Fuse, ActiveMQ, E2E, suite, evidence).",
             "itest.actions + reports", "pdf", 10, "Shared", ("evidence",), ("PDF report",), ()),
    AgentDef("email_developer", "Email Developer Agent",
             "Drafts the developer email with selected attachments; sends only when a person confirms.",
             "itest.actions + mailer", "email", 10, "Shared", ("draft", "attachments"), ("accepted / failed status",),
             ("pdf_reporting",)),
)

# 23.14.0: category, inputs, outputs and dependencies of the agents that existed before
_META = {
    "requirements": ("Planning", ("user stories",), ("testable requirements",), ()),
    "test_planning": ("Planning", ("requirements",), ("test plan",), ("requirements",)),
    "test_suite_design": ("Planning", ("test cases",), ("suites",), ("test_case_design",)),
    "test_case_design": ("Planning", ("acceptance criteria",), ("test cases",), ("requirements",)),
    "test_data": ("Planning", ("test cases",), ("datasets",), ()),
    "app_discovery": ("Automation", ("application URL",), ("pages", "elements"), ()),
    "automation_engineering": ("Automation", ("test cases",), ("scripts",), ("app_discovery",)),
    "guided_browser": ("Automation", ("browser session",), ("scripts",), ()),
    "test_execution": ("Automation", ("scripts",), ("results", "evidence"), ()),
    "self_healing": ("Automation", ("failed locators",), ("healed locators",), ("test_execution",)),
    "performance_engineering": ("Performance", ("plans",), ("SLA results",), ()),
    "jmeter_load": ("Performance", ("plans",), ("JMX", "results"), ("performance_engineering",)),
    "failure_analysis": ("Analysis", ("failures",), ("analysis",), ()),
    "defect_intelligence": ("Defects", ("analysis",), ("TFS defects",), ()),
    "orchestration": ("Shared", ("requests",), ("coordinated runs",), ()),
    "manual_playback": ("Automation", ("recordings",), ("playback",), ()),
    "reporting": ("Shared", ("results",), ("reports",), ()),
    "voice_command": ("Shared", ("speech",), ("commands", "spoken replies"), ()),
    "ai_operations": ("Automation", ("instructions",), ("executed workflows",), ()),
    "integration_comparison": ("Integration", ("two applications",), ("differences",), ()),
    "api_testing": ("API", ("API definitions",), ("results",), ()),
    "api_failure_analysis": ("API", ("API failures",), ("classification",), ("api_testing",)),
    "api_contract": ("API", ("contracts",), ("validation results",), ("api_testing",)),
    "scheduled_execution": ("Shared", ("schedules",), ("runs",), ()),
}
AGENTS = tuple(replace(a, category=_META[a.id][0], inputs=_META[a.id][1], outputs=_META[a.id][2],
                       depends=_META[a.id][3]) if a.id in _META and not a.category else a for a in AGENTS)

AGENTS_BY_ID: dict[str, AgentDef] = {a.id: a for a in AGENTS}


def get_agent(agent_id: str) -> AgentDef:
    try:
        return AGENTS_BY_ID[agent_id]
    except KeyError as exc:
        raise KeyError(f"Unknown agent: {agent_id}") from exc
