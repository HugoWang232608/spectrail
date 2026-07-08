from spectrail.core.models import DocumentBlock, SourceSpan
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


def test_source_quote_exact_match():
    source = SourceSpan(document_id="doc_001", block_id="blk_0001", quote="系统应记录事件。")
    validated = SourceQuoteValidator().validate_source(source, {"blk_0001": block("系统应记录事件。")})
    assert validated.match_status == "PASS_EXACT"


def test_source_quote_normalized_match():
    source = SourceSpan(document_id="doc_001", block_id="blk_0001", quote="系统应记录事件。")
    validated = SourceQuoteValidator().validate_source(source, {"blk_0001": block("系统应记录事件.")})
    assert validated.match_status == "PASS_NORMALIZED"


def test_source_quote_fuzzy_warning():
    source = SourceSpan(document_id="doc_001", block_id="blk_0001", quote="系统应记录完整事件。")
    validated = SourceQuoteValidator().validate_source(source, {"blk_0001": block("系统应记录事件。")})
    assert validated.match_status == "WARNING_FUZZY"


def test_source_quote_fail_not_found():
    source = SourceSpan(document_id="doc_001", block_id="blk_missing", quote="系统应记录事件。")
    validated = SourceQuoteValidator().validate_source(source, {"blk_0001": block("系统应记录事件。")})
    assert validated.match_status == "FAIL_NOT_FOUND"


def test_normalize_text_collapses_whitespace_and_punctuation():
    assert normalize_text("系统 应 记录 事件。") == "系统 应 记录 事件."
    assert normalize_text("| A | B |") == "|A|B|"
