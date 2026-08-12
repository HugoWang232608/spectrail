from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from spectrail.agent.models import AgentModel
from spectrail.agent.errors import AgentPolicyViolationError
from spectrail.evidence.models import EvidencePolicy
from spectrail.llm.fingerprints import sha256_hex

if TYPE_CHECKING:
    from pydantic import BaseModel

    from spectrail.agent.models import PlannerObservation
    from spectrail.agent.planner import FinishDecision, InvokeToolDecision
    from spectrail.tools.registry import ToolRegistry


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


HUMAN_ACTIONABLE_WARNING_CODES = frozenset(
    {
        "ALL_CANDIDATES_QUARANTINED",
        "NO_REQUIREMENTS_FOUND",
        "PARTIAL_CHUNK_FAILURE",
    }
)
HUMAN_ACTIONABLE_ERROR_CODES = frozenset(
    {
        "ALL_CHUNKS_FAILED",
        "NO_EXTRACTABLE_CONTENT",
        "NO_VALID_MODEL_ITEMS",
    }
)


def validate_tool_decision(
    policy: AgentPolicy,
    registry: "ToolRegistry",
    decision: "InvokeToolDecision",
) -> "BaseModel":
    try:
        validated = registry.validate_arguments(decision.tool, decision.arguments)
    except ValueError as exc:
        raise AgentPolicyViolationError(str(exc)) from exc
    if decision.tool not in policy.allowed_tools:
        raise AgentPolicyViolationError(
            f"AGENT_TOOL_FORBIDDEN: {decision.tool}"
        )
    if decision.tool == "run_requirement_extraction":
        if validated.chunking_mode not in policy.allow_chunking_modes:
            raise AgentPolicyViolationError("AGENT_CHUNKING_MODE_FORBIDDEN")
        if (
            validated.max_rendered_prompt_chars is not None
            and not (
                policy.min_prompt_chars
                <= validated.max_rendered_prompt_chars
                <= policy.max_prompt_chars
            )
        ):
            raise AgentPolicyViolationError("AGENT_PROMPT_BUDGET_OUT_OF_RANGE")
        if (
            validated.overlap_blocks is not None
            and validated.overlap_blocks > policy.max_overlap_blocks
        ):
            raise AgentPolicyViolationError("AGENT_OVERLAP_OUT_OF_RANGE")
    return validated


def validate_finish_decision(
    decision: "FinishDecision",
    *,
    pipeline_attempts: int,
    latest_observation: "PlannerObservation | None",
) -> None:
    outcome = decision.outcome
    if pipeline_attempts == 0:
        if outcome in {"needs_human", "failed"}:
            return
        _invalid_finish()
    if latest_observation is None:
        _invalid_finish()

    assert latest_observation is not None
    pipeline_status = latest_observation.metrics.get("pipeline_status")
    validated = latest_observation.metrics.get("validated_requirements", 0)
    quarantined = latest_observation.metrics.get("quarantined_requirements", 0)
    readable = latest_observation.metrics.get("readable_final_artifact", False)
    zero_result_reason = latest_observation.metrics.get("zero_result_reason")

    if pipeline_status == "failed":
        if outcome == "failed":
            return
        if (
            outcome == "needs_human"
            and latest_observation.error_code in HUMAN_ACTIONABLE_ERROR_CODES
        ):
            return
        _invalid_finish()
    if pipeline_status == "completed_with_warnings":
        if outcome == "completed_with_warnings":
            return
        if outcome == "needs_human" and (
            set(latest_observation.warning_codes)
            & HUMAN_ACTIONABLE_WARNING_CODES
        ):
            return
        _invalid_finish()
    if pipeline_status == "completed":
        clean_success = (
            isinstance(validated, int)
            and not isinstance(validated, bool)
            and validated > 0
            and quarantined == 0
            and readable is True
            and not latest_observation.warning_codes
        )
        if clean_success and outcome == "completed":
            return
        zero_is_human_actionable = zero_result_reason in (
            HUMAN_ACTIONABLE_WARNING_CODES | HUMAN_ACTIONABLE_ERROR_CODES
        )
        if not clean_success and (
            outcome == "failed"
            or (outcome == "needs_human" and zero_is_human_actionable)
        ):
            return
        _invalid_finish()
    _invalid_finish()


def build_action_signature(
    decision: "InvokeToolDecision",
    observation: "PlannerObservation | None",
) -> str:
    return sha256_hex(
        {
            "tool": decision.tool,
            "arguments": decision.arguments,
            "observation": (
                observation.model_dump(mode="json")
                if observation is not None
                else None
            ),
        }
    )


def _invalid_finish() -> None:
    raise AgentPolicyViolationError("AGENT_FINAL_STATE_INVALID")
