import pytest
from pydantic import ValidationError

from spectrail.evidence import (
    BlockEvidenceRecord,
    BoundingBox,
    CapabilityValidationResult,
    CellBlockOccurrence,
    EvidenceIndex,
    PageRecord,
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


def test_all_evidence_blocks_must_expect_text_range():
    with pytest.raises(ValidationError, match="must expect text_range"):
        BlockEvidenceRecord(
            block_id="blk_0001",
            text_length=1,
            text_sha256=sha256_text("x"),
            expected_capabilities=["page_region"],
        )


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


def test_locator_status_rejects_unexpected_results_and_empty_expected_set():
    text_pass = CapabilityValidationResult(capability="text_range", status="PASS")
    assert aggregate_locator_status([], []) == "UNVERIFIED"
    with pytest.raises(ValueError, match="not expected"):
        aggregate_locator_status([], [text_pass])


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


def test_evidence_index_rejects_page_with_foreign_block():
    with pytest.raises(ValidationError, match="block from another page"):
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.pdf",
            source_format="pdf",
            source_sha256="1" * 64,
            parser_identity=ParserIdentity(parser_name="pdf_parser_v2", parser_version="2"),
            evidence_fingerprint="0" * 64,
            pages=[
                PageRecord(
                    page_id="page_0001",
                    page=1,
                    width=100,
                    height=100,
                    source_rotation=0,
                    block_ids=["blk_0001"],
                    table_ids=[],
                )
            ],
            blocks=[
                BlockEvidenceRecord(
                    block_id="blk_0001",
                    text_length=1,
                    text_sha256=sha256_text("x"),
                    page=2,
                )
            ],
        )


def test_evidence_index_rejects_block_cells_without_table():
    with pytest.raises(ValidationError, match="cell IDs require table_id"):
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
                    cell_ids=["cell_1"],
                )
            ],
            cells=[
                TableCellRecord(
                    cell_id="cell_1",
                    table_id="table_1",
                    row_index=1,
                    column_index=1,
                    text="x",
                    text_sha256=sha256_text("x"),
                )
            ],
            tables=[
                TableRecord(
                    table_id="table_1",
                    block_ids=[],
                    row_count=1,
                    column_count=1,
                    cell_ids=["cell_1"],
                    occurrence_ids=[],
                    parser_method="docx_xml",
                )
            ],
        )


def test_evidence_index_rejects_table_with_foreign_cell():
    with pytest.raises(ValidationError, match="cell owned by another table"):
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.docx",
            source_format="docx",
            source_sha256="1" * 64,
            parser_identity=ParserIdentity(parser_name="docx_parser_v2", parser_version="2"),
            evidence_fingerprint="0" * 64,
            tables=[
                TableRecord(
                    table_id="table_1",
                    block_ids=[],
                    row_count=1,
                    column_count=1,
                    cell_ids=["cell_2"],
                    occurrence_ids=[],
                    parser_method="docx_xml",
                ),
                TableRecord(
                    table_id="table_2",
                    block_ids=[],
                    row_count=1,
                    column_count=1,
                    cell_ids=[],
                    occurrence_ids=[],
                    parser_method="docx_xml",
                ),
            ],
            cells=[
                TableCellRecord(
                    cell_id="cell_2",
                    table_id="table_2",
                    row_index=1,
                    column_index=1,
                    text="x",
                    text_sha256=sha256_text("x"),
                )
            ],
        )


def test_evidence_index_rejects_occurrence_cell_not_registered_by_block():
    with pytest.raises(ValidationError, match="not registered by its block"):
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
                    table_id="table_1",
                    cell_ids=["cell_1"],
                )
            ],
            tables=[
                TableRecord(
                    table_id="table_1",
                    block_ids=["blk_0001"],
                    row_count=1,
                    column_count=2,
                    cell_ids=["cell_1", "cell_2"],
                    occurrence_ids=["occ_1"],
                    parser_method="docx_xml",
                )
            ],
            cells=[
                TableCellRecord(
                    cell_id=cell,
                    table_id="table_1",
                    row_index=1,
                    column_index=index,
                    text="x",
                    text_sha256=sha256_text("x"),
                )
                for index, cell in enumerate(["cell_1", "cell_2"], start=1)
            ],
            cell_occurrences=[
                CellBlockOccurrence(
                    occurrence_id="occ_1",
                    cell_id="cell_2",
                    block_id="blk_0001",
                    canonical_start=0,
                    canonical_end=1,
                )
            ],
        )


def test_evidence_index_rejects_block_cell_without_occurrence():
    with pytest.raises(ValidationError, match="has no occurrence"):
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
                    table_id="table_1",
                    cell_ids=["cell_1"],
                )
            ],
            tables=[
                TableRecord(
                    table_id="table_1",
                    block_ids=["blk_0001"],
                    row_count=1,
                    column_count=1,
                    cell_ids=["cell_1"],
                    occurrence_ids=[],
                    parser_method="docx_xml",
                )
            ],
            cells=[
                TableCellRecord(
                    cell_id="cell_1",
                    table_id="table_1",
                    row_index=1,
                    column_index=1,
                    text="x",
                    text_sha256=sha256_text("x"),
                )
            ],
        )
