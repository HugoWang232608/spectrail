from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from spectrail.evidence.models import BoundingBox
from spectrail.llm.request_profile import ModelRequestProfile


class GoldSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    quote: str
    page: int | None = None
    table_id: str | None = None
    cell_ids: list[str] = Field(default_factory=list)
    bbox: BoundingBox | None = None
    bbox_iou_threshold: float = 0.8

    @model_validator(mode="after")
    def validate_locator_gold(self) -> "GoldSource":
        if self.page is not None and self.page < 1:
            raise ValueError("gold source page must be 1-based")
        if len(set(self.cell_ids)) != len(self.cell_ids):
            raise ValueError("gold source cell IDs must be unique")
        if self.cell_ids and self.table_id is None:
            raise ValueError("gold source cell IDs require table_id")
        if not 0 <= self.bbox_iou_threshold <= 1:
            raise ValueError("bbox_iou_threshold must be between 0 and 1")
        if self.bbox is not None and self.page is None:
            raise ValueError("gold source bbox requires page")
        return self


class GoldRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gold_id: str
    statement: str
    accepted_statements: list[str] = Field(default_factory=list)
    sources: list[GoldSource]
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None


class GoldPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: dict[str, Any] = Field(default_factory=dict)
    items: list[GoldRequirement]


class EvaluationRequestProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_adapter: str
    provider_endpoint_id: str
    model_name: str
    temperature: float = 0.0
    response_format: dict[str, Any] | None = None
    safe_request_options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_runtime_profile(self) -> "EvaluationRequestProfile":
        self.to_runtime()
        return self

    def to_runtime(self) -> ModelRequestProfile:
        return ModelRequestProfile(**self.model_dump(mode="python"))


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    document: str
    gold: str
    scope_block_ids: list[str] = Field(default_factory=list)
    allow_empty_gold_scope: bool = False
    model_mode: Literal["mock", "recorded", "live"] = "mock"
    model_name: str | None = None
    request_profile: EvaluationRequestProfile | None = None
    recorded_fixture: str | None = None
    chunking_mode: Literal["off", "auto", "force"] = "auto"
    max_rendered_prompt_chars: int = 16000
    overlap_blocks: int = 1
    validation_policy: Literal["strict", "quarantine"] = "strict"
    allowed_pipeline_statuses: list[str] = Field(default_factory=lambda: ["completed"])
    allowed_zero_result_reasons: list[str | None] = Field(default_factory=lambda: [None])
    thresholds: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_model_identity(self) -> "EvaluationCase":
        if (
            self.request_profile is not None
            and self.model_name is not None
            and self.model_name != self.request_profile.model_name
        ):
            raise ValueError("model_name must match request_profile.model_name")
        return self
