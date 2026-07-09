from __future__ import annotations

from pathlib import Path

from spectrail.parsers.base import ParsedDocument, UnsupportedDocumentTypeError
from spectrail.parsers.docx_parser import DocxParser
from spectrail.parsers.markdown_parser import MarkdownDocumentParser
from spectrail.parsers.pdf_parser import TextPdfParser


SUPPORTED_DOCUMENT_SUFFIXES = {".md", ".markdown", ".docx", ".pdf"}


def parse_document(path: str | Path, document_id: str = "doc_001") -> ParsedDocument:
    source_path = Path(path)
    suffix = source_path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return MarkdownDocumentParser().parse(source_path, document_id=document_id)
    if suffix == ".docx":
        return DocxParser().parse(source_path, document_id=document_id)
    if suffix == ".pdf":
        return TextPdfParser().parse(source_path, document_id=document_id)

    supported = ", ".join(sorted(SUPPORTED_DOCUMENT_SUFFIXES))
    raise UnsupportedDocumentTypeError(
        f"unsupported document type: {suffix or '<none>'}; supported suffixes: {supported}"
    )
