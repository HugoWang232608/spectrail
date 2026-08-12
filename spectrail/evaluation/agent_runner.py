from __future__ import annotations

from json import JSONDecodeError
from pathlib import Path
import shutil
from typing import Any

from pydantic import ValidationError

from spectrail.agent import (
    AgentRunner,
    build_default_agent_policy,
    read_agent_trace_snapshot,
)
from spectrail.chunking import ChunkingConfig
from spectrail.core.io import read_json, write_json
from spectrail.evaluation.agent_models import AgentEvaluationCase
from spectrail.llm.recorded_agent_planner import RecordedAgentPlanner
from spectrail.llm.errors import ModelProviderError
from spectrail.pipeline import PipelineConfig, PipelineRunner


AGENT_EVALUATION_OUTPUT_MARKER = ".spectrail-agent-evaluation-output"
AGENT_EVALUATION_OUTPUT_MARKER_PAYLOAD = {
    "schema_version": "spectrail_agent_evaluation_output_v1",
    "managed_paths": [
        "cases",
        "agent_evaluation_report.json",
        "agent_evaluation_report.md",
    ],
}


class AgentEvaluationRunner:
    def run(
        self,
        case_path: str | Path,
        output_dir: str | Path,
    ) -> dict[str, Any]:
        path = Path(case_path)
        case_files = (
            sorted(path.rglob("agent_case.json"))
            if path.is_dir()
            else [path]
        )
        if not case_files:
            raise ValueError(f"no Agent evaluation cases found: {path}")
        relative_outputs = [
            _case_output_relative_path(path, case_file)
            for case_file in case_files
        ]
        if len(set(relative_outputs)) != len(relative_outputs):
            raise ValueError("AGENT_EVALUATION_CASE_OUTPUT_COLLISION")
        output = _prepare_output(Path(output_dir))
        reports = []
        for case_file, relative_output in zip(
            case_files,
            relative_outputs,
        ):
            reports.append(
                self._run_case(
                    case_file,
                    output / "cases" / relative_output,
                )
            )
        passed = sum(report["passed"] for report in reports)
        suite = {
            "schema_version": "agent_evaluation_report_v1",
            "case_count": len(reports),
            "case_passed": passed,
            "case_failed": len(reports) - passed,
            "passed": passed == len(reports),
            "cases": reports,
        }
        write_json(output / "agent_evaluation_report.json", suite)
        (output / "agent_evaluation_report.md").write_text(
            _suite_markdown(suite),
            encoding="utf-8",
        )
        return suite

    def _run_case(self, case_file: Path, output: Path) -> dict[str, Any]:
        try:
            case = AgentEvaluationCase.model_validate(read_json(case_file))
            document = _resolve_path(case.document, case_file.parent)
            planner_fixture = _resolve_path(
                case.planner_fixture,
                case_file.parent,
            )
            recorded_fixture = (
                _resolve_path(case.recorded_fixture, case_file.parent)
                if case.recorded_fixture is not None
                else None
            )
            if not document.is_file():
                raise ValueError(f"Agent evaluation document not found: {document}")
            if not planner_fixture.is_file():
                raise ValueError(
                    f"Agent planner fixture not found: {planner_fixture}"
                )
            config = PipelineConfig(
                model_mode=case.model_mode,
                recorded_fixture=recorded_fixture,
                chunking=ChunkingConfig(
                    mode=case.chunking_mode,
                    max_rendered_prompt_chars=case.max_rendered_prompt_chars,
                    overlap_blocks=case.overlap_blocks,
                    fail_fast=False,
                ),
                validation_policy=case.validation_policy,
                evidence_policy=case.evidence_policy,
            )
        except (
            OSError,
            UnicodeError,
            JSONDecodeError,
            TypeError,
            ValidationError,
            ValueError,
        ) as exc:
            return _configuration_failure(case_file, output, exc)

        run_error: Exception | None = None
        try:
            AgentRunner(
                planner=RecordedAgentPlanner(planner_fixture),
                policy=build_default_agent_policy(config),
                pipeline_config=config,
                pipeline_runner=_pipeline_runner_for(case.pipeline_scenario),
            ).run(document, output / "run", run_generation=1)
        except Exception as exc:
            run_error = exc

        try:
            snapshot = read_agent_trace_snapshot(
                output / "run" / "agent",
                task_id="run",
                run_generation=1,
            )
            manifest = read_json(output / "run" / "run_manifest.json")
        except Exception as exc:
            return _runtime_failure(case.name, output, run_error or exc)

        actual = {
            "outcome": snapshot.final_state.outcome,
            "manifest_status": manifest.get("status"),
            "final_pipeline_status": snapshot.final_state.final_pipeline_status,
            "steps_used": snapshot.final_state.steps_used,
            "planner_calls": snapshot.final_state.planner_calls,
            "tool_invocations": snapshot.final_state.tool_invocations,
            "pipeline_attempts": snapshot.final_state.pipeline_attempts,
            "tool_sequence": [
                event.tool
                for event in snapshot.events
                if event.event_type == "tool_started"
            ],
            "decision_actions": [
                event.payload.get("action")
                for event in snapshot.events
                if event.event_type == "decision"
            ],
            "attempt_statuses": [
                attempt.pipeline_status for attempt in snapshot.attempts
            ],
            "event_types": [event.event_type for event in snapshot.events],
            "warning_codes": manifest.get("warning_codes", []),
        }
        expected = case.expected.model_dump(mode="json")
        checks = {
            key: {
                "expected": expected_value,
                "actual": actual.get(key),
                "passed": actual.get(key) == expected_value,
            }
            for key, expected_value in expected.items()
        }
        report = {
            "schema_version": "agent_evaluation_case_report_v1",
            "name": case.name,
            "passed": run_error is None
            and all(check["passed"] for check in checks.values()),
            "run_error": str(run_error) if run_error is not None else None,
            "checks": checks,
        }
        _publish_case_report(output, report)
        return report


class _RecoverableFailureThenSuccessRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.delegate = PipelineRunner()

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
        self.calls += 1
        if self.calls > 1:
            return self.delegate.extract_within_transaction(
                document_path,
                output_dir,
                run_generation=run_generation,
                config=config,
                parsed_document=parsed_document,
                **kwargs,
            )
        output = Path(output_dir)
        write_json(
            output / "run_manifest.json",
            {
                "task_id": output.name,
                "run_generation": run_generation,
                "status": "failed",
                "warning_codes": [],
                "counts": {
                    "chunks": 1,
                    "chunks_failed": 1,
                    "validated_requirements": 0,
                    "quarantined_requirements": 0,
                    "model_items_rejected": 0,
                },
                "zero_result_reason": None,
                "error_code": "ModelProviderError",
            },
        )
        raise ModelProviderError("synthetic recoverable evaluation failure")


def _pipeline_runner_for(scenario: str):
    if scenario == "recoverable_failure_then_success":
        return _RecoverableFailureThenSuccessRunner()
    return PipelineRunner()


def _prepare_output(output_dir: Path) -> Path:
    output = output_dir.resolve(strict=False)
    if output.exists() and not output.is_dir():
        raise ValueError("AGENT_EVALUATION_OUTPUT_NOT_DIRECTORY")
    marker = output / AGENT_EVALUATION_OUTPUT_MARKER
    if output.exists():
        entries = list(output.iterdir())
        if entries and not _valid_marker(marker):
            raise ValueError("AGENT_EVALUATION_OUTPUT_NOT_OWNED")
    else:
        output.mkdir(parents=True)
    if not _valid_marker(marker):
        write_json(marker, AGENT_EVALUATION_OUTPUT_MARKER_PAYLOAD)
    cases = output / "cases"
    if cases.is_symlink() or (cases.exists() and not cases.is_dir()):
        raise ValueError("AGENT_EVALUATION_CASES_OUTPUT_INVALID")
    if cases.exists():
        shutil.rmtree(cases)
    cases.mkdir()
    for name in ("agent_evaluation_report.json", "agent_evaluation_report.md"):
        (output / name).unlink(missing_ok=True)
    return output


def _valid_marker(marker: Path) -> bool:
    if marker.is_symlink() or not marker.is_file():
        return False
    try:
        return read_json(marker) == AGENT_EVALUATION_OUTPUT_MARKER_PAYLOAD
    except (OSError, UnicodeError, JSONDecodeError):
        return False


def _case_output_relative_path(case_path: Path, case_file: Path) -> Path:
    if case_path.is_dir():
        relative = case_file.parent.relative_to(case_path)
        return Path("_root") if relative == Path(".") else relative
    return Path(case_file.parent.name or "_root")


def _resolve_path(value: str | Path, case_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return case_dir / path


def _configuration_failure(
    case_file: Path,
    output: Path,
    error: Exception,
) -> dict[str, Any]:
    return _runtime_failure(case_file.parent.name, output, error)


def _runtime_failure(
    name: str,
    output: Path,
    error: Exception,
) -> dict[str, Any]:
    report = {
        "schema_version": "agent_evaluation_case_report_v1",
        "name": name,
        "passed": False,
        "run_error": str(error),
        "checks": {},
    }
    _publish_case_report(output, report)
    return report


def _publish_case_report(output: Path, report: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "case_report.json", report)
    (output / "case_report.md").write_text(
        _case_markdown(report),
        encoding="utf-8",
    )


def _suite_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# SpecTrail Agent Evaluation",
        "",
        f"Passed: {report['case_passed']}/{report['case_count']}",
        "",
    ]
    for case in report["cases"]:
        lines.append(
            f"- {'PASS' if case['passed'] else 'FAIL'} — {case['name']}"
        )
    return "\n".join(lines) + "\n"


def _case_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['name']}",
        "",
        f"Status: {'PASS' if report['passed'] else 'FAIL'}",
        "",
    ]
    if report.get("run_error"):
        lines.extend([f"Error: {report['run_error']}", ""])
    for name, check in report.get("checks", {}).items():
        lines.append(
            f"- {'PASS' if check['passed'] else 'FAIL'} {name}: "
            f"expected={check['expected']!r} actual={check['actual']!r}"
        )
    return "\n".join(lines) + "\n"
