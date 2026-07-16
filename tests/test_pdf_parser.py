from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from spectrail.chunking import SectionAwareChunker
from spectrail.core.models import RequirementIR, SourceSpan
from spectrail.evidence import build_quote_match_registry
from spectrail.evidence.enricher import SourceEvidenceEnricher
from spectrail.evidence.index_builder import (
    ensure_evidence_index,
    validate_evidence_index_against_parsed_document,
)
from spectrail.parsers.base import DocumentParseError
from spectrail.parsers import pdf_parser as pdf_parser_module
from spectrail.parsers.pdf_parser import (
    PdfParserV2,
    TextPdfParser,
    _project_text_block,
)
from spectrail.parsers.registry import parse_document
from spectrail.validators.source_locator_validator import SourceLocatorValidator


def test_text_pdf_parser_extracts_page_aware_blocks(tmp_path: Path):
    path = tmp_path / "sample.pdf"
    document = fitz.open()
    page1 = document.new_page()
    page1.insert_text((72, 72), "System Requirements\n\nThe system shall export requirements as XLSX.")
    page2 = document.new_page()
    page2.insert_text((72, 72), "The system shall show source quotes.")
    document.save(path)
    document.close()

    parsed = TextPdfParser().parse(path)

    assert parsed.document_name == "sample.pdf"
    assert parsed.source_format == "pdf"
    assert parsed.parser_name == "pdf_parser_v2"
    assert parsed.blocks
    assert {block.page for block in parsed.blocks} == {1, 2}
    assert parsed.blocks[0].metadata["source_format"] == "pdf"
    assert parsed.blocks[0].metadata["parser"] == "pdf_parser_v2"
    assert parsed.blocks[0].metadata["page"] == parsed.blocks[0].page
    assert "The system shall show source quotes." in parsed.text
    assert parsed.parser_identity is not None
    assert parsed.parser_identity.parser_name == "pdf_parser_v2"
    assert parsed.parser_identity.parser_version == "2.4"
    assert parsed.parser_identity.runtime_dependencies["PyMuPDF"] == fitz.__version__
    assert parsed.parser_identity.runtime_dependencies["MuPDF"] == fitz.mupdf_version
    assert parsed.evidence_index is not None
    assert len(parsed.evidence_index.pages) == 2
    assert all(
        block.expected_capabilities == ["text_range", "page_region"]
        and block.available_capabilities == ["text_range", "page_region"]
        for block in parsed.evidence_index.blocks
    )
    assert parsed.evidence_index.fragments
    assert ensure_evidence_index(path, parsed) == parsed.evidence_index
    assert TextPdfParser is PdfParserV2

    stale_fragment = parsed.evidence_index.fragments[0].model_copy(
        update={"text": "X" * len(parsed.evidence_index.fragments[0].text)}
    )
    stale_index = parsed.evidence_index.model_copy(
        update={
            "fragments": [
                stale_fragment,
                *parsed.evidence_index.fragments[1:],
            ]
        }
    )
    with pytest.raises(ValueError, match="fragment content"):
        validate_evidence_index_against_parsed_document(stale_index, parsed)


def test_text_pdf_parser_extracts_included_ieee29148_fixture():
    path = Path("tests/fixtures/ieee29148_srs_example.pdf")

    parsed = TextPdfParser().parse(path)

    assert parsed.document_name == "ieee29148_srs_example.pdf"
    assert parsed.source_format == "pdf"
    assert parsed.metadata["page_count"] == 11
    assert len(parsed.blocks) >= 30
    assert any(
        warning.startswith("PDF_REPEATED_HEADER_CANDIDATE: page=")
        for warning in parsed.warnings
    )
    assert {block.page for block in parsed.blocks}
    assert all(block.metadata["page"] == block.page for block in parsed.blocks)
    assert len(parsed.text) > 1000
    requirement_block = next(
        block
        for block in parsed.blocks
        if "create a new empty document" in block.text
    )
    assert requirement_block.section_path == [
        "2. Requirements",
        "2.2.1 File Operations",
        "2.2.1.1 Create Document",
    ]
    requirement_position = parsed.blocks.index(requirement_block)
    heading_context = SectionAwareChunker._heading_context(
        parsed.blocks,
        requirement_position,
    )
    assert [block.text.strip() for block in heading_context] == [
        "2. Requirements",
        "2.2.1 File Operations",
        "2.2.1.1 Create Document",
    ]


def test_text_pdf_parser_records_warning_for_empty_page_and_continues(tmp_path: Path):
    path = tmp_path / "partly-empty.pdf"
    document = fitz.open()
    document.new_page()
    page2 = document.new_page()
    page2.insert_text((72, 72), "The system shall parse text PDFs.")
    document.save(path)
    document.close()

    parsed = parse_document(path)

    assert parsed.source_format == "pdf"
    assert parsed.warnings == ["page 1 has no extractable text"]
    assert [block.page for block in parsed.blocks] == [2]


def test_text_pdf_parser_rejects_pdf_without_extractable_text(tmp_path: Path):
    path = tmp_path / "empty.pdf"
    document = fitz.open()
    document.new_page()
    document.save(path)
    document.close()

    with pytest.raises(DocumentParseError, match="no extractable text"):
        TextPdfParser().parse(path)


@pytest.mark.parametrize(
    ("rotation", "expected_size"),
    [
        (0, (200.0, 300.0)),
        (90, (300.0, 200.0)),
        (180, (200.0, 300.0)),
        (270, (300.0, 200.0)),
    ],
)
def test_pdf_v2_builds_rotated_page_geometry_and_validates_page_locator(
    tmp_path: Path,
    rotation: int,
    expected_size: tuple[float, float],
):
    path = tmp_path / "rotated.pdf"
    document = fitz.open()
    page = document.new_page(width=200, height=300)
    page.insert_text((20, 40), "Hello rotated PDF")
    page.set_rotation(rotation)
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index
    assert index is not None
    page_record = index.pages[0]
    assert page_record.source_rotation == rotation
    assert (page_record.width, page_record.height) == expected_size
    assert index.fragments
    assert all(
        0 <= fragment.bbox.x0 < fragment.bbox.x1 <= page_record.width
        and 0 <= fragment.bbox.y0 < fragment.bbox.y1 <= page_record.height
        for fragment in index.fragments
    )

    requirement = RequirementIR(
        id="REQ-1",
        statement="Hello rotated PDF",
        sources=[
            SourceSpan(
                document_id=parsed.document_id,
                block_id=parsed.blocks[0].block_id,
                quote="Hello rotated PDF",
            )
        ],
    )
    registry = build_quote_match_registry(
        [requirement],
        parsed.blocks,
        evidence_fingerprint=index.evidence_fingerprint,
    )
    SourceEvidenceEnricher().enrich([requirement], index, registry, parsed.blocks)
    source = requirement.sources[0]
    assert source.page_locator is not None
    assert source.page_locator.source_rotation == rotation
    assert source.page_locator.derivation == "quote_span_union"
    validated, report, failures = SourceLocatorValidator().validate(
        [requirement],
        index,
        registry,
        policy="structured_required",
        document_blocks=parsed.blocks,
    )
    assert validated == [requirement]
    assert report.valid is True
    assert failures == []
    assert source.locator_status == "PASS_STRUCTURED"


def test_pdf_v2_uses_column_major_reading_order(tmp_path: Path):
    path = tmp_path / "columns.pdf"
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    page.insert_textbox(fitz.Rect(50, 100, 250, 140), "Left 1")
    page.insert_textbox(fitz.Rect(50, 200, 250, 240), "Left 2")
    page.insert_textbox(fitz.Rect(350, 100, 550, 140), "Right 1")
    page.insert_textbox(fitz.Rect(350, 200, 550, 240), "Right 2")
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    assert [block.text for block in parsed.blocks] == [
        "Left 1",
        "Left 2",
        "Right 1",
        "Right 2",
    ]
    assert [block.metadata["layout_column_index"] for block in parsed.blocks] == [
        1,
        1,
        2,
        2,
    ]
    assert parsed.evidence_index is not None
    assert parsed.evidence_index.pages[0].warnings == [
        "PDF_MULTI_COLUMN_ORDER_BEST_EFFORT: columns=2"
    ]
    assert parsed.warnings == [
        "PDF_MULTI_COLUMN_ORDER_BEST_EFFORT: page=1, columns=2"
    ]


def test_pdf_v2_keeps_column_order_with_one_geometryless_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "partial-geometry-columns.pdf"
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    page.insert_textbox(fitz.Rect(50, 100, 250, 140), "Left 1")
    page.insert_textbox(fitz.Rect(50, 200, 250, 240), "Left 2")
    page.insert_textbox(fitz.Rect(350, 100, 550, 140), "Right 1")
    page.insert_textbox(fitz.Rect(350, 200, 550, 240), "Right 2")
    document.save(path)
    document.close()

    original = pdf_parser_module._rotated_bbox
    call_count = 0

    def fail_first_bbox(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None
        return original(*args, **kwargs)

    monkeypatch.setattr(pdf_parser_module, "_rotated_bbox", fail_first_bbox)
    parsed = PdfParserV2().parse(path)

    assert [block.text for block in parsed.blocks] == [
        "Left 1",
        "Left 2",
        "Right 1",
        "Right 2",
    ]
    assert all(block.metadata["layout_column_count"] == 2 for block in parsed.blocks)
    assert parsed.blocks[0].metadata["page_region_available"] is False
    assert all(
        block.metadata["page_region_available"] is True
        for block in parsed.blocks[1:]
    )
    assert (
        "PDF_READING_ORDER_PARTIAL_GEOMETRY_FALLBACK: page=1"
        in parsed.warnings
    )
    assert (
        "PDF_MULTI_COLUMN_ORDER_BEST_EFFORT: page=1, columns=2"
        in parsed.warnings
    )


def test_pdf_v2_preserves_and_marks_repeated_headers_and_footers(tmp_path: Path):
    path = tmp_path / "headers.pdf"
    document = fitz.open()
    for page_number in range(1, 4):
        page = document.new_page(width=600, height=800)
        page.insert_text((50, 30), "Repeated Header")
        page.insert_text((50, 120), f"Body page {page_number}")
        page.insert_text((50, 785), "Repeated Footer")
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    assert [block.text for block in parsed.blocks] == [
        "Repeated Header",
        "Body page 1",
        "Repeated Footer",
        "Repeated Header",
        "Body page 2",
        "Repeated Footer",
        "Repeated Header",
        "Body page 3",
        "Repeated Footer",
    ]
    assert parsed.metadata["suppressed_repeated_edge_blocks"] == 0
    assert parsed.metadata["repeated_edge_candidate_blocks"] == 6
    assert parsed.evidence_index is not None
    assert all(
        page.warnings
        == [
            "PDF_REPEATED_HEADER_CANDIDATE",
            "PDF_REPEATED_FOOTER_CANDIDATE",
        ]
        for page in parsed.evidence_index.pages
    )
    assert all(
        block.metadata["repeated_edge_candidate"]
        for block in parsed.blocks
        if block.text in {"Repeated Header", "Repeated Footer"}
    )
    assert all(
        block.type == "paragraph"
        for block in parsed.blocks
        if block.text in {"Repeated Header", "Repeated Footer"}
    )


def test_pdf_v2_does_not_mark_two_page_repeated_edge_text(tmp_path: Path):
    path = tmp_path / "two-page-edge.pdf"
    document = fitz.open()
    for page_number in range(1, 3):
        page = document.new_page(width=600, height=800)
        page.insert_text((50, 30), "Potential requirement heading")
        page.insert_text((50, 120), f"Body page {page_number}")
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)

    assert len(parsed.blocks) == 4
    assert parsed.metadata["repeated_edge_candidate_blocks"] == 0
    assert not any("REPEATED_HEADER" in warning for warning in parsed.warnings)


def test_pdf_v2_does_not_claim_table_cell_without_proven_sparse_topology(
    tmp_path: Path,
):
    path = tmp_path / "table.pdf"
    document = fitz.open()
    page = document.new_page(width=400, height=300)
    for x in (50, 150, 300):
        page.draw_line((x, 50), (x, 150))
    for y in (50, 100, 150):
        page.draw_line((50, y), (300, y))
    for point, text in [
        ((60, 80), "A"),
        ((160, 80), "B"),
        ((60, 130), "C"),
        ((160, 130), "D"),
    ]:
        page.insert_text(point, text)
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index
    assert index is not None
    assert index.tables == []
    assert index.cells == []
    assert index.cell_occurrences == []
    assert all(
        block.expected_capabilities == ["text_range", "page_region"]
        and block.available_capabilities == ["text_range", "page_region"]
        for block in index.blocks
    )


def test_pdf_v2_keeps_text_and_downgrades_page_region_for_bad_span_bbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "bad-geometry.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "The system shall preserve recoverable PDF text.")
    document.save(path)
    document.close()

    original = pdf_parser_module._rotated_bbox
    call_count = 0

    def fail_first_bbox(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None
        return original(*args, **kwargs)

    monkeypatch.setattr(pdf_parser_module, "_rotated_bbox", fail_first_bbox)
    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index

    assert [block.text for block in parsed.blocks] == [
        "The system shall preserve recoverable PDF text."
    ]
    assert index is not None
    assert index.blocks[0].expected_capabilities == ["text_range", "page_region"]
    assert index.blocks[0].available_capabilities == ["text_range"]
    assert index.blocks[0].bbox is None
    assert index.blocks[0].fragment_ids == []
    assert index.fragments == []
    assert index.warnings == parsed.warnings
    assert "PDF_PAGE_REGION_UNAVAILABLE: page=1, source_block=1, segment=1" in (
        parsed.warnings
    )

    requirement = RequirementIR(
        id="REQ-1",
        statement=parsed.blocks[0].text,
        sources=[
            SourceSpan(
                document_id=parsed.document_id,
                block_id=parsed.blocks[0].block_id,
                quote=parsed.blocks[0].text,
            )
        ],
    )
    registry = build_quote_match_registry(
        [requirement],
        parsed.blocks,
        evidence_fingerprint=index.evidence_fingerprint,
    )
    SourceEvidenceEnricher().enrich([requirement], index, registry, parsed.blocks)
    validated, report, failures = SourceLocatorValidator().validate(
        [requirement],
        index,
        registry,
        policy="structured_if_available",
        document_blocks=parsed.blocks,
    )
    assert validated == [requirement]
    assert report.valid is True
    assert failures == []
    assert requirement.sources[0].text_locator is not None
    assert requirement.sources[0].page_locator is None
    assert requirement.sources[0].locator_status == "WARNING_UNAVAILABLE"


def test_pdf_v2_inserts_canonical_space_between_geometrically_separated_spans():
    page = _IdentityPage()
    raw_block = {
        "type": 0,
        "lines": [
            {
                "spans": [
                    {
                        "text": "🧪 The system",
                        "bbox": [10, 10, 70, 22],
                        "size": 11,
                        "flags": 0,
                    },
                    {
                        "text": "shall respond 𠀀\ufe0f",
                        "bbox": [80, 10, 170, 22],
                        "size": 11,
                        "flags": 16,
                    },
                ]
            }
        ],
    }

    blocks = _project_text_block(
        page,
        raw_block,
        page_index=1,
        page_width=300,
        page_height=200,
        source_block_number=1,
    )

    assert len(blocks) == 1
    assert blocks[0].text == "🧪 The system shall respond 𠀀\ufe0f"
    assert len(blocks[0].fragments) == 2
    assert blocks[0].fragments[1].separator_before == " "
    assert blocks[0].fragments[1].start == len("🧪 The system ")


def test_pdf_v2_splits_heading_and_paragraph_inside_raw_block():
    page = _IdentityPage()
    raw_block = {
        "type": 0,
        "lines": [
            {
                "spans": [
                    {
                        "text": "System Requirements",
                        "bbox": [10, 10, 180, 30],
                        "size": 18,
                        "flags": 16,
                    }
                ]
            },
            {
                "spans": [
                    {
                        "text": "The system shall respond.",
                        "bbox": [10, 55, 190, 67],
                        "size": 10,
                        "flags": 0,
                    }
                ]
            },
        ],
    }

    blocks = _project_text_block(
        page,
        raw_block,
        page_index=1,
        page_width=300,
        page_height=200,
        source_block_number=1,
    )

    assert [(block.block_type, block.text) for block in blocks] == [
        ("heading", "System Requirements"),
        ("paragraph", "The system shall respond."),
    ]
    assert [block.source_segment_number for block in blocks] == [1, 2]


def test_pdf_v2_builds_numeric_heading_hierarchy_and_replaces_siblings(
    tmp_path: Path,
):
    path = tmp_path / "sections.pdf"
    document = fitz.open()
    page = document.new_page(width=500, height=500)
    page.insert_text((50, 50), "1 Requirements", fontsize=18, fontname="hebo")
    page.insert_text((50, 100), "1.1 Interface", fontsize=15, fontname="hebo")
    page.insert_text((50, 130), "The system shall expose an API.")
    page.insert_text((50, 190), "1.2 Behavior", fontsize=15, fontname="hebo")
    page.insert_text((50, 220), "The system shall validate input.")
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    by_text = {block.text.strip(): block for block in parsed.blocks}

    assert by_text["1 Requirements"].type == "heading"
    assert by_text["1 Requirements"].metadata["level"] == 1
    assert by_text["1.1 Interface"].metadata["level"] == 2
    assert by_text["The system shall expose an API."].section_path == [
        "1 Requirements",
        "1.1 Interface",
    ]
    assert by_text["1.2 Behavior"].section_path == [
        "1 Requirements",
        "1.2 Behavior",
    ]
    assert by_text["The system shall validate input."].section_path == [
        "1 Requirements",
        "1.2 Behavior",
    ]


def test_pdf_v2_bold_labels_and_requirements_do_not_create_sections(
    tmp_path: Path,
):
    path = tmp_path / "bold-body-text.pdf"
    document = fitz.open()
    page = document.new_page(width=500, height=500)
    page.insert_text((50, 50), "System Requirements", fontname="hebo")
    page.insert_text((50, 90), "This section defines system behavior.")
    page.insert_text((50, 140), "Warning:", fontname="hebo")
    page.insert_text((50, 170), "Validate configuration before deployment.")
    page.insert_text(
        (50, 220),
        "The system shall preserve audit records.",
        fontname="hebo",
    )
    page.insert_text((50, 250), "Audit records remain available to reviewers.")
    page.insert_text((50, 300), "Status", fontname="hebo")
    page.insert_text((50, 340), "Architecture", fontname="hebo")
    page.insert_text((50, 370), "The service uses a layered architecture.")
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    by_text = {block.text.strip(): block for block in parsed.blocks}

    assert by_text["System Requirements"].type == "heading"
    assert by_text["Warning:"].type == "paragraph"
    assert by_text["The system shall preserve audit records."].type == "paragraph"
    assert by_text["Status"].type == "paragraph"
    assert by_text["Architecture"].type == "heading"
    assert by_text["Validate configuration before deployment."].section_path == [
        "System Requirements"
    ]
    assert by_text["Audit records remain available to reviewers."].section_path == [
        "System Requirements"
    ]
    assert by_text["The service uses a layered architecture."].section_path == [
        "Architecture"
    ]


def test_pdf_v2_quote_can_span_multiple_font_spans(tmp_path: Path):
    path = tmp_path / "multi-font.pdf"
    document = fitz.open()
    page = document.new_page(width=500, height=200)
    page.insert_htmlbox(
        fitz.Rect(50, 50, 450, 120),
        "<span>The system </span><b>shall respond</b>",
    )
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index
    assert index is not None
    block = next(item for item in parsed.blocks if "shall respond" in item.text)
    assert block.text == "The system shall respond"
    assert len(index.fragments) == 2

    requirement = RequirementIR(
        id="REQ-1",
        statement=block.text,
        sources=[
            SourceSpan(
                document_id=parsed.document_id,
                block_id=block.block_id,
                quote=block.text,
            )
        ],
    )
    registry = build_quote_match_registry(
        [requirement],
        parsed.blocks,
        evidence_fingerprint=index.evidence_fingerprint,
    )
    SourceEvidenceEnricher().enrich([requirement], index, registry, parsed.blocks)
    source = requirement.sources[0]
    assert source.text_locator is not None
    assert source.page_locator is not None
    assert source.page_locator.derivation == "quote_span_union"


def test_pdf_v2_uses_cropbox_preview_dimensions(tmp_path: Path):
    path = tmp_path / "cropbox.pdf"
    document = fitz.open()
    page = document.new_page(width=400, height=400)
    page.set_cropbox(fitz.Rect(50, 60, 350, 360))
    page.insert_text((20, 40), "CropBox text")
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index
    assert index is not None
    page_record = index.pages[0]
    assert (page_record.width, page_record.height) == (300.0, 300.0)
    assert all(
        0 <= fragment.bbox.x0 < fragment.bbox.x1 <= page_record.width
        and 0 <= fragment.bbox.y0 < fragment.bbox.y1 <= page_record.height
        for fragment in index.fragments
    )


def test_pdf_v2_fragment_bbox_overlays_rendered_preview_pixels(tmp_path: Path):
    path = tmp_path / "overlay.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=200)
    page.insert_text((40, 80), "Overlay target", fontsize=18)
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    assert parsed.evidence_index is not None
    bbox = parsed.evidence_index.fragments[0].bbox

    document = fitz.open(path)
    pixmap = document[0].get_pixmap(
        matrix=fitz.Matrix(2, 2),
        colorspace=fitz.csGRAY,
        alpha=False,
    )
    document.close()
    scale = 2
    tolerance = 2
    x0 = max(0, int(bbox.x0 * scale) - tolerance)
    y0 = max(0, int(bbox.y0 * scale) - tolerance)
    x1 = min(pixmap.width, int(bbox.x1 * scale) + tolerance + 1)
    y1 = min(pixmap.height, int(bbox.y1 * scale) + tolerance + 1)
    darkest = min(
        pixmap.samples[y * pixmap.stride + x]
        for y in range(y0, y1)
        for x in range(x0, x1)
    )
    assert darkest < 128


class _IdentityPage:
    rotation_matrix = fitz.Matrix(1, 0, 0, 1, 0, 0)
