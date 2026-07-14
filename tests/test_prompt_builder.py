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

    assert "confidence must be a number from 0.0 to 1.0" in prompt
    assert "not textual labels such as high/medium/low" in prompt


def test_reqir_v3_prompt_renders_table_cell_map_without_changing_canonical_text():
    cell = "cell_00000001_r0001_c0001"
    table = "tbl_00000001"
    block = DocumentBlock(
        block_id="blk_0001",
        document_id="doc_001",
        type="table",
        text="audit.retention_days | >= 180",
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
                    text=block.text,
                    text_sha256=sha256_text(block.text),
                )
            ],
            cell_occurrences=[
                CellBlockOccurrence(
                    occurrence_id="occ_00000001",
                    cell_id=cell,
                    block_id=block.block_id,
                    physical_row_index=1,
                    canonical_start=0,
                    canonical_end=len(block.text),
                ),
                CellBlockOccurrence(
                    occurrence_id="occ_00000002",
                    cell_id=cell,
                    block_id=block.block_id,
                    physical_row_index=2,
                    canonical_start=0,
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
    assert f"table_id: {table}" in prompt
    assert "primary_rows: 1-2" in prompt
    assert "row 1:" in prompt
    assert "row 2:" in prompt
    assert f"canonical_text: {block.text}" in prompt
    assert f"c1={cell}" in prompt
    assert "anchor_row=1" in prompt
    assert "column_span=2" in prompt
    assert "row_span=2" in prompt
    assert f'text="{block.text}"' in prompt

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
    assert "source_cell_ids are optional under the quote_only evidence policy" in quote_only_prompt
    assert "Omit empty cells from source_cell_ids" in quote_only_prompt
    assert "must also include source_cell_ids" not in quote_only_prompt
