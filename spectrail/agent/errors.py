from __future__ import annotations


class AgentError(ValueError):
    """Base error for bounded agent orchestration."""


class AgentPlannerResponseError(AgentError):
    """Raised when planner output is not valid AgentDecision JSON."""


class AgentPlannerFixtureError(AgentError):
    """Raised when deterministic planner replay cannot continue safely."""
