from __future__ import annotations

from pathlib import Path

from spectrail.parsers.base import ParsedDocument, UnsupportedDocumentTypeError
from spectrail.parsers.markdown_parser import MarkdownDocumentParser


SUPPORTED_DOCUMENT_SUFFIXES = {".md", ".markdown"}


def parse_document(path: str | Path, document_id: str = "doc_001") -> ParsedDocument:
    source_path = Path(path)
    suffix = source_path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return MarkdownDocumentParser().parse(source_path, document_id=document_id)

    supported = ", ".join(sorted(SUPPORTED_DOCUMENT_SUFFIXES))
    raise UnsupportedDocumentTypeError(
        f"unsupported document type: {suffix or '<none>'}; supported suffixes: {supported}"
    )
