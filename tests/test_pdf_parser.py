from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from spectrail.parsers.base import DocumentParseError
from spectrail.parsers.pdf_parser import TextPdfParser
from spectrail.parsers.registry import parse_document


def test_text_pdf_parser_extracts_page_aware_blocks(tmp_path: Path):
    path = tmp_path / "sample.pdf"
    document = fitz.open()
    page1 = document.new_page()
    page1.insert_text((72, 72), "System Requirements\n\nThe system shall export requirements as XLSX.")
    page2 = document.new_page()
    page2.insert_text((72, 72), "The system shall show source quotes.")
    document.save(path)
    document.close()

    parsed = TextPdfParser().parse(path)

    assert parsed.document_name == "sample.pdf"
    assert parsed.source_format == "pdf"
    assert parsed.parser_name == "text_pdf_parser_v1"
    assert parsed.blocks
    assert {block.page for block in parsed.blocks} == {1, 2}
    assert parsed.blocks[0].metadata["source_format"] == "pdf"
    assert parsed.blocks[0].metadata["parser"] == "text_pdf_parser_v1"
    assert parsed.blocks[0].metadata["page"] == parsed.blocks[0].page
    assert "The system shall show source quotes." in parsed.text


def test_text_pdf_parser_records_warning_for_empty_page_and_continues(tmp_path: Path):
    path = tmp_path / "partly-empty.pdf"
    document = fitz.open()
    document.new_page()
    page2 = document.new_page()
    page2.insert_text((72, 72), "The system shall parse text PDFs.")
    document.save(path)
    document.close()

    parsed = parse_document(path)

    assert parsed.source_format == "pdf"
    assert parsed.warnings == ["page 1 has no extractable text"]
    assert [block.page for block in parsed.blocks] == [2]


def test_text_pdf_parser_rejects_pdf_without_extractable_text(tmp_path: Path):
    path = tmp_path / "empty.pdf"
    document = fitz.open()
    document.new_page()
    document.save(path)
    document.close()

    with pytest.raises(DocumentParseError, match="no extractable text"):
        TextPdfParser().parse(path)
