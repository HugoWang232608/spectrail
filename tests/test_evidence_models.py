import pytest
from pydantic import ValidationError

from spectrail.evidence import (
    BlockEvidenceRecord,
    BoundingBox,
    CapabilityValidationResult,
    CellBlockOccurrence,
    EvidenceIndex,
    PageRecord,
    ParserIdentity,
    TableCellRecord,
    TableLocator,
    TableRecord,
    aggregate_locator_status,
    cell_id,
    occurrence_id,
    sha256_text,
    table_id,
)


def test_bbox_rejects_non_positive_geometry():
    with pytest.raises(ValidationError, match="positive width and height"):
        BoundingBox(x0=1, y0=1, x1=1, y1=2)


def test_all_evidence_blocks_must_expect_text_range():
    with pytest.raises(ValidationError, match="must expect text_range"):
        BlockEvidenceRecord(
            block_id="blk_0001",
            text_length=1,
            text_sha256=sha256_text("x"),
            expected_capabilities=["page_region"],
        )


def test_available_table_cell_capability_requires_cell_map():
    with pytest.raises(ValidationError, match="requires table_id, a primary row range"):
        BlockEvidenceRecord(
            block_id="blk_0001",
            text_length=1,
            text_sha256=sha256_text("x"),
            table_id="tbl_00000001",
            expected_capabilities=["text_range", "table_cell"],
            available_capabilities=["text_range", "table_cell"],
        )


def test_table_locator_requires_canonical_unique_start_columns():
    with pytest.raises(ValidationError, match="canonical column order"):
        TableLocator(
            table_id="tbl_00000001",
            cell_ids=["c2", "c1"],
            row_indices=[1, 1],
            selected_row_index=1,
            column_indices=[2, 1],
        )
    locator = TableLocator(
        table_id="tbl_00000001",
        cell_ids=["c1", "c3"],
        row_indices=[1, 1],
        selected_row_index=1,
        column_indices=[1, 3],
    )
    assert locator.column_indices == [1, 3]
    with pytest.raises(ValidationError, match="unique"):
        TableLocator(
            table_id="tbl_00000001",
            cell_ids=["c1", "c2"],
            row_indices=[1, 1],
            selected_row_index=1,
            column_indices=[1, 1],
        )


def test_table_locator_schema_marks_continuity_as_evidence_bound():
    schema = TableLocator.model_json_schema()

    assert "EvidenceIndex" in schema["description"]
    assert "SourceLocatorValidator" in schema["properties"]["cell_ids"]["description"]
    assert "cannot prove continuity" in schema["properties"]["column_indices"]["description"]
    assert "topology_status" in TableRecord.model_json_schema()["required"]


def _topology_index(
    *,
    table_identifier: str = "tbl_00000001",
    row_count: int = 1,
    column_count: int = 3,
    topology_status: str = "complete",
    parser_method: str = "docx_xml",
    cells: list[TableCellRecord],
) -> EvidenceIndex:
    source_format = "pdf" if parser_method == "pymupdf_find_tables" else "docx"
    parser_name = "pdf_parser_v2" if source_format == "pdf" else "docx_parser_v2"
    block_id = "blk_0001"
    occurrences = []
    block_text_parts = []
    offset = 0
    occurrence_index = 0
    for physical_row_index in range(1, row_count + 1):
        row_cells = sorted(
            (cell for cell in cells if cell.occupies_row(physical_row_index)),
            key=lambda cell: (cell.column_index, cell.row_index, cell.cell_id),
        )
        for cell in row_cells:
            block_text_parts.append(cell.text)
            end = offset + len(cell.text)
            occurrence_index += 1
            occurrences.append(
                CellBlockOccurrence(
                    occurrence_id=occurrence_id(occurrence_index),
                    cell_id=cell.cell_id,
                    block_id=block_id,
                    physical_row_index=physical_row_index,
                    canonical_start=offset,
                    canonical_end=end,
                    occurrence_role=(
                        "original"
                        if physical_row_index == cell.row_index
                        else "row_span_projection"
                    ),
                )
            )
            offset = end
    block_text = "".join(block_text_parts)
    return EvidenceIndex(
        document_id="doc_001",
        document_name=f"sample.{source_format}",
        source_format=source_format,
        source_sha256="1" * 64,
        parser_identity=ParserIdentity(
            parser_name=parser_name,
            parser_version="2",
            source_format=source_format,
        ),
        evidence_fingerprint="0" * 64,
        blocks=[
            BlockEvidenceRecord(
                block_id=block_id,
                text_length=len(block_text),
                text_sha256=sha256_text(block_text),
                table_id=table_identifier,
                table_row_start=1,
                table_row_end=row_count,
                cell_ids=[cell.cell_id for cell in cells],
                expected_capabilities=["text_range", "table_cell"],
                available_capabilities=["text_range", "table_cell"],
            )
        ],
        tables=[
            TableRecord(
                table_id=table_identifier,
                block_ids=[block_id],
                row_count=row_count,
                column_count=column_count,
                cell_ids=[cell.cell_id for cell in cells],
                occurrence_ids=[item.occurrence_id for item in occurrences],
                parser_method=parser_method,
                topology_status=topology_status,
            )
        ],
        cells=cells,
        cell_occurrences=occurrences,
    )


def test_evidence_index_rejects_cell_span_outside_table_grid():
    table_identifier = "tbl_00000001"
    with pytest.raises(ValidationError, match="span exceeds table bounds"):
        _topology_index(
            table_identifier=table_identifier,
            column_count=3,
            cells=[
                TableCellRecord(
                    cell_id="cell_00000001_r0001_c0003",
                    table_id=table_identifier,
                    row_index=1,
                    column_index=3,
                    column_span=2,
                    text="x",
                    text_sha256=sha256_text("x"),
                )
            ],
        )


def test_evidence_index_rejects_overlapping_logical_cells():
    table_identifier = "tbl_00000001"
    with pytest.raises(ValidationError, match="overlap at row 1 column 2"):
        _topology_index(
            table_identifier=table_identifier,
            cells=[
                TableCellRecord(
                    cell_id="cell_00000001_r0001_c0001",
                    table_id=table_identifier,
                    row_index=1,
                    column_index=1,
                    column_span=2,
                    text="a",
                    text_sha256=sha256_text("a"),
                ),
                TableCellRecord(
                    cell_id="cell_00000001_r0001_c0002",
                    table_id=table_identifier,
                    row_index=1,
                    column_index=2,
                    text="b",
                    text_sha256=sha256_text("b"),
                ),
            ],
        )


def test_sparse_table_row_with_unknown_gaps_cannot_expose_table_cell():
    table_identifier = "tbl_00000001"
    with pytest.raises(
        ValidationError,
        match="unknown column gaps cannot expose table_cell capability",
    ):
        _topology_index(
            table_identifier=table_identifier,
            column_count=5,
            topology_status="sparse",
            parser_method="pymupdf_find_tables",
            cells=[
                TableCellRecord(
                    cell_id="cell_00000001_r0001_c0001",
                    table_id=table_identifier,
                    row_index=1,
                    column_index=1,
                    text="A",
                    text_sha256=sha256_text("A"),
                ),
                TableCellRecord(
                    cell_id="cell_00000001_r0001_c0005",
                    table_id=table_identifier,
                    row_index=1,
                    column_index=5,
                    text="B",
                    text_sha256=sha256_text("B"),
                ),
            ],
        )


def test_parser_identity_source_format_must_agree_but_custom_names_are_allowed():
    cell = TableCellRecord(
        cell_id="cell_00000001_r0001_c0001",
        table_id="tbl_00000001",
        row_index=1,
        column_index=1,
        text="A",
        text_sha256=sha256_text("A"),
    )
    payload = _topology_index(
        cells=[cell],
        column_count=1,
    ).model_dump(mode="json")
    payload["parser_identity"]["source_format"] = "pdf"
    with pytest.raises(ValidationError, match="identity source_format does not match"):
        EvidenceIndex.model_validate(payload)

    payload = _topology_index(
        cells=[cell],
        column_count=1,
        topology_status="sparse",
        parser_method="pymupdf_find_tables",
    ).model_dump(mode="json")
    payload["parser_identity"]["parser_name"] = "custom_pdf_parser_v1"
    assert (
        EvidenceIndex.model_validate(payload).parser_identity.parser_name
        == "custom_pdf_parser_v1"
    )


@pytest.mark.parametrize(
    ("table_identifier", "cell_identifier", "message"),
    [
        ("table_1", "cell_00000001_r0001_c0001", "table ID is not canonical"),
        (
            "tbl_00000000",
            "cell_00000000_r0001_c0001",
            "table ID index must be positive",
        ),
        (
            "tbl_00000001",
            "cell_00000001_r0002_c0001",
            "cell ID does not match table, row, and column fields",
        ),
    ],
)
def test_evidence_index_rejects_noncanonical_or_inconsistent_stable_ids(
    table_identifier: str,
    cell_identifier: str,
    message: str,
):
    with pytest.raises(ValidationError, match=message):
        _topology_index(
            table_identifier=table_identifier,
            cells=[
                TableCellRecord(
                    cell_id=cell_identifier,
                    table_id=table_identifier,
                    row_index=1,
                    column_index=1,
                    text="x",
                    text_sha256=sha256_text("x"),
                )
            ],
        )


def test_complete_topology_requires_full_grid_but_sparse_topology_allows_holes():
    table_identifier = "tbl_00000001"
    cell = TableCellRecord(
        cell_id="cell_00000001_r0001_c0001",
        table_id=table_identifier,
        row_index=1,
        column_index=1,
        text="x",
        text_sha256=sha256_text("x"),
    )

    with pytest.raises(ValidationError, match="uncovered cells"):
        _topology_index(cells=[cell], column_count=3)

    sparse = _topology_index(
        cells=[cell],
        column_count=3,
        topology_status="sparse",
        parser_method="pymupdf_find_tables",
    )
    assert sparse.tables[0].topology_status == "sparse"

    with pytest.raises(ValidationError, match="DOCX tables must declare complete"):
        _topology_index(
            cells=[cell],
            column_count=3,
            topology_status="sparse",
        )


def test_previous_evidence_artifacts_are_rejected_explicitly():
    with pytest.raises(ValidationError, match="EVIDENCE_V4_REBUILD_REQUIRED"):
        EvidenceIndex(
            schema_version="evidence_v4",  # type: ignore[arg-type]
            document_id="doc_001",
            document_name="sample.docx",
            source_format="docx",
            source_sha256="1" * 64,
            parser_identity=ParserIdentity(
                parser_name="docx_parser_v2",
                parser_version="2",
                source_format="docx",
            ),
            evidence_fingerprint="0" * 64,
        )


def test_structural_occurrences_from_different_rows_must_not_overlap():
    table_identifier = "tbl_00000001"
    cell = TableCellRecord(
        cell_id="cell_00000001_r0001_c0001",
        table_id=table_identifier,
        row_index=1,
        column_index=1,
        row_span=2,
        text="A",
        text_sha256=sha256_text("A"),
    )
    index = _topology_index(
        table_identifier=table_identifier,
        row_count=2,
        column_count=1,
        cells=[cell],
    )
    assert [
        (item.canonical_start, item.canonical_end)
        for item in index.cell_occurrences
    ] == [(0, 1), (1, 2)]

    payload = index.model_dump(mode="json")
    payload["cell_occurrences"][1]["canonical_start"] = 0
    payload["cell_occurrences"][1]["canonical_end"] = 1
    with pytest.raises(ValidationError, match="occurrence ranges must not overlap"):
        EvidenceIndex.model_validate(payload)


def test_non_empty_occurrence_ranges_must_be_unique_for_all_roles():
    table_identifier = "tbl_00000001"
    cells = [
        TableCellRecord(
            cell_id=f"cell_00000001_r0001_c{column:04d}",
            table_id=table_identifier,
            row_index=1,
            column_index=column,
            text="A",
            text_sha256=sha256_text("A"),
        )
        for column in (1, 2)
    ]
    index = _topology_index(
        table_identifier=table_identifier,
        row_count=1,
        column_count=2,
        cells=cells,
    )
    payload = index.model_dump(mode="json")
    payload["cell_occurrences"][1]["canonical_start"] = 0
    payload["cell_occurrences"][1]["canonical_end"] = 1
    with pytest.raises(ValidationError, match="occurrence ranges must not overlap"):
        EvidenceIndex.model_validate(payload)

    payload = index.model_dump(mode="json")
    duplicate = {
        **payload["cell_occurrences"][0],
        "occurrence_id": "occ_00000003",
        "occurrence_role": "duplicate_text_occurrence",
    }
    payload["cell_occurrences"].append(duplicate)
    payload["tables"][0]["occurrence_ids"].append("occ_00000003")
    with pytest.raises(ValidationError, match="occurrence ranges must not overlap"):
        EvidenceIndex.model_validate(payload)


def test_rendered_table_row_ranges_must_not_interleave():
    table_identifier = "tbl_00000001"
    cells = [
        TableCellRecord(
            cell_id=f"cell_00000001_r0001_c{column:04d}",
            table_id=table_identifier,
            row_index=1,
            column_index=column,
            row_span=2,
            text=text,
            text_sha256=sha256_text(text),
        )
        for column, text in ((1, "A"), (2, "C"))
    ]
    index = _topology_index(
        table_identifier=table_identifier,
        row_count=2,
        column_count=2,
        cells=cells,
    )
    payload = index.model_dump(mode="json")
    by_id = {
        item["occurrence_id"]: item for item in payload["cell_occurrences"]
    }
    by_id["occ_00000002"]["canonical_start"] = 2
    by_id["occ_00000002"]["canonical_end"] = 3
    by_id["occ_00000003"]["canonical_start"] = 1
    by_id["occ_00000003"]["canonical_end"] = 2
    with pytest.raises(ValidationError, match="must not interleave"):
        EvidenceIndex.model_validate(payload)


@pytest.mark.parametrize(
    ("source_format", "parser_method"),
    [
        ("docx", "pymupdf_find_tables"),
        ("pdf", "docx_xml"),
    ],
)
def test_table_parser_method_must_match_source_format(
    source_format: str,
    parser_method: str,
):
    cell = TableCellRecord(
        cell_id="cell_00000001_r0001_c0001",
        table_id="tbl_00000001",
        row_index=1,
        column_index=1,
        text="x",
        text_sha256=sha256_text("x"),
    )
    base_parser = (
        "pymupdf_find_tables" if source_format == "docx" else "docx_xml"
    )
    payload = _topology_index(
        cells=[cell],
        column_count=1,
        parser_method=base_parser,
        topology_status="sparse" if base_parser == "pymupdf_find_tables" else "complete",
    ).model_dump(mode="json")
    payload["source_format"] = source_format
    payload["tables"][0]["parser_method"] = parser_method

    with pytest.raises(ValidationError, match="does not match evidence source_format"):
        EvidenceIndex.model_validate(payload)


def test_table_cell_must_be_referenced_by_a_table_block():
    table_identifier = "tbl_00000001"
    cells = [
        TableCellRecord(
            cell_id=f"cell_00000001_r0001_c{column:04d}",
            table_id=table_identifier,
            row_index=1,
            column_index=column,
            text=text,
            text_sha256=sha256_text(text),
        )
        for column, text in enumerate(["A", "B"], start=1)
    ]
    payload = _topology_index(cells=cells, column_count=2).model_dump(mode="json")
    payload["blocks"][0]["cell_ids"] = [cells[0].cell_id]
    payload["tables"][0]["occurrence_ids"] = [occurrence_id(1)]
    payload["cell_occurrences"] = [payload["cell_occurrences"][0]]

    with pytest.raises(ValidationError, match="not referenced by a table block"):
        EvidenceIndex.model_validate(payload)


def test_table_cell_hash_must_match_cell_text():
    with pytest.raises(ValidationError, match="text_sha256 does not match"):
        TableCellRecord(
            cell_id="cell_00000001_r0001_c0001",
            table_id="tbl_00000001",
            row_index=1,
            column_index=1,
            text="A",
            text_sha256=sha256_text("B"),
        )


def test_evidence_index_rejects_table_without_blocks():
    with pytest.raises(ValidationError, match="at least one block"):
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.docx",
            source_format="docx",
            source_sha256="1" * 64,
            parser_identity=ParserIdentity(
                parser_name="docx_parser_v2",
                parser_version="2",
                source_format="docx",
            ),
            evidence_fingerprint="0" * 64,
            tables=[
                TableRecord(
                    table_id="tbl_00000001",
                    block_ids=[],
                    row_count=1,
                    column_count=1,
                    cell_ids=[],
                    occurrence_ids=[],
                    parser_method="docx_xml",
                    topology_status="complete",
                )
            ],
        )


def test_evidence_index_rejects_table_without_cells():
    with pytest.raises(ValidationError, match="at least one cell"):
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.docx",
            source_format="docx",
            source_sha256="1" * 64,
            parser_identity=ParserIdentity(
                parser_name="docx_parser_v2",
                parser_version="2",
                source_format="docx",
            ),
            evidence_fingerprint="0" * 64,
            blocks=[
                BlockEvidenceRecord(
                    block_id="blk_0001",
                    text_length=0,
                    text_sha256=sha256_text(""),
                    table_id="tbl_00000001",
                    table_row_start=1,
                    table_row_end=1,
                )
            ],
            tables=[
                TableRecord(
                    table_id="tbl_00000001",
                    block_ids=["blk_0001"],
                    row_count=1,
                    column_count=1,
                    cell_ids=[],
                    occurrence_ids=[],
                    parser_method="docx_xml",
                    topology_status="complete",
                )
            ],
        )


def test_locator_status_distinguishes_derived_and_structured_sources():
    text_pass = CapabilityValidationResult(capability="text_range", status="PASS")
    page_pass = CapabilityValidationResult(capability="page_region", status="PASS")
    assert aggregate_locator_status(["text_range"], [text_pass]) == "PASS_DERIVED"
    assert aggregate_locator_status(
        ["text_range", "page_region"], [text_pass, page_pass]
    ) == "PASS_STRUCTURED"
    assert aggregate_locator_status(
        ["text_range", "page_region"],
        [
            text_pass,
            CapabilityValidationResult(
                capability="page_region", status="WARNING_AMBIGUOUS"
            ),
        ],
    ) == "WARNING_AMBIGUOUS"


def test_locator_status_rejects_unexpected_results_and_empty_expected_set():
    text_pass = CapabilityValidationResult(capability="text_range", status="PASS")
    assert aggregate_locator_status([], []) == "UNVERIFIED"
    with pytest.raises(ValueError, match="not expected"):
        aggregate_locator_status([], [text_pass])


def test_evidence_index_supports_repeated_header_occurrences():
    table_identifier = table_id(1)
    header_cell = cell_id(1, 1, 1)
    data_cell = cell_id(1, 2, 1)
    blocks = [
        BlockEvidenceRecord(
            block_id="blk_0001",
            text_length=6,
            text_sha256=sha256_text("Header"),
            table_id=table_identifier,
            table_row_start=1,
            table_row_end=1,
            cell_ids=[header_cell],
            expected_capabilities=["text_range", "table_cell"],
            available_capabilities=["text_range", "table_cell"],
        ),
        BlockEvidenceRecord(
            block_id="blk_0002",
            text_length=10,
            text_sha256=sha256_text("HeaderData"),
            table_id=table_identifier,
            table_row_start=2,
            table_row_end=2,
            cell_ids=[header_cell, data_cell],
            expected_capabilities=["text_range", "table_cell"],
            available_capabilities=["text_range", "table_cell"],
        ),
    ]
    occurrences = [
        CellBlockOccurrence(
            occurrence_id=occurrence_id(1),
            cell_id=header_cell,
            block_id="blk_0001",
            physical_row_index=1,
            canonical_start=0,
            canonical_end=6,
        ),
        CellBlockOccurrence(
            occurrence_id=occurrence_id(2),
            cell_id=header_cell,
            block_id="blk_0002",
            physical_row_index=1,
            canonical_start=0,
            canonical_end=6,
            occurrence_role="repeated_header",
        ),
        CellBlockOccurrence(
            occurrence_id=occurrence_id(3),
            cell_id=data_cell,
            block_id="blk_0002",
            physical_row_index=2,
            canonical_start=6,
            canonical_end=10,
        ),
    ]
    index = EvidenceIndex(
        document_id="doc_001",
        document_name="sample.docx",
        source_format="docx",
        source_sha256="1" * 64,
        parser_identity=ParserIdentity(parser_name="docx_parser_v2", parser_version="2", source_format="docx"),
        evidence_fingerprint="0" * 64,
        blocks=blocks,
        tables=[
            TableRecord(
                table_id=table_identifier,
                block_ids=["blk_0001", "blk_0002"],
                row_count=2,
                column_count=1,
                cell_ids=[header_cell, data_cell],
                occurrence_ids=[item.occurrence_id for item in occurrences],
                parser_method="docx_xml",
                topology_status="complete",
            )
        ],
        cells=[
            TableCellRecord(
                cell_id=header_cell,
                table_id=table_identifier,
                row_index=1,
                column_index=1,
                text="Header",
                text_sha256=sha256_text("Header"),
                is_header=True,
            ),
            TableCellRecord(
                cell_id=data_cell,
                table_id=table_identifier,
                row_index=2,
                column_index=1,
                text="Data",
                text_sha256=sha256_text("Data"),
            ),
        ],
        cell_occurrences=occurrences,
    )
    assert len(index.cell_occurrences) == 3
    assert index.cell_occurrences[1].occurrence_role == "repeated_header"

    duplicate_header = index.model_dump(mode="json")
    duplicate_occurrence = {
        **duplicate_header["cell_occurrences"][1],
        "occurrence_id": occurrence_id(4),
    }
    duplicate_header["cell_occurrences"].append(duplicate_occurrence)
    duplicate_header["tables"][0]["occurrence_ids"].append(occurrence_id(4))
    with pytest.raises(ValidationError, match="at most one repeated header"):
        EvidenceIndex.model_validate(duplicate_header)

    overlapping_projection = index.model_dump(mode="json")
    overlapping_projection["cell_occurrences"][2]["canonical_start"] = 0
    overlapping_projection["cell_occurrences"][2]["canonical_end"] = 4
    with pytest.raises(ValidationError, match="occurrence ranges must not overlap"):
        EvidenceIndex.model_validate(overlapping_projection)

    same_block = index.model_dump(mode="json")
    same_block_occurrence = {
        **same_block["cell_occurrences"][1],
        "occurrence_id": occurrence_id(4),
        "block_id": "blk_0001",
    }
    same_block["cell_occurrences"].append(same_block_occurrence)
    same_block["tables"][0]["occurrence_ids"].append(occurrence_id(4))
    with pytest.raises(ValidationError, match="projected header anchor row"):
        EvidenceIndex.model_validate(same_block)

    out_of_order = index.model_dump(mode="json")
    out_of_order["tables"][0]["block_ids"] = ["blk_0002", "blk_0001"]
    with pytest.raises(ValidationError, match="ordered, contiguous, and complete"):
        EvidenceIndex.model_validate(out_of_order)


def test_occurrence_roles_must_match_cell_and_physical_row():
    table_identifier = "tbl_00000001"
    cell = TableCellRecord(
        cell_id="cell_00000001_r0001_c0001",
        table_id=table_identifier,
        row_index=1,
        column_index=1,
        row_span=2,
        text="A",
        text_sha256=sha256_text("A"),
    )
    payload = _topology_index(
        table_identifier=table_identifier,
        row_count=2,
        column_count=1,
        cells=[cell],
    ).model_dump(mode="json")
    payload["cell_occurrences"][0]["physical_row_index"] = 2
    with pytest.raises(ValidationError, match="structural occurrence"):
        EvidenceIndex.model_validate(payload)

    payload = _topology_index(
        table_identifier=table_identifier,
        row_count=2,
        column_count=1,
        cells=[cell],
    ).model_dump(mode="json")
    payload["cell_occurrences"][0]["occurrence_role"] = "row_span_projection"
    with pytest.raises(ValidationError, match="structural occurrence"):
        EvidenceIndex.model_validate(payload)

    payload = _topology_index(
        table_identifier=table_identifier,
        row_count=2,
        column_count=1,
        cells=[cell],
    ).model_dump(mode="json")
    payload["cell_occurrences"][0]["occurrence_role"] = "repeated_header"
    with pytest.raises(ValidationError, match="structural occurrence"):
        EvidenceIndex.model_validate(payload)


def test_evidence_index_rejects_dangling_occurrence():
    with pytest.raises(ValidationError, match="unknown cell"):
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.docx",
            source_format="docx",
            source_sha256="1" * 64,
            parser_identity=ParserIdentity(parser_name="docx_parser_v2", parser_version="2", source_format="docx"),
            evidence_fingerprint="0" * 64,
            blocks=[
                BlockEvidenceRecord(
                    block_id="blk_0001",
                    text_length=1,
                    text_sha256=sha256_text("x"),
                )
            ],
            cell_occurrences=[
                CellBlockOccurrence(
                    occurrence_id=occurrence_id(1),
                    cell_id="missing",
                    block_id="blk_0001",
                    physical_row_index=1,
                    canonical_start=0,
                    canonical_end=1,
                )
            ],
        )


def test_evidence_index_rejects_page_with_foreign_block():
    with pytest.raises(ValidationError, match="block from another page"):
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.pdf",
            source_format="pdf",
            source_sha256="1" * 64,
            parser_identity=ParserIdentity(parser_name="pdf_parser_v2", parser_version="2", source_format="pdf"),
            evidence_fingerprint="0" * 64,
            pages=[
                PageRecord(
                    page_id="page_0001",
                    page=1,
                    width=100,
                    height=100,
                    source_rotation=0,
                    block_ids=["blk_0001"],
                    table_ids=[],
                )
            ],
            blocks=[
                BlockEvidenceRecord(
                    block_id="blk_0001",
                    text_length=1,
                    text_sha256=sha256_text("x"),
                    page=2,
                )
            ],
        )


def test_evidence_index_rejects_block_cells_without_table():
    with pytest.raises(ValidationError, match="cell IDs require table_id"):
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.docx",
            source_format="docx",
            source_sha256="1" * 64,
            parser_identity=ParserIdentity(parser_name="docx_parser_v2", parser_version="2", source_format="docx"),
            evidence_fingerprint="0" * 64,
            blocks=[
                BlockEvidenceRecord(
                    block_id="blk_0001",
                    text_length=1,
                    text_sha256=sha256_text("x"),
                    cell_ids=["cell_1"],
                )
            ],
            cells=[
                TableCellRecord(
                    cell_id="cell_1",
                    table_id="table_1",
                    row_index=1,
                    column_index=1,
                    text="x",
                    text_sha256=sha256_text("x"),
                )
            ],
            tables=[
                TableRecord(
                    table_id="table_1",
                    block_ids=[],
                    row_count=1,
                    column_count=1,
                    cell_ids=["cell_1"],
                    occurrence_ids=[],
                    parser_method="docx_xml",
                    topology_status="complete",
                )
            ],
        )


def test_evidence_index_rejects_table_with_foreign_cell():
    with pytest.raises(ValidationError, match="cell owned by another table"):
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.docx",
            source_format="docx",
            source_sha256="1" * 64,
            parser_identity=ParserIdentity(parser_name="docx_parser_v2", parser_version="2", source_format="docx"),
            evidence_fingerprint="0" * 64,
            tables=[
                TableRecord(
                    table_id="table_1",
                    block_ids=[],
                    row_count=1,
                    column_count=1,
                    cell_ids=["cell_2"],
                    occurrence_ids=[],
                    parser_method="docx_xml",
                    topology_status="complete",
                ),
                TableRecord(
                    table_id="table_2",
                    block_ids=[],
                    row_count=1,
                    column_count=1,
                    cell_ids=[],
                    occurrence_ids=[],
                    parser_method="docx_xml",
                    topology_status="complete",
                ),
            ],
            cells=[
                TableCellRecord(
                    cell_id="cell_2",
                    table_id="table_2",
                    row_index=1,
                    column_index=1,
                    text="x",
                    text_sha256=sha256_text("x"),
                )
            ],
        )


def test_evidence_index_rejects_occurrence_cell_not_registered_by_block():
    with pytest.raises(ValidationError, match="not registered by its block"):
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.docx",
            source_format="docx",
            source_sha256="1" * 64,
            parser_identity=ParserIdentity(parser_name="docx_parser_v2", parser_version="2", source_format="docx"),
            evidence_fingerprint="0" * 64,
            blocks=[
                BlockEvidenceRecord(
                    block_id="blk_0001",
                    text_length=1,
                    text_sha256=sha256_text("x"),
                    table_id="table_1",
                    cell_ids=["cell_1"],
                )
            ],
            tables=[
                TableRecord(
                    table_id="table_1",
                    block_ids=["blk_0001"],
                    row_count=1,
                    column_count=2,
                    cell_ids=["cell_1", "cell_2"],
                    occurrence_ids=["occ_1"],
                    parser_method="docx_xml",
                    topology_status="complete",
                )
            ],
            cells=[
                TableCellRecord(
                    cell_id=cell,
                    table_id="table_1",
                    row_index=1,
                    column_index=index,
                    text="x",
                    text_sha256=sha256_text("x"),
                )
                for index, cell in enumerate(["cell_1", "cell_2"], start=1)
            ],
            cell_occurrences=[
                CellBlockOccurrence(
                    occurrence_id="occ_1",
                    cell_id="cell_2",
                    block_id="blk_0001",
                    physical_row_index=1,
                    canonical_start=0,
                    canonical_end=1,
                )
            ],
        )


def test_evidence_index_rejects_block_cell_without_occurrence():
    with pytest.raises(ValidationError, match="has no occurrence"):
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.docx",
            source_format="docx",
            source_sha256="1" * 64,
            parser_identity=ParserIdentity(parser_name="docx_parser_v2", parser_version="2", source_format="docx"),
            evidence_fingerprint="0" * 64,
            blocks=[
                BlockEvidenceRecord(
                    block_id="blk_0001",
                    text_length=1,
                    text_sha256=sha256_text("x"),
                    table_id="table_1",
                    cell_ids=["cell_1"],
                )
            ],
            tables=[
                TableRecord(
                    table_id="table_1",
                    block_ids=["blk_0001"],
                    row_count=1,
                    column_count=1,
                    cell_ids=["cell_1"],
                    occurrence_ids=[],
                    parser_method="docx_xml",
                    topology_status="complete",
                )
            ],
            cells=[
                TableCellRecord(
                    cell_id="cell_1",
                    table_id="table_1",
                    row_index=1,
                    column_index=1,
                    text="x",
                    text_sha256=sha256_text("x"),
                )
            ],
        )
