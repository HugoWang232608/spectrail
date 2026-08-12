from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    orchestration_mode: Literal["fixed", "agent"] = "fixed"
    planner_mode: Literal["recorded", "live"] | None = None
    planner_fixture: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
    )
    planner_model_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_orchestration(self) -> "TaskCreateRequest":
        planner_options = (
            self.planner_mode,
            self.planner_fixture,
            self.planner_model_name,
        )
        if self.orchestration_mode == "fixed":
            if any(value is not None for value in planner_options):
                raise ValueError("planner options require orchestration_mode=agent")
            return self
        if self.fail_fast:
            raise ValueError("Agent orchestration requires fail_fast=false")
        if self.max_rendered_prompt_chars > 32_000:
            raise ValueError("Agent prompt budget must not exceed 32000")
        if self.overlap_blocks > 3:
            raise ValueError("Agent overlap_blocks must not exceed 3")
        if self.planner_mode is None:
            raise ValueError("Agent orchestration requires planner_mode")
        if self.planner_mode == "recorded":
            if self.planner_fixture is None:
                raise ValueError("recorded Agent planner requires planner_fixture")
            if self.planner_model_name is not None:
                raise ValueError(
                    "recorded Agent planner does not accept planner_model_name"
                )
        elif self.planner_fixture is not None:
            raise ValueError("live Agent planner does not accept planner_fixture")
        return self


class TaskResponse(BaseModel):
    task_id: str
    status: str
    output_dir: str


class DocumentUploadResponse(BaseModel):
    task_id: str
    status: str
    filename: str


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str
    goal: str
    model_mode: str
    status: str
    run_generation: int = Field(ge=0)
    created_at: str
    updated_at: str
    input_document: str | None
    original_filename: str | None
    output_dir: str
    pipeline_config: dict[str, Any] = Field(default_factory=dict)


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str
    # Generation zero represents a readable legacy snapshot produced before
    # task generations were persisted. New pipeline runs always start at one.
    run_generation: int = Field(default=0, ge=0)
    status: str
    input_document: str
    output_dir: str
    model_mode: str
    started_at: str
    completed_at: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    warning_codes: list[str] = Field(default_factory=list)
    zero_result_reason: str | None = None


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    run_generation: int = Field(ge=0)
    task: TaskRecord
    manifest: RunManifest | None = None

    @model_validator(mode="after")
    def validate_generation_consistency(self) -> "TaskStatusResponse":
        if self.task_id != self.task.task_id:
            raise ValueError("task response task IDs do not match")
        if self.run_generation != self.task.run_generation:
            raise ValueError("task response run generations do not match")
        if self.manifest is not None:
            if self.task_id != self.manifest.task_id:
                raise ValueError("task response manifest task ID does not match")
            if self.run_generation != self.manifest.run_generation:
                raise ValueError(
                    "task response manifest run generation does not match"
                )
        return self


class TaskRunResponse(BaseModel):
    task_id: str
    status: str
    run_generation: int = Field(ge=1)
    manifest: RunManifest

    @model_validator(mode="after")
    def validate_generation_consistency(self) -> "TaskRunResponse":
        if self.task_id != self.manifest.task_id:
            raise ValueError("run response manifest task ID does not match")
        if self.run_generation != self.manifest.run_generation:
            raise ValueError(
                "run response manifest run generation does not match"
            )
        return self


class ReviewRequest(BaseModel):
    requirement_id: str
    expected_run_generation: int = Field(ge=0)
    expected_review_revision: int = Field(ge=0)
    action: Literal["approve", "reject", "edit", "restore", "request_recheck"]
    patch: dict[str, Any] = Field(default_factory=dict)
    reviewer: str | None = None
    reason: str | None = None


class ReviewResponse(BaseModel):
    task_id: str
    run_generation: int = Field(ge=0)
    requirement_id: str
    review_revision: int = Field(ge=1)
    action: str
    review_status: str
