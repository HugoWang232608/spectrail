from __future__ import annotations

from spectrail.core.models import RequirementIR, ValidationIssue, ValidationReport


class SchemaValidator:
    def validate(self, requirements: list[RequirementIR]) -> ValidationReport:
        report = ValidationReport(valid=True)
        for requirement in requirements:
            for normalization in requirement.metadata.get("enum_normalizations", []):
                report.add_issue(
                    ValidationIssue(
                        level="warning",
                        code="MODEL_ENUM_NORMALIZED",
                        message=(
                            f"normalized {normalization.get('field')} from "
                            f"{normalization.get('input')} to {normalization.get('normalized')}"
                        ),
                        requirement_id=requirement.id,
                        metadata=normalization,
                    )
                )
            if not requirement.id.strip():
                report.add_issue(self._error("SCHEMA_ID_EMPTY", "id must not be empty", requirement.id))
            if not requirement.statement.strip():
                report.add_issue(
                    self._error("SCHEMA_STATEMENT_EMPTY", "statement must not be empty", requirement.id)
                )
            if not requirement.sources:
                report.add_issue(self._error("SCHEMA_SOURCES_EMPTY", "sources must not be empty", requirement.id))
            if not 0 <= requirement.confidence <= 1:
                report.add_issue(
                    self._error("SCHEMA_CONFIDENCE_RANGE", "confidence must be between 0 and 1", requirement.id)
                )
                requirement.review_status = "needs_recheck"
        return report

    @staticmethod
    def _error(code: str, message: str, requirement_id: str | None) -> ValidationIssue:
        return ValidationIssue(level="error", code=code, message=message, requirement_id=requirement_id)
