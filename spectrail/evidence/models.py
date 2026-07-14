from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PDF_PREVIEW_COORDINATE_SPACE = "pdf_preview_rotated_points_top_left_v1"

CoordinateSpace = Literal["pdf_preview_rotated_points_top_left_v1"]
OffsetEncoding = Literal["unicode_code_point"]
EvidenceCapability = Literal["text_range", "page_region", "table_cell"]
EvidencePolicy = Literal[
    "quote_only",
    "structured_if_available",
    "structured_required",
]
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
TABLE_ID_RE = re.compile(r"^tbl_(\d{8})$")
CELL_ID_RE = re.compile(r"^cell_(\d{8})_r(\d{4})_c(\d{4})$")


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
    """Evidence-bound table reference whose topology requires an EvidenceIndex."""

    table_id: str = Field(
        description="Stable table ID resolved and validated against an EvidenceIndex."
    )
    cell_ids: list[str] = Field(
        description=(
            "Stable logical cell IDs in canonical column order. Selection continuity "
            "is validated by SourceLocatorValidator against EvidenceIndex cell spans."
        )
    )
    row_indices: list[int] = Field(
        description="EvidenceIndex row anchors corresponding to cell_ids."
    )
    selected_row_index: int = Field(
        description="Physical table row occupied by every selected logical cell."
    )
    column_indices: list[int] = Field(
        description=(
            "EvidenceIndex starting column anchors corresponding to cell_ids; these "
            "anchors alone cannot prove continuity when cells have column_span > 1."
        )
    )
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
        if self.selected_row_index < 1:
            raise ValueError("selected physical table row must be 1-based")
        if self.column_indices != sorted(self.column_indices):
            raise ValueError("table locator cells must use canonical column order")
        if len(set(self.column_indices)) != size:
            raise ValueError("table locator cell columns must be unique")
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
    source_format: Literal["markdown", "docx", "pdf"]
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
    table_row_start: int | None = None
    table_row_end: int | None = None
    cell_ids: list[str] = Field(default_factory=list)
    expected_capabilities: list[EvidenceCapability] = Field(
        default_factory=lambda: ["text_range"]
    )
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
        if "text_range" not in self.expected_capabilities:
            raise ValueError("all evidence blocks must expect text_range")
        if "table_cell" in self.available_capabilities and (
            self.table_id is None
            or self.table_row_start is None
            or self.table_row_end is None
            or not self.cell_ids
        ):
            raise ValueError(
                "available table_cell capability requires table_id, a primary row "
                "range, and cell_ids"
            )
        if (self.table_row_start is None) != (self.table_row_end is None):
            raise ValueError("table primary row range requires both endpoints")
        if self.table_row_start is not None and self.table_row_end is not None:
            if self.table_id is None:
                raise ValueError("table primary row range requires table_id")
            if self.table_row_start < 1 or self.table_row_end < self.table_row_start:
                raise ValueError("table primary row range is invalid")
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

    def occupies_row(self, row_index: int) -> bool:
        return self.row_index <= row_index < self.row_index + self.row_span

    @field_validator("text_sha256")
    @classmethod
    def validate_text_hash(cls, value: str) -> str:
        return _validate_sha256(value, "text_sha256")

    @model_validator(mode="after")
    def validate_cell(self) -> "TableCellRecord":
        if min(self.row_index, self.column_index, self.row_span, self.column_span) < 1:
            raise ValueError("cell indices and spans must be positive")
        if self.text_sha256 != hashlib.sha256(self.text.encode("utf-8")).hexdigest():
            raise ValueError("cell text_sha256 does not match cell text")
        if self.page is not None and self.page < 1:
            raise ValueError("cell page must be 1-based")
        if self.bbox is not None and self.page is None:
            raise ValueError("a cell bbox requires a page")
        return self


class CellBlockOccurrence(EvidenceModel):
    occurrence_id: str
    cell_id: str
    block_id: str
    physical_row_index: int
    canonical_start: int
    canonical_end: int
    offset_encoding: OffsetEncoding = "unicode_code_point"
    occurrence_role: Literal[
        "original",
        "repeated_header",
        "row_span_projection",
        "duplicate_text_occurrence",
    ] = "original"
    prompt_start: int | None = None
    prompt_end: int | None = None

    @model_validator(mode="after")
    def validate_occurrence(self) -> "CellBlockOccurrence":
        if self.physical_row_index < 1:
            raise ValueError("occurrence physical row must be 1-based")
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
    topology_status: Literal["complete", "sparse"] = Field(
        description=(
            "Whether logical cells cover the complete rectangular grid or an "
            "explicitly sparse best-effort table topology."
        )
    )
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
        if self.parser_method == "docx_xml" and self.topology_status != "complete":
            raise ValueError("DOCX tables must declare complete topology")
        _require_unique(self.block_ids, "table block IDs")
        _require_unique(self.cell_ids, "table cell IDs")
        _require_unique(self.occurrence_ids, "table occurrence IDs")
        return self


class EvidenceIndex(EvidenceModel):
    schema_version: Literal["evidence_v5"] = "evidence_v5"
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

    @model_validator(mode="before")
    @classmethod
    def reject_evidence_v4(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("schema_version") == "evidence_v4":
            raise ValueError("EVIDENCE_V4_REBUILD_REQUIRED")
        return value

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

        if self.parser_identity.source_format != self.source_format:
            raise ValueError(
                "parser identity source_format does not match evidence source_format"
            )

        parser_method_by_format = {
            "docx": "docx_xml",
            "pdf": "pymupdf_find_tables",
        }
        if self.tables and self.source_format not in parser_method_by_format:
            raise ValueError(
                f"evidence tables are unsupported for source format: {self.source_format}"
            )
        expected_parser_method = parser_method_by_format.get(self.source_format)
        for table in self.tables:
            if table.parser_method != expected_parser_method:
                raise ValueError(
                    "table parser_method does not match evidence source_format: "
                    f"{table.table_id}"
                )
            if self.source_format == "docx" and table.topology_status != "complete":
                raise ValueError(
                    f"DOCX table topology must be complete: {table.table_id}"
                )

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
            if occurrence.cell_id not in block.cell_ids:
                raise ValueError(
                    "occurrence cell is not registered by its block: "
                    f"{occurrence.occurrence_id}"
                )
            table = tables_by_id[cell.table_id]
            if occurrence.occurrence_id not in table.occurrence_ids:
                raise ValueError(
                    f"occurrence is not registered by its table: {occurrence.occurrence_id}"
                )
        occurrence_pairs = {
            (occurrence.block_id, occurrence.cell_id)
            for occurrence in self.cell_occurrences
        }
        for block in self.blocks:
            for cell_id in block.cell_ids:
                if (block.block_id, cell_id) not in occurrence_pairs:
                    raise ValueError(
                        f"block cell has no occurrence in the block: {block.block_id}/{cell_id}"
                    )
            if block.table_id is not None:
                table = tables_by_id[block.table_id]
                if block.table_row_start is None or block.table_row_end is None:
                    raise ValueError(
                        f"table block requires a primary row range: {block.block_id}"
                    )
                if block.table_row_end > table.row_count:
                    raise ValueError(
                        f"table block row range exceeds table bounds: {block.block_id}"
                    )
                if (
                    table.topology_status == "sparse"
                    and "table_cell" in block.available_capabilities
                    and any(
                        _cell_spans_have_gaps(
                            [cells_by_id[cell_id] for cell_id in table.cell_ids],
                            row_index,
                        )
                        for row_index in range(
                            block.table_row_start,
                            block.table_row_end + 1,
                        )
                    )
                ):
                    raise ValueError(
                        "sparse table row-group with unknown column gaps cannot expose "
                        f"table_cell capability: {block.block_id}"
                    )
        for table in self.tables:
            _validate_table_topology(
                table,
                cells_by_id,
                blocks_by_id,
                occurrences_by_id,
            )
        _validate_table_row_groups(
            self.tables,
            cells_by_id,
            self.cell_occurrences,
            blocks_by_id,
        )
        _validate_occurrence_roles(
            cells_by_id,
            self.cell_occurrences,
            blocks_by_id,
            tables_by_id,
        )
        _validate_occurrence_ranges(
            cells_by_id,
            self.cell_occurrences,
            blocks_by_id,
        )
        return self


def _validate_table_row_groups(
    tables: list[TableRecord],
    cells_by_id: dict[str, TableCellRecord],
    occurrences: list[CellBlockOccurrence],
    blocks_by_id: dict[str, BlockEvidenceRecord],
) -> None:
    structural_roles = {"original", "row_span_projection"}
    structural_counts: dict[tuple[str, str, int, str], int] = {}
    repeated_counts: dict[tuple[str, str, int], int] = {}
    repeated_keys: set[tuple[str, str, int]] = set()
    duplicate_text_keys: set[tuple[str, str, int]] = set()
    repeated_cells_by_block: dict[str, set[str]] = {}
    for occurrence in occurrences:
        if occurrence.occurrence_role in structural_roles:
            key = (
                occurrence.block_id,
                occurrence.cell_id,
                occurrence.physical_row_index,
                occurrence.occurrence_role,
            )
            structural_counts[key] = structural_counts.get(key, 0) + 1
        elif occurrence.occurrence_role == "repeated_header":
            repeated_key = (
                occurrence.block_id,
                occurrence.cell_id,
                occurrence.physical_row_index,
            )
            repeated_counts[repeated_key] = repeated_counts.get(repeated_key, 0) + 1
            repeated_keys.add(repeated_key)
            if repeated_counts[repeated_key] > 1:
                raise ValueError(
                    "a table block may contain at most one repeated header occurrence "
                    "for each cell and physical row: "
                    f"{occurrence.block_id}/{occurrence.cell_id}/"
                    f"row {occurrence.physical_row_index}"
                )
            repeated_cells_by_block.setdefault(occurrence.block_id, set()).add(
                occurrence.cell_id
            )
        elif (
            occurrence.occurrence_role == "duplicate_text_occurrence"
            and cells_by_id[occurrence.cell_id].text.strip()
        ):
            duplicate_text_keys.add(
                (
                    occurrence.block_id,
                    occurrence.cell_id,
                    occurrence.physical_row_index,
                )
            )

    ambiguous_repeated_keys = repeated_keys & duplicate_text_keys
    if ambiguous_repeated_keys:
        block_id, cell_id, physical_row = sorted(ambiguous_repeated_keys)[0]
        raise ValueError(
            "a repeated header cell and physical row may have only one non-empty "
            "text occurrence per block: "
            f"{block_id}/{cell_id}/row {physical_row}"
        )

    for table in tables:
        primary_owners: dict[int, str] = {}
        expected_row_start = 1
        for block_id in table.block_ids:
            block = blocks_by_id[block_id]
            assert block.table_row_start is not None
            assert block.table_row_end is not None
            if block.table_row_start != expected_row_start:
                raise ValueError(
                    "table primary row ranges must be ordered, contiguous, and "
                    f"complete: {table.table_id}/{block_id}"
                )
            expected_row_start = block.table_row_end + 1
            for row_index in range(block.table_row_start, block.table_row_end + 1):
                owner = primary_owners.get(row_index)
                if owner is not None:
                    raise ValueError(
                        "table primary row ranges overlap: "
                        f"{table.table_id}/row {row_index}/{owner}/{block_id}"
                    )
                primary_owners[row_index] = block_id

            primary_cells = {
                cell_id
                for cell_id in table.cell_ids
                if any(
                    cells_by_id[cell_id].occupies_row(row_index)
                    for row_index in range(
                        block.table_row_start,
                        block.table_row_end + 1,
                    )
                )
            }
            expected_cells = primary_cells | repeated_cells_by_block.get(block_id, set())
            if set(block.cell_ids) != expected_cells:
                raise ValueError(
                    "table block cell map does not match its primary rows and projections: "
                    f"{block_id}"
                )

            for row_index in range(block.table_row_start, block.table_row_end + 1):
                for cell_id in table.cell_ids:
                    cell = cells_by_id[cell_id]
                    if not cell.occupies_row(row_index):
                        continue
                    expected_role = (
                        "original" if row_index == cell.row_index else "row_span_projection"
                    )
                    key = (block_id, cell_id, row_index, expected_role)
                    if structural_counts.get(key) != 1:
                        raise ValueError(
                            "table primary row cell must have exactly one structural "
                            f"occurrence: {block_id}/{cell_id}/row {row_index}"
                        )

        if (
            expected_row_start != table.row_count + 1
            or set(primary_owners) != set(range(1, table.row_count + 1))
        ):
            raise ValueError(
                f"table primary row ranges must cover every physical row: {table.table_id}"
            )


def _validate_occurrence_roles(
    cells_by_id: dict[str, TableCellRecord],
    occurrences: list[CellBlockOccurrence],
    blocks_by_id: dict[str, BlockEvidenceRecord],
    tables_by_id: dict[str, TableRecord],
) -> None:
    originals: dict[str, list[CellBlockOccurrence]] = {
        cell_id: [] for cell_id in cells_by_id
    }
    support_keys = {
        (
            occurrence.block_id,
            occurrence.cell_id,
            occurrence.physical_row_index,
        )
        for occurrence in occurrences
        if occurrence.occurrence_role
        in {"original", "row_span_projection", "repeated_header"}
    }
    for occurrence in occurrences:
        cell = cells_by_id[occurrence.cell_id]
        block = blocks_by_id[occurrence.block_id]
        table = tables_by_id[cell.table_id]
        physical_row = occurrence.physical_row_index
        assert block.table_row_start is not None
        assert block.table_row_end is not None
        if physical_row > table.row_count:
            raise ValueError(
                f"occurrence physical row exceeds table bounds: {occurrence.occurrence_id}"
            )
        in_primary_range = block.table_row_start <= physical_row <= block.table_row_end
        if occurrence.occurrence_role == "original":
            if physical_row != cell.row_index or not in_primary_range:
                raise ValueError(
                    "original cell occurrence must use its primary anchor row: "
                    f"{occurrence.occurrence_id}"
                )
            originals[cell.cell_id].append(occurrence)
        elif occurrence.occurrence_role == "row_span_projection":
            if (
                physical_row <= cell.row_index
                or not cell.occupies_row(physical_row)
                or not in_primary_range
            ):
                raise ValueError(
                    "row-span projection must use a primary covered row after the anchor: "
                    f"{occurrence.occurrence_id}"
                )
        elif occurrence.occurrence_role == "repeated_header":
            if not cell.is_header or physical_row != cell.row_index or in_primary_range:
                raise ValueError(
                    "repeated header occurrence requires a projected header anchor row: "
                    f"{occurrence.occurrence_id}"
                )
        elif occurrence.occurrence_role == "duplicate_text_occurrence":
            if (
                occurrence.block_id,
                occurrence.cell_id,
                physical_row,
            ) not in support_keys:
                raise ValueError(
                    "duplicate text occurrence requires a structural occurrence in "
                    f"the same block and physical row: {occurrence.occurrence_id}"
                )

    for cell_id, original_items in originals.items():
        if len(original_items) != 1:
            raise ValueError(
                "each logical cell must have exactly one original occurrence: "
                f"{cell_id}"
            )
    for occurrence in occurrences:
        if occurrence.occurrence_role != "repeated_header":
            continue
        original = originals[occurrence.cell_id][0]
        table = tables_by_id[cells_by_id[occurrence.cell_id].table_id]
        block_positions = {
            block_id: index for index, block_id in enumerate(table.block_ids)
        }
        if (
            occurrence.block_id == original.block_id
            or block_positions[occurrence.block_id] <= block_positions[original.block_id]
        ):
            raise ValueError(
                "repeated header occurrence must be in a later table block than "
                f"its original: {occurrence.occurrence_id}"
            )


def _validate_occurrence_ranges(
    cells_by_id: dict[str, TableCellRecord],
    occurrences: list[CellBlockOccurrence],
    blocks_by_id: dict[str, BlockEvidenceRecord],
) -> None:
    by_block: dict[str, list[CellBlockOccurrence]] = {}
    for occurrence in occurrences:
        if cells_by_id[occurrence.cell_id].text:
            if occurrence.canonical_end <= occurrence.canonical_start:
                raise ValueError(
                    "non-empty cell occurrence must use a non-empty canonical range: "
                    f"{occurrence.occurrence_id}"
                )
            by_block.setdefault(occurrence.block_id, []).append(occurrence)

    for block_id, block_occurrences in by_block.items():
        ordered = sorted(
            block_occurrences,
            key=lambda item: (
                item.canonical_start,
                item.canonical_end,
                item.physical_row_index,
                item.occurrence_id,
            ),
        )
        for index, current in enumerate(ordered):
            for following in ordered[index + 1 :]:
                if following.canonical_start >= current.canonical_end:
                    break
                raise ValueError(
                    "non-empty cell occurrence ranges must not overlap: "
                    f"{block_id}/{current.occurrence_id}/{following.occurrence_id}"
                )

        row_groups = rendered_table_row_groups(
            blocks_by_id[block_id],
            block_occurrences,
        )
        for index, current in enumerate(row_groups):
            current_start, current_end, current_label, current_row, _ = current
            for following in row_groups[index + 1 :]:
                following_start, following_end, following_label, following_row, _ = (
                    following
                )
                if following_start >= current_end:
                    break
                raise ValueError(
                    "rendered table row occurrence ranges must not interleave: "
                    f"{block_id}/{current_label}:{current_row}="
                    f"{current_start}:{current_end}/"
                    f"{following_label}:{following_row}="
                    f"{following_start}:{following_end}"
                )


def rendered_table_row_groups(
    block: BlockEvidenceRecord,
    occurrences: list[CellBlockOccurrence],
) -> list[tuple[int, int, str, int, list[CellBlockOccurrence]]]:
    if block.table_row_start is None or block.table_row_end is None:
        raise ValueError("rendered table rows require a primary row range")
    grouped: dict[tuple[str, int], list[CellBlockOccurrence]] = {}
    for occurrence in occurrences:
        if occurrence.block_id != block.block_id:
            continue
        in_primary_range = (
            block.table_row_start
            <= occurrence.physical_row_index
            <= block.table_row_end
        )
        label = "row" if in_primary_range else "repeated_header_row"
        grouped.setdefault((label, occurrence.physical_row_index), []).append(
            occurrence
        )
    return sorted(
        (
            min(item.canonical_start for item in items),
            max(item.canonical_end for item in items),
            label,
            row_index,
            sorted(
                items,
                key=lambda item: (
                    item.canonical_start,
                    item.canonical_end,
                    item.occurrence_id,
                ),
            ),
        )
        for (label, row_index), items in grouped.items()
    )


def _validate_table_topology(
    table: TableRecord,
    cells_by_id: dict[str, TableCellRecord],
    blocks_by_id: dict[str, BlockEvidenceRecord],
    occurrences_by_id: dict[str, CellBlockOccurrence],
) -> None:
    table_match = TABLE_ID_RE.fullmatch(table.table_id)
    if table_match is None:
        raise ValueError(f"table ID is not canonical: {table.table_id}")
    table_index = table_match.group(1)
    if int(table_index) < 1:
        raise ValueError("table ID index must be positive")
    if not table.block_ids:
        raise ValueError(f"table must contain at least one block: {table.table_id}")
    if not table.cell_ids:
        raise ValueError(f"table must contain at least one cell: {table.table_id}")
    if not table.occurrence_ids:
        raise ValueError(
            f"table must contain at least one cell occurrence: {table.table_id}"
        )

    block_cell_ids = {
        cell_id
        for block_id in table.block_ids
        for cell_id in blocks_by_id[block_id].cell_ids
    }
    occurrence_cell_ids = {
        occurrences_by_id[occurrence_id].cell_id
        for occurrence_id in table.occurrence_ids
    }
    for cell_id in table.cell_ids:
        if cell_id not in block_cell_ids:
            raise ValueError(f"table cell is not referenced by a table block: {cell_id}")
        if cell_id not in occurrence_cell_ids:
            raise ValueError(f"table cell has no occurrence: {cell_id}")

    occupied: dict[tuple[int, int], str] = {}
    anchors: set[tuple[int, int]] = set()

    for cell_id in table.cell_ids:
        cell = cells_by_id[cell_id]
        cell_match = CELL_ID_RE.fullmatch(cell.cell_id)
        if cell_match is None:
            raise ValueError(f"cell ID is not canonical: {cell.cell_id}")
        encoded_table, encoded_row, encoded_column = cell_match.groups()
        if (
            encoded_table != table_index
            or int(encoded_row) != cell.row_index
            or int(encoded_column) != cell.column_index
        ):
            raise ValueError(
                "cell ID does not match table, row, and column fields: "
                f"{cell.cell_id}"
            )

        row_end = cell.row_index + cell.row_span - 1
        column_end = cell.column_index + cell.column_span - 1
        if row_end > table.row_count or column_end > table.column_count:
            raise ValueError(f"cell span exceeds table bounds: {cell.cell_id}")

        anchor = (cell.row_index, cell.column_index)
        if anchor in anchors:
            raise ValueError(
                "table cells share an anchor coordinate: "
                f"{table.table_id}/r{cell.row_index}/c{cell.column_index}"
            )
        anchors.add(anchor)

        for row_index in range(cell.row_index, row_end + 1):
            for column_index in range(cell.column_index, column_end + 1):
                coordinate = (row_index, column_index)
                owner = occupied.get(coordinate)
                if owner is not None:
                    raise ValueError(
                        "table cells overlap at "
                        f"row {row_index} column {column_index}: "
                        f"{owner}, {cell.cell_id}"
                    )
                occupied[coordinate] = cell.cell_id

    if (
        table.topology_status == "complete"
        and len(occupied) != table.row_count * table.column_count
    ):
        raise ValueError(f"complete table topology has uncovered cells: {table.table_id}")


def _cell_spans_have_gaps(
    cells: list[TableCellRecord],
    row_index: int,
) -> bool:
    intervals = sorted(
        (
            cell.column_index,
            cell.column_index + cell.column_span,
        )
        for cell in cells
        if cell.occupies_row(row_index)
    )
    if not intervals:
        return True
    cursor = intervals[0][1]
    for start, end in intervals[1:]:
        if start > cursor:
            return True
        cursor = max(cursor, end)
    return False


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
