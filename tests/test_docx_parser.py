from __future__ import annotations

from pathlib import Path

from docx import Document

from spectrail.parsers.docx_parser import DocxParser
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
    assert parsed.parser_name == "docx_parser_v1"
    assert [block.type for block in parsed.blocks] == ["heading", "paragraph", "table", "list"]
    assert [block.order for block in parsed.blocks] == [1, 2, 3, 4]
    assert [block.block_id for block in parsed.blocks] == ["blk_0001", "blk_0002", "blk_0003", "blk_0004"]
    assert parsed.blocks[0].section_path == ["System Requirements"]
    assert parsed.blocks[1].section_path == ["System Requirements"]
    assert parsed.blocks[2].section_path == ["System Requirements"]
    assert parsed.blocks[3].section_path == ["System Requirements"]
    assert parsed.blocks[0].metadata["source_format"] == "docx"
    assert parsed.blocks[0].metadata["parser"] == "docx_parser_v1"
    assert parsed.blocks[0].metadata["style"] == "Heading 1"
    assert parsed.blocks[2].text == (
        "| Field | Description |\n"
        "| --- | --- |\n"
        "| timeout | The system shall respond within 3 seconds. |"
    )
    assert "##" not in parsed.text
    assert "# System Requirements" in parsed.text


def test_parse_document_routes_docx(tmp_path: Path):
    path = tmp_path / "sample.docx"
    document = Document()
    document.add_paragraph("The system shall import DOCX files.")
    document.save(path)

    parsed = parse_document(path)

    assert parsed.source_format == "docx"
    assert parsed.blocks[0].text == "The system shall import DOCX files."
