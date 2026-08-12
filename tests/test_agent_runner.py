from __future__ import annotations

import json
from pathlib import Path

import pytest

from spectrail.agent.errors import AgentError
from spectrail.agent.planner import FinishDecision, InvokeToolDecision
from spectrail.agent.policy import AgentPolicy
from spectrail.agent.runner import AgentRunner
from spectrail.agent.trace import AgentTraceRecoveryError, AgentTraceWriter
from spectrail.core.io import read_json
from spectrail.core.io import write_json
from spectrail.llm.errors import ModelProviderError
from spectrail.llm.mock_model import MockModel
from spectrail.llm.recorded_agent_planner import RecordedAgentPlanner
from spectrail.llm.request_profile import ModelRequestProfile
from spectrail.pipeline import PipelineConfig, PipelineResult


def _policy(**overrides) -> AgentPolicy:
    values = {
        "allowed_tools": ["run_requirement_extraction"],
        "evidence_policy": "structured_if_available",
        "validation_policy": "strict",
        "allow_chunking_modes": ["auto", "force"],
    }
    values.update(overrides)
    return AgentPolicy(**values)


def test_agent_runner_completes_clean_recorded_run_with_durable_trace(tmp_path: Path):
    output = tmp_path / "agent_demo"
    runner = AgentRunner(
        planner=RecordedAgentPlanner("fixtures/agent/sample_srs_agent.json"),
        policy=_policy(),
        pipeline_config=PipelineConfig(model_mode="mock"),
    )

    result = runner.run(
        "docs/sample_srs.md",
        output,
        run_generation=1,
    )

    assert result.final_state.outcome == "completed"
    assert result.final_state.steps_used == 2
    assert result.final_state.planner_calls == 2
    assert result.final_state.tool_invocations == 2  # profile prelude + extraction
    assert result.final_state.pipeline_attempts == 1
    assert read_json(output / "agent" / "policy.json")["fail_fast"] is False
    assert read_json(output / "agent" / "profile.json")["schema_version"] == "document_profile_v1"
    events = sorted((output / "agent" / "events").glob("*.json"))
    assert [path.name for path in events] == [
        f"{index:06d}.json" for index in range(1, 9)
    ]
    trace = (output / "agent" / "trace.jsonl").read_text(encoding="utf-8")
    assert "用户输入正确账号密码后" not in trace
    assert str(output) not in trace
    assert "SPECTRAIL_LLM_API_KEY" not in trace

    manifest = read_json(output / "run_manifest.json")
    assert manifest["run_generation"] == 1
    assert manifest["orchestration"] == {
        "mode": "agent",
        "planner_mode": "recorded",
        "planner_model": "recorded-agent-v1",
        "planner_prompt_version": "agent_planner_v1",
        "policy_schema_version": "agent_policy_v1",
        "profile_schema_version": "document_profile_v1",
        "steps_used": 2,
        "planner_calls": 2,
        "tool_invocations": 2,
        "pipeline_attempts": 1,
        "outcome": "completed",
        "events": "agent/events",
        "trace": "agent/trace.jsonl",
        "final_state": "agent/final_state.json",
    }


def test_agent_runner_parses_source_once(tmp_path: Path, monkeypatch):
    calls = 0
    from spectrail.parsers.registry import parse_document as real_parse

    def counted_parse(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_parse(*args, **kwargs)

    monkeypatch.setattr("spectrail.agent.runner.parse_document", counted_parse)
    runner = AgentRunner(
        planner=RecordedAgentPlanner("fixtures/agent/sample_srs_agent.json"),
        policy=_policy(),
        pipeline_config=PipelineConfig(model_mode="mock"),
    )

    runner.run("docs/sample_srs.md", tmp_path / "agent_demo", run_generation=1)

    assert calls == 1


class StaticPlanner:
    planner_mode = "recorded"
    request_profile = ModelRequestProfile(
        provider_adapter="openai_compatible_v1",
        provider_endpoint_id="static-agent",
        model_name="static-agent",
        response_format={"type": "json_object"},
    )

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.inputs = []

    def decide(self, planner_input):
        self.inputs.append(planner_input)
        return self.decisions.pop(0)

    def assert_consumed(self):
        if self.decisions:
            raise AssertionError("unused decisions")


@pytest.mark.parametrize(
    "decision",
    [
        InvokeToolDecision(
            action="invoke_tool",
            tool="unknown_tool",
            arguments={},
            reason="Try an unknown tool.",
        ),
        InvokeToolDecision(
            action="invoke_tool",
            tool="run_requirement_extraction",
            arguments={"evidence_policy": "quote_only"},
            reason="Try to weaken policy.",
        ),
        InvokeToolDecision(
            action="invoke_tool",
            tool="run_requirement_extraction",
            arguments={"fail_fast": True},
            reason="Try to enable fail-fast.",
        ),
    ],
)
def test_agent_runner_rejects_invalid_invocation_before_side_effect(
    tmp_path: Path,
    decision,
):
    output = tmp_path / "agent_demo"
    runner = AgentRunner(
        planner=StaticPlanner([decision]),
        policy=_policy(),
        pipeline_config=PipelineConfig(model_mode="mock"),
    )

    with pytest.raises(AgentError):
        runner.run("docs/sample_srs.md", output, run_generation=1)

    assert not (output / "run_manifest.json").exists()
    events = [read_json(path) for path in sorted((output / "agent" / "events").glob("*.json"))]
    assert any(event["event_type"] == "policy_rejection" for event in events)
    assert not any(event["event_type"] == "tool_started" for event in events)


def test_agent_runner_rejects_completed_finish_without_pipeline_attempt(tmp_path: Path):
    runner = AgentRunner(
        planner=StaticPlanner(
            [
                FinishDecision(
                    action="finish",
                    outcome="completed",
                    reason="Pretend the task completed.",
                )
            ]
        ),
        policy=_policy(),
        pipeline_config=PipelineConfig(model_mode="mock"),
    )

    with pytest.raises(AgentError, match="AGENT_FINAL_STATE_INVALID"):
        runner.run("docs/sample_srs.md", tmp_path / "agent_demo", run_generation=1)


def test_agent_runner_allows_needs_human_without_pipeline_attempt(tmp_path: Path):
    output = tmp_path / "agent_demo"
    runner = AgentRunner(
        planner=StaticPlanner(
            [
                FinishDecision(
                    action="finish",
                    outcome="needs_human",
                    reason="No safe autonomous extraction action is available.",
                )
            ]
        ),
        policy=_policy(),
        pipeline_config=PipelineConfig(model_mode="mock"),
    )

    result = runner.run("docs/sample_srs.md", output, run_generation=2)

    assert result.final_state.outcome == "needs_human"
    manifest = read_json(output / "run_manifest.json")
    assert manifest["status"] == "completed_with_warnings"
    assert manifest["warning_codes"] == ["AGENT_NEEDS_HUMAN"]
    assert manifest["run_generation"] == 2


@pytest.mark.parametrize(
    "arguments",
    [
        {"chunking_mode": "off"},
        {"chunking_mode": "auto", "max_rendered_prompt_chars": 999},
        {"chunking_mode": "auto", "max_rendered_prompt_chars": 32001},
        {"chunking_mode": "auto", "overlap_blocks": 4},
    ],
)
def test_agent_runner_enforces_policy_argument_bounds(tmp_path: Path, arguments):
    runner = AgentRunner(
        planner=StaticPlanner(
            [
                InvokeToolDecision(
                    action="invoke_tool",
                    tool="run_requirement_extraction",
                    arguments=arguments,
                    reason="Try a policy-bounded setting.",
                )
            ]
        ),
        policy=_policy(),
        pipeline_config=PipelineConfig(model_mode="mock"),
    )

    with pytest.raises(AgentError):
        runner.run("docs/sample_srs.md", tmp_path / "agent_demo", run_generation=1)


def test_agent_runner_stops_before_planner_call_after_budget_exhaustion(tmp_path: Path):
    output = tmp_path / "agent_demo"
    runner = AgentRunner(
        planner=StaticPlanner(
            [
                InvokeToolDecision(
                    action="invoke_tool",
                    tool="run_requirement_extraction",
                    arguments={"chunking_mode": "auto"},
                    reason="Run the only allowed step.",
                ),
                FinishDecision(
                    action="finish",
                    outcome="completed",
                    reason="This must not be reached.",
                ),
            ]
        ),
        policy=_policy(max_steps=1, max_planner_calls=1, max_pipeline_attempts=1),
        pipeline_config=PipelineConfig(model_mode="mock"),
    )

    with pytest.raises(AgentError, match="AGENT_BUDGET_EXHAUSTED"):
        runner.run("docs/sample_srs.md", output, run_generation=1)

    events = [read_json(path) for path in sorted((output / "agent" / "events").glob("*.json"))]
    assert sum(event["event_type"] == "planner_request" for event in events) == 1


def test_agent_trace_rebuilds_jsonl_from_immutable_events(tmp_path: Path):
    output = tmp_path / "agent_demo"
    AgentRunner(
        planner=RecordedAgentPlanner("fixtures/agent/sample_srs_agent.json"),
        policy=_policy(),
        pipeline_config=PipelineConfig(model_mode="mock"),
    ).run("docs/sample_srs.md", output, run_generation=3)
    trace_path = output / "agent" / "trace.jsonl"
    trace_path.write_text('{"truncated":', encoding="utf-8")

    writer = AgentTraceWriter(output / "agent", run_generation=3)
    writer.rebuild_trace()

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 8
    assert json.loads(lines[-1])["event_type"] == "finish"


def test_agent_trace_fails_closed_on_sequence_gap(tmp_path: Path):
    output = tmp_path / "agent_demo"
    AgentRunner(
        planner=RecordedAgentPlanner("fixtures/agent/sample_srs_agent.json"),
        policy=_policy(),
        pipeline_config=PipelineConfig(model_mode="mock"),
    ).run("docs/sample_srs.md", output, run_generation=1)
    events = output / "agent" / "events"
    (events / "000002.json").rename(events / "000009.json")

    with pytest.raises(AgentTraceRecoveryError, match="AGENT_TRACE_RECOVERY_REQUIRED"):
        AgentTraceWriter(output / "agent", run_generation=1)


def test_pipeline_within_transaction_entry_rejects_unlocked_call(tmp_path: Path):
    from spectrail.pipeline import PipelineRunner

    with pytest.raises(ValueError, match="PIPELINE_TRANSACTION_REQUIRED"):
        PipelineRunner().extract_within_transaction(
            "docs/sample_srs.md",
            tmp_path / "demo",
        )


class ReplanPipelineRunner:
    def __init__(self) -> None:
        self.calls = []

    def extract_within_transaction(
        self,
        document_path,
        output_dir,
        *,
        run_generation,
        config,
        parsed_document,
        **kwargs,
    ):
        output = Path(output_dir)
        attempt = len(self.calls) + 1
        self.calls.append(
            {
                "attempt": attempt,
                "run_generation": run_generation,
                "chunking_mode": config.chunking.mode,
                "max_rendered_prompt_chars": config.chunking.max_rendered_prompt_chars,
                "parsed_document": parsed_document,
            }
        )
        for name in ("parsed", "extracted", "review", "exports"):
            (output / name).mkdir(parents=True, exist_ok=True)
        write_json(output / "plan.json", {"attempt": attempt})
        write_json(output / "exports" / "reqir.json", {"items": []})
        (output / "exports" / "requirements.xlsx").write_bytes(b"xlsx")

        if attempt == 1:
            status = "completed_with_warnings"
            warning_codes = ["PARTIAL_CHUNK_FAILURE"]
            counts = {
                "chunks": 2,
                "chunks_completed": 1,
                "chunks_failed": 1,
                "model_items_accepted": 3,
                "model_items_rejected": 1,
                "validated_requirements": 2,
                "quarantined_requirements": 1,
                "source_quote_failed": 1,
                "source_locator_failed": 0,
            }
            chunks = [
                {"warnings": []},
                {"warnings": ["CHUNK_PROMPT_OVER_BUDGET"]},
            ]
        else:
            status = "completed"
            warning_codes = []
            counts = {
                "chunks": 2,
                "chunks_completed": 2,
                "chunks_failed": 0,
                "model_items_accepted": 4,
                "model_items_rejected": 0,
                "validated_requirements": 4,
                "quarantined_requirements": 0,
                "source_quote_failed": 0,
                "source_locator_failed": 0,
            }
            chunks = [{"warnings": []}, {"warnings": []}]
        write_json(output / "parsed" / "chunks.json", chunks)
        write_json(
            output / "run_manifest.json",
            {
                "task_id": output.name,
                "run_generation": run_generation,
                "status": status,
                "warning_codes": warning_codes,
                "counts": counts,
                "zero_result_reason": None,
                "error_code": None,
            },
        )
        return PipelineResult(
            task_id=output.name,
            output_dir=output,
            plan_path=output / "plan.json",
            manifest_path=output / "run_manifest.json",
            validated_reqir_path=output / "extracted" / "reqir.validated.json",
            exported_reqir_path=output / "exports" / "reqir.json",
            xlsx_path=output / "exports" / "requirements.xlsx",
            validated_count=counts["validated_requirements"],
            status=status,
        )


def test_agent_runner_inspects_and_replans_within_one_generation(tmp_path: Path):
    output = tmp_path / "agent_replan"
    pipeline_runner = ReplanPipelineRunner()
    policy = AgentPolicy(
        allowed_tools=[
            "inspect_extraction_result",
            "run_requirement_extraction",
        ],
        evidence_policy="structured_if_available",
        validation_policy="strict",
        allow_chunking_modes=["auto", "force"],
    )
    runner = AgentRunner(
        planner=RecordedAgentPlanner("fixtures/agent/sample_srs_replan_agent.json"),
        policy=policy,
        pipeline_config=PipelineConfig(model_mode="mock"),
        pipeline_runner=pipeline_runner,
    )

    result = runner.run("docs/sample_srs.md", output, run_generation=7)

    assert result.final_state.outcome == "completed"
    assert result.final_state.steps_used == 4
    assert result.final_state.planner_calls == 4
    assert result.final_state.tool_invocations == 4  # profile, run, inspect, retry
    assert result.final_state.pipeline_attempts == 2
    assert [call["run_generation"] for call in pipeline_runner.calls] == [7, 7]
    assert pipeline_runner.calls[0]["parsed_document"] is pipeline_runner.calls[1]["parsed_document"]
    assert [call["chunking_mode"] for call in pipeline_runner.calls] == ["auto", "force"]
    assert pipeline_runner.calls[1]["max_rendered_prompt_chars"] == 8000
    assert sorted(path.name for path in (output / "agent" / "attempts").glob("*.json")) == [
        "attempt_0001.json",
        "attempt_0002.json",
    ]
    first_attempt = read_json(output / "agent" / "attempts" / "attempt_0001.json")
    second_attempt = read_json(output / "agent" / "attempts" / "attempt_0002.json")
    assert first_attempt["pipeline_status"] == "completed_with_warnings"
    assert second_attempt["pipeline_status"] == "completed"
    assert first_attempt["run_generation"] == second_attempt["run_generation"] == 7
    events = [read_json(path) for path in sorted((output / "agent" / "events").glob("*.json"))]
    inspect_result = next(
        event
        for event in events
        if event["event_type"] == "tool_result"
        and event["tool"] == "inspect_extraction_result"
    )
    assert inspect_result["payload"]["observation"]["metrics"]["chunk_prompt_over_budget"] is True
    assert read_json(output / "run_manifest.json")["run_generation"] == 7


class FailOnceModel:
    model_mode = "mock"

    def __init__(self) -> None:
        self.delegate = MockModel()
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        if self.calls == 1:
            raise ModelProviderError("synthetic recoverable provider failure")
        return self.delegate.generate(request)


def test_failed_extraction_becomes_observation_and_can_retry(
    tmp_path: Path,
    monkeypatch,
):
    output = tmp_path / "agent_failed_retry"
    model = FailOnceModel()
    monkeypatch.setattr(
        "spectrail.pipeline.runner.create_model_client",
        lambda **kwargs: model,
    )
    planner = StaticPlanner(
        [
            InvokeToolDecision(
                action="invoke_tool",
                tool="run_requirement_extraction",
                arguments={"chunking_mode": "auto"},
                reason="Run extraction.",
            ),
            InvokeToolDecision(
                action="invoke_tool",
                tool="run_requirement_extraction",
                arguments={
                    "chunking_mode": "auto",
                    "max_rendered_prompt_chars": 16000,
                },
                reason="Retry the recoverable provider failure.",
            ),
            FinishDecision(
                action="finish",
                outcome="completed",
                reason="The retry completed cleanly.",
            ),
        ]
    )
    runner = AgentRunner(
        planner=planner,
        policy=_policy(),
        pipeline_config=PipelineConfig(model_mode="mock"),
    )

    result = runner.run("docs/sample_srs.md", output, run_generation=8)

    failed_observation = planner.inputs[1].latest_observation
    assert failed_observation is not None
    assert failed_observation.status == "failed"
    assert failed_observation.error_code == "ModelProviderError"
    assert failed_observation.retryable is True
    assert failed_observation.metrics["pipeline_status"] == "failed"
    assert result.final_state.outcome == "completed"
    assert result.final_state.pipeline_attempts == 2
    assert read_json(output / "agent" / "attempts" / "attempt_0001.json")[
        "pipeline_status"
    ] == "failed"
    assert read_json(output / "agent" / "attempts" / "attempt_0002.json")[
        "pipeline_status"
    ] == "completed"


def test_inspect_failure_does_not_overwrite_extraction_attempt(
    tmp_path: Path,
    monkeypatch,
):
    output = tmp_path / "agent_inspect_failure"
    policy = AgentPolicy(
        allowed_tools=[
            "inspect_extraction_result",
            "run_requirement_extraction",
        ],
        evidence_policy="structured_if_available",
        validation_policy="strict",
        allow_chunking_modes=["auto", "force"],
    )
    planner = StaticPlanner(
        [
            InvokeToolDecision(
                action="invoke_tool",
                tool="run_requirement_extraction",
                arguments={"chunking_mode": "auto"},
                reason="Run extraction.",
            ),
            InvokeToolDecision(
                action="invoke_tool",
                tool="inspect_extraction_result",
                arguments={},
                reason="Inspect the extraction artifact.",
            ),
        ]
    )

    def fail_inspection(self, context, arguments):
        raise ValueError("AGENT_INSPECTION_GENERATION_MISMATCH")

    monkeypatch.setattr(
        "spectrail.agent.runner.InspectExtractionResultTool.invoke",
        fail_inspection,
    )

    with pytest.raises(AgentError, match="AGENT_INSPECTION_GENERATION_MISMATCH"):
        AgentRunner(
            planner=planner,
            policy=policy,
            pipeline_config=PipelineConfig(model_mode="mock"),
        ).run("docs/sample_srs.md", output, run_generation=9)

    attempts = sorted((output / "agent" / "attempts").glob("*.json"))
    assert [path.name for path in attempts] == ["attempt_0001.json"]
    assert read_json(attempts[0])["pipeline_status"] == "completed"
    events = [
        read_json(path)
        for path in sorted((output / "agent" / "events").glob("*.json"))
    ]
    inspect_failure = next(
        event
        for event in events
        if event["event_type"] == "tool_result"
        and event["tool"] == "inspect_extraction_result"
    )
    assert inspect_failure["payload"]["observation"]["error_code"] == (
        "AGENT_INSPECTION_GENERATION_MISMATCH"
    )


class HardFailurePipelineRunner:
    def extract_within_transaction(
        self,
        document_path,
        output_dir,
        *,
        run_generation,
        **kwargs,
    ):
        output = Path(output_dir)
        write_json(
            output / "run_manifest.json",
            {
                "task_id": output.name,
                "run_generation": run_generation,
                "status": "failed",
                "warning_codes": [],
                "counts": {},
                "error_code": "PREPARSED_DOCUMENT_MISMATCH",
            },
        )
        raise ValueError("PREPARSED_DOCUMENT_MISMATCH")


def test_unallowlisted_pipeline_failure_remains_hard_failure(tmp_path: Path):
    output = tmp_path / "agent_hard_failure"
    planner = StaticPlanner(
        [
            InvokeToolDecision(
                action="invoke_tool",
                tool="run_requirement_extraction",
                arguments={"chunking_mode": "auto"},
                reason="Run extraction.",
            ),
            FinishDecision(
                action="finish",
                outcome="failed",
                reason="This must not be reached.",
            ),
        ]
    )

    with pytest.raises(AgentError, match="PREPARSED_DOCUMENT_MISMATCH"):
        AgentRunner(
            planner=planner,
            policy=_policy(),
            pipeline_config=PipelineConfig(model_mode="mock"),
            pipeline_runner=HardFailurePipelineRunner(),
        ).run("docs/sample_srs.md", output, run_generation=10)

    assert len(planner.inputs) == 1
    assert read_json(output / "agent" / "final_state.json")["outcome"] == "failed"
