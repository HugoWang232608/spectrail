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
    ) -> None:
        self.transport = transport
        self.request_profile = request_profile

    def decide(self, planner_input: AgentPlannerInput) -> AgentDecision:
        prompt = build_agent_planner_prompt(planner_input)
        response = self.transport.complete(
            CompletionRequest(
                prompt=prompt,
                request_profile=self.request_profile,
                metadata={"prompt_version": AGENT_PLANNER_PROMPT_VERSION},
            )
        )
        return parse_agent_decision(response.raw_text)
