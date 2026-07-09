import pytest

from spectrail.core.models import RequirementIR, SourceSpan
from spectrail.review.review_state import apply_review_action


def req(status: str = "pending") -> RequirementIR:
    return RequirementIR(
        id="REQ-0001",
        statement="系统应记录事件。",
        sources=[SourceSpan(document_id="doc_001", block_id="blk_0001", quote="系统应记录事件。")],
        confidence=0.9,
        review_status=status,
    )


def test_approve_and_reject_transitions():
    approved = apply_review_action(req(), "approve", reviewer="tester")
    assert approved.review_status == "approved"
    assert approved.review_log[-1].action == "approve"

    rejected = apply_review_action(approved, "reject", reason="duplicate")
    assert rejected.review_status == "rejected"
    assert rejected.review_log[-1].action == "reject"


def test_structural_edit_requires_recheck():
    requirement = apply_review_action(req("approved"), "edit", {"statement": "系统应记录完整事件。"})
    assert requirement.review_status == "needs_recheck"


def test_non_structural_edit_keeps_status():
    requirement = apply_review_action(req("approved"), "edit", {"tags": ["audit"]})
    assert requirement.review_status == "approved"
    assert requirement.tags == ["audit"]


def test_restore_rejected_to_pending():
    requirement = apply_review_action(req("rejected"), "restore")
    assert requirement.review_status == "pending"


def test_rejected_cannot_be_approved_without_restore():
    with pytest.raises(ValueError, match="cannot approve"):
        apply_review_action(req("rejected"), "approve")


def test_edit_rejects_sources_patch():
    with pytest.raises(ValueError, match="unsupported edit field"):
        apply_review_action(
            req(),
            "edit",
            {
                "sources": [
                    {
                        "document_id": "doc_001",
                        "block_id": "blk_0002",
                        "quote": "new quote",
                    }
                ]
            },
        )
