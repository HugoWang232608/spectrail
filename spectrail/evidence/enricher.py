from __future__ import annotations

from spectrail.core.models import DocumentBlock, RequirementIR
from spectrail.evidence.locator_derivation import (
    derive_page_locator,
    derive_table_evidence,
)
from spectrail.evidence.models import (
    CapabilityValidationResult,
    EvidenceIndex,
    TextLocator,
    aggregate_locator_status,
)
from spectrail.evidence.quote_matcher import QuoteMatchRegistry


class SourceEvidenceEnricher:
    def enrich(
        self,
        requirements: list[RequirementIR],
        evidence_index: EvidenceIndex,
        quote_matches: QuoteMatchRegistry,
        document_blocks: list[DocumentBlock] | None = None,
    ) -> list[RequirementIR]:
        blocks_by_id = {block.block_id: block for block in evidence_index.blocks}
        document_blocks_by_id = {
            block.block_id: block for block in document_blocks or []
        }
        for requirement in requirements:
            for source in requirement.sources:
                if source.source_evidence_key is None:
                    raise ValueError("source_evidence_key is required for evidence enrichment")
                result = quote_matches.require(source.source_evidence_key)
                block = blocks_by_id.get(source.block_id)
                expected = block.expected_capabilities if block is not None else ["text_range"]
                source.text_locator = None
                source.provisional_text_locator = None
                source.page_locator = None
                source.table_locator = None
                source.locator_score = result.score

                if block is None:
                    text_status = "FAIL_INVALID_REFERENCE"
                    issue_code = "SOURCE_BLOCK_NOT_FOUND"
                elif result.status == "UNIQUE_MATCH" and result.selected_range is not None:
                    source.text_locator = TextLocator(
                        block_id=source.block_id,
                        start=result.selected_range.start,
                        end=result.selected_range.end,
                        match_basis=result.match_basis,  # type: ignore[arg-type]
                    )
                    text_status = "PASS"
                    issue_code = None
                elif result.status == "AMBIGUOUS_MATCH":
                    if result.provisional_range is not None:
                        source.provisional_text_locator = TextLocator(
                            block_id=source.block_id,
                            start=result.provisional_range.start,
                            end=result.provisional_range.end,
                            match_basis=result.match_basis,  # type: ignore[arg-type]
                        )
                    text_status = "WARNING_AMBIGUOUS"
                    issue_code = "QUOTE_OCCURRENCE_AMBIGUOUS"
                else:
                    text_status = "FAIL_DERIVATION"
                    issue_code = "QUOTE_RANGE_NOT_FOUND"

                source.capability_results = [
                    CapabilityValidationResult(
                        capability="text_range",
                        status=text_status,
                        issue_code=issue_code,
                    )
                ]
                source.locator_status = aggregate_locator_status(
                    expected,
                    source.capability_results,
                )
                if (
                    block is not None
                    and result.selected_range is not None
                    and result.status == "UNIQUE_MATCH"
                ):
                    table_evidence = None
                    document_block = document_blocks_by_id.get(source.block_id)
                    if (
                        "table_cell" in block.available_capabilities
                        and source.canonical_source_cell_ids
                        and document_block is not None
                    ):
                        try:
                            table_evidence = derive_table_evidence(
                                evidence_index,
                                block_id=source.block_id,
                                selected_range=result.selected_range,
                                canonical_cell_ids=source.canonical_source_cell_ids,
                                block_text=document_block.text,
                            )
                            source.table_locator = table_evidence.locator
                        except ValueError:
                            table_evidence = None
                    if "page_region" in block.available_capabilities:
                        source.page_locator = derive_page_locator(
                            evidence_index,
                            block_id=source.block_id,
                            selected_range=result.selected_range,
                            table_evidence=table_evidence,
                        )
                    source.page = block.page
        return requirements
