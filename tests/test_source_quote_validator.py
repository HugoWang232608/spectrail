from spectrail.core.models import DocumentBlock, RequirementIR, SourceSpan
from spectrail.evidence import build_quote_match_registry
from spectrail.validators.source_quote_validator import SourceQuoteValidator, normalize_text


def block(text: str) -> DocumentBlock:
    return DocumentBlock(
        block_id="blk_0001",
        document_id="doc_001",
        type="paragraph",
        text=text,
        section_path=["S"],
        order=1,
    )


def validate_source(source: SourceSpan, blocks_by_id: dict[str, DocumentBlock]) -> SourceSpan:
    requirement = type("Requirement", (), {"sources": [source]})()
    registry = build_quote_match_registry(
        [requirement], blocks_by_id.values(), evidence_fingerprint="a" * 64
    )
    return SourceQuoteValidator().validate_source(source, blocks_by_id, registry)


def test_source_quote_exact_match():
    source = SourceSpan(document_id="doc_001", block_id="blk_0001", quote="系统应记录事件。")
    validated = validate_source(source, {"blk_0001": block("系统应记录事件。")})
    assert validated.match_status == "PASS_EXACT"


def test_source_quote_normalized_match():
    source = SourceSpan(document_id="doc_001", block_id="blk_0001", quote="系统应记录事件。")
    validated = validate_source(source, {"blk_0001": block("系统应记录事件.")})
    assert validated.match_status == "PASS_NORMALIZED"


def test_source_quote_fuzzy_warning():
    source = SourceSpan(document_id="doc_001", block_id="blk_0001", quote="系统应记录完整事件。")
    validated = validate_source(source, {"blk_0001": block("系统应记录事件。")})
    assert validated.match_status == "WARNING_FUZZY"


def test_source_quote_fail_not_found():
    source = SourceSpan(document_id="doc_001", block_id="blk_missing", quote="系统应记录事件。")
    validated = validate_source(source, {"blk_0001": block("系统应记录事件。")})
    assert validated.match_status == "FAIL_NOT_FOUND"


def test_normalize_text_collapses_whitespace_and_punctuation():
    assert normalize_text("系统 应 记录 事件。") == "系统 应 记录 事件."
    assert normalize_text("| A | B |") == "|A|B|"


def test_requirement_requires_all_sources_and_reports_each_failure():
    blocks = [
        block("系统应记录事件。"),
        DocumentBlock(
            block_id="blk_0002",
            document_id="doc_001",
            type="paragraph",
            text="系统应审计操作。",
            order=2,
        ),
    ]
    requirement = RequirementIR(
        id="REQ-1",
        statement="系统应记录并审计。",
        sources=[
            SourceSpan(
                document_id="doc_001",
                block_id="blk_0001",
                quote="系统应记录事件。",
            ),
            SourceSpan(
                document_id="doc_001",
                block_id="blk_0002",
                quote="不存在的引用一",
            ),
            SourceSpan(
                document_id="doc_001",
                block_id="blk_missing",
                quote="不存在的引用二",
            ),
        ],
    )
    registry = build_quote_match_registry(
        [requirement], blocks, evidence_fingerprint="a" * 64
    )
    validated, report = SourceQuoteValidator().validate(
        [requirement], blocks, registry
    )

    assert validated == []
    assert report.valid is False
    assert [issue.metadata["source_index"] for issue in report.issues] == [1, 2]
    assert [issue.source_block_id for issue in report.issues] == [
        "blk_0002",
        "blk_missing",
    ]
