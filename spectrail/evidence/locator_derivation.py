from __future__ import annotations

from dataclasses import dataclass

from spectrail.evidence.models import (
    BoundingBox,
    EvidenceIndex,
    PageLocator,
    TableLocator,
)
from spectrail.evidence.quote_matcher import QuoteMatchRange


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
        raise ValueError("table locator requires a table evidence block")
    if not canonical_cell_ids:
        raise ValueError("table locator requires canonical source cell IDs")
    try:
        cells = [cells_by_id[cell_id] for cell_id in canonical_cell_ids]
    except KeyError as exc:
        raise ValueError(f"table locator references an unknown cell: {exc.args[0]}") from exc
    canonical = sorted(
        cells,
        key=lambda cell: (
            cell.table_id,
            cell.row_index,
            cell.column_index,
            cell.cell_id,
        ),
    )
    if [cell.cell_id for cell in canonical] != canonical_cell_ids:
        raise ValueError("source cell IDs are not in canonical order")
    if any(cell.table_id != block.table_id for cell in canonical):
        raise ValueError("source cells do not belong to the source block table")
    if len({cell.row_index for cell in canonical}) != 1:
        raise ValueError("source cells must belong to one logical row")
    columns = [cell.column_index for cell in canonical]
    if columns != list(range(columns[0], columns[0] + len(columns))):
        raise ValueError("source cell columns must be contiguous")

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
    covered_ids = {
        occurrence.cell_id
        for occurrence in occurrences
        if occurrence.canonical_start < selected_range.end
        and occurrence.canonical_end > selected_range.start
        and cells_by_id[occurrence.cell_id].text.strip()
    }
    expected_ids = [
        cell.cell_id for cell in canonical if cell.cell_id in covered_ids
    ]
    if expected_ids != canonical_cell_ids or covered_ids != set(canonical_cell_ids):
        raise ValueError("quote range and canonical source cells differ")

    selected_occurrences = [
        occurrence
        for occurrence in occurrences
        if occurrence.cell_id in set(canonical_cell_ids)
    ]
    if len({occurrence.cell_id for occurrence in selected_occurrences}) != len(
        canonical_cell_ids
    ):
        raise ValueError("source cell occurrence is missing from the source block")
    reconstruction_start = min(
        occurrence.canonical_start for occurrence in selected_occurrences
    )
    reconstruction_end = max(
        occurrence.canonical_end for occurrence in selected_occurrences
    )
    if reconstruction_start < 0 or reconstruction_end > len(block_text):
        raise ValueError("cell occurrence range exceeds source block text")
    reconstructed_text = block_text[reconstruction_start:reconstruction_end]

    cell_boxes = [cell.bbox for cell in canonical]
    bbox = (
        _bbox_union([box for box in cell_boxes if box is not None])
        if all(box is not None for box in cell_boxes)
        else None
    )
    cell_pages = {cell.page for cell in canonical if cell.page is not None}
    if len(cell_pages) > 1:
        raise ValueError("selected table cells span multiple pages")
    table = tables_by_id[block.table_id]
    page = next(iter(cell_pages), table.page if table.page is not None else block.page)
    if any(
        value is not None and page is not None and value != page
        for value in (table.page, block.page)
    ):
        raise ValueError("table, block, and selected cell pages differ")
    return DerivedTableEvidence(
        locator=TableLocator(
            table_id=block.table_id,
            cell_ids=list(canonical_cell_ids),
            row_indices=[cell.row_index for cell in canonical],
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
        raise ValueError("bbox union requires at least one box")
    coordinate_space = boxes[0].coordinate_space
    if any(box.coordinate_space != coordinate_space for box in boxes):
        raise ValueError("bbox union requires one coordinate space")
    return BoundingBox(
        x0=min(box.x0 for box in boxes),
        y0=min(box.y0 for box in boxes),
        x1=max(box.x1 for box in boxes),
        y1=max(box.y1 for box in boxes),
        coordinate_space=coordinate_space,
    )
