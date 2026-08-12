from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from spectrail.agent.errors import (
    AgentError,
    AgentPolicyViolationError,
    AgentRunnerError,
)
from spectrail.agent.artifacts import (
    prepare_new_agent_generation,
    reset_pipeline_artifacts_for_agent_retry,
)
from spectrail.agent.models import AgentRunState, PlannerObservation
from spectrail.agent.planner import (
    AGENT_PLANNER_PROMPT_VERSION,
    AgentBudgetState,
    AgentHistorySummary,
    AgentPlanner,
    AgentPlannerInput,
    FinishDecision,
    InvokeToolDecision,
    build_agent_planner_request_fingerprint,
)
from spectrail.agent.policy import (
    AgentPolicy,
    build_action_signature,
    validate_finish_decision,
    validate_tool_decision,
)
from spectrail.agent.profiler import DocumentProfiler
from spectrail.agent.trace import (
    AgentAttemptSummary,
    AgentFinalState,
    AgentTraceWriter,
)
from spectrail.core.io import read_json, write_json
from spectrail.core.manifest import complete_manifest, fail_manifest, init_manifest
from spectrail.evidence.index_builder import ensure_evidence_index
from spectrail.parsers import parse_document
from spectrail.pipeline import PipelineConfig, PipelineRunner
from spectrail.task_transactions import task_operation
from spectrail.tools.base import AgentExecutionContext
from spectrail.tools.document_profile import ProfileDocumentTool
from spectrail.tools.extraction_inspection import InspectExtractionResultTool
from spectrail.tools.registry import ToolRegistry
from spectrail.tools.requirement_extraction import RunRequirementExtractionTool


@dataclass(frozen=True)
class AgentRunResult:
    task_id: str
    output_dir: Path
    final_state: AgentFinalState
    manifest_path: Path
    trace_path: Path


class AgentRunner:
    def __init__(
        self,
        *,
        planner: AgentPlanner,
        policy: AgentPolicy,
        pipeline_config: PipelineConfig,
        pipeline_runner: PipelineRunner | None = None,
    ) -> None:
        if pipeline_config.chunking.fail_fast or policy.fail_fast:
            raise ValueError("M6 freezes fail_fast=false")
        if pipeline_config.validation_policy != policy.validation_policy:
            raise ValueError("pipeline validation_policy does not match AgentPolicy")
        if pipeline_config.evidence_policy != policy.evidence_policy:
            raise ValueError("pipeline evidence_policy does not match AgentPolicy")
        self.planner = planner
        self.policy = policy
        self.pipeline_config = pipeline_config
        self.pipeline_runner = pipeline_runner or PipelineRunner()

    def run(
        self,
        document_path: str | Path,
        output_dir: str | Path,
        *,
        run_generation: int = 1,
    ) -> AgentRunResult:
        output = Path(output_dir)
        with task_operation(output, "agent_run"):
            return self._run_locked(
                Path(document_path),
                output,
                run_generation=run_generation,
            )

    def _run_locked(
        self,
        document: Path,
        output: Path,
        *,
        run_generation: int,
    ) -> AgentRunResult:
        if run_generation < 1:
            raise ValueError("run_generation must be positive")
        prepare_new_agent_generation(output)
        task_id = output.name
        parsed = parse_document(document, document_id="doc_001")
        evidence_index = ensure_evidence_index(document, parsed)
        profile = DocumentProfiler().build(parsed, evidence_index)
        trace = AgentTraceWriter(output / "agent", run_generation=run_generation)
        trace.publish_model("policy.json", self.policy)
        trace.publish_model("profile.json", profile)

        registry = ToolRegistry(
            [
                ProfileDocumentTool(),
                InspectExtractionResultTool(),
                RunRequirementExtractionTool(
                    pipeline_runner=self.pipeline_runner,
                    pipeline_config=self.pipeline_config,
                ),
            ]
        )
        self.policy.validate_registry(registry)
        context = AgentExecutionContext(
            task_id=task_id,
            run_generation=run_generation,
            task_dir=output,
            document_path=document,
            policy=self.policy,
            parsed_document=parsed,
            evidence_index=evidence_index,
            document_profile=profile,
        )
        state = AgentRunState()
        history: list[AgentHistorySummary] = []
        action_signatures: set[str] = set()

        profile_result = registry.invoke("profile_document", context, {})
        state.tool_invocations += 1
        trace.append(
            "profile",
            step=0,
            tool="profile_document",
            payload={
                "profile_schema_version": profile.schema_version,
                "observation": PlannerObservation.from_tool_result(
                    profile_result
                ).model_dump(mode="json"),
            },
        )

        while True:
            if (
                state.step_count >= self.policy.max_steps
                or state.planner_calls >= self.policy.max_planner_calls
            ):
                self._fail(
                    trace,
                    state,
                    task_id=task_id,
                    run_generation=run_generation,
                    code="AGENT_BUDGET_EXHAUSTED",
                )

            planner_input = self._planner_input(
                profile=profile,
                registry=registry,
                state=state,
                history=history,
            )
            fingerprint = build_agent_planner_request_fingerprint(
                planner_input,
                self.planner.request_profile,
            )
            next_step = state.step_count + 1
            trace.append(
                "planner_request",
                step=next_step,
                planner_request_fingerprint=fingerprint,
                payload={
                    "planner_prompt_version": AGENT_PLANNER_PROMPT_VERSION,
                    "budget": planner_input.budget.model_dump(mode="json"),
                },
            )
            state.planner_calls += 1
            try:
                decision = self.planner.decide(planner_input)
            except Exception as exc:
                self._fail(
                    trace,
                    state,
                    task_id=task_id,
                    run_generation=run_generation,
                    code="AGENT_PLANNER_FAILED",
                    cause=exc,
                )
            state.step_count += 1
            trace.append(
                "decision",
                step=state.step_count,
                planner_request_fingerprint=fingerprint,
                tool=(decision.tool if isinstance(decision, InvokeToolDecision) else None),
                payload=decision.model_dump(mode="json"),
            )

            if isinstance(decision, FinishDecision):
                try:
                    validate_finish_decision(
                        decision,
                        pipeline_attempts=state.pipeline_attempts,
                        latest_observation=state.latest_observation,
                    )
                    assert_consumed = getattr(self.planner, "assert_consumed", None)
                    if assert_consumed is not None:
                        assert_consumed()
                except Exception as exc:
                    self._fail(
                        trace,
                        state,
                        task_id=task_id,
                        run_generation=run_generation,
                        code=str(exc),
                        cause=exc,
                    )
                return self._finish(
                    trace,
                    state,
                    task_id=task_id,
                    output=output,
                    run_generation=run_generation,
                    profile=profile,
                    decision=decision,
                )

            assert isinstance(decision, InvokeToolDecision)
            try:
                validated_arguments = validate_tool_decision(
                    self.policy,
                    registry,
                    decision,
                )
                if decision.tool == "run_requirement_extraction":
                    if state.pipeline_attempts >= self.policy.max_pipeline_attempts:
                        raise AgentPolicyViolationError(
                            "AGENT_PIPELINE_ATTEMPT_BUDGET_EXHAUSTED"
                        )
                if (
                    decision.tool == "inspect_extraction_result"
                    and state.pipeline_attempts == 0
                ):
                    raise AgentPolicyViolationError(
                        "AGENT_INSPECTION_REQUIRES_ATTEMPT"
                    )
                signature = build_action_signature(
                    decision,
                    state.latest_observation,
                )
                if signature in action_signatures:
                    raise AgentPolicyViolationError("AGENT_REPEATED_ACTION_LOOP")
            except Exception as exc:
                trace.append(
                    "policy_rejection",
                    step=state.step_count,
                    tool=decision.tool,
                    payload={"error_code": _safe_error_code(exc)},
                )
                self._fail(
                    trace,
                    state,
                    task_id=task_id,
                    run_generation=run_generation,
                    code=_safe_error_code(exc),
                    cause=exc,
                )

            action_signatures.add(signature)
            if (
                decision.tool == "run_requirement_extraction"
                and state.pipeline_attempts > 0
            ):
                reset_pipeline_artifacts_for_agent_retry(output)
            if decision.tool == "run_requirement_extraction":
                state.pipeline_attempts += 1
            state.tool_invocations += 1
            trace.append(
                "tool_started",
                step=state.step_count,
                tool=decision.tool,
                payload={"arguments": decision.arguments},
            )
            started_at = _now_utc()
            try:
                tool_result = registry.invoke(
                    decision.tool,
                    context,
                    validated_arguments.model_dump(
                        mode="json",
                        exclude_none=True,
                    ),
                )
            except Exception as exc:
                self._publish_failed_attempt(
                    trace,
                    output,
                    state,
                    decision,
                    started_at,
                    exc,
                )
                self._fail(
                    trace,
                    state,
                    task_id=task_id,
                    run_generation=run_generation,
                    code=_safe_error_code(exc),
                    cause=exc,
                )

            observation = PlannerObservation.from_tool_result(tool_result)
            state.latest_observation = observation
            trace.append(
                "tool_result",
                step=state.step_count,
                tool=decision.tool,
                payload={"observation": observation.model_dump(mode="json")},
            )
            if decision.tool == "run_requirement_extraction":
                trace.publish_attempt(
                    AgentAttemptSummary(
                        run_generation=run_generation,
                        attempt=state.pipeline_attempts,
                        arguments=decision.arguments,
                        pipeline_status=str(
                            observation.metrics.get("pipeline_status", "failed")
                        ),
                        warning_codes=observation.warning_codes,
                        counts=_attempt_counts(observation),
                        error_code=observation.error_code,
                        started_at=started_at,
                        completed_at=_now_utc(),
                    )
                )
            history.append(
                AgentHistorySummary(
                    step=state.step_count,
                    action="invoke_tool",
                    tool=decision.tool,
                    arguments=decision.arguments,
                    observation=observation,
                )
            )

    def _planner_input(
        self,
        *,
        profile,
        registry: ToolRegistry,
        state: AgentRunState,
        history: list[AgentHistorySummary],
    ) -> AgentPlannerInput:
        specs = [
            registry.get_spec(name)
            for name in self.policy.allowed_tools
        ]
        return AgentPlannerInput(
            goal="extract_requirements",
            profile=profile,
            policy=self.policy,
            allowed_tools=specs,
            latest_observation=state.latest_observation,
            history=history,
            budget=AgentBudgetState(
                steps_used=state.step_count,
                steps_remaining=self.policy.max_steps - state.step_count,
                planner_calls_used=state.planner_calls,
                planner_calls_remaining=(
                    self.policy.max_planner_calls - state.planner_calls
                ),
                pipeline_attempts_used=state.pipeline_attempts,
                pipeline_attempts_remaining=(
                    self.policy.max_pipeline_attempts - state.pipeline_attempts
                ),
            ),
        )

    def _finish(
        self,
        trace: AgentTraceWriter,
        state: AgentRunState,
        *,
        task_id: str,
        output: Path,
        run_generation: int,
        profile,
        decision: FinishDecision,
    ) -> AgentRunResult:
        pipeline_status = (
            str(state.latest_observation.metrics.get("pipeline_status"))
            if state.latest_observation is not None
            else None
        )
        trace.append(
            "finish",
            step=state.step_count,
            payload={"outcome": decision.outcome, "reason": decision.reason},
        )
        final_state = AgentFinalState(
            task_id=task_id,
            run_generation=run_generation,
            outcome=decision.outcome,
            steps_used=state.step_count,
            planner_calls=state.planner_calls,
            tool_invocations=state.tool_invocations,
            pipeline_attempts=state.pipeline_attempts,
            final_pipeline_status=pipeline_status,
            reason=decision.reason,
        )
        trace.publish_model("final_state.json", final_state)
        manifest_path = output / "run_manifest.json"
        if manifest_path.is_file():
            manifest = read_json(manifest_path)
        else:
            manifest = init_manifest(
                task_id=task_id,
                input_document=output.name,
                output_dir=output.as_posix(),
                model_mode=self.pipeline_config.model_mode,
                run_generation=run_generation,
            )
            if decision.outcome == "failed":
                manifest = fail_manifest(manifest, decision.reason)
            else:
                manifest = complete_manifest(
                    manifest,
                    counts={},
                    outputs={},
                    status=(
                        "completed_with_warnings"
                        if decision.outcome == "needs_human"
                        else decision.outcome
                    ),
                    warning_codes=(
                        ["AGENT_NEEDS_HUMAN"]
                        if decision.outcome == "needs_human"
                        else []
                    ),
                )
        if manifest.get("run_generation") != run_generation:
            raise AgentRunnerError("AGENT_MANIFEST_GENERATION_MISMATCH")
        if decision.outcome == "needs_human":
            manifest["status"] = "completed_with_warnings"
            manifest["warning_codes"] = list(
                dict.fromkeys(
                    [*manifest.get("warning_codes", []), "AGENT_NEEDS_HUMAN"]
                )
            )
        manifest["orchestration"] = {
            "mode": "agent",
            "planner_mode": self.planner.planner_mode,
            "planner_model": self.planner.request_profile.model_name,
            "planner_prompt_version": AGENT_PLANNER_PROMPT_VERSION,
            "policy_schema_version": self.policy.schema_version,
            "profile_schema_version": profile.schema_version,
            "steps_used": state.step_count,
            "planner_calls": state.planner_calls,
            "tool_invocations": state.tool_invocations,
            "pipeline_attempts": state.pipeline_attempts,
            "outcome": decision.outcome,
            "events": "agent/events",
            "trace": "agent/trace.jsonl",
            "final_state": "agent/final_state.json",
        }
        write_json(manifest_path, manifest)
        return AgentRunResult(
            task_id=task_id,
            output_dir=output,
            final_state=final_state,
            manifest_path=manifest_path,
            trace_path=output / "agent" / "trace.jsonl",
        )

    def _publish_failed_attempt(
        self,
        trace: AgentTraceWriter,
        output: Path,
        state: AgentRunState,
        decision: InvokeToolDecision,
        started_at: datetime,
        exc: Exception,
    ) -> None:
        manifest_path = output / "run_manifest.json"
        manifest = read_json(manifest_path) if manifest_path.is_file() else {}
        trace.publish_attempt(
            AgentAttemptSummary(
                run_generation=trace.run_generation,
                attempt=max(1, state.pipeline_attempts),
                arguments=decision.arguments,
                pipeline_status=str(manifest.get("status", "failed")),
                warning_codes=list(manifest.get("warning_codes", [])),
                counts={
                    key: value
                    for key, value in manifest.get("counts", {}).items()
                    if isinstance(value, int) and not isinstance(value, bool)
                },
                error_code=str(manifest.get("error_code") or type(exc).__name__),
                started_at=started_at,
                completed_at=_now_utc(),
            )
        )

    def _fail(
        self,
        trace: AgentTraceWriter,
        state: AgentRunState,
        *,
        task_id: str,
        run_generation: int,
        code: str,
        cause: Exception | None = None,
    ) -> None:
        safe_code = _safe_error_code(code)
        trace.append(
            "error",
            step=state.step_count,
            payload={"error_code": safe_code},
        )
        trace.publish_model(
            "final_state.json",
            AgentFinalState(
                task_id=task_id,
                run_generation=run_generation,
                outcome="failed",
                steps_used=state.step_count,
                planner_calls=state.planner_calls,
                tool_invocations=state.tool_invocations,
                pipeline_attempts=state.pipeline_attempts,
                final_pipeline_status=(
                    str(state.latest_observation.metrics.get("pipeline_status"))
                    if state.latest_observation is not None
                    else None
                ),
                reason=safe_code,
            ),
        )
        manifest_path = trace.root.parent / "run_manifest.json"
        if manifest_path.is_file():
            manifest = fail_manifest(read_json(manifest_path), safe_code)
            manifest["orchestration"] = {
                "mode": "agent",
                "planner_mode": self.planner.planner_mode,
                "planner_model": self.planner.request_profile.model_name,
                "planner_prompt_version": AGENT_PLANNER_PROMPT_VERSION,
                "policy_schema_version": self.policy.schema_version,
                "steps_used": state.step_count,
                "planner_calls": state.planner_calls,
                "tool_invocations": state.tool_invocations,
                "pipeline_attempts": state.pipeline_attempts,
                "outcome": "failed",
                "events": "agent/events",
                "trace": "agent/trace.jsonl",
                "final_state": "agent/final_state.json",
            }
            write_json(manifest_path, manifest)
        error = AgentRunnerError(safe_code)
        if cause is not None:
            raise error from cause
        raise error


def _attempt_counts(observation: PlannerObservation) -> dict[str, int]:
    return {
        key: value
        for key, value in observation.metrics.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }


def _safe_error_code(value: object) -> str:
    text = str(value)
    candidate = text.split(":", 1)[0].strip()
    if candidate and all(char.isupper() or char.isdigit() or char == "_" for char in candidate):
        return candidate[:128]
    if isinstance(value, Exception):
        return type(value).__name__.upper()[:128]
    return "AGENT_RUNNER_FAILED"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)
