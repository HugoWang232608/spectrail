from collections.abc import Sequence

from spectrail.evidence.errors import EvidenceReferenceError
from spectrail.evidence.models import TableCellRecord, TableRecord


def canonicalize_nonempty_cell_selection(
    selected_cells: Sequence[TableCellRecord],
    available_cells: Sequence[TableCellRecord],
    *,
    table: TableRecord,
    selected_row_index: int | None = None,
) -> list[TableCellRecord]:
    if len({cell.cell_id for cell in selected_cells}) != len(selected_cells):
        raise EvidenceReferenceError("source cell IDs must be unique")
    if selected_cells and len({cell.table_id for cell in selected_cells}) != 1:
        raise EvidenceReferenceError("source cells must belong to one table")
    canonical = sorted(
        (cell for cell in selected_cells if cell.text.strip()),
        key=lambda cell: (
            cell.table_id,
            cell.column_index,
            cell.row_index,
            cell.cell_id,
        ),
    )
    if not canonical:
        raise EvidenceReferenceError(
            "source cells must include at least one non-empty logical cell"
        )

    if selected_row_index is None:
        candidate_rows = [
            row_index
            for row_index in range(1, table.row_count + 1)
            if all(cell.occupies_row(row_index) for cell in selected_cells)
        ]
        for row_index in candidate_rows:
            try:
                return _validate_selection_on_row(
                    selected_cells,
                    available_cells,
                    canonical,
                    table=table,
                    selected_row_index=row_index,
                )
            except EvidenceReferenceError:
                continue
        raise EvidenceReferenceError(
            "source cells do not form a valid selection on any shared physical row"
        )
    return _validate_selection_on_row(
        selected_cells,
        available_cells,
        canonical,
        table=table,
        selected_row_index=selected_row_index,
    )


def _validate_selection_on_row(
    selected_cells: Sequence[TableCellRecord],
    available_cells: Sequence[TableCellRecord],
    canonical: list[TableCellRecord],
    *,
    table: TableRecord,
    selected_row_index: int,
) -> list[TableCellRecord]:
    if selected_row_index < 1 or selected_row_index > table.row_count:
        raise EvidenceReferenceError("selected physical table row is out of bounds")
    if any(not cell.occupies_row(selected_row_index) for cell in selected_cells):
        raise EvidenceReferenceError(
            "source cells must occupy the selected physical table row"
        )

    table_id = canonical[0].table_id
    selected_min = canonical[0].column_index
    selected_max = max(
        cell.column_index + cell.column_span for cell in canonical
    )
    for cell in selected_cells:
        cell_end = cell.column_index + cell.column_span
        if not cell.text.strip() and not (
            cell.table_id == table_id
            and cell.occupies_row(selected_row_index)
            and cell.column_index >= selected_min
            and cell_end <= selected_max
        ):
            raise EvidenceReferenceError(
                "selected empty cells must lie between selected non-empty cells"
            )

    selected_ids = {cell.cell_id for cell in canonical}
    missing_non_empty = [
        cell.cell_id
        for cell in available_cells
        if cell.table_id == table_id
        and cell.occupies_row(selected_row_index)
        and cell.text.strip()
        and cell.column_index < selected_max
        and cell.column_index + cell.column_span > selected_min
        and cell.cell_id not in selected_ids
    ]
    if missing_non_empty:
        raise EvidenceReferenceError(
            "source cell selection omits non-empty logical cells in its column span: "
            f"{missing_non_empty}"
        )
    if table.topology_status == "sparse" and _has_unknown_gap(
        available_cells,
        selected_row_index=selected_row_index,
        selected_min=selected_min,
        selected_max=selected_max,
    ):
        raise EvidenceReferenceError(
            "sparse table selection crosses an unknown column gap"
        )
    return canonical


def _has_unknown_gap(
    cells: Sequence[TableCellRecord],
    *,
    selected_row_index: int,
    selected_min: int,
    selected_max: int,
) -> bool:
    intervals = sorted(
        (
            max(cell.column_index, selected_min),
            min(cell.column_index + cell.column_span, selected_max),
        )
        for cell in cells
        if cell.occupies_row(selected_row_index)
        and cell.column_index < selected_max
        and cell.column_index + cell.column_span > selected_min
    )
    cursor = selected_min
    for start, end in intervals:
        if start > cursor:
            return True
        cursor = max(cursor, end)
        if cursor >= selected_max:
            return False
    return cursor < selected_max
