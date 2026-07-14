from pathlib import Path

import pytest

from spectrail.cli import main
from spectrail.core.io import read_json


def test_extract_writes_plan_and_completed_manifest(tmp_path: Path):
    output = tmp_path / "demo"
    assert main(["extract", "docs/sample_srs.md", "--model-mode", "mock", "--output", str(output)]) == 0

    plan = read_json(output / "plan.json")
    assert plan["planner"] == "fixed_workflow_v1"
    assert [step["id"] for step in plan["steps"]] == [
        "parse",
        "extract",
        "normalize_ears",
        "validate_schema",
        "validate_source_quote",
        "validate_source_locator",
        "init_review",
        "export_json",
        "export_xlsx",
    ]
    assert plan["steps"][0]["tool"] == "document_parser_registry"
    assert plan["steps"][0]["config"]["selected_parser"] == "markdown_parser_v1"

    manifest = read_json(output / "run_manifest.json")
    requirements = read_json(output / "extracted" / "reqir.validated.json")["items"]
    assert manifest["status"] == "completed"
    assert manifest["counts"]["validated_requirements"] == len(requirements)
    assert manifest["counts"]["validated_requirements"] >= 14
    assert manifest["outputs"]["reqir_export"] == "exports/reqir.json"
    assert manifest["model"]["mode"] == "mock"
    assert manifest["model"]["prompt_version"] == "reqir_extraction_v7_row_group_evidence_v3"
    assert manifest["parser"]["parser_name"] == "markdown_parser_v1"
    assert manifest["evidence"]["schema_version"] == "evidence_v3"

    raw = read_json(output / "extracted" / "reqir.raw.json")
    validated = read_json(output / "extracted" / "reqir.validated.json")
    exported = read_json(output / "exports" / "reqir.json")
    assert raw["schema_version"] == "reqir_v2"
    assert validated["schema_version"] == "reqir_v2"
    assert exported["schema_version"] == "reqir_v2"
    assert raw["items"][0]["metadata"]["extractor_version"] == (
        "reqir_extractor_v3_evidence"
    )


def test_extract_failure_marks_manifest_failed(tmp_path: Path):
    output = tmp_path / "demo"

    with pytest.raises(FileNotFoundError):
        main(["extract", str(tmp_path / "missing.md"), "--model-mode", "mock", "--output", str(output)])

    manifest = read_json(output / "run_manifest.json")
    assert manifest["status"] == "failed"
    assert manifest["error"]
