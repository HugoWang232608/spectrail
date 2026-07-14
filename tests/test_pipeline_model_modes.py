from pathlib import Path

from spectrail.core.io import read_json
from spectrail.pipeline import PipelineRunner, PipelineValidationError


def test_pipeline_runner_extract_recorded_generates_outputs(tmp_path: Path):
    result = PipelineRunner().extract(
        "docs/sample_srs.md",
        tmp_path / "demo_recorded",
        model_mode="recorded",
        recorded_fixture="fixtures/recorded/sample_srs_reqir_response.json",
        dump_prompt=True,
    )

    assert result.validated_count == 2
    assert result.exported_reqir_path.exists()
    assert (result.output_dir / "extracted" / "prompt.txt").exists()
    assert (result.output_dir / "extracted" / "model_response.json").exists()
    assert (result.output_dir / "extracted" / "model_response.raw.txt").exists()
    raw = read_json(result.output_dir / "extracted" / "reqir.raw.json")
    assert raw["metadata"]["model_mode"] == "recorded"
    assert raw["metadata"]["model_name"] == "recorded-sample-fixture"
    assert raw["metadata"]["prompt_version"] == "reqir_extraction_v8_row_group_evidence_v4"


def test_pipeline_runner_extract_recorded_full_fixture_covers_enum_drift(tmp_path: Path):
    result = PipelineRunner().extract(
        "docs/sample_srs.md",
        tmp_path / "demo_recorded_full",
        model_mode="recorded",
        recorded_fixture="fixtures/recorded/sample_srs_reqir_response_full.json",
    )

    assert result.validated_count == 8
    reqir = read_json(result.output_dir / "extracted" / "reqir.validated.json")
    types = {item["type"] for item in reqir["items"]}
    patterns = {item["ears_pattern"] for item in reqir["items"]}
    assert {"functional", "non_functional", "constraint", "unknown"}.issubset(types)
    assert {"event_driven", "ubiquitous", "state_driven", "optional", "unwanted_behavior"}.issubset(patterns)
    assert any(item["tags"] == [] for item in reqir["items"])
    assert any(item["type"] == "unknown" and item["review_status"] == "needs_recheck" for item in reqir["items"])
    assert any(item["ears_pattern"] == "unwanted_behavior" and item["review_status"] == "pending" for item in reqir["items"])

    report = read_json(result.output_dir / "extracted" / "validation_report.json")
    assert any(issue["code"] == "MODEL_FIELD_NORMALIZED" for issue in report["issues"])


def test_pipeline_runner_replays_sanitized_live_output_with_textual_confidence(tmp_path: Path):
    result = PipelineRunner().extract(
        "docs/sample_srs.md",
        tmp_path / "demo_recorded_live_output",
        model_mode="recorded",
        recorded_fixture="fixtures/recorded/sample_srs_live_deepseek_response.json",
    )

    assert result.validated_count == 15
    manifest = read_json(result.manifest_path)
    assert manifest["status"] == "completed"
    assert manifest["counts"]["source_quote_failed"] == 0
    assert manifest["model"]["name"] == "DeepSeek-V4-Flash"

    reqir = read_json(result.output_dir / "extracted" / "reqir.validated.json")
    assert {item["confidence"] for item in reqir["items"]} == {0.9}

    report = read_json(result.output_dir / "extracted" / "validation_report.json")
    assert report["valid"] is True
    assert sum(
        issue["code"] == "MODEL_FIELD_NORMALIZED"
        and issue["metadata"]["field"] == "confidence"
        and issue["metadata"]["input"] == "high"
        for issue in report["issues"]
    ) == 15
    assert any(
        issue["code"] == "MODEL_FIELD_NORMALIZED"
        and issue["metadata"]["field"] == "source_quote"
        for issue in report["issues"]
    )


def test_pipeline_runner_writes_model_parser_metadata_to_manifest_and_plan(tmp_path: Path):
    result = PipelineRunner().extract(
        "docs/sample_srs.md",
        tmp_path / "demo_manifest",
        model_mode="recorded",
        recorded_fixture="fixtures/recorded/sample_srs_reqir_response.json",
    )

    manifest = read_json(result.manifest_path)
    assert manifest["model"] == {
        "mode": "recorded",
        "name": "recorded-sample-fixture",
        "prompt_version": "reqir_extraction_v8_row_group_evidence_v4",
        "recorded_fixture": "fixtures/recorded/sample_srs_reqir_response.json",
    }
    assert manifest["parser"] == {
        "source_format": "markdown",
        "parser_name": "markdown_parser_v1",
        "warnings": [],
    }

    plan = read_json(result.plan_path)
    parse_step = plan["steps"][0]
    assert parse_step["tool"] == "document_parser_registry"
    assert parse_step["config"]["selected_parser"] == "markdown_parser_v1"


def test_pipeline_runner_writes_model_response_json_before_extractor_failure(tmp_path: Path):
    fixture = tmp_path / "bad_recorded.json"
    fixture.write_text(
        '{"metadata":{"model_name":"bad-fixture"},"raw_text":"```json\\n{\\"items\\":[{\\"statement\\":\\"missing source\\"}]}\\n```"}',
        encoding="utf-8",
    )

    try:
        PipelineRunner().extract(
            "docs/sample_srs.md",
            tmp_path / "bad_output",
            model_mode="recorded",
            recorded_fixture=fixture,
        )
    except PipelineValidationError:
        pass
    else:
        raise AssertionError("expected extractor validation to fail")

    assert read_json(tmp_path / "bad_output" / "extracted" / "model_response.json") == {
        "items": [{"statement": "missing source"}]
    }
    report = read_json(tmp_path / "bad_output" / "extracted" / "validation_report.json")
    assert report["issues"][0]["code"] == "MODEL_OUTPUT_VALIDATION_FAILED"


def test_pipeline_runner_reports_source_quote_normalization(tmp_path: Path):
    fixture = tmp_path / "quote_recorded.json"
    fixture.write_text(
        (
            '{"metadata":{"model_name":"quote-fixture"},"raw_text":"```json\\n'
            '{\\"items\\":[{'
            '\\"statement\\":\\"管理员应能够创建、停用和恢复普通用户账号。\\",'
            '\\"source_block_id\\":\\"blk_0006\\",'
            '\\"source_quote\\":\\"- 管理员应能够创建、停用和恢复普通用户账号。\\",'
            '\\"confidence\\":0.9'
            '}]}\\n```"}'
        ),
        encoding="utf-8",
    )

    result = PipelineRunner().extract(
        "docs/sample_srs.md",
        tmp_path / "quote_output",
        model_mode="recorded",
        recorded_fixture=fixture,
    )

    report = read_json(result.output_dir / "extracted" / "validation_report.json")
    assert any(
        issue["code"] == "MODEL_FIELD_NORMALIZED"
        and issue["metadata"]["field"] == "source_quote"
        and issue["metadata"]["input"].startswith("- ")
        for issue in report["issues"]
    )
