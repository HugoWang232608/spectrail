from spectrail.core.models import RequirementIR, SourceSpan
from spectrail.evaluation.matcher import match_requirements
from spectrail.evaluation.metrics import build_evaluation_metrics, precision_recall_f1
from spectrail.evaluation.models import GoldRequirement, GoldSource


def _candidate(identifier: str, statement: str, quote: str) -> RequirementIR:
    return RequirementIR(
        id=identifier,
        statement=statement,
        sources=[SourceSpan(document_id="doc_001", block_id="blk_0001", quote=quote)],
    )


def test_matcher_maximizes_cardinality_before_local_quality():
    candidates = [
        RequirementIR(
            id="C1",
            statement="Gold two",
            sources=[
                SourceSpan(document_id="doc_001", block_id="blk_0001", quote="specific"),
                SourceSpan(document_id="doc_001", block_id="blk_0001", quote="shared"),
            ],
        ),
        _candidate("C2", "Gold one", "specific"),
    ]
    gold = [
        GoldRequirement(
            gold_id="G1",
            statement="Gold one",
            sources=[GoldSource(block_id="blk_0001", quote="specific")],
        ),
        GoldRequirement(
            gold_id="G2",
            statement="Gold two",
            sources=[GoldSource(block_id="blk_0001", quote="shared")],
        ),
    ]
    matches = match_requirements(candidates, gold)
    assert len(matches.source_alignment_matches) == 2
    assert len(matches.requirement_exact_matches) == 2
    assert {(pair.candidate_id, pair.gold_id) for pair in matches.requirement_exact_matches} == {
        ("C1", "G2"),
        ("C2", "G1"),
    }


def test_source_alignment_does_not_imply_requirement_exactness():
    candidates = [_candidate("C1", "Wrong statement", "shared")]
    gold = [
        GoldRequirement(
            gold_id="G1",
            statement="Correct statement",
            sources=[GoldSource(block_id="blk_0001", quote="shared")],
        )
    ]
    matches = match_requirements(candidates, gold)
    assert len(matches.source_alignment_matches) == 1
    assert matches.requirement_exact_matches == []


def test_zero_denominator_policy_is_explicit():
    assert precision_recall_f1(0, 0, 0) == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert precision_recall_f1(1, 0, 0) == {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    assert precision_recall_f1(0, 1, 0) == {"precision": 0.0, "recall": 1.0, "f1": 0.0}


def test_metrics_keep_source_and_exact_counts_separate():
    candidates = [_candidate("C1", "Wrong statement", "shared")]
    gold = [
        GoldRequirement(
            gold_id="G1",
            statement="Correct statement",
            sources=[GoldSource(block_id="blk_0001", quote="shared")],
        )
    ]
    matches = match_requirements(candidates, gold)
    metrics = build_evaluation_metrics(
        gold_count=1,
        candidate_count=1,
        matches=matches,
        aggregated_count=1,
        validated_count=1,
        exported_count=1,
        grounded_exported_count=1,
    )
    assert metrics["source_alignment_recall"] == 1.0
    assert metrics["requirement_exact_recall"] == 0.0


def test_match_set_is_stable_when_inputs_are_reordered():
    candidates = [
        _candidate("C2", "Second", "quote two"),
        _candidate("C1", "First", "quote one"),
    ]
    gold = [
        GoldRequirement(
            gold_id="G2", statement="Second", sources=[GoldSource(block_id="blk_0001", quote="quote two")]
        ),
        GoldRequirement(
            gold_id="G1", statement="First", sources=[GoldSource(block_id="blk_0001", quote="quote one")]
        ),
    ]
    forward = match_requirements(candidates, gold)
    reversed_result = match_requirements(list(reversed(candidates)), list(reversed(gold)))
    expected = {(pair.candidate_id, pair.gold_id) for pair in forward.requirement_exact_matches}
    assert {(pair.candidate_id, pair.gold_id) for pair in reversed_result.requirement_exact_matches} == expected
