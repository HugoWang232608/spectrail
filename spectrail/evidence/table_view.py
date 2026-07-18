from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import Field

from spectrail.evidence.models import BoundingBox, EvidenceIndex, EvidenceModel


class TableEvidenceViewNotFoundError(ValueError):
    pass


class TableEvidenceOccurrenceView(EvidenceModel):
    occurrence_id: str
    occurrence_role: Literal[
        "original",
        "repeated_header",
        "row_span_projection",
        "duplicate_text_occurrence",
    ]
    canonical_start: int
    canonical_end: int


class TableEvidenceCellView(EvidenceModel):
    cell_id: str
    row_index: int
    column_index: int
    row_span: int
    column_span: int
    text: str
    is_header: bool
    page: int | None = None
    bbox: BoundingBox | None = None
    occurrences: list[TableEvidenceOccurrenceView] = Field(default_factory=list)


class TableEvidenceRowView(EvidenceModel):
    physical_row_index: int
    rendered_start: int
    rendered_end: int
    repeated_header: bool = False
    cells: list[TableEvidenceCellView] = Field(default_factory=list)


class TableEvidenceView(EvidenceModel):
    schema_version: Literal["table_evidence_view_v1"] = "table_evidence_view_v1"
    task_id: str
    evidence_fingerprint: str
    table_id: str
    block_id: str
    row_count: int
    column_count: int
    topology_status: Literal["complete", "sparse"]
    page: int | None = None
    bbox: BoundingBox | None = None
    primary_row_start: int
    primary_row_end: int
    continuation_role: Literal["single", "start", "continuation"] = "single"
    continuation_group_id: str | None = None
    continuation_sequence: int | None = None
    continuation_of_table_id: str | None = None
    continuation_label: str | None = None
    continuation_basis: Literal[
        "legacy_header_geometry_heuristic",
        "explicit_marker_page_edge_header_match",
    ] | None = None
    continued_header_cell_ids: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    rows: list[TableEvidenceRowView] = Field(default_factory=list)


def build_table_evidence_view(
    index: EvidenceIndex,
    *,
    task_id: str,
    table_id: str,
    block_id: str,
) -> TableEvidenceView:
    tables_by_id = {table.table_id: table for table in index.tables}
    blocks_by_id = {block.block_id: block for block in index.blocks}
    cells_by_id = {cell.cell_id: cell for cell in index.cells}

    table = tables_by_id.get(table_id)
    if table is None:
        raise TableEvidenceViewNotFoundError(f"table evidence not found: {table_id}")
    block = blocks_by_id.get(block_id)
    if block is None:
        raise TableEvidenceViewNotFoundError(f"table block not found: {block_id}")
    if block.table_id != table_id or block_id not in table.block_ids:
        raise TableEvidenceViewNotFoundError(
            f"block {block_id} does not belong to table {table_id}"
        )
    if block.table_row_start is None or block.table_row_end is None:
        raise TableEvidenceViewNotFoundError(
            f"table block has no primary row range: {block_id}"
        )

    occurrences_by_row_and_cell: dict[
        tuple[int, str], list[TableEvidenceOccurrenceView]
    ] = defaultdict(list)
    row_ranges: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for occurrence in index.cell_occurrences:
        if occurrence.block_id != block_id:
            continue
        key = (occurrence.physical_row_index, occurrence.cell_id)
        occurrences_by_row_and_cell[key].append(
            TableEvidenceOccurrenceView(
                occurrence_id=occurrence.occurrence_id,
                occurrence_role=occurrence.occurrence_role,
                canonical_start=occurrence.canonical_start,
                canonical_end=occurrence.canonical_end,
            )
        )
        row_ranges[occurrence.physical_row_index].append(
            (occurrence.canonical_start, occurrence.canonical_end)
        )

    rows: list[TableEvidenceRowView] = []
    for physical_row_index, ranges in row_ranges.items():
        row_cells: list[TableEvidenceCellView] = []
        repeated_header = True
        for (row_index, cell_id), occurrence_views in occurrences_by_row_and_cell.items():
            if row_index != physical_row_index:
                continue
            cell = cells_by_id[cell_id]
            occurrence_views.sort(
                key=lambda item: (
                    item.canonical_start,
                    item.canonical_end,
                    item.occurrence_id,
                )
            )
            if any(
                item.occurrence_role != "repeated_header"
                for item in occurrence_views
            ):
                repeated_header = False
            row_cells.append(
                TableEvidenceCellView(
                    cell_id=cell.cell_id,
                    row_index=cell.row_index,
                    column_index=cell.column_index,
                    row_span=cell.row_span,
                    column_span=cell.column_span,
                    text=cell.text,
                    is_header=cell.is_header,
                    page=cell.page,
                    bbox=cell.bbox,
                    occurrences=occurrence_views,
                )
            )
        row_cells.sort(
            key=lambda item: (item.column_index, item.row_index, item.cell_id)
        )
        rows.append(
            TableEvidenceRowView(
                physical_row_index=physical_row_index,
                rendered_start=min(start for start, _ in ranges),
                rendered_end=max(end for _, end in ranges),
                repeated_header=repeated_header,
                cells=row_cells,
            )
        )

    rows.sort(
        key=lambda item: (
            item.rendered_start,
            item.rendered_end,
            item.physical_row_index,
        )
    )
    if not rows:
        raise TableEvidenceViewNotFoundError(
            f"table block has no cell occurrences: {block_id}"
        )

    return TableEvidenceView(
        task_id=task_id,
        evidence_fingerprint=index.evidence_fingerprint,
        table_id=table.table_id,
        block_id=block.block_id,
        row_count=table.row_count,
        column_count=table.column_count,
        topology_status=table.topology_status,
        page=table.page,
        bbox=table.bbox,
        primary_row_start=block.table_row_start,
        primary_row_end=block.table_row_end,
        continuation_role=table.continuation_role,
        continuation_group_id=table.continuation_group_id,
        continuation_sequence=table.continuation_sequence,
        continuation_of_table_id=table.continuation_of_table_id,
        continuation_label=table.continuation_label,
        continuation_basis=table.continuation_basis,
        continued_header_cell_ids=dict(table.continued_header_cell_ids),
        warnings=list(table.warnings),
        rows=rows,
    )
