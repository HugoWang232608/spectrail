from __future__ import annotations

import re
from pathlib import Path

from spectrail.core.ids import block_id
from spectrail.core.models import DocumentBlock
from spectrail.parsers.base import DocumentParseError, ParsedDocument


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+).+")


class MarkdownParser:
    def parse_file(self, path: str | Path, document_id: str = "doc_001") -> list[DocumentBlock]:
        source_path = Path(path)
        text = source_path.read_text(encoding="utf-8")
        return self.parse_text(text, document_id=document_id)

    def parse_text(self, text: str, document_id: str = "doc_001") -> list[DocumentBlock]:
        lines = text.splitlines()
        blocks: list[DocumentBlock] = []
        section_stack: list[str] = []
        i = 0

        def append(kind: str, block_text: str, section_path: list[str], metadata: dict | None = None) -> None:
            order = len(blocks) + 1
            blocks.append(
                DocumentBlock(
                    block_id=block_id(order),
                    document_id=document_id,
                    type=kind,  # type: ignore[arg-type]
                    text=block_text.strip("\n"),
                    section_path=list(section_path),
                    order=order,
                    metadata=metadata or {},
                )
            )

        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue

            heading = HEADING_RE.match(line)
            if heading:
                level = len(heading.group(1))
                title = heading.group(2).strip()
                section_stack = section_stack[: level - 1]
                section_stack.append(title)
                append("heading", title, section_stack, {"level": level})
                i += 1
                continue

            if line.startswith("```"):
                collected = [line]
                i += 1
                while i < len(lines):
                    collected.append(lines[i])
                    if lines[i].startswith("```"):
                        i += 1
                        break
                    i += 1
                append("code", "\n".join(collected), section_stack)
                continue

            if line.lstrip().startswith(">"):
                collected = []
                while i < len(lines) and lines[i].lstrip().startswith(">"):
                    collected.append(lines[i])
                    i += 1
                append("blockquote", "\n".join(collected), section_stack)
                continue

            if self._is_table_line(line):
                collected = []
                while i < len(lines) and self._is_table_line(lines[i]):
                    collected.append(lines[i])
                    i += 1
                append("table", "\n".join(collected), section_stack)
                continue

            if LIST_RE.match(line):
                collected = []
                while i < len(lines) and (LIST_RE.match(lines[i]) or not lines[i].strip()):
                    if lines[i].strip():
                        collected.append(lines[i])
                    i += 1
                    if i < len(lines) and not lines[i].strip():
                        break
                append("list", "\n".join(collected), section_stack)
                continue

            collected = [line]
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if not next_line.strip():
                    break
                if HEADING_RE.match(next_line) or next_line.startswith("```"):
                    break
                if next_line.lstrip().startswith(">") or self._is_table_line(next_line) or LIST_RE.match(next_line):
                    break
                collected.append(next_line)
                i += 1
            append("paragraph", "\n".join(collected), section_stack)

        return blocks

    @staticmethod
    def _is_table_line(line: str) -> bool:
        stripped = line.strip()
        return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


class MarkdownDocumentParser:
    parser_name = "markdown_parser_v1"
    source_format = "markdown"

    def parse(self, path: str | Path, document_id: str = "doc_001") -> ParsedDocument:
        source_path = Path(path)
        try:
            text = source_path.read_text(encoding="utf-8")
            blocks = MarkdownParser().parse_text(text, document_id=document_id)
        except FileNotFoundError:
            raise
        except UnicodeDecodeError as exc:
            raise DocumentParseError(f"markdown document is not valid UTF-8: {source_path.name}") from exc
        except OSError as exc:
            raise DocumentParseError(f"failed to read markdown document: {source_path.name}") from exc

        for block in blocks:
            block.metadata.setdefault("source_format", self.source_format)
            block.metadata.setdefault("parser", self.parser_name)

        return ParsedDocument(
            document_id=document_id,
            document_name=source_path.name,
            source_format=self.source_format,
            parser_name=self.parser_name,
            text=text,
            blocks=blocks,
            warnings=[],
            metadata={"source_path": source_path.as_posix()},
        )
