import json
from pathlib import Path

import pytest

from spectrail.cli import main
from spectrail.core.io import read_json
from spectrail.evaluation.runner import PipelineRunner
from spectrail.parsers import parse_document


def test_evaluate_cli_generates_passing_report(tmp_path: Path):
    output = tmp_path / "evaluation"
    assert main(["evaluate", "eval/cases/sample_srs/case.json", "--output", str(output)]) == 0
    report = read_json(output / "evaluation_report.json")
    assert report["passed"] is True
    assert report["cases"][0]["requirement_exact_recall"] == 1.0
    assert report["cases"][0]["chunk_count"] == 1
    assert report["cases"][0]["model_call_count"] == 1
    assert report["cases"][0]["raw_candidates"] == 15
    assert report["cases"][0]["evidence_index_available"] is True
    assert report["cases"][0]["text_locator_pass_rate"] == 1.0
    case_markdown = (output / "cases" / "sample_srs" / "case_report.md").read_text(
        encoding="utf-8"
    )
    assert "## Counts and execution" in case_markdown
    assert "## Thresholds" in case_markdown
    assert "Structured grounding coverage" in case_markdown


def test_selected_scope_evaluation_is_reported_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    case = tmp_path / "case.json"
    case.write_text(
        "{"
        '"name":"selected-scope","document":"docs/sample_srs.md",'
        '"gold":"eval/cases/sample_srs/gold.json","scope_block_ids":["blk_0006"],'
        '"thresholds":{"requirement_exact_recall_min":1.0}'
        "}",
        encoding="utf-8",
    )

    def fail_if_document_is_parsed_again(*args, **kwargs):
        raise AssertionError("selected-scope pipeline must reuse the preflight parse")

    monkeypatch.setattr("spectrail.pipeline.runner.parse_document", fail_if_document_is_parsed_again)

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


def test_locator_metrics_participate_in_threshold_gate(tmp_path: Path):
    case = tmp_path / "case.json"
    case.write_text(
        '{"name":"locator-gate","document":"docs/sample_srs.md",'
        '"gold":"eval/cases/sample_srs/gold.json",'
        '"thresholds":{"text_locator_pass_rate_min":1.0,'
        '"text_locator_evaluated_count_min":1}}',
        encoding="utf-8",
    )
    output = tmp_path / "locator-gate-report"
    assert main(["evaluate", str(case), "--output", str(output)]) == 0
    results = read_json(output / "evaluation_report.json")["cases"][0][
        "threshold_results"
    ]
    assert results["text_locator_pass_rate_min"]["actual"] == 1.0
    assert results["text_locator_pass_rate_min"]["passed"] is True
    assert results["text_locator_evaluated_count_min"]["actual"] > 0


def test_ieee_pdf_release_gate_evaluates_real_page_region(tmp_path: Path):
    output = tmp_path / "ieee-page-region-gate"

    assert main(
        [
            "evaluate",
            "eval/cases/ieee29148_selected/case.json",
            "--output",
            str(output),
        ]
    ) == 0
    report = read_json(output / "evaluation_report.json")["cases"][0]
    assert report["page_accuracy"] == 1.0
    assert report["page_evaluated_count"] == 1
    assert report["bbox_iou_mean"] == pytest.approx(0.8794261336524364)
    assert report["bbox_iou_pass_rate"] == 1.0
    assert report["bbox_evaluated_count"] == 1
    assert report["structured_grounding_coverage"] == 1.0
    assert report["structured_grounding_eligible_count"] == 1
    assert report["text_locator_pass_rate"] == 1.0
    assert report["text_locator_evaluated_count"] == 1
    assert all(
        result["passed"] for result in report["threshold_results"].values()
    )


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


def test_selected_scope_parse_failure_is_reported_and_suite_continues(tmp_path: Path):
    cases = tmp_path / "cases"
    failing = cases / "a_parse_failure"
    passing = cases / "b_passing"
    failing.mkdir(parents=True)
    passing.mkdir(parents=True)
    invalid_document = failing / "invalid.docx"
    invalid_document.write_text("not a docx package", encoding="utf-8")
    (failing / "case.json").write_text(
        '{"name":"selected-scope-parse-failure",'
        f'"document":"{invalid_document.as_posix()}",'
        '"gold":"eval/cases/sample_srs/gold.json",'
        '"scope_block_ids":["blk_0001"]}',
        encoding="utf-8",
    )
    (passing / "case.json").write_text(
        '{"name":"passing-after-parse-failure","document":"docs/sample_srs.md",'
        '"gold":"eval/cases/sample_srs/gold.json",'
        '"thresholds":{"requirement_exact_recall_min":1.0}}',
        encoding="utf-8",
    )

    output = tmp_path / "parse-failure-suite-report"
    assert main(["evaluate", str(cases), "--output", str(output)]) == 1
    suite = read_json(output / "evaluation_report.json")
    assert suite["case_count"] == 2
    assert suite["case_passed"] == 1
    failed = suite["cases"][0]
    assert failed["name"] == "selected-scope-parse-failure"
    assert failed["pipeline_status"] == "failed"
    assert failed["error_code"] == "DocumentParseError"
    assert failed["passed"] is False
    assert suite["cases"][1]["passed"] is True
    assert (output / "cases" / "a_parse_failure" / "case_report.json").exists()


def test_evaluation_fixture_identity_mismatch_is_reported_and_suite_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    cases = tmp_path / "cases"
    stale = cases / "a_stale"
    passing = cases / "b_passing"
    stale.mkdir(parents=True)
    passing.mkdir(parents=True)
    (stale / "case.json").write_text(
        json.dumps(
            {
                "name": "stale-parser-fixture",
                "document": "docs/sample_srs.md",
                "gold": "eval/cases/sample_srs/gold.json",
                "expected_parser_name": "pdf_parser_v2",
            }
        ),
        encoding="utf-8",
    )
    (passing / "case.json").write_text(
        '{"name":"passing-after-stale","document":"docs/sample_srs.md",'
        '"gold":"eval/cases/sample_srs/gold.json"}',
        encoding="utf-8",
    )

    original_extract = PipelineRunner.extract
    calls = 0

    def count_pipeline_calls(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return original_extract(self, *args, **kwargs)

    monkeypatch.setattr(
        "spectrail.evaluation.runner.PipelineRunner.extract",
        count_pipeline_calls,
    )
    output = tmp_path / "stale-suite-report"

    assert main(["evaluate", str(cases), "--output", str(output)]) == 1
    suite = read_json(output / "evaluation_report.json")
    assert suite["case_count"] == 2
    assert suite["case_passed"] == 1
    assert suite["cases"][0]["error_code"] == "EVALUATION_FIXTURE_STALE"
    assert "parser_name" in suite["cases"][0]["error"]
    assert suite["cases"][1]["passed"] is True
    assert calls == 1
    markdown = (output / "cases" / "a_stale" / "case_report.md").read_text(
        encoding="utf-8"
    )
    assert "EVALUATION_FIXTURE_STALE" in markdown
    assert "parser_name" in markdown


def test_recorded_fixture_fingerprint_is_bound_to_evaluation_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    document = Path("tests/fixtures/ieee29148_srs_example.pdf")
    parsed = parse_document(document)
    assert parsed.parser_identity is not None
    assert parsed.evidence_index is not None
    fixture = tmp_path / "response.json"
    fixture.write_text(
        json.dumps(
            {
                "metadata": {"evidence_fingerprint": "0" * 64},
                "items": [],
            }
        ),
        encoding="utf-8",
    )
    case = tmp_path / "case.json"
    case.write_text(
        json.dumps(
            {
                "name": "stale-recorded-bundle",
                "document": document.as_posix(),
                "gold": "eval/cases/ieee29148_selected/gold.json",
                "model_mode": "recorded",
                "recorded_fixture": fixture.as_posix(),
                "expected_parser_name": parsed.parser_identity.parser_name,
                "expected_parser_version": parsed.parser_identity.parser_version,
                "expected_evidence_fingerprint": (
                    parsed.evidence_index.evidence_fingerprint
                ),
            }
        ),
        encoding="utf-8",
    )

    def fail_if_pipeline_called(*args, **kwargs):
        raise AssertionError("stale recorded fixture must fail before pipeline")

    monkeypatch.setattr(
        "spectrail.evaluation.runner.PipelineRunner.extract",
        fail_if_pipeline_called,
    )
    output = tmp_path / "stale-recorded-report"

    assert main(["evaluate", str(case), "--output", str(output)]) == 1
    report = read_json(output / "evaluation_report.json")["cases"][0]
    assert report["error_code"] == "EVALUATION_FIXTURE_STALE"
    assert "recorded_fixture.evidence_fingerprint" in report["error"]
