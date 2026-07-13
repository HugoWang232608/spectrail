from pathlib import Path

import pytest

from spectrail.core.io import read_json
from spectrail.llm.base import ModelResponse
from spectrail.pipeline import PipelineRunner, PipelineValidationError


def test_forced_chunked_mock_pipeline_preserves_final_requirements(tmp_path: Path):
    result = PipelineRunner().extract(
        "docs/sample_srs.md",
        tmp_path / "chunked",
        model_mode="mock",
        chunking_mode="force",
        max_rendered_prompt_chars=1600,
    )
    manifest = read_json(result.manifest_path)
    chunks = read_json(result.output_dir / "parsed" / "chunks.json")
    assert manifest["status"] == "completed"
    assert manifest["counts"]["chunks"] >= 3
    assert manifest["counts"]["validated_requirements"] == 15
    assert manifest["counts"]["collapsed_overlap_duplicates"] > 0
    assert all(chunk["new_block_ids"] for chunk in chunks)
    assert all(chunk["rendered_prompt_chars"] <= 1600 for chunk in chunks)


def test_quarantine_policy_exports_only_grounded_candidates(tmp_path: Path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        '{"items":['
        '{"statement":"Valid","source_block_id":"blk_0006","source_quote":"管理员应能够创建、停用和恢复普通用户账号。"},'
        '{"statement":"Invalid","source_block_id":"blk_0006","source_quote":"not present"}'
        ']}' ,
        encoding="utf-8",
    )
    result = PipelineRunner().extract(
        "docs/sample_srs.md",
        tmp_path / "quarantine",
        model_mode="recorded",
        recorded_fixture=fixture,
        validation_policy="quarantine",
    )
    manifest = read_json(result.manifest_path)
    assert manifest["status"] == "completed_with_warnings"
    assert manifest["counts"]["validated_requirements"] == 1
    assert manifest["counts"]["quarantined_requirements"] == 1
    assert len(read_json(result.output_dir / "exports" / "reqir.json")["items"]) == 1
    assert len(read_json(result.output_dir / "extracted" / "reqir.quarantined.json")["items"]) == 1

    with pytest.raises(PipelineValidationError, match="source quote validation failed"):
        PipelineRunner().extract(
            "docs/sample_srs.md",
            tmp_path / "strict",
            model_mode="recorded",
            recorded_fixture=fixture,
            validation_policy="strict",
        )


def test_valid_empty_response_is_completed_with_warning(tmp_path: Path):
    fixture = tmp_path / "empty.json"
    fixture.write_text('{"items":[]}', encoding="utf-8")
    result = PipelineRunner().extract(
        "docs/sample_srs.md",
        tmp_path / "empty",
        model_mode="recorded",
        recorded_fixture=fixture,
    )
    manifest = read_json(result.manifest_path)
    assert manifest["status"] == "completed_with_warnings"
    assert manifest["zero_result_reason"] == "NO_REQUIREMENTS_FOUND"
    assert read_json(result.exported_reqir_path)["items"] == []


def test_pipeline_isolates_malformed_item_and_exports_valid_siblings(tmp_path: Path):
    fixture = tmp_path / "mixed.json"
    fixture.write_text(
        '{"items":['
        '{"statement":"First","source_block_id":"blk_0006","source_quote":"管理员应能够创建、停用和恢复普通用户账号。"},'
        '{"statement":"Malformed"},'
        '{"statement":"Third","source_block_id":"blk_0008","source_quote":"用户输入正确账号密码后，系统应完成身份验证并建立会话。"}'
        "]}",
        encoding="utf-8",
    )
    result = PipelineRunner().extract(
        "docs/sample_srs.md", tmp_path / "mixed", model_mode="recorded", recorded_fixture=fixture
    )
    manifest = read_json(result.manifest_path)
    assert manifest["status"] == "completed_with_warnings"
    assert manifest["counts"]["model_items_accepted"] == 2
    assert manifest["counts"]["model_items_rejected"] == 1
    assert len(read_json(result.exported_reqir_path)["items"]) == 2
    assert len(read_json(result.output_dir / "extracted" / "rejected_model_items.json")) == 1
    assert read_json(result.output_dir / "extracted" / "chunk_errors.json") == []


def test_all_malformed_items_fail_with_distinct_zero_result_reason(tmp_path: Path):
    fixture = tmp_path / "malformed.json"
    fixture.write_text('{"items":[{"statement":"missing source"}]}', encoding="utf-8")
    output = tmp_path / "malformed"
    with pytest.raises(PipelineValidationError, match="NO_VALID_MODEL_ITEMS"):
        PipelineRunner().extract(
            "docs/sample_srs.md", output, model_mode="recorded", recorded_fixture=fixture
        )
    manifest = read_json(output / "run_manifest.json")
    assert manifest["status"] == "failed"
    assert manifest["zero_result_reason"] == "NO_VALID_MODEL_ITEMS"
    assert manifest["counts"]["model_items_total"] == 1
    assert manifest["counts"]["model_items_accepted"] == 0
    assert manifest["counts"]["model_items_rejected"] == 1


def test_all_quarantined_items_have_distinct_zero_result_reason(tmp_path: Path):
    fixture = tmp_path / "ungrounded.json"
    fixture.write_text(
        '{"items":[{"statement":"Ungrounded","source_block_id":"blk_0006","source_quote":"absent"}]}',
        encoding="utf-8",
    )
    result = PipelineRunner().extract(
        "docs/sample_srs.md",
        tmp_path / "all-quarantined",
        model_mode="recorded",
        recorded_fixture=fixture,
        validation_policy="quarantine",
    )
    manifest = read_json(result.manifest_path)
    assert manifest["status"] == "completed_with_warnings"
    assert manifest["zero_result_reason"] == "ALL_CANDIDATES_QUARANTINED"
    assert read_json(result.exported_reqir_path)["items"] == []


def test_recorded_single_file_is_rejected_for_multi_chunk_run(tmp_path: Path):
    with pytest.raises(PipelineValidationError, match="RECORDED_FIXTURE_NOT_CHUNK_AWARE"):
        PipelineRunner().extract(
            "docs/sample_srs.md",
            tmp_path / "recorded-single",
            model_mode="recorded",
            recorded_fixture="fixtures/recorded/sample_srs_reqir_response_full.json",
            chunking_mode="force",
            max_rendered_prompt_chars=1600,
        )


def test_recorded_chunk_bundle_replays_and_validates_profile(tmp_path: Path):
    result = PipelineRunner().extract(
        "eval/cases/sample_srs_long/document.md",
        tmp_path / "recorded-bundle",
        model_mode="recorded",
        recorded_fixture="fixtures/recorded/chunked/sample_srs_long",
        chunking_mode="force",
        max_rendered_prompt_chars=4000,
    )
    assert result.validated_count == 15

    with pytest.raises(PipelineValidationError, match="RECORDED_REQUEST_PROFILE_MISMATCH"):
        PipelineRunner().extract(
            "eval/cases/sample_srs_long/document.md",
            tmp_path / "recorded-mismatch",
            model_mode="recorded",
            model_name="different-model",
            recorded_fixture="fixtures/recorded/chunked/sample_srs_long",
            chunking_mode="force",
            max_rendered_prompt_chars=4000,
        )


def test_empty_document_fails_before_model_call(tmp_path: Path, monkeypatch):
    document = tmp_path / "empty.md"
    document.write_text("", encoding="utf-8")

    class NeverCalledModel:
        def generate(self, request):
            raise AssertionError("model must not be called")

    monkeypatch.setattr("spectrail.pipeline.runner.create_model_client", lambda **kwargs: NeverCalledModel())
    output = tmp_path / "empty-document"
    with pytest.raises(PipelineValidationError, match="NO_EXTRACTABLE_CONTENT"):
        PipelineRunner().extract(document, output, model_mode="mock")
    manifest = read_json(output / "run_manifest.json")
    assert manifest["error_code"] == "NO_EXTRACTABLE_CONTENT"
    assert manifest["zero_result_reason"] is None
    assert not (output / "extracted" / "chunk_results").exists()


def test_partial_chunk_failure_with_empty_results_has_distinct_reason(tmp_path: Path, monkeypatch):
    class PartiallyFailingModel:
        def generate(self, request):
            if request.metadata["chunk_index"] == 2:
                raise RuntimeError("synthetic chunk failure")
            return ModelResponse(
                payload={"items": []},
                model_mode="mock",
                model_name="partial-test",
            )

    monkeypatch.setattr(
        "spectrail.pipeline.runner.create_model_client", lambda **kwargs: PartiallyFailingModel()
    )
    result = PipelineRunner().extract(
        "docs/sample_srs.md",
        tmp_path / "partial-empty",
        model_mode="mock",
        chunking_mode="force",
        max_rendered_prompt_chars=1600,
    )
    manifest = read_json(result.manifest_path)
    assert manifest["status"] == "completed_with_warnings"
    assert manifest["zero_result_reason"] == "PARTIAL_EXECUTION_EMPTY_RESULT"
    assert manifest["counts"]["chunks_failed"] == 1
    assert read_json(result.exported_reqir_path)["items"] == []
