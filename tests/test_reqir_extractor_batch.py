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
    assert result.accepted_candidates[0].metadata["extractor_version"] == "reqir_extractor_v2"


def test_extract_batch_keeps_envelope_errors_at_chunk_level():
    try:
        ReqIRExtractor().extract_batch({}, [], "sample.md")
    except ValueError as exc:
        assert "items array" in str(exc)
    else:
        raise AssertionError("expected envelope error")
