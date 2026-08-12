from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


MetricValue = int | float | str | bool | None
ToolStatus = Literal["ok", "warning", "failed"]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WARNING_CODE_RE = re.compile(
    r"^[A-Z][A-Z0-9_]*(?::[a-z][a-z0-9_]*=-?\d+(?:,[a-z][a-z0-9_]*=-?\d+)*)?$"
)
_METRIC_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_ABSOLUTE_PATH_RE = re.compile(r"^(?:/|[A-Za-z]:[\\/]|file://)")


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentProfile(AgentModel):
    schema_version: Literal["document_profile_v1"] = "document_profile_v1"

    document_id: str = Field(min_length=1, max_length=128)
    document_name: str = Field(min_length=1, max_length=255)
    source_format: str = Field(min_length=1, max_length=32)
    source_sha256: str

    parser_name: str = Field(min_length=1, max_length=128)
    parser_version: str | None = Field(default=None, max_length=64)

    page_count: int | None = Field(default=None, ge=0)
    block_count: int = Field(ge=0)
    section_count: int = Field(ge=0)

    block_type_counts: dict[str, int]
    heading_count: int = Field(ge=0)
    paragraph_count: int = Field(ge=0)
    table_block_count: int = Field(ge=0)

    evidence_table_count: int = Field(ge=0)
    evidence_cell_count: int = Field(ge=0)
    expected_capability_counts: dict[str, int]
    available_capability_counts: dict[str, int]

    rendered_text_chars: int = Field(ge=0)
    estimated_prompt_chars: int = Field(ge=0)
    prompt_estimator_version: str = Field(min_length=1, max_length=128)

    warnings: list[str] = Field(default_factory=list, max_length=64)
    complexity_flags: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("source_sha256")
    @classmethod
    def validate_source_sha256(cls, value: str) -> str:
        if _SHA256_RE.fullmatch(value) is None:
            raise ValueError("source_sha256 must be a lowercase SHA-256 digest")
        return value

    @field_validator(
        "block_type_counts",
        "expected_capability_counts",
        "available_capability_counts",
    )
    @classmethod
    def validate_counts(cls, value: dict[str, int]) -> dict[str, int]:
        if any(not key or count < 0 for key, count in value.items()):
            raise ValueError("profile counts require non-empty keys and non-negative values")
        return value

    @field_validator("warnings")
    @classmethod
    def validate_warning_codes(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("profile warnings must be unique")
        if any(_WARNING_CODE_RE.fullmatch(item) is None for item in value):
            raise ValueError("profile warnings must contain stable codes only")
        return value

    @field_validator("complexity_flags")
    @classmethod
    def validate_complexity_flags(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("complexity flags must be unique")
        if any(re.fullmatch(r"[A-Z][A-Z0-9_]*", item) is None for item in value):
            raise ValueError("invalid complexity flag")
        return value


class ToolSpec(AgentModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    description: str = Field(min_length=1, max_length=512)
    side_effects: Literal["none", "task_artifacts"]
    input_schema_version: str = Field(min_length=1, max_length=128)
    input_schema: dict
    output_schema_version: str = Field(min_length=1, max_length=128)


class ToolResult(AgentModel):
    schema_version: Literal["agent_tool_result_v1"] = "agent_tool_result_v1"
    tool: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    status: ToolStatus
    summary: str = Field(max_length=512)
    metrics: dict[str, MetricValue] = Field(default_factory=dict)
    warning_codes: list[str] = Field(default_factory=list, max_length=64)
    artifacts: dict[str, str] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, max_length=128)
    retryable: bool = False

    @field_validator("warning_codes")
    @classmethod
    def validate_warning_codes(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("tool warning codes must be unique")
        if any(re.fullmatch(r"[A-Z][A-Z0-9_]*", item) is None for item in value):
            raise ValueError("invalid tool warning code")
        return value

    @field_validator("metrics")
    @classmethod
    def validate_metrics(cls, value: dict[str, MetricValue]) -> dict[str, MetricValue]:
        return _validate_planner_safe_metrics(value)


class PlannerObservation(AgentModel):
    schema_version: Literal["planner_observation_v1"] = "planner_observation_v1"
    tool: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    status: ToolStatus
    metrics: dict[str, MetricValue] = Field(default_factory=dict)
    warning_codes: list[str] = Field(default_factory=list, max_length=64)
    error_code: str | None = Field(default=None, max_length=128)
    retryable: bool = False

    @field_validator("metrics")
    @classmethod
    def validate_metrics(cls, value: dict[str, MetricValue]) -> dict[str, MetricValue]:
        return _validate_planner_safe_metrics(value)

    @classmethod
    def from_tool_result(cls, result: ToolResult) -> "PlannerObservation":
        return cls(
            tool=result.tool,
            status=result.status,
            metrics=result.metrics,
            warning_codes=result.warning_codes,
            error_code=result.error_code,
            retryable=result.retryable,
        )


class AgentRunState(AgentModel):
    step_count: int = Field(default=0, ge=0)
    planner_calls: int = Field(default=0, ge=0)
    tool_invocations: int = Field(default=0, ge=0)
    pipeline_attempts: int = Field(default=0, ge=0)
    latest_observation: PlannerObservation | None = None


def _validate_planner_safe_metrics(
    value: dict[str, MetricValue],
) -> dict[str, MetricValue]:
    if len(value) > 64:
        raise ValueError("tool metrics exceed the bounded key count")
    for key, item in value.items():
        if _METRIC_KEY_RE.fullmatch(key) is None:
            raise ValueError("tool metric keys must be stable snake_case identifiers")
        if isinstance(item, str):
            if len(item) > 256:
                raise ValueError("tool metric string exceeds the bounded length")
            if _ABSOLUTE_PATH_RE.match(item):
                raise ValueError("tool metrics must not contain artifact paths")
    return value
