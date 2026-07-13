import pytest

from spectrail.core.models import DocumentBlock, RequirementIR, SourceSpan
from spectrail.evidence import (
    BlockEvidenceRecord,
    BoundingBox,
    CellBlockOccurrence,
    EvidenceIndex,
    PageRecord,
    ParserIdentity,
    TableCellRecord,
    TableRecord,
    TextFragmentRecord,
    build_quote_match_registry,
    finalize_evidence_fingerprint,
    sha256_text,
)
from spectrail.evidence.enricher import SourceEvidenceEnricher
from spectrail.evidence.errors import EvidenceReferenceError, LocatorDerivationError
from spectrail.evidence.locator_derivation import derive_table_evidence
from spectrail.evidence.quote_matcher import QuoteMatchRange
from spectrail.evidence.source_identity import canonicalize_source_cell_ids
from spectrail.evidence.table_cells import require_contiguous_cell_spans
from spectrail.validators.source_locator_validator import SourceLocatorValidator


def test_table_identity_is_canonicalized_before_registry_and_rederived_for_validation():
    cell_1 = "cell_00000001_r0001_c0001"
    cell_2 = "cell_00000001_r0001_c0003"
    table_id = "tbl_00000001"
    cell_1_bbox = BoundingBox(x0=10, y0=20, x1=30, y1=40)
    cell_2_bbox = BoundingBox(x0=30, y0=20, x1=50, y1=40)
    block = DocumentBlock(
        block_id="blk_0001",
        document_id="doc_001",
        type="table",
        text="A | B",
        page=1,
        order=1,
    )
    index = finalize_evidence_fingerprint(
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.docx",
            source_format="docx",
            source_sha256="a" * 64,
            parser_identity=ParserIdentity(
                parser_name="docx_parser_v2", parser_version="2"
            ),
            evidence_fingerprint="0" * 64,
            pages=[
                PageRecord(
                    page_id="page_0001",
                    page=1,
                    width=100,
                    height=100,
                    source_rotation=0,
                    block_ids=[block.block_id],
                    table_ids=[table_id],
                )
            ],
            blocks=[
                BlockEvidenceRecord(
                    block_id=block.block_id,
                    text_length=len(block.text),
                    text_sha256=sha256_text(block.text),
                    page=1,
                    table_id=table_id,
                    cell_ids=[cell_1, cell_2],
                    expected_capabilities=["text_range", "table_cell"],
                    available_capabilities=["text_range", "table_cell"],
                )
            ],
            tables=[
                TableRecord(
                    table_id=table_id,
                    block_ids=[block.block_id],
                    page=1,
                    bbox=BoundingBox(x0=10, y0=20, x1=50, y1=40),
                    row_count=1,
                    column_count=3,
                    cell_ids=[cell_1, cell_2],
                    occurrence_ids=["occ_00000001", "occ_00000002"],
                    parser_method="docx_xml",
                )
            ],
            cells=[
                TableCellRecord(
                    cell_id=cell_id,
                    table_id=table_id,
                    row_index=1,
                    column_index=column,
                    column_span=column_span,
                    text=text,
                    text_sha256=sha256_text(text),
                    page=1,
                    bbox=bbox,
                )
                for cell_id, text, column, column_span, bbox in [
                    (cell_1, "A", 1, 2, cell_1_bbox),
                    (cell_2, "B", 3, 1, cell_2_bbox),
                ]
            ],
            cell_occurrences=[
                CellBlockOccurrence(
                    occurrence_id="occ_00000001",
                    cell_id=cell_1,
                    block_id=block.block_id,
                    canonical_start=0,
                    canonical_end=1,
                ),
                CellBlockOccurrence(
                    occurrence_id="occ_00000002",
                    cell_id=cell_2,
                    block_id=block.block_id,
                    canonical_start=4,
                    canonical_end=5,
                ),
            ],
        )
    )
    requirement = RequirementIR(
        id="REQ-1",
        statement="A maps to B.",
        sources=[
            SourceSpan(
                document_id="doc_001",
                block_id=block.block_id,
                quote=block.text,
                source_cell_ids_raw=[cell_2, cell_1],
            )
        ],
    )

    canonicalize_source_cell_ids([requirement], index)
    source = requirement.sources[0]
    assert source.canonical_source_cell_ids == [cell_1, cell_2]
    registry = build_quote_match_registry(
        [requirement], [block], evidence_fingerprint=index.evidence_fingerprint
    )
    SourceEvidenceEnricher().enrich([requirement], index, registry, [block])
    assert source.table_locator is not None
    assert source.table_locator.cell_ids == [cell_1, cell_2]
    assert source.table_locator.column_indices == [1, 3]
    assert source.table_locator.bbox == BoundingBox(x0=10, y0=20, x1=50, y1=40)
    assert source.page == 1

    validated, report, failures = SourceLocatorValidator().validate(
        [requirement],
        index,
        registry,
        policy="structured_required",
        document_blocks=[block],
    )
    assert validated == [requirement]
    assert report.valid is True
    assert failures == []
    require_contiguous_cell_spans(index.cells)
    with pytest.raises(EvidenceReferenceError, match="occupied column spans"):
        require_contiguous_cell_spans(
            [index.cells[0].model_copy(update={"column_span": 1}), index.cells[1]]
        )

    with pytest.raises(
        LocatorDerivationError,
        match="quote range and canonical source cells differ",
    ):
        derive_table_evidence(
            index,
            block_id=block.block_id,
            selected_range=QuoteMatchRange(start=0, end=len(block.text)),
            canonical_cell_ids=[cell_1],
            block_text=block.text,
        )
    with pytest.raises(EvidenceReferenceError, match="unknown cell"):
        derive_table_evidence(
            index,
            block_id=block.block_id,
            selected_range=QuoteMatchRange(start=0, end=len(block.text)),
            canonical_cell_ids=["cell_99999999_r0001_c0001"],
            block_text=block.text,
        )

    validator = SourceLocatorValidator()
    source.canonical_source_cell_ids = ["cell_99999999_r0001_c0001"]
    results = validator.validate_source(
        source,
        index,
        {item.block_id: item for item in index.blocks},
        registry,
        {block.block_id: block},
    )
    assert next(
        result for result in results if result.capability == "table_cell"
    ).status == "FAIL_INVALID_REFERENCE"

    source.canonical_source_cell_ids = [cell_1]
    results = validator.validate_source(
        source,
        index,
        {item.block_id: item for item in index.blocks},
        registry,
        {block.block_id: block},
    )
    assert next(
        result for result in results if result.capability == "table_cell"
    ).status == "FAIL_DERIVATION"


def test_page_locator_validates_rotation_derivation_and_source_page():
    bbox = BoundingBox(x0=10, y0=20, x1=50, y1=60)
    block = DocumentBlock(
        block_id="blk_0001",
        document_id="doc_001",
        type="paragraph",
        text="hello",
        page=1,
        order=1,
    )
    index = finalize_evidence_fingerprint(
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.pdf",
            source_format="pdf",
            source_sha256="a" * 64,
            parser_identity=ParserIdentity(
                parser_name="pdf_parser_v2", parser_version="2"
            ),
            evidence_fingerprint="0" * 64,
            pages=[
                PageRecord(
                    page_id="page_0001",
                    page=1,
                    width=100,
                    height=200,
                    source_rotation=90,
                    block_ids=[block.block_id],
                    table_ids=[],
                )
            ],
            blocks=[
                BlockEvidenceRecord(
                    block_id=block.block_id,
                    text_length=5,
                    text_sha256=sha256_text(block.text),
                    page=1,
                    bbox=bbox,
                    fragment_ids=["frag_1"],
                    expected_capabilities=["text_range", "page_region"],
                    available_capabilities=["text_range", "page_region"],
                )
            ],
            fragments=[
                TextFragmentRecord(
                    fragment_id="frag_1",
                    block_id=block.block_id,
                    start=0,
                    end=5,
                    text="hello",
                    page=1,
                    bbox=bbox,
                    line_index=0,
                    span_index=0,
                )
            ],
        )
    )
    requirement = RequirementIR(
        id="REQ-1",
        statement="Hello.",
        sources=[
            SourceSpan(
                document_id="doc_001",
                block_id=block.block_id,
                quote="hello",
            )
        ],
    )
    registry = build_quote_match_registry(
        [requirement], [block], evidence_fingerprint=index.evidence_fingerprint
    )
    SourceEvidenceEnricher().enrich([requirement], index, registry, [block])
    source = requirement.sources[0]
    assert source.page_locator is not None
    assert source.page_locator.source_rotation == 90
    assert source.page_locator.derivation == "quote_span_union"
    validated, report, _ = SourceLocatorValidator().validate(
        [requirement],
        index,
        registry,
        policy="structured_required",
        document_blocks=[block],
    )
    assert validated == [requirement]
    assert report.valid is True

    source.page_locator = source.page_locator.model_copy(
        update={"derivation": "block_bbox"}
    )
    validated, report, _ = SourceLocatorValidator().validate(
        [requirement],
        index,
        registry,
        policy="structured_required",
        document_blocks=[block],
    )
    assert validated == []
    assert report.valid is False


def test_ambiguous_quote_uses_separate_provisional_text_locator():
    text = "repeat / repeat"
    block = DocumentBlock(
        block_id="blk_0001",
        document_id="doc_001",
        type="paragraph",
        text=text,
        order=1,
    )
    index = finalize_evidence_fingerprint(
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.md",
            source_format="markdown",
            source_sha256="a" * 64,
            parser_identity=ParserIdentity(
                parser_name="markdown_parser_v1", parser_version="1"
            ),
            evidence_fingerprint="0" * 64,
            blocks=[
                BlockEvidenceRecord(
                    block_id=block.block_id,
                    text_length=len(text),
                    text_sha256=sha256_text(text),
                    available_capabilities=["text_range"],
                )
            ],
        )
    )
    requirement = RequirementIR(
        id="REQ-1",
        statement="Repeat.",
        sources=[
            SourceSpan(
                document_id="doc_001",
                block_id=block.block_id,
                quote="repeat",
            )
        ],
    )
    registry = build_quote_match_registry(
        [requirement], [block], evidence_fingerprint=index.evidence_fingerprint
    )
    SourceEvidenceEnricher().enrich([requirement], index, registry, [block])
    source = requirement.sources[0]
    assert source.text_locator is None
    assert source.provisional_text_locator is not None
    assert (source.provisional_text_locator.start, source.provisional_text_locator.end) == (0, 6)

    validated, report, failures = SourceLocatorValidator().validate(
        [requirement],
        index,
        registry,
        policy="structured_if_available",
        document_blocks=[block],
    )
    assert validated == [requirement]
    assert report.valid is True
    assert failures == []
    assert source.locator_status == "WARNING_AMBIGUOUS"

    validated, report, _ = SourceLocatorValidator().validate(
        [requirement],
        index,
        registry,
        policy="structured_required",
        document_blocks=[block],
    )
    assert validated == []
    assert report.valid is False


def test_table_derivation_selects_only_the_overlapping_repeated_occurrence():
    cell = "cell_00000001_r0001_c0001"
    table = "tbl_00000001"
    text = "A / A"
    index = finalize_evidence_fingerprint(
        EvidenceIndex(
            document_id="doc_001",
            document_name="repeated.docx",
            source_format="docx",
            source_sha256="a" * 64,
            parser_identity=ParserIdentity(
                parser_name="docx_parser_v2",
                parser_version="2",
            ),
            evidence_fingerprint="0" * 64,
            blocks=[
                BlockEvidenceRecord(
                    block_id="blk_0001",
                    text_length=len(text),
                    text_sha256=sha256_text(text),
                    table_id=table,
                    cell_ids=[cell],
                    expected_capabilities=["text_range", "table_cell"],
                    available_capabilities=["text_range", "table_cell"],
                )
            ],
            tables=[
                TableRecord(
                    table_id=table,
                    block_ids=["blk_0001"],
                    row_count=1,
                    column_count=1,
                    cell_ids=[cell],
                    occurrence_ids=["occ_00000001", "occ_00000002"],
                    parser_method="docx_xml",
                )
            ],
            cells=[
                TableCellRecord(
                    cell_id=cell,
                    table_id=table,
                    row_index=1,
                    column_index=1,
                    text="A",
                    text_sha256=sha256_text("A"),
                )
            ],
            cell_occurrences=[
                CellBlockOccurrence(
                    occurrence_id="occ_00000001",
                    cell_id=cell,
                    block_id="blk_0001",
                    canonical_start=0,
                    canonical_end=1,
                ),
                CellBlockOccurrence(
                    occurrence_id="occ_00000002",
                    cell_id=cell,
                    block_id="blk_0001",
                    canonical_start=4,
                    canonical_end=5,
                    occurrence_role="repeated_header",
                ),
            ],
        )
    )

    derived = derive_table_evidence(
        index,
        block_id="blk_0001",
        selected_range=QuoteMatchRange(start=0, end=1),
        canonical_cell_ids=[cell],
        block_text=text,
    )

    assert derived.reconstructed_text == "A"


def test_partial_cell_quote_derives_and_validates_the_selected_text_range():
    cell = "cell_00000001_r0001_c0001"
    table = "tbl_00000001"
    text = "The system shall respond within 2 seconds."
    quote = "within 2 seconds"
    block = DocumentBlock(
        block_id="blk_0001",
        document_id="doc_001",
        type="table",
        text=text,
        order=1,
    )
    index = finalize_evidence_fingerprint(
        EvidenceIndex(
            document_id="doc_001",
            document_name="partial.docx",
            source_format="docx",
            source_sha256="a" * 64,
            parser_identity=ParserIdentity(
                parser_name="docx_parser_v2",
                parser_version="2",
            ),
            evidence_fingerprint="0" * 64,
            blocks=[
                BlockEvidenceRecord(
                    block_id=block.block_id,
                    text_length=len(text),
                    text_sha256=sha256_text(text),
                    table_id=table,
                    cell_ids=[cell],
                    expected_capabilities=["text_range", "table_cell"],
                    available_capabilities=["text_range", "table_cell"],
                )
            ],
            tables=[
                TableRecord(
                    table_id=table,
                    block_ids=[block.block_id],
                    row_count=1,
                    column_count=1,
                    cell_ids=[cell],
                    occurrence_ids=["occ_00000001"],
                    parser_method="docx_xml",
                )
            ],
            cells=[
                TableCellRecord(
                    cell_id=cell,
                    table_id=table,
                    row_index=1,
                    column_index=1,
                    text=text,
                    text_sha256=sha256_text(text),
                )
            ],
            cell_occurrences=[
                CellBlockOccurrence(
                    occurrence_id="occ_00000001",
                    cell_id=cell,
                    block_id=block.block_id,
                    canonical_start=0,
                    canonical_end=len(text),
                )
            ],
        )
    )
    requirement = RequirementIR(
        id="REQ-1",
        statement="The system responds within two seconds.",
        sources=[
            SourceSpan(
                document_id="doc_001",
                block_id=block.block_id,
                quote=quote,
                source_cell_ids_raw=[cell],
            )
        ],
    )

    canonicalize_source_cell_ids([requirement], index)
    registry = build_quote_match_registry(
        [requirement],
        [block],
        evidence_fingerprint=index.evidence_fingerprint,
    )
    SourceEvidenceEnricher().enrich([requirement], index, registry, [block])
    validated, report, failures = SourceLocatorValidator().validate(
        [requirement],
        index,
        registry,
        policy="structured_required",
        document_blocks=[block],
    )

    assert requirement.sources[0].table_locator is not None
    assert validated == [requirement]
    assert report.valid is True
    assert failures == []
