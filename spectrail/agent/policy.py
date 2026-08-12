from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from spectrail.agent.models import AgentModel
from spectrail.evidence.models import EvidencePolicy


MAX_PIPELINE_ATTEMPTS_HARD_LIMIT = 4


class AgentPolicy(AgentModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["agent_policy_v1"] = "agent_policy_v1"
    allowed_tools: tuple[str, ...] = Field(min_length=1, max_length=32)

    max_steps: int = Field(default=6, gt=0)
    max_planner_calls: int = Field(default=6, gt=0)
    max_pipeline_attempts: int = Field(default=2, gt=0)

    min_prompt_chars: int = Field(default=1_000, gt=0)
    max_prompt_chars: int = Field(default=32_000, gt=0)
    max_overlap_blocks: int = Field(default=3, ge=0)

    evidence_policy: EvidencePolicy
    validation_policy: Literal["strict", "quarantine"]
    allow_chunking_modes: tuple[Literal["auto", "force", "off"], ...] = Field(
        min_length=1,
        max_length=3,
    )
    fail_fast: Literal[False] = False

    @field_validator("allowed_tools")
    @classmethod
    def validate_allowed_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("allowed_tools must not contain duplicate tool names")
        return tuple(sorted(value))

    @field_validator("allow_chunking_modes")
    @classmethod
    def validate_chunking_modes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("allow_chunking_modes must not contain duplicate values")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_limits(self) -> "AgentPolicy":
        if self.max_pipeline_attempts > MAX_PIPELINE_ATTEMPTS_HARD_LIMIT:
            raise ValueError(
                "max_pipeline_attempts exceeds the product hard limit of "
                f"{MAX_PIPELINE_ATTEMPTS_HARD_LIMIT}"
            )
        if self.min_prompt_chars > self.max_prompt_chars:
            raise ValueError("min_prompt_chars must not exceed max_prompt_chars")
        if self.max_pipeline_attempts > self.max_steps:
            raise ValueError("max_pipeline_attempts must not exceed max_steps")
        return self

    def validate_registry(self, registry) -> "AgentPolicy":
        registered = {spec.name for spec in registry.specs()}
        unknown = sorted(set(self.allowed_tools) - registered)
        if unknown:
            raise ValueError(f"AgentPolicy contains unknown tool: {', '.join(unknown)}")
        return self
