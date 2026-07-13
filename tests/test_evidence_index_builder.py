from dataclasses import replace
from pathlib import Path

import pytest

from spectrail.core.models import DocumentBlock
from spectrail.evidence.fingerprint import finalize_evidence_fingerprint
from spectrail.evidence.index_builder import ensure_evidence_index
from spectrail.parsers.base import ParsedDocument


def _parsed(path: Path) -> ParsedDocument:
    return ParsedDocument(
        document_id="doc_001",
        document_name=path.name,
        source_format="markdown",
        parser_name="markdown_parser_v1",
        text="hello",
        blocks=[
            DocumentBlock(
                block_id="blk_0001",
                document_id="doc_001",
                type="paragraph",
                text="hello",
                order=1,
            )
        ],
    )


def test_parser_evidence_index_must_match_parsed_block_content(tmp_path: Path):
    document = tmp_path / "sample.md"
    document.write_text("hello", encoding="utf-8")
    parsed = _parsed(document)
    index = ensure_evidence_index(document, parsed)
    stale_block = index.blocks[0].model_copy(
        update={"text_length": 5, "text_sha256": "f" * 64}
    )
    stale_index = finalize_evidence_fingerprint(
        index.model_copy(update={"blocks": [stale_block]})
    )

    with pytest.raises(ValueError, match="text_sha256"):
        ensure_evidence_index(
            document,
            replace(parsed, evidence_index=stale_index),
        )


@pytest.mark.parametrize("field_name", ["document_id", "document_name", "source_format"])
def test_parser_evidence_index_identity_must_match_parsed_document(
    tmp_path: Path,
    field_name: str,
):
    document = tmp_path / "sample.md"
    document.write_text("hello", encoding="utf-8")
    parsed = _parsed(document)
    index = ensure_evidence_index(document, parsed)
    mismatched = index.model_copy(update={field_name: "different"})

    with pytest.raises(ValueError, match=field_name):
        ensure_evidence_index(
            document,
            replace(parsed, evidence_index=mismatched),
        )


def test_parser_evidence_index_rejects_stale_fingerprint(tmp_path: Path):
    document = tmp_path / "sample.md"
    document.write_text("hello", encoding="utf-8")
    parsed = _parsed(document)
    index = ensure_evidence_index(document, parsed)
    stale = index.model_copy(update={"evidence_fingerprint": "f" * 64})

    with pytest.raises(ValueError, match="fingerprint"):
        ensure_evidence_index(document, replace(parsed, evidence_index=stale))


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"text_length": 4}, "text_length"),
        ({"page": 1}, "page"),
    ],
)
def test_parser_evidence_block_shape_must_match_parsed_block(
    tmp_path: Path,
    update: dict,
    message: str,
):
    document = tmp_path / "sample.md"
    document.write_text("hello", encoding="utf-8")
    parsed = _parsed(document)
    index = ensure_evidence_index(document, parsed)
    mismatched = index.model_copy(
        update={"blocks": [index.blocks[0].model_copy(update=update)]}
    )

    with pytest.raises(ValueError, match=message):
        ensure_evidence_index(
            document,
            replace(parsed, evidence_index=mismatched),
        )


def test_parser_evidence_block_set_must_match_parsed_blocks(tmp_path: Path):
    document = tmp_path / "sample.md"
    document.write_text("hello", encoding="utf-8")
    parsed = _parsed(document)
    index = ensure_evidence_index(document, parsed)
    missing_block = index.model_copy(update={"blocks": []})

    with pytest.raises(ValueError, match="block order"):
        ensure_evidence_index(
            document,
            replace(parsed, evidence_index=missing_block),
        )
