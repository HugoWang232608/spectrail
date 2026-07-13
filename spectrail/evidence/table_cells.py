from collections.abc import Sequence

from spectrail.evidence.errors import EvidenceReferenceError
from spectrail.evidence.models import TableCellRecord


def require_contiguous_cell_spans(cells: Sequence[TableCellRecord]) -> None:
    for current, following in zip(cells, cells[1:]):
        current_end = current.column_index + current.column_span
        if current_end != following.column_index:
            raise EvidenceReferenceError(
                "source cell occupied column spans must be contiguous"
            )
