from __future__ import annotations

import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PDF_PREVIEW_COORDINATE_SPACE = "pdf_preview_rotated_points_top_left_v1"

CoordinateSpace = Literal["pdf_preview_rotated_points_top_left_v1"]
OffsetEncoding = Literal["unicode_code_point"]
EvidenceCapability = Literal["text_range", "page_region", "table_cell"]
CapabilityStatus = Literal[
    "UNVERIFIED",
    "PASS",
    "WARNING_UNAVAILABLE",
    "WARNING_AMBIGUOUS",
    "FAIL_INVALID_REFERENCE",
    "FAIL_DERIVATION",
]
LocatorStatus = Literal[
    "UNVERIFIED",
    "PASS_DERIVED",
    "PASS_STRUCTURED",
    "WARNING_UNAVAILABLE",
    "WARNING_AMBIGUOUS",
    "FAIL_INVALID_REFERENCE",
    "FAIL_DERIVATION",
]

STRUCTURED_CAPABILITIES = frozenset({"page_region", "table_cell"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BoundingBox(EvidenceModel):
    x0: float
    y0: float
    x1: float
    y1: float
    coordinate_space: CoordinateSpace = PDF_PREVIEW_COORDINATE_SPACE

    @model_validator(mode="after")
    def validate_bounds(self) -> "BoundingBox":
        values = (self.x0, self.y0, self.x1, self.y1)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("bbox coordinates must be finite")
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("bbox must have positive width and height")
        return self


class TextLocator(EvidenceModel):
    block_id: str
    start: int
    end: int
    offset_encoding: OffsetEncoding = "unicode_code_point"
    match_basis: Literal["exact", "normalized"]

    @model_validator(mode="after")
    def validate_range(self) -> "TextLocator":
        if self.start < 0 or self.end <= self.start:
            raise ValueError("text locator must be a non-empty half-open range")
        return self


class PageLocator(EvidenceModel):
    page: int
    bbox: BoundingBox
    page_width: float
    page_height: float
    source_rotation: Literal[0, 90, 180, 270] = 0
    coordinate_space: CoordinateSpace = PDF_PREVIEW_COORDINATE_SPACE
    derivation: Literal["block_bbox", "quote_span_union", "table_cell_union"]

    @model_validator(mode="after")
    def validate_page_geometry(self) -> "PageLocator":
        if self.page < 1:
            raise ValueError("page must be 1-based")
        if self.page_width <= 0 or self.page_height <= 0:
            raise ValueError("page dimensions must be positive")
        if self.bbox.coordinate_space != self.coordinate_space:
            raise ValueError("page locator and bbox coordinate spaces must match")
        if (
            self.bbox.x0 < 0
            or self.bbox.y0 < 0
            or self.bbox.x1 > self.page_width
            or self.bbox.y1 > self.page_height
        ):
            raise ValueError("bbox must be within rotated preview page bounds")
        return self


class TableCellRef(EvidenceModel):
    table_id: str
    cell_id: str
    row_index: int
    column_index: int

    @model_validator(mode="after")
    def validate_indices(self) -> "TableCellRef":
        if self.row_index < 1 or self.column_index < 1:
            raise ValueError("table row and column indices must be 1-based")
        return self


class TableLocator(EvidenceModel):
    table_id: str
    cell_ids: list[str]
    row_indices: list[int]
    column_indices: list[int]
    bbox: BoundingBox | None = None

    @model_validator(mode="after")
    def validate_cell_shape(self) -> "TableLocator":
        size = len(self.cell_ids)
        if size == 0:
            raise ValueError("table locator must contain at least one cell")
        if len(set(self.cell_ids)) != size:
            raise ValueError("table locator cell IDs must be unique")
        if len(self.row_indices) != size or len(self.column_indices) != size:
            raise ValueError("cell, row, and column lists must have equal length")
        if any(index < 1 for index in [*self.row_indices, *self.column_indices]):
            raise ValueError("table row and column indices must be 1-based")
        if len(set(self.row_indices)) != 1:
            raise ValueError("selected table cells must belong to one logical row")
        if self.column_indices != sorted(self.column_indices):
            raise ValueError("table locator cells must use canonical column order")
        if self.column_indices != list(
            range(self.column_indices[0], self.column_indices[0] + size)
        ):
            raise ValueError("selected table cell columns must be contiguous")
        return self


class CapabilityValidationResult(EvidenceModel):
    capability: EvidenceCapability
    status: CapabilityStatus
    issue_code: str | None = None
    message: str | None = None


class PageRecord(EvidenceModel):
    page_id: str
    page: int
    width: float
    height: float
    source_rotation: Literal[0, 90, 180, 270]
    coordinate_space: CoordinateSpace = PDF_PREVIEW_COORDINATE_SPACE
    block_ids: list[str]
    table_ids: list[str]
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_page(self) -> "PageRecord":
        if self.page < 1:
            raise ValueError("page must be 1-based")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("page dimensions must be positive")
        _require_unique(self.block_ids, "page block IDs")
        _require_unique(self.table_ids, "page table IDs")
        return self


class TextFragmentRecord(EvidenceModel):
    fragment_id: str
    block_id: str
    start: int
    end: int
    offset_encoding: OffsetEncoding = "unicode_code_point"
    text: str
    page: int
    bbox: BoundingBox
    line_index: int
    span_index: int
    separator_before: str = ""

    @model_validator(mode="after")
    def validate_fragment(self) -> "TextFragmentRecord":
        if self.start < 0 or self.end <= self.start:
            raise ValueError("fragment must be a non-empty half-open range")
        if len(self.text) != self.end - self.start:
            raise ValueError("fragment text length must match its code-point range")
        if self.page < 1:
            raise ValueError("fragment page must be 1-based")
        if self.line_index < 0 or self.span_index < 0:
            raise ValueError("fragment line and span indices must be non-negative")
        return self


class ParserIdentity(EvidenceModel):
    parser_name: str
    parser_version: str
    parser_config: dict[str, Any] = Field(default_factory=dict)
    runtime_dependencies: dict[str, str] = Field(default_factory=dict)


class BlockEvidenceRecord(EvidenceModel):
    block_id: str
    text_length: int
    text_sha256: str
    page: int | None = None
    bbox: BoundingBox | None = None
    fragment_ids: list[str] = Field(default_factory=list)
    table_id: str | None = None
    cell_ids: list[str] = Field(default_factory=list)
    expected_capabilities: list[EvidenceCapability] = Field(default_factory=list)
    available_capabilities: list[EvidenceCapability] = Field(default_factory=list)

    @field_validator("text_sha256")
    @classmethod
    def validate_text_hash(cls, value: str) -> str:
        return _validate_sha256(value, "text_sha256")

    @model_validator(mode="after")
    def validate_block_evidence(self) -> "BlockEvidenceRecord":
        if self.text_length < 0:
            raise ValueError("block text length must be non-negative")
        if self.page is not None and self.page < 1:
            raise ValueError("block page must be 1-based")
        _require_unique(self.fragment_ids, "block fragment IDs")
        _require_unique(self.cell_ids, "block cell IDs")
        _require_unique(self.expected_capabilities, "expected capabilities")
        _require_unique(self.available_capabilities, "available capabilities")
        unexpected = set(self.available_capabilities) - set(self.expected_capabilities)
        if unexpected:
            raise ValueError(f"available capabilities are not expected: {sorted(unexpected)}")
        return self


class TableCellRecord(EvidenceModel):
    cell_id: str
    table_id: str
    row_index: int
    column_index: int
    row_span: int = 1
    column_span: int = 1
    text: str
    text_sha256: str
    is_header: bool = False
    page: int | None = None
    bbox: BoundingBox | None = None

    @field_validator("text_sha256")
    @classmethod
    def validate_text_hash(cls, value: str) -> str:
        return _validate_sha256(value, "text_sha256")

    @model_validator(mode="after")
    def validate_cell(self) -> "TableCellRecord":
        if min(self.row_index, self.column_index, self.row_span, self.column_span) < 1:
            raise ValueError("cell indices and spans must be positive")
        if self.page is not None and self.page < 1:
            raise ValueError("cell page must be 1-based")
        if self.bbox is not None and self.page is None:
            raise ValueError("a cell bbox requires a page")
        return self


class CellBlockOccurrence(EvidenceModel):
    occurrence_id: str
    cell_id: str
    block_id: str
    canonical_start: int
    canonical_end: int
    offset_encoding: OffsetEncoding = "unicode_code_point"
    occurrence_role: Literal["original", "repeated_header"] = "original"
    prompt_start: int | None = None
    prompt_end: int | None = None

    @model_validator(mode="after")
    def validate_occurrence(self) -> "CellBlockOccurrence":
        if self.canonical_start < 0 or self.canonical_end < self.canonical_start:
            raise ValueError("cell occurrence canonical range is invalid")
        if (self.prompt_start is None) != (self.prompt_end is None):
            raise ValueError("prompt occurrence range must provide both endpoints")
        if self.prompt_start is not None and (
            self.prompt_start < 0 or self.prompt_end <= self.prompt_start  # type: ignore[operator]
        ):
            raise ValueError("prompt occurrence range is invalid")
        return self


class TableRecord(EvidenceModel):
    table_id: str
    block_ids: list[str]
    page: int | None = None
    bbox: BoundingBox | None = None
    row_count: int
    column_count: int
    cell_ids: list[str]
    occurrence_ids: list[str]
    parser_method: Literal["docx_xml", "pymupdf_find_tables"]
    confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_table(self) -> "TableRecord":
        if self.row_count < 1 or self.column_count < 1:
            raise ValueError("table dimensions must be positive")
        if self.page is not None and self.page < 1:
            raise ValueError("table page must be 1-based")
        if self.bbox is not None and self.page is None:
            raise ValueError("a table bbox requires a page")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("table confidence must be between 0 and 1")
        _require_unique(self.block_ids, "table block IDs")
        _require_unique(self.cell_ids, "table cell IDs")
        _require_unique(self.occurrence_ids, "table occurrence IDs")
        return self


class EvidenceIndex(EvidenceModel):
    schema_version: Literal["evidence_v1"] = "evidence_v1"
    document_id: str
    document_name: str
    source_format: str
    source_sha256: str
    parser_identity: ParserIdentity
    evidence_fingerprint: str
    pages: list[PageRecord] = Field(default_factory=list)
    blocks: list[BlockEvidenceRecord] = Field(default_factory=list)
    fragments: list[TextFragmentRecord] = Field(default_factory=list)
    tables: list[TableRecord] = Field(default_factory=list)
    cells: list[TableCellRecord] = Field(default_factory=list)
    cell_occurrences: list[CellBlockOccurrence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("source_sha256", "evidence_fingerprint")
    @classmethod
    def validate_hashes(cls, value: str, info) -> str:
        return _validate_sha256(value, info.field_name)

    @model_validator(mode="after")
    def validate_references(self) -> "EvidenceIndex":
        page_ids = _unique_ids(self.pages, "page_id", "page IDs")
        block_ids = _unique_ids(self.blocks, "block_id", "block IDs")
        fragment_ids = _unique_ids(self.fragments, "fragment_id", "fragment IDs")
        table_ids = _unique_ids(self.tables, "table_id", "table IDs")
        cell_ids = _unique_ids(self.cells, "cell_id", "cell IDs")
        occurrence_ids = _unique_ids(
            self.cell_occurrences, "occurrence_id", "cell occurrence IDs"
        )
        del page_ids

        blocks_by_id = {item.block_id: item for item in self.blocks}
        fragments_by_id = {item.fragment_id: item for item in self.fragments}
        tables_by_id = {item.table_id: item for item in self.tables}
        cells_by_id = {item.cell_id: item for item in self.cells}
        occurrences_by_id = {
            item.occurrence_id: item for item in self.cell_occurrences
        }

        for page in self.pages:
            _require_subset(page.block_ids, block_ids, f"page {page.page_id} block IDs")
            _require_subset(page.table_ids, table_ids, f"page {page.page_id} table IDs")
            for block_id in page.block_ids:
                if blocks_by_id[block_id].page != page.page:
                    raise ValueError(
                        f"page {page.page_id} contains block from another page: {block_id}"
                    )
            for table_id in page.table_ids:
                if tables_by_id[table_id].page != page.page:
                    raise ValueError(
                        f"page {page.page_id} contains table from another page: {table_id}"
                    )
        for fragment in self.fragments:
            block = blocks_by_id.get(fragment.block_id)
            if block is None:
                raise ValueError(f"fragment references unknown block: {fragment.block_id}")
            if fragment.end > block.text_length:
                raise ValueError(f"fragment range exceeds block text: {fragment.fragment_id}")
            if block.page != fragment.page:
                raise ValueError(
                    f"fragment and block pages differ: {fragment.fragment_id}"
                )
        for block in self.blocks:
            _require_subset(block.fragment_ids, fragment_ids, f"block {block.block_id} fragment IDs")
            _require_subset(block.cell_ids, cell_ids, f"block {block.block_id} cell IDs")
            for fragment_id in block.fragment_ids:
                if fragments_by_id[fragment_id].block_id != block.block_id:
                    raise ValueError(
                        f"block references fragment owned by another block: {fragment_id}"
                    )
            if block.cell_ids and block.table_id is None:
                raise ValueError(f"block cell IDs require table_id: {block.block_id}")
            if block.table_id is not None and block.table_id not in table_ids:
                raise ValueError(f"block references unknown table: {block.table_id}")
            if block.table_id is not None:
                table = tables_by_id[block.table_id]
                if block.block_id not in table.block_ids:
                    raise ValueError(
                        f"block is not registered by its table: {block.block_id}"
                    )
                for cell_id in block.cell_ids:
                    if cells_by_id[cell_id].table_id != block.table_id:
                        raise ValueError(
                            f"block references cell owned by another table: {cell_id}"
                        )
        for table in self.tables:
            _require_subset(table.block_ids, block_ids, f"table {table.table_id} block IDs")
            _require_subset(table.cell_ids, cell_ids, f"table {table.table_id} cell IDs")
            _require_subset(
                table.occurrence_ids,
                occurrence_ids,
                f"table {table.table_id} occurrence IDs",
            )
            for block_id in table.block_ids:
                if blocks_by_id[block_id].table_id != table.table_id:
                    raise ValueError(
                        f"table contains block owned by another table: {block_id}"
                    )
            for cell_id in table.cell_ids:
                if cells_by_id[cell_id].table_id != table.table_id:
                    raise ValueError(
                        f"table contains cell owned by another table: {cell_id}"
                    )
            for occurrence_id in table.occurrence_ids:
                occurrence = occurrences_by_id[occurrence_id]
                if occurrence.cell_id not in table.cell_ids:
                    raise ValueError(
                        f"table occurrence references a cell outside the table: {occurrence_id}"
                    )
                if occurrence.block_id not in table.block_ids:
                    raise ValueError(
                        f"table occurrence references a block outside the table: {occurrence_id}"
                    )
        for cell in self.cells:
            table = tables_by_id.get(cell.table_id)
            if table is None:
                raise ValueError(f"cell references unknown table: {cell.table_id}")
            if cell.cell_id not in table.cell_ids:
                raise ValueError(f"cell is not registered by its table: {cell.cell_id}")
        for occurrence in self.cell_occurrences:
            cell = cells_by_id.get(occurrence.cell_id)
            block = blocks_by_id.get(occurrence.block_id)
            if cell is None:
                raise ValueError(f"occurrence references unknown cell: {occurrence.cell_id}")
            if block is None:
                raise ValueError(f"occurrence references unknown block: {occurrence.block_id}")
            if occurrence.canonical_end > block.text_length:
                raise ValueError(
                    f"occurrence range exceeds block text: {occurrence.occurrence_id}"
                )
            if block.table_id != cell.table_id:
                raise ValueError(
                    f"occurrence cell and block tables differ: {occurrence.occurrence_id}"
                )
            table = tables_by_id[cell.table_id]
            if occurrence.occurrence_id not in table.occurrence_ids:
                raise ValueError(
                    f"occurrence is not registered by its table: {occurrence.occurrence_id}"
                )
        return self


def aggregate_locator_status(
    expected_capabilities: list[EvidenceCapability],
    results: list[CapabilityValidationResult],
) -> LocatorStatus:
    expected = set(expected_capabilities)
    by_capability = {result.capability: result.status for result in results}
    if len(by_capability) != len(results):
        raise ValueError("capability validation results must be unique")
    unexpected = set(by_capability) - expected
    if unexpected:
        raise ValueError(
            f"capability validation results are not expected: {sorted(unexpected)}"
        )

    statuses = [by_capability.get(capability, "UNVERIFIED") for capability in expected]
    for failure in ("FAIL_INVALID_REFERENCE", "FAIL_DERIVATION"):
        if failure in statuses:
            return failure  # type: ignore[return-value]
    if "WARNING_AMBIGUOUS" in statuses:
        return "WARNING_AMBIGUOUS"
    if "WARNING_UNAVAILABLE" in statuses:
        return "WARNING_UNAVAILABLE"

    expected_structured = expected & STRUCTURED_CAPABILITIES
    if expected_structured and statuses and all(status == "PASS" for status in statuses):
        return "PASS_STRUCTURED"
    if (
        "text_range" in expected
        and not expected_structured
        and by_capability.get("text_range") == "PASS"
    ):
        return "PASS_DERIVED"
    return "UNVERIFIED"


def _validate_sha256(value: str, field_name: str) -> str:
    if not SHA256_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return value


def _require_unique(values: list[Any], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")


def _unique_ids(items: list[Any], field_name: str, label: str) -> set[str]:
    values = [getattr(item, field_name) for item in items]
    _require_unique(values, label)
    return set(values)


def _require_subset(values: list[str], allowed: set[str], label: str) -> None:
    missing = sorted(set(values) - allowed)
    if missing:
        raise ValueError(f"{label} reference unknown IDs: {missing}")
