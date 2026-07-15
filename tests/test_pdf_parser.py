from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from spectrail.core.models import RequirementIR, SourceSpan
from spectrail.evidence import build_quote_match_registry
from spectrail.evidence.enricher import SourceEvidenceEnricher
from spectrail.evidence.index_builder import (
    ensure_evidence_index,
    validate_evidence_index_against_parsed_document,
)
from spectrail.parsers.base import DocumentParseError
from spectrail.parsers.pdf_parser import PdfParserV2, TextPdfParser
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
    assert parsed.warnings == []
    assert {block.page for block in parsed.blocks}
    assert all(block.metadata["page"] == block.page for block in parsed.blocks)
    assert len(parsed.text) > 1000


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


def test_pdf_v2_builds_rotated_page_geometry_and_validates_page_locator(
    tmp_path: Path,
):
    path = tmp_path / "rotated.pdf"
    document = fitz.open()
    page = document.new_page(width=200, height=300)
    page.insert_text((20, 40), "Hello rotated PDF")
    page.set_rotation(90)
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index
    assert index is not None
    page_record = index.pages[0]
    assert page_record.source_rotation == 90
    assert (page_record.width, page_record.height) == (300.0, 200.0)
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
    assert source.page_locator.source_rotation == 90
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
        "PDF_MULTI_COLUMN_LAYOUT_DETECTED: columns=2"
    ]


def test_pdf_v2_suppresses_repeated_headers_and_footers(tmp_path: Path):
    path = tmp_path / "headers.pdf"
    document = fitz.open()
    for page_number in range(1, 3):
        page = document.new_page(width=600, height=800)
        page.insert_text((50, 30), "Repeated Header")
        page.insert_text((50, 120), f"Body page {page_number}")
        page.insert_text((50, 785), "Repeated Footer")
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    assert [block.text for block in parsed.blocks] == ["Body page 1", "Body page 2"]
    assert parsed.metadata["suppressed_repeated_edge_blocks"] == 4
    assert parsed.evidence_index is not None
    assert all(
        page.warnings
        == [
            "PDF_REPEATED_HEADER_SUPPRESSED",
            "PDF_REPEATED_FOOTER_SUPPRESSED",
        ]
        for page in parsed.evidence_index.pages
    )


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
