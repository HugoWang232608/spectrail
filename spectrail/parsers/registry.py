from __future__ import annotations

from pathlib import Path

from spectrail.parsers.base import (
    DocumentParseError,
    ParsedDocument,
    UnsupportedDocumentTypeError,
)
from spectrail.parsers.docx_parser import DocxParserV2
from spectrail.parsers.markdown_parser import MarkdownDocumentParser
from spectrail.parsers.pdf_parser import PdfParserV2


PARSER_REGISTRY = {
    ".md": MarkdownDocumentParser,
    ".markdown": MarkdownDocumentParser,
    ".docx": DocxParserV2,
    ".pdf": PdfParserV2,
}
SUPPORTED_DOCUMENT_SUFFIXES = set(PARSER_REGISTRY)
PARSER_SOURCE_FORMATS = {
    parser_type.parser_name: parser_type.source_format
    for parser_type in set(PARSER_REGISTRY.values())
}


def parse_document(path: str | Path, document_id: str = "doc_001") -> ParsedDocument:
    source_path = Path(path)
    suffix = source_path.suffix.lower()
    parser_type = PARSER_REGISTRY.get(suffix)
    if parser_type is not None:
        parser = parser_type()
        parsed = parser.parse(source_path, document_id=document_id)
        if (
            parsed.parser_name != parser.parser_name
            or parsed.source_format != parser.source_format
        ):
            raise DocumentParseError(
                "registered parser returned an inconsistent identity"
            )
        return parsed

    supported = ", ".join(sorted(SUPPORTED_DOCUMENT_SUFFIXES))
    raise UnsupportedDocumentTypeError(
        f"unsupported document type: {suffix or '<none>'}; supported suffixes: {supported}"
    )
