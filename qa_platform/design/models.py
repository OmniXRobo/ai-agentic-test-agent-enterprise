"""Structured outputs for the test design agents (validated AI responses)."""

from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, field_validator


def _choice(value: Any, allowed: tuple[str, ...], default: str) -> Any:
    """Small local models often answer 'Positive' or 'happy path'; map such values onto the allowed ones."""
    if not isinstance(value, str):
        return value
    v = value.strip().lower().replace("-", "_").replace(" ", "_")
    if v in allowed:
        return v
    return next((a for a in allowed if a in v or v in a), default) if v else default


class Requirement(BaseModel):
    id: str = Field(description="Short id such as R1")
    text: str
    acceptance_criterion: str | None = Field(default=None, description="The acceptance criterion it comes from")
    kind: Literal["functional", "non_functional", "performance", "security", "usability"] = "functional"
    risk: Literal["high", "medium", "low"] = "medium"


class RequirementsAnalysis(BaseModel):
    summary: str
    requirements: list[Requirement]
    ambiguities: list[str] = []


class TestPlan(BaseModel):
    scope: str
    out_of_scope: list[str] = []
    approach: str
    test_types: list[str]
    risks: list[str] = []
    entry_criteria: list[str] = []
    exit_criteria: list[str] = []
    performance_slas: list[str] = Field(default=[], description="Performance requirements stated or implied by the story")
    suite_name: str = Field(description="Suggested test suite name")


class TestStep(BaseModel):
    action: str
    expected: str


class TestCase(BaseModel):
    ref: str = Field(description="Short id such as TC1")
    title: str
    priority: int = Field(ge=1, le=4, default=2)
    category: Literal["functional", "positive", "negative", "boundary", "edge", "end_to_end", "smoke",
                      "regression"] = Field(default="functional", description="Type of testing; decides the TFS suite")
    requirement_ids: list[str] = []
    preconditions: str | None = None
    steps: list[TestStep] = Field(min_length=1)
    automation_candidate: bool = True


class TestCaseSet(BaseModel):
    test_cases: list[TestCase] = Field(min_length=1)


class DataSet(BaseModel):
    test_case_ref: str
    name: str
    kind: Literal["valid", "invalid", "boundary", "edge"] = "valid"
    values: dict[str, str | int | float | bool | None]


class TestDataSet(BaseModel):
    datasets: list[DataSet]


# ---- suite-aware, acceptance-criterion traceable test cases --------------------------------------------


class Condition(BaseModel):
    id: str = Field(description="<criterion id>.<n>, e.g. AC-01.1")
    text: str


class CriterionAnalysis(BaseModel):
    ac_id: str = Field(description="The criterion id exactly as given")
    conditions: list[Condition] = []
    actors: list[str] = []
    clarification: str | None = Field(default=None, description="Question to ask when the criterion is too unclear "
                                                                "to write a valid test")
    applicable: bool = Field(default=True, description="False when this testing type cannot test the criterion")
    not_applicable_reason: str | None = None


class SuiteAnalysis(BaseModel):
    criteria: list[CriterionAnalysis] = []
    business_rules: list[str] = Field(default=[], description="Rules stated in the story; never invented ones")
    gaps: list[str] = Field(default=[], description="Missing or ambiguous information")


class NotApplicable(BaseModel):
    ac_id: str
    reason: str


class Clarification(BaseModel):
    ac_id: str | None = None
    question: str


class SuiteTestCase(BaseModel):
    ref: str = Field(description="Short id such as TC1")
    title: str
    ac_ids: list[str] = Field(default=[], description="Criterion ids this case verifies")
    condition_ids: list[str] = Field(default=[], description="Condition ids this case verifies")
    objective: str = ""
    preconditions: str | None = None
    dependencies: list[str] = []
    test_data: list[str] = Field(default=[], description="Input values used, e.g. \"username = standard_user\"")
    steps: list[TestStep] = Field(min_length=1)
    priority: int = Field(ge=1, le=4, default=2)
    risk: Literal["high", "medium", "low"] = "medium"
    risk_rationale: str = ""
    scenario: Literal["positive", "negative", "boundary", "edge", "error", "security", "performance", "usability",
                      "integration", "other"] = "positive"
    automation: Literal["automatable", "partial", "manual"] = "automatable"
    automation_reason: str | None = None
    gaps: list[str] = Field(default=[], description="Assumptions or missing requirements a reviewer must check")

    @field_validator("scenario", "risk", "automation", mode="before")
    @classmethod
    def _lenient(cls, value: Any, info: Any) -> Any:
        field = cls.model_fields[info.field_name]
        return _choice(value, get_args(field.annotation), str(field.default))

    @field_validator("priority", mode="before")
    @classmethod
    def _priority(cls, value: Any) -> Any:
        if isinstance(value, str):
            digits = "".join(ch for ch in value if ch.isdigit())
            return min(4, max(1, int(digits))) if digits else 2
        return min(4, max(1, value)) if isinstance(value, int) else value


class SuiteCaseSet(BaseModel):
    test_cases: list[SuiteTestCase] = []
    not_applicable: list[NotApplicable] = []
    clarifications: list[Clarification] = []


# ---- suite-aware test data ------------------------------------------------------------------------------


class DataField(BaseModel):
    name: str
    type: Literal["string", "integer", "decimal", "boolean", "date", "datetime", "time", "email", "phone", "secret",
                  "enum", "other"] = "string"
    required: bool | None = None
    rule: str | None = Field(default=None, description="Format, length, range or allowed values")
    source: Literal["specified", "assumed"] = Field(default="assumed",
                                                    description="specified = stated in the story/test case")


class DataRow(BaseModel):
    name: str
    scenario: Literal["valid", "invalid", "boundary", "outside_boundary", "missing", "empty", "null", "whitespace",
                      "duplicate", "date_time", "role", "relationship", "unicode", "special_characters", "pairwise",
                      "volume", "security", "other"] = "valid"
    intended_valid: bool = True
    violated_rule: str | None = Field(default=None, description="For an invalid row: the rule it breaks on purpose")
    expected_outcome: str = Field(description="Observable result the test case expects with this row")
    ac_ids: list[str] = []
    values: dict[str, str | int | float | bool | None]

    @field_validator("scenario", mode="before")
    @classmethod
    def _lenient(cls, value: Any) -> Any:
        return _choice(value, get_args(cls.model_fields["scenario"].annotation), "other")


class TestDataDesign(BaseModel):
    fields: list[DataField] = []
    rows: list[DataRow] = []
    not_applicable: list[str] = Field(default=[], description="Data scenarios that do not apply, with the reason")
    gaps: list[str] = Field(default=[], description="Missing or conflicting rules; never invented")
    preconditions: list[str] = []
    cleanup: list[str] = []
