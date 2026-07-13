from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GoldSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    quote: str


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


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    document: str
    gold: str
    scope_block_ids: list[str] = Field(default_factory=list)
    model_mode: Literal["mock", "recorded", "live"] = "mock"
    model_name: str | None = None
    recorded_fixture: str | None = None
    chunking_mode: Literal["off", "auto", "force"] = "auto"
    max_rendered_prompt_chars: int = 16000
    overlap_blocks: int = 1
    validation_policy: Literal["strict", "quarantine"] = "strict"
    allowed_pipeline_statuses: list[str] = Field(default_factory=lambda: ["completed"])
    allowed_zero_result_reasons: list[str | None] = Field(default_factory=lambda: [None])
    thresholds: dict[str, float] = Field(default_factory=dict)
