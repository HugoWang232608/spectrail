from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

from spectrail.chunking import SectionAwareChunker
from spectrail.core.io import read_json
from spectrail.core.models import RequirementIR, SourceSpan
from spectrail.evidence import (
    build_quote_match_registry,
    build_table_evidence_view,
)
from spectrail.evidence.enricher import SourceEvidenceEnricher
from spectrail.evidence.index_builder import (
    ensure_evidence_index,
    validate_evidence_index_against_parsed_document,
)
from spectrail.evidence.pdf_preview import render_pdf_page
from spectrail.evidence.source_identity import canonicalize_source_cell_ids
from spectrail.extractors.reqir_extractor import ReqIRExtractor
from spectrail.llm.base import ModelRequest
from spectrail.llm.prompt_builder import build_reqir_prompt
from spectrail.parsers.base import DocumentParseError
from spectrail.parsers import pdf_parser as pdf_parser_module
from spectrail.parsers.pdf_parser import (
    PdfParserV2,
    TextPdfParser,
    _extract_page_tables,
    _project_text_block,
)
from spectrail.parsers.registry import parse_document
from spectrail.validators.source_locator_validator import SourceLocatorValidator
from spectrail.validators.source_quote_validator import SourceQuoteValidator


def _mark_cjk_font_as_bold(
    document: fitz.Document,
    page: fitz.Page,
    *,
    resource_name: str,
) -> None:
    type_zero_xref = next(
        font[0] for font in page.get_fonts(full=True) if font[4] == resource_name
    )
    descendant_value = document.xref_get_key(
        type_zero_xref,
        "DescendantFonts",
    )[1]
    descendant_xref = int(descendant_value.lstrip("[ ").split()[0])
    descriptor_value = document.xref_get_key(
        descendant_xref,
        "FontDescriptor",
    )[1]
    descriptor_xref = int(descriptor_value.split()[0])
    document.xref_set_key(type_zero_xref, "BaseFont", "/Heiti-Bold")
    document.xref_set_key(descendant_xref, "BaseFont", "/Heiti-Bold")
    document.xref_set_key(descriptor_xref, "FontName", "/Heiti-Bold")


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
    assert parsed.parser_identity.parser_version == "2.16"
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


def test_pdf_v2_checked_in_table_fixture_builds_structured_evidence():
    path = Path("tests/fixtures/pdf_table_requirements.pdf")
    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index
    assert index is not None
    table_block = next(block for block in parsed.blocks if block.type == "table")
    table_evidence = next(
        block for block in index.blocks if block.block_id == table_block.block_id
    )
    assert table_block.text == (
        "Requirement ID | Acceptance criterion | Owner\n"
        "REQ-001 | Approved within 2 seconds | Safety\n"
        "REQ-002 | Audit source evidence | QA"
    )
    assert table_evidence.expected_capabilities == [
        "text_range",
        "page_region",
        "table_cell",
    ]
    assert table_evidence.available_capabilities == [
        "text_range",
        "page_region",
        "table_cell",
    ]
    assert table_evidence.fragment_ids == []
    assert len(index.tables) == 1
    table = index.tables[0]
    assert table.parser_method == "pymupdf_find_tables"
    assert table.topology_status == "complete"
    assert (table.row_count, table.column_count) == (3, 3)
    assert table.block_ids == [table_block.block_id]
    assert index.pages[0].table_ids == [table.table_id]
    assert len(index.cells) == 9
    assert len(index.cell_occurrences) == 9
    assert parsed.metadata["table_count"] == 1
    assert parsed.metadata["table_cell_count"] == 9
    assert parsed.warnings == []
    assert [cell.is_header for cell in index.cells[:3]] == [True, True, True]
    assert all(cell.page == 1 and cell.bbox is not None for cell in index.cells)
    validate_evidence_index_against_parsed_document(index, parsed)

    projection = build_table_evidence_view(
        index,
        task_id="fixture-task",
        table_id=table.table_id,
        block_id=table_block.block_id,
    )
    assert projection.schema_version == "table_evidence_view_v1"
    assert [row.physical_row_index for row in projection.rows] == [1, 2, 3]
    assert [cell.text for cell in projection.rows[1].cells] == [
        "REQ-001",
        "Approved within 2 seconds",
        "Safety",
    ]
    visual_fixture = read_json(
        "frontend/src/fixtures/pdf-table-evidence.json"
    )
    assert visual_fixture["evidenceFingerprint"] == index.evidence_fingerprint
    assert visual_fixture["blocks"] == [table_block.model_dump(mode="json")]
    expected_visual_projection = build_table_evidence_view(
        index,
        task_id="visual-task",
        table_id=table.table_id,
        block_id=table_block.block_id,
    )
    assert visual_fixture["tableEvidence"] == (
        expected_visual_projection.model_dump(mode="json")
    )
    current_preview, _, _ = render_pdf_page(path, 1)
    checked_preview = Path(
        "frontend/tests/visual/fixtures/pdf-table-page.png"
    ).read_bytes()
    assert hashlib.sha256(checked_preview).hexdigest() == (
        hashlib.sha256(current_preview).hexdigest()
    )


def test_pdf_v2_checked_horizontal_merge_infers_column_span():
    path = Path("tests/fixtures/pdf_table_horizontal_merge.pdf")
    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index
    assert index is not None
    assert parsed.warnings == []
    assert parsed.blocks[0].text == (
        "Merged requirement header\nREQ-H | Approved"
    )
    assert len(index.tables) == 1
    assert [
        (
            cell.row_index,
            cell.column_index,
            cell.row_span,
            cell.column_span,
            cell.text,
        )
        for cell in index.cells
    ] == [
        (1, 1, 1, 2, "Merged requirement header"),
        (2, 1, 1, 1, "REQ-H"),
        (2, 2, 1, 1, "Approved"),
    ]
    assert [
        (occurrence.physical_row_index, occurrence.occurrence_role)
        for occurrence in index.cell_occurrences
    ] == [
        (1, "original"),
        (2, "original"),
        (2, "original"),
    ]

    table_block = parsed.blocks[0]
    merged_cell_id = "cell_00000001_r0001_c0001"
    prompt = build_reqir_prompt(
        ModelRequest(
            document_text=parsed.text,
            document_name=parsed.document_name,
            source_format=parsed.source_format,
            parser_name=parsed.parser_name,
            model_mode="mock",
            blocks=parsed.blocks,
            evidence_index=index,
            metadata={"evidence_policy": "structured_required"},
        )
    )
    assert "column_span=2" in prompt
    assert merged_cell_id in prompt

    requirement = ReqIRExtractor().extract(
        {
            "items": [
                {
                    "statement": (
                        "The system shall retain the merged requirement "
                        "header."
                    ),
                    "source_block_id": table_block.block_id,
                    "source_quote": "Merged requirement header",
                    "source_cell_ids": [merged_cell_id],
                    "source_table_row_index": 1,
                }
            ]
        },
        parsed.blocks,
        document_name=parsed.document_name,
    )[0]
    canonicalize_source_cell_ids([requirement], index)
    registry = build_quote_match_registry(
        [requirement],
        parsed.blocks,
        evidence_fingerprint=index.evidence_fingerprint,
        evidence_index=index,
    )
    SourceEvidenceEnricher().enrich(
        [requirement],
        index,
        registry,
        parsed.blocks,
    )
    quote_validated, quote_report = SourceQuoteValidator().validate(
        [requirement],
        parsed.blocks,
        registry,
    )
    locator_validated, locator_report, failures = (
        SourceLocatorValidator().validate(
            quote_validated,
            index,
            registry,
            policy="structured_required",
            document_blocks=parsed.blocks,
        )
    )
    source = requirement.sources[0]
    assert quote_report.valid is True
    assert locator_report.valid is True
    assert failures == []
    assert locator_validated == [requirement]
    assert source.locator_status == "PASS_STRUCTURED"
    assert source.table_locator is not None
    assert source.table_locator.cell_ids == [merged_cell_id]
    assert source.table_locator.selected_row_index == 1
    assert source.page_locator is not None
    assert source.page_locator.derivation == "table_cell_union"
    assert source.page_locator.bbox == source.table_locator.bbox

    projection = build_table_evidence_view(
        index,
        task_id="horizontal-merge-task",
        table_id=index.tables[0].table_id,
        block_id=table_block.block_id,
    )
    projected_merged = projection.rows[0].cells[0]
    assert projected_merged.cell_id == merged_cell_id
    assert projected_merged.column_span == 2
    assert projected_merged.occurrences[0].occurrence_role == "original"
    validate_evidence_index_against_parsed_document(index, parsed)


def test_pdf_v2_checked_vertical_merge_passes_structured_projection():
    path = Path("tests/fixtures/pdf_table_vertical_merge.pdf")
    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index
    assert index is not None
    table_block = parsed.blocks[0]
    assert table_block.text == (
        "Shared control | First state\n"
        "Shared control | Second state"
    )
    assert [
        (
            cell.row_index,
            cell.column_index,
            cell.row_span,
            cell.column_span,
            cell.text,
        )
        for cell in index.cells
    ] == [
        (1, 1, 2, 1, "Shared control"),
        (1, 2, 1, 1, "First state"),
        (2, 2, 1, 1, "Second state"),
    ]
    merged_cell_id = "cell_00000001_r0001_c0001"
    selected_cell_id = "cell_00000001_r0002_c0002"
    assert [
        (
            occurrence.cell_id,
            occurrence.physical_row_index,
            occurrence.occurrence_role,
        )
        for occurrence in index.cell_occurrences
    ] == [
        (merged_cell_id, 1, "original"),
        ("cell_00000001_r0001_c0002", 1, "original"),
        (merged_cell_id, 2, "row_span_projection"),
        (selected_cell_id, 2, "original"),
    ]

    prompt = build_reqir_prompt(
        ModelRequest(
            document_text=parsed.text,
            document_name=parsed.document_name,
            source_format=parsed.source_format,
            parser_name=parsed.parser_name,
            model_mode="mock",
            blocks=parsed.blocks,
            evidence_index=index,
            metadata={"evidence_policy": "structured_required"},
        )
    )
    assert "row_span=2" in prompt
    assert merged_cell_id in prompt
    assert selected_cell_id in prompt

    requirement = ReqIRExtractor().extract(
        {
            "items": [
                {
                    "statement": (
                        "The system shall preserve the shared control "
                        "in the second state."
                    ),
                    "source_block_id": table_block.block_id,
                    "source_quote": "Shared control | Second state",
                    "source_cell_ids": [
                        merged_cell_id,
                        selected_cell_id,
                    ],
                    "source_table_row_index": 2,
                }
            ]
        },
        parsed.blocks,
        document_name=parsed.document_name,
    )[0]
    canonicalize_source_cell_ids([requirement], index)
    registry = build_quote_match_registry(
        [requirement],
        parsed.blocks,
        evidence_fingerprint=index.evidence_fingerprint,
        evidence_index=index,
    )
    SourceEvidenceEnricher().enrich(
        [requirement],
        index,
        registry,
        parsed.blocks,
    )
    quote_validated, quote_report = SourceQuoteValidator().validate(
        [requirement],
        parsed.blocks,
        registry,
    )
    locator_validated, locator_report, failures = (
        SourceLocatorValidator().validate(
            quote_validated,
            index,
            registry,
            policy="structured_required",
            document_blocks=parsed.blocks,
        )
    )
    source = requirement.sources[0]
    assert quote_report.valid is True
    assert locator_report.valid is True
    assert failures == []
    assert locator_validated == [requirement]
    assert source.locator_status == "PASS_STRUCTURED"
    assert source.table_locator is not None
    assert source.table_locator.selected_row_index == 2
    assert source.table_locator.cell_ids == [
        merged_cell_id,
        selected_cell_id,
    ]
    assert source.page_locator is not None
    assert source.page_locator.derivation == "table_cell_union"
    assert source.page_locator.bbox == source.table_locator.bbox

    projection = build_table_evidence_view(
        index,
        task_id="merged-fixture-task",
        table_id=index.tables[0].table_id,
        block_id=table_block.block_id,
    )
    assert [row.physical_row_index for row in projection.rows] == [1, 2]
    projected_merged = projection.rows[1].cells[0]
    assert projected_merged.cell_id == merged_cell_id
    assert projected_merged.row_span == 2
    assert projected_merged.occurrences[0].occurrence_role == (
        "row_span_projection"
    )
    visual_fixture = read_json(
        "frontend/src/fixtures/pdf-merged-table-evidence.json"
    )
    assert visual_fixture["evidenceFingerprint"] == index.evidence_fingerprint
    assert visual_fixture["blocks"] == [
        table_block.model_dump(mode="json")
    ]
    expected_visual_projection = build_table_evidence_view(
        index,
        task_id="visual-task",
        table_id=index.tables[0].table_id,
        block_id=table_block.block_id,
    )
    assert visual_fixture["tableEvidence"] == (
        expected_visual_projection.model_dump(mode="json")
    )
    current_preview, _, _ = render_pdf_page(path, 1)
    checked_preview = Path(
        "frontend/tests/visual/fixtures/pdf-merged-table-page.png"
    ).read_bytes()
    assert hashlib.sha256(checked_preview).digest() == (
        hashlib.sha256(current_preview).digest()
    )
    validate_evidence_index_against_parsed_document(index, parsed)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_pdf_v2_merged_topology_survives_page_rotation(
    tmp_path: Path,
    rotation: int,
):
    source = fitz.open("tests/fixtures/pdf_table_vertical_merge.pdf")
    rotated = fitz.open()
    rotated.insert_pdf(source)
    source.close()
    rotated[0].set_rotation(rotation)
    path = tmp_path / f"pdf-table-vertical-merge-{rotation}.pdf"
    rotated.save(path)
    rotated.close()

    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index
    assert index is not None
    assert len(index.tables) == 1
    assert index.tables[0].topology_status == "complete"
    assert any(
        cell.row_span > 1 or cell.column_span > 1
        for cell in index.cells
    )
    table_block_ids = set(index.tables[0].block_ids)
    assert all(
        block.expected_capabilities
        == ["text_range", "page_region", "table_cell"]
        and block.available_capabilities
        == ["text_range", "page_region", "table_cell"]
        for block in index.blocks
        if block.block_id in table_block_ids
    )
    assert index.pages[0].source_rotation == rotation
    validate_evidence_index_against_parsed_document(index, parsed)


def test_pdf_v2_checked_ambiguous_merge_downgrades_table_cell():
    path = Path("tests/fixtures/pdf_table_ambiguous_merge.pdf")
    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index
    assert index is not None
    assert index.tables == []
    assert index.cells == []
    assert index.cell_occurrences == []
    assert any(
        "PDF_TABLE_MERGED_BOUNDARY_AMBIGUOUS: axis=x" in warning
        for warning in parsed.warnings
    )
    assert all(
        block.expected_capabilities
        == ["text_range", "page_region", "table_cell"]
        and block.available_capabilities == ["text_range", "page_region"]
        for block in index.blocks
    )

    source_block = next(
        block for block in parsed.blocks if "Ambiguous header" in block.text
    )
    requirement = RequirementIR(
        id="REQ-PDF-AMBIGUOUS-MERGE",
        statement="Ambiguous header",
        sources=[
            SourceSpan(
                document_id=parsed.document_id,
                block_id=source_block.block_id,
                quote="Ambiguous header",
            )
        ],
    )
    registry = build_quote_match_registry(
        [requirement],
        parsed.blocks,
        evidence_fingerprint=index.evidence_fingerprint,
        evidence_index=index,
    )
    SourceEvidenceEnricher().enrich(
        [requirement],
        index,
        registry,
        parsed.blocks,
    )
    optional, optional_report, optional_failures = (
        SourceLocatorValidator().validate(
            [requirement],
            index,
            registry,
            policy="structured_if_available",
            document_blocks=parsed.blocks,
        )
    )
    assert optional == [requirement]
    assert optional_report.valid is True
    assert optional_failures == []
    assert requirement.sources[0].locator_status == "WARNING_UNAVAILABLE"

    strict, strict_report, strict_failures = (
        SourceLocatorValidator().validate(
            [requirement],
            index,
            registry,
            policy="structured_required",
            document_blocks=parsed.blocks,
        )
    )
    assert strict == []
    assert strict_report.valid is False
    assert len(strict_failures) == 1


def test_pdf_v2_rejects_conflicting_text_from_bboxless_merged_slot(
    monkeypatch: pytest.MonkeyPatch,
):
    raw_table = SimpleNamespace(
        row_count=2,
        col_count=2,
        bbox=(50.0, 50.0, 300.0, 150.0),
        rows=[
            SimpleNamespace(
                cells=[
                    (50.0, 50.0, 175.0, 150.0),
                    (175.0, 50.0, 300.0, 100.0),
                ]
            ),
            SimpleNamespace(
                cells=[
                    None,
                    (175.0, 100.0, 300.0, 150.0),
                ]
            ),
        ],
        extract=lambda: [
            ["Shared", "First state"],
            ["Different", "Second state"],
        ],
        header=SimpleNamespace(
            names=["Shared", "First state"],
            external=False,
        ),
    )
    monkeypatch.setattr(
        fitz.Page,
        "find_tables",
        lambda self, **kwargs: SimpleNamespace(tables=[raw_table]),
    )
    document = fitz.open()
    page = document.new_page(width=400, height=300)
    projected, rejected, warnings = _extract_page_tables(
        page,
        page_width=400,
        page_height=300,
    )
    document.close()

    assert projected == []
    assert len(rejected) == 1
    assert rejected[0].reason == (
        "PDF_TABLE_MERGED_TEXT_CONFLICT: row=1, column=1"
    )
    assert warnings == [
        "PDF_TABLE_CELL_EVIDENCE_UNAVAILABLE: table=1, "
        "reason=PDF_TABLE_MERGED_TEXT_CONFLICT: row=1, column=1"
    ]


def test_pdf_v2_libreoffice_merged_table_corpus_passes_full_evidence_chain():
    path = Path("tests/fixtures/pdf_table_merged_libreoffice.pdf")
    manifest = read_json(
        "tests/fixtures/pdf_table_merged_libreoffice.manifest.json"
    )
    assert manifest["schema_version"] == "pdf_fixture_manifest_v2"
    assert manifest["fixture"] == path.name
    assert manifest["content_producer"]["name"] == "LibreOffice"
    assert manifest["content_producer"]["identity"].strip()
    assert manifest["source_builder"]["name"] == "python-docx"
    assert manifest["metadata_normalizer"]["name"] == "pypdf"
    constraints = Path("constraints-pdf-fixtures.txt").read_text(
        encoding="utf-8"
    )
    project_config = Path("pyproject.toml").read_text(encoding="utf-8")
    for package, version in (
        (
            manifest["source_builder"]["name"],
            manifest["source_builder"]["version"],
        ),
        (
            manifest["metadata_normalizer"]["name"],
            manifest["metadata_normalizer"]["version"],
        ),
    ):
        assert f"{package}=={version}" in constraints
    assert (
        f'"pypdf=={manifest["metadata_normalizer"]["version"]}"'
        in project_config
    )
    assert '"reportlab==4.4.9"' in project_config
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        manifest["pdf_sha256"]
    )
    assert [case["topology"] for case in manifest["cases"]] == [
        "horizontal_merge",
        "vertical_merge",
    ]

    producer_document = fitz.open(path)
    assert len(producer_document) == manifest["page_count"]
    assert manifest["page_count"] == len(manifest["cases"])
    assert producer_document.metadata["creator"] == (
        manifest["content_producer"]["identity"]
    )
    assert producer_document.metadata["producer"] == (
        f'{manifest["content_producer"]["identity"]}; '
        "metadata normalized by pypdf"
    )
    producer_document.close()

    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index
    assert index is not None
    assert parsed.warnings == []
    assert [
        {
            "page": table.page,
            "row_count": table.row_count,
            "column_count": table.column_count,
        }
        for table in index.tables
    ] == [
        {
            "page": case["page"],
            **case["expected_table"],
        }
        for case in manifest["cases"]
    ]
    for case in manifest["cases"]:
        assert [
            {
                "row_index": cell.row_index,
                "column_index": cell.column_index,
                "row_span": cell.row_span,
                "column_span": cell.column_span,
                "text": cell.text,
            }
            for cell in index.cells
            if cell.page == case["page"]
        ] == case["expected_cells"]
    validate_evidence_index_against_parsed_document(index, parsed)

    for case in manifest["cases"]:
        source_case = case["source"]
        projection_case = case["projection"]
        table = next(
            table for table in index.tables if table.page == case["page"]
        )
        block = next(
            block for block in parsed.blocks if block.page == case["page"]
        )
        requirement = ReqIRExtractor().extract(
            {
                "items": [
                    {
                        "statement": source_case["quote"],
                        "source_block_id": block.block_id,
                        "source_quote": source_case["quote"],
                        "source_cell_ids": source_case["cell_ids"],
                        "source_table_row_index": (
                            source_case["selected_row_index"]
                        ),
                    }
                ]
            },
            parsed.blocks,
            document_name=parsed.document_name,
        )[0]
        canonicalize_source_cell_ids([requirement], index)
        registry = build_quote_match_registry(
            [requirement],
            parsed.blocks,
            evidence_fingerprint=index.evidence_fingerprint,
            evidence_index=index,
        )
        SourceEvidenceEnricher().enrich(
            [requirement],
            index,
            registry,
            parsed.blocks,
        )
        quote_validated, quote_report = SourceQuoteValidator().validate(
            [requirement],
            parsed.blocks,
            registry,
        )
        locator_validated, locator_report, failures = (
            SourceLocatorValidator().validate(
                quote_validated,
                index,
                registry,
                policy="structured_required",
                document_blocks=parsed.blocks,
            )
        )
        source = requirement.sources[0]
        assert quote_report.valid is True
        assert locator_report.valid is True
        assert failures == []
        assert locator_validated == [requirement]
        assert source.locator_status == "PASS_STRUCTURED"
        assert source.table_locator is not None
        assert source.table_locator.cell_ids == source_case["cell_ids"]
        assert source.table_locator.selected_row_index == (
            source_case["selected_row_index"]
        )
        assert source.page_locator is not None
        assert source.page_locator.page == case["page"]
        assert source.page_locator.derivation == "table_cell_union"
        assert source.page_locator.bbox == source.table_locator.bbox

        projection = build_table_evidence_view(
            index,
            task_id="libreoffice-corpus-task",
            table_id=table.table_id,
            block_id=block.block_id,
        )
        selected_row = next(
            row
            for row in projection.rows
            if row.physical_row_index
            == source_case["selected_row_index"]
        )
        projected_owner = next(
            cell
            for cell in selected_row.cells
            if cell.cell_id == projection_case["owner_cell_id"]
        )
        assert getattr(projected_owner, projection_case["span_field"]) == (
            projection_case["span_value"]
        )
        assert projected_owner.occurrences[0].occurrence_role == (
            projection_case["occurrence_role"]
        )

        preview_png, preview_width, preview_height = render_pdf_page(
            path,
            case["page"],
        )
        pixmap = fitz.Pixmap(preview_png)
        page_locator = source.page_locator
        scale_x = preview_width / page_locator.page_width
        scale_y = preview_height / page_locator.page_height
        assert scale_x == pytest.approx(scale_y)
        bbox = page_locator.bbox
        tolerance = 2
        x0 = max(0, int(bbox.x0 * scale_x) - tolerance)
        y0 = max(0, int(bbox.y0 * scale_y) - tolerance)
        x1 = min(pixmap.width, int(bbox.x1 * scale_x) + tolerance + 1)
        y1 = min(pixmap.height, int(bbox.y1 * scale_y) + tolerance + 1)
        darkest = min(
            pixmap.samples[y * pixmap.stride + x * pixmap.n + channel]
            for y in range(y0, y1)
            for x in range(x0, x1)
            for channel in range(min(3, pixmap.n))
        )
        assert darkest < 128


def test_pdf_v2_table_source_passes_structured_locator_validation():
    path = Path("tests/fixtures/pdf_table_requirements.pdf")
    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index
    assert index is not None
    table_block = next(block for block in parsed.blocks if block.type == "table")
    source_cell_ids = [
        "cell_00000001_r0002_c0001",
        "cell_00000001_r0002_c0002",
    ]
    prompt = build_reqir_prompt(
        ModelRequest(
            document_text=parsed.text,
            document_name=parsed.document_name,
            source_format=parsed.source_format,
            parser_name=parsed.parser_name,
            model_mode="mock",
            blocks=parsed.blocks,
            evidence_index=index,
            metadata={"evidence_policy": "structured_required"},
        )
    )
    assert "row 2:" in prompt
    assert all(cell_identifier in prompt for cell_identifier in source_cell_ids)

    requirement = ReqIRExtractor().extract(
        {
            "items": [
                {
                    "statement": (
                        "The system shall approve the request within 2 seconds."
                    ),
                    "source_block_id": table_block.block_id,
                    "source_quote": "REQ-001 | Approved within 2 seconds",
                    "source_cell_ids": source_cell_ids,
                    "source_table_row_index": 2,
                }
            ]
        },
        parsed.blocks,
        document_name=parsed.document_name,
    )[0]
    canonicalize_source_cell_ids([requirement], index)
    registry = build_quote_match_registry(
        [requirement],
        parsed.blocks,
        evidence_fingerprint=index.evidence_fingerprint,
        evidence_index=index,
    )
    SourceEvidenceEnricher().enrich(
        [requirement],
        index,
        registry,
        parsed.blocks,
    )
    quote_validated, quote_report = SourceQuoteValidator().validate(
        [requirement],
        parsed.blocks,
        registry,
    )
    locator_validated, locator_report, failures = (
        SourceLocatorValidator().validate(
            quote_validated,
            index,
            registry,
            policy="structured_required",
            document_blocks=parsed.blocks,
        )
    )

    source = requirement.sources[0]
    assert quote_report.valid is True
    assert locator_report.valid is True
    assert failures == []
    assert locator_validated == [requirement]
    assert source.locator_status == "PASS_STRUCTURED"
    assert {
        result.capability: result.status
        for result in source.capability_results
    } == {
        "text_range": "PASS",
        "page_region": "PASS",
        "table_cell": "PASS",
    }
    assert source.table_locator is not None
    assert source.table_locator.cell_ids == source_cell_ids
    assert source.table_locator.selected_row_index == 2
    assert source.table_locator.bbox is not None
    assert source.page_locator is not None
    assert source.page_locator.derivation == "table_cell_union"
    assert source.page_locator.bbox == source.table_locator.bbox


def test_pdf_v2_rejected_table_marks_only_related_mixed_page_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    def span(
        text: str,
        bbox: tuple[float, float, float, float] | None,
    ) -> dict:
        return {
            "text": text,
            "bbox": bbox,
            "size": 11,
            "flags": 0,
        }

    raw_blocks = [
        {
            "type": 0,
            "lines": [{"spans": [span("Table title", (60, 20, 140, 35))]}],
        },
        {
            "type": 0,
            "lines": [{"spans": [span("Inside table", (60, 60, 140, 75))]}],
        },
        {
            "type": 0,
            "lines": [
                {
                    "spans": [
                        span("Table tail", (60, 130, 140, 145)),
                        span("Outside explanation", (310, 130, 390, 145)),
                    ]
                }
            ],
        },
        {
            "type": 0,
            "lines": [
                {"spans": [span("Adjacent caption", (60, 160, 180, 175))]}
            ],
        },
        {
            "type": 0,
            "lines": [{"spans": [span("Geometry unavailable", None)]}],
        },
    ]
    raw_table = SimpleNamespace(
        row_count=1,
        col_count=2,
        bbox=(50.0, 50.0, 300.0, 150.0),
        rows=[
            SimpleNamespace(
                cells=[
                    (50.0, 50.0, 175.0, 150.0),
                    None,
                ]
            )
        ],
        extract=lambda: [["A", "B"]],
        header=SimpleNamespace(names=["A", "B"], external=False),
    )

    class FakePage:
        rect = SimpleNamespace(width=400.0, height=300.0)
        rotation = 0
        rotation_matrix = fitz.Matrix(1, 0, 0, 1, 0, 0)

        def get_text(self, *args, **kwargs):
            return {"blocks": raw_blocks}

        def find_tables(self, **kwargs):
            return SimpleNamespace(tables=[raw_table])

    class FakeDocument:
        def __iter__(self):
            return iter([FakePage()])

        def close(self):
            return None

    source_path = tmp_path / "mixed-rejected-table.pdf"
    source_path.write_bytes(b"%PDF-1.7 mixed-page test fixture")
    monkeypatch.setattr(fitz, "open", lambda *args, **kwargs: FakeDocument())

    parsed = PdfParserV2().parse(source_path)
    index = parsed.evidence_index
    assert index is not None
    evidence_by_text = {
        block.text: next(
            evidence
            for evidence in index.blocks
            if evidence.block_id == block.block_id
        )
        for block in parsed.blocks
    }
    blocks_by_text = {block.text: block for block in parsed.blocks}

    for text in ("Inside table", "Table tail Outside explanation"):
        assert evidence_by_text[text].expected_capabilities == [
            "text_range",
            "page_region",
            "table_cell",
        ]
        assert evidence_by_text[text].available_capabilities == [
            "text_range",
            "page_region",
        ]
        assert blocks_by_text[text].metadata["rejected_table_candidates"] == [1]

    mixed_bbox = evidence_by_text["Table tail Outside explanation"].bbox
    assert mixed_bbox is not None
    intersection_area = (
        (min(mixed_bbox.x1, 300.0) - max(mixed_bbox.x0, 50.0))
        * (min(mixed_bbox.y1, 150.0) - max(mixed_bbox.y0, 50.0))
    )
    mixed_block_area = (
        (mixed_bbox.x1 - mixed_bbox.x0)
        * (mixed_bbox.y1 - mixed_bbox.y0)
    )
    assert intersection_area / mixed_block_area < 0.80

    for text in ("Table title", "Adjacent caption", "Geometry unavailable"):
        assert evidence_by_text[text].expected_capabilities == [
            "text_range",
            "page_region",
        ]
        assert blocks_by_text[text].metadata["rejected_table_candidates"] == []
    assert evidence_by_text["Geometry unavailable"].available_capabilities == [
        "text_range"
    ]


@pytest.mark.parametrize(
    ("table_bbox", "cell_boxes", "expected_reason"),
    [
        (
            (50.0, 50.0, 300.0, 150.0),
            [
                [(40.0, 50.0, 150.0, 100.0), (150.0, 50.0, 300.0, 100.0)],
                [(50.0, 100.0, 150.0, 150.0), (150.0, 100.0, 300.0, 150.0)],
            ],
            "outside table bbox",
        ),
        (
            (50.0, 50.0, 300.0, 150.0),
            [
                [(60.0, 50.0, 150.0, 100.0), (150.0, 50.0, 300.0, 100.0)],
                [(60.0, 100.0, 150.0, 150.0), (150.0, 100.0, 300.0, 150.0)],
            ],
            "does not cover the detected table bbox",
        ),
        (
            (50.0, 50.0, 300.0, 150.0),
            [
                [(50.0, 50.0, 150.0, 100.0), (150.0, 55.0, 300.0, 100.0)],
                [(50.0, 100.0, 150.0, 150.0), (150.0, 100.0, 300.0, 150.0)],
            ],
            "row boundaries are not aligned",
        ),
        (
            (50.0, 50.0, 300.0, 150.0),
            [
                [(50.0, 50.0, 150.0, 100.0), (150.0, 50.0, 300.0, 100.0)],
                [(50.0, 100.0, 160.0, 150.0), (160.0, 100.0, 300.0, 150.0)],
            ],
            "column boundaries are not aligned",
        ),
        (
            (50.0, 50.0, 300.0, 150.0),
            [
                [(50.0, 50.0, 145.0, 100.0), (155.0, 50.0, 300.0, 100.0)],
                [(50.0, 100.0, 150.0, 150.0), (150.0, 100.0, 300.0, 150.0)],
            ],
            "column boundaries contain a gap",
        ),
        (
            (50.0, 50.0, 300.0, 150.0),
            [
                [(50.0, 50.0, 150.0, 95.0), (150.0, 50.0, 300.0, 95.0)],
                [(50.0, 105.0, 150.0, 150.0), (150.0, 105.0, 300.0, 150.0)],
            ],
            "row boundaries contain a gap",
        ),
        (
            (50.0, 50.0, 300.0, 150.0),
            [
                [(50.0, 50.0, 160.0, 100.0), (150.0, 50.0, 300.0, 100.0)],
                [(50.0, 100.0, 150.0, 150.0), (150.0, 100.0, 300.0, 150.0)],
            ],
            "overlap in physical PDF space",
        ),
    ],
)
def test_pdf_v2_rejects_invalid_physical_cell_geometry(
    monkeypatch: pytest.MonkeyPatch,
    table_bbox: tuple[float, float, float, float],
    cell_boxes: list[list[tuple[float, float, float, float]]],
    expected_reason: str,
):
    raw_table = SimpleNamespace(
        row_count=2,
        col_count=2,
        bbox=table_bbox,
        rows=[
            SimpleNamespace(cells=row)
            for row in cell_boxes
        ],
        extract=lambda: [["A", "B"], ["C", "D"]],
        header=SimpleNamespace(
            names=["A", "B"],
            external=False,
        ),
    )
    monkeypatch.setattr(
        fitz.Page,
        "find_tables",
        lambda self, **kwargs: SimpleNamespace(tables=[raw_table]),
    )
    document = fitz.open()
    page = document.new_page(width=400, height=300)
    projected, rejected, warnings = _extract_page_tables(
        page,
        page_width=400,
        page_height=300,
    )
    document.close()

    assert projected == []
    assert len(rejected) == 1
    assert rejected[0].source_table_number == 1
    assert rejected[0].reason in warnings[0]
    assert len(warnings) == 1
    assert warnings[0].startswith(
        "PDF_TABLE_CELL_EVIDENCE_UNAVAILABLE: table=1"
    )
    assert expected_reason in warnings[0]


def test_pdf_v2_groups_large_table_and_projects_header(tmp_path: Path):
    path = tmp_path / "large-table.pdf"
    document = fitz.open()
    page = document.new_page(width=400, height=800)
    row_count = 22
    row_height = 30
    table_top = 40
    for x in (40, 140, 360):
        page.draw_line((x, table_top), (x, table_top + row_count * row_height))
    for row_index in range(row_count + 1):
        y = table_top + row_index * row_height
        page.draw_line((40, y), (360, y))
    page.insert_text((50, 60), "ID")
    page.insert_text((150, 60), "Value")
    for row_index in range(1, row_count):
        baseline = table_top + row_index * row_height + 20
        page.insert_text((50, baseline), f"R{row_index:02d}")
        page.insert_text((150, baseline), f"Value {row_index:02d}")
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index
    assert index is not None
    assert len(index.tables) == 1
    table = index.tables[0]
    assert table.row_count == 22
    assert table.block_ids == ["blk_0001", "blk_0002"]
    assert table.warnings == ["PDF_REPEATED_HEADER_PROJECTED"]
    assert [
        (block.table_row_start, block.table_row_end)
        for block in index.blocks
    ] == [(1, 20), (21, 22)]
    assert parsed.blocks[1].text.startswith("ID | Value\nR20 | Value 20")
    repeated = [
        occurrence
        for occurrence in index.cell_occurrences
        if occurrence.block_id == "blk_0002"
        and occurrence.occurrence_role == "repeated_header"
    ]
    assert [occurrence.cell_id for occurrence in repeated] == [
        "cell_00000001_r0001_c0001",
        "cell_00000001_r0001_c0002",
    ]
    validate_evidence_index_against_parsed_document(index, parsed)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_pdf_v2_table_cell_union_overlays_rotated_preview_pixels(
    tmp_path: Path,
    rotation: int,
):
    path = tmp_path / f"rotated-table-{rotation}.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=200)
    for x in (40, 120, 280):
        page.draw_line((x, 50), (x, 150))
    for y in (50, 100, 150):
        page.draw_line((40, y), (280, y))
    page.insert_text((50, 80), "Key")
    page.insert_text((130, 80), "Requirement")
    page.insert_text((50, 130), "R1")
    page.insert_text((130, 130), "Rotated table target")
    page.set_rotation(rotation)
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index
    assert index is not None
    table_block = next(block for block in parsed.blocks if block.type == "table")
    selected_row_index = 1
    selected_cells = sorted(
        (
            cell
            for cell in index.cells
            if cell.row_index == selected_row_index
        ),
        key=lambda cell: cell.column_index,
    )
    source_cell_ids = [
        cell.cell_id for cell in selected_cells
    ]
    source_quote = table_block.text.splitlines()[0]
    requirement = RequirementIR(
        id=f"REQ-PDF-TABLE-{rotation}",
        statement=source_quote,
        sources=[
            SourceSpan(
                document_id=parsed.document_id,
                block_id=table_block.block_id,
                quote=source_quote,
                source_cell_ids_raw=source_cell_ids,
                canonical_source_cell_ids=source_cell_ids,
                source_table_row_index=selected_row_index,
            )
        ],
    )
    registry = build_quote_match_registry(
        [requirement],
        parsed.blocks,
        evidence_fingerprint=index.evidence_fingerprint,
        evidence_index=index,
    )
    SourceEvidenceEnricher().enrich(
        [requirement],
        index,
        registry,
        parsed.blocks,
    )
    validated, report, failures = SourceLocatorValidator().validate(
        [requirement],
        index,
        registry,
        policy="structured_required",
        document_blocks=parsed.blocks,
    )
    source = requirement.sources[0]
    assert validated == [requirement]
    assert report.valid is True
    assert failures == []
    assert source.locator_status == "PASS_STRUCTURED"
    assert source.table_locator is not None
    assert source.table_locator.bbox is not None
    assert source.page_locator is not None
    assert source.page_locator.source_rotation == rotation
    assert source.page_locator.derivation == "table_cell_union"
    assert source.page_locator.bbox == source.table_locator.bbox

    preview_png, preview_width, preview_height = render_pdf_page(path, 1)
    pixmap = fitz.Pixmap(preview_png)
    scale_x = preview_width / source.page_locator.page_width
    scale_y = preview_height / source.page_locator.page_height
    assert scale_x == pytest.approx(scale_y)
    bbox = source.page_locator.bbox
    tolerance = 2
    x0 = max(0, int(bbox.x0 * scale_x) - tolerance)
    y0 = max(0, int(bbox.y0 * scale_y) - tolerance)
    x1 = min(pixmap.width, int(bbox.x1 * scale_x) + tolerance + 1)
    y1 = min(pixmap.height, int(bbox.y1 * scale_y) + tolerance + 1)
    darkest = min(
        pixmap.samples[y * pixmap.stride + x * pixmap.n + channel]
        for y in range(y0, y1)
        for x in range(x0, x1)
        for channel in range(min(3, pixmap.n))
    )
    assert darkest < 128


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


def test_pdf_v2_bold_heading_can_be_confirmed_by_next_page_body(
    tmp_path: Path,
):
    path = tmp_path / "cross-page-heading.pdf"
    document = fitz.open()
    page_one = document.new_page(width=500, height=800)
    page_one.insert_text(
        (50, 760),
        "System Interfaces",
        fontname="hebo",
    )
    page_two = document.new_page(width=500, height=800)
    page_two.insert_text(
        (50, 50),
        "Interfaces are exposed through authenticated endpoints.",
    )
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    by_text = {block.text.strip(): block for block in parsed.blocks}

    assert by_text["System Interfaces"].type == "heading"
    assert by_text[
        "Interfaces are exposed through authenticated endpoints."
    ].section_path == ["System Interfaces"]


def test_pdf_v2_cross_page_heading_skips_bold_page_number_footer(
    tmp_path: Path,
):
    path = tmp_path / "cross-page-heading-with-footer.pdf"
    document = fitz.open()
    page_one = document.new_page(width=500, height=800)
    page_one.insert_text(
        (50, 680),
        "System Interfaces",
        fontname="hebo",
    )
    page_one.insert_text((230, 790), "PAGE 1", fontname="hebo")
    page_two = document.new_page(width=500, height=800)
    page_two.insert_text(
        (50, 50),
        "Interfaces are exposed through authenticated endpoints.",
    )
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index
    assert index is not None
    by_text = {block.text.strip(): block for block in parsed.blocks}

    assert by_text["System Interfaces"].type == "heading"
    assert by_text[
        "Interfaces are exposed through authenticated endpoints."
    ].section_path == ["System Interfaces"]
    assert by_text["PAGE 1"].metadata["repeated_edge_candidate"] is False
    assert by_text["PAGE 1"].type == "paragraph"
    assert any(
        evidence.block_id == by_text["PAGE 1"].block_id
        for evidence in index.blocks
    )


def test_pdf_v2_cross_page_heading_skips_repeated_bold_header(
    tmp_path: Path,
):
    path = tmp_path / "cross-page-heading-with-repeated-header.pdf"
    document = fitz.open()
    for page_number in range(1, 4):
        page = document.new_page(width=500, height=800)
        page.insert_text(
            (50, 30),
            "ACME SYSTEM SPECIFICATION",
            fontname="hebo",
        )
        if page_number == 1:
            page.insert_text(
                (50, 680),
                "System Interfaces",
                fontname="hebo",
            )
        elif page_number == 2:
            page.insert_text(
                (50, 80),
                "Interfaces are exposed through authenticated endpoints.",
            )
        else:
            page.insert_text((50, 120), "Additional interface details.")
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    headers = [
        block
        for block in parsed.blocks
        if block.text.strip() == "ACME SYSTEM SPECIFICATION"
    ]
    by_text = {block.text.strip(): block for block in parsed.blocks}

    assert len(headers) == 3
    assert all(block.metadata["repeated_edge_candidate"] for block in headers)
    assert all(block.type == "paragraph" for block in headers)
    assert by_text["System Interfaces"].type == "heading"
    assert by_text[
        "Interfaces are exposed through authenticated endpoints."
    ].section_path == ["System Interfaces"]


def test_pdf_v2_cross_page_heading_skips_bold_chinese_page_number(
    tmp_path: Path,
):
    path = tmp_path / "cross-page-heading-with-chinese-footer.pdf"
    document = fitz.open()
    page_one = document.new_page(width=500, height=800)
    page_one.insert_text(
        (50, 680),
        "System Interfaces",
        fontname="hebo",
    )
    page_one.insert_text(
        (230, 790),
        "第 1 页",
        fontname="china-s",
    )
    _mark_cjk_font_as_bold(
        document,
        page_one,
        resource_name="china-s",
    )
    page_two = document.new_page(width=500, height=800)
    page_two.insert_text(
        (50, 50),
        "Interfaces are exposed through authenticated endpoints.",
    )
    document.save(path)
    document.close()

    raw_document = fitz.open(path)
    chinese_span = next(
        span
        for block in raw_document[0].get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        if span["text"] == "第 1 页"
    )
    assert chinese_span["flags"] & 16
    raw_document.close()

    parsed = PdfParserV2().parse(path)
    by_text = {block.text.strip(): block for block in parsed.blocks}

    assert by_text["第 1 页"].type == "paragraph"
    assert by_text["System Interfaces"].type == "heading"
    assert by_text[
        "Interfaces are exposed through authenticated endpoints."
    ].section_path == ["System Interfaces"]


@pytest.mark.parametrize(
    "text",
    [
        "PAGE 1",
        "Page 1 of 20",
        "第 1 页",
        "第 1 / 20 页",
        "CONFIDENTIAL",
        "机密",
        "内部资料",
        "Document ID: ABC-001",
        "文档编号：ABC-001",
    ],
)
def test_pdf_v2_recognizes_multilingual_edge_decoration_patterns(text: str):
    assert pdf_parser_module.EDGE_DECORATION_RE.fullmatch(text) is not None


@pytest.mark.parametrize("text", ["2 Architecture", "2.1 Interfaces", "架构设计"])
def test_pdf_v2_edge_decoration_patterns_do_not_match_real_headings(text: str):
    assert pdf_parser_module.EDGE_DECORATION_RE.fullmatch(text) is None


def test_pdf_v2_cross_page_heading_stops_at_next_page_numbered_heading(
    tmp_path: Path,
):
    path = tmp_path / "cross-page-numbered-heading.pdf"
    document = fitz.open()
    page_one = document.new_page(width=500, height=800)
    page_one.insert_text(
        (50, 680),
        "Interface Notes",
        fontname="hebo",
    )
    page_two = document.new_page(width=500, height=800)
    page_two.insert_text(
        (50, 30),
        "2 Architecture",
        fontname="hebo",
    )
    page_two.insert_text(
        (50, 80),
        "Architecture body begins below the numbered heading.",
    )
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    by_text = {block.text.strip(): block for block in parsed.blocks}

    assert by_text["Interface Notes"].type == "paragraph"
    assert by_text["2 Architecture"].type == "heading"
    assert by_text[
        "Architecture body begins below the numbered heading."
    ].section_path == ["2 Architecture"]


def test_pdf_v2_cross_page_heading_rejects_different_horizontal_region(
    tmp_path: Path,
):
    path = tmp_path / "cross-page-different-columns.pdf"
    document = fitz.open()
    page_one = document.new_page(width=500, height=800)
    page_one.insert_text(
        (330, 760),
        "Right Column Label",
        fontname="hebo",
    )
    page_two = document.new_page(width=500, height=800)
    page_two.insert_text(
        (50, 50),
        "Left column body starts on the following page.",
    )
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    by_text = {block.text.strip(): block for block in parsed.blocks}

    assert by_text["Right Column Label"].type == "paragraph"
    assert by_text[
        "Left column body starts on the following page."
    ].section_path == []


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


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_pdf_v2_page_locator_overlays_rendered_preview_pixels(
    tmp_path: Path,
    rotation: int,
):
    path = tmp_path / "overlay.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=200)
    page.insert_text((40, 80), "Overlay target", fontsize=18)
    page.set_rotation(rotation)
    document.save(path)
    document.close()

    parsed = PdfParserV2().parse(path)
    index = parsed.evidence_index
    assert index is not None
    requirement = RequirementIR(
        id="REQ-OVERLAY",
        statement="Overlay target",
        sources=[
            SourceSpan(
                document_id=parsed.document_id,
                block_id=parsed.blocks[0].block_id,
                quote="Overlay target",
            )
        ],
    )
    registry = build_quote_match_registry(
        [requirement],
        parsed.blocks,
        evidence_fingerprint=index.evidence_fingerprint,
    )
    SourceEvidenceEnricher().enrich([requirement], index, registry, parsed.blocks)
    locator = requirement.sources[0].page_locator
    assert locator is not None
    assert locator.source_rotation == rotation

    preview_png, preview_width, preview_height = render_pdf_page(path, 1)
    pixmap = fitz.Pixmap(preview_png)
    assert (pixmap.width, pixmap.height) == (preview_width, preview_height)
    scale_x = preview_width / locator.page_width
    scale_y = preview_height / locator.page_height
    assert scale_x == pytest.approx(scale_y)

    bbox = locator.bbox
    tolerance = 2
    x0 = max(0, int(bbox.x0 * scale_x) - tolerance)
    y0 = max(0, int(bbox.y0 * scale_y) - tolerance)
    x1 = min(pixmap.width, int(bbox.x1 * scale_x) + tolerance + 1)
    y1 = min(pixmap.height, int(bbox.y1 * scale_y) + tolerance + 1)
    darkest = min(
        pixmap.samples[y * pixmap.stride + x * pixmap.n + channel]
        for y in range(y0, y1)
        for x in range(x0, x1)
        for channel in range(min(3, pixmap.n))
    )
    assert darkest < 128


class _IdentityPage:
    rotation_matrix = fitz.Matrix(1, 0, 0, 1, 0, 0)
