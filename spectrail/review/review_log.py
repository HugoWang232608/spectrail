from __future__ import annotations

from spectrail.core.models import RequirementIR, ReviewRecord


def append_review_log(requirement: RequirementIR, record: ReviewRecord) -> RequirementIR:
    requirement.review_log.append(record)
    return requirement


def collect_review_log(requirements: list[RequirementIR]) -> list[dict]:
    records = []
    for requirement in requirements:
        for record in requirement.review_log:
            payload = record.model_dump(mode="json")
            payload["requirement_id"] = requirement.id
            records.append(payload)
    return records
