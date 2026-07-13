from __future__ import annotations

import math
from typing import Any

from spectrail.core.models import RequirementIR, SourceSpan, ValidationIssue, ValidationReport
from spectrail.evidence.models import (
    BlockEvidenceRecord,
    BoundingBox,
    CapabilityValidationResult,
    EvidenceIndex,
    EvidencePolicy,
    aggregate_locator_status,
)
from spectrail.evidence.quote_matcher import QuoteMatchRegistry, QuoteMatchResult


class SourceLocatorValidator:
    def validate(
        self,
        requirements: list[RequirementIR],
        evidence_index: EvidenceIndex,
        quote_matches: QuoteMatchRegistry,
        *,
        policy: EvidencePolicy,
    ) -> tuple[list[RequirementIR], ValidationReport, list[dict[str, Any]]]:
        blocks_by_id = {block.block_id: block for block in evidence_index.blocks}
        report = ValidationReport(valid=True, metadata={"evidence_policy": policy})
        failures: list[dict[str, Any]] = []
        validated: list[RequirementIR] = []

        for requirement in requirements:
            source_passes: list[bool] = []
            for source_index, source in enumerate(requirement.sources):
                results = self.validate_source(
                    source,
                    evidence_index,
                    blocks_by_id,
                    quote_matches,
                )
                source.capability_results = results
                block = blocks_by_id.get(source.block_id)
                expected = (
                    block.expected_capabilities if block is not None else ["text_range"]
                )
                source.locator_status = aggregate_locator_status(expected, results)
                passed = _passes_policy(block, results, policy)
                source_passes.append(passed)

                for result in results:
                    if result.status == "PASS":
                        continue
                    level = (
                        "error"
                        if not passed and _blocks_policy(result, block, policy)
                        else "warning"
                    )
                    report.add_issue(
                        ValidationIssue(
                            level=level,
                            code=result.issue_code or "SOURCE_LOCATOR_NOT_VERIFIED",
                            message=result.message or "source locator capability did not pass",
                            requirement_id=requirement.id,
                            source_block_id=source.block_id,
                            metadata={
                                "source_index": source_index,
                                "source_evidence_key": source.source_evidence_key,
                                "capability": result.capability,
                                "capability_status": result.status,
                                "locator_status": source.locator_status,
                                "evidence_policy": policy,
                            },
                        )
                    )

                if not passed:
                    failures.append(
                        {
                            "requirement_id": requirement.id,
                            "source_index": source_index,
                            "source_block_id": source.block_id,
                            "source_evidence_key": source.source_evidence_key,
                            "locator_status": source.locator_status,
                            "capability_results": [
                                result.model_dump(mode="json") for result in results
                            ],
                        }
                    )
            if source_passes and all(source_passes):
                validated.append(requirement)

        report.metadata.update(
            {
                "validated_requirements": len(validated),
                "failed_sources": len(failures),
            }
        )
        return validated, report, failures

    def validate_source(
        self,
        source: SourceSpan,
        evidence_index: EvidenceIndex,
        blocks_by_id: dict[str, BlockEvidenceRecord],
        quote_matches: QuoteMatchRegistry,
    ) -> list[CapabilityValidationResult]:
        if source.source_evidence_key is None:
            raise ValueError("source_evidence_key is required for locator validation")
        quote_match = quote_matches.require(source.source_evidence_key)
        block = blocks_by_id.get(source.block_id)
        if block is None:
            return [
                CapabilityValidationResult(
                    capability="text_range",
                    status="FAIL_INVALID_REFERENCE",
                    issue_code="SOURCE_BLOCK_NOT_FOUND",
                    message="source block is not present in the EvidenceIndex",
                )
            ]

        return [
            self._validate_capability(
                capability,
                source,
                block,
                evidence_index,
                quote_match,
            )
            for capability in block.expected_capabilities
        ]

    def _validate_capability(
        self,
        capability: str,
        source: SourceSpan,
        block: BlockEvidenceRecord,
        evidence_index: EvidenceIndex,
        quote_match: QuoteMatchResult,
    ) -> CapabilityValidationResult:
        if capability not in block.available_capabilities:
            return CapabilityValidationResult(
                capability=capability,  # type: ignore[arg-type]
                status="WARNING_UNAVAILABLE",
                issue_code=f"SOURCE_{capability.upper()}_UNAVAILABLE",
                message="expected evidence capability is unavailable",
            )
        if capability == "text_range":
            return _validate_text_locator(source, quote_match)
        if quote_match.status == "AMBIGUOUS_MATCH":
            return CapabilityValidationResult(
                capability=capability,  # type: ignore[arg-type]
                status="WARNING_AMBIGUOUS",
                issue_code="QUOTE_OCCURRENCE_AMBIGUOUS",
                message="structured locator cannot select a unique quote occurrence",
            )
        if quote_match.status == "NO_MATCH" or quote_match.selected_range is None:
            return CapabilityValidationResult(
                capability=capability,  # type: ignore[arg-type]
                status="FAIL_DERIVATION",
                issue_code="QUOTE_RANGE_NOT_FOUND",
                message="structured locator cannot be derived without a quote range",
            )
        if capability == "page_region":
            return _validate_page_locator(source, block, evidence_index, quote_match)
        return _validate_table_locator(source, block, evidence_index, quote_match)


def _validate_text_locator(
    source: SourceSpan,
    quote_match: QuoteMatchResult,
) -> CapabilityValidationResult:
    if quote_match.status == "AMBIGUOUS_MATCH":
        return CapabilityValidationResult(
            capability="text_range",
            status="WARNING_AMBIGUOUS",
            issue_code="QUOTE_OCCURRENCE_AMBIGUOUS",
            message="quote matches multiple ranges in the source block",
        )
    if quote_match.status == "NO_MATCH" or quote_match.selected_range is None:
        return CapabilityValidationResult(
            capability="text_range",
            status="FAIL_DERIVATION",
            issue_code="QUOTE_RANGE_NOT_FOUND",
            message="quote cannot be mapped to a source block range",
        )
    locator = source.text_locator
    expected = quote_match.selected_range
    if (
        locator is None
        or locator.block_id != source.block_id
        or locator.start != expected.start
        or locator.end != expected.end
        or locator.match_basis != quote_match.match_basis
    ):
        return CapabilityValidationResult(
            capability="text_range",
            status="FAIL_INVALID_REFERENCE",
            issue_code="SOURCE_TEXT_LOCATOR_INVALID",
            message="text locator does not match the registered quote range",
        )
    return CapabilityValidationResult(capability="text_range", status="PASS")


def _validate_page_locator(
    source: SourceSpan,
    block: BlockEvidenceRecord,
    evidence_index: EvidenceIndex,
    quote_match: QuoteMatchResult,
) -> CapabilityValidationResult:
    locator = source.page_locator
    if locator is None:
        return CapabilityValidationResult(
            capability="page_region",
            status="FAIL_INVALID_REFERENCE",
            issue_code="SOURCE_PAGE_LOCATOR_MISSING",
            message="available page evidence requires a page locator",
        )
    page = next((item for item in evidence_index.pages if item.page == block.page), None)
    expected_bbox = _quote_bbox(block, evidence_index, quote_match)
    if (
        block.page is None
        or page is None
        or expected_bbox is None
        or locator.page != block.page
        or not math.isclose(locator.page_width, page.width, abs_tol=1e-4)
        or not math.isclose(locator.page_height, page.height, abs_tol=1e-4)
        or locator.coordinate_space != page.coordinate_space
        or not _bbox_equal(locator.bbox, expected_bbox)
    ):
        return CapabilityValidationResult(
            capability="page_region",
            status="FAIL_INVALID_REFERENCE",
            issue_code="SOURCE_PAGE_LOCATOR_INVALID",
            message="page locator does not match EvidenceIndex geometry",
        )
    return CapabilityValidationResult(capability="page_region", status="PASS")


def _validate_table_locator(
    source: SourceSpan,
    block: BlockEvidenceRecord,
    evidence_index: EvidenceIndex,
    quote_match: QuoteMatchResult,
) -> CapabilityValidationResult:
    locator = source.table_locator
    if locator is None:
        return CapabilityValidationResult(
            capability="table_cell",
            status="FAIL_INVALID_REFERENCE",
            issue_code="SOURCE_TABLE_LOCATOR_MISSING",
            message="available table evidence requires a table locator",
        )
    cells_by_id = {cell.cell_id: cell for cell in evidence_index.cells}
    selected_range = quote_match.selected_range
    expected_cells = [
        occurrence.cell_id
        for occurrence in evidence_index.cell_occurrences
        if occurrence.block_id == block.block_id
        and occurrence.canonical_start < selected_range.end  # type: ignore[union-attr]
        and occurrence.canonical_end > selected_range.start  # type: ignore[union-attr]
        and cells_by_id[occurrence.cell_id].text.strip()
    ]
    expected_cells = list(dict.fromkeys(expected_cells))
    locator_cells = [cells_by_id.get(cell_id) for cell_id in locator.cell_ids]
    if (
        block.table_id is None
        or locator.table_id != block.table_id
        or not expected_cells
        or locator.cell_ids != expected_cells
        or any(cell is None for cell in locator_cells)
        or locator.row_indices != [cell.row_index for cell in locator_cells if cell]
        or locator.column_indices != [cell.column_index for cell in locator_cells if cell]
    ):
        return CapabilityValidationResult(
            capability="table_cell",
            status="FAIL_INVALID_REFERENCE",
            issue_code="SOURCE_TABLE_LOCATOR_INVALID",
            message="table locator does not match quote cell occurrences",
        )
    return CapabilityValidationResult(capability="table_cell", status="PASS")


def _quote_bbox(
    block: BlockEvidenceRecord,
    evidence_index: EvidenceIndex,
    quote_match: QuoteMatchResult,
) -> BoundingBox | None:
    selected = quote_match.selected_range
    fragments_by_id = {
        fragment.fragment_id: fragment for fragment in evidence_index.fragments
    }
    overlapping = [
        fragments_by_id[fragment_id].bbox
        for fragment_id in block.fragment_ids
        if fragments_by_id[fragment_id].start < selected.end  # type: ignore[union-attr]
        and fragments_by_id[fragment_id].end > selected.start  # type: ignore[union-attr]
    ]
    if not overlapping:
        return block.bbox
    return BoundingBox(
        x0=min(box.x0 for box in overlapping),
        y0=min(box.y0 for box in overlapping),
        x1=max(box.x1 for box in overlapping),
        y1=max(box.y1 for box in overlapping),
        coordinate_space=overlapping[0].coordinate_space,
    )


def _bbox_equal(first: BoundingBox, second: BoundingBox) -> bool:
    return first.coordinate_space == second.coordinate_space and all(
        math.isclose(left, right, abs_tol=1e-4)
        for left, right in zip(
            (first.x0, first.y0, first.x1, first.y1),
            (second.x0, second.y0, second.x1, second.y1),
        )
    )


def _passes_policy(
    block: BlockEvidenceRecord | None,
    results: list[CapabilityValidationResult],
    policy: EvidencePolicy,
) -> bool:
    if policy == "quote_only":
        return True
    if block is None:
        return False
    by_capability = {result.capability: result.status for result in results}
    if policy == "structured_if_available":
        return not any(
            by_capability.get(capability) in {
                "FAIL_INVALID_REFERENCE",
                "FAIL_DERIVATION",
            }
            for capability in block.available_capabilities
        )
    return all(
        by_capability.get(capability) == "PASS"
        for capability in block.expected_capabilities
    )


def _blocks_policy(
    result: CapabilityValidationResult,
    block: BlockEvidenceRecord | None,
    policy: EvidencePolicy,
) -> bool:
    if policy == "quote_only":
        return False
    if block is None:
        return True
    if policy == "structured_required":
        return result.status != "PASS"
    return (
        result.capability in block.available_capabilities
        and result.status in {"FAIL_INVALID_REFERENCE", "FAIL_DERIVATION"}
    )
