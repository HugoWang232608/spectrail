from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError as DistributionNotFoundError
from importlib.metadata import version as distribution_version
import math
import re
from pathlib import Path

from spectrail.core.ids import block_id
from spectrail.core.models import DocumentBlock
from spectrail.evidence.fingerprint import (
    finalize_evidence_fingerprint,
    sha256_file,
    sha256_text,
)
from spectrail.evidence.ids import fragment_id, page_id
from spectrail.evidence.models import (
    BlockEvidenceRecord,
    BoundingBox,
    EvidenceIndex,
    PageRecord,
    ParserIdentity,
    TextFragmentRecord,
)
from spectrail.parsers.base import DocumentParseError, ParsedDocument
from spectrail.parsers.render import render_blocks_to_markdown


LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|[•‣]\s*|\d+[.)]\s+).+")
EDGE_REGION_RATIO = 0.08
WIDE_BLOCK_RATIO = 0.70
COLUMN_OVERLAP_RATIO = 0.20


class PdfParserV2:
    parser_name = "pdf_parser_v2"
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
            raise DocumentParseError(
                f"failed to open PDF document: {source_path.name}"
            ) from exc

        source_hash = sha256_file(source_path)
        parser_identity = _parser_identity()
        page_layouts: list[_PageLayout] = []
        warnings: list[str] = []
        try:
            for page_index, page in enumerate(document, start=1):
                layout = _extract_page_layout(page, page_index)
                page_layouts.append(layout)
                if not layout.blocks:
                    warnings.append(f"page {page_index} has no extractable text")
        finally:
            document.close()

        _suppress_repeated_page_edges(page_layouts)

        blocks: list[DocumentBlock] = []
        evidence_blocks: list[BlockEvidenceRecord] = []
        fragments: list[TextFragmentRecord] = []
        pages: list[PageRecord] = []
        suppressed_edge_blocks = 0

        for layout in page_layouts:
            page_block_ids: list[str] = []
            candidates = [item for item in layout.blocks if not item.suppressed]
            suppressed_edge_blocks += len(layout.blocks) - len(candidates)
            ordered, column_count = _order_page_blocks(candidates, layout.width)
            if column_count > 1:
                layout.warnings.append(
                    f"PDF_MULTI_COLUMN_LAYOUT_DETECTED: columns={column_count}"
                )

            for source_index, candidate in enumerate(ordered, start=1):
                order = len(blocks) + 1
                block_identifier = block_id(order)
                block = DocumentBlock(
                    block_id=block_identifier,
                    document_id=document_id,
                    type="list" if LIST_RE.match(candidate.text) else "paragraph",
                    text=candidate.text,
                    page=layout.page,
                    section_path=[],
                    order=order,
                    metadata={
                        "source_format": self.source_format,
                        "parser": self.parser_name,
                        "page": layout.page,
                        "source_index": source_index,
                        "source_block_number": candidate.source_block_number,
                        "layout_column_index": candidate.column_index,
                        "layout_column_count": column_count,
                    },
                )
                blocks.append(block)
                page_block_ids.append(block_identifier)

                fragment_ids: list[str] = []
                for index, projected in enumerate(candidate.fragments, start=1):
                    identifier = fragment_id(block_identifier, index)
                    fragment_ids.append(identifier)
                    fragments.append(
                        TextFragmentRecord(
                            fragment_id=identifier,
                            block_id=block_identifier,
                            start=projected.start,
                            end=projected.end,
                            text=projected.text,
                            page=layout.page,
                            bbox=projected.bbox,
                            line_index=projected.line_index,
                            span_index=projected.span_index,
                            separator_before=projected.separator_before,
                        )
                    )

                evidence_blocks.append(
                    BlockEvidenceRecord(
                        block_id=block_identifier,
                        text_length=len(candidate.text),
                        text_sha256=sha256_text(candidate.text),
                        page=layout.page,
                        bbox=candidate.bbox,
                        fragment_ids=fragment_ids,
                        expected_capabilities=["text_range", "page_region"],
                        available_capabilities=["text_range", "page_region"],
                    )
                )

            pages.append(
                PageRecord(
                    page_id=page_id(layout.page),
                    page=layout.page,
                    width=layout.width,
                    height=layout.height,
                    source_rotation=layout.rotation,  # type: ignore[arg-type]
                    block_ids=page_block_ids,
                    table_ids=[],
                    warnings=list(dict.fromkeys(layout.warnings)),
                )
            )

        if not blocks:
            raise DocumentParseError("no extractable text; scanned PDF is not supported")

        evidence_index = finalize_evidence_fingerprint(
            EvidenceIndex(
                document_id=document_id,
                document_name=source_path.name,
                source_format=self.source_format,
                source_sha256=source_hash,
                parser_identity=parser_identity,
                evidence_fingerprint="0" * 64,
                pages=pages,
                blocks=evidence_blocks,
                fragments=fragments,
                warnings=warnings,
            )
        )
        return ParsedDocument(
            document_id=document_id,
            document_name=source_path.name,
            source_format=self.source_format,
            parser_name=self.parser_name,
            text=render_blocks_to_markdown(blocks),
            blocks=blocks,
            warnings=warnings,
            metadata={
                "source_path": source_path.as_posix(),
                "page_count": len(page_layouts),
                "suppressed_repeated_edge_blocks": suppressed_edge_blocks,
            },
            source_sha256=source_hash,
            parser_identity=parser_identity,
            evidence_index=evidence_index,
        )


# Backward-compatible import name; registry selection is explicitly V2.
TextPdfParser = PdfParserV2


@dataclass(frozen=True)
class _ProjectedFragment:
    start: int
    end: int
    text: str
    bbox: BoundingBox
    line_index: int
    span_index: int
    separator_before: str


@dataclass
class _PageTextBlock:
    text: str
    bbox: BoundingBox
    fragments: list[_ProjectedFragment]
    source_block_number: int
    column_index: int = 1
    suppressed: bool = False
    edge_role: str | None = None


@dataclass
class _PageLayout:
    page: int
    width: float
    height: float
    rotation: int
    blocks: list[_PageTextBlock]
    warnings: list[str] = field(default_factory=list)


def _extract_page_layout(page: object, page_index: int) -> _PageLayout:
    page_rect = getattr(page, "rect")
    width = float(page_rect.width)
    height = float(page_rect.height)
    rotation = int(getattr(page, "rotation", 0)) % 360
    raw = page.get_text("dict", sort=False)
    blocks: list[_PageTextBlock] = []
    for source_block_number, raw_block in enumerate(raw.get("blocks", []), start=1):
        if raw_block.get("type", 0) != 0:
            continue
        projected = _project_text_block(
            page,
            raw_block,
            page_index=page_index,
            page_width=width,
            page_height=height,
            source_block_number=source_block_number,
        )
        if projected is not None:
            blocks.append(projected)
    return _PageLayout(
        page=page_index,
        width=width,
        height=height,
        rotation=rotation,
        blocks=blocks,
    )


def _project_text_block(
    page: object,
    raw_block: dict,
    *,
    page_index: int,
    page_width: float,
    page_height: float,
    source_block_number: int,
) -> _PageTextBlock | None:
    text_parts: list[str] = []
    fragments: list[_ProjectedFragment] = []
    cursor = 0
    rendered_line_count = 0
    for line_index, line in enumerate(raw_block.get("lines", [])):
        raw_spans = [
            (span_index, span)
            for span_index, span in enumerate(line.get("spans", []))
            if str(span.get("text", ""))
        ]
        if not raw_spans:
            continue
        line_has_text = any(str(span.get("text", "")).strip() for _, span in raw_spans)
        if not line_has_text:
            continue
        first_span_in_line = True
        for span_index, span in raw_spans:
            text = str(span.get("text", ""))
            separator = "\n" if first_span_in_line and rendered_line_count else ""
            if separator:
                text_parts.append(separator)
                cursor += len(separator)
            start = cursor
            text_parts.append(text)
            cursor += len(text)
            bbox = _rotated_bbox(
                page,
                span.get("bbox"),
                page_width=page_width,
                page_height=page_height,
            )
            if bbox is None:
                return None
            fragments.append(
                _ProjectedFragment(
                    start=start,
                    end=cursor,
                    text=text,
                    bbox=bbox,
                    line_index=line_index,
                    span_index=span_index,
                    separator_before=separator,
                )
            )
            first_span_in_line = False
        rendered_line_count += 1

    text = "".join(text_parts)
    if not text.strip() or not fragments:
        return None
    bbox = _bbox_union([fragment.bbox for fragment in fragments])
    return _PageTextBlock(
        text=text,
        bbox=bbox,
        fragments=fragments,
        source_block_number=source_block_number,
    )


def _rotated_bbox(
    page: object,
    raw_bbox: object,
    *,
    page_width: float,
    page_height: float,
) -> BoundingBox | None:
    try:
        import fitz

        rect = fitz.Rect(raw_bbox) * page.rotation_matrix
    except Exception:
        return None
    x0 = _clamp(float(rect.x0), 0.0, page_width)
    y0 = _clamp(float(rect.y0), 0.0, page_height)
    x1 = _clamp(float(rect.x1), 0.0, page_width)
    y1 = _clamp(float(rect.y1), 0.0, page_height)
    if x1 <= x0 or y1 <= y0:
        return None
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _suppress_repeated_page_edges(page_layouts: list[_PageLayout]) -> None:
    if len(page_layouts) < 2:
        return
    occurrences: dict[tuple[str, str], set[int]] = {}
    for layout in page_layouts:
        for block in layout.blocks:
            role = _edge_role(block.bbox, layout.height)
            if role is None:
                continue
            key = (role, _normalized_edge_text(block.text))
            if key[1]:
                occurrences.setdefault(key, set()).add(layout.page)

    repeated = {
        key
        for key, page_numbers in occurrences.items()
        if len(page_numbers) >= 2
        and len(page_numbers) >= math.ceil(len(page_layouts) * 0.5)
    }
    for layout in page_layouts:
        for block in layout.blocks:
            role = _edge_role(block.bbox, layout.height)
            if role is None or (role, _normalized_edge_text(block.text)) not in repeated:
                continue
            block.suppressed = True
            block.edge_role = role
            layout.warnings.append(
                "PDF_REPEATED_HEADER_SUPPRESSED"
                if role == "header"
                else "PDF_REPEATED_FOOTER_SUPPRESSED"
            )


def _edge_role(bbox: BoundingBox, page_height: float) -> str | None:
    if bbox.y0 <= page_height * EDGE_REGION_RATIO:
        return "header"
    if bbox.y1 >= page_height * (1.0 - EDGE_REGION_RATIO):
        return "footer"
    return None


def _normalized_edge_text(text: str) -> str:
    return " ".join(text.split()).casefold()


def _order_page_blocks(
    blocks: list[_PageTextBlock],
    page_width: float,
) -> tuple[list[_PageTextBlock], int]:
    if len(blocks) < 2:
        return sorted(blocks, key=_vertical_key), 1
    wide = [block for block in blocks if _bbox_width(block.bbox) >= page_width * WIDE_BLOCK_RATIO]
    narrow = [block for block in blocks if block not in wide]
    columns = _column_clusters(narrow)
    if len(columns) < 2 or not _clusters_form_parallel_columns(columns):
        ordered = sorted(blocks, key=_vertical_key)
        for block in ordered:
            block.column_index = 1
        return ordered, 1

    for column_index, column in enumerate(columns, start=1):
        for block in column:
            block.column_index = column_index

    ordered: list[_PageTextBlock] = []
    remaining = list(narrow)
    for anchor in sorted(wide, key=_vertical_key):
        before = [block for block in remaining if block.bbox.y0 < anchor.bbox.y0]
        ordered.extend(_column_major(before))
        remaining = [block for block in remaining if block not in before]
        anchor.column_index = 1
        ordered.append(anchor)
    ordered.extend(_column_major(remaining))
    return ordered, len(columns)


def _column_clusters(blocks: list[_PageTextBlock]) -> list[list[_PageTextBlock]]:
    clusters: list[list[_PageTextBlock]] = []
    for block in sorted(blocks, key=lambda item: (item.bbox.x0, item.bbox.y0)):
        matching = next(
            (
                cluster
                for cluster in clusters
                if _horizontal_overlap_ratio(block, cluster) >= COLUMN_OVERLAP_RATIO
            ),
            None,
        )
        if matching is None:
            clusters.append([block])
        else:
            matching.append(block)
    return sorted(clusters, key=lambda cluster: min(item.bbox.x0 for item in cluster))


def _horizontal_overlap_ratio(
    block: _PageTextBlock,
    cluster: list[_PageTextBlock],
) -> float:
    cluster_x0 = min(item.bbox.x0 for item in cluster)
    cluster_x1 = max(item.bbox.x1 for item in cluster)
    overlap = max(0.0, min(block.bbox.x1, cluster_x1) - max(block.bbox.x0, cluster_x0))
    denominator = min(_bbox_width(block.bbox), cluster_x1 - cluster_x0)
    return overlap / denominator if denominator > 0 else 0.0


def _clusters_form_parallel_columns(
    columns: list[list[_PageTextBlock]],
) -> bool:
    return any(
        _vertical_overlap(left.bbox, right.bbox) > 0
        for left_index, left_column in enumerate(columns)
        for right_column in columns[left_index + 1 :]
        for left in left_column
        for right in right_column
    )


def _vertical_overlap(left: BoundingBox, right: BoundingBox) -> float:
    return max(0.0, min(left.y1, right.y1) - max(left.y0, right.y0))


def _column_major(blocks: list[_PageTextBlock]) -> list[_PageTextBlock]:
    return sorted(blocks, key=lambda item: (item.column_index, item.bbox.y0, item.bbox.x0))


def _vertical_key(block: _PageTextBlock) -> tuple[float, float, int]:
    return (block.bbox.y0, block.bbox.x0, block.source_block_number)


def _bbox_width(bbox: BoundingBox) -> float:
    return bbox.x1 - bbox.x0


def _bbox_union(boxes: list[BoundingBox]) -> BoundingBox:
    return BoundingBox(
        x0=min(box.x0 for box in boxes),
        y0=min(box.y0 for box in boxes),
        x1=max(box.x1 for box in boxes),
        y1=max(box.y1 for box in boxes),
    )


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _parser_identity() -> ParserIdentity:
    try:
        pymupdf_version = distribution_version("PyMuPDF")
    except DistributionNotFoundError:  # pragma: no cover - import already succeeded
        pymupdf_version = "unknown"
    return ParserIdentity(
        parser_name=PdfParserV2.parser_name,
        parser_version="2",
        source_format="pdf",
        parser_config={
            "text_extraction": "pymupdf_dict_blocks_spans",
            "canonical_line_separator": "\\n",
            "coordinate_space": "pdf_preview_rotated_points_top_left_v1",
            "reading_order": "wide_anchors_then_column_major_v1",
            "repeated_page_edges": "suppress_exact_normalized_majority",
            "table_detection": "deferred_text_only",
        },
        runtime_dependencies={"PyMuPDF": pymupdf_version},
    )
