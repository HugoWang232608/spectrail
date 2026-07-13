import pytest
from pydantic import ValidationError

from spectrail.evidence import (
    BlockEvidenceRecord,
    BoundingBox,
    CapabilityValidationResult,
    CellBlockOccurrence,
    EvidenceIndex,
    ParserIdentity,
    TableCellRecord,
    TableLocator,
    TableRecord,
    aggregate_locator_status,
    cell_id,
    occurrence_id,
    sha256_text,
    table_id,
)


def test_bbox_rejects_non_positive_geometry():
    with pytest.raises(ValidationError, match="positive width and height"):
        BoundingBox(x0=1, y0=1, x1=1, y1=2)


def test_table_locator_requires_canonical_contiguous_columns():
    with pytest.raises(ValidationError, match="canonical column order"):
        TableLocator(
            table_id="tbl_00000001",
            cell_ids=["c2", "c1"],
            row_indices=[1, 1],
            column_indices=[2, 1],
        )
    with pytest.raises(ValidationError, match="contiguous"):
        TableLocator(
            table_id="tbl_00000001",
            cell_ids=["c1", "c3"],
            row_indices=[1, 1],
            column_indices=[1, 3],
        )


def test_locator_status_distinguishes_derived_and_structured_sources():
    text_pass = CapabilityValidationResult(capability="text_range", status="PASS")
    page_pass = CapabilityValidationResult(capability="page_region", status="PASS")
    assert aggregate_locator_status(["text_range"], [text_pass]) == "PASS_DERIVED"
    assert aggregate_locator_status(
        ["text_range", "page_region"], [text_pass, page_pass]
    ) == "PASS_STRUCTURED"
    assert aggregate_locator_status(
        ["text_range", "page_region"],
        [
            text_pass,
            CapabilityValidationResult(
                capability="page_region", status="WARNING_AMBIGUOUS"
            ),
        ],
    ) == "WARNING_AMBIGUOUS"


def test_evidence_index_supports_repeated_header_occurrences():
    table_identifier = table_id(1)
    header_cell = cell_id(1, 1, 1)
    blocks = [
        BlockEvidenceRecord(
            block_id="blk_0001",
            text_length=6,
            text_sha256=sha256_text("Header"),
            table_id=table_identifier,
            cell_ids=[header_cell],
            expected_capabilities=["text_range", "table_cell"],
            available_capabilities=["text_range", "table_cell"],
        ),
        BlockEvidenceRecord(
            block_id="blk_0002",
            text_length=6,
            text_sha256=sha256_text("Header"),
            table_id=table_identifier,
            cell_ids=[header_cell],
            expected_capabilities=["text_range", "table_cell"],
            available_capabilities=["text_range", "table_cell"],
        ),
    ]
    occurrences = [
        CellBlockOccurrence(
            occurrence_id=occurrence_id(1),
            cell_id=header_cell,
            block_id="blk_0001",
            canonical_start=0,
            canonical_end=6,
        ),
        CellBlockOccurrence(
            occurrence_id=occurrence_id(2),
            cell_id=header_cell,
            block_id="blk_0002",
            canonical_start=0,
            canonical_end=6,
            occurrence_role="repeated_header",
        ),
    ]
    index = EvidenceIndex(
        document_id="doc_001",
        document_name="sample.docx",
        source_format="docx",
        source_sha256="1" * 64,
        parser_identity=ParserIdentity(parser_name="docx_parser_v2", parser_version="2"),
        evidence_fingerprint="0" * 64,
        blocks=blocks,
        tables=[
            TableRecord(
                table_id=table_identifier,
                block_ids=["blk_0001", "blk_0002"],
                row_count=1,
                column_count=1,
                cell_ids=[header_cell],
                occurrence_ids=[item.occurrence_id for item in occurrences],
                parser_method="docx_xml",
            )
        ],
        cells=[
            TableCellRecord(
                cell_id=header_cell,
                table_id=table_identifier,
                row_index=1,
                column_index=1,
                text="Header",
                text_sha256=sha256_text("Header"),
                is_header=True,
            )
        ],
        cell_occurrences=occurrences,
    )
    assert len(index.cell_occurrences) == 2
    assert index.cell_occurrences[1].occurrence_role == "repeated_header"


def test_evidence_index_rejects_dangling_occurrence():
    with pytest.raises(ValidationError, match="unknown cell"):
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.docx",
            source_format="docx",
            source_sha256="1" * 64,
            parser_identity=ParserIdentity(parser_name="docx_parser_v2", parser_version="2"),
            evidence_fingerprint="0" * 64,
            blocks=[
                BlockEvidenceRecord(
                    block_id="blk_0001",
                    text_length=1,
                    text_sha256=sha256_text("x"),
                )
            ],
            cell_occurrences=[
                CellBlockOccurrence(
                    occurrence_id=occurrence_id(1),
                    cell_id="missing",
                    block_id="blk_0001",
                    canonical_start=0,
                    canonical_end=1,
                )
            ],
        )
