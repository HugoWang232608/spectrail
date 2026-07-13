from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    goal: str = "extract_requirements"
    model_mode: Literal["mock", "recorded"] = "mock"
    chunking_mode: Literal["auto", "force", "off"] = "auto"
    max_rendered_prompt_chars: int = Field(default=16000, ge=1000)
    overlap_blocks: int = Field(default=1, ge=0, le=5)
    validation_policy: Literal["strict", "quarantine"] = "strict"
    evidence_policy: Literal[
        "quote_only", "structured_if_available", "structured_required"
    ] = "structured_if_available"
    fail_fast: bool = False


class TaskResponse(BaseModel):
    task_id: str
    status: str
    output_dir: str


class DocumentUploadResponse(BaseModel):
    task_id: str
    status: str
    filename: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    task: dict[str, Any]
    manifest: dict[str, Any] | None = None


class TaskRunResponse(BaseModel):
    task_id: str
    status: str
    manifest: dict[str, Any]


class ReviewRequest(BaseModel):
    requirement_id: str
    action: Literal["approve", "reject", "edit", "restore", "request_recheck"]
    patch: dict[str, Any] = Field(default_factory=dict)
    reviewer: str | None = None
    reason: str | None = None


class ReviewResponse(BaseModel):
    task_id: str
    requirement_id: str
    action: str
    review_status: str
