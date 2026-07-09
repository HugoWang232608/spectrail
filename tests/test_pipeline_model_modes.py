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
