from __future__ import annotations

from dataclasses import replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from spectrail.agent.models import ToolSpec
from spectrail.agent.models import ToolResult
from spectrail.chunking import ChunkingConfig
from spectrail.core.io import read_json
from spectrail.pipeline import PipelineConfig, PipelineRunner
from spectrail.tools.base import AgentExecutionContext


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


class RunRequirementExtractionTool:
    name = "run_requirement_extraction"
    description = RUN_REQUIREMENT_EXTRACTION_DESCRIPTION
    side_effects = "task_artifacts"
    input_schema_version = "run_requirement_extraction_args_v1"
    output_schema_version = "agent_tool_result_v1"
    arguments_model = RunRequirementExtractionArgs

    def __init__(
        self,
        *,
        pipeline_runner: PipelineRunner,
        pipeline_config: PipelineConfig,
    ) -> None:
        self.pipeline_runner = pipeline_runner
        self.pipeline_config = pipeline_config

    def invoke(
        self,
        context: AgentExecutionContext,
        arguments: RunRequirementExtractionArgs,
    ) -> ToolResult:
        base_chunking = self.pipeline_config.chunking
        chunking = ChunkingConfig(
            mode=arguments.chunking_mode,
            max_rendered_prompt_chars=(
                arguments.max_rendered_prompt_chars
                if arguments.max_rendered_prompt_chars is not None
                else base_chunking.max_rendered_prompt_chars
            ),
            overlap_blocks=(
                arguments.overlap_blocks
                if arguments.overlap_blocks is not None
                else base_chunking.overlap_blocks
            ),
            min_blocks_for_auto=base_chunking.min_blocks_for_auto,
            fail_fast=False,
        )
        config = replace(
            self.pipeline_config,
            chunking=chunking,
            validation_policy=context.policy.validation_policy,
            evidence_policy=context.policy.evidence_policy,
        )
        result = self.pipeline_runner.extract_within_transaction(
            context.document_path,
            context.task_dir,
            run_generation=context.run_generation,
            config=config,
            parsed_document=context.parsed_document,
        )
        manifest = read_json(result.manifest_path)
        counts = manifest.get("counts", {})
        pipeline_status = manifest.get("status", result.status)
        warning_codes = list(manifest.get("warning_codes", []))
        return ToolResult(
            tool=self.name,
            status=(
                "ok"
                if pipeline_status == "completed"
                else "warning"
                if pipeline_status == "completed_with_warnings"
                else "failed"
            ),
            summary="Deterministic requirement extraction attempt completed.",
            metrics={
                "attempt": 1,
                "pipeline_status": pipeline_status,
                "validated_requirements": counts.get("validated_requirements", 0),
                "quarantined_requirements": counts.get("quarantined_requirements", 0),
                "chunks": counts.get("chunks", 0),
                "chunks_failed": counts.get("chunks_failed", 0),
                "model_items_rejected": counts.get("model_items_rejected", 0),
                "zero_result_reason": manifest.get("zero_result_reason"),
                "readable_final_artifact": result.exported_reqir_path.is_file(),
            },
            warning_codes=warning_codes,
            artifacts={"manifest": result.manifest_path.as_posix()},
            error_code=manifest.get("error_code"),
            retryable=(
                pipeline_status == "failed"
                or "PARTIAL_CHUNK_FAILURE" in warning_codes
            ),
        )
