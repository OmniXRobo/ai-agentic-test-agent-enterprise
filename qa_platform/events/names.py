"""Centralized QA event names. Use these constants, never string literals."""

from __future__ import annotations

# Agent lifecycle (emitted by BaseAgent.activity)
AGENT_STARTED = "agent.started"
AGENT_PROGRESS = "agent.progress"
AGENT_COMPLETED = "agent.completed"
AGENT_FAILED = "agent.failed"
AGENT_LOG = "agent.log"
# A running agent waits for the tester (for example an OTP during discovery), then continues.
AGENT_PAUSED = "agent.paused"
AGENT_RESUMED = "agent.resumed"

REQUIREMENTS_STARTED = "requirements.started"
REQUIREMENTS_COMPLETED = "requirements.completed"
TESTPLAN_STARTED = "testplan.started"
TESTPLAN_COMPLETED = "testplan.completed"
TESTCASE_STARTED = "testcase.started"
TESTCASE_COMPLETED = "testcase.completed"
TESTDATA_STARTED = "testdata.started"
TESTDATA_COMPLETED = "testdata.completed"

AUTOMATION_GENERATION_STARTED = "automation.generation.started"
AUTOMATION_GENERATION_COMPLETED = "automation.generation.completed"
RECORDING_STARTED = "recording.started"
RECORDING_COMPLETED = "recording.completed"

EXECUTION_STARTED = "execution.started"
EXECUTION_STEP = "execution.step"
EXECUTION_COMPLETED = "execution.completed"
EXECUTION_FAILED = "execution.failed"

SELFHEALING_STARTED = "selfhealing.started"
SELFHEALING_COMPLETED = "selfhealing.completed"
SELFHEALING_FAILED = "selfhealing.failed"

PERFORMANCE_GENERATION_STARTED = "performance.generation.started"
PERFORMANCE_EXECUTION_STARTED = "performance.execution.started"
PERFORMANCE_SAMPLE_SUMMARY = "performance.sample_summary"
PERFORMANCE_COMPLETED = "performance.completed"
PERFORMANCE_FAILED = "performance.failed"

DEFECT_ANALYSIS_STARTED = "defect.analysis.started"
DEFECT_DUPLICATE_FOUND = "defect.duplicate_found"
DEFECT_CREATED = "defect.created"
DEFECT_FAILED = "defect.failed"

VOICE_COMMAND_RECEIVED = "voice.command.received"
VOICE_COMMAND_ACCEPTED = "voice.command.accepted"
VOICE_COMMAND_COMPLETED = "voice.command.completed"
VOICE_COMMAND_FAILED = "voice.command.failed"
# The command moved from the Voice Command Agent to the agent that does the work (and back).
VOICE_HANDOFF = "voice.handoff"

REPORT_STARTED = "report.started"
REPORT_COMPLETED = "report.completed"
REPORT_FAILED = "report.failed"

SYSTEM_DIAGNOSTICS_STARTED = "system.diagnostics.started"
SYSTEM_DIAGNOSTICS_COMPLETED = "system.diagnostics.completed"
AI_UNAVAILABLE = "ai.unavailable"
TFS_UNAVAILABLE = "tfs.unavailable"

ALL_EVENTS: frozenset[str] = frozenset(
    v for k, v in dict(globals()).items() if k.isupper() and isinstance(v, str)
)
