from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from spectrail.agent.planner import AgentOutcome
from spectrail.evidence.models import EvidencePolicy


class AgentEvaluationExpected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: AgentOutcome
    manifest_status: str = Field(min_length=1, max_length=64)
    final_pipeline_status: str | None = Field(default=None, max_length=64)
    steps_used: int = Field(ge=0)
    planner_calls: int = Field(ge=0)
    tool_invocations: int = Field(ge=0)
    pipeline_attempts: int = Field(ge=0)
    tool_sequence: list[str] = Field(default_factory=list)
    decision_actions: list[str] = Field(default_factory=list)
    attempt_statuses: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(default_factory=list)
    warning_codes: list[str] = Field(default_factory=list)


class AgentEvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["agent_evaluation_case_v1"] = (
        "agent_evaluation_case_v1"
    )
    name: str = Field(min_length=1, max_length=128)
    document: str = Field(min_length=1)
    planner_fixture: str = Field(min_length=1)
    pipeline_scenario: Literal[
        "production",
        "recoverable_failure_then_success",
    ] = "production"
    model_mode: Literal["mock", "recorded"] = "mock"
    recorded_fixture: str | None = None
    chunking_mode: Literal["off", "auto", "force"] = "auto"
    max_rendered_prompt_chars: int = Field(default=16000, ge=1000, le=32000)
    overlap_blocks: int = Field(default=1, ge=0, le=3)
    validation_policy: Literal["strict", "quarantine"] = "strict"
    evidence_policy: EvidencePolicy = "structured_if_available"
    expected: AgentEvaluationExpected
