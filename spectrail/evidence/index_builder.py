from __future__ import annotations

from pathlib import Path

from spectrail.evidence.fingerprint import (
    finalize_evidence_fingerprint,
    sha256_file,
    sha256_text,
)
from spectrail.evidence.models import BlockEvidenceRecord, EvidenceIndex, ParserIdentity
from spectrail.parsers.base import ParsedDocument


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
        return finalize_evidence_fingerprint(index)

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
