from pathlib import Path

from spectrail.core.io import read_json
from spectrail.pipeline import PipelineRunner


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
    assert (result.output_dir / "extracted" / "model_response.raw.txt").exists()
    raw = read_json(result.output_dir / "extracted" / "reqir.raw.json")
    assert raw["metadata"]["model_mode"] == "recorded"
    assert raw["metadata"]["model_name"] == "recorded-sample-fixture"
    assert raw["metadata"]["prompt_version"] == "reqir_extraction_v1"


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

    report = read_json(result.output_dir / "extracted" / "validation_report.json")
    assert any(issue["code"] == "MODEL_ENUM_NORMALIZED" for issue in report["issues"])


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
        "prompt_version": "reqir_extraction_v1",
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
