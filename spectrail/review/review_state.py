from __future__ import annotations

from typing import Any

from spectrail.core.models import RequirementIR, ReviewRecord
from spectrail.review.review_log import append_review_log


EDITABLE_FIELDS = {"statement", "tags", "priority", "metadata"}
STRUCTURAL_FIELDS = {"statement"}
NON_STRUCTURAL_FIELDS = {"tags", "priority", "metadata"}
APPROVE_FROM = {"pending", "needs_recheck"}
REJECT_FROM = {"pending", "needs_recheck", "approved"}


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
        if requirement.review_status not in APPROVE_FROM:
            raise ValueError(f"cannot approve requirement from status: {requirement.review_status}")
        requirement.review_status = "approved"
    elif action == "reject":
        if requirement.review_status not in REJECT_FROM:
            raise ValueError(f"cannot reject requirement from status: {requirement.review_status}")
        requirement.review_status = "rejected"
    elif action == "request_recheck":
        requirement.review_status = "needs_recheck"
    elif action == "restore":
        if requirement.review_status != "rejected":
            raise ValueError(f"cannot restore requirement from status: {requirement.review_status}")
        requirement.review_status = "pending"
    elif action == "edit":
        changed_fields = set(patch)
        unsupported = changed_fields - EDITABLE_FIELDS
        if unsupported:
            fields = ", ".join(sorted(unsupported))
            raise ValueError(f"unsupported edit field(s): {fields}")
        updated = requirement.model_copy(update=patch)
        validated = RequirementIR.model_validate(updated.model_dump(mode="python"))
        for field in changed_fields:
            setattr(requirement, field, getattr(validated, field))
        if changed_fields & STRUCTURAL_FIELDS:
            requirement.review_status = "needs_recheck"
        elif changed_fields & NON_STRUCTURAL_FIELDS:
            requirement.review_status = requirement.review_status
    else:
        raise ValueError(f"unknown review action: {action}")

    requirement.review_revision += 1
    after = requirement.model_dump(mode="json")
    record = ReviewRecord(
        action=action,  # type: ignore[arg-type]
        reviewer=reviewer,
        before=before,
        after=after,
        reason=reason,
    )
    return append_review_log(requirement, record)
