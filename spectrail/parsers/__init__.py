"""Document parsers."""

from spectrail.parsers.base import (
    DocumentParseError,
    DocumentParser,
    ParsedDocument,
    UnsupportedDocumentTypeError,
)
from spectrail.parsers.registry import SUPPORTED_DOCUMENT_SUFFIXES, parse_document

__all__ = [
    "DocumentParseError",
    "DocumentParser",
    "ParsedDocument",
    "SUPPORTED_DOCUMENT_SUFFIXES",
    "UnsupportedDocumentTypeError",
    "parse_document",
]
