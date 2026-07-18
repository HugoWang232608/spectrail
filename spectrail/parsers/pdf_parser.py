from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError as DistributionNotFoundError
from importlib.metadata import version as distribution_version
import math
import re
from statistics import median
from pathlib import Path

from spectrail.core.ids import block_id
from spectrail.core.models import DocumentBlock
from spectrail.evidence.fingerprint import (
    finalize_evidence_fingerprint,
    sha256_file,
    sha256_text,
)
from spectrail.evidence.ids import (
    cell_id,
    fragment_id,
    occurrence_id,
    page_id,
    table_id,
)
from spectrail.evidence.models import (
    BlockEvidenceRecord,
    BoundingBox,
    CellBlockOccurrence,
    EvidenceIndex,
    PageRecord,
    ParserIdentity,
    TableCellRecord,
    TableRecord,
    TextFragmentRecord,
)
from spectrail.parsers.base import DocumentParseError, ParsedDocument
from spectrail.parsers.render import render_blocks_to_markdown


LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|[•‣]\s*|\d+[.)]\s+).+")
EDGE_REGION_RATIO = 0.08
WIDE_BLOCK_RATIO = 0.70
COLUMN_OVERLAP_RATIO = 0.20
MIN_REPEATED_EDGE_PAGES = 3
EDGE_POSITION_TOLERANCE = 0.01
EDGE_DECORATION_MAX_CHARS = 40
EDGE_DECORATION_MAX_WORDS = 8
PARAGRAPH_GAP_RATIO = 0.60
FONT_LEVEL_DELTA_RATIO = 0.20
MIN_FONT_LEVEL_DELTA = 2.0
SPAN_GAP_EM_RATIO = 0.12
MIN_SPAN_GAP = 0.75
BOLD_HEADING_MAX_CHARS = 80
BOLD_HEADING_MAX_WORDS = 12
BOLD_HEADING_BODY_GAP_POINTS = 36.0
BOLD_HEADING_BODY_GAP_EM_RATIO = 3.0
CROSS_PAGE_HEADING_BOTTOM_RATIO = 0.80
CROSS_PAGE_BODY_TOP_RATIO = 0.20
CROSS_PAGE_HORIZONTAL_OVERLAP_RATIO = 0.50
NORMATIVE_SENTENCE_RE = re.compile(r"\b(?:shall|must|should|will)\b", re.IGNORECASE)
NUMBERED_HEADING_RE = re.compile(r"^\s*\d+(?:\.\d+)*(?:[.)]|\s)")
EDGE_DECORATION_RE = re.compile(
    r"^(?:page\s+)?\d+\s*(?:(?:/|of)\s*\d+)?$"
    r"|^第\s*\d+\s*(?:/\s*\d+\s*)?页$"
    r"|^(?:confidential|proprietary|internal use only)$"
    r"|^(?:机密|内部资料|内部使用)$"
    r"|^document\s+(?:id|no\.?|number)\s*:\s*\S.*$"
    r"|^文档(?:编号|号)\s*[：:]\s*\S.*$",
    re.IGNORECASE,
)
BOLD_LABEL_RE = re.compile(
    r"^(?:note|warning|caution|input|output|example|tip|status|owner|"
    r"rationale|priority|dependencies)\s*:?$",
    re.IGNORECASE,
)
MAX_PRIMARY_ROWS_PER_PDF_TABLE_BLOCK = 20
PDF_TABLE_CELL_SEPARATOR = " | "
PDF_TABLE_ROW_SEPARATOR = "\n"
PDF_TABLE_TEXT_BLOCK_OVERLAP_RATIO = 0.80
PDF_TABLE_DETECTION_STRATEGY = "lines_strict"
PDF_TABLE_GEOMETRY_TOLERANCE = 0.5
PDF_TABLE_DETECTION_SNAP_TOLERANCE = 0.5
PDF_TABLE_OVERLAP_AREA_EPSILON = 0.25


class _PdfTableEvidenceUnavailable(ValueError):
    """The detected region cannot be trusted as logical table-cell evidence."""


class PdfParserV2:
    parser_name = "pdf_parser_v2"
    source_format = "pdf"

    def parse(self, path: str | Path, document_id: str = "doc_001") -> ParsedDocument:
        source_path = Path(path)
        try:
            import fitz
        except ImportError as exc:
            raise DocumentParseError("PyMuPDF is required to parse PDF documents") from exc
        disable_layout_recommendation = getattr(
            fitz,
            "no_recommend_layout",
            None,
        )
        if callable(disable_layout_recommendation):
            disable_layout_recommendation()

        try:
            document = fitz.open(source_path)
        except Exception as exc:
            raise DocumentParseError(
                f"failed to open PDF document: {source_path.name}"
            ) from exc

        source_hash = sha256_file(source_path)
        parser_identity = _parser_identity()
        page_layouts: list[_PageLayout] = []
        warnings: list[str] = []
        try:
            for page_index, page in enumerate(document, start=1):
                layout = _extract_page_layout(page, page_index)
                page_layouts.append(layout)
                if not layout.blocks:
                    warnings.append(f"page {page_index} has no extractable text")
        finally:
            document.close()

        _mark_repeated_page_edges(page_layouts)
        _assign_pdf_sections(page_layouts)

        blocks: list[DocumentBlock] = []
        evidence_blocks: list[BlockEvidenceRecord] = []
        fragments: list[TextFragmentRecord] = []
        tables: list[TableRecord] = []
        cells: list[TableCellRecord] = []
        occurrences: list[CellBlockOccurrence] = []
        pages: list[PageRecord] = []
        repeated_edge_candidate_blocks = 0
        table_index = 0
        occurrence_index = 0

        for layout in page_layouts:
            page_block_ids: list[str] = []
            page_table_ids: list[str] = []
            candidates = list(layout.blocks)
            repeated_edge_candidate_blocks += sum(
                item.edge_candidate for item in candidates
            )
            if any(item.bbox is None for item in candidates):
                layout.warnings.append(
                    "PDF_READING_ORDER_PARTIAL_GEOMETRY_FALLBACK"
                )
            ordered, column_count = _order_page_blocks(candidates, layout.width)
            if column_count > 1:
                layout.warnings.append(
                    f"PDF_MULTI_COLUMN_ORDER_BEST_EFFORT: columns={column_count}"
                )

            for source_index, candidate in enumerate(ordered, start=1):
                if candidate.table is not None:
                    table_index += 1
                    built = _build_pdf_table_evidence(
                        candidate.table,
                        document_id=document_id,
                        page=layout.page,
                        section_path=candidate.section_path,
                        source_index=source_index,
                        layout_column_index=candidate.column_index,
                        layout_column_count=column_count,
                        first_block_order=len(blocks) + 1,
                        table_index=table_index,
                        first_occurrence_index=occurrence_index + 1,
                    )
                    blocks.extend(built.blocks)
                    evidence_blocks.extend(built.evidence_blocks)
                    tables.append(built.table)
                    cells.extend(built.cells)
                    occurrences.extend(built.occurrences)
                    occurrence_index += len(built.occurrences)
                    page_block_ids.extend(block.block_id for block in built.blocks)
                    page_table_ids.append(built.table.table_id)
                    continue

                order = len(blocks) + 1
                block_identifier = block_id(order)
                block = DocumentBlock(
                    block_id=block_identifier,
                    document_id=document_id,
                    type=(
                        "heading"
                        if candidate.block_type == "heading"
                        else "list"
                        if LIST_RE.match(candidate.text)
                        else "paragraph"
                    ),
                    text=candidate.text,
                    page=layout.page,
                    section_path=list(candidate.section_path),
                    order=order,
                    metadata={
                        "source_format": self.source_format,
                        "parser": self.parser_name,
                        "page": layout.page,
                        "source_index": source_index,
                        "source_block_number": candidate.source_block_number,
                        "source_segment_number": candidate.source_segment_number,
                        "layout_column_index": candidate.column_index,
                        "layout_column_count": column_count,
                        "page_region_available": candidate.bbox is not None,
                        "table_cell_expected": bool(
                            candidate.rejected_table_numbers
                        ),
                        "rejected_table_candidates": list(
                            candidate.rejected_table_numbers
                        ),
                        "repeated_edge_candidate": candidate.edge_candidate,
                        "repeated_edge_role": candidate.edge_role,
                        **(
                            {"level": candidate.heading_level}
                            if candidate.heading_level is not None
                            else {}
                        ),
                    },
                )
                blocks.append(block)
                page_block_ids.append(block_identifier)

                fragment_ids: list[str] = []
                for index, projected in enumerate(candidate.fragments, start=1):
                    identifier = fragment_id(block_identifier, index)
                    fragment_ids.append(identifier)
                    fragments.append(
                        TextFragmentRecord(
                            fragment_id=identifier,
                            block_id=block_identifier,
                            start=projected.start,
                            end=projected.end,
                            text=projected.text,
                            page=layout.page,
                            bbox=projected.bbox,
                            line_index=projected.line_index,
                            span_index=projected.span_index,
                            separator_before=projected.separator_before,
                        )
                    )

                expected_capabilities = ["text_range", "page_region"]
                if candidate.rejected_table_numbers:
                    expected_capabilities.append("table_cell")
                evidence_blocks.append(
                    BlockEvidenceRecord(
                        block_id=block_identifier,
                        text_length=len(candidate.text),
                        text_sha256=sha256_text(candidate.text),
                        page=layout.page,
                        bbox=candidate.bbox,
                        fragment_ids=fragment_ids,
                        expected_capabilities=expected_capabilities,
                        available_capabilities=(
                            ["text_range", "page_region"]
                            if candidate.bbox is not None
                            else ["text_range"]
                        ),
                    )
                )

            pages.append(
                PageRecord(
                    page_id=page_id(layout.page),
                    page=layout.page,
                    width=layout.width,
                    height=layout.height,
                    source_rotation=layout.rotation,  # type: ignore[arg-type]
                    block_ids=page_block_ids,
                    table_ids=page_table_ids,
                    warnings=list(dict.fromkeys(layout.warnings)),
                )
            )

        if not blocks:
            raise DocumentParseError("no extractable text; scanned PDF is not supported")

        warnings.extend(_top_level_page_warnings(page_layouts))
        warnings = list(dict.fromkeys(warnings))

        evidence_index = finalize_evidence_fingerprint(
            EvidenceIndex(
                document_id=document_id,
                document_name=source_path.name,
                source_format=self.source_format,
                source_sha256=source_hash,
                parser_identity=parser_identity,
                evidence_fingerprint="0" * 64,
                pages=pages,
                blocks=evidence_blocks,
                fragments=fragments,
                tables=tables,
                cells=cells,
                cell_occurrences=occurrences,
                warnings=warnings,
            )
        )
        return ParsedDocument(
            document_id=document_id,
            document_name=source_path.name,
            source_format=self.source_format,
            parser_name=self.parser_name,
            text=render_blocks_to_markdown(blocks),
            blocks=blocks,
            warnings=warnings,
            metadata={
                "source_path": source_path.as_posix(),
                "page_count": len(page_layouts),
                "table_count": len(tables),
                "table_cell_count": len(cells),
                "suppressed_repeated_edge_blocks": 0,
                "repeated_edge_candidate_blocks": repeated_edge_candidate_blocks,
            },
            source_sha256=source_hash,
            parser_identity=parser_identity,
            evidence_index=evidence_index,
        )


# Backward-compatible import name; registry selection is explicitly V2.
TextPdfParser = PdfParserV2


@dataclass(frozen=True)
class _ProjectedFragment:
    start: int
    end: int
    text: str
    bbox: BoundingBox
    line_index: int
    span_index: int
    separator_before: str


@dataclass(frozen=True)
class _ProjectedPdfTableCell:
    row_index: int
    column_index: int
    text: str
    bbox: BoundingBox
    is_header: bool
    row_span: int = 1
    column_span: int = 1


@dataclass(frozen=True)
class _RawPdfTableCellGeometry:
    row_index: int
    column_index: int
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class _PdfTableBoundaryGrid:
    x_boundaries: tuple[float, ...]
    y_boundaries: tuple[float, ...]


@dataclass(frozen=True)
class _ProjectedPdfTable:
    source_table_number: int
    bbox: BoundingBox
    row_count: int
    column_count: int
    cells: tuple[_ProjectedPdfTableCell, ...]


@dataclass(frozen=True)
class _RejectedPdfTableRegion:
    source_table_number: int
    bbox: BoundingBox
    reason: str


@dataclass
class _PageTextBlock:
    text: str
    bbox: BoundingBox | None
    fragments: list[_ProjectedFragment]
    source_block_number: int
    source_segment_number: int = 1
    block_type: str = "paragraph"
    font_size: float = 0.0
    bold_heading_candidate: bool = False
    heading_level: int | None = None
    section_path: list[str] = field(default_factory=list)
    column_index: int = 1
    edge_candidate: bool = False
    edge_role: str | None = None
    table: _ProjectedPdfTable | None = None
    rejected_table_numbers: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class _RenderedPdfTableCell:
    cell: _ProjectedPdfTableCell
    physical_row_index: int
    start: int
    end: int
    role: str


@dataclass(frozen=True)
class _BuiltPdfTableEvidence:
    blocks: list[DocumentBlock]
    evidence_blocks: list[BlockEvidenceRecord]
    table: TableRecord
    cells: list[TableCellRecord]
    occurrences: list[CellBlockOccurrence]


@dataclass
class _PageLayout:
    page: int
    width: float
    height: float
    rotation: int
    blocks: list[_PageTextBlock]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _RawProjectedSpan:
    text: str
    raw_bbox: tuple[float, float, float, float] | None
    bbox: BoundingBox | None
    line_index: int
    span_index: int
    font_size: float
    flags: int


@dataclass(frozen=True)
class _ProjectedLine:
    spans: list[_RawProjectedSpan]
    raw_bbox: tuple[float, float, float, float] | None
    font_size: float


def _extract_page_layout(page: object, page_index: int) -> _PageLayout:
    page_rect = getattr(page, "rect")
    width = float(page_rect.width)
    height = float(page_rect.height)
    rotation = int(getattr(page, "rotation", 0)) % 360
    raw = page.get_text("dict", sort=False)
    blocks: list[_PageTextBlock] = []
    for source_block_number, raw_block in enumerate(raw.get("blocks", []), start=1):
        if raw_block.get("type", 0) != 0:
            continue
        projected = _project_text_block(
            page,
            raw_block,
            page_index=page_index,
            page_width=width,
            page_height=height,
            source_block_number=source_block_number,
        )
        for segment in projected:
            blocks.append(segment)
    warnings = [
        (
            "PDF_PAGE_REGION_UNAVAILABLE: "
            f"source_block={block.source_block_number}, "
            f"segment={block.source_segment_number}"
        )
        for block in blocks
        if block.bbox is None
    ]
    projected_tables, rejected_tables, table_warnings = _extract_page_tables(
        page,
        page_width=width,
        page_height=height,
    )
    warnings.extend(table_warnings)
    for block in blocks:
        block.rejected_table_numbers = [
            rejected.source_table_number
            for rejected in rejected_tables
            if _text_block_matches_rejected_table(block, rejected.bbox)
        ]
    if projected_tables:
        blocks = [
            block
            for block in blocks
            if not any(
                _text_block_belongs_to_table(block, table)
                for table in projected_tables
            )
        ]
        blocks.extend(
            _table_page_block(table)
            for table in projected_tables
        )
    return _PageLayout(
        page=page_index,
        width=width,
        height=height,
        rotation=rotation,
        blocks=blocks,
        warnings=warnings,
    )


def _extract_page_tables(
    page: object,
    *,
    page_width: float,
    page_height: float,
) -> tuple[
    list[_ProjectedPdfTable],
    list[_RejectedPdfTableRegion],
    list[str],
]:
    try:
        finder = page.find_tables(
            strategy=PDF_TABLE_DETECTION_STRATEGY,
            snap_tolerance=PDF_TABLE_DETECTION_SNAP_TOLERANCE,
        )
    except Exception as exc:
        return [], [], [
            "PDF_TABLE_DETECTION_UNAVAILABLE: "
            f"{type(exc).__name__}"
        ]

    projected: list[_ProjectedPdfTable] = []
    rejected: list[_RejectedPdfTableRegion] = []
    warnings: list[str] = []
    raw_tables = list(getattr(finder, "tables", []))
    for source_table_number, raw_table in enumerate(raw_tables, start=1):
        try:
            table = _project_pdf_table(
                page,
                raw_table,
                source_table_number=source_table_number,
                page_width=page_width,
                page_height=page_height,
            )
        except _PdfTableEvidenceUnavailable as exc:
            rejected_bbox = _rotated_bbox(
                page,
                getattr(raw_table, "bbox", None),
                page_width=page_width,
                page_height=page_height,
            )
            if rejected_bbox is not None:
                rejected.append(
                    _RejectedPdfTableRegion(
                        source_table_number=source_table_number,
                        bbox=rejected_bbox,
                        reason=str(exc),
                    )
                )
            warnings.append(
                "PDF_TABLE_CELL_EVIDENCE_UNAVAILABLE: "
                f"table={source_table_number}, reason={exc}"
            )
            continue
        projected.append(table)

    projected.sort(
        key=lambda item: (
            item.bbox.y0,
            item.bbox.x0,
            item.bbox.y1,
            item.bbox.x1,
            item.source_table_number,
        )
    )
    rejected.sort(
        key=lambda item: (
            item.bbox.y0,
            item.bbox.x0,
            item.bbox.y1,
            item.bbox.x1,
            item.source_table_number,
        )
    )
    return projected, rejected, warnings


def _project_pdf_table(
    page: object,
    raw_table: object,
    *,
    source_table_number: int,
    page_width: float,
    page_height: float,
) -> _ProjectedPdfTable:
    try:
        row_count = int(getattr(raw_table, "row_count"))
        column_count = int(getattr(raw_table, "col_count"))
        rows = list(getattr(raw_table, "rows"))
        extracted = list(raw_table.extract())
    except Exception as exc:
        raise _PdfTableEvidenceUnavailable(
            f"table extraction failed ({type(exc).__name__})"
        ) from exc
    if row_count < 1 or column_count < 1:
        raise _PdfTableEvidenceUnavailable("table dimensions are invalid")
    if len(rows) != row_count or len(extracted) != row_count:
        raise _PdfTableEvidenceUnavailable("table row count is inconsistent")

    raw_table_bbox = _raw_bbox(getattr(raw_table, "bbox", None))
    table_bbox = _rotated_bbox(
        page,
        getattr(raw_table, "bbox", None),
        page_width=page_width,
        page_height=page_height,
    )
    if raw_table_bbox is None or table_bbox is None:
        raise _PdfTableEvidenceUnavailable("table bbox is unavailable")

    header = getattr(raw_table, "header", None)
    header_names = [
        _canonicalize_pdf_table_cell_text(value or "")
        for value in list(getattr(header, "names", []) or [])
    ]
    first_row_text = [
        _canonicalize_pdf_table_cell_text(value or "")
        for value in list(extracted[0])
    ]
    first_row_is_header = (
        header is not None
        and not bool(getattr(header, "external", True))
        and len(header_names) == column_count
        and header_names == first_row_text
    )

    row_cell_boxes = [
        list(getattr(row, "cells", []))
        for row in rows
    ]
    if any(len(row_cells) != column_count for row_cells in row_cell_boxes):
        raise _PdfTableEvidenceUnavailable(
            "table rows do not cover the declared column count"
        )
    if any(len(list(values)) != column_count for values in extracted):
        raise _PdfTableEvidenceUnavailable(
            "table text rows do not cover the declared column count"
        )
    normalized_boxes = [
        _raw_bbox(raw_bbox)
        for row_cells in row_cell_boxes
        for raw_bbox in row_cells
    ]
    non_empty_boxes = [bbox for bbox in normalized_boxes if bbox is not None]
    merged_candidate = (
        len(non_empty_boxes) != row_count * column_count
        or len(set(non_empty_boxes)) != len(non_empty_boxes)
    )
    if merged_candidate:
        cells = _project_pdf_merged_table_cells(
            page,
            raw_table_bbox,
            row_cell_boxes,
            extracted,
            row_count=row_count,
            column_count=column_count,
            first_row_is_header=first_row_is_header,
            page_width=page_width,
            page_height=page_height,
        )
    else:
        cells = _project_pdf_simple_table_cells(
            page,
            raw_table_bbox,
            row_cell_boxes,
            extracted,
            row_count=row_count,
            column_count=column_count,
            first_row_is_header=first_row_is_header,
            page_width=page_width,
            page_height=page_height,
        )

    if not any(cell.text.strip() for cell in cells):
        raise _PdfTableEvidenceUnavailable("table has no extractable cell text")
    return _ProjectedPdfTable(
        source_table_number=source_table_number,
        bbox=table_bbox,
        row_count=row_count,
        column_count=column_count,
        cells=tuple(cells),
    )


def _project_pdf_simple_table_cells(
    page: object,
    raw_table_bbox: tuple[float, float, float, float],
    row_cell_boxes: list[list[object]],
    extracted: list[object],
    *,
    row_count: int,
    column_count: int,
    first_row_is_header: bool,
    page_width: float,
    page_height: float,
) -> list[_ProjectedPdfTableCell]:
    cells: list[_ProjectedPdfTableCell] = []
    raw_geometry: list[_RawPdfTableCellGeometry] = []
    raw_cell_boxes: set[tuple[float, float, float, float]] = set()
    for row_index, (row_cells, values) in enumerate(
        zip(row_cell_boxes, extracted),
        start=1,
    ):
        row_values = list(values)
        if len(row_cells) != column_count or len(row_values) != column_count:
            raise _PdfTableEvidenceUnavailable(
                f"row {row_index} does not cover the complete grid"
            )
        for column_index, (raw_bbox, raw_text) in enumerate(
            zip(row_cells, row_values),
            start=1,
        ):
            normalized_raw_bbox = _raw_bbox(raw_bbox)
            bbox = _rotated_bbox(
                page,
                raw_bbox,
                page_width=page_width,
                page_height=page_height,
            )
            if normalized_raw_bbox is None or bbox is None:
                raise _PdfTableEvidenceUnavailable(
                    f"cell r{row_index}c{column_index} bbox is unavailable"
                )
            if normalized_raw_bbox in raw_cell_boxes:
                raise _PdfTableEvidenceUnavailable(
                    "merged or duplicated cell geometry is not yet supported"
                )
            raw_cell_boxes.add(normalized_raw_bbox)
            raw_geometry.append(
                _RawPdfTableCellGeometry(
                    row_index=row_index,
                    column_index=column_index,
                    bbox=normalized_raw_bbox,
                )
            )
            cells.append(
                _ProjectedPdfTableCell(
                    row_index=row_index,
                    column_index=column_index,
                    text=_canonicalize_pdf_table_cell_text(raw_text or ""),
                    bbox=bbox,
                    is_header=first_row_is_header and row_index == 1,
                )
            )

    if len(cells) != row_count * column_count:
        raise _PdfTableEvidenceUnavailable("table grid is incomplete")
    _validate_pdf_table_geometry(
        raw_table_bbox,
        raw_geometry,
        row_count=row_count,
        column_count=column_count,
    )
    return cells


def _project_pdf_merged_table_cells(
    page: object,
    raw_table_bbox: tuple[float, float, float, float],
    row_cell_boxes: list[list[object]],
    extracted: list[object],
    *,
    row_count: int,
    column_count: int,
    first_row_is_header: bool,
    page_width: float,
    page_height: float,
) -> list[_ProjectedPdfTableCell]:
    normalized_by_slot = {
        (row_index, column_index): _raw_bbox(raw_bbox)
        for row_index, row in enumerate(row_cell_boxes, start=1)
        for column_index, raw_bbox in enumerate(row, start=1)
    }
    non_empty_boxes = [
        bbox for bbox in normalized_by_slot.values() if bbox is not None
    ]
    if not non_empty_boxes:
        raise _PdfTableEvidenceUnavailable(
            "PDF_TABLE_MERGED_BOUNDARY_UNRESOLVED: no cell geometry"
        )
    grid = _build_pdf_table_boundary_grid(
        raw_table_bbox,
        non_empty_boxes,
        row_count=row_count,
        column_count=column_count,
    )

    slot_keys: dict[tuple[int, int], tuple[int, int, int, int] | None] = {}
    references: dict[
        tuple[int, int, int, int],
        list[tuple[int, int]],
    ] = {}
    for slot, bbox in normalized_by_slot.items():
        if bbox is None:
            slot_keys[slot] = None
            continue
        key = _pdf_bbox_lattice_key(bbox, grid)
        slot_keys[slot] = key
        references.setdefault(key, []).append(slot)

    occupied: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    for key in references:
        row_start, row_end, column_start, column_end = key
        if row_start >= row_end or column_start >= column_end:
            raise _PdfTableEvidenceUnavailable(
                "PDF_TABLE_MERGED_BOUNDARY_UNRESOLVED: "
                "cell span has no physical area"
            )
        for row_index in range(row_start + 1, row_end + 1):
            for column_index in range(column_start + 1, column_end + 1):
                coordinate = (row_index, column_index)
                owner = occupied.get(coordinate)
                if owner is not None and owner != key:
                    raise _PdfTableEvidenceUnavailable(
                        "PDF_TABLE_MERGED_SLOT_CONFLICT: "
                        f"row={row_index}, column={column_index}"
                    )
                occupied[coordinate] = key

    expected_slots = {
        (row_index, column_index)
        for row_index in range(1, row_count + 1)
        for column_index in range(1, column_count + 1)
    }
    missing_slots = sorted(expected_slots - set(occupied))
    if missing_slots:
        row_index, column_index = missing_slots[0]
        raise _PdfTableEvidenceUnavailable(
            "PDF_TABLE_MERGED_SLOT_UNCOVERED: "
            f"row={row_index}, column={column_index}"
        )

    for slot in sorted(expected_slots):
        owner = occupied[slot]
        declared = slot_keys[slot]
        anchor = (owner[0] + 1, owner[2] + 1)
        if declared is None:
            if slot == anchor:
                raise _PdfTableEvidenceUnavailable(
                    "PDF_TABLE_MERGED_ANCHOR_MISSING: "
                    f"row={slot[0]}, column={slot[1]}"
                )
            continue
        if declared != owner:
            raise _PdfTableEvidenceUnavailable(
                "PDF_TABLE_MERGED_SLOT_CONFLICT: "
                f"row={slot[0]}, column={slot[1]}"
            )

    extracted_rows = [list(values) for values in extracted]
    texts_by_owner: dict[
        tuple[int, int, int, int],
        set[str],
    ] = {}
    for row_index, column_index in sorted(expected_slots):
        text = _canonicalize_pdf_table_cell_text(
            extracted_rows[row_index - 1][column_index - 1] or ""
        )
        if text.strip():
            texts_by_owner.setdefault(
                occupied[(row_index, column_index)],
                set(),
            ).add(text)

    projected: list[_ProjectedPdfTableCell] = []
    for key in sorted(references):
        row_start, row_end, column_start, column_end = key
        anchor = (row_start + 1, column_start + 1)
        if anchor not in references[key]:
            raise _PdfTableEvidenceUnavailable(
                "PDF_TABLE_MERGED_ANCHOR_MISSING: "
                f"row={anchor[0]}, column={anchor[1]}"
            )
        non_empty_texts = texts_by_owner.get(key, set())
        if len(non_empty_texts) > 1:
            raise _PdfTableEvidenceUnavailable(
                "PDF_TABLE_MERGED_TEXT_CONFLICT: "
                f"row={anchor[0]}, column={anchor[1]}"
            )
        text = next(iter(non_empty_texts), "")
        raw_bbox = (
            grid.x_boundaries[column_start],
            grid.y_boundaries[row_start],
            grid.x_boundaries[column_end],
            grid.y_boundaries[row_end],
        )
        bbox = _rotated_bbox(
            page,
            raw_bbox,
            page_width=page_width,
            page_height=page_height,
        )
        if bbox is None:
            raise _PdfTableEvidenceUnavailable(
                "PDF_TABLE_MERGED_BOUNDARY_UNRESOLVED: "
                f"cell bbox is unavailable at row={anchor[0]}, "
                f"column={anchor[1]}"
            )
        projected.append(
            _ProjectedPdfTableCell(
                row_index=anchor[0],
                column_index=anchor[1],
                row_span=row_end - row_start,
                column_span=column_end - column_start,
                text=text,
                bbox=bbox,
                is_header=first_row_is_header and anchor[0] == 1,
            )
        )
    return sorted(
        projected,
        key=lambda cell: (cell.row_index, cell.column_index),
    )


def _build_pdf_table_boundary_grid(
    table_bbox: tuple[float, float, float, float],
    cell_boxes: list[tuple[float, float, float, float]],
    *,
    row_count: int,
    column_count: int,
) -> _PdfTableBoundaryGrid:
    x_boundaries = _cluster_pdf_table_boundaries(
        [table_bbox[0], table_bbox[2]]
        + [value for bbox in cell_boxes for value in (bbox[0], bbox[2])],
        expected_count=column_count + 1,
        axis="x",
    )
    y_boundaries = _cluster_pdf_table_boundaries(
        [table_bbox[1], table_bbox[3]]
        + [value for bbox in cell_boxes for value in (bbox[1], bbox[3])],
        expected_count=row_count + 1,
        axis="y",
    )
    if (
        not _pdf_geometry_close(x_boundaries[0], table_bbox[0])
        or not _pdf_geometry_close(x_boundaries[-1], table_bbox[2])
        or not _pdf_geometry_close(y_boundaries[0], table_bbox[1])
        or not _pdf_geometry_close(y_boundaries[-1], table_bbox[3])
    ):
        raise _PdfTableEvidenceUnavailable(
            "PDF_TABLE_MERGED_BOUNDARY_UNRESOLVED: "
            "lattice does not match table bbox"
        )
    return _PdfTableBoundaryGrid(
        x_boundaries=x_boundaries,
        y_boundaries=y_boundaries,
    )


def _cluster_pdf_table_boundaries(
    values: list[float],
    *,
    expected_count: int,
    axis: str,
) -> tuple[float, ...]:
    clusters: list[list[float]] = []
    for value in sorted(values):
        if (
            not clusters
            or value - clusters[-1][0] > PDF_TABLE_GEOMETRY_TOLERANCE
        ):
            clusters.append([value])
        else:
            clusters[-1].append(value)
    boundaries = tuple(
        sum(cluster) / len(cluster)
        for cluster in clusters
    )
    if len(boundaries) != expected_count:
        raise _PdfTableEvidenceUnavailable(
            "PDF_TABLE_MERGED_BOUNDARY_UNRESOLVED: "
            f"axis={axis}, expected={expected_count}, actual={len(boundaries)}"
        )
    if any(
        following - current <= 2 * PDF_TABLE_GEOMETRY_TOLERANCE
        for current, following in zip(boundaries, boundaries[1:])
    ):
        raise _PdfTableEvidenceUnavailable(
            "PDF_TABLE_MERGED_BOUNDARY_AMBIGUOUS: "
            f"axis={axis}"
        )
    return boundaries


def _pdf_bbox_lattice_key(
    bbox: tuple[float, float, float, float],
    grid: _PdfTableBoundaryGrid,
) -> tuple[int, int, int, int]:
    return (
        _pdf_boundary_index(bbox[1], grid.y_boundaries, axis="y0"),
        _pdf_boundary_index(bbox[3], grid.y_boundaries, axis="y1"),
        _pdf_boundary_index(bbox[0], grid.x_boundaries, axis="x0"),
        _pdf_boundary_index(bbox[2], grid.x_boundaries, axis="x1"),
    )


def _pdf_boundary_index(
    value: float,
    boundaries: tuple[float, ...],
    *,
    axis: str,
) -> int:
    matches = [
        index
        for index, boundary in enumerate(boundaries)
        if abs(value - boundary) <= PDF_TABLE_GEOMETRY_TOLERANCE
    ]
    if len(matches) != 1:
        raise _PdfTableEvidenceUnavailable(
            "PDF_TABLE_MERGED_BOUNDARY_UNRESOLVED: "
            f"axis={axis}, matches={len(matches)}"
        )
    return matches[0]


def _validate_pdf_table_geometry(
    table_bbox: tuple[float, float, float, float],
    cells: list[_RawPdfTableCellGeometry],
    *,
    row_count: int,
    column_count: int,
) -> None:
    tolerance = PDF_TABLE_GEOMETRY_TOLERANCE
    table_x0, table_y0, table_x1, table_y1 = table_bbox
    by_position = {
        (cell.row_index, cell.column_index): cell
        for cell in cells
    }
    if len(by_position) != row_count * column_count:
        raise _PdfTableEvidenceUnavailable(
            "table physical cell coordinates are incomplete or duplicated"
        )

    for cell in cells:
        x0, y0, x1, y1 = cell.bbox
        if (
            x0 < table_x0 - tolerance
            or y0 < table_y0 - tolerance
            or x1 > table_x1 + tolerance
            or y1 > table_y1 + tolerance
        ):
            raise _PdfTableEvidenceUnavailable(
                "cell bbox lies outside table bbox: "
                f"r{cell.row_index}c{cell.column_index}"
            )

    grid_x0 = min(cell.bbox[0] for cell in cells)
    grid_y0 = min(cell.bbox[1] for cell in cells)
    grid_x1 = max(cell.bbox[2] for cell in cells)
    grid_y1 = max(cell.bbox[3] for cell in cells)
    if not all(
        _pdf_geometry_close(actual, expected)
        for actual, expected in (
            (grid_x0, table_x0),
            (grid_y0, table_y0),
            (grid_x1, table_x1),
            (grid_y1, table_y1),
        )
    ):
        raise _PdfTableEvidenceUnavailable(
            "cell grid does not cover the detected table bbox"
        )

    for row_index in range(1, row_count + 1):
        row = [
            by_position[(row_index, column_index)]
            for column_index in range(1, column_count + 1)
        ]
        reference_y0, reference_y1 = row[0].bbox[1], row[0].bbox[3]
        if any(
            not _pdf_geometry_close(cell.bbox[1], reference_y0)
            or not _pdf_geometry_close(cell.bbox[3], reference_y1)
            for cell in row[1:]
        ):
            raise _PdfTableEvidenceUnavailable(
                f"cell row boundaries are not aligned: row {row_index}"
            )
        for left, right in zip(row, row[1:]):
            if right.bbox[0] < left.bbox[0] - tolerance:
                raise _PdfTableEvidenceUnavailable(
                    f"cell columns are not ordered: row {row_index}"
                )
            if (
                _pdf_bbox_intersection_area(left.bbox, right.bbox)
                > PDF_TABLE_OVERLAP_AREA_EPSILON
            ):
                raise _PdfTableEvidenceUnavailable(
                    "adjacent cell bboxes overlap in physical PDF space: "
                    f"row {row_index}, columns "
                    f"{left.column_index}/{right.column_index}"
                )
            boundary_delta = right.bbox[0] - left.bbox[2]
            if boundary_delta < -tolerance:
                raise _PdfTableEvidenceUnavailable(
                    "adjacent cell bboxes overlap in physical PDF space: "
                    f"row {row_index}, columns "
                    f"{left.column_index}/{right.column_index}"
                )
            if boundary_delta > tolerance:
                raise _PdfTableEvidenceUnavailable(
                    "adjacent cell column boundaries contain a gap: "
                    f"row {row_index}, columns "
                    f"{left.column_index}/{right.column_index}"
                )

    for column_index in range(1, column_count + 1):
        column = [
            by_position[(row_index, column_index)]
            for row_index in range(1, row_count + 1)
        ]
        reference_x0, reference_x1 = column[0].bbox[0], column[0].bbox[2]
        if any(
            not _pdf_geometry_close(cell.bbox[0], reference_x0)
            or not _pdf_geometry_close(cell.bbox[2], reference_x1)
            for cell in column[1:]
        ):
            raise _PdfTableEvidenceUnavailable(
                f"cell column boundaries are not aligned: column {column_index}"
            )
        for upper, lower in zip(column, column[1:]):
            if lower.bbox[1] < upper.bbox[1] - tolerance:
                raise _PdfTableEvidenceUnavailable(
                    f"cell rows are not ordered: column {column_index}"
                )
            if (
                _pdf_bbox_intersection_area(upper.bbox, lower.bbox)
                > PDF_TABLE_OVERLAP_AREA_EPSILON
            ):
                raise _PdfTableEvidenceUnavailable(
                    "adjacent cell bboxes overlap in physical PDF space: "
                    f"column {column_index}, rows "
                    f"{upper.row_index}/{lower.row_index}"
                )
            boundary_delta = lower.bbox[1] - upper.bbox[3]
            if boundary_delta < -tolerance:
                raise _PdfTableEvidenceUnavailable(
                    "adjacent cell bboxes overlap in physical PDF space: "
                    f"column {column_index}, rows "
                    f"{upper.row_index}/{lower.row_index}"
                )
            if boundary_delta > tolerance:
                raise _PdfTableEvidenceUnavailable(
                    "adjacent cell row boundaries contain a gap: "
                    f"column {column_index}, rows "
                    f"{upper.row_index}/{lower.row_index}"
                )

def _pdf_geometry_close(left: float, right: float) -> bool:
    return abs(left - right) <= PDF_TABLE_GEOMETRY_TOLERANCE


def _pdf_bbox_intersection_area(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    intersection_width = max(
        0.0,
        min(left[2], right[2]) - max(left[0], right[0]),
    )
    intersection_height = max(
        0.0,
        min(left[3], right[3]) - max(left[1], right[1]),
    )
    return intersection_width * intersection_height


def _table_page_block(table: _ProjectedPdfTable) -> _PageTextBlock:
    text, _ = _render_pdf_table_row_group(
        table,
        row_start=1,
        row_end=table.row_count,
        repeated_header=False,
    )
    return _PageTextBlock(
        text=text,
        bbox=table.bbox,
        fragments=[],
        source_block_number=1_000_000 + table.source_table_number,
        block_type="table",
        table=table,
    )


def _text_block_belongs_to_table(
    block: _PageTextBlock,
    table: _ProjectedPdfTable,
) -> bool:
    return _text_block_belongs_to_bbox(block, table.bbox)


def _text_block_matches_rejected_table(
    block: _PageTextBlock,
    bbox: BoundingBox,
) -> bool:
    if _text_block_belongs_to_bbox(block, bbox):
        return True
    return any(
        bbox.x0 <= (fragment.bbox.x0 + fragment.bbox.x1) / 2 <= bbox.x1
        and bbox.y0 <= (fragment.bbox.y0 + fragment.bbox.y1) / 2 <= bbox.y1
        for fragment in block.fragments
    )


def _text_block_belongs_to_bbox(
    block: _PageTextBlock,
    bbox: BoundingBox,
) -> bool:
    if block.bbox is None:
        return False
    intersection_x = max(
        0.0,
        min(block.bbox.x1, bbox.x1)
        - max(block.bbox.x0, bbox.x0),
    )
    intersection_y = max(
        0.0,
        min(block.bbox.y1, bbox.y1)
        - max(block.bbox.y0, bbox.y0),
    )
    block_area = (
        (block.bbox.x1 - block.bbox.x0)
        * (block.bbox.y1 - block.bbox.y0)
    )
    if block_area <= 0:
        return False
    return (
        intersection_x * intersection_y / block_area
        >= PDF_TABLE_TEXT_BLOCK_OVERLAP_RATIO
    )


def _build_pdf_table_evidence(
    table: _ProjectedPdfTable,
    *,
    document_id: str,
    page: int,
    section_path: list[str],
    source_index: int,
    layout_column_index: int,
    layout_column_count: int,
    first_block_order: int,
    table_index: int,
    first_occurrence_index: int,
) -> _BuiltPdfTableEvidence:
    table_identifier = table_id(table_index)
    cell_identifiers = {
        (cell.row_index, cell.column_index): cell_id(
            table_index,
            cell.row_index,
            cell.column_index,
        )
        for cell in table.cells
    }
    cell_records = [
        TableCellRecord(
            cell_id=cell_identifiers[(cell.row_index, cell.column_index)],
            table_id=table_identifier,
            row_index=cell.row_index,
            column_index=cell.column_index,
            row_span=cell.row_span,
            column_span=cell.column_span,
            text=cell.text,
            text_sha256=sha256_text(cell.text),
            is_header=cell.is_header,
            page=page,
            bbox=cell.bbox,
        )
        for cell in table.cells
    ]

    blocks: list[DocumentBlock] = []
    evidence_blocks: list[BlockEvidenceRecord] = []
    occurrences: list[CellBlockOccurrence] = []
    table_occurrence_ids: list[str] = []
    header_available = all(
        cell.is_header and cell.row_span == 1
        for cell in table.cells
        if cell.row_index == 1
    )
    row_groups = [
        (
            start,
            min(
                start + MAX_PRIMARY_ROWS_PER_PDF_TABLE_BLOCK - 1,
                table.row_count,
            ),
        )
        for start in range(
            1,
            table.row_count + 1,
            MAX_PRIMARY_ROWS_PER_PDF_TABLE_BLOCK,
        )
    ]

    for group_index, (row_start, row_end) in enumerate(row_groups):
        repeated_header = group_index > 0 and header_available
        text, rendered_cells = _render_pdf_table_row_group(
            table,
            row_start=row_start,
            row_end=row_end,
            repeated_header=repeated_header,
        )
        order = first_block_order + group_index
        block_identifier = block_id(order)
        block_cell_ids = list(
            dict.fromkeys(
                cell_identifiers[
                    (rendered.cell.row_index, rendered.cell.column_index)
                ]
                for rendered in rendered_cells
            )
        )
        primary_cells = [
            cell
            for cell in table.cells
            if cell.row_index <= row_end
            and cell.row_index + cell.row_span - 1 >= row_start
        ]
        block_bbox = _bbox_union([cell.bbox for cell in primary_cells])
        block = DocumentBlock(
            block_id=block_identifier,
            document_id=document_id,
            type="table",
            text=text,
            page=page,
            section_path=list(section_path),
            order=order,
            metadata={
                "source_format": PdfParserV2.source_format,
                "parser": PdfParserV2.parser_name,
                "page": page,
                "source_index": source_index,
                "source_table_number": table.source_table_number,
                "layout_column_index": layout_column_index,
                "layout_column_count": layout_column_count,
                "page_region_available": True,
                "structured_table_evidence": True,
                "table_id": table_identifier,
                "table_index": table_index,
                "table_row_start": row_start,
                "table_row_end": row_end,
                "repeated_header_rows": [1] if repeated_header else [],
            },
        )
        blocks.append(block)
        evidence_blocks.append(
            BlockEvidenceRecord(
                block_id=block_identifier,
                text_length=len(text),
                text_sha256=sha256_text(text),
                page=page,
                bbox=block_bbox,
                table_id=table_identifier,
                table_row_start=row_start,
                table_row_end=row_end,
                cell_ids=block_cell_ids,
                expected_capabilities=[
                    "text_range",
                    "page_region",
                    "table_cell",
                ],
                available_capabilities=[
                    "text_range",
                    "page_region",
                    "table_cell",
                ],
            )
        )
        for rendered in rendered_cells:
            occurrence_identifier = occurrence_id(
                first_occurrence_index + len(occurrences)
            )
            occurrences.append(
                CellBlockOccurrence(
                    occurrence_id=occurrence_identifier,
                    cell_id=cell_identifiers[
                        (rendered.cell.row_index, rendered.cell.column_index)
                    ],
                    block_id=block_identifier,
                    physical_row_index=rendered.physical_row_index,
                    canonical_start=rendered.start,
                    canonical_end=rendered.end,
                    occurrence_role=rendered.role,  # type: ignore[arg-type]
                )
            )
            table_occurrence_ids.append(occurrence_identifier)

    table_record = TableRecord(
        table_id=table_identifier,
        block_ids=[block.block_id for block in blocks],
        page=page,
        bbox=table.bbox,
        row_count=table.row_count,
        column_count=table.column_count,
        cell_ids=[cell.cell_id for cell in cell_records],
        occurrence_ids=table_occurrence_ids,
        parser_method="pymupdf_find_tables",
        topology_status="complete",
        warnings=(
            ["PDF_REPEATED_HEADER_PROJECTED"]
            if len(row_groups) > 1 and header_available
            else []
        ),
    )
    return _BuiltPdfTableEvidence(
        blocks=blocks,
        evidence_blocks=evidence_blocks,
        table=table_record,
        cells=cell_records,
        occurrences=occurrences,
    )


def _render_pdf_table_row_group(
    table: _ProjectedPdfTable,
    *,
    row_start: int,
    row_end: int,
    repeated_header: bool,
) -> tuple[str, list[_RenderedPdfTableCell]]:
    rendered_rows = (
        ([(1, True)] if repeated_header else [])
        + [
            (row_index, False)
            for row_index in range(row_start, row_end + 1)
        ]
    )
    parts: list[str] = []
    rendered_cells: list[_RenderedPdfTableCell] = []
    cursor = 0
    for rendered_row_index, (row_index, repeated) in enumerate(rendered_rows):
        if rendered_row_index:
            parts.append(PDF_TABLE_ROW_SEPARATOR)
            cursor += len(PDF_TABLE_ROW_SEPARATOR)
        row_cells = sorted(
            (
                cell
                for cell in table.cells
                if cell.row_index <= row_index
                < cell.row_index + cell.row_span
            ),
            key=lambda cell: cell.column_index,
        )
        for cell_index, cell in enumerate(row_cells):
            start = cursor
            parts.append(cell.text)
            cursor += len(cell.text)
            rendered_cells.append(
                _RenderedPdfTableCell(
                    cell=cell,
                    physical_row_index=row_index,
                    start=start,
                    end=cursor,
                    role=(
                        "repeated_header"
                        if repeated
                        else "original"
                        if row_index == cell.row_index
                        else "row_span_projection"
                    ),
                )
            )
            if cell_index < len(row_cells) - 1:
                parts.append(PDF_TABLE_CELL_SEPARATOR)
                cursor += len(PDF_TABLE_CELL_SEPARATOR)
    return "".join(parts), rendered_cells


def _canonicalize_pdf_table_cell_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return (
        normalized.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\n", "\\n")
    )


def _project_text_block(
    page: object,
    raw_block: dict,
    *,
    page_index: int,
    page_width: float,
    page_height: float,
    source_block_number: int,
) -> list[_PageTextBlock]:
    lines: list[_ProjectedLine] = []
    for line_index, line in enumerate(raw_block.get("lines", [])):
        raw_spans = [
            (span_index, span)
            for span_index, span in enumerate(line.get("spans", []))
            if str(span.get("text", ""))
        ]
        if not raw_spans:
            continue
        line_has_text = any(str(span.get("text", "")).strip() for _, span in raw_spans)
        if not line_has_text:
            continue
        projected_spans: list[_RawProjectedSpan] = []
        for span_index, span in raw_spans:
            text = str(span.get("text", ""))
            raw_bbox = _raw_bbox(span.get("bbox"))
            bbox = _rotated_bbox(
                page,
                span.get("bbox"),
                page_width=page_width,
                page_height=page_height,
            )
            try:
                font_size = float(span.get("size", 0.0))
            except (TypeError, ValueError):
                font_size = 0.0
            projected_spans.append(
                _RawProjectedSpan(
                    text=text,
                    raw_bbox=raw_bbox,
                    bbox=bbox,
                    line_index=line_index,
                    span_index=span_index,
                    font_size=max(0.0, font_size),
                    flags=int(span.get("flags", 0) or 0),
                )
            )
        line_boxes = [span.raw_bbox for span in projected_spans if span.raw_bbox]
        lines.append(
            _ProjectedLine(
                spans=projected_spans,
                raw_bbox=_raw_bbox_union(line_boxes) if line_boxes else None,
                font_size=max((span.font_size for span in projected_spans), default=0.0),
            )
        )

    if not lines:
        return []
    line_groups = _paragraph_line_groups(lines)
    baseline_font_size = median(
        [line.font_size for line in lines if line.font_size > 0]
    ) if any(line.font_size > 0 for line in lines) else 0.0
    return [
        _render_line_group(
            group,
            source_block_number=source_block_number,
            source_segment_number=segment_number,
            baseline_font_size=baseline_font_size,
        )
        for segment_number, group in enumerate(line_groups, start=1)
        if any(span.text.strip() for line in group for span in line.spans)
    ]


def _paragraph_line_groups(
    lines: list[_ProjectedLine],
) -> list[list[_ProjectedLine]]:
    groups: list[list[_ProjectedLine]] = []
    current: list[_ProjectedLine] = []
    for line in lines:
        if current and _starts_new_paragraph(current[-1], line):
            groups.append(current)
            current = []
        current.append(line)
    if current:
        groups.append(current)
    return groups


def _starts_new_paragraph(
    previous: _ProjectedLine,
    current: _ProjectedLine,
) -> bool:
    if previous.raw_bbox is not None and current.raw_bbox is not None:
        previous_height = previous.raw_bbox[3] - previous.raw_bbox[1]
        current_height = current.raw_bbox[3] - current.raw_bbox[1]
        vertical_gap = current.raw_bbox[1] - previous.raw_bbox[3]
        if vertical_gap > max(
            4.0,
            max(previous_height, current_height) * PARAGRAPH_GAP_RATIO,
        ):
            return True
    smaller_size = min(previous.font_size, current.font_size)
    if smaller_size <= 0:
        return False
    return abs(previous.font_size - current.font_size) >= max(
        MIN_FONT_LEVEL_DELTA,
        smaller_size * FONT_LEVEL_DELTA_RATIO,
    )


def _render_line_group(
    lines: list[_ProjectedLine],
    *,
    source_block_number: int,
    source_segment_number: int,
    baseline_font_size: float,
) -> _PageTextBlock:
    text_parts: list[str] = []
    fragments: list[_ProjectedFragment] = []
    cursor = 0
    geometry_available = all(
        span.raw_bbox is not None and span.bbox is not None
        for line in lines
        for span in line.spans
    )
    for rendered_line_index, line in enumerate(lines):
        previous_span: _RawProjectedSpan | None = None
        for span_position, span in enumerate(line.spans):
            if span_position == 0:
                separator = "\n" if rendered_line_index else ""
            else:
                separator = _same_line_separator(previous_span, span)
            if separator:
                text_parts.append(separator)
                cursor += len(separator)
            start = cursor
            text_parts.append(span.text)
            cursor += len(span.text)
            if geometry_available:
                assert span.bbox is not None
                fragments.append(
                    _ProjectedFragment(
                        start=start,
                        end=cursor,
                        text=span.text,
                        bbox=span.bbox,
                        line_index=span.line_index,
                        span_index=span.span_index,
                        separator_before=separator,
                    )
                )
            previous_span = span

    text = "".join(text_parts)
    block_font_size = max((line.font_size for line in lines), default=0.0)
    larger_heading, bold_heading_candidate = _line_group_heading_signals(
        lines,
        text,
        block_font_size,
        baseline_font_size,
    )
    return _PageTextBlock(
        text=text,
        bbox=(
            _bbox_union([fragment.bbox for fragment in fragments])
            if fragments
            else None
        ),
        fragments=fragments,
        source_block_number=source_block_number,
        source_segment_number=source_segment_number,
        block_type="heading" if larger_heading else "paragraph",
        font_size=block_font_size,
        bold_heading_candidate=bold_heading_candidate and not larger_heading,
    )


def _same_line_separator(
    previous: _RawProjectedSpan | None,
    current: _RawProjectedSpan,
) -> str:
    if previous is None:
        return ""
    if previous.text[-1:].isspace() or current.text[:1].isspace():
        return ""
    if previous.raw_bbox is None or current.raw_bbox is None:
        return ""
    horizontal_gap = current.raw_bbox[0] - previous.raw_bbox[2]
    reference_size = min(
        value for value in (previous.font_size, current.font_size) if value > 0
    ) if previous.font_size > 0 or current.font_size > 0 else 0.0
    threshold = max(MIN_SPAN_GAP, reference_size * SPAN_GAP_EM_RATIO)
    return " " if horizontal_gap > threshold else ""


def _line_group_heading_signals(
    lines: list[_ProjectedLine],
    text: str,
    block_font_size: float,
    baseline_font_size: float,
) -> tuple[bool, bool]:
    if len(lines) != 1 or len(text.strip()) > 160:
        return False, False
    non_empty_spans = [span for span in lines[0].spans if span.text.strip()]
    bold = bool(non_empty_spans) and all(span.flags & 16 for span in non_empty_spans)
    larger = (
        baseline_font_size > 0
        and block_font_size >= baseline_font_size * 1.15
        and block_font_size - baseline_font_size >= 1.0
    )
    return larger, bold


def _bold_candidate_looks_like_heading(text: str) -> bool:
    normalized = " ".join(text.split())
    if not normalized or len(normalized) > BOLD_HEADING_MAX_CHARS:
        return False
    if len(normalized.split()) > BOLD_HEADING_MAX_WORDS:
        return False
    if normalized.endswith((":", ".", "!", "?")):
        return False
    if BOLD_LABEL_RE.fullmatch(normalized):
        return False
    return NORMATIVE_SENTENCE_RE.search(normalized) is None


def _raw_bbox(raw_bbox: object) -> tuple[float, float, float, float] | None:
    try:
        values = tuple(float(value) for value in raw_bbox)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        return None
    x0, y0, x1, y1 = values
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _raw_bbox_union(
    boxes: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _rotated_bbox(
    page: object,
    raw_bbox: object,
    *,
    page_width: float,
    page_height: float,
) -> BoundingBox | None:
    coordinates = _raw_bbox(raw_bbox)
    if coordinates is None:
        return None
    try:
        import fitz

        rect = fitz.Rect(coordinates) * page.rotation_matrix
    except Exception:
        return None
    x0 = _clamp(float(rect.x0), 0.0, page_width)
    y0 = _clamp(float(rect.y0), 0.0, page_height)
    x1 = _clamp(float(rect.x1), 0.0, page_width)
    y1 = _clamp(float(rect.y1), 0.0, page_height)
    if x1 <= x0 or y1 <= y0:
        return None
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _mark_repeated_page_edges(page_layouts: list[_PageLayout]) -> None:
    if len(page_layouts) < MIN_REPEATED_EDGE_PAGES:
        return
    occurrences: dict[
        tuple[str, str],
        list[tuple[_PageLayout, _PageTextBlock]],
    ] = {}
    for layout in page_layouts:
        for block in layout.blocks:
            if block.bbox is None:
                continue
            role = _edge_role(block.bbox, layout.height)
            if role is None:
                continue
            key = (role, _normalized_edge_text(block.text))
            if key[1]:
                occurrences.setdefault(key, []).append((layout, block))

    repeated = {
        key
        for key, candidates in occurrences.items()
        if len({layout.page for layout, _ in candidates})
        >= max(
            MIN_REPEATED_EDGE_PAGES,
            math.ceil(len(page_layouts) * 0.5),
        )
        and _edge_geometry_is_stable(candidates)
    }
    for key in sorted(
        repeated,
        key=lambda item: (0 if item[0] == "header" else 1, item[1]),
    ):
        role, _ = key
        for layout, block in occurrences[key]:
            block.edge_candidate = True
            block.edge_role = role
            layout.warnings.append(
                "PDF_REPEATED_HEADER_CANDIDATE"
                if role == "header"
                else "PDF_REPEATED_FOOTER_CANDIDATE"
            )


def _edge_geometry_is_stable(
    candidates: list[tuple[_PageLayout, _PageTextBlock]],
) -> bool:
    normalized_boxes: list[tuple[float, float, float, float]] = []
    for layout, block in candidates:
        if block.bbox is None or layout.width <= 0 or layout.height <= 0:
            return False
        normalized_boxes.append(
            (
                block.bbox.x0 / layout.width,
                block.bbox.y0 / layout.height,
                block.bbox.x1 / layout.width,
                block.bbox.y1 / layout.height,
            )
        )
    return all(
        max(box[index] for box in normalized_boxes)
        - min(box[index] for box in normalized_boxes)
        <= EDGE_POSITION_TOLERANCE
        for index in range(4)
    )


def _edge_role(bbox: BoundingBox, page_height: float) -> str | None:
    if bbox.y0 <= page_height * EDGE_REGION_RATIO:
        return "header"
    if bbox.y1 >= page_height * (1.0 - EDGE_REGION_RATIO):
        return "footer"
    return None


def _normalized_edge_text(text: str) -> str:
    return " ".join(text.split()).casefold()


def _assign_pdf_sections(page_layouts: list[_PageLayout]) -> None:
    _resolve_bold_heading_candidates(page_layouts)
    heading_sizes = sorted(
        {
            round(block.font_size, 1)
            for layout in page_layouts
            for block in layout.blocks
            if block.block_type == "heading"
            and not block.edge_candidate
            and block.font_size > 0
        },
        reverse=True,
    )
    font_levels = {
        size: min(index, 6) for index, size in enumerate(heading_sizes, start=1)
    }
    sections_by_level: dict[int, str] = {}
    for layout in page_layouts:
        ordered, _ = _order_page_blocks(list(layout.blocks), layout.width)
        for block in ordered:
            if block.edge_candidate:
                block.block_type = "paragraph"
                block.heading_level = None
                block.section_path = _section_path(sections_by_level)
                continue
            if block.block_type == "heading":
                level = _pdf_heading_level(block, font_levels)
                title = " ".join(block.text.split())
                for stale_level in [
                    item for item in sections_by_level if item >= level
                ]:
                    del sections_by_level[stale_level]
                sections_by_level[level] = title
                block.heading_level = level
            block.section_path = _section_path(sections_by_level)


def _resolve_bold_heading_candidates(page_layouts: list[_PageLayout]) -> None:
    reading_sequence: list[tuple[_PageLayout, _PageTextBlock]] = []
    for layout in page_layouts:
        ordered, _ = _order_page_blocks(list(layout.blocks), layout.width)
        reading_sequence.extend((layout, block) for block in ordered)

    for index, (layout, block) in enumerate(reading_sequence):
        if (
            _is_heading_inference_edge_decoration(layout, block)
            or not block.bold_heading_candidate
            or not _bold_candidate_looks_like_heading(block.text)
        ):
            continue
        if NUMBERED_HEADING_RE.match(block.text):
            block.block_type = "heading"
            continue
        following_entry = next(
            (
                entry
                for entry in reading_sequence[index + 1 :]
                if not _is_heading_inference_edge_decoration(*entry)
            ),
            None,
        )
        if following_entry is None:
            continue
        following_layout, following = following_entry
        if following_layout is layout:
            if _is_adjacent_heading_body(
                block,
                following,
            ):
                block.block_type = "heading"
            continue
        if _is_cross_page_adjacent_heading_body(
            layout,
            block,
            following_layout,
            following,
        ):
            block.block_type = "heading"


def _is_heading_inference_edge_decoration(
    layout: _PageLayout,
    block: _PageTextBlock,
) -> bool:
    normalized = " ".join(block.text.split())
    is_patterned_edge_decoration = (
        block.bbox is not None
        and _edge_role(block.bbox, layout.height) is not None
        and len(normalized) <= EDGE_DECORATION_MAX_CHARS
        and len(normalized.split()) <= EDGE_DECORATION_MAX_WORDS
        and EDGE_DECORATION_RE.fullmatch(normalized) is not None
    )
    if is_patterned_edge_decoration:
        return True
    if block.edge_candidate:
        return True
    if (
        block.block_type == "heading"
        or block.bold_heading_candidate
        or NUMBERED_HEADING_RE.match(block.text)
    ):
        return False
    return False


def _is_adjacent_heading_body(
    heading: _PageTextBlock,
    following: _PageTextBlock,
) -> bool:
    if (
        following.block_type != "paragraph"
        or following.bold_heading_candidate
        or heading.bbox is None
        or following.bbox is None
        or heading.column_index != following.column_index
    ):
        return False
    vertical_gap = following.bbox.y0 - heading.bbox.y1
    maximum_gap = max(
        BOLD_HEADING_BODY_GAP_POINTS,
        max(heading.font_size, following.font_size) * BOLD_HEADING_BODY_GAP_EM_RATIO,
    )
    return -1.0 <= vertical_gap <= maximum_gap


def _is_cross_page_adjacent_heading_body(
    heading_layout: _PageLayout,
    heading: _PageTextBlock,
    body_layout: _PageLayout,
    body: _PageTextBlock,
) -> bool:
    return (
        body_layout.page == heading_layout.page + 1
        and body.block_type == "paragraph"
        and not body.bold_heading_candidate
        and heading.bbox is not None
        and body.bbox is not None
        and _cross_page_horizontal_overlap_ratio(
            heading.bbox,
            heading_layout.width,
            body.bbox,
            body_layout.width,
        )
        >= CROSS_PAGE_HORIZONTAL_OVERLAP_RATIO
        and heading.bbox.y1
        >= heading_layout.height * CROSS_PAGE_HEADING_BOTTOM_RATIO
        and body.bbox.y0 <= body_layout.height * CROSS_PAGE_BODY_TOP_RATIO
    )


def _cross_page_horizontal_overlap_ratio(
    heading: BoundingBox,
    heading_page_width: float,
    body: BoundingBox,
    body_page_width: float,
) -> float:
    if heading_page_width <= 0 or body_page_width <= 0:
        return 0.0
    heading_x0 = heading.x0 / heading_page_width
    heading_x1 = heading.x1 / heading_page_width
    body_x0 = body.x0 / body_page_width
    body_x1 = body.x1 / body_page_width
    overlap = max(0.0, min(heading_x1, body_x1) - max(heading_x0, body_x0))
    minimum_width = min(heading_x1 - heading_x0, body_x1 - body_x0)
    return 0.0 if minimum_width <= 0 else overlap / minimum_width


def _section_path(sections_by_level: dict[int, str]) -> list[str]:
    return [sections_by_level[level] for level in sorted(sections_by_level)]


def _pdf_heading_level(
    block: _PageTextBlock,
    font_levels: dict[float, int],
) -> int:
    numeric = re.match(r"^\s*(\d+(?:\.\d+)*)(?:[.)]|\s)", block.text)
    if numeric is not None:
        return min(numeric.group(1).count(".") + 1, 6)
    return font_levels.get(round(block.font_size, 1), 1)


def _top_level_page_warnings(page_layouts: list[_PageLayout]) -> list[str]:
    warnings: list[str] = []
    for layout in page_layouts:
        for warning in dict.fromkeys(layout.warnings):
            code, separator, details = warning.partition(":")
            suffix = f",{details}" if separator and details else ""
            warnings.append(f"{code}: page={layout.page}{suffix}")
    return warnings


def _order_page_blocks(
    blocks: list[_PageTextBlock],
    page_width: float,
) -> tuple[list[_PageTextBlock], int]:
    geometric = [block for block in blocks if block.bbox is not None]
    fallback = [block for block in blocks if block.bbox is None]
    ordered, column_count = _order_geometric_page_blocks(geometric, page_width)
    for block in sorted(fallback, key=_source_key):
        _insert_fallback_block(ordered, block)
    return ordered, column_count


def _order_geometric_page_blocks(
    blocks: list[_PageTextBlock],
    page_width: float,
) -> tuple[list[_PageTextBlock], int]:
    if len(blocks) < 2:
        return sorted(blocks, key=_vertical_key), 1
    wide = [
        block
        for block in blocks
        if block.bbox is not None
        and _bbox_width(block.bbox) >= page_width * WIDE_BLOCK_RATIO
    ]
    narrow = [block for block in blocks if block not in wide]
    columns = _column_clusters(narrow)
    if len(columns) < 2 or not _clusters_form_parallel_columns(columns):
        ordered = sorted(blocks, key=_vertical_key)
        for block in ordered:
            block.column_index = 1
        return ordered, 1

    for column_index, column in enumerate(columns, start=1):
        for block in column:
            block.column_index = column_index

    ordered: list[_PageTextBlock] = []
    remaining = list(narrow)
    for anchor in sorted(wide, key=_vertical_key):
        before = [block for block in remaining if block.bbox.y0 < anchor.bbox.y0]
        ordered.extend(_column_major(before))
        remaining = [block for block in remaining if block not in before]
        anchor.column_index = 1
        ordered.append(anchor)
    ordered.extend(_column_major(remaining))
    return ordered, len(columns)


def _insert_fallback_block(
    ordered: list[_PageTextBlock],
    fallback: _PageTextBlock,
) -> None:
    fallback_key = _source_key(fallback)
    preceding = [block for block in ordered if _source_key(block) < fallback_key]
    following = [block for block in ordered if _source_key(block) > fallback_key]
    previous = max(preceding, key=_source_key) if preceding else None
    next_block = min(following, key=_source_key) if following else None

    if previous is not None:
        fallback.column_index = previous.column_index
    elif next_block is not None:
        fallback.column_index = next_block.column_index
    else:
        fallback.column_index = 1

    if previous is None and next_block is None:
        ordered.append(fallback)
        return
    if previous is None:
        ordered.insert(ordered.index(next_block), fallback)
        return
    if next_block is None:
        ordered.insert(ordered.index(previous) + 1, fallback)
        return

    previous_position = ordered.index(previous)
    next_position = ordered.index(next_block)
    insertion_position = (
        previous_position + 1
        if previous_position < next_position
        else next_position
    )
    ordered.insert(insertion_position, fallback)


def _source_key(block: _PageTextBlock) -> tuple[int, int]:
    return block.source_block_number, block.source_segment_number


def _column_clusters(blocks: list[_PageTextBlock]) -> list[list[_PageTextBlock]]:
    clusters: list[list[_PageTextBlock]] = []
    for block in sorted(blocks, key=lambda item: (item.bbox.x0, item.bbox.y0)):
        matching = next(
            (
                cluster
                for cluster in clusters
                if _horizontal_overlap_ratio(block, cluster) >= COLUMN_OVERLAP_RATIO
            ),
            None,
        )
        if matching is None:
            clusters.append([block])
        else:
            matching.append(block)
    return sorted(clusters, key=lambda cluster: min(item.bbox.x0 for item in cluster))


def _horizontal_overlap_ratio(
    block: _PageTextBlock,
    cluster: list[_PageTextBlock],
) -> float:
    if block.bbox is None or any(item.bbox is None for item in cluster):
        return 0.0
    assert all(item.bbox is not None for item in cluster)
    cluster_x0 = min(item.bbox.x0 for item in cluster)
    cluster_x1 = max(item.bbox.x1 for item in cluster)
    overlap = max(0.0, min(block.bbox.x1, cluster_x1) - max(block.bbox.x0, cluster_x0))
    denominator = min(_bbox_width(block.bbox), cluster_x1 - cluster_x0)
    return overlap / denominator if denominator > 0 else 0.0


def _clusters_form_parallel_columns(
    columns: list[list[_PageTextBlock]],
) -> bool:
    return any(
        left.bbox is not None
        and right.bbox is not None
        and _vertical_overlap(left.bbox, right.bbox) > 0
        for left_index, left_column in enumerate(columns)
        for right_column in columns[left_index + 1 :]
        for left in left_column
        for right in right_column
    )


def _vertical_overlap(left: BoundingBox, right: BoundingBox) -> float:
    return max(0.0, min(left.y1, right.y1) - max(left.y0, right.y0))


def _column_major(blocks: list[_PageTextBlock]) -> list[_PageTextBlock]:
    return sorted(
        blocks,
        key=lambda item: (
            item.column_index,
            item.bbox.y0 if item.bbox is not None else float("inf"),
            item.bbox.x0 if item.bbox is not None else float("inf"),
        ),
    )


def _vertical_key(block: _PageTextBlock) -> tuple[float, float, int]:
    return (
        block.bbox.y0 if block.bbox is not None else float("inf"),
        block.bbox.x0 if block.bbox is not None else float("inf"),
        block.source_block_number,
    )


def _bbox_width(bbox: BoundingBox) -> float:
    return bbox.x1 - bbox.x0


def _bbox_union(boxes: list[BoundingBox]) -> BoundingBox:
    return BoundingBox(
        x0=min(box.x0 for box in boxes),
        y0=min(box.y0 for box in boxes),
        x1=max(box.x1 for box in boxes),
        y1=max(box.y1 for box in boxes),
    )


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _parser_identity() -> ParserIdentity:
    try:
        pymupdf_version = distribution_version("PyMuPDF")
    except DistributionNotFoundError:  # pragma: no cover - import already succeeded
        pymupdf_version = "unknown"
    try:
        import fitz

        mupdf_version = str(
            getattr(fitz, "mupdf_version", None)
            or getattr(fitz, "version", (None, "unknown"))[1]
        )
    except (ImportError, IndexError, TypeError):  # pragma: no cover
        mupdf_version = "unknown"
    return ParserIdentity(
        parser_name=PdfParserV2.parser_name,
        parser_version="2.16",
        source_format="pdf",
        parser_config={
            "text_extraction": "pymupdf_dict_blocks_spans",
            "canonical_line_separator": "\\n",
            "canonical_span_gap_separator": "space_when_geometrically_separated_v1",
            "logical_block_segmentation": "line_gap_and_font_hierarchy_v1",
            "section_hierarchy": "numeric_prefix_then_font_size_v9",
            "bold_heading_detection": "adjacent_body_repeated_edge_priority_v8",
            "coordinate_space": "pdf_preview_rotated_points_top_left_v1",
            "reading_order": "hybrid_geometry_with_source_anchor_fallback_v2",
            "repeated_page_edges": "preserve_stable_candidate_v1",
            "geometry_failure_policy": "text_only_block_v1",
            "table_detection": (
                "pymupdf_find_tables_lines_strict_boundary_lattice_v3"
            ),
            "table_detection_snap_tolerance": (
                PDF_TABLE_DETECTION_SNAP_TOLERANCE
            ),
            "table_geometry_validation": (
                "contained_aligned_complete_non_overlapping_v1"
            ),
            "merged_table_topology": (
                "unique_boundary_lattice_complete_slot_coverage_v2"
            ),
            "table_block_mode": "complete_row_groups",
            "max_primary_rows_per_table_block": (
                MAX_PRIMARY_ROWS_PER_PDF_TABLE_BLOCK
            ),
            "repeat_header_rows": 1,
            "canonical_table_serializer": (
                "escaped_cells_with_row_span_projection_v2"
            ),
            "unsupported_table_topology": (
                "preserve_text_expected_table_cell_unavailable_v2"
            ),
            "rejected_table_region_projection": (
                "block_80pct_overlap_or_fragment_center_v2"
            ),
        },
        runtime_dependencies={
            "PyMuPDF": pymupdf_version,
            "MuPDF": mupdf_version,
        },
    )
