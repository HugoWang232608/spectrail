from pathlib import Path

import pytest

from spectrail.core.io import read_json
from spectrail.llm.errors import ModelConfigurationError
from spectrail.parsers import parse_document
from spectrail.pipeline import PipelineResult, PipelineRunner, UnsupportedModelModeError


def test_pipeline_runner_extract_generates_outputs(tmp_path: Path):
    output = tmp_path / "demo"
    result = PipelineRunner().extract("docs/sample_srs.md", output)

    assert isinstance(result, PipelineResult)
    assert result.output_dir == output
    assert result.validated_count >= 14
    assert result.plan_path.exists()
    assert result.manifest_path.exists()
    assert result.validated_reqir_path.exists()
    assert result.exported_reqir_path.exists()
    assert result.xlsx_path.exists()

    manifest = read_json(result.manifest_path)
    assert manifest["status"] == "completed"
    assert manifest["counts"]["validated_requirements"] == result.validated_count


def test_pipeline_runner_reuses_preparsed_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    document = Path("docs/sample_srs.md")
    parsed_document = parse_document(document, document_id="doc_001")

    def fail_if_document_is_parsed_again(*args, **kwargs):
        raise AssertionError("preparsed document must be reused")

    monkeypatch.setattr("spectrail.pipeline.runner.parse_document", fail_if_document_is_parsed_again)

    result = PipelineRunner().extract(
        document,
        tmp_path / "preparsed",
        parsed_document=parsed_document,
    )
    assert result.status == "completed"


def test_pipeline_runner_failure_marks_manifest_failed(tmp_path: Path):
    output = tmp_path / "demo"

    with pytest.raises(FileNotFoundError):
        PipelineRunner().extract(tmp_path / "missing.md", output)

    manifest = read_json(output / "run_manifest.json")
    assert manifest["status"] == "failed"
    assert manifest["error"]


def test_pipeline_runner_rejects_unsupported_model_mode(tmp_path: Path):
    with pytest.raises(UnsupportedModelModeError):
        PipelineRunner().extract("docs/sample_srs.md", tmp_path / "demo", model_mode="unknown")


def test_pipeline_runner_live_requires_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    document = Path("docs/sample_srs.md").resolve()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPECTRAIL_LLM_API_KEY", raising=False)
    monkeypatch.setenv("SPECTRAIL_LLM_MODEL", "test-model")

    with pytest.raises(ModelConfigurationError):
        PipelineRunner().extract(document, tmp_path / "demo", model_mode="live")
