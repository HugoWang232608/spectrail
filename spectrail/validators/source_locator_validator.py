from __future__ import annotations

from typing import Any

from spectrail.core.models import (
    DocumentBlock,
    RequirementIR,
    SourceSpan,
    ValidationIssue,
    ValidationReport,
)
from spectrail.evidence.errors import EvidenceReferenceError, LocatorDerivationError
from spectrail.evidence.locator_derivation import (
    derive_page_locator,
    derive_table_evidence,
)
from spectrail.evidence.models import (
    BlockEvidenceRecord,
    CapabilityValidationResult,
    EvidenceIndex,
    EvidencePolicy,
    aggregate_locator_status,
)
from spectrail.evidence.quote_matcher import (
    QuoteMatchRegistry,
    QuoteMatchResult,
    normalize_text,
)


class SourceLocatorValidator:
    def validate(
        self,
        requirements: list[RequirementIR],
        evidence_index: EvidenceIndex,
        quote_matches: QuoteMatchRegistry,
        *,
        policy: EvidencePolicy,
        document_blocks: list[DocumentBlock] | None = None,
    ) -> tuple[list[RequirementIR], ValidationReport, list[dict[str, Any]]]:
        blocks_by_id = {block.block_id: block for block in evidence_index.blocks}
        report = ValidationReport(valid=True, metadata={"evidence_policy": policy})
        failures: list[dict[str, Any]] = []
        validated: list[RequirementIR] = []
        document_blocks_by_id = {
            block.block_id: block for block in document_blocks or []
        }

        for requirement in requirements:
            source_passes: list[bool] = []
            for source_index, source in enumerate(requirement.sources):
                results = self.validate_source(
                    source,
                    evidence_index,
                    blocks_by_id,
                    quote_matches,
                    document_blocks_by_id,
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
        document_blocks_by_id: dict[str, DocumentBlock] | None = None,
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
                (document_blocks_by_id or {}).get(source.block_id),
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
        document_block: DocumentBlock | None,
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
            return _validate_page_locator(
                source,
                block,
                evidence_index,
                quote_match,
                document_block,
            )
        return _validate_table_locator(
            source,
            block,
            evidence_index,
            quote_match,
            document_block,
        )


def _validate_text_locator(
    source: SourceSpan,
    quote_match: QuoteMatchResult,
) -> CapabilityValidationResult:
    if quote_match.status == "AMBIGUOUS_MATCH":
        provisional = quote_match.provisional_range
        locator = source.provisional_text_locator
        if (
            source.text_locator is not None
            or provisional is None
            or locator is None
            or locator.block_id != source.block_id
            or locator.start != provisional.start
            or locator.end != provisional.end
            or locator.match_basis != quote_match.match_basis
        ):
            return CapabilityValidationResult(
                capability="text_range",
                status="FAIL_INVALID_REFERENCE",
                issue_code="SOURCE_PROVISIONAL_TEXT_LOCATOR_INVALID",
                message="provisional text locator does not match the registry",
            )
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
    document_block: DocumentBlock | None,
) -> CapabilityValidationResult:
    locator = source.page_locator
    if locator is None:
        return CapabilityValidationResult(
            capability="page_region",
            status="FAIL_INVALID_REFERENCE",
            issue_code="SOURCE_PAGE_LOCATOR_MISSING",
            message="available page evidence requires a page locator",
        )
    table_evidence = None
    if source.canonical_source_cell_ids and document_block is not None:
        try:
            table_evidence = derive_table_evidence(
                evidence_index,
                block_id=source.block_id,
                selected_range=quote_match.selected_range,  # type: ignore[arg-type]
                canonical_cell_ids=source.canonical_source_cell_ids,
                block_text=document_block.text,
            )
        except ValueError:
            table_evidence = None
    expected = derive_page_locator(
        evidence_index,
        block_id=source.block_id,
        selected_range=quote_match.selected_range,  # type: ignore[arg-type]
        table_evidence=table_evidence,
    )
    if expected is None:
        return CapabilityValidationResult(
            capability="page_region",
            status="FAIL_DERIVATION",
            issue_code="SOURCE_PAGE_LOCATOR_DERIVATION_FAILED",
            message="page locator cannot be derived from EvidenceIndex geometry",
        )
    if (
        source.page != block.page
        or locator.model_dump(mode="json") != expected.model_dump(mode="json")
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
    document_block: DocumentBlock | None,
) -> CapabilityValidationResult:
    if document_block is None:
        return CapabilityValidationResult(
            capability="table_cell",
            status="FAIL_DERIVATION",
            issue_code="SOURCE_TABLE_TEXT_UNAVAILABLE",
            message="source block text is required to validate table evidence",
        )
    try:
        expected = derive_table_evidence(
            evidence_index,
            block_id=source.block_id,
            selected_range=quote_match.selected_range,  # type: ignore[arg-type]
            canonical_cell_ids=source.canonical_source_cell_ids,
            block_text=document_block.text,
        )
    except EvidenceReferenceError as exc:
        return CapabilityValidationResult(
            capability="table_cell",
            status="FAIL_INVALID_REFERENCE",
            issue_code="SOURCE_TABLE_REFERENCE_INVALID",
            message=str(exc),
        )
    except LocatorDerivationError as exc:
        return CapabilityValidationResult(
            capability="table_cell",
            status="FAIL_DERIVATION",
            issue_code="SOURCE_TABLE_LOCATOR_DERIVATION_FAILED",
            message=str(exc),
        )
    locator = source.table_locator
    if locator is None:
        return CapabilityValidationResult(
            capability="table_cell",
            status="FAIL_INVALID_REFERENCE",
            issue_code="SOURCE_TABLE_LOCATOR_MISSING",
            message="available table evidence requires a table locator",
        )
    quote_matches = (
        expected.reconstructed_text == source.quote
        if quote_match.match_basis == "exact"
        else normalize_text(expected.reconstructed_text) == normalize_text(source.quote)
    )
    table = next(
        (item for item in evidence_index.tables if item.table_id == block.table_id),
        None,
    )
    if (
        not quote_matches
        or locator.model_dump(mode="json")
        != expected.locator.model_dump(mode="json")
        or expected.page != block.page
        or (table is not None and table.page != expected.page)
        or source.page != expected.page
    ):
        return CapabilityValidationResult(
            capability="table_cell",
            status="FAIL_INVALID_REFERENCE",
            issue_code="SOURCE_TABLE_LOCATOR_INVALID",
            message="table locator does not match quote cell occurrences",
        )
    return CapabilityValidationResult(capability="table_cell", status="PASS")


def _passes_policy(
    block: BlockEvidenceRecord | None,
    results: list[CapabilityValidationResult],
    policy: EvidencePolicy,
) -> bool:
    if policy == "quote_only":
        return True
    if block is None:
        return False
    if not block.expected_capabilities:
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
