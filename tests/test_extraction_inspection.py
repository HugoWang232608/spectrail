from __future__ import annotations

from pathlib import Path

from spectrail.core.io import write_json
from spectrail.tools.extraction_inspection import InspectExtractionResultTool


def test_inspection_adds_diagnostic_facts_not_present_in_run_summary(tmp_path: Path):
    task_dir = tmp_path / "task"
    write_json(
        task_dir / "run_manifest.json",
        {
            "status": "completed_with_warnings",
            "run_generation": 3,
            "warning_codes": ["PARTIAL_CHUNK_FAILURE"],
            "counts": {
                "chunks": 3,
                "chunks_completed": 2,
                "chunks_failed": 1,
                "model_items_accepted": 4,
                "model_items_rejected": 2,
                "validated_requirements": 3,
                "quarantined_requirements": 1,
                "source_quote_failed": 1,
                "source_locator_failed": 0,
            },
            "zero_result_reason": None,
        },
    )
    write_json(
        task_dir / "parsed" / "chunks.json",
        [
            {"warnings": []},
            {"warnings": ["CHUNK_PROMPT_OVER_BUDGET"]},
            {"warnings": []},
        ],
    )

    result = InspectExtractionResultTool().inspect_task_dir(
        task_dir,
        expected_run_generation=3,
    )

    assert result.status == "warning"
    assert result.metrics["partial_chunk_failure"] is True
    assert result.metrics["chunk_prompt_over_budget"] is True
    assert result.metrics["model_items_rejected"] == 2
    assert result.metrics["source_quote_failed"] == 1
    assert "CHUNK_PROMPT_OVER_BUDGET" in result.warning_codes
    assert "PARTIAL_CHUNK_FAILURE" in result.warning_codes


def test_inspection_rejects_mixed_generation(tmp_path: Path):
    task_dir = tmp_path / "task"
    write_json(
        task_dir / "run_manifest.json",
        {"run_generation": 2, "status": "failed", "counts": {}},
    )

    try:
        InspectExtractionResultTool().inspect_task_dir(
            task_dir,
            expected_run_generation=3,
        )
    except ValueError as exc:
        assert str(exc) == "AGENT_INSPECTION_GENERATION_MISMATCH"
    else:
        raise AssertionError("expected mixed generation to fail")
