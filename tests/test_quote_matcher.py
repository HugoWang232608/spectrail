import pytest

from spectrail.core.models import DocumentBlock, SourceSpan
from spectrail.evidence import (
    QuoteMatcher,
    QuoteMatchRegistry,
    build_quote_match_registry,
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
    block = _block("repeat / repeat")
    requirement = type("Requirement", (), {"sources": [source]})()
    registry = build_quote_match_registry(
        [requirement], [block], evidence_fingerprint="a" * 64
    )
    assert registry.require(source.source_evidence_key) == result
    validated = SourceQuoteValidator().validate_source(
        source, {"blk_0001": block}, registry
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
    registry = QuoteMatchRegistry(schema_version="quote_matches_v2")
    registry.add(key, QuoteMatcher().match("quote", "quote"))
    with pytest.raises(ValueError, match="collision"):
        registry.add(key, QuoteMatcher().match("quote / quote", "quote"))


def test_quote_match_registry_requires_v2_schema_marker():
    with pytest.raises(ValueError, match="schema_version"):
        QuoteMatchRegistry.model_validate({"entries": {}})


def test_source_evidence_key_includes_physical_table_row_identity():
    common = {
        "evidence_fingerprint": "a" * 64,
        "document_id": "doc_001",
        "block_id": "blk_0001",
        "quote": "Merged",
        "canonical_cell_ids": ["cell_00000001_r0001_c0001"],
    }

    assert source_evidence_key(**common, source_table_row_index=1) != (
        source_evidence_key(**common, source_table_row_index=2)
    )
