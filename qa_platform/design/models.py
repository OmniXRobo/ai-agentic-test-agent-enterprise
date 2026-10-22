"""Structured outputs for the test design agents (validated AI responses)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
