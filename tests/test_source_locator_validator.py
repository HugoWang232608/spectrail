from spectrail.core.models import DocumentBlock, RequirementIR, SourceSpan
from spectrail.evidence import (
    BlockEvidenceRecord,
    EvidenceIndex,
    ParserIdentity,
    build_quote_match_registry,
    finalize_evidence_fingerprint,
    sha256_text,
)
from spectrail.evidence.enricher import SourceEvidenceEnricher
from spectrail.validators.source_locator_validator import SourceLocatorValidator


def _block() -> DocumentBlock:
    return DocumentBlock(
        block_id="blk_0001",
        document_id="doc_001",
        type="paragraph",
        text="source quote",
        order=1,
    )


def _index(*, expected: list[str], available: list[str]) -> EvidenceIndex:
    return finalize_evidence_fingerprint(
        EvidenceIndex(
            document_id="doc_001",
            document_name="sample.md",
            source_format="markdown",
            source_sha256="a" * 64,
            parser_identity=ParserIdentity(
                parser_name="markdown_parser_v1", parser_version="1"
            ),
            evidence_fingerprint="0" * 64,
            blocks=[
                BlockEvidenceRecord(
                    block_id="blk_0001",
                    text_length=12,
                    text_sha256=sha256_text("source quote"),
                    expected_capabilities=expected,
                    available_capabilities=available,
                )
            ],
        )
    )


def _prepared(index: EvidenceIndex):
    requirements = [
        RequirementIR(
            id="REQ-1",
            statement="Statement",
            sources=[
                SourceSpan(
                    document_id="doc_001",
                    block_id="blk_0001",
                    quote="source quote",
                )
            ],
        )
    ]
    registry = build_quote_match_registry(
        requirements,
        [_block()],
        evidence_fingerprint=index.evidence_fingerprint,
    )
    SourceEvidenceEnricher().enrich(requirements, index, registry)
    return requirements, registry


def test_structured_if_available_allows_expected_but_unavailable_capability():
    index = _index(
        expected=["text_range", "page_region"],
        available=["text_range"],
    )
    requirements, registry = _prepared(index)
    validated, report, failures = SourceLocatorValidator().validate(
        requirements,
        index,
        registry,
        policy="structured_if_available",
    )

    assert validated == requirements
    assert failures == []
    assert report.valid is True
    assert requirements[0].sources[0].locator_status == "WARNING_UNAVAILABLE"
    assert report.issues[0].level == "warning"


def test_structured_required_rejects_unavailable_capability():
    index = _index(
        expected=["text_range", "page_region"],
        available=["text_range"],
    )
    requirements, registry = _prepared(index)
    validated, report, failures = SourceLocatorValidator().validate(
        requirements,
        index,
        registry,
        policy="structured_required",
    )

    assert validated == []
    assert report.valid is False
    assert failures[0]["source_index"] == 0
    assert any(issue.code == "SOURCE_PAGE_REGION_UNAVAILABLE" for issue in report.issues)


def test_available_page_capability_requires_valid_locator():
    index = _index(
        expected=["text_range", "page_region"],
        available=["text_range", "page_region"],
    )
    requirements, registry = _prepared(index)
    validated, report, failures = SourceLocatorValidator().validate(
        requirements,
        index,
        registry,
        policy="structured_if_available",
    )

    assert validated == []
    assert report.valid is False
    assert failures[0]["locator_status"] == "FAIL_INVALID_REFERENCE"
    assert any(issue.code == "SOURCE_PAGE_LOCATOR_MISSING" for issue in report.issues)


def test_missing_evidence_block_is_invalid_reference():
    index = _index(expected=["text_range"], available=["text_range"])
    requirements, registry = _prepared(index)
    source = requirements[0].sources[0]
    source.block_id = "blk_missing"
    result = SourceLocatorValidator().validate_source(
        source,
        index,
        {block.block_id: block for block in index.blocks},
        registry,
    )
    assert result[0].status == "FAIL_INVALID_REFERENCE"
    assert result[0].issue_code == "SOURCE_BLOCK_NOT_FOUND"


def test_locator_validation_requires_all_sources_to_pass():
    index = _index(expected=["text_range"], available=["text_range"])
    requirements, registry = _prepared(index)
    duplicate = requirements[0].sources[0].model_copy(deep=True)
    duplicate.text_locator = None
    requirements[0].sources.append(duplicate)

    validated, report, failures = SourceLocatorValidator().validate(
        requirements,
        index,
        registry,
        policy="structured_if_available",
    )

    assert validated == []
    assert report.valid is False
    assert failures[0]["source_index"] == 1
