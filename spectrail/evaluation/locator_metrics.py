from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from spectrail.core.models import RequirementIR, SourceSpan
from spectrail.evaluation.matcher import EvaluationMatches
from spectrail.evaluation.models import GoldRequirement
from spectrail.evidence.models import (
    STRUCTURED_CAPABILITIES,
    BlockEvidenceRecord,
    BoundingBox,
)


def bbox_iou(first: BoundingBox, second: BoundingBox) -> float:
    if first.coordinate_space != second.coordinate_space:
        return 0.0
    x0 = max(first.x0, second.x0)
    y0 = max(first.y0, second.y0)
    x1 = min(first.x1, second.x1)
    y1 = min(first.y1, second.y1)
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    first_area = (first.x1 - first.x0) * (first.y1 - first.y0)
    second_area = (second.x1 - second.x0) * (second.y1 - second.y0)
    union = first_area + second_area - intersection
    return 0.0 if union <= 0 else intersection / union


def build_locator_metrics(
    *,
    candidates: list[RequirementIR],
    gold: list[GoldRequirement],
    matches: EvaluationMatches,
    block_evidence: Iterable[BlockEvidenceRecord] = (),
) -> dict[str, Any]:
    blocks_by_id = {block.block_id: block for block in block_evidence}

    page_evaluated = 0
    page_passed = 0
    cell_evaluated = 0
    cell_true_positive = 0
    cell_actual_total = 0
    cell_gold_total = 0
    bbox_values: list[float] = []
    bbox_passed = 0
    text_evaluated = 0
    text_passed = 0
    structured_eligible = 0
    structured_passed = 0
    structured_source_missing = 0
    structured_source_ambiguous = 0
    structured_source_unverified = 0
    structured_source_failed = 0
    structured_capability_expected = 0
    structured_capability_passed = 0
    structured_capability_missing = 0
    structured_capability_ambiguous = 0
    structured_capability_unverified = 0
    structured_capability_failed = 0
    structured_invalid_reference = 0
    structured_derivation_failed = 0

    for pair in matches.source_alignment_matches:
        source = candidates[pair.candidate_index].sources[pair.candidate_source_index]
        gold_source = gold[pair.gold_index].sources[pair.gold_source_index]

        if source.match_status in {"PASS_EXACT", "PASS_NORMALIZED"}:
            text_evaluated += 1
            if source.text_locator is not None and _capability_passed(source, "text_range"):
                text_passed += 1

        if gold_source.page is not None:
            page_evaluated += 1
            if source.page_locator is not None and source.page_locator.page == gold_source.page:
                page_passed += 1

        if gold_source.cell_ids:
            cell_evaluated += 1
            actual_cells = set(source.table_locator.cell_ids if source.table_locator else [])
            gold_cells = set(gold_source.cell_ids)
            cell_true_positive += len(actual_cells & gold_cells)
            cell_actual_total += len(actual_cells)
            cell_gold_total += len(gold_cells)

        if gold_source.bbox is not None:
            actual_bbox = _source_bbox(source, prefer_table=bool(gold_source.cell_ids))
            actual_page = (
                source.page_locator.page
                if source.page_locator is not None
                else source.page
            )
            value = (
                0.0
                if actual_bbox is None or actual_page != gold_source.page
                else bbox_iou(actual_bbox, gold_source.bbox)
            )
            bbox_values.append(value)
            if value >= gold_source.bbox_iou_threshold:
                bbox_passed += 1

        block = blocks_by_id.get(source.block_id)
        if block is not None:
            expected_structured = set(block.expected_capabilities) & STRUCTURED_CAPABILITIES
            if expected_structured:
                structured_eligible += 1
                statuses = {
                    item.capability: item.status for item in source.capability_results
                }
                expected_statuses = [statuses.get(capability) for capability in expected_structured]
                structured_capability_expected += len(expected_statuses)
                for status in expected_statuses:
                    if status == "PASS":
                        structured_capability_passed += 1
                    elif status in {None, "WARNING_UNAVAILABLE"}:
                        structured_capability_missing += 1
                    elif status == "WARNING_AMBIGUOUS":
                        structured_capability_ambiguous += 1
                    elif status == "UNVERIFIED":
                        structured_capability_unverified += 1
                    elif status == "FAIL_INVALID_REFERENCE":
                        structured_capability_failed += 1
                        structured_invalid_reference += 1
                    elif status == "FAIL_DERIVATION":
                        structured_capability_failed += 1
                        structured_derivation_failed += 1
                if all(status == "PASS" for status in expected_statuses):
                    structured_passed += 1
                elif any(
                    status in {"FAIL_INVALID_REFERENCE", "FAIL_DERIVATION"}
                    for status in expected_statuses
                ):
                    structured_source_failed += 1
                elif any(status == "WARNING_AMBIGUOUS" for status in expected_statuses):
                    structured_source_ambiguous += 1
                elif any(
                    status in {None, "WARNING_UNAVAILABLE"}
                    for status in expected_statuses
                ):
                    structured_source_missing += 1
                else:
                    structured_source_unverified += 1

    cell_precision = _ratio(cell_true_positive, cell_actual_total)
    cell_recall = _ratio(cell_true_positive, cell_gold_total)
    cell_f1 = (
        0.0
        if cell_precision + cell_recall == 0
        else 2 * cell_precision * cell_recall / (cell_precision + cell_recall)
    )
    return {
        "page_accuracy": _ratio(page_passed, page_evaluated),
        "page_evaluated_count": page_evaluated,
        "table_cell_precision": cell_precision,
        "table_cell_recall": cell_recall,
        "table_cell_f1": cell_f1,
        "table_cell_evaluated_count": cell_evaluated,
        "table_cell_true_positive_count": cell_true_positive,
        "table_cell_actual_count": cell_actual_total,
        "table_cell_gold_count": cell_gold_total,
        "bbox_iou_mean": _mean(bbox_values),
        "bbox_iou_pass_rate": _ratio(bbox_passed, len(bbox_values)),
        "bbox_evaluated_count": len(bbox_values),
        "text_locator_pass_rate": _ratio(text_passed, text_evaluated),
        "text_locator_evaluated_count": text_evaluated,
        "structured_grounding_coverage": _ratio(
            structured_passed, structured_eligible
        ),
        "structured_grounding_eligible_count": structured_eligible,
        "structured_grounding_pass_count": structured_passed,
        "structured_grounding_missing_count": structured_source_missing,
        "structured_grounding_ambiguous_count": structured_source_ambiguous,
        "structured_grounding_unverified_count": structured_source_unverified,
        "structured_grounding_failed_count": structured_source_failed,
        "structured_capability_expected_count": structured_capability_expected,
        "structured_capability_pass_count": structured_capability_passed,
        "structured_capability_missing_count": structured_capability_missing,
        "structured_capability_ambiguous_count": structured_capability_ambiguous,
        "structured_capability_unverified_count": structured_capability_unverified,
        "structured_capability_failed_count": structured_capability_failed,
        "structured_invalid_reference_count": structured_invalid_reference,
        "structured_derivation_failed_count": structured_derivation_failed,
    }


def _capability_passed(source: SourceSpan, capability: str) -> bool:
    return any(
        result.capability == capability and result.status == "PASS"
        for result in source.capability_results
    )


def _source_bbox(source: SourceSpan, *, prefer_table: bool) -> BoundingBox | None:
    if prefer_table:
        return source.table_locator.bbox if source.table_locator is not None else None
    return source.page_locator.bbox if source.page_locator is not None else None


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


def _mean(values: list[float]) -> float:
    return 1.0 if not values else sum(values) / len(values)
