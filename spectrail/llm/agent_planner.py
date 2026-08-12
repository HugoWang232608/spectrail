from __future__ import annotations

from spectrail.agent.planner import (
    AGENT_PLANNER_PROMPT_VERSION,
    AgentDecision,
    AgentPlannerInput,
    build_agent_planner_prompt,
    parse_agent_decision,
)
from spectrail.llm.request_profile import ModelRequestProfile
from spectrail.llm.transport import CompletionRequest, CompletionTransport


class AgentPlannerClient:
    planner_mode = "live"

    def __init__(
        self,
        transport: CompletionTransport,
        request_profile: ModelRequestProfile,
        *,
        insecure: bool = False,
    ) -> None:
        self.transport = transport
        self.request_profile = request_profile
        self.insecure = insecure

    def decide(self, planner_input: AgentPlannerInput) -> AgentDecision:
        prompt = build_agent_planner_prompt(planner_input)
        metadata = {"prompt_version": AGENT_PLANNER_PROMPT_VERSION}
        if self.insecure:
            metadata["insecure"] = True
        response = self.transport.complete(
            CompletionRequest(
                prompt=prompt,
                request_profile=self.request_profile,
                metadata=metadata,
            )
        )
        return parse_agent_decision(response.raw_text)
