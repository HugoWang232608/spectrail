from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from spectrail.evidence.models import (
    CapabilityValidationResult,
    LocatorStatus,
    PageLocator,
    TableLocator,
    TextLocator,
)


RequirementType = Literal[
    "functional",
    "non_functional",
    "interface",
    "constraint",
    "business",
    "unknown",
]
EARSPattern = Literal[
    "ubiquitous",
    "event_driven",
    "state_driven",
    "optional",
    "unwanted_behavior",
    "unknown",
]
Priority = Literal["high", "medium", "low", "unknown"]
VerificationMethod = Literal["test", "inspection", "analysis", "demonstration", "unknown"]
ReviewStatus = Literal["pending", "approved", "rejected", "needs_recheck"]
ReviewAction = Literal["approve", "edit", "reject", "request_recheck", "restore"]
MatchStatus = Literal[
    "UNVERIFIED",
    "PASS_EXACT",
    "PASS_NORMALIZED",
    "WARNING_FUZZY",
    "FAIL_NOT_FOUND",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class DocumentBlock(BaseModel):
    block_id: str
    document_id: str
    type: Literal["heading", "paragraph", "table", "list", "code", "blockquote"]
    text: str
    page: int | None = None
    section_path: list[str] = Field(default_factory=list)
    order: int
    metadata: dict = Field(default_factory=dict)


class SourceSpan(BaseModel):
    document_id: str
    document_name: str | None = None
    page: int | None = None
    section: str | None = None
    section_path: list[str] = Field(default_factory=list)
    block_id: str
    quote: str
    match_status: MatchStatus = "UNVERIFIED"
    match_score: float | None = None
    bbox: list[float] | None = None
    table_cell: str | None = None
    image_region: str | None = None
    text_locator: TextLocator | None = None
    page_locator: PageLocator | None = None
    table_locator: TableLocator | None = None
    source_cell_ids_raw: list[str] = Field(default_factory=list)
    canonical_source_cell_ids: list[str] = Field(default_factory=list)
    source_evidence_key: str | None = None
    provisional_text_locator: TextLocator | None = None
    locator_status: LocatorStatus = "UNVERIFIED"
    capability_results: list[CapabilityValidationResult] = Field(default_factory=list)
    locator_score: float | None = None


class ReviewRecord(BaseModel):
    action: ReviewAction
    reviewer: str | None = None
    before: dict = Field(default_factory=dict)
    after: dict = Field(default_factory=dict)
    reason: str | None = None
    created_at: datetime = Field(default_factory=now_utc)


class RequirementIR(BaseModel):
    id: str
    version: int = 1
    title: str | None = None
    type: RequirementType = "unknown"
    ears_pattern: EARSPattern = "unknown"
    statement: str
    subject: str | None = None
    condition: str | None = None
    response: str | None = None
    priority: Priority = "unknown"
    verification_method: VerificationMethod = "unknown"
    sources: list[SourceSpan] = Field(default_factory=list)
    confidence: float = 0.0
    grounding_score: float | None = None
    review_status: ReviewStatus = "pending"
    duplicate_group_id: str | None = None
    possible_duplicate_ids: list[str] = Field(default_factory=list)
    derived_from: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    review_log: list[ReviewRecord] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class ReqIRPackage(BaseModel):
    schema_version: Literal["reqir_v2"] = "reqir_v2"
    metadata: dict[str, Any] = Field(default_factory=dict)
    items: list[RequirementIR] = Field(default_factory=list)


class PlanStep(BaseModel):
    id: str
    tool: str
    depends_on: list[str] = Field(default_factory=list)
    output: str | None = None
    input_ref: dict = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)
    reason: str | None = None


class PlanSpec(BaseModel):
    task_id: str
    goal: str
    planner: str = "fixed_workflow_v1"
    model_mode: str
    input_document: str
    steps: list[PlanStep]


class ValidationIssue(BaseModel):
    level: Literal["error", "warning", "info"]
    code: str
    message: str
    requirement_id: str | None = None
    source_block_id: str | None = None
    metadata: dict = Field(default_factory=dict)


class ValidationReport(BaseModel):
    valid: bool = True
    issues: list[ValidationIssue] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    def add_issue(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)
        if issue.level == "error":
            self.valid = False
