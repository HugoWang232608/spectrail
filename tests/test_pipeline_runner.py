from pathlib import Path

import pytest

from spectrail.core.io import read_json
from spectrail.pipeline import PipelineResult, PipelineRunner


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


def test_pipeline_runner_failure_marks_manifest_failed(tmp_path: Path):
    output = tmp_path / "demo"

    with pytest.raises(FileNotFoundError):
        PipelineRunner().extract(tmp_path / "missing.md", output)

    manifest = read_json(output / "run_manifest.json")
    assert manifest["status"] == "failed"
    assert manifest["error"]
