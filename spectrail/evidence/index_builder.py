from __future__ import annotations

from pathlib import Path

from spectrail.evidence.fingerprint import (
    finalize_evidence_fingerprint,
    sha256_file,
    sha256_text,
    validate_evidence_fingerprint,
)
from spectrail.evidence.models import BlockEvidenceRecord, EvidenceIndex, ParserIdentity
from spectrail.parsers.base import ParsedDocument


def validate_evidence_index_against_parsed_document(
    index: EvidenceIndex,
    parsed_document: ParsedDocument,
) -> None:
    for field_name in ("document_id", "document_name", "source_format"):
        if getattr(index, field_name) != getattr(parsed_document, field_name):
            raise ValueError(
                f"evidence index {field_name} does not match parsed document"
            )

    parsed_block_ids = [block.block_id for block in parsed_document.blocks]
    index_block_ids = [block.block_id for block in index.blocks]
    if index_block_ids != parsed_block_ids:
        raise ValueError(
            "evidence index block order does not match parsed document blocks"
        )

    evidence_blocks = {block.block_id: block for block in index.blocks}
    parsed_blocks = {block.block_id: block for block in parsed_document.blocks}
    for block in parsed_document.blocks:
        if block.document_id != parsed_document.document_id:
            raise ValueError(
                f"parsed block document_id does not match parsed document: {block.block_id}"
            )
        evidence = evidence_blocks[block.block_id]
        if evidence.text_length != len(block.text):
            raise ValueError(
                f"evidence block text_length does not match parsed block: {block.block_id}"
            )
        if evidence.text_sha256 != sha256_text(block.text):
            raise ValueError(
                f"evidence block text_sha256 does not match parsed block: {block.block_id}"
            )
        if evidence.page != block.page:
            raise ValueError(
                f"evidence block page does not match parsed block: {block.block_id}"
            )

    cells_by_id = {cell.cell_id: cell for cell in index.cells}
    for occurrence in index.cell_occurrences:
        block_text = parsed_blocks[occurrence.block_id].text
        cell = cells_by_id[occurrence.cell_id]
        actual = block_text[
            occurrence.canonical_start : occurrence.canonical_end
        ]
        if actual != cell.text:
            raise ValueError(
                "cell occurrence text does not match logical cell text: "
                f"{occurrence.occurrence_id}"
            )


def ensure_evidence_index(
    path: str | Path,
    parsed_document: ParsedDocument,
) -> EvidenceIndex:
    source_hash = sha256_file(path)
    if (
        parsed_document.source_sha256 is not None
        and parsed_document.source_sha256 != source_hash
    ):
        raise ValueError("parsed document source_sha256 does not match input bytes")

    parser_identity = parsed_document.parser_identity or ParserIdentity(
        parser_name=parsed_document.parser_name,
        parser_version="1",
    )
    if parsed_document.evidence_index is not None:
        index = parsed_document.evidence_index
        if index.source_sha256 != source_hash:
            raise ValueError("evidence index source_sha256 does not match input bytes")
        if index.parser_identity != parser_identity:
            raise ValueError("evidence index parser identity does not match parsed document")
        validate_evidence_index_against_parsed_document(index, parsed_document)
        finalized = finalize_evidence_fingerprint(index)
        if index.evidence_fingerprint not in {
            "0" * 64,
            finalized.evidence_fingerprint,
        }:
            raise ValueError("evidence index fingerprint does not match its content")
        validate_evidence_fingerprint(finalized)
        return finalized

    index = EvidenceIndex(
        document_id=parsed_document.document_id,
        document_name=parsed_document.document_name,
        source_format=parsed_document.source_format,
        source_sha256=source_hash,
        parser_identity=parser_identity,
        evidence_fingerprint="0" * 64,
        blocks=[
            BlockEvidenceRecord(
                block_id=block.block_id,
                text_length=len(block.text),
                text_sha256=sha256_text(block.text),
                page=block.page,
                expected_capabilities=["text_range"],
                available_capabilities=["text_range"],
            )
            for block in parsed_document.blocks
        ],
        warnings=list(parsed_document.warnings),
    )
    return finalize_evidence_fingerprint(index)
