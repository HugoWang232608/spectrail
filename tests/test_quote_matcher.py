import pytest

from spectrail.core.models import DocumentBlock, SourceSpan
from spectrail.evidence import (
    QuoteMatcher,
    QuoteMatchRegistry,
    find_all_normalized_ranges,
    normalize_with_mapping,
    source_evidence_key,
)
from spectrail.validators.source_quote_validator import SourceQuoteValidator


def _block(text: str) -> DocumentBlock:
    return DocumentBlock(
        block_id="blk_0001",
        document_id="doc_001",
        type="paragraph",
        text=text,
        order=1,
    )


def test_exact_ambiguity_keeps_quote_pass_but_has_no_selected_range():
    result = QuoteMatcher().match("repeat / repeat", "repeat", provisional=True)
    assert result.status == "AMBIGUOUS_MATCH"
    assert result.match_basis == "exact"
    assert result.selected_range is None
    assert result.provisional_range == result.original_ranges[0]

    source = SourceSpan(document_id="doc_001", block_id="blk_0001", quote="repeat")
    validated = SourceQuoteValidator().validate_source(
        source,
        {"blk_0001": _block("repeat / repeat")},
        match_result=result,
    )
    assert validated.match_status == "PASS_EXACT"


def test_normalized_match_enumerates_distinct_original_ranges():
    text = "A\u3000B / A B"
    ranges = find_all_normalized_ranges(text, "A  B")
    assert [(item.start, item.end) for item in ranges] == [(0, 3), (6, 9)]
    result = QuoteMatcher().match(text, "A  B")
    assert result.status == "AMBIGUOUS_MATCH"
    assert result.match_basis == "normalized"


def test_normalized_range_maps_back_across_whitespace_and_punctuation():
    text = "😀  系统应记录事件。"
    normalized = normalize_with_mapping(text)
    assert normalized.text == "😀 系统应记录事件."
    result = QuoteMatcher().match(text, "系统应记录事件.")
    assert result.status == "UNIQUE_MATCH"
    assert result.match_basis == "normalized"
    assert result.selected_range is not None
    assert text[result.selected_range.start : result.selected_range.end] == "系统应记录事件。"


def test_registry_rejects_conflicting_results_for_same_source_key():
    key = source_evidence_key(
        evidence_fingerprint="a" * 64,
        document_id="doc_001",
        block_id="blk_0001",
        quote="quote",
    )
    registry = QuoteMatchRegistry()
    registry.add(key, QuoteMatcher().match("quote", "quote"))
    with pytest.raises(ValueError, match="collision"):
        registry.add(key, QuoteMatcher().match("quote / quote", "quote"))
