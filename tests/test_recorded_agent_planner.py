from __future__ import annotations

import json
from pathlib import Path

import pytest

from spectrail.agent.planner import (
    FinishDecision,
    build_agent_planner_request_fingerprint,
)
from spectrail.llm.recorded_agent_planner import (
    RecordedAgentPlanner,
)
from spectrail.agent.errors import AgentPlannerFixtureError as RecordedPlannerFixtureError
from tests.test_agent_planner import (
    clean_completed_planner_input,
    planner_input,
    planner_profile,
)


def _write_fixture(path: Path, fingerprints: list[str]) -> None:
    responses = [
        {
            "action": "invoke_tool",
            "tool": "run_requirement_extraction",
            "arguments": {"chunking_mode": "auto"},
            "reason": "Use the bounded default strategy.",
        },
        {
            "action": "finish",
            "outcome": "completed",
            "reason": "The latest deterministic observation is complete.",
        },
    ]
    path.write_text(
        json.dumps(
            {
                "schema_version": "agent_planner_fixture_v1",
                "metadata": {
                    "planner_prompt_version": "agent_planner_v2_json_contract",
                    "request_profile": planner_profile().to_dict(),
                },
                "steps": [
                    {
                        "request_fingerprint": fingerprint,
                        "response": response,
                    }
                    for fingerprint, response in zip(fingerprints, responses)
                ],
            }
        ),
        encoding="utf-8",
    )


def test_recorded_agent_planner_replays_exact_fingerprints(tmp_path: Path):
    first_input = planner_input()
    second_input = clean_completed_planner_input()
    fixture = tmp_path / "planner.json"
    _write_fixture(
        fixture,
        [
            build_agent_planner_request_fingerprint(first_input, planner_profile()),
            build_agent_planner_request_fingerprint(second_input, planner_profile()),
        ],
    )
    planner = RecordedAgentPlanner(fixture)

    first = planner.decide(first_input)
    second = planner.decide(second_input)
    planner.assert_consumed()

    assert first.action == "invoke_tool"
    assert isinstance(second, FinishDecision)
    assert planner.steps_used == 2


def test_checked_in_recorded_agent_fixture_is_current():
    planner = RecordedAgentPlanner(
        "spectrail/fixtures/agent/sample_srs_agent.json"
    )

    first = planner.decide(planner_input())
    second = planner.decide(clean_completed_planner_input())
    planner.assert_consumed()

    assert first.action == "invoke_tool"
    assert isinstance(second, FinishDecision)


def test_recorded_agent_planner_rejects_fingerprint_mismatch(tmp_path: Path):
    fixture = tmp_path / "planner.json"
    _write_fixture(fixture, ["0" * 64])
    planner = RecordedAgentPlanner(fixture)

    with pytest.raises(RecordedPlannerFixtureError, match="FIXTURE_MISMATCH"):
        planner.decide(planner_input())
    assert planner.steps_used == 0


def test_recorded_agent_planner_rejects_fixture_exhaustion(tmp_path: Path):
    value = planner_input()
    fixture = tmp_path / "planner.json"
    _write_fixture(
        fixture,
        [build_agent_planner_request_fingerprint(value, planner_profile())],
    )
    planner = RecordedAgentPlanner(fixture)
    planner.decide(value)

    with pytest.raises(RecordedPlannerFixtureError, match="FIXTURE_EXHAUSTED"):
        planner.decide(value)


def test_recorded_agent_planner_reports_unused_required_steps(tmp_path: Path):
    value = planner_input()
    fingerprint = build_agent_planner_request_fingerprint(value, planner_profile())
    fixture = tmp_path / "planner.json"
    _write_fixture(fixture, [fingerprint, fingerprint])
    planner = RecordedAgentPlanner(fixture)
    planner.decide(value)

    with pytest.raises(RecordedPlannerFixtureError, match="UNUSED_STEPS"):
        planner.assert_consumed()


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_version": "wrong"},
        {"steps": []},
        {"metadata": {"planner_prompt_version": "wrong", "request_profile": {}}},
    ],
)
def test_recorded_agent_planner_rejects_invalid_fixture(
    tmp_path: Path,
    mutation: dict,
):
    value = planner_input()
    fixture = tmp_path / "planner.json"
    _write_fixture(
        fixture,
        [build_agent_planner_request_fingerprint(value, planner_profile())],
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload.update(mutation)
    fixture.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RecordedPlannerFixtureError):
        RecordedAgentPlanner(fixture)
