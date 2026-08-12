from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from spectrail.agent.models import ToolResult
from spectrail.core.io import read_json
from spectrail.tools.base import AgentExecutionContext


class InspectExtractionResultArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InspectExtractionResultTool:
    name = "inspect_extraction_result"
    description = (
        "Inspect the latest extraction attempt and expose additional deterministic "
        "failure and validation facts."
    )
    side_effects = "none"
    input_schema_version = "inspect_extraction_result_args_v1"
    output_schema_version = "agent_tool_result_v1"
    arguments_model = InspectExtractionResultArgs

    def invoke(
        self,
        context: AgentExecutionContext,
        arguments: InspectExtractionResultArgs,
    ) -> ToolResult:
        del arguments
        return self.inspect_task_dir(
            context.task_dir,
            expected_run_generation=context.run_generation,
        )

    def inspect_task_dir(
        self,
        task_dir,
        *,
        expected_run_generation: int | None = None,
    ) -> ToolResult:
        manifest = read_json(task_dir / "run_manifest.json")
        if (
            expected_run_generation is not None
            and manifest.get("run_generation") != expected_run_generation
        ):
            raise ValueError("AGENT_INSPECTION_GENERATION_MISMATCH")
        chunks_path = task_dir / "parsed" / "chunks.json"
        chunks = read_json(chunks_path) if chunks_path.is_file() else []
        counts = manifest.get("counts", {})
        base_warnings = list(manifest.get("warning_codes", []))
        chunk_warnings = {
            warning
            for chunk in chunks
            for warning in chunk.get("warnings", [])
            if isinstance(warning, str)
        }
        partial_chunk_failure = (
            counts.get("chunks_failed", 0) > 0
            or "PARTIAL_CHUNK_FAILURE" in base_warnings
        )
        chunk_prompt_over_budget = bool(
            {"CHUNK_PROMPT_OVER_BUDGET", "CHUNK_OVERSIZED_BLOCK"}
            & chunk_warnings
        )
        all_candidates_quarantined = (
            counts.get("validated_requirements", 0) == 0
            and counts.get("quarantined_requirements", 0) > 0
        )
        no_requirements_found = manifest.get("zero_result_reason") in {
            "NO_REQUIREMENTS_FOUND",
            "NO_VALID_MODEL_ITEMS",
        }
        facts: list[str] = []
        if partial_chunk_failure:
            facts.append("PARTIAL_CHUNK_FAILURE")
        if chunk_prompt_over_budget:
            facts.append("CHUNK_PROMPT_OVER_BUDGET")
        if all_candidates_quarantined:
            facts.append("ALL_CANDIDATES_QUARANTINED")
        if no_requirements_found:
            facts.append("NO_REQUIREMENTS_FOUND")
        if counts.get("model_items_rejected", 0):
            facts.append("MODEL_ITEMS_REJECTED")
        warning_codes = list(dict.fromkeys([*base_warnings, *facts]))
        pipeline_status = manifest.get("status", "failed")
        return ToolResult(
            tool=self.name,
            status=(
                "ok"
                if pipeline_status == "completed" and not warning_codes
                else "failed"
                if pipeline_status == "failed"
                else "warning"
            ),
            summary="Latest extraction diagnostics are available.",
            metrics={
                "pipeline_status": pipeline_status,
                "chunks_completed": counts.get("chunks_completed", 0),
                "chunks_failed": counts.get("chunks_failed", 0),
                "accepted_candidates": counts.get("model_items_accepted", 0),
                "model_items_rejected": counts.get("model_items_rejected", 0),
                "validated_requirements": counts.get("validated_requirements", 0),
                "quarantined_requirements": counts.get("quarantined_requirements", 0),
                "source_quote_failed": counts.get("source_quote_failed", 0),
                "source_locator_failed": counts.get("source_locator_failed", 0),
                "partial_chunk_failure": partial_chunk_failure,
                "chunk_prompt_over_budget": chunk_prompt_over_budget,
                "all_candidates_quarantined": all_candidates_quarantined,
                "no_requirements_found": no_requirements_found,
                "zero_result_reason": manifest.get("zero_result_reason"),
                "readable_final_artifact": (
                    task_dir / "exports" / "reqir.json"
                ).is_file(),
            },
            warning_codes=warning_codes,
            error_code=manifest.get("error_code"),
            retryable=partial_chunk_failure or chunk_prompt_over_budget,
        )
