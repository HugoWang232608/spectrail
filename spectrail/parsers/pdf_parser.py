from __future__ import annotations

import re
from pathlib import Path

from spectrail.core.ids import block_id
from spectrail.core.models import DocumentBlock
from spectrail.parsers.base import DocumentParseError, ParsedDocument
from spectrail.parsers.render import render_blocks_to_markdown


LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|[•‣]\s*|\d+[.)]\s+).+")


class TextPdfParser:
    parser_name = "text_pdf_parser_v1"
    source_format = "pdf"

    def parse(self, path: str | Path, document_id: str = "doc_001") -> ParsedDocument:
        source_path = Path(path)
        try:
            import fitz
        except ImportError as exc:
            raise DocumentParseError("PyMuPDF is required to parse PDF documents") from exc

        try:
            document = fitz.open(source_path)
        except Exception as exc:
            raise DocumentParseError(f"failed to open PDF document: {source_path.name}") from exc

        blocks: list[DocumentBlock] = []
        warnings: list[str] = []
        try:
            for page_index, page in enumerate(document, start=1):
                text = page.get_text("text")
                paragraphs = _split_paragraphs(text)
                if not paragraphs:
                    warnings.append(f"page {page_index} has no extractable text")
                    continue

                for source_index, paragraph in enumerate(paragraphs, start=1):
                    order = len(blocks) + 1
                    blocks.append(
                        DocumentBlock(
                            block_id=block_id(order),
                            document_id=document_id,
                            type="list" if LIST_RE.match(paragraph) else "paragraph",
                            text=paragraph,
                            page=page_index,
                            section_path=[],
                            order=order,
                            metadata={
                                "source_format": self.source_format,
                                "parser": self.parser_name,
                                "page": page_index,
                                "source_index": source_index,
                            },
                        )
                    )
        finally:
            document.close()

        if not blocks:
            raise DocumentParseError("no extractable text; scanned PDF is not supported")

        return ParsedDocument(
            document_id=document_id,
            document_name=source_path.name,
            source_format=self.source_format,
            parser_name=self.parser_name,
            text=render_blocks_to_markdown(blocks),
            blocks=blocks,
            warnings=warnings,
            metadata={"source_path": source_path.as_posix(), "page_count": page_index if "page_index" in locals() else 0},
        )


def _split_paragraphs(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = []
    for chunk in re.split(r"\n\s*\n+", normalized):
        paragraph = " ".join(line.strip() for line in chunk.splitlines() if line.strip())
        if paragraph:
            paragraphs.append(paragraph)
    return paragraphs
