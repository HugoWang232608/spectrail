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
from spectrail.evidence.locator_derivation import derive_table_evidence
from spectrail.evidence.quote_matcher import QuoteMatchRange
from spectrail.evidence.source_identity import canonicalize_source_cell_ids
from spectrail.validators.source_locator_validator import SourceLocatorValidator


def test_table_identity_is_canonicalized_before_registry_and_rederived_for_validation():
    cell_1 = "cell_00000001_r0001_c0001"
    cell_2 = "cell_00000001_r0001_c0002"
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
                    column_count=2,
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
                    text=text,
                    text_sha256=sha256_text(text),
                    page=1,
                    bbox=cell_1_bbox if column == 1 else cell_2_bbox,
                )
                for column, (cell_id, text) in enumerate(
                    [(cell_1, "A"), (cell_2, "B")], start=1
                )
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

    try:
        derive_table_evidence(
            index,
            block_id=block.block_id,
            selected_range=QuoteMatchRange(start=0, end=len(block.text)),
            canonical_cell_ids=[cell_1],
            block_text=block.text,
        )
    except ValueError as exc:
        assert "quote range and canonical source cells differ" in str(exc)
    else:
        raise AssertionError("an omitted non-empty quote cell must be rejected")


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
