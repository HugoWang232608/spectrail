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
