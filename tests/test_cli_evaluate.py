from pathlib import Path

import pytest

from spectrail.cli import main
from spectrail.core.io import read_json


def test_evaluate_cli_generates_passing_report(tmp_path: Path):
    output = tmp_path / "evaluation"
    assert main(["evaluate", "eval/cases/sample_srs/case.json", "--output", str(output)]) == 0
    report = read_json(output / "evaluation_report.json")
    assert report["passed"] is True
    assert report["cases"][0]["requirement_exact_recall"] == 1.0
    assert report["cases"][0]["chunk_count"] == 1
    assert report["cases"][0]["model_call_count"] == 1
    assert report["cases"][0]["raw_candidates"] == 15
    case_markdown = (output / "cases" / "sample_srs" / "case_report.md").read_text(
        encoding="utf-8"
    )
    assert "## Counts and execution" in case_markdown
    assert "## Thresholds" in case_markdown


def test_selected_scope_evaluation_is_reported_explicitly(tmp_path: Path):
    case = tmp_path / "case.json"
    case.write_text(
        "{"
        '"name":"selected-scope","document":"docs/sample_srs.md",'
        '"gold":"eval/cases/sample_srs/gold.json","scope_block_ids":["blk_0006"],'
        '"thresholds":{"requirement_exact_recall_min":1.0}'
        "}",
        encoding="utf-8",
    )
    output = tmp_path / "selected-evaluation"
    assert main(["evaluate", str(case), "--output", str(output)]) == 0
    report = read_json(output / "evaluation_report.json")["cases"][0]
    assert report["annotation_scope"] == "selected_blocks"
    assert report["scope_block_ids"] == ["blk_0006"]
    assert report["full_gold_requirements"] == 15
    assert report["gold_requirements"] == 2
    assert report["validated_candidates_in_scope"] == 2
    assert report["requirement_exact_recall"] == 1.0


def test_selected_scope_rejects_unknown_parsed_block_before_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    case = tmp_path / "case.json"
    case.write_text(
        '{"name":"invalid-scope","document":"docs/sample_srs.md",'
        '"gold":"eval/cases/sample_srs/gold.json",'
        '"scope_block_ids":["blk_missing"]}',
        encoding="utf-8",
    )
    pipeline_called = False

    def fail_if_pipeline_called(*args, **kwargs):
        nonlocal pipeline_called
        pipeline_called = True
        raise AssertionError("pipeline must not run for an invalid scope")

    monkeypatch.setattr("spectrail.evaluation.runner.PipelineRunner.extract", fail_if_pipeline_called)

    with pytest.raises(SystemExit, match="scope_block_ids not found"):
        main(["evaluate", str(case), "--output", str(tmp_path / "invalid-scope-report")])
    assert pipeline_called is False


def test_selected_scope_rejects_empty_gold_before_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    case = tmp_path / "case.json"
    case.write_text(
        '{"name":"empty-gold-scope","document":"docs/sample_srs.md",'
        '"gold":"eval/cases/sample_srs/gold.json",'
        '"scope_block_ids":["blk_0001"]}',
        encoding="utf-8",
    )
    pipeline_called = False

    def fail_if_pipeline_called(*args, **kwargs):
        nonlocal pipeline_called
        pipeline_called = True
        raise AssertionError("pipeline must not run for an empty gold scope")

    monkeypatch.setattr("spectrail.evaluation.runner.PipelineRunner.extract", fail_if_pipeline_called)

    with pytest.raises(SystemExit, match="selected scope contains no gold requirements"):
        main(["evaluate", str(case), "--output", str(tmp_path / "empty-scope-report")])
    assert pipeline_called is False


def test_selected_scope_allows_intentional_empty_gold(tmp_path: Path):
    case = tmp_path / "case.json"
    case.write_text(
        '{"name":"allowed-empty-gold-scope","document":"docs/sample_srs.md",'
        '"gold":"eval/cases/sample_srs/gold.json",'
        '"scope_block_ids":["blk_0001"],"allow_empty_gold_scope":true}',
        encoding="utf-8",
    )
    output = tmp_path / "allowed-empty-scope-report"
    assert main(["evaluate", str(case), "--output", str(output)]) == 0
    report = read_json(output / "evaluation_report.json")["cases"][0]
    assert report["gold_requirements"] == 0
    assert report["validated_candidates_in_scope"] == 0
    assert report["requirement_exact_recall"] == 1.0


def test_gold_requirements_min_prevents_empty_scope_ci_pass(tmp_path: Path):
    case = tmp_path / "case.json"
    case.write_text(
        '{"name":"empty-gold-gate","document":"docs/sample_srs.md",'
        '"gold":"eval/cases/sample_srs/gold.json","scope_block_ids":["blk_0001"],'
        '"allow_empty_gold_scope":true,"thresholds":{"gold_requirements_min":1}}',
        encoding="utf-8",
    )
    output = tmp_path / "empty-scope-gate-report"
    assert main(["evaluate", str(case), "--output", str(output)]) == 1
    result = read_json(output / "evaluation_report.json")["cases"][0]["threshold_results"]
    assert result["gold_requirements_min"] == {
        "metric": "gold_requirements",
        "operator": ">=",
        "threshold": 1.0,
        "actual": 0,
        "passed": False,
    }


def test_evaluate_cli_returns_one_when_threshold_fails(tmp_path: Path):
    case = tmp_path / "case.json"
    case.write_text(
        '{"name":"failing-gate","document":"docs/sample_srs.md",'
        '"gold":"eval/cases/sample_srs/gold.json",'
        '"thresholds":{"requirement_exact_recall_min":1.1}}',
        encoding="utf-8",
    )
    assert main(["evaluate", str(case), "--output", str(tmp_path / "report")]) == 1


def test_evaluation_suite_reports_failed_pipeline_and_continues(tmp_path: Path):
    cases = tmp_path / "cases"
    failing = cases / "a_failing"
    passing = cases / "b_passing"
    failing.mkdir(parents=True)
    passing.mkdir(parents=True)
    invalid_response = failing / "response.json"
    invalid_response.write_text('{"payload":{"items":{"invalid":true}}}', encoding="utf-8")
    (failing / "case.json").write_text(
        '{"name":"failed-pipeline","document":"docs/sample_srs.md",'
        '"gold":"eval/cases/sample_srs/gold.json","model_mode":"recorded",'
        f'"recorded_fixture":"{invalid_response.as_posix()}"}}',
        encoding="utf-8",
    )
    (passing / "case.json").write_text(
        '{"name":"passing-pipeline","document":"docs/sample_srs.md",'
        '"gold":"eval/cases/sample_srs/gold.json",'
        '"thresholds":{"requirement_exact_recall_min":1.0}}',
        encoding="utf-8",
    )

    output = tmp_path / "suite-report"
    assert main(["evaluate", str(cases), "--output", str(output)]) == 1
    suite = read_json(output / "evaluation_report.json")
    assert suite["case_count"] == 2
    assert suite["case_passed"] == 1
    failed = suite["cases"][0]
    assert failed["pipeline_status"] == "failed"
    assert failed["error_code"] == "ModelPayloadContractError"
    assert failed["passed"] is False
    markdown = (output / "cases" / "a_failing" / "case_report.md").read_text(encoding="utf-8")
    assert "Pipeline status: failed" in markdown
    assert "Zero result reason: None" in markdown
