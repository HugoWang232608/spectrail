from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spectrail.agent.factory import create_agent_planner
from spectrail.cli import main
from spectrail.core.io import read_json
from spectrail.llm.agent_planner import AgentPlannerClient


def test_cli_runs_recorded_agent_orchestration(tmp_path: Path):
    output = tmp_path / "cli_agent"

    exit_code = main(
        [
            "extract",
            "docs/sample_srs.md",
            "--output",
            str(output),
            "--orchestration-mode",
            "agent",
            "--planner-mode",
            "recorded",
            "--planner-fixture",
            "sample_srs_agent_full.json",
        ]
    )

    assert exit_code == 0
    manifest = read_json(output / "run_manifest.json")
    assert manifest["status"] == "completed"
    assert manifest["orchestration"]["mode"] == "agent"
    assert manifest["orchestration"]["planner_mode"] == "recorded"
    assert (output / "agent" / "trace.jsonl").is_file()
    assert (output / "agent" / "final_state.json").is_file()


def test_cli_rejects_planner_options_in_fixed_mode(tmp_path: Path):
    with pytest.raises(SystemExit, match="AGENT_OPTIONS_REQUIRE_AGENT_MODE"):
        main(
            [
                "extract",
                "docs/sample_srs.md",
                "--output",
                str(tmp_path / "fixed"),
                "--planner-mode",
                "recorded",
            ]
        )


def test_live_agent_planner_uses_separate_configured_transport(monkeypatch):
    monkeypatch.setenv("SPECTRAIL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SPECTRAIL_LLM_MODEL", "extraction-model")
    monkeypatch.setenv("SPECTRAIL_LLM_ENDPOINT_ID", "test-endpoint")

    planner = create_agent_planner(
        planner_mode="live",
        model_name="planner-model",
        insecure=True,
    )

    assert isinstance(planner, AgentPlannerClient)
    assert planner.request_profile.model_name == "planner-model"
    assert planner.insecure is True


def test_api_runs_recorded_agent_orchestration(api_client: TestClient):
    created = api_client.post(
        "/api/tasks",
        json={
            "model_mode": "mock",
            "orchestration_mode": "agent",
            "planner_mode": "recorded",
            "planner_fixture": "sample_srs_api_agent_full.json",
        },
    )
    assert created.status_code == 200
    task_id = created.json()["task_id"]
    sample = Path("docs/sample_srs.md")
    uploaded = api_client.post(
        f"/api/tasks/{task_id}/documents",
        files={"file": (sample.name, sample.read_bytes(), "text/markdown")},
    )
    assert uploaded.status_code == 200

    run = api_client.post(f"/api/tasks/{task_id}/run")

    assert run.status_code == 200
    payload = run.json()
    assert payload["status"] == "completed"
    assert payload["manifest"]["orchestration"]["mode"] == "agent"
    assert payload["manifest"]["orchestration"]["planner_mode"] == "recorded"
    status = api_client.get(f"/api/tasks/{task_id}").json()
    assert status["task"]["pipeline_config"]["orchestration_mode"] == "agent"
    task_dir = api_client.app.state.task_store.get_task_dir(task_id)
    assert (task_dir / "agent" / "trace.jsonl").is_file()
    assert (task_dir / "agent" / "attempts" / "attempt_0001.json").is_file()


@pytest.mark.parametrize(
    "request_payload",
    [
        {
            "orchestration_mode": "agent",
            "planner_mode": "recorded",
        },
        {
            "orchestration_mode": "agent",
            "planner_mode": "live",
            "planner_fixture": "sample_srs_agent_full.json",
        },
        {
            "orchestration_mode": "fixed",
            "planner_mode": "recorded",
        },
        {
            "orchestration_mode": "agent",
            "planner_mode": "recorded",
            "planner_fixture": "../secret.json",
        },
        {
            "orchestration_mode": "agent",
            "planner_mode": "recorded",
            "planner_fixture": "sample_srs_agent_full.json",
            "fail_fast": True,
        },
        {
            "orchestration_mode": "agent",
            "planner_mode": "recorded",
            "planner_fixture": "sample_srs_agent_full.json",
            "max_rendered_prompt_chars": 32001,
        },
        {
            "orchestration_mode": "agent",
            "planner_mode": "recorded",
            "planner_fixture": "sample_srs_agent_full.json",
            "overlap_blocks": 4,
        },
    ],
)
def test_api_rejects_invalid_agent_configuration(
    api_client: TestClient,
    request_payload: dict,
):
    response = api_client.post("/api/tasks", json=request_payload)

    assert response.status_code == 422


def test_api_rejects_unknown_bundled_agent_fixture(api_client: TestClient):
    created = api_client.post(
        "/api/tasks",
        json={
            "orchestration_mode": "agent",
            "planner_mode": "recorded",
            "planner_fixture": "missing.json",
        },
    )
    assert created.status_code == 200
    task_id = created.json()["task_id"]
    sample = Path("docs/sample_srs.md")
    api_client.post(
        f"/api/tasks/{task_id}/documents",
        files={"file": (sample.name, sample.read_bytes(), "text/markdown")},
    )

    run = api_client.post(f"/api/tasks/{task_id}/run")

    assert run.status_code == 422
    assert run.json()["detail"]["code"] == "AGENT_RUN_FAILED"
    assert run.json()["detail"]["message"] == "AGENT_PLANNER_FIXTURE_NOT_FOUND"
