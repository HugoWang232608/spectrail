from __future__ import annotations

from typing import Any

from spectrail.core.models import RequirementIR, ReviewRecord
from spectrail.review.review_log import append_review_log


STRUCTURAL_FIELDS = {"statement", "sources", "ears_pattern", "type"}
NON_STRUCTURAL_FIELDS = {"tags", "priority", "metadata"}


def apply_review_action(
    requirement: RequirementIR,
    action: str,
    patch: dict[str, Any] | None = None,
    reviewer: str | None = None,
    reason: str | None = None,
) -> RequirementIR:
    patch = patch or {}
    before = requirement.model_dump(mode="json")

    if action == "approve":
        requirement.review_status = "approved"
    elif action == "reject":
        requirement.review_status = "rejected"
    elif action == "request_recheck":
        requirement.review_status = "needs_recheck"
    elif action == "restore":
        if requirement.review_status == "rejected":
            requirement.review_status = "pending"
    elif action == "edit":
        changed_fields = set(patch)
        for field, value in patch.items():
            if not hasattr(requirement, field):
                continue
            setattr(requirement, field, value)
        if changed_fields & STRUCTURAL_FIELDS:
            requirement.review_status = "needs_recheck"
        elif changed_fields & NON_STRUCTURAL_FIELDS:
            requirement.review_status = requirement.review_status
    else:
        raise ValueError(f"unknown review action: {action}")

    after = requirement.model_dump(mode="json")
    record = ReviewRecord(
        action=action,  # type: ignore[arg-type]
        reviewer=reviewer,
        before=before,
        after=after,
        reason=reason,
    )
    return append_review_log(requirement, record)
