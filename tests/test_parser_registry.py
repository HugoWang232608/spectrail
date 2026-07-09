from __future__ import annotations

from pathlib import Path

import pytest

from spectrail.parsers import UnsupportedDocumentTypeError
from spectrail.parsers.registry import SUPPORTED_DOCUMENT_SUFFIXES, parse_document


def test_parse_document_returns_markdown_parsed_document():
    parsed = parse_document(Path("docs/sample_srs.md"))

    assert parsed.document_name == "sample_srs.md"
    assert parsed.source_format == "markdown"
    assert parsed.parser_name == "markdown_parser_v1"
    assert parsed.text == Path("docs/sample_srs.md").read_text(encoding="utf-8")
    assert parsed.blocks
    assert parsed.blocks[0].metadata["source_format"] == "markdown"
    assert parsed.blocks[0].metadata["parser"] == "markdown_parser_v1"


def test_parse_document_rejects_future_suffixes_until_parser_is_registered(tmp_path: Path):
    document = tmp_path / "sample.pdf"
    document.write_bytes(b"%PDF-1.4\n")

    assert SUPPORTED_DOCUMENT_SUFFIXES == {".md", ".markdown"}
    with pytest.raises(UnsupportedDocumentTypeError):
        parse_document(document)
