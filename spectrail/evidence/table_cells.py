from collections.abc import Sequence

from spectrail.evidence.errors import EvidenceReferenceError
from spectrail.evidence.models import TableCellRecord


def canonicalize_nonempty_cell_selection(
    selected_cells: Sequence[TableCellRecord],
    available_cells: Sequence[TableCellRecord],
) -> list[TableCellRecord]:
    if len({cell.cell_id for cell in selected_cells}) != len(selected_cells):
        raise EvidenceReferenceError("source cell IDs must be unique")
    if selected_cells and len({cell.table_id for cell in selected_cells}) != 1:
        raise EvidenceReferenceError("source cells must belong to one table")
    if selected_cells and len({cell.row_index for cell in selected_cells}) != 1:
        raise EvidenceReferenceError("source cells must belong to one logical row")

    canonical = sorted(
        (cell for cell in selected_cells if cell.text.strip()),
        key=lambda cell: (
            cell.table_id,
            cell.row_index,
            cell.column_index,
            cell.cell_id,
        ),
    )
    if not canonical:
        raise EvidenceReferenceError(
            "source cells must include at least one non-empty logical cell"
        )

    table_id = canonical[0].table_id
    row_index = canonical[0].row_index
    selected_min = canonical[0].column_index
    selected_max = max(
        cell.column_index + cell.column_span for cell in canonical
    )
    for cell in selected_cells:
        cell_end = cell.column_index + cell.column_span
        if not cell.text.strip() and not (
            cell.table_id == table_id
            and cell.row_index == row_index
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
        and cell.row_index == row_index
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
    return canonical
