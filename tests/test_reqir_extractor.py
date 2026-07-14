from __future__ import annotations

from spectrail.core.models import DocumentBlock
from spectrail.extractors.reqir_extractor import ReqIRExtractor


def test_extractor_enriches_source_with_block_page_and_copied_section_path():
    section_path = ["System Requirements", "Access Control"]
    blocks = [
        DocumentBlock(
            block_id="blk_0001",
            document_id="doc_001",
            type="paragraph",
            text="The system shall show source quotes.",
            page=3,
            section_path=section_path,
            order=1,
        )
    ]
    payload = {
        "items": [
            {
                "statement": "The system shall show source quotes.",
                "source_block_id": "blk_0001",
                "source_quote": "The system shall show source quotes.",
            }
        ]
    }

    requirement = ReqIRExtractor().extract(payload, blocks, document_name="sample.pdf")[0]
    source = requirement.sources[0]

    assert source.page == 3
    assert source.section_path == section_path
    assert source.section_path is not section_path


def test_extractor_normalizes_live_enum_drift():
    blocks = [
        DocumentBlock(
            block_id="blk_0001",
            document_id="doc_001",
            type="paragraph",
            text="The system shall reject unsafe access.",
            order=1,
        )
    ]
    payload = {
        "items": [
            {
                "statement": "The system shall reject unsafe access.",
                "type": "data",
                "ears_pattern": "unwanted",
                "priority": "urgent",
                "verification_method": "review",
                "source_block_id": "blk_0001",
                "source_quote": "- The system shall reject unsafe access.",
                "tags": "security",
                "confidence": "high",
            }
        ]
    }

    requirement = ReqIRExtractor().extract(payload, blocks, document_name="sample.md", model_mode="live")[0]

    assert requirement.type == "unknown"
    assert requirement.ears_pattern == "unwanted_behavior"
    assert requirement.priority == "unknown"
    assert requirement.verification_method == "unknown"
    assert requirement.review_status == "needs_recheck"
    assert requirement.confidence == 0.9
    assert requirement.sources[0].quote == "The system shall reject unsafe access."
    assert requirement.metadata["extractor_version"] == (
        "reqir_extractor_v4_table_row_evidence"
    )
    assert requirement.tags == ["security"]
    assert requirement.metadata["field_normalizations"] == [
        {"field": "confidence", "input": "high", "normalized": "0.9"},
        {"field": "type", "input": "data", "normalized": "unknown"},
        {"field": "ears_pattern", "input": "unwanted", "normalized": "unwanted_behavior"},
        {"field": "priority", "input": "urgent", "normalized": "unknown"},
        {"field": "verification_method", "input": "review", "normalized": "unknown"},
        {
            "field": "source_quote",
            "input": "- The system shall reject unsafe access.",
            "normalized": "The system shall reject unsafe access.",
        },
    ]


def test_extractor_marks_unknown_confidence_for_recheck():
    blocks = [
        DocumentBlock(
            block_id="blk_0001",
            document_id="doc_001",
            type="paragraph",
            text="The system shall show source quotes.",
            order=1,
        )
    ]
    payload = {
        "items": [
            {
                "statement": "The system shall show source quotes.",
                "source_block_id": "blk_0001",
                "source_quote": "| Label | The system shall show source quotes. |",
                "confidence": "certain",
            }
        ]
    }

    requirement = ReqIRExtractor().extract(payload, blocks, document_name="sample.md", model_mode="live")[0]

    assert requirement.confidence == 0.0
    assert requirement.review_status == "needs_recheck"
    assert requirement.sources[0].quote == "The system shall show source quotes."
    assert {
        "field": "source_quote",
        "input": "| Label | The system shall show source quotes. |",
        "normalized": "The system shall show source quotes.",
    } in requirement.metadata["field_normalizations"]


def test_extractor_preserves_raw_table_cell_ids_without_canonicalizing():
    blocks = [
        DocumentBlock(
            block_id="blk_0001",
            document_id="doc_001",
            type="table",
            text="| A | B |",
            order=1,
        )
    ]
    cell_1 = "cell_00000001_r0001_c0001"
    cell_2 = "cell_00000001_r0001_c0002"
    payload = {
        "items": [
            {
                "statement": "A maps to B.",
                "source_block_id": "blk_0001",
                "source_quote": "| A | B |",
                "source_cell_ids": [cell_2, cell_1],
                "source_table_row_index": 1,
            }
        ]
    }

    source = ReqIRExtractor().extract(payload, blocks, document_name="sample.docx")[0].sources[0]
    assert source.source_cell_ids_raw == [cell_2, cell_1]
    assert source.canonical_source_cell_ids == []
    assert source.source_table_row_index == 1
    assert source.quote == "| A | B |"
