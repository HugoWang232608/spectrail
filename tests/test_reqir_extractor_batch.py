from spectrail.core.models import DocumentBlock
from spectrail.extractors.reqir_extractor import ReqIRExtractor


def test_extract_batch_isolates_malformed_middle_item():
    blocks = [
        DocumentBlock(
            block_id="blk_0001",
            document_id="doc_001",
            type="paragraph",
            text="The system shall start. The system shall stop.",
            order=1,
        )
    ]
    payload = {
        "items": [
            {
                "statement": "The system shall start.",
                "source_block_id": "blk_0001",
                "source_quote": "The system shall start.",
            },
            {"statement": "missing source"},
            {
                "statement": "The system shall stop.",
                "source_block_id": "blk_0001",
                "source_quote": "The system shall stop.",
            },
        ]
    }

    result = ReqIRExtractor().extract_batch(
        payload,
        blocks,
        "sample.md",
        chunk_id="chk_00000001",
        chunk_fingerprint="chunk-fingerprint",
        request_fingerprint="request-fingerprint",
    )

    assert [item.id for item in result.accepted_candidates] == [
        "CAND-chk_00000001-0001",
        "CAND-chk_00000001-0003",
    ]
    assert len(result.rejected_items) == 1
    assert result.rejected_items[0].item_index == 1
    assert result.rejected_items[0].error_code == "MODEL_ITEM_MISSING_FIELD"
    assert result.accepted_candidates[0].metadata["extractor_version"] == (
        "reqir_extractor_v4_table_row_evidence"
    )


def test_extract_batch_keeps_envelope_errors_at_chunk_level():
    try:
        ReqIRExtractor().extract_batch({}, [], "sample.md")
    except ValueError as exc:
        assert "items array" in str(exc)
    else:
        raise AssertionError("expected envelope error")


def test_extract_batch_rejects_context_only_source():
    blocks = [
        DocumentBlock(
            block_id="blk_0001",
            document_id="doc_001",
            type="heading",
            text="Security",
            order=1,
        )
    ]
    result = ReqIRExtractor().extract_batch(
        {
            "items": [
                {
                    "statement": "Security",
                    "source_block_id": "blk_0001",
                    "source_quote": "Security",
                }
            ]
        },
        blocks,
        "sample.md",
        context_block_ids={"blk_0001"},
    )
    assert result.accepted_candidates == []
    assert result.rejected_items[0].error_message == "item 1 cites a context-only block"


def test_extract_batch_emits_specific_cell_error_codes():
    table = DocumentBlock(
        block_id="blk_0001",
        document_id="doc_001",
        type="table",
        text="A",
        order=1,
    )
    paragraph = DocumentBlock(
        block_id="blk_0002",
        document_id="doc_001",
        type="paragraph",
        text="B",
        order=2,
    )
    context_table = DocumentBlock(
        block_id="blk_0003",
        document_id="doc_001",
        type="table",
        text="C",
        order=3,
    )
    cell = "cell_00000001_r0001_c0001"
    payload = {
        "items": [
            {
                "statement": "Invalid shape",
                "source_block_id": table.block_id,
                "source_quote": "A",
                "source_cell_ids": ["not-a-cell"],
                "source_table_row_index": 1,
            },
            {
                "statement": "Missing cells",
                "source_block_id": table.block_id,
                "source_quote": "A",
            },
            {
                "statement": "Missing row",
                "source_block_id": table.block_id,
                "source_quote": "A",
                "source_cell_ids": [cell],
            },
            {
                "statement": "Row without cells",
                "source_block_id": table.block_id,
                "source_quote": "A",
                "source_table_row_index": 1,
            },
            {
                "statement": "Invalid row",
                "source_block_id": table.block_id,
                "source_quote": "A",
                "source_cell_ids": [cell],
                "source_table_row_index": 0,
            },
            {
                "statement": "Wrong block type",
                "source_block_id": paragraph.block_id,
                "source_quote": "B",
                "source_cell_ids": [cell],
                "source_table_row_index": 1,
            },
            {
                "statement": "Context cell",
                "source_block_id": context_table.block_id,
                "source_quote": "C",
                "source_cell_ids": [cell],
                "source_table_row_index": 1,
            },
        ]
    }

    result = ReqIRExtractor().extract_batch(
        payload,
        [table, paragraph, context_table],
        "sample.docx",
        context_block_ids={context_table.block_id},
        table_cell_required_block_ids={table.block_id},
    )

    assert [item.error_code for item in result.rejected_items] == [
        "MODEL_ITEM_INVALID_CELL_IDS",
        "MODEL_ITEM_TABLE_SOURCE_MISSING_CELL_IDS",
        "MODEL_ITEM_TABLE_SOURCE_MISSING_ROW_INDEX",
        "MODEL_ITEM_TABLE_ROW_WITHOUT_CELL_IDS",
        "MODEL_ITEM_INVALID_TABLE_ROW_INDEX",
        "MODEL_ITEM_NON_TABLE_WITH_CELL_IDS",
        "MODEL_ITEM_CONTEXT_CELL_REFERENCE",
    ]
