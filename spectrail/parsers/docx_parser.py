from __future__ import annotations

from pathlib import Path
from typing import Iterator

from spectrail.core.ids import block_id
from spectrail.core.models import DocumentBlock
from spectrail.parsers.base import DocumentParseError, ParsedDocument
from spectrail.parsers.render import render_blocks_to_markdown


class DocxParser:
    parser_name = "docx_parser_v1"
    source_format = "docx"

    def parse(self, path: str | Path, document_id: str = "doc_001") -> ParsedDocument:
        source_path = Path(path)
        try:
            from docx import Document
            from docx.opc.exceptions import PackageNotFoundError
        except ImportError as exc:
            raise DocumentParseError("python-docx is required to parse DOCX documents") from exc

        try:
            document = Document(source_path)
        except PackageNotFoundError as exc:
            raise DocumentParseError(f"invalid DOCX package: {source_path.name}") from exc
        except Exception as exc:
            raise DocumentParseError(f"failed to parse DOCX document: {source_path.name}") from exc

        blocks: list[DocumentBlock] = []
        section_stack: list[str] = []
        source_index = 0

        for kind, item in _iter_block_items(document):
            source_index += 1
            if kind == "paragraph":
                block = self._paragraph_to_block(
                    paragraph=item,
                    document_id=document_id,
                    order=len(blocks) + 1,
                    source_index=source_index,
                    section_stack=section_stack,
                )
            else:
                block = self._table_to_block(
                    table=item,
                    document_id=document_id,
                    order=len(blocks) + 1,
                    source_index=source_index,
                    section_stack=section_stack,
                )

            if block is not None:
                blocks.append(block)

        if not blocks:
            raise DocumentParseError(f"docx document has no extractable text: {source_path.name}")

        return ParsedDocument(
            document_id=document_id,
            document_name=source_path.name,
            source_format=self.source_format,
            parser_name=self.parser_name,
            text=render_blocks_to_markdown(blocks),
            blocks=blocks,
            warnings=[],
            metadata={"source_path": source_path.as_posix()},
        )

    def _paragraph_to_block(
        self,
        paragraph: object,
        document_id: str,
        order: int,
        source_index: int,
        section_stack: list[str],
    ) -> DocumentBlock | None:
        text = _normalize_text(getattr(paragraph, "text", ""))
        if not text:
            return None

        style_name = _style_name(paragraph)
        metadata = self._metadata(source_index, style=style_name)
        heading_level = _heading_level(style_name)
        if heading_level is not None:
            section_stack[:] = section_stack[: heading_level - 1]
            section_stack.append(text)
            metadata["level"] = heading_level
            block_type = "heading"
            block_section_path = list(section_stack)
        elif _is_list_style(style_name):
            block_type = "list"
            block_section_path = list(section_stack)
        else:
            block_type = "paragraph"
            block_section_path = list(section_stack)

        return DocumentBlock(
            block_id=block_id(order),
            document_id=document_id,
            type=block_type,  # type: ignore[arg-type]
            text=text,
            section_path=block_section_path,
            order=order,
            metadata=metadata,
        )

    def _table_to_block(
        self,
        table: object,
        document_id: str,
        order: int,
        source_index: int,
        section_stack: list[str],
    ) -> DocumentBlock | None:
        text = _table_to_markdown(table)
        if not text:
            return None

        return DocumentBlock(
            block_id=block_id(order),
            document_id=document_id,
            type="table",
            text=text,
            section_path=list(section_stack),
            order=order,
            metadata=self._metadata(source_index),
        )

    def _metadata(self, source_index: int, style: str | None = None) -> dict:
        metadata = {
            "source_format": self.source_format,
            "parser": self.parser_name,
            "source_index": source_index,
        }
        if style:
            metadata["style"] = style
        return metadata


def _iter_block_items(document: object) -> Iterator[tuple[str, object]]:
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = document.element.body
    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            yield "paragraph", Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield "table", Table(child, document)


def _style_name(paragraph: object) -> str | None:
    style = getattr(paragraph, "style", None)
    name = getattr(style, "name", None)
    return str(name) if name else None


def _heading_level(style_name: str | None) -> int | None:
    if not style_name or not style_name.startswith("Heading"):
        return None
    parts = style_name.split()
    if len(parts) < 2:
        return 1
    try:
        return max(1, min(6, int(parts[1])))
    except ValueError:
        return 1


def _is_list_style(style_name: str | None) -> bool:
    if not style_name:
        return False
    return style_name in {"List Paragraph", "List Bullet", "List Number"} or style_name.startswith("List ")


def _table_to_markdown(table: object) -> str:
    rows = []
    for row in getattr(table, "rows", []):
        cells = [_normalize_text(cell.text) for cell in row.cells]
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""

    width = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (width - len(row)) for row in rows]
    header = normalized_rows[0]
    separator = ["---"] * width
    body = normalized_rows[1:]
    markdown_rows = [header, separator, *body]
    return "\n".join("| " + " | ".join(row) + " |" for row in markdown_rows)


def _normalize_text(text: str) -> str:
    return " ".join(text.split())
