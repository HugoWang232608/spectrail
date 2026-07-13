from __future__ import annotations

from typing import Any

from spectrail.evaluation.matcher import EvaluationMatches


ZERO_DENOMINATOR_POLICY = "explicit_v1"


def precision_recall_f1(gold_count: int, candidate_count: int, match_count: int) -> dict[str, float]:
    if gold_count == 0 and candidate_count == 0:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    if gold_count > 0 and candidate_count == 0:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    if gold_count == 0:
        return {"precision": 0.0, "recall": 1.0, "f1": 0.0}
    precision = match_count / candidate_count
    recall = match_count / gold_count
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1}


def build_evaluation_metrics(
    *,
    gold_count: int,
    candidate_count: int,
    matches: EvaluationMatches,
    aggregated_count: int,
    validated_count: int,
    exported_count: int,
    grounded_exported_count: int,
    quarantined_count: int = 0,
    model_items_total: int = 0,
    rejected_item_count: int = 0,
    raw_candidate_count: int = 0,
    collapsed_duplicate_count: int = 0,
    chunk_count: int = 0,
    chunk_completed_count: int = 0,
    chunk_failed_count: int = 0,
    model_call_count: int = 0,
    elapsed_ms: int = 0,
    rendered_prompt_chars: int = 0,
    response_chars: int = 0,
    estimated_tokens: int = 0,
) -> dict[str, Any]:
    source = precision_recall_f1(gold_count, candidate_count, len(matches.source_alignment_matches))
    exact = precision_recall_f1(gold_count, candidate_count, len(matches.requirement_exact_matches))
    return {
        "metric_zero_denominator_policy": ZERO_DENOMINATOR_POLICY,
        "matching_algorithm": matches.algorithm,
        "gold_requirements": gold_count,
        "validated_candidates_in_scope": candidate_count,
        "source_matching_cardinality": len(matches.source_alignment_matches),
        "requirement_matching_cardinality": len(matches.requirement_exact_matches),
        "ambiguous_optimum_count": matches.ambiguous_optimum_count,
        "raw_candidates": raw_candidate_count,
        "aggregated_requirements": aggregated_count,
        "validated_requirements": validated_count,
        "exported_requirements": exported_count,
        "grounded_exported_requirements": grounded_exported_count,
        "quarantined_requirements": quarantined_count,
        "model_items_total": model_items_total,
        "rejected_model_items": rejected_item_count,
        "collapsed_exact_candidates": collapsed_duplicate_count,
        "duplicate_rate": (
            0.0 if raw_candidate_count == 0 else collapsed_duplicate_count / raw_candidate_count
        ),
        "chunk_count": chunk_count,
        "chunk_completed_count": chunk_completed_count,
        "chunk_failed_count": chunk_failed_count,
        "model_call_count": model_call_count,
        "elapsed_ms": elapsed_ms,
        "rendered_prompt_chars": rendered_prompt_chars,
        "response_chars": response_chars,
        "estimated_tokens": estimated_tokens,
        "source_alignment_precision": source["precision"],
        "source_alignment_recall": source["recall"],
        "source_alignment_f1": source["f1"],
        "requirement_exact_precision": exact["precision"],
        "requirement_exact_recall": exact["recall"],
        "requirement_exact_f1": exact["f1"],
        "grounding_pass_rate": 1.0 if aggregated_count == 0 else validated_count / aggregated_count,
        "export_grounding_pass_rate": (
            1.0 if exported_count == 0 else grounded_exported_count / exported_count
        ),
        "quarantine_rate": 0.0 if aggregated_count == 0 else quarantined_count / aggregated_count,
        "rejected_item_rate": (
            0.0 if model_items_total == 0 else rejected_item_count / model_items_total
        ),
    }
