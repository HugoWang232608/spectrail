from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from spectrail.agent.models import ToolResult
from spectrail.tools.base import AgentExecutionContext


class ProfileDocumentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileDocumentTool:
    name = "profile_document"
    description = "Return a deterministic, planner-safe document profile summary."
    side_effects = "none"
    input_schema_version = "profile_document_args_v1"
    output_schema_version = "agent_tool_result_v1"
    arguments_model = ProfileDocumentArgs

    def invoke(
        self,
        context: AgentExecutionContext,
        arguments: ProfileDocumentArgs,
    ) -> ToolResult:
        del arguments
        profile = context.document_profile
        warning_codes = list(
            dict.fromkeys(warning.split(":", 1)[0] for warning in profile.warnings)
        )
        return ToolResult(
            tool=self.name,
            status="warning" if profile.warnings else "ok",
            summary="Document profile is available.",
            metrics={
                "block_count": profile.block_count,
                "section_count": profile.section_count,
                "page_count": profile.page_count,
                "table_block_count": profile.table_block_count,
                "estimated_prompt_chars": profile.estimated_prompt_chars,
                "parser_warnings": len(profile.warnings),
            },
            warning_codes=warning_codes,
        )
