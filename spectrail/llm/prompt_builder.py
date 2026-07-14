from __future__ import annotations

import json
from collections.abc import Iterable

from spectrail.core.models import DocumentBlock
from spectrail.evidence.models import (
    EvidenceIndex,
    TableCellRecord,
    rendered_table_row_groups,
)
from spectrail.llm.base import ModelRequest


PROMPT_VERSION = "reqir_extraction_v9_table_row_evidence_v4"
CHUNKED_PROMPT_VERSION = PROMPT_VERSION


def build_reqir_prompt(request: ModelRequest, *, max_blocks: int | None = None) -> str:
    blocks = request.blocks[:max_blocks] if max_blocks is not None else request.blocks
    rendered_blocks = "\n\n".join(
        _render_block(block, request.evidence_index) for block in blocks
    )
    chunk_context = ""
    if request.metadata.get("chunked") or request.metadata.get("chunk_id"):
        chunk_context = (
            "Chunk:\n"
            f"- chunk_id: {request.metadata.get('chunk_id', '')}\n"
            f"- chunk_index: {request.metadata.get('chunk_index_rendered', request.metadata.get('chunk_index', ''))}\n"
            f"- chunk_count: {request.metadata.get('chunk_count_rendered', request.metadata.get('chunk_count', ''))}\n"
            f"- new_block_ids: {request.metadata.get('new_block_ids', [])}\n"
            f"- overlap_block_ids: {request.metadata.get('overlap_block_ids', [])}\n"
            f"- context_block_ids: {request.metadata.get('context_block_ids', [])}\n"
            "- Use only shown blocks; context headings are never sources.\n\n"
        )
    quote_only = request.metadata.get("evidence_policy") == "quote_only"
    table_item_contract = (
        "cell_map sources may omit source_cell_ids and source_table_row_index.\n\n"
        if quote_only
        else "cell_map sources require source_cell_ids and source_table_row_index.\n\n"
    )
    table_cell_contract = (
        "- In quote_only, table identity is optional; if used, follow the table rules.\n"
        if quote_only
        else "- For cell_map sources, output table identity using the table rules.\n"
    )
    table_overlap_contract = "- A table source_quote may select part of a cell's text.\n"
    return (
        "Extract software requirements into ReqIR JSON.\n"
        "Return JSON only with top-level items.\n\n"
        "Item fields: title, type, ears_pattern, statement, subject, response, "
        "source_block_id, source_quote, confidence, tags. "
        f"{table_item_contract}"
        "Enums:\n"
        "- type: functional | non_functional | interface | constraint | business | unknown\n"
        "- ears_pattern: ubiquitous | event_driven | state_driven | optional | unwanted_behavior | unknown\n"
        "- priority: high | medium | low | unknown\n"
        "- verification_method: test | inspection | analysis | demonstration | unknown\n\n"
        "Rules:\n"
        "- Use a shown source_block_id and an exact source_quote substring.\n"
        f"{table_cell_contract}"
        f"{table_overlap_contract}"
        "- Selected table cells must occupy one displayed physical row. Include every "
        "non-empty cell in the quote's column span; omit empty cells. ID order is irrelevant.\n"
        "- source_table_row_index equals N in row N or repeated_header_row N.\n"
        "- Never invent cell IDs. Do not output page, bbox, or derived row/column arrays.\n"
        "- confidence is numeric 0.0..1.0. Use unknown for unsupported enums.\n"
        "- Do not invent unsupported requirements.\n\n"
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
    projected_rows = []
    for _, _, label, row_index, row_occurrences in rendered_table_row_groups(
        block,
        block_occurrences,
    ):
        if label == "row":
            cells = _sort_cells(
                cells_by_id[cell_id]
                for cell_id in table.cell_ids
                if cells_by_id[cell_id].occupies_row(row_index)
            )
        else:
            cells = _sort_cells(
                cells_by_id[cell_id]
                for cell_id in {
                    occurrence.cell_id for occurrence in row_occurrences
                }
            )
        projected_rows.append((label, row_index, cells))
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
