from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from spectrail.core.models import DocumentBlock


@dataclass(frozen=True)
class ParsedDocument:
    document_id: str
    document_name: str
    source_format: str
    parser_name: str
    text: str
    blocks: list[DocumentBlock]
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class DocumentParseError(ValueError):
    """Raised when a document cannot be parsed into usable text blocks."""


class UnsupportedDocumentTypeError(DocumentParseError):
    """Raised when the input suffix is not supported."""


class DocumentParser(Protocol):
    parser_name: str
    source_format: str

    def parse(self, path: str | Path, document_id: str = "doc_001") -> ParsedDocument:
        ...
