from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from spectrail.evidence.errors import EvidenceReferenceError, LocatorDerivationError
from spectrail.evidence.models import (
    BoundingBox,
    EvidenceIndex,
    PageLocator,
    TableLocator,
)
from spectrail.evidence.quote_matcher import QuoteMatchRange
from spectrail.evidence.table_cells import canonicalize_nonempty_cell_selection


@dataclass(frozen=True)
class DerivedTableEvidence:
    locator: TableLocator
    reconstructed_text: str
    page: int | None


def derive_table_evidence(
    evidence_index: EvidenceIndex,
    *,
    block_id: str,
    selected_range: QuoteMatchRange,
    canonical_cell_ids: list[str],
    block_text: str,
) -> DerivedTableEvidence:
    blocks_by_id = {block.block_id: block for block in evidence_index.blocks}
    cells_by_id = {cell.cell_id: cell for cell in evidence_index.cells}
    tables_by_id = {table.table_id: table for table in evidence_index.tables}
    block = blocks_by_id.get(block_id)
    if block is None or block.table_id is None:
        raise EvidenceReferenceError(
            "table locator requires a table evidence block"
        )
    if block.table_row_start is None or block.table_row_end is None:
        raise EvidenceReferenceError(
            "table locator requires a primary table row range"
        )
    if not canonical_cell_ids:
        raise EvidenceReferenceError(
            "table locator requires canonical source cell IDs"
        )
    try:
        cells = [cells_by_id[cell_id] for cell_id in canonical_cell_ids]
    except KeyError as exc:
        raise EvidenceReferenceError(
            f"table locator references an unknown cell: {exc.args[0]}"
        ) from exc
    if any(cell.table_id != block.table_id for cell in cells):
        raise EvidenceReferenceError(
            "source cells do not belong to the source block table"
        )
    occurrences = sorted(
        (
            occurrence
            for occurrence in evidence_index.cell_occurrences
            if occurrence.block_id == block_id
        ),
        key=lambda occurrence: (
            occurrence.canonical_start,
            occurrence.canonical_end,
            cells_by_id[occurrence.cell_id].row_index,
            cells_by_id[occurrence.cell_id].column_index,
            occurrence.cell_id,
        ),
    )
    covered_occurrences = [
        occurrence
        for occurrence in occurrences
        if occurrence.canonical_start < selected_range.end
        and occurrence.canonical_end > selected_range.start
        and cells_by_id[occurrence.cell_id].text.strip()
    ]
    covered_ids = {occurrence.cell_id for occurrence in covered_occurrences}
    selected_rows = {
        occurrence.physical_row_index for occurrence in covered_occurrences
    }
    if len(selected_rows) != 1:
        raise LocatorDerivationError(
            "quote range must resolve to exactly one physical table row"
        )
    selected_row_index = next(iter(selected_rows))
    table = tables_by_id[block.table_id]
    canonical = canonicalize_nonempty_cell_selection(
        cells,
        [cells_by_id[cell_id] for cell_id in table.cell_ids],
        table=table,
        selected_row_index=selected_row_index,
    )
    if [cell.cell_id for cell in canonical] != canonical_cell_ids:
        raise EvidenceReferenceError(
            "source cell IDs are not in canonical order"
        )
    expected_ids = [
        cell.cell_id for cell in canonical if cell.cell_id in covered_ids
    ]
    if expected_ids != canonical_cell_ids or covered_ids != set(canonical_cell_ids):
        raise LocatorDerivationError(
            "quote range and canonical source cells differ"
        )

    selected_ids = set(canonical_cell_ids)
    selected_occurrences = [
        occurrence
        for occurrence in occurrences
        if occurrence.cell_id in selected_ids
        and occurrence.canonical_start < selected_range.end
        and occurrence.canonical_end > selected_range.start
    ]
    occurrence_counts = Counter(
        occurrence.cell_id for occurrence in selected_occurrences
    )
    if any(occurrence_counts[cell_id] != 1 for cell_id in canonical_cell_ids):
        raise EvidenceReferenceError(
            "each source cell must have exactly one occurrence in the quote range"
        )
    if selected_range.start < 0 or selected_range.end > len(block_text):
        raise LocatorDerivationError(
            "selected quote range exceeds source block text"
        )
    reconstructed_text = block_text[selected_range.start:selected_range.end]

    cell_boxes = [cell.bbox for cell in canonical]
    bbox = (
        _bbox_union([box for box in cell_boxes if box is not None])
        if all(box is not None for box in cell_boxes)
        else None
    )
    cell_pages = {cell.page for cell in canonical if cell.page is not None}
    if len(cell_pages) > 1:
        raise EvidenceReferenceError("selected table cells span multiple pages")
    page = next(iter(cell_pages), table.page if table.page is not None else block.page)
    if any(
        value is not None and page is not None and value != page
        for value in (table.page, block.page)
    ):
        raise LocatorDerivationError(
            "table, block, and selected cell pages differ"
        )
    return DerivedTableEvidence(
        locator=TableLocator(
            table_id=block.table_id,
            cell_ids=list(canonical_cell_ids),
            row_indices=[cell.row_index for cell in canonical],
            selected_row_index=selected_row_index,
            column_indices=[cell.column_index for cell in canonical],
            bbox=bbox,
        ),
        reconstructed_text=reconstructed_text,
        page=page,
    )


def derive_page_locator(
    evidence_index: EvidenceIndex,
    *,
    block_id: str,
    selected_range: QuoteMatchRange,
    table_evidence: DerivedTableEvidence | None = None,
) -> PageLocator | None:
    blocks_by_id = {block.block_id: block for block in evidence_index.blocks}
    fragments_by_id = {
        fragment.fragment_id: fragment for fragment in evidence_index.fragments
    }
    block = blocks_by_id.get(block_id)
    if block is None or block.page is None:
        return None
    page = next((record for record in evidence_index.pages if record.page == block.page), None)
    if page is None:
        return None
    overlapping_fragments = [
        fragments_by_id[fragment_id]
        for fragment_id in block.fragment_ids
        if fragments_by_id[fragment_id].start < selected_range.end
        and fragments_by_id[fragment_id].end > selected_range.start
    ]
    if overlapping_fragments:
        bbox = _bbox_union([fragment.bbox for fragment in overlapping_fragments])
        derivation = "quote_span_union"
    elif table_evidence is not None and table_evidence.locator.bbox is not None:
        bbox = table_evidence.locator.bbox
        derivation = "table_cell_union"
    elif block.bbox is not None:
        bbox = block.bbox
        derivation = "block_bbox"
    else:
        return None
    return PageLocator(
        page=block.page,
        bbox=bbox,
        page_width=page.width,
        page_height=page.height,
        source_rotation=page.source_rotation,
        coordinate_space=page.coordinate_space,
        derivation=derivation,
    )


def _bbox_union(boxes: list[BoundingBox]) -> BoundingBox:
    if not boxes:
        raise LocatorDerivationError("bbox union requires at least one box")
    coordinate_space = boxes[0].coordinate_space
    if any(box.coordinate_space != coordinate_space for box in boxes):
        raise LocatorDerivationError(
            "bbox union requires one coordinate space"
        )
    return BoundingBox(
        x0=min(box.x0 for box in boxes),
        y0=min(box.y0 for box in boxes),
        x1=max(box.x1 for box in boxes),
        y1=max(box.y1 for box in boxes),
        coordinate_space=coordinate_space,
    )
