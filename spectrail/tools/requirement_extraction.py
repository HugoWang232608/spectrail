from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from spectrail.agent.models import ToolSpec


RUN_REQUIREMENT_EXTRACTION_DESCRIPTION = (
    "Run the deterministic requirement extraction pipeline with policy-bounded "
    "chunking arguments."
)


class RunRequirementExtractionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunking_mode: Literal["auto", "force", "off"] = "auto"
    max_rendered_prompt_chars: int | None = Field(default=None, gt=0)
    overlap_blocks: int | None = Field(default=None, ge=0)


def requirement_extraction_tool_spec() -> ToolSpec:
    """Return the stable contract used before the M6.3 executor is wired."""

    return ToolSpec(
        name="run_requirement_extraction",
        description=RUN_REQUIREMENT_EXTRACTION_DESCRIPTION,
        side_effects="task_artifacts",
        input_schema_version="run_requirement_extraction_args_v1",
        input_schema=RunRequirementExtractionArgs.model_json_schema(),
        output_schema_version="agent_tool_result_v1",
    )
