from spectrail.core.models import DocumentBlock
from spectrail.evidence import (
    BlockEvidenceRecord,
    CellBlockOccurrence,
    EvidenceIndex,
    ParserIdentity,
    TableCellRecord,
    TableRecord,
    finalize_evidence_fingerprint,
    sha256_text,
)
from spectrail.llm.base import ModelRequest
from spectrail.llm.prompt_builder import build_reqir_prompt


def test_reqir_prompt_requires_numeric_confidence():
    prompt = build_reqir_prompt(
        ModelRequest(
            document_text="The system shall log failed sign-in attempts.",
            blocks=[
                DocumentBlock(
                    block_id="blk_0001",
                    document_id="doc_1",
                    type="paragraph",
                    text="The system shall log failed sign-in attempts.",
                    order=1,
                )
            ],
            document_name="sample.md",
            source_format="markdown",
            parser_name="markdown_parser_v1",
            model_mode="live",
        )
    )

    assert "confidence is numeric 0.0..1.0" in prompt


def test_reqir_v4_prompt_renders_table_cell_map_without_changing_canonical_text():
    cell = "cell_00000001_r0001_c0001"
    table = "tbl_00000001"
    cell_text = "audit.retention_days | >= 180"
    block = DocumentBlock(
        block_id="blk_0001",
        document_id="doc_001",
        type="table",
        text=f"{cell_text}\n{cell_text}",
        order=1,
    )
    index = finalize_evidence_fingerprint(
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.docx",
            source_format="docx",
            source_sha256="a" * 64,
            parser_identity=ParserIdentity(
                parser_name="docx_parser_v2",
                parser_version="2",
                source_format="docx",
            ),
            evidence_fingerprint="0" * 64,
            blocks=[
                BlockEvidenceRecord(
                    block_id=block.block_id,
                    text_length=len(block.text),
                    text_sha256=sha256_text(block.text),
                    table_id=table,
                    table_row_start=1,
                    table_row_end=2,
                    cell_ids=[cell],
                    expected_capabilities=["text_range", "table_cell"],
                    available_capabilities=["text_range", "table_cell"],
                )
            ],
            tables=[
                TableRecord(
                    table_id=table,
                    block_ids=[block.block_id],
                    row_count=2,
                    column_count=2,
                    cell_ids=[cell],
                    occurrence_ids=["occ_00000001", "occ_00000002"],
                    parser_method="docx_xml",
                    topology_status="complete",
                )
            ],
            cells=[
                TableCellRecord(
                    cell_id=cell,
                    table_id=table,
                    row_index=1,
                    column_index=1,
                    row_span=2,
                    column_span=2,
                    text=cell_text,
                    text_sha256=sha256_text(cell_text),
                )
            ],
            cell_occurrences=[
                CellBlockOccurrence(
                    occurrence_id="occ_00000001",
                    cell_id=cell,
                    block_id=block.block_id,
                    physical_row_index=1,
                    canonical_start=0,
                    canonical_end=len(cell_text),
                ),
                CellBlockOccurrence(
                    occurrence_id="occ_00000002",
                    cell_id=cell,
                    block_id=block.block_id,
                    physical_row_index=2,
                    canonical_start=len(cell_text) + 1,
                    canonical_end=len(block.text),
                    occurrence_role="row_span_projection",
                ),
            ],
        )
    )

    prompt = build_reqir_prompt(
        ModelRequest(
            document_text=block.text,
            blocks=[block],
            document_name="sample.docx",
            source_format="docx",
            parser_name="docx_parser_v2",
            model_mode="mock",
            evidence_index=index,
        )
    )

    assert "source_cell_ids" in prompt
    assert "source_table_row_index" in prompt
    assert f"table_id: {table}" in prompt
    assert "primary_rows: 1-2" in prompt
    assert "row 1:" in prompt
    assert "row 2:" in prompt
    assert f"canonical_text: {block.text}" in prompt
    assert f"c1={cell}" in prompt
    assert "anchor_row=1" in prompt
    assert "column_span=2" in prompt
    assert "row_span=2" in prompt
    assert f'text="{cell_text}"' in prompt

    quote_only_prompt = build_reqir_prompt(
        ModelRequest(
            document_text=block.text,
            blocks=[block],
            document_name="sample.docx",
            source_format="docx",
            parser_name="docx_parser_v2",
            model_mode="mock",
            metadata={"evidence_policy": "quote_only"},
            evidence_index=index,
        )
    )
    assert "cell_map sources may omit source_cell_ids" in quote_only_prompt
    assert "source_table_row_index" in quote_only_prompt
    assert "omit empty cells" in quote_only_prompt
    assert "cell_map sources require source_cell_ids" not in quote_only_prompt


def test_repeated_header_cell_map_follows_canonical_occurrence_order():
    table = "tbl_00000001"
    header = "cell_00000001_r0001_c0001"
    data = "cell_00000001_r0002_c0001"
    header_block = DocumentBlock(
        block_id="blk_0001",
        document_id="doc_001",
        type="table",
        text="Header",
        order=1,
    )
    data_block = DocumentBlock(
        block_id="blk_0002",
        document_id="doc_001",
        type="table",
        text="HeaderData",
        order=2,
    )
    index = finalize_evidence_fingerprint(
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.docx",
            source_format="docx",
            source_sha256="a" * 64,
            parser_identity=ParserIdentity(
                parser_name="docx_parser_v2",
                parser_version="2",
                source_format="docx",
            ),
            evidence_fingerprint="0" * 64,
            blocks=[
                BlockEvidenceRecord(
                    block_id=header_block.block_id,
                    text_length=len(header_block.text),
                    text_sha256=sha256_text(header_block.text),
                    table_id=table,
                    table_row_start=1,
                    table_row_end=1,
                    cell_ids=[header],
                    expected_capabilities=["text_range", "table_cell"],
                    available_capabilities=["text_range", "table_cell"],
                ),
                BlockEvidenceRecord(
                    block_id=data_block.block_id,
                    text_length=len(data_block.text),
                    text_sha256=sha256_text(data_block.text),
                    table_id=table,
                    table_row_start=2,
                    table_row_end=2,
                    cell_ids=[header, data],
                    expected_capabilities=["text_range", "table_cell"],
                    available_capabilities=["text_range", "table_cell"],
                ),
            ],
            tables=[
                TableRecord(
                    table_id=table,
                    block_ids=[header_block.block_id, data_block.block_id],
                    row_count=2,
                    column_count=1,
                    cell_ids=[header, data],
                    occurrence_ids=[
                        "occ_00000001",
                        "occ_00000002",
                        "occ_00000003",
                    ],
                    parser_method="docx_xml",
                    topology_status="complete",
                )
            ],
            cells=[
                TableCellRecord(
                    cell_id=header,
                    table_id=table,
                    row_index=1,
                    column_index=1,
                    text="Header",
                    text_sha256=sha256_text("Header"),
                    is_header=True,
                ),
                TableCellRecord(
                    cell_id=data,
                    table_id=table,
                    row_index=2,
                    column_index=1,
                    text="Data",
                    text_sha256=sha256_text("Data"),
                ),
            ],
            cell_occurrences=[
                CellBlockOccurrence(
                    occurrence_id="occ_00000001",
                    cell_id=header,
                    block_id=header_block.block_id,
                    physical_row_index=1,
                    canonical_start=0,
                    canonical_end=6,
                ),
                CellBlockOccurrence(
                    occurrence_id="occ_00000002",
                    cell_id=header,
                    block_id=data_block.block_id,
                    physical_row_index=1,
                    canonical_start=0,
                    canonical_end=6,
                    occurrence_role="repeated_header",
                ),
                CellBlockOccurrence(
                    occurrence_id="occ_00000003",
                    cell_id=data,
                    block_id=data_block.block_id,
                    physical_row_index=2,
                    canonical_start=6,
                    canonical_end=10,
                ),
            ],
        )
    )

    prompt = build_reqir_prompt(
        ModelRequest(
            document_text=data_block.text,
            blocks=[data_block],
            document_name="sample.docx",
            source_format="docx",
            parser_name="docx_parser_v2",
            model_mode="mock",
            evidence_index=index,
        )
    )

    assert prompt.index("repeated_header_row 1:") < prompt.index("row 2:")
