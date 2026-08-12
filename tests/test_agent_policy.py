from __future__ import annotations

import pytest

from spectrail.agent.errors import AgentPolicyViolationError
from spectrail.agent.models import PlannerObservation
from spectrail.agent.planner import FinishDecision, InvokeToolDecision
from spectrail.agent.policy import (
    build_action_signature,
    validate_finish_decision,
)


def _finish(outcome: str) -> FinishDecision:
    return FinishDecision(
        action="finish",
        outcome=outcome,
        reason="Proposed deterministic final state.",
    )


def _observation(
    pipeline_status: str,
    *,
    validated: int = 0,
    quarantined: int = 0,
    readable: bool = False,
    warnings: list[str] | None = None,
    error_code: str | None = None,
    zero_result_reason: str | None = None,
) -> PlannerObservation:
    return PlannerObservation(
        tool="run_requirement_extraction",
        status=(
            "ok"
            if pipeline_status == "completed"
            else "warning"
            if pipeline_status == "completed_with_warnings"
            else "failed"
        ),
        metrics={
            "pipeline_status": pipeline_status,
            "validated_requirements": validated,
            "quarantined_requirements": quarantined,
            "readable_final_artifact": readable,
            "zero_result_reason": zero_result_reason,
        },
        warning_codes=warnings or [],
        error_code=error_code,
    )


@pytest.mark.parametrize("outcome", ["needs_human", "failed"])
def test_finish_lattice_allows_safe_outcomes_before_attempt(outcome: str):
    validate_finish_decision(
        _finish(outcome),
        pipeline_attempts=0,
        latest_observation=None,
    )


@pytest.mark.parametrize("outcome", ["completed", "completed_with_warnings"])
def test_finish_lattice_rejects_success_before_attempt(outcome: str):
    with pytest.raises(AgentPolicyViolationError, match="AGENT_FINAL_STATE_INVALID"):
        validate_finish_decision(
            _finish(outcome),
            pipeline_attempts=0,
            latest_observation=None,
        )


def test_finish_lattice_allows_only_clean_completed_success():
    clean = _observation(
        "completed",
        validated=3,
        readable=True,
    )
    validate_finish_decision(
        _finish("completed"),
        pipeline_attempts=1,
        latest_observation=clean,
    )

    for observation in [
        _observation("completed", validated=0, readable=True),
        _observation("completed", validated=3, quarantined=1, readable=True),
        _observation("completed", validated=3, readable=False),
        _observation(
            "completed",
            validated=3,
            readable=True,
            warnings=["TRUST_WARNING"],
        ),
    ]:
        with pytest.raises(AgentPolicyViolationError):
            validate_finish_decision(
                _finish("completed"),
                pipeline_attempts=1,
                latest_observation=observation,
            )


def test_finish_lattice_preserves_warning_and_failed_states():
    warning = _observation(
        "completed_with_warnings",
        validated=2,
        readable=True,
        warnings=["PARTIAL_CHUNK_FAILURE"],
    )
    validate_finish_decision(
        _finish("completed_with_warnings"),
        pipeline_attempts=1,
        latest_observation=warning,
    )
    validate_finish_decision(
        _finish("needs_human"),
        pipeline_attempts=1,
        latest_observation=warning,
    )
    failed = _observation("failed", error_code="ALL_CHUNKS_FAILED")
    validate_finish_decision(
        _finish("failed"),
        pipeline_attempts=1,
        latest_observation=failed,
    )
    validate_finish_decision(
        _finish("needs_human"),
        pipeline_attempts=1,
        latest_observation=failed,
    )

    with pytest.raises(AgentPolicyViolationError):
        validate_finish_decision(
            _finish("completed"),
            pipeline_attempts=1,
            latest_observation=warning,
        )


def test_action_signature_excludes_reason_but_binds_observation():
    first = InvokeToolDecision(
        action="invoke_tool",
        tool="run_requirement_extraction",
        arguments={"chunking_mode": "auto"},
        reason="First reason.",
    )
    second = first.model_copy(update={"reason": "Different audit prose."})
    observation = _observation("completed_with_warnings")

    assert build_action_signature(first, None) == build_action_signature(second, None)
    assert build_action_signature(first, None) != build_action_signature(
        second,
        observation,
    )
