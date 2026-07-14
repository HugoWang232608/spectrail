from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement

from spectrail.evidence.index_builder import ensure_evidence_index
from spectrail.parsers.docx_parser import DocxParser, DocxParserV2
from spectrail.parsers.registry import parse_document


def test_docx_parser_extracts_blocks_in_document_order(tmp_path: Path):
    path = tmp_path / "sample.docx"
    document = Document()
    document.add_heading("System Requirements", level=1)
    document.add_paragraph("The system shall export requirements as XLSX.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Field"
    table.cell(0, 1).text = "Description"
    table.cell(1, 0).text = "timeout"
    table.cell(1, 1).text = "The system shall respond within 3 seconds."
    document.add_paragraph("The system shall allow review actions.", style="List Bullet")
    document.save(path)

    parsed = DocxParser().parse(path)

    assert parsed.document_name == "sample.docx"
    assert parsed.source_format == "docx"
    assert parsed.parser_name == "docx_parser_v2"
    assert [block.type for block in parsed.blocks] == [
        "heading",
        "paragraph",
        "table",
        "table",
        "list",
    ]
    assert [block.order for block in parsed.blocks] == [1, 2, 3, 4, 5]
    assert [block.block_id for block in parsed.blocks] == [
        "blk_0001",
        "blk_0002",
        "blk_0003",
        "blk_0004",
        "blk_0005",
    ]
    assert parsed.blocks[0].section_path == ["System Requirements"]
    assert parsed.blocks[1].section_path == ["System Requirements"]
    assert parsed.blocks[2].section_path == ["System Requirements"]
    assert parsed.blocks[3].section_path == ["System Requirements"]
    assert parsed.blocks[4].section_path == ["System Requirements"]
    assert parsed.blocks[0].metadata["source_format"] == "docx"
    assert parsed.blocks[0].metadata["parser"] == "docx_parser_v2"
    assert parsed.blocks[0].metadata["style"] == "Heading 1"
    assert parsed.blocks[2].text == "| Field | Description |"
    assert parsed.blocks[3].text == (
        "| timeout | The system shall respond within 3 seconds. |"
    )
    assert "##" not in parsed.text
    assert "# System Requirements" in parsed.text
    assert parsed.parser_identity is not None
    assert parsed.parser_identity.parser_name == "docx_parser_v2"
    assert parsed.parser_identity.parser_version == "2"
    assert parsed.evidence_index is not None
    assert parsed.evidence_index.tables[0].block_ids == ["blk_0003", "blk_0004"]
    assert parsed.evidence_index.tables[0].row_count == 2
    assert parsed.evidence_index.tables[0].column_count == 2
    assert parsed.evidence_index.blocks[2].table_row_start == 1
    assert parsed.evidence_index.blocks[3].table_row_start == 2
    assert ensure_evidence_index(path, parsed) == parsed.evidence_index


def test_parse_document_routes_docx(tmp_path: Path):
    path = tmp_path / "sample.docx"
    document = Document()
    document.add_paragraph("The system shall import DOCX files.")
    document.save(path)

    parsed = parse_document(path)

    assert parsed.source_format == "docx"
    assert parsed.parser_name == "docx_parser_v2"
    assert parsed.blocks[0].text == "The system shall import DOCX files."
    assert DocxParser is DocxParserV2


def test_docx_parser_builds_merged_cell_grid_and_occurrences(tmp_path: Path):
    path = tmp_path / "merged.docx"
    document = Document()
    table = document.add_table(rows=3, cols=3)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "Header"
    table.cell(0, 2).text = "Status"
    _mark_repeating_header(table.rows[0])
    table.cell(1, 0).merge(table.cell(2, 0)).text = "Merged"
    table.cell(1, 1).text = "A"
    table.cell(1, 2).text = "B"
    table.cell(2, 1).text = ""
    table.cell(2, 2).text = "C"
    document.save(path)

    parsed = DocxParser().parse(path)
    index = parsed.evidence_index
    assert index is not None
    assert [block.text for block in parsed.blocks] == [
        "| Header | Status |",
        "| Merged | A | B |",
        "| Merged |  | C |",
    ]
    assert index.tables[0].row_count == 3
    assert index.tables[0].column_count == 3
    assert index.tables[0].topology_status == "complete"
    assert "w:tblHeader" in index.tables[0].warnings[0]

    cells = {cell.cell_id: cell for cell in index.cells}
    header = cells["cell_00000001_r0001_c0001"]
    merged = cells["cell_00000001_r0002_c0001"]
    empty = cells["cell_00000001_r0003_c0002"]
    assert header.column_span == 2
    assert header.row_span == 1
    assert header.is_header is True
    assert cells["cell_00000001_r0001_c0003"].is_header is True
    assert merged.row_span == 2
    assert merged.column_span == 1
    assert empty.text == ""

    merged_occurrences = [
        item for item in index.cell_occurrences if item.cell_id == merged.cell_id
    ]
    assert [item.physical_row_index for item in merged_occurrences] == [2, 3]
    assert [item.occurrence_role for item in merged_occurrences] == [
        "original",
        "row_span_projection",
    ]
    for occurrence in index.cell_occurrences:
        block = next(
            item for item in parsed.blocks if item.block_id == occurrence.block_id
        )
        cell = cells[occurrence.cell_id]
        assert block.text[
            occurrence.canonical_start : occurrence.canonical_end
        ] == cell.text
    reparsed = DocxParser().parse(path)
    assert reparsed.evidence_index == index


def test_docx_parser_supports_legacy_horizontal_merge_markup(tmp_path: Path):
    path = tmp_path / "legacy-hmerge.docx"
    document = Document()
    table = document.add_table(rows=1, cols=3)
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "B"
    table.cell(0, 2).text = "C"
    _set_horizontal_merge(table.cell(0, 0), "restart")
    _set_horizontal_merge(table.cell(0, 1), "continue")
    document.save(path)

    parsed = DocxParser().parse(path)
    index = parsed.evidence_index
    assert index is not None
    assert parsed.blocks[0].text == "| A B | C |"
    assert [
        (cell.column_index, cell.column_span, cell.text)
        for cell in index.cells
    ] == [(1, 2, "A B"), (3, 1, "C")]


def _mark_repeating_header(row) -> None:
    properties = row._tr.get_or_add_trPr()
    properties.append(OxmlElement("w:tblHeader"))


def _set_horizontal_merge(cell, value: str) -> None:
    merge = OxmlElement("w:hMerge")
    merge.set(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val",
        value,
    )
    cell._tc.get_or_add_tcPr().append(merge)
