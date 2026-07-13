from pathlib import Path
from dataclasses import replace

import pytest

from spectrail.core.io import read_json
from spectrail.chunking import ChunkingConfig
from spectrail.llm.base import ModelResponse
from spectrail.llm.errors import ModelProviderError
from spectrail.llm.request_profile import ModelRequestProfile
from spectrail.pipeline import PipelineConfig, PipelineRunner, PipelineValidationError
from spectrail.parsers import parse_document
from spectrail.evidence import (
    BlockEvidenceRecord,
    CellBlockOccurrence,
    EvidenceIndex,
    ParserIdentity,
    TableCellRecord,
    TableRecord,
    finalize_evidence_fingerprint,
    sha256_file,
    sha256_text,
)
from spectrail.core.models import DocumentBlock
from spectrail.parsers import ParsedDocument
from spectrail.evidence.index_builder import ensure_evidence_index


def test_forced_chunked_mock_pipeline_preserves_final_requirements(tmp_path: Path):
    result = PipelineRunner().extract(
        "docs/sample_srs.md",
        tmp_path / "chunked",
        model_mode="mock",
        chunking_mode="force",
        max_rendered_prompt_chars=2400,
    )
    manifest = read_json(result.manifest_path)
    chunks = read_json(result.output_dir / "parsed" / "chunks.json")
    assert manifest["status"] == "completed"
    assert manifest["counts"]["chunks"] >= 3
    assert manifest["counts"]["validated_requirements"] == 15
    assert manifest["counts"]["collapsed_overlap_duplicates"] > 0
    assert all(chunk["new_block_ids"] for chunk in chunks)
    assert all(chunk["rendered_prompt_chars"] <= 2400 for chunk in chunks)


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


def test_locator_policy_gates_available_structured_capability(tmp_path: Path):
    document = Path("docs/sample_srs.md")
    parsed = parse_document(document, document_id="doc_001")
    index = ensure_evidence_index(document, parsed)
    blocks = [
        block.model_copy(
            update={
                "expected_capabilities": ["text_range", "page_region"],
                "available_capabilities": ["text_range", "page_region"],
            }
        )
        if block.block_id == "blk_0006"
        else block
        for block in index.blocks
    ]
    index = finalize_evidence_fingerprint(index.model_copy(update={"blocks": blocks}))
    parsed = replace(
        parsed,
        source_sha256=index.source_sha256,
        parser_identity=index.parser_identity,
        evidence_index=index,
    )
    fixture = tmp_path / "locator.json"
    fixture.write_text(
        '{"items":[{"statement":"Valid quote, missing page locator",'
        '"source_block_id":"blk_0006",'
        '"source_quote":"管理员应能够创建、停用和恢复普通用户账号。"}]}',
        encoding="utf-8",
    )

    strict_output = tmp_path / "locator-strict"
    with pytest.raises(PipelineValidationError, match="source locator validation failed"):
        PipelineRunner().extract(
            document,
            strict_output,
            model_mode="recorded",
            recorded_fixture=fixture,
            parsed_document=parsed,
        )
    failures = read_json(
        strict_output / "extracted" / "source_locator_failures.json"
    )
    assert failures[0]["capability_results"][1]["issue_code"] == (
        "SOURCE_PAGE_LOCATOR_MISSING"
    )

    result = PipelineRunner().extract(
        document,
        tmp_path / "locator-quarantine",
        model_mode="recorded",
        recorded_fixture=fixture,
        validation_policy="quarantine",
        parsed_document=parsed,
    )
    manifest = read_json(result.manifest_path)
    assert manifest["counts"]["validated_requirements"] == 0
    assert manifest["counts"]["quarantined_requirements"] == 1


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


def _table_document_with_evidence(
    tmp_path: Path,
) -> tuple[Path, ParsedDocument]:
    document = tmp_path / "sample.docx"
    document.write_text("A | B", encoding="utf-8")
    block = DocumentBlock(
        block_id="blk_0001",
        document_id="doc_001",
        type="table",
        text="A | B",
        order=1,
    )
    table = "tbl_00000001"
    cells = [
        "cell_00000001_r0001_c0001",
        "cell_00000001_r0001_c0002",
    ]
    source_hash = sha256_file(document)
    parser_identity = ParserIdentity(
        parser_name="docx_parser_v2",
        parser_version="2",
    )
    index = finalize_evidence_fingerprint(
        EvidenceIndex(
            document_id="doc_001",
            document_name=document.name,
            source_format="docx",
            source_sha256=source_hash,
            parser_identity=parser_identity,
            evidence_fingerprint="0" * 64,
            blocks=[
                BlockEvidenceRecord(
                    block_id=block.block_id,
                    text_length=len(block.text),
                    text_sha256=sha256_text(block.text),
                    table_id=table,
                    cell_ids=cells,
                    expected_capabilities=["text_range", "table_cell"],
                    available_capabilities=["text_range", "table_cell"],
                )
            ],
            tables=[
                TableRecord(
                    table_id=table,
                    block_ids=[block.block_id],
                    row_count=1,
                    column_count=2,
                    cell_ids=cells,
                    occurrence_ids=["occ_00000001", "occ_00000002"],
                    parser_method="docx_xml",
                    topology_status="complete",
                )
            ],
            cells=[
                TableCellRecord(
                    cell_id=cell_id,
                    table_id=table,
                    row_index=1,
                    column_index=column,
                    text=text,
                    text_sha256=sha256_text(text),
                )
                for column, (cell_id, text) in enumerate(
                    zip(cells, ["A", "B"]),
                    start=1,
                )
            ],
            cell_occurrences=[
                CellBlockOccurrence(
                    occurrence_id="occ_00000001",
                    cell_id=cells[0],
                    block_id=block.block_id,
                    canonical_start=0,
                    canonical_end=1,
                ),
                CellBlockOccurrence(
                    occurrence_id="occ_00000002",
                    cell_id=cells[1],
                    block_id=block.block_id,
                    canonical_start=4,
                    canonical_end=5,
                ),
            ],
        )
    )
    parsed = ParsedDocument(
        document_id="doc_001",
        document_name=document.name,
        source_format="docx",
        parser_name=parser_identity.parser_name,
        text=block.text,
        blocks=[block],
        source_sha256=source_hash,
        parser_identity=parser_identity,
        evidence_index=index,
    )
    return document, parsed


def test_pipeline_isolates_invalid_table_cell_reference(tmp_path: Path):
    document, parsed = _table_document_with_evidence(tmp_path)

    result = PipelineRunner().extract(
        document,
        tmp_path / "table-isolation",
        model_mode="recorded",
        recorded_fixture="fixtures/mock_table_reqir_response.json",
        validation_policy="quarantine",
        parsed_document=parsed,
    )

    manifest = read_json(result.manifest_path)
    rejected = read_json(
        result.output_dir / "extracted" / "rejected_model_items.json"
    )
    report = read_json(result.output_dir / "extracted" / "validation_report.json")
    assert manifest["counts"]["model_items_accepted"] == 1
    assert manifest["counts"]["model_items_rejected"] == 1
    assert manifest["counts"]["validated_requirements"] == 1
    assert len(read_json(result.exported_reqir_path)["items"]) == 1
    assert rejected[0]["error_code"] == "MODEL_ITEM_INVALID_CELL_IDS"
    assert any(
        issue["code"] == "MODEL_ITEM_INVALID_CELL_IDS"
        for issue in report["issues"]
    )


def test_quote_only_pipeline_does_not_require_table_cell_ids(tmp_path: Path):
    document, parsed = _table_document_with_evidence(tmp_path)
    fixture = tmp_path / "quote-only.json"
    fixture.write_text(
        '{"items":[{"statement":"A maps to B",'
        '"source_block_id":"blk_0001","source_quote":"A | B"}]}',
        encoding="utf-8",
    )

    result = PipelineRunner().extract(
        document,
        tmp_path / "table-quote-only",
        model_mode="recorded",
        recorded_fixture=fixture,
        evidence_policy="quote_only",
        parsed_document=parsed,
    )

    manifest = read_json(result.manifest_path)
    exported = read_json(result.exported_reqir_path)["items"]
    locator_report = read_json(
        result.output_dir / "extracted" / "source_locator_report.json"
    )
    assert manifest["status"] == "completed"
    assert manifest["warning_codes"] == []
    assert manifest["counts"]["model_items_rejected"] == 0
    assert manifest["counts"]["validated_requirements"] == 1
    assert locator_report["issues"] == []
    assert exported[0]["sources"][0]["canonical_source_cell_ids"] == []
    assert exported[0]["sources"][0]["locator_status"] == "PASS_DERIVED"


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
        max_rendered_prompt_chars=4600,
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
            max_rendered_prompt_chars=4600,
        )

    with pytest.raises(PipelineValidationError, match="RECORDED_REQUEST_PROFILE_MISMATCH"):
        PipelineRunner().extract(
            "eval/cases/sample_srs_long/document.md",
            tmp_path / "recorded-temperature-mismatch",
            config=PipelineConfig(
                model_mode="recorded",
                recorded_fixture="fixtures/recorded/chunked/sample_srs_long",
                request_profile=ModelRequestProfile(
                    provider_adapter="openai_compatible_v1",
                    provider_endpoint_id="mock",
                    model_name="mock-fixture",
                    temperature=0.5,
                ),
                chunking=ChunkingConfig(
                    mode="force", max_rendered_prompt_chars=4600, overlap_blocks=1
                ),
            ),
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
                raise ModelProviderError("synthetic chunk failure")
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


def test_all_chunks_with_invalid_top_level_payload_fail(tmp_path: Path, monkeypatch):
    class InvalidEnvelopeModel:
        def generate(self, request):
            payload = {"foo": "missing items"}
            if request.metadata["chunk_index"] % 2 == 0:
                payload = {"items": {"not": "an array"}}
            return ModelResponse(payload=payload, model_mode="mock", model_name="invalid-envelope")

    monkeypatch.setattr(
        "spectrail.pipeline.runner.create_model_client", lambda **kwargs: InvalidEnvelopeModel()
    )
    output = tmp_path / "all-invalid-envelopes"
    with pytest.raises(PipelineValidationError, match="ALL_CHUNKS_FAILED"):
        PipelineRunner().extract(
            "docs/sample_srs.md",
            output,
            model_mode="mock",
            chunking_mode="force",
            max_rendered_prompt_chars=1600,
        )
    manifest = read_json(output / "run_manifest.json")
    assert manifest["status"] == "failed"
    assert manifest["error_code"] == "ALL_CHUNKS_FAILED"
    assert manifest["zero_result_reason"] == "ALL_CHUNKS_FAILED"
    assert manifest["counts"]["chunks_failed"] == manifest["counts"]["chunks"]


def test_unexpected_programming_error_is_not_isolated(tmp_path: Path, monkeypatch):
    class BrokenModel:
        def generate(self, request):
            raise AttributeError("synthetic programming defect")

    monkeypatch.setattr("spectrail.pipeline.runner.create_model_client", lambda **kwargs: BrokenModel())
    output = tmp_path / "unexpected-defect"
    with pytest.raises(AttributeError, match="synthetic programming defect"):
        PipelineRunner().extract(
            "docs/sample_srs.md",
            output,
            model_mode="mock",
            chunking_mode="force",
            max_rendered_prompt_chars=1600,
        )
    manifest = read_json(output / "run_manifest.json")
    assert manifest["status"] == "failed"
    assert manifest["error_code"] == "AttributeError"
    assert manifest["counts"]["chunks_failed"] == 0
