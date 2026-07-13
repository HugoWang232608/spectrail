from __future__ import annotations

from spectrail.core.models import RequirementIR, SourceSpan
from spectrail.evidence.errors import EvidenceReferenceError
from spectrail.evidence.models import (
    BlockEvidenceRecord,
    EvidenceIndex,
    TableCellRecord,
    TableRecord,
)
from spectrail.evidence.table_cells import canonicalize_nonempty_cell_selection


def canonicalize_source_cell_ids(
    requirements: list[RequirementIR],
    evidence_index: EvidenceIndex,
) -> list[RequirementIR]:
    blocks_by_id = {block.block_id: block for block in evidence_index.blocks}
    cells_by_id = {cell.cell_id: cell for cell in evidence_index.cells}
    tables_by_id = {table.table_id: table for table in evidence_index.tables}
    occurrence_pairs = {
        (occurrence.block_id, occurrence.cell_id)
        for occurrence in evidence_index.cell_occurrences
    }
    for requirement in requirements:
        for source in requirement.sources:
            source.canonical_source_cell_ids = _canonical_cell_ids(
                source,
                blocks_by_id,
                cells_by_id,
                tables_by_id,
                occurrence_pairs,
            )
    return requirements


def _canonical_cell_ids(
    source: SourceSpan,
    blocks_by_id: dict[str, BlockEvidenceRecord],
    cells_by_id: dict[str, TableCellRecord],
    tables_by_id: dict[str, TableRecord],
    occurrence_pairs: set[tuple[str, str]],
) -> list[str]:
    raw = source.source_cell_ids_raw or source.canonical_source_cell_ids
    if not raw:
        return []
    if len(set(raw)) != len(raw):
        raise EvidenceReferenceError("source_cell_ids_raw must be unique")
    block = blocks_by_id.get(source.block_id)
    if block is None:
        raise EvidenceReferenceError(
            "table source references an unknown evidence block"
        )
    if block.table_id is None:
        raise EvidenceReferenceError(
            "source_cell_ids_raw require a table evidence block"
        )
    try:
        cells = [cells_by_id[cell_id] for cell_id in raw]
    except KeyError as exc:
        raise EvidenceReferenceError(
            f"source references an unknown logical cell: {exc.args[0]}"
        ) from exc
    if any(cell.table_id != block.table_id for cell in cells):
        raise EvidenceReferenceError(
            "source cells must belong to the source block table"
        )
    if any((source.block_id, cell.cell_id) not in occurrence_pairs for cell in cells):
        raise EvidenceReferenceError(
            "source cell has no occurrence in the source block"
        )
    if block.table_row_index is None:
        raise EvidenceReferenceError("table source block has no physical row index")
    canonical = canonicalize_nonempty_cell_selection(
        cells,
        [cells_by_id[cell_id] for cell_id in block.cell_ids],
        table=tables_by_id[block.table_id],
        selected_row_index=block.table_row_index,
    )
    return [cell.cell_id for cell in canonical]
