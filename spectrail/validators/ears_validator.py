from __future__ import annotations

from spectrail.core.models import RequirementIR, ValidationIssue, ValidationReport


EVENT_TRIGGERS = ("当", "时", "后", "when", "WHEN")


class BasicEARSValidator:
    def validate(self, requirements: list[RequirementIR]) -> ValidationReport:
        report = ValidationReport(valid=True)
        for requirement in requirements:
            if not requirement.statement.strip():
                report.add_issue(
                    ValidationIssue(
                        level="warning",
                        code="EARS_EMPTY_STATEMENT",
                        message="statement is empty",
                        requirement_id=requirement.id,
                    )
                )
            if requirement.ears_pattern == "event_driven" and not requirement.condition:
                if not any(trigger in requirement.statement for trigger in EVENT_TRIGGERS):
                    report.add_issue(
                        ValidationIssue(
                            level="warning",
                            code="EARS_EVENT_TRIGGER_MISSING",
                            message="event_driven requirement should include a condition or trigger",
                            requirement_id=requirement.id,
                        )
                    )
        return report
