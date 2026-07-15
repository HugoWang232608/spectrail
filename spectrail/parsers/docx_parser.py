from __future__ import annotations

from dataclasses import dataclass, replace
from importlib.metadata import PackageNotFoundError as DistributionNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Iterator

from spectrail.core.ids import block_id
from spectrail.core.models import DocumentBlock
from spectrail.evidence.fingerprint import (
    finalize_evidence_fingerprint,
    sha256_file,
    sha256_text,
)
from spectrail.evidence.ids import cell_id, occurrence_id, table_id
from spectrail.evidence.models import (
    BlockEvidenceRecord,
    CellBlockOccurrence,
    EvidenceIndex,
    ParserIdentity,
    TableCellRecord,
    TableRecord,
)
from spectrail.parsers.base import DocumentParseError, ParsedDocument
from spectrail.parsers.render import render_blocks_to_markdown


MAX_ROWS_PER_TABLE_BLOCK = 20
REPEAT_HEADER_ROWS = 1
TABLE_CELL_SEPARATOR = " | "
TABLE_ROW_SEPARATOR = "\n"


class _TableEvidenceUnavailable(ValueError):
    """The table can be rendered as text but not trusted as cell evidence."""


class DocxParserV2:
    parser_name = "docx_parser_v2"
    source_format = "docx"

    def parse(self, path: str | Path, document_id: str = "doc_001") -> ParsedDocument:
        source_path = Path(path)
        try:
            from docx import Document
            from docx.opc.exceptions import PackageNotFoundError
        except ImportError as exc:
            raise DocumentParseError(
                "python-docx is required to parse DOCX documents"
            ) from exc

        try:
            document = Document(source_path)
        except PackageNotFoundError as exc:
            raise DocumentParseError(
                f"invalid DOCX package: {source_path.name}"
            ) from exc
        except Exception as exc:
            raise DocumentParseError(
                f"failed to parse DOCX document: {source_path.name}"
            ) from exc

        source_hash = sha256_file(source_path)
        parser_identity = _parser_identity()
        blocks: list[DocumentBlock] = []
        evidence_blocks: list[BlockEvidenceRecord] = []
        tables: list[TableRecord] = []
        cells: list[TableCellRecord] = []
        occurrences: list[CellBlockOccurrence] = []
        warnings: list[str] = []
        section_stack: list[str] = []
        source_index = 0
        table_index = 0
        occurrence_index = 0

        for kind, item in _iter_block_items(document):
            source_index += 1
            if kind == "paragraph":
                block = self._paragraph_to_block(
                    paragraph=item,
                    document_id=document_id,
                    order=len(blocks) + 1,
                    source_index=source_index,
                    section_stack=section_stack,
                )
                if block is None:
                    continue
                blocks.append(block)
                evidence_blocks.append(_text_block_evidence(block))
                continue

            table_index += 1
            try:
                grid = _parse_table_grid(item, table_index)
            except _TableEvidenceUnavailable:
                order = len(blocks) + 1
                text = _render_table_text_only(item)
                if not text:
                    continue
                block = DocumentBlock(
                    block_id=block_id(order),
                    document_id=document_id,
                    type="table",
                    text=text,
                    section_path=list(section_stack),
                    order=order,
                    metadata={
                        **self._metadata(source_index),
                        "table_index": table_index,
                        "structured_table_evidence": False,
                    },
                )
                blocks.append(block)
                evidence_blocks.append(_text_block_evidence(block))
                warnings.append(
                    f"DOCX_TABLE_TOPOLOGY_UNAVAILABLE: table {table_index}"
                )
                continue
            table_identifier = table_id(table_index)
            table_block_ids: list[str] = []
            table_occurrence_ids: list[str] = []

            header_row_indices = _repeated_header_row_indices(grid)
            row_groups = _table_row_groups(len(grid.rows))
            for group_index, (row_start, row_end) in enumerate(row_groups):
                order = len(blocks) + 1
                block_identifier = block_id(order)
                repeated_rows = header_row_indices if group_index > 0 else []
                row_text, rendered_occurrences = _render_table_row_group(
                    grid,
                    row_start=row_start,
                    row_end=row_end,
                    repeated_header_rows=repeated_rows,
                )
                row_cell_ids = list(
                    dict.fromkeys(item.cell.cell_id for item in rendered_occurrences)
                )
                block = DocumentBlock(
                    block_id=block_identifier,
                    document_id=document_id,
                    type="table",
                    text=row_text,
                    section_path=list(section_stack),
                    order=order,
                    metadata={
                        **self._metadata(source_index),
                        "table_id": table_identifier,
                        "table_index": table_index,
                        "table_row_start": row_start,
                        "table_row_end": row_end,
                        "repeated_header_rows": repeated_rows,
                    },
                )
                blocks.append(block)
                table_block_ids.append(block_identifier)
                evidence_blocks.append(
                    BlockEvidenceRecord(
                        block_id=block_identifier,
                        text_length=len(row_text),
                        text_sha256=sha256_text(row_text),
                        table_id=table_identifier,
                        table_row_start=row_start,
                        table_row_end=row_end,
                        cell_ids=row_cell_ids,
                        expected_capabilities=["text_range", "table_cell"],
                        available_capabilities=["text_range", "table_cell"],
                    )
                )
                for rendered in rendered_occurrences:
                    occurrence_index += 1
                    occurrence_identifier = occurrence_id(occurrence_index)
                    occurrences.append(
                        CellBlockOccurrence(
                            occurrence_id=occurrence_identifier,
                            cell_id=rendered.cell.cell_id,
                            block_id=block_identifier,
                            physical_row_index=rendered.physical_row_index,
                            canonical_start=rendered.start,
                            canonical_end=rendered.end,
                            occurrence_role=rendered.role,
                        )
                    )
                    table_occurrence_ids.append(occurrence_identifier)

            table_cells = [cell.to_record(table_identifier) for cell in grid.cells]
            cells.extend(table_cells)
            tables.append(
                TableRecord(
                    table_id=table_identifier,
                    block_ids=table_block_ids,
                    row_count=len(grid.rows),
                    column_count=grid.column_count,
                    cell_ids=[cell.cell_id for cell in grid.cells],
                    occurrence_ids=table_occurrence_ids,
                    parser_method="docx_xml",
                    topology_status="complete",
                    warnings=[
                        *grid.warnings,
                        *(
                            ["DOCX_REPEATED_HEADER_PROJECTED"]
                            if len(row_groups) > 1 and header_row_indices
                            else []
                        ),
                    ],
                )
            )
            warnings.extend(grid.warnings)

        if not blocks:
            raise DocumentParseError(
                f"docx document has no extractable text: {source_path.name}"
            )

        evidence_index = finalize_evidence_fingerprint(
            EvidenceIndex(
                document_id=document_id,
                document_name=source_path.name,
                source_format=self.source_format,
                source_sha256=source_hash,
                parser_identity=parser_identity,
                evidence_fingerprint="0" * 64,
                blocks=evidence_blocks,
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
            metadata={"source_path": source_path.as_posix()},
            source_sha256=source_hash,
            parser_identity=parser_identity,
            evidence_index=evidence_index,
        )

    def _paragraph_to_block(
        self,
        paragraph: object,
        document_id: str,
        order: int,
        source_index: int,
        section_stack: list[str],
    ) -> DocumentBlock | None:
        text = _normalize_text(getattr(paragraph, "text", ""))
        if not text:
            return None

        style_name = _style_name(paragraph)
        metadata = self._metadata(source_index, style=style_name)
        heading_level = _heading_level(style_name)
        if heading_level is not None:
            section_stack[:] = section_stack[: heading_level - 1]
            section_stack.append(text)
            metadata["level"] = heading_level
            block_type = "heading"
            block_section_path = list(section_stack)
        elif _is_list_style(style_name):
            block_type = "list"
            block_section_path = list(section_stack)
        else:
            block_type = "paragraph"
            block_section_path = list(section_stack)

        return DocumentBlock(
            block_id=block_id(order),
            document_id=document_id,
            type=block_type,  # type: ignore[arg-type]
            text=text,
            section_path=block_section_path,
            order=order,
            metadata=metadata,
        )

    def _metadata(self, source_index: int, style: str | None = None) -> dict:
        metadata = {
            "source_format": self.source_format,
            "parser": self.parser_name,
            "source_index": source_index,
        }
        if style:
            metadata["style"] = style
        return metadata


# Backward-compatible import name; registry selection is explicitly V2.
DocxParser = DocxParserV2


@dataclass(frozen=True)
class _RawCell:
    row_index: int
    column_index: int
    column_span: int
    text: str
    vertical_merge: str | None
    horizontal_merge: str | None
    is_header: bool


@dataclass
class _GridCell:
    cell_id: str
    row_index: int
    column_index: int
    row_span: int
    column_span: int
    text: str
    is_header: bool

    def to_record(self, table_identifier: str) -> TableCellRecord:
        return TableCellRecord(
            cell_id=self.cell_id,
            table_id=table_identifier,
            row_index=self.row_index,
            column_index=self.column_index,
            row_span=self.row_span,
            column_span=self.column_span,
            text=self.text,
            text_sha256=sha256_text(self.text),
            is_header=self.is_header,
        )


@dataclass(frozen=True)
class _TableGrid:
    column_count: int
    cells: list[_GridCell]
    rows: list[list[_GridCell]]
    header_rows: list[bool]
    warnings: list[str]


@dataclass(frozen=True)
class _RenderedCellOccurrence:
    cell: _GridCell
    physical_row_index: int
    start: int
    end: int
    role: str


def _parse_table_grid(table: object, table_index: int) -> _TableGrid:
    try:
        from docx.oxml.ns import qn
        from docx.table import _Cell
    except ImportError as exc:  # pragma: no cover - guarded by DocxParser.parse
        raise DocumentParseError("python-docx table support is unavailable") from exc

    table_element = getattr(table, "_tbl")
    row_elements = list(table_element.findall(qn("w:tr")))
    grid = table_element.find(qn("w:tblGrid"))
    declared_columns = (
        len(grid.findall(qn("w:gridCol"))) if grid is not None else 0
    )
    raw_rows: list[list[_RawCell]] = []
    header_rows: list[bool] = []
    warnings: list[str] = []
    measured_columns = declared_columns

    for row_index, row_element in enumerate(row_elements, start=1):
        row_properties = row_element.find(qn("w:trPr"))
        is_header = _on_off_enabled(row_properties, "w:tblHeader", qn)
        header_rows.append(is_header)
        grid_before = _integer_property(row_properties, "w:gridBefore", qn, 0)
        grid_after = _integer_property(row_properties, "w:gridAfter", qn, 0)
        column_index = grid_before + 1
        raw_cells: list[_RawCell] = []
        for cell_element in row_element.findall(qn("w:tc")):
            cell_properties = cell_element.find(qn("w:tcPr"))
            column_span = _integer_property(
                cell_properties,
                "w:gridSpan",
                qn,
                1,
            )
            if column_span < 1:
                raise DocumentParseError(
                    "DOCX table gridSpan must be positive: "
                    f"table {table_index}, row {row_index}, column {column_index}"
                )
            raw_cells.append(
                _RawCell(
                    row_index=row_index,
                    column_index=column_index,
                    column_span=column_span,
                    text=_canonicalize_table_cell_text(
                        _Cell(cell_element, table).text
                    ),
                    vertical_merge=_vertical_merge(cell_properties, qn),
                    horizontal_merge=_horizontal_merge(cell_properties, qn),
                    is_header=is_header,
                )
            )
            if raw_cells[-1].vertical_merge == "invalid":
                warnings.append("DOCX_MERGED_CELL_BEST_EFFORT")
            if raw_cells[-1].horizontal_merge == "invalid":
                warnings.append("DOCX_MERGED_CELL_BEST_EFFORT")
            column_index += column_span
        measured_columns = max(
            measured_columns,
            column_index - 1 + grid_after,
        )
        raw_rows.append(
            _collapse_horizontal_merges(raw_cells, warnings)
        )

    if not raw_rows or measured_columns < 1:
        raise DocumentParseError(
            f"DOCX table has no physical grid: table {table_index}"
        )

    completed_rows = [
        _validate_complete_physical_row(
            raw_cells,
            row_index=row_index,
            column_count=measured_columns,
        )
        for row_index, raw_cells in enumerate(raw_rows, start=1)
    ]
    logical_cells: list[_GridCell] = []
    physical_rows: list[list[_GridCell]] = []
    active_vertical_cells: dict[int, _GridCell] = {}

    for row_index, raw_cells in enumerate(completed_rows, start=1):
        row_cells: list[_GridCell] = []
        next_active: dict[int, _GridCell] = {}
        for raw_cell in raw_cells:
            occupied_columns = range(
                raw_cell.column_index,
                raw_cell.column_index + raw_cell.column_span,
            )
            if raw_cell.vertical_merge == "continue":
                anchor = active_vertical_cells.get(raw_cell.column_index)
                if (
                    anchor is None
                    or anchor.column_index != raw_cell.column_index
                    or anchor.column_span != raw_cell.column_span
                    or any(
                        active_vertical_cells.get(column) is not anchor
                        for column in occupied_columns
                    )
                ):
                    warnings.append("DOCX_MERGED_CELL_BEST_EFFORT")
                    cell = _new_grid_cell(raw_cell, table_index, row_index)
                    logical_cells.append(cell)
                elif raw_cell.text and raw_cell.text != anchor.text:
                    warnings.append("DOCX_MERGED_CELL_BEST_EFFORT")
                    cell = _new_grid_cell(raw_cell, table_index, row_index)
                    logical_cells.append(cell)
                else:
                    anchor.row_span = row_index - anchor.row_index + 1
                    cell = anchor
            else:
                cell = _new_grid_cell(raw_cell, table_index, row_index)
                logical_cells.append(cell)

            row_cells.append(cell)
            if raw_cell.vertical_merge in {"restart", "continue"}:
                for column in occupied_columns:
                    next_active[column] = cell

        physical_rows.append(row_cells)
        active_vertical_cells = next_active

    return _TableGrid(
        column_count=measured_columns,
        cells=logical_cells,
        rows=physical_rows,
        header_rows=header_rows,
        warnings=list(dict.fromkeys(warnings)),
    )


def _new_grid_cell(
    raw_cell: _RawCell,
    table_index: int,
    row_index: int,
) -> _GridCell:
    return _GridCell(
        cell_id=cell_id(table_index, row_index, raw_cell.column_index),
        row_index=row_index,
        column_index=raw_cell.column_index,
        row_span=1,
        column_span=raw_cell.column_span,
        text=raw_cell.text,
        is_header=raw_cell.is_header,
    )


def _validate_complete_physical_row(
    raw_cells: list[_RawCell],
    *,
    row_index: int,
    column_count: int,
) -> list[_RawCell]:
    occupied: set[int] = set()
    for raw_cell in raw_cells:
        for column in range(
            raw_cell.column_index,
            raw_cell.column_index + raw_cell.column_span,
        ):
            if column > column_count or column in occupied:
                raise DocumentParseError(
                    "DOCX table row contains overlapping or out-of-bounds cells: "
                    f"row {row_index}, column {column}"
                )
            occupied.add(column)
    missing = sorted(set(range(1, column_count + 1)) - occupied)
    if missing:
        raise _TableEvidenceUnavailable(
            "DOCX table physical row has XML grid slots without w:tc elements: "
            f"row {row_index}, columns {missing}"
        )
    return sorted(raw_cells, key=lambda item: item.column_index)


def _render_physical_row(
    cells: list[_GridCell],
) -> tuple[str, dict[str, tuple[int, int]]]:
    parts: list[str] = []
    ranges: dict[str, tuple[int, int]] = {}
    cursor = 0
    for index, cell in enumerate(cells):
        start = cursor
        parts.append(cell.text)
        cursor += len(cell.text)
        ranges[cell.cell_id] = (start, cursor)
        if index < len(cells) - 1:
            parts.append(TABLE_CELL_SEPARATOR)
            cursor += len(TABLE_CELL_SEPARATOR)
    return "".join(parts), ranges


def _render_table_row_group(
    grid: _TableGrid,
    *,
    row_start: int,
    row_end: int,
    repeated_header_rows: list[int],
) -> tuple[str, list[_RenderedCellOccurrence]]:
    rendered_rows = [
        *((row_index, True) for row_index in repeated_header_rows),
        *((row_index, False) for row_index in range(row_start, row_end + 1)),
    ]
    parts: list[str] = []
    occurrences: list[_RenderedCellOccurrence] = []
    cursor = 0
    for rendered_index, (row_index, repeated) in enumerate(rendered_rows):
        if rendered_index:
            parts.append(TABLE_ROW_SEPARATOR)
            cursor += len(TABLE_ROW_SEPARATOR)
        row_text, ranges = _render_physical_row(grid.rows[row_index - 1])
        parts.append(row_text)
        for cell in grid.rows[row_index - 1]:
            start, end = ranges[cell.cell_id]
            occurrences.append(
                _RenderedCellOccurrence(
                    cell=cell,
                    physical_row_index=row_index,
                    start=cursor + start,
                    end=cursor + end,
                    role=(
                        "repeated_header"
                        if repeated
                        else (
                            "original"
                            if row_index == cell.row_index
                            else "row_span_projection"
                        )
                    ),
                )
            )
        cursor += len(row_text)
    return "".join(parts), occurrences


def _table_row_groups(row_count: int) -> list[tuple[int, int]]:
    return [
        (start, min(start + MAX_ROWS_PER_TABLE_BLOCK - 1, row_count))
        for start in range(1, row_count + 1, MAX_ROWS_PER_TABLE_BLOCK)
    ]


def _repeated_header_row_indices(grid: _TableGrid) -> list[int]:
    result: list[int] = []
    for row_index, is_header in enumerate(grid.header_rows, start=1):
        if not is_header or len(result) >= REPEAT_HEADER_ROWS:
            break
        result.append(row_index)
    return result


def _render_table_text_only(table: object) -> str:
    from docx.oxml.ns import qn
    from docx.table import _Cell

    rows: list[str] = []
    for row_element in getattr(table, "_tbl").findall(qn("w:tr")):
        values = [
            _canonicalize_table_cell_text(_Cell(cell_element, table).text)
            for cell_element in row_element.findall(qn("w:tc"))
        ]
        rows.append(TABLE_CELL_SEPARATOR.join(values))
    return TABLE_ROW_SEPARATOR.join(rows)


def _canonicalize_table_cell_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return (
        normalized.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\n", "\\n")
    )


def _text_block_evidence(block: DocumentBlock) -> BlockEvidenceRecord:
    return BlockEvidenceRecord(
        block_id=block.block_id,
        text_length=len(block.text),
        text_sha256=sha256_text(block.text),
        page=block.page,
        expected_capabilities=["text_range"],
        available_capabilities=["text_range"],
    )


def _parser_identity() -> ParserIdentity:
    try:
        python_docx_version = distribution_version("python-docx")
    except DistributionNotFoundError:  # pragma: no cover - import already succeeded
        python_docx_version = "unknown"
    return ParserIdentity(
        parser_name=DocxParserV2.parser_name,
        parser_version="2",
        source_format="docx",
        parser_config={
            "table_block_mode": "complete_row_groups",
            "max_rows_per_table_block": MAX_ROWS_PER_TABLE_BLOCK,
            "repeat_header_rows": REPEAT_HEADER_ROWS,
            "canonical_table_serializer": "escaped_cells_v1",
            "merged_cell_projection": "repeat_logical_text_per_physical_row",
            "header_detection": "explicit_w_tblHeader",
            "repeated_header_projection": "logical_cell_identity",
            "irregular_topology": "text_range_only",
            "merged_cell_errors": "best_effort_or_text_range_only",
        },
        runtime_dependencies={"python-docx": python_docx_version},
    )


def _integer_property(
    parent: object | None,
    child_name: str,
    qn,
    default: int,
) -> int:
    if parent is None:
        return default
    child = parent.find(qn(child_name))
    if child is None:
        return default
    raw = child.get(qn("w:val"))
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise DocumentParseError(
            f"DOCX table property {child_name} must be an integer"
        ) from exc


def _vertical_merge(parent: object | None, qn) -> str | None:
    if parent is None:
        return None
    child = parent.find(qn("w:vMerge"))
    if child is None:
        return None
    value = child.get(qn("w:val"))
    if value is None or value.lower() == "continue":
        return "continue"
    if value.lower() == "restart":
        return "restart"
    return "invalid"


def _horizontal_merge(parent: object | None, qn) -> str | None:
    if parent is None:
        return None
    child = parent.find(qn("w:hMerge"))
    if child is None:
        return None
    value = child.get(qn("w:val"))
    if value is None or value.lower() == "continue":
        return "continue"
    if value.lower() == "restart":
        return "restart"
    return "invalid"


def _collapse_horizontal_merges(
    raw_cells: list[_RawCell],
    warnings: list[str],
) -> list[_RawCell]:
    collapsed: list[_RawCell] = []
    active_index: int | None = None
    for raw_cell in raw_cells:
        if raw_cell.horizontal_merge == "continue":
            if active_index is None:
                warnings.append("DOCX_MERGED_CELL_BEST_EFFORT")
                collapsed.append(replace(raw_cell, horizontal_merge=None))
                continue
            anchor = collapsed[active_index]
            if anchor.vertical_merge != raw_cell.vertical_merge:
                warnings.append("DOCX_MERGED_CELL_BEST_EFFORT")
                collapsed.append(replace(raw_cell, horizontal_merge=None))
                active_index = None
                continue
            combined_text = " ".join(
                value for value in (anchor.text, raw_cell.text) if value
            )
            collapsed[active_index] = replace(
                anchor,
                column_span=anchor.column_span + raw_cell.column_span,
                text=combined_text,
                horizontal_merge=None,
            )
            continue

        collapsed.append(
            replace(raw_cell, horizontal_merge=None)
        )
        active_index = (
            len(collapsed) - 1
            if raw_cell.horizontal_merge == "restart"
            else None
        )
    return collapsed


def _on_off_enabled(parent: object | None, child_name: str, qn) -> bool:
    if parent is None:
        return False
    child = parent.find(qn(child_name))
    if child is None:
        return False
    value = child.get(qn("w:val"))
    return value is None or value.lower() not in {"0", "false", "off", "no"}


def _iter_block_items(document: object) -> Iterator[tuple[str, object]]:
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = document.element.body
    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            yield "paragraph", Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield "table", Table(child, document)


def _style_name(paragraph: object) -> str | None:
    style = getattr(paragraph, "style", None)
    name = getattr(style, "name", None)
    return str(name) if name else None


def _heading_level(style_name: str | None) -> int | None:
    if not style_name or not style_name.startswith("Heading"):
        return None
    parts = style_name.split()
    if len(parts) < 2:
        return 1
    try:
        return max(1, min(6, int(parts[1])))
    except ValueError:
        return 1


def _is_list_style(style_name: str | None) -> bool:
    if not style_name:
        return False
    return style_name in {
        "List Paragraph",
        "List Bullet",
        "List Number",
    } or style_name.startswith("List ")


def _normalize_text(text: str) -> str:
    return " ".join(text.split())
