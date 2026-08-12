from pathlib import Path

import pytest

from spectrail.cli import main
from spectrail.core.io import read_json, write_json
from spectrail.evaluation.agent_runner import AgentEvaluationRunner


def test_agent_evaluation_gate_passes_frozen_suite(tmp_path: Path):
    output = tmp_path / "agent-evaluation"

    report = AgentEvaluationRunner().run("eval/agent/cases", output)

    assert report["schema_version"] == "agent_evaluation_report_v1"
    assert report["case_count"] == 3
    assert report["case_passed"] == 3
    assert report["passed"] is True
    completed = next(
        case
        for case in report["cases"]
        if case["name"] == "sample_srs_agent_completed"
    )
    assert completed["checks"]["event_types"]["passed"] is True
    assert completed["checks"]["attempt_statuses"]["actual"] == [
        "completed"
    ]
    assert (output / "agent_evaluation_report.md").is_file()
    retry = next(
        case
        for case in report["cases"]
        if case["name"] == "sample_srs_agent_failure_retry"
    )
    assert retry["checks"]["attempt_statuses"]["actual"] == [
        "failed",
        "completed",
    ]
    assert retry["checks"]["attempt_error_codes"]["actual"] == [
        "ModelProviderError",
        None,
    ]
    assert main(
        [
            "evaluate-agent",
            "eval/agent/cases",
            "--output",
            str(output),
        ]
    ) == 0


def test_agent_evaluation_gate_fails_exact_expectation(tmp_path: Path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    payload = read_json(
        "eval/agent/cases/sample_srs_completed/agent_case.json"
    )
    payload["document"] = Path("docs/sample_srs.md").resolve().as_posix()
    payload["planner_fixture"] = Path(
        "spectrail/fixtures/agent/sample_srs_agent_full.json"
    ).resolve().as_posix()
    payload["expected"]["steps_used"] = 99
    write_json(case_dir / "agent_case.json", payload)

    report = AgentEvaluationRunner().run(
        case_dir,
        tmp_path / "output",
    )

    assert report["passed"] is False
    assert report["case_failed"] == 1
    check = report["cases"][0]["checks"]["steps_used"]
    assert check == {"expected": 99, "actual": 2, "passed": False}


def test_agent_evaluation_rejects_unowned_output(tmp_path: Path):
    output = tmp_path / "not-owned"
    output.mkdir()
    (output / "sentinel.txt").write_text("preserve", encoding="utf-8")

    with pytest.raises(ValueError, match="OUTPUT_NOT_OWNED"):
        AgentEvaluationRunner().run("eval/agent/cases", output)

    assert (output / "sentinel.txt").read_text(encoding="utf-8") == "preserve"


def test_agent_evaluation_invalid_case_is_reported_not_crashed(
    tmp_path: Path,
):
    case_dir = tmp_path / "invalid"
    case_dir.mkdir()
    write_json(
        case_dir / "agent_case.json",
        {
            "schema_version": "agent_evaluation_case_v1",
            "name": "invalid",
            "document": "missing.md",
            "planner_fixture": "missing.json",
            "expected": {
                "outcome": "completed",
                "manifest_status": "completed",
                "steps_used": 1,
                "planner_calls": 1,
                "tool_invocations": 1,
                "pipeline_attempts": 0
            },
        },
    )

    report = AgentEvaluationRunner().run(case_dir, tmp_path / "output")

    assert report["passed"] is False
    assert report["cases"][0]["checks"] == {}
    assert "document not found" in report["cases"][0]["run_error"]
