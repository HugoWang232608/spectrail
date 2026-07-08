from __future__ import annotations

import difflib
import re
import unicodedata

from spectrail.core.models import DocumentBlock, RequirementIR, SourceSpan, ValidationIssue, ValidationReport


PUNCT_MAP = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "：": ":",
        "；": ";",
        "（": "(",
        "）": ")",
        "！": "!",
        "？": "?",
        "、": ",",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
    }
)


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.translate(PUNCT_MAP)
    normalized = re.sub(r"\s*\|\s*", "|", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


class SourceQuoteValidator:
    pass_statuses = {"PASS_EXACT", "PASS_NORMALIZED"}

    def validate_source(self, source: SourceSpan, blocks_by_id: dict[str, DocumentBlock]) -> SourceSpan:
        block = blocks_by_id.get(source.block_id)
        if block is None:
            source.match_status = "FAIL_NOT_FOUND"
            source.match_score = 0.0
            return source

        if source.quote in block.text:
            source.match_status = "PASS_EXACT"
            source.match_score = 1.0
            return source

        normalized_quote = normalize_text(source.quote)
        normalized_block = normalize_text(block.text)
        if normalized_quote and normalized_quote in normalized_block:
            source.match_status = "PASS_NORMALIZED"
            source.match_score = 0.95
            return source

        score = difflib.SequenceMatcher(None, normalized_quote, normalized_block).ratio()
        if score >= 0.85:
            source.match_status = "WARNING_FUZZY"
            source.match_score = score
            return source

        source.match_status = "FAIL_NOT_FOUND"
        source.match_score = score
        return source

    def validate(
        self, requirements: list[RequirementIR], blocks: list[DocumentBlock]
    ) -> tuple[list[RequirementIR], ValidationReport]:
        blocks_by_id = {block.block_id: block for block in blocks}
        validated: list[RequirementIR] = []
        report = ValidationReport(valid=True)
        for requirement in requirements:
            passed = False
            for index, source in enumerate(requirement.sources):
                requirement.sources[index] = self.validate_source(source, blocks_by_id)
                if requirement.sources[index].match_status in self.pass_statuses:
                    passed = True
            if passed:
                validated.append(requirement)
            else:
                report.add_issue(
                    ValidationIssue(
                        level="error",
                        code="SOURCE_QUOTE_NOT_GROUNDED",
                        message="requirement has no exact or normalized source quote match",
                        requirement_id=requirement.id,
                        source_block_id=requirement.sources[0].block_id if requirement.sources else None,
                    )
                )
        return validated, report
