from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from spectrail.agent.errors import AgentPlannerFixtureError
from spectrail.agent.models import AgentModel
from spectrail.agent.planner import (
    AGENT_PLANNER_PROMPT_VERSION,
    AgentDecision,
    AgentPlannerInput,
    build_agent_planner_request_fingerprint,
)
from spectrail.llm.request_profile import ModelRequestProfile


class RecordedPlannerMetadata(AgentModel):
    planner_prompt_version: Literal["agent_planner_v2_json_contract"]
    request_profile: dict


class RecordedPlannerStep(AgentModel):
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    response: AgentDecision


class RecordedPlannerFixture(AgentModel):
    schema_version: Literal["agent_planner_fixture_v1"]
    metadata: RecordedPlannerMetadata
    steps: list[RecordedPlannerStep] = Field(min_length=1)


class RecordedAgentPlanner:
    planner_mode = "recorded"

    def __init__(self, fixture_path: str | Path) -> None:
        self.fixture_path = Path(fixture_path)
        try:
            raw = self.fixture_path.read_text(encoding="utf-8")
            fixture = RecordedPlannerFixture.model_validate(json.loads(raw))
            request_profile = ModelRequestProfile(**fixture.metadata.request_profile)
        except (OSError, json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            raise AgentPlannerFixtureError("AGENT_PLANNER_FIXTURE_INVALID") from exc
        if fixture.metadata.planner_prompt_version != AGENT_PLANNER_PROMPT_VERSION:
            raise AgentPlannerFixtureError("AGENT_PLANNER_FIXTURE_PROMPT_MISMATCH")
        self.fixture = fixture
        self.request_profile = request_profile
        self._position = 0

    @property
    def steps_used(self) -> int:
        return self._position

    def decide(self, planner_input: AgentPlannerInput) -> AgentDecision:
        if self._position >= len(self.fixture.steps):
            raise AgentPlannerFixtureError("AGENT_PLANNER_FIXTURE_EXHAUSTED")
        step = self.fixture.steps[self._position]
        actual = build_agent_planner_request_fingerprint(
            planner_input,
            self.request_profile,
        )
        if step.request_fingerprint != actual:
            raise AgentPlannerFixtureError("AGENT_PLANNER_FIXTURE_MISMATCH")
        self._position += 1
        return step.response.model_copy(deep=True)

    def assert_consumed(self) -> None:
        unused = len(self.fixture.steps) - self._position
        if unused:
            raise AgentPlannerFixtureError(
                f"AGENT_PLANNER_FIXTURE_UNUSED_STEPS: {unused}"
            )
