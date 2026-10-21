"""Centralized visible terminology.

Internal identifiers stay stable for persistence and backward compatibility.
Everything a person reads (UI, reports, logs, agent names) goes through
``display_name``.
"""

from __future__ import annotations

DISPLAY_NAMES: dict[str, str] = {
    # Automation modes
    "option_1": "AI-Driven Automation",
    "option_2": "Guided Browser Automation",
    "option_3": "Application Discovery & Update",
    "option_4": "Hybrid Test Automation",
    # Phases
    "phase_1": "Test Design",
    "phase_2": "Automation Studio",
    # Runtime
    "local_agent": "Enterprise QA Agent",
    "local_agent_runtime": "Enterprise QA Runtime",
    # TFS modes
    "azure_devops_cloud": "Azure DevOps Online",
    "cloud_tfs": "Online TFS",
    "tfs_online": "Azure DevOps Online",
    "tfs_offline": "Local TFS / Azure DevOps Server",
    # AI
    "local_ai": "On-Premise AI",
    "local_llm": "On-Premise LLM",
    # Defect creation modes
    "defects.review": "Review Before Create",
    "defects.automatic": "Automatic",
    "defects.disabled": "Disabled",
}

# Legacy wording that must never appear in visible text, mapped to its replacement.
LEGACY_PHRASES: dict[str, str] = {
    "Option 1": "AI-Driven Automation",
    "Option 2": "Guided Browser Automation",
    "Option 3": "Application Discovery & Update",
    "Option 4": "Hybrid Test Automation",
    "Phase 1": "Test Design",
    "Phase 2": "Automation Studio",
    "Local Agent Runtime": "Enterprise QA Runtime",
    "Local Agent": "Enterprise QA Agent",
    "Azure DevOps Cloud": "Azure DevOps Online",
    "Cloud TFS": "Online TFS",
    "Local LLM": "On-Premise LLM",
    "Local AI": "On-Premise AI",
}


def display_name(key: str, default: str | None = None) -> str:
    return DISPLAY_NAMES.get(key, default if default is not None else key)


def professionalize(text: str) -> str:
    """Replace legacy phrases in free text (longest phrase first)."""
    for legacy in sorted(LEGACY_PHRASES, key=len, reverse=True):
        text = text.replace(legacy, LEGACY_PHRASES[legacy])
    return text
