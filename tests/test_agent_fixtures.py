from pathlib import Path

import pytest

from spectrail.agent.errors import AgentConfigurationError
from spectrail.agent.fixtures import (
    BUNDLED_AGENT_FIXTURE_ROOT,
    resolve_bundled_agent_fixture,
)
from spectrail.llm.recorded_agent_planner import RecordedAgentPlanner
from spectrail.llm.mock_model import MockModel


EXPECTED_AGENT_FIXTURES = {
    "sample_srs_agent.json",
    "sample_srs_agent_failure_retry_full.json",
    "sample_srs_agent_full.json",
    "sample_srs_agent_needs_human_full.json",
    "sample_srs_api_agent_full.json",
    "sample_srs_replan_agent.json",
}


def test_bundled_agent_fixture_set_is_complete_and_loadable():
    actual = {
        path.name
        for path in BUNDLED_AGENT_FIXTURE_ROOT.glob("*.json")
    }

    assert actual == EXPECTED_AGENT_FIXTURES
    fixture = resolve_bundled_agent_fixture("sample_srs_agent.json")
    assert fixture is not None
    assert fixture.parent == BUNDLED_AGENT_FIXTURE_ROOT
    assert RecordedAgentPlanner(fixture).fixture.steps


def test_default_mock_fixture_is_package_owned_and_loadable():
    fixture = MockModel().fixture_path

    assert fixture == BUNDLED_AGENT_FIXTURE_ROOT.parent / "mock_reqir_response.json"
    assert fixture.is_file()


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "../secret.json",
        "nested/fixture.json",
        "nested\\fixture.json",
    ],
)
def test_bundled_agent_fixture_rejects_paths(name: str):
    with pytest.raises(
        AgentConfigurationError,
        match="AGENT_PLANNER_FIXTURE_INVALID",
    ):
        resolve_bundled_agent_fixture(name)


def test_bundled_agent_fixture_reports_unknown_filename():
    with pytest.raises(
        AgentConfigurationError,
        match="AGENT_PLANNER_FIXTURE_NOT_FOUND",
    ):
        resolve_bundled_agent_fixture("missing.json")


def test_agent_fixture_package_data_is_declared():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert '[tool.setuptools.package-data]' in pyproject
    assert '"spectrail.fixtures" = ["*.json"]' in pyproject
    assert '"spectrail.fixtures.agent" = ["*.json"]' in pyproject
