from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from spectrail.core.models import DocumentBlock
from spectrail.evidence.models import (
    CellBlockOccurrence,
    EvidenceIndex,
    TableCellRecord,
)
from spectrail.llm.base import ModelRequest


PROMPT_VERSION = "reqir_extraction_v8_row_group_evidence_v4"
CHUNKED_PROMPT_VERSION = PROMPT_VERSION


def build_reqir_prompt(request: ModelRequest, *, max_blocks: int | None = None) -> str:
    blocks = request.blocks[:max_blocks] if max_blocks is not None else request.blocks
    rendered_blocks = "\n\n".join(
        _render_block(block, request.evidence_index) for block in blocks
    )
    chunk_context = ""
    if request.metadata.get("chunked") or request.metadata.get("chunk_id"):
        chunk_context = (
            "Chunk context:\n"
            f"- chunk_id: {request.metadata.get('chunk_id', '')}\n"
            f"- chunk_index: {request.metadata.get('chunk_index_rendered', request.metadata.get('chunk_index', ''))}\n"
            f"- chunk_count: {request.metadata.get('chunk_count_rendered', request.metadata.get('chunk_count', ''))}\n"
            f"- new_block_ids: {request.metadata.get('new_block_ids', [])}\n"
            f"- overlap_block_ids: {request.metadata.get('overlap_block_ids', [])}\n"
            f"- context_block_ids: {request.metadata.get('context_block_ids', [])}\n"
            "- Extract requirements only from the blocks in this chunk.\n"
            "- Heading context blocks are context only; never extract or cite them as requirements.\n"
            "- Never cite a block ID that is absent from this chunk.\n\n"
        )
    quote_only = request.metadata.get("evidence_policy") == "quote_only"
    table_item_contract = (
        "a cell_map may optionally include source_cell_ids.\n\n"
        if quote_only
        else "a cell_map must also include source_cell_ids.\n\n"
    )
    table_cell_contract = (
        "- For a table block with a cell_map, source_cell_ids are optional under "
        "the quote_only evidence policy. If supplied, they must contain the logical "
        "non-empty cell IDs covered by source_quote.\n"
        if quote_only
        else "- For a table block with a cell_map, source_cell_ids must contain the "
        "non-empty logical cell IDs covered by source_quote.\n"
    )
    table_overlap_contract = "- A table source_quote may select part of a cell's text.\n"
    return (
        "You are extracting software requirements into ReqIR JSON.\n\n"
        "Return JSON only with a top-level items array.\n\n"
        "Each item must include title, type, ears_pattern, statement, subject, response, "
        "source_block_id, source_quote, confidence, and tags. Table blocks that display "
        f"{table_item_contract}"
        "Allowed enum values:\n"
        "- type: functional | non_functional | interface | constraint | business | unknown\n"
        "- ears_pattern: ubiquitous | event_driven | state_driven | optional | unwanted_behavior | unknown\n"
        "- priority: high | medium | low | unknown\n"
        "- verification_method: test | inspection | analysis | demonstration | unknown\n\n"
        "Rules:\n"
        "- source_block_id must be one of the provided block IDs.\n"
        "- source_quote must be an exact substring from the chosen block text.\n"
        f"{table_cell_contract}"
        f"{table_overlap_contract}"
        "- Table cells must occupy one physical row displayed in the cell_map. Omit empty cells from "
        "source_cell_ids; "
        "within the selected column span, include every non-empty cell. Output order "
        "is identity-insignificant.\n"
        "- Never invent a cell ID or cite a cell that is absent from the chosen "
        "block cell_map.\n"
        "- Do not output page, bbox, row, or column fields.\n"
        "- confidence must be a number from 0.0 to 1.0, not textual labels such as high/medium/low.\n"
        "- Use unknown for unsupported enum values instead of inventing new enum labels.\n"
        "- Do not invent requirements not supported by source text.\n\n"
        f"Document: {request.document_name}\n"
        f"Source format: {request.source_format}\n"
        f"Parser: {request.parser_name}\n\n"
        f"{chunk_context}"
        "Blocks:\n\n"
        f"{rendered_blocks}"
    )


def _render_block(
    block: DocumentBlock,
    evidence_index: EvidenceIndex | None,
) -> str:
    section_path = " > ".join(block.section_path) if block.section_path else ""
    table_projection = _table_projection(block.block_id, evidence_index)
    if block.type == "table" and table_projection is not None:
        table_id, row_start, row_end, projected_rows = table_projection
        cell_map = "\n".join(
            f"{label} {row_index}: "
            + ", ".join(
                f"c{cell.column_index}={cell.cell_id} "
                f"(anchor_row={cell.row_index}, column_span={cell.column_span}, "
                f"row_span={cell.row_span}, "
                f"text={json.dumps(cell.text, ensure_ascii=False)})"
                for cell in cells
            )
            for label, row_index, cells in projected_rows
        )
        return (
            f"[{block.block_id}]\n"
            f"type: {block.type}\n"
            f"section_path: {section_path}\n"
            f"table_id: {table_id}\n"
            f"primary_rows: {row_start}-{row_end}\n"
            f"canonical_text: {block.text}\n"
            f"cell_map:\n{cell_map}"
        )
    return (
        f"[{block.block_id}]\n"
        f"type: {block.type}\n"
        f"section_path: {section_path}\n"
        f"text: {block.text}"
    )


def _table_projection(
    block_id: str,
    evidence_index: EvidenceIndex | None,
) -> tuple[str, int, int, list[tuple[str, int, list[TableCellRecord]]]] | None:
    if evidence_index is None:
        return None
    block = next(
        (item for item in evidence_index.blocks if item.block_id == block_id),
        None,
    )
    if (
        block is None
        or block.table_id is None
        or block.table_row_start is None
        or block.table_row_end is None
        or "table_cell" not in block.available_capabilities
        or not block.cell_ids
    ):
        return None
    cells_by_id = {cell.cell_id: cell for cell in evidence_index.cells}
    table = next(item for item in evidence_index.tables if item.table_id == block.table_id)
    block_occurrences = [
        occurrence
        for occurrence in evidence_index.cell_occurrences
        if occurrence.block_id == block_id
    ]
    rendered_rows = []
    for row_index in range(block.table_row_start, block.table_row_end + 1):
        row_occurrences = [
            occurrence
            for occurrence in block_occurrences
            if occurrence.physical_row_index == row_index
            and occurrence.occurrence_role in {"original", "row_span_projection"}
        ]
        rendered_rows.append(
            _rendered_row_entry(
                "row",
                row_index,
                _sort_cells(
                    cells_by_id[cell_id]
                    for cell_id in table.cell_ids
                    if cells_by_id[cell_id].occupies_row(row_index)
                ),
                row_occurrences,
            )
        )
    repeated_by_row: dict[int, set[str]] = {}
    for occurrence in block_occurrences:
        if (
            occurrence.block_id == block_id
            and occurrence.occurrence_role == "repeated_header"
        ):
            repeated_by_row.setdefault(occurrence.physical_row_index, set()).add(
                occurrence.cell_id
            )
    for row_index, cell_ids in repeated_by_row.items():
        row_occurrences = [
            occurrence
            for occurrence in block_occurrences
            if occurrence.physical_row_index == row_index
            and occurrence.occurrence_role == "repeated_header"
        ]
        rendered_rows.append(
            _rendered_row_entry(
                "repeated_header_row",
                row_index,
                _sort_cells(cells_by_id[cell_id] for cell_id in cell_ids),
                row_occurrences,
            )
        )
    projected_rows = [
        (label, row_index, cells)
        for _, _, row_index, label, cells in sorted(
            rendered_rows,
            key=lambda item: item[:4],
        )
    ]
    return (
        block.table_id,
        block.table_row_start,
        block.table_row_end,
        projected_rows,
    )


def _sort_cells(cells: Iterable[TableCellRecord]) -> list[TableCellRecord]:
    return sorted(
        cells,
        key=lambda cell: (
            cell.table_id,
            cell.column_index,
            cell.row_index,
            cell.cell_id,
        ),
    )


def _rendered_row_entry(
    label: str,
    row_index: int,
    cells: list[TableCellRecord],
    occurrences: Sequence[CellBlockOccurrence],
) -> tuple[int, int, int, str, list[TableCellRecord]]:
    if not occurrences:
        raise ValueError(
            f"rendered table row has no canonical occurrences: {label}/{row_index}"
        )
    return (
        min(item.canonical_start for item in occurrences),
        max(item.canonical_end for item in occurrences),
        row_index,
        label,
        cells,
    )
