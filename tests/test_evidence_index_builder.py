from dataclasses import replace
from pathlib import Path

import pytest

from spectrail.core.models import DocumentBlock
from spectrail.core.io import read_json
from spectrail.evidence import (
    BlockEvidenceRecord,
    CellBlockOccurrence,
    EvidenceIndex,
    ParserIdentity,
    TableCellRecord,
    TableRecord,
    sha256_text,
)
from spectrail.evidence.fingerprint import (
    finalize_evidence_fingerprint,
    sha256_file,
)
from spectrail.evidence.index_builder import (
    ensure_evidence_index,
    validate_evidence_index_against_parsed_document,
)
from spectrail.parsers.base import ParsedDocument
from spectrail.pipeline import PipelineRunner


def _parsed(path: Path) -> ParsedDocument:
    return ParsedDocument(
        document_id="doc_001",
        document_name=path.name,
        source_format="markdown",
        parser_name="markdown_parser_v1",
        text="hello",
        blocks=[
            DocumentBlock(
                block_id="blk_0001",
                document_id="doc_001",
                type="paragraph",
                text="hello",
                order=1,
            )
        ],
    )


def test_parser_evidence_index_must_match_parsed_block_content(tmp_path: Path):
    document = tmp_path / "sample.md"
    document.write_text("hello", encoding="utf-8")
    parsed = _parsed(document)
    index = ensure_evidence_index(document, parsed)
    stale_block = index.blocks[0].model_copy(
        update={"text_length": 5, "text_sha256": "f" * 64}
    )
    stale_index = finalize_evidence_fingerprint(
        index.model_copy(update={"blocks": [stale_block]})
    )

    with pytest.raises(ValueError, match="text_sha256"):
        ensure_evidence_index(
            document,
            replace(parsed, evidence_index=stale_index),
        )


@pytest.mark.parametrize("field_name", ["document_id", "document_name", "source_format"])
def test_parser_evidence_index_identity_must_match_parsed_document(
    tmp_path: Path,
    field_name: str,
):
    document = tmp_path / "sample.md"
    document.write_text("hello", encoding="utf-8")
    parsed = _parsed(document)
    index = ensure_evidence_index(document, parsed)
    mismatched = index.model_copy(update={field_name: "different"})

    with pytest.raises(ValueError, match=field_name):
        ensure_evidence_index(
            document,
            replace(parsed, evidence_index=mismatched),
        )


def test_parser_evidence_index_rejects_stale_fingerprint(tmp_path: Path):
    document = tmp_path / "sample.md"
    document.write_text("hello", encoding="utf-8")
    parsed = _parsed(document)
    index = ensure_evidence_index(document, parsed)
    stale = index.model_copy(update={"evidence_fingerprint": "f" * 64})

    with pytest.raises(ValueError, match="fingerprint"):
        ensure_evidence_index(document, replace(parsed, evidence_index=stale))


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"text_length": 4}, "text_length"),
        ({"page": 1}, "page"),
    ],
)
def test_parser_evidence_block_shape_must_match_parsed_block(
    tmp_path: Path,
    update: dict,
    message: str,
):
    document = tmp_path / "sample.md"
    document.write_text("hello", encoding="utf-8")
    parsed = _parsed(document)
    index = ensure_evidence_index(document, parsed)
    mismatched = index.model_copy(
        update={"blocks": [index.blocks[0].model_copy(update=update)]}
    )

    with pytest.raises(ValueError, match=message):
        ensure_evidence_index(
            document,
            replace(parsed, evidence_index=mismatched),
        )


def test_parser_evidence_block_set_must_match_parsed_blocks(tmp_path: Path):
    document = tmp_path / "sample.md"
    document.write_text("hello", encoding="utf-8")
    parsed = _parsed(document)
    index = ensure_evidence_index(document, parsed)
    missing_block = index.model_copy(update={"blocks": []})

    with pytest.raises(ValueError, match="block order"):
        ensure_evidence_index(
            document,
            replace(parsed, evidence_index=missing_block),
        )


def _parsed_table_with_repeated_and_empty_occurrences() -> tuple[ParsedDocument, EvidenceIndex]:
    text = "HeaderHeader"
    block = DocumentBlock(
        block_id="blk_0001",
        document_id="doc_001",
        type="table",
        text=text,
        order=1,
    )
    table = "tbl_00000001"
    header = "cell_00000001_r0001_c0001"
    empty = "cell_00000001_r0001_c0002"
    index = EvidenceIndex(
        document_id="doc_001",
        document_name="sample.docx",
        source_format="docx",
        source_sha256="1" * 64,
        parser_identity=ParserIdentity(
            parser_name="docx_parser_v2",
            parser_version="2",
            source_format="docx",
        ),
        evidence_fingerprint="0" * 64,
        blocks=[
            BlockEvidenceRecord(
                block_id=block.block_id,
                text_length=len(text),
                text_sha256=sha256_text(text),
                table_id=table,
                table_row_index=1,
                cell_ids=[header, empty],
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
                cell_ids=[header, empty],
                occurrence_ids=["occ_00000001", "occ_00000002", "occ_00000003"],
                parser_method="docx_xml",
                topology_status="complete",
            )
        ],
        cells=[
            TableCellRecord(
                cell_id=header,
                table_id=table,
                row_index=1,
                column_index=1,
                text="Header",
                text_sha256=sha256_text("Header"),
                is_header=True,
            ),
            TableCellRecord(
                cell_id=empty,
                table_id=table,
                row_index=1,
                column_index=2,
                text="",
                text_sha256=sha256_text(""),
            ),
        ],
        cell_occurrences=[
            CellBlockOccurrence(
                occurrence_id="occ_00000001",
                cell_id=header,
                block_id=block.block_id,
                canonical_start=0,
                canonical_end=6,
            ),
            CellBlockOccurrence(
                occurrence_id="occ_00000002",
                cell_id=header,
                block_id=block.block_id,
                canonical_start=6,
                canonical_end=12,
                occurrence_role="repeated_header",
            ),
            CellBlockOccurrence(
                occurrence_id="occ_00000003",
                cell_id=empty,
                block_id=block.block_id,
                canonical_start=12,
                canonical_end=12,
            ),
        ],
    )
    parsed = ParsedDocument(
        document_id="doc_001",
        document_name="sample.docx",
        source_format="docx",
        parser_name="docx_parser_v2",
        text=text,
        blocks=[block],
    )
    return parsed, index


def test_cell_occurrences_match_logical_cell_text_including_repeated_and_empty():
    parsed, index = _parsed_table_with_repeated_and_empty_occurrences()

    validate_evidence_index_against_parsed_document(index, parsed)


def test_cell_occurrence_rejects_range_mapped_to_different_text():
    parsed, index = _parsed_table_with_repeated_and_empty_occurrences()
    wrong = index.cell_occurrences[0].model_copy(
        update={"canonical_start": 1, "canonical_end": 7}
    )
    stale = index.model_copy(
        update={"cell_occurrences": [wrong, *index.cell_occurrences[1:]]}
    )

    with pytest.raises(ValueError, match="occurrence text does not match"):
        validate_evidence_index_against_parsed_document(stale, parsed)


def test_table_evidence_requires_table_document_block():
    parsed, index = _parsed_table_with_repeated_and_empty_occurrences()
    inconsistent = replace(
        parsed,
        blocks=[parsed.blocks[0].model_copy(update={"type": "paragraph"})],
    )

    with pytest.raises(ValueError, match="table evidence requires a table document block"):
        validate_evidence_index_against_parsed_document(index, inconsistent)


def test_pipeline_rejects_table_evidence_type_mismatch_before_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    document = tmp_path / "sample.docx"
    document.write_bytes(b"preparsed table fixture")
    parsed, index = _parsed_table_with_repeated_and_empty_occurrences()
    source_hash = sha256_file(document)
    index = finalize_evidence_fingerprint(
        index.model_copy(update={"source_sha256": source_hash})
    )
    inconsistent = replace(
        parsed,
        blocks=[parsed.blocks[0].model_copy(update={"type": "paragraph"})],
        source_sha256=source_hash,
        parser_identity=index.parser_identity,
        evidence_index=index,
    )

    def unexpected_model_client(**_: object):
        raise AssertionError("model client must not be created")

    monkeypatch.setattr(
        "spectrail.pipeline.runner.create_model_client",
        unexpected_model_client,
    )
    output = tmp_path / "output"
    with pytest.raises(
        ValueError,
        match="table evidence requires a table document block",
    ):
        PipelineRunner().extract(
            document,
            output,
            model_mode="mock",
            parsed_document=inconsistent,
        )

    manifest = read_json(output / "run_manifest.json")
    assert manifest["counts"]["model_call_count"] == 0
