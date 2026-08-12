from __future__ import annotations

from pathlib import Path
from typing import Literal

from spectrail.agent.errors import AgentConfigurationError
from spectrail.agent.planner import AgentPlanner
from spectrail.agent.policy import AgentPolicy
from spectrail.llm.agent_planner import AgentPlannerClient
from spectrail.llm.openai_compatible_transport import OpenAICompatibleTransport
from spectrail.llm.recorded_agent_planner import RecordedAgentPlanner
from spectrail.pipeline import PipelineConfig


DEFAULT_AGENT_ALLOWED_TOOLS = (
    "inspect_extraction_result",
    "run_requirement_extraction",
)


def build_default_agent_policy(pipeline_config: PipelineConfig) -> AgentPolicy:
    policy = AgentPolicy(
        allowed_tools=DEFAULT_AGENT_ALLOWED_TOOLS,
        evidence_policy=pipeline_config.evidence_policy,
        validation_policy=pipeline_config.validation_policy,
        allow_chunking_modes=("auto", "force"),
    )
    prompt_chars = pipeline_config.chunking.max_rendered_prompt_chars
    if not policy.min_prompt_chars <= prompt_chars <= policy.max_prompt_chars:
        raise AgentConfigurationError("AGENT_PROMPT_BUDGET_OUT_OF_RANGE")
    if pipeline_config.chunking.overlap_blocks > policy.max_overlap_blocks:
        raise AgentConfigurationError("AGENT_OVERLAP_OUT_OF_RANGE")
    return policy


def create_agent_planner(
    *,
    planner_mode: Literal["recorded", "live"],
    recorded_fixture: str | Path | None = None,
    model_name: str | None = None,
    insecure: bool = False,
) -> AgentPlanner:
    if planner_mode == "recorded":
        if recorded_fixture is None:
            raise AgentConfigurationError("AGENT_PLANNER_FIXTURE_REQUIRED")
        if model_name is not None:
            raise AgentConfigurationError(
                "AGENT_RECORDED_PLANNER_MODEL_NOT_ALLOWED"
            )
        return RecordedAgentPlanner(recorded_fixture)
    if planner_mode == "live":
        if recorded_fixture is not None:
            raise AgentConfigurationError(
                "AGENT_LIVE_PLANNER_FIXTURE_NOT_ALLOWED"
            )
        transport = OpenAICompatibleTransport(model_name=model_name)
        profile = transport.resolve_request_profile(None, insecure=insecure)
        return AgentPlannerClient(
            transport,
            profile,
            insecure=insecure,
        )
    raise AgentConfigurationError("AGENT_PLANNER_MODE_INVALID")
