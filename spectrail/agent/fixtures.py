from __future__ import annotations

from pathlib import Path

from spectrail.agent.errors import AgentConfigurationError


BUNDLED_AGENT_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "agent"
)


def resolve_bundled_agent_fixture(name: object) -> Path | None:
    """Resolve one package-owned Agent fixture without accepting filesystem paths."""

    if name is None:
        return None
    if (
        not isinstance(name, str)
        or not name
        or "/" in name
        or "\\" in name
        or name in {".", ".."}
    ):
        raise AgentConfigurationError("AGENT_PLANNER_FIXTURE_INVALID")
    fixture = BUNDLED_AGENT_FIXTURE_ROOT / name
    if fixture.is_symlink():
        raise AgentConfigurationError("AGENT_PLANNER_FIXTURE_INVALID")
    if not fixture.is_file():
        raise AgentConfigurationError("AGENT_PLANNER_FIXTURE_NOT_FOUND")
    return fixture


def resolve_recorded_agent_fixture(value: str | Path) -> Path:
    """Resolve an explicit path, or fall back to a bundled fixture filename."""

    candidate = Path(value)
    if candidate.is_file() or candidate.parent != Path("."):
        return candidate
    bundled = resolve_bundled_agent_fixture(candidate.name)
    assert bundled is not None
    return bundled
