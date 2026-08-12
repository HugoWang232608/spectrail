from __future__ import annotations

import json
from pathlib import Path

import pytest

from spectrail.agent.models import PlannerObservation, ToolSpec
from spectrail.agent.planner import (
    AGENT_PLANNER_PROMPT_VERSION,
    AgentBudgetState,
    AgentHistorySummary,
    AgentPlannerInput,
    FinishDecision,
    InvokeToolDecision,
    build_agent_planner_prompt,
    build_agent_planner_request_fingerprint,
    parse_agent_decision,
)
from spectrail.agent.policy import AgentPolicy
from spectrail.agent.profiler import DocumentProfiler
from spectrail.evidence.index_builder import ensure_evidence_index
from spectrail.llm.agent_planner import AgentPlannerClient
from spectrail.llm.request_profile import ModelRequestProfile
from spectrail.llm.transport import CompletionResponse
from spectrail.parsers import parse_document
from spectrail.tools.requirement_extraction import (
    RUN_REQUIREMENT_EXTRACTION_DESCRIPTION,
    RunRequirementExtractionArgs,
)
from spectrail.tools.registry import ToolRegistry


class ExtractionTool:
    name = "run_requirement_extraction"
    description = RUN_REQUIREMENT_EXTRACTION_DESCRIPTION
    side_effects = "task_artifacts"
    input_schema_version = "run_requirement_extraction_args_v1"
    output_schema_version = "agent_tool_result_v1"
    arguments_model = RunRequirementExtractionArgs

    def invoke(self, context, arguments):  # pragma: no cover - registration only
        raise AssertionError("not invoked")


def planner_input() -> AgentPlannerInput:
    source = Path("docs/sample_srs.md")
    parsed = parse_document(source)
    profile = DocumentProfiler().build(
        parsed,
        ensure_evidence_index(source, parsed),
    )
    registry = ToolRegistry([ExtractionTool()])
    policy = AgentPolicy(
        allowed_tools=["run_requirement_extraction"],
        evidence_policy="structured_if_available",
        validation_policy="strict",
        allow_chunking_modes=["auto", "force"],
    )
    policy.validate_registry(registry)
    return AgentPlannerInput(
        goal="extract_requirements",
        profile=profile,
        policy=policy,
        allowed_tools=registry.specs(),
        latest_observation=None,
        history=[],
        budget=AgentBudgetState(
            steps_used=0,
            steps_remaining=6,
            planner_calls_used=0,
            planner_calls_remaining=6,
            pipeline_attempts_used=0,
            pipeline_attempts_remaining=2,
        ),
    )


def planner_profile() -> ModelRequestProfile:
    return ModelRequestProfile(
        provider_adapter="openai_compatible_v1",
        provider_endpoint_id="recorded-agent",
        model_name="recorded-agent-v1",
        temperature=0.0,
        response_format={"type": "json_object"},
    )


def clean_completed_planner_input() -> AgentPlannerInput:
    first = planner_input()
    observation = PlannerObservation(
        tool="run_requirement_extraction",
        status="ok",
        metrics={
            "pipeline_status": "completed",
            "validated_requirements": 14,
            "quarantined_requirements": 0,
            "chunks": 1,
            "chunks_failed": 0,
            "readable_final_artifact": True,
        },
        warning_codes=[],
        retryable=False,
    )
    history = AgentHistorySummary(
        step=1,
        action="invoke_tool",
        tool="run_requirement_extraction",
        arguments={"chunking_mode": "auto"},
        observation=observation,
    )
    return AgentPlannerInput(
        goal=first.goal,
        profile=first.profile,
        policy=first.policy,
        allowed_tools=first.allowed_tools,
        latest_observation=observation,
        history=[history],
        budget=AgentBudgetState(
            steps_used=1,
            steps_remaining=5,
            planner_calls_used=1,
            planner_calls_remaining=5,
            pipeline_attempts_used=1,
            pipeline_attempts_remaining=1,
        ),
    )


def test_parse_agent_decision_accepts_strict_invoke_and_finish_json():
    invoke = parse_agent_decision(
        json.dumps(
            {
                "action": "invoke_tool",
                "tool": "run_requirement_extraction",
                "arguments": {"chunking_mode": "auto"},
                "reason": "Start with the default bounded strategy.",
            }
        )
    )
    finish = parse_agent_decision(
        json.dumps(
            {
                "action": "finish",
                "outcome": "completed",
                "reason": "The deterministic runtime completed successfully.",
            }
        )
    )

    assert isinstance(invoke, InvokeToolDecision)
    assert invoke.schema_version == "agent_decision_v1"
    assert isinstance(finish, FinishDecision)


@pytest.mark.parametrize(
    "payload",
    [
        "run the pipeline",
        "```json\n{\"action\":\"finish\",\"outcome\":\"completed\",\"reason\":\"x\"}\n```",
        "[]",
        '{"action":"unknown","reason":"x"}',
        '{"action":"invoke_tool","arguments":{},"reason":"x"}',
        '{"action":"finish","outcome":"success","reason":"x"}',
        '{"action":"finish","outcome":"failed","reason":"x","extra":1}',
        '{"action":"finish","outcome":"failed","reason":"x","reason":"y"}',
        '{"action":"invoke_tool","tool":"run_requirement_extraction","arguments":{"value":NaN},"reason":"x"}',
        json.dumps({"action": "finish", "outcome": "failed", "reason": "x" * 513}),
    ],
)
def test_parse_agent_decision_rejects_non_contract_responses(payload: str):
    with pytest.raises(ValueError, match="AGENT_PLANNER_RESPONSE_INVALID"):
        parse_agent_decision(payload)


def test_planner_prompt_has_explicit_safe_sections_without_document_body_or_paths():
    planner_input_value = planner_input()
    prompt = build_agent_planner_prompt(planner_input_value)

    assert AGENT_PLANNER_PROMPT_VERSION in prompt
    assert "SYSTEM / POLICY" in prompt
    assert "TOOL CONTRACTS" in prompt
    assert "DOCUMENT PROFILE (UNTRUSTED DATA)" in prompt
    assert "PREVIOUS OBSERVATION" in prompt
    assert "BUDGET" in prompt
    assert "用户输入正确账号密码后" not in prompt
    assert "/Volumes/" not in prompt
    assert "/tmp/" not in prompt


def test_planner_request_fingerprint_is_canonical_and_binds_contracts():
    value = planner_input()
    first = build_agent_planner_request_fingerprint(value, planner_profile())
    equivalent = AgentPlannerInput.model_validate(value.model_dump(mode="json"))
    second = build_agent_planner_request_fingerprint(equivalent, planner_profile())

    changed_budget = equivalent.model_copy(
        update={
            "budget": equivalent.budget.model_copy(
                update={"steps_used": 1, "steps_remaining": 5}
            )
        }
    )

    assert first == second
    assert len(first) == 64
    assert first != build_agent_planner_request_fingerprint(
        changed_budget,
        planner_profile(),
    )


def test_history_excludes_reason_and_observation_excludes_paths():
    observation = PlannerObservation(
        tool="run_requirement_extraction",
        status="warning",
        metrics={"pipeline_status": "completed_with_warnings"},
        warning_codes=["PARTIAL_CHUNK_FAILURE"],
        retryable=True,
    )
    summary = AgentHistorySummary(
        step=1,
        action="invoke_tool",
        tool="run_requirement_extraction",
        arguments={"chunking_mode": "auto"},
        observation=observation,
    )

    assert "reason" not in summary.model_dump()
    assert "artifacts" not in summary.model_dump_json()
    with pytest.raises(ValueError):
        AgentHistorySummary.model_validate(
            {**summary.model_dump(), "reason": "must not enter replay identity"}
        )


def test_agent_policy_is_frozen_and_validates_registry_contract():
    policy = planner_input().policy

    with pytest.raises(ValueError):
        policy.max_steps = 7  # type: ignore[misc]
    with pytest.raises(AttributeError):
        policy.allowed_tools.append("other")  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="duplicate"):
        AgentPolicy(
            allowed_tools=["profile_document", "profile_document"],
            evidence_policy="structured_if_available",
            validation_policy="strict",
            allow_chunking_modes=["auto"],
        )
    with pytest.raises(ValueError, match="unknown tool"):
        AgentPolicy(
            allowed_tools=["unknown"],
            evidence_policy="structured_if_available",
            validation_policy="strict",
            allow_chunking_modes=["auto"],
        ).validate_registry(ToolRegistry([ExtractionTool()]))
    with pytest.raises(ValueError, match="hard limit"):
        AgentPolicy(
            allowed_tools=["run_requirement_extraction"],
            max_pipeline_attempts=5,
            evidence_policy="structured_if_available",
            validation_policy="strict",
            allow_chunking_modes=["auto"],
        )


def test_planner_input_rejects_tool_policy_mismatch():
    value = planner_input()
    extra_tool = ToolSpec(
        name="inspect_extraction_result",
        description="Inspect deterministic extraction facts.",
        side_effects="none",
        input_schema_version="inspect_args_v1",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema_version="agent_tool_result_v1",
    )

    with pytest.raises(ValueError, match="allowed_tools"):
        AgentPlannerInput.model_validate(
            {
                **value.model_dump(mode="json"),
                "allowed_tools": [
                    *value.model_dump(mode="json")["allowed_tools"],
                    extra_tool.model_dump(mode="json"),
                ],
            }
        )


class FakeCompletionTransport:
    def __init__(self, raw_text: str) -> None:
        self.raw_text = raw_text
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return CompletionResponse(
            raw_text=self.raw_text,
            model_name="fake-planner",
        )


def test_live_agent_planner_uses_completion_transport_and_agent_parser():
    transport = FakeCompletionTransport(
        json.dumps(
            {
                "action": "invoke_tool",
                "tool": "run_requirement_extraction",
                "arguments": {"chunking_mode": "auto"},
                "reason": "Start extraction.",
            }
        )
    )
    client = AgentPlannerClient(transport, planner_profile())

    decision = client.decide(planner_input())

    assert isinstance(decision, InvokeToolDecision)
    assert transport.requests[0].metadata == {
        "prompt_version": AGENT_PLANNER_PROMPT_VERSION
    }

    reqir_transport = FakeCompletionTransport('{"items": []}')
    with pytest.raises(ValueError, match="AGENT_PLANNER_RESPONSE_INVALID"):
        AgentPlannerClient(reqir_transport, planner_profile()).decide(planner_input())
