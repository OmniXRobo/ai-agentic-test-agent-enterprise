from __future__ import annotations

from qa_platform.logging_setup import redact, redact_text
from qa_platform.terminology import professionalize


def test_redact_text_patterns():
    assert "abc" not in redact_text("Authorization: Basic YWJjOmRlZmdoaWprbG1ub3A=")
    assert "s3cr3t" not in redact_text('password="s3cr3t"')
    assert "tok123" not in redact_text("token=tok123")


def test_redact_nested():
    out = redact({"profile": {"PAT": "zzz", "url": "https://dev.azure.com/x"}, "items": ["token: abc"]})
    assert out["profile"]["PAT"] == "***REDACTED***"
    assert out["profile"]["url"] == "https://dev.azure.com/x"
    assert "abc" not in out["items"][0]


def test_professional_terms():
    assert professionalize("Local Agent Runtime uses Local LLM") == "Enterprise QA Runtime uses On-Premise LLM"
    assert professionalize("Option 2 in Phase 1") == "Guided Browser Automation in Test Design"
