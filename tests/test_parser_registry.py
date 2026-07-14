from __future__ import annotations

from pathlib import Path

import pytest

from spectrail.parsers import UnsupportedDocumentTypeError
from spectrail.parsers.registry import (
    PARSER_SOURCE_FORMATS,
    SUPPORTED_DOCUMENT_SUFFIXES,
    parse_document,
)


def test_parse_document_returns_markdown_parsed_document():
    parsed = parse_document(Path("docs/sample_srs.md"))

    assert parsed.document_name == "sample_srs.md"
    assert parsed.source_format == "markdown"
    assert parsed.parser_name == "markdown_parser_v1"
    assert parsed.text == Path("docs/sample_srs.md").read_text(encoding="utf-8")
    assert parsed.blocks
    assert parsed.blocks[0].metadata["source_format"] == "markdown"
    assert parsed.blocks[0].metadata["parser"] == "markdown_parser_v1"


def test_parse_document_rejects_unknown_suffix(tmp_path: Path):
    document = tmp_path / "sample.rtf"
    document.write_text("{\\rtf1}", encoding="utf-8")

    assert SUPPORTED_DOCUMENT_SUFFIXES == {".md", ".markdown", ".docx", ".pdf"}
    with pytest.raises(UnsupportedDocumentTypeError):
        parse_document(document)


def test_parser_name_format_registration_is_owned_by_parser_registry():
    assert PARSER_SOURCE_FORMATS == {
        "markdown_parser_v1": "markdown",
        "docx_parser_v2": "docx",
        "text_pdf_parser_v1": "pdf",
    }
