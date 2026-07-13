from spectrail.evidence import (
    BlockEvidenceRecord,
    EvidenceIndex,
    ParserIdentity,
    build_evidence_fingerprint,
    finalize_evidence_fingerprint,
    sha256_text,
)


def _index(text: str, *, runtime_version: str = "1.24.0") -> EvidenceIndex:
    return EvidenceIndex(
        document_id="doc_001",
        document_name="sample.md",
        source_format="markdown",
        source_sha256=sha256_text(text),
        parser_identity=ParserIdentity(
            parser_name="markdown_parser_v1",
            parser_version="1",
            source_format="markdown",
            runtime_dependencies={"parser-runtime": runtime_version},
        ),
        evidence_fingerprint="0" * 64,
        blocks=[
            BlockEvidenceRecord(
                block_id="blk_0001",
                text_length=len(text),
                text_sha256=sha256_text(text),
                expected_capabilities=["text_range"],
                available_capabilities=["text_range"],
            )
        ],
    )


def test_fingerprint_is_stable_and_excludes_itself():
    index = _index("alpha")
    first = finalize_evidence_fingerprint(index)
    second = finalize_evidence_fingerprint(first)
    assert first.evidence_fingerprint == second.evidence_fingerprint
    assert first.evidence_fingerprint == build_evidence_fingerprint(first)


def test_equal_length_different_content_has_different_fingerprint():
    assert build_evidence_fingerprint(_index("alpha")) != build_evidence_fingerprint(
        _index("bravo")
    )


def test_runtime_dependency_version_changes_fingerprint():
    assert build_evidence_fingerprint(
        _index("same", runtime_version="1.24.0")
    ) != build_evidence_fingerprint(_index("same", runtime_version="1.24.1"))
