from __future__ import annotations

from spectrail.core.models import DocumentBlock, RequirementIR, SourceSpan, ValidationIssue, ValidationReport
from spectrail.evidence.quote_matcher import QuoteMatchRegistry, normalize_text


class SourceQuoteValidator:
    pass_statuses = {"PASS_EXACT", "PASS_NORMALIZED"}

    def validate_source(
        self,
        source: SourceSpan,
        blocks_by_id: dict[str, DocumentBlock],
        quote_matches: QuoteMatchRegistry,
    ) -> SourceSpan:
        if source.source_evidence_key is None:
            raise ValueError("source_evidence_key is required for quote validation")
        result = quote_matches.require(source.source_evidence_key)
        block = blocks_by_id.get(source.block_id)
        if block is None:
            source.match_status = "FAIL_NOT_FOUND"
            source.match_score = 0.0
            return source

        source.match_score = result.score
        if result.status != "NO_MATCH" and result.match_basis == "exact":
            source.match_status = "PASS_EXACT"
            return source
        if result.status != "NO_MATCH" and result.match_basis == "normalized":
            source.match_status = "PASS_NORMALIZED"
            return source
        if result.score >= 0.85:
            source.match_status = "WARNING_FUZZY"
            return source

        source.match_status = "FAIL_NOT_FOUND"
        return source

    def validate(
        self,
        requirements: list[RequirementIR],
        blocks: list[DocumentBlock],
        quote_matches: QuoteMatchRegistry,
    ) -> tuple[list[RequirementIR], ValidationReport]:
        blocks_by_id = {block.block_id: block for block in blocks}
        validated: list[RequirementIR] = []
        report = ValidationReport(valid=True)
        for requirement in requirements:
            source_passes: list[bool] = []
            for index, source in enumerate(requirement.sources):
                requirement.sources[index] = self.validate_source(
                    source, blocks_by_id, quote_matches
                )
                validated_source = requirement.sources[index]
                passed = validated_source.match_status in self.pass_statuses
                source_passes.append(passed)
                if not passed:
                    report.add_issue(
                        ValidationIssue(
                            level="error",
                            code="SOURCE_QUOTE_NOT_GROUNDED",
                            message="source quote has no exact or normalized match",
                            requirement_id=requirement.id,
                            source_block_id=validated_source.block_id,
                            metadata={
                                "source_index": index,
                                "source_evidence_key": validated_source.source_evidence_key,
                                "match_status": validated_source.match_status,
                                "match_score": validated_source.match_score,
                            },
                        )
                    )
            if source_passes and all(source_passes):
                validated.append(requirement)
            elif not source_passes:
                report.add_issue(
                    ValidationIssue(
                        level="error",
                        code="SOURCE_QUOTE_MISSING",
                        message="requirement contains no source quotes",
                        requirement_id=requirement.id,
                    )
                )
        return validated, report
